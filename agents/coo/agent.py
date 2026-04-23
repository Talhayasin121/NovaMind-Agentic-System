"""
agents/coo/agent.py — NovaMind COO Agent (Self-Healing System Monitor)

Responsibilities:
  1. Detect stalled tasks (in_progress > STALL_THRESHOLD_MINUTES) and auto-retry them
  2. Detect dead agents (no heartbeat in the last 2 hours)
  3. Detect high QA rejection rates and escalate
  4. Compute a dynamic agency health score (0–100)
  5. Send a daily health digest to Discord
  6. Log all metrics to Supabase for the Analytics Agent
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from core.message_bus import (
    write_alert,
    log_metric,
    log_agent_heartbeat,
    send_discord_notify,
    update_task_status,
    send_task,
    get_stalled_tasks,
)
from core.supabase_client import get_supabase
from core.config import STALL_THRESHOLD_MINUTES
from core.logger import AgentLogger

log = AgentLogger("coo_agent")


# ─── Sub-routines ─────────────────────────────────────────────────────────────

def _handle_stalled_tasks() -> tuple[int, int]:
    """
    Find tasks stuck in 'in_progress' beyond the threshold.
    Auto-resets them to 'pending' (self-healing) and alerts.

    Returns:
        (stalled_count, retried_count)
    """
    stalled = get_stalled_tasks(STALL_THRESHOLD_MINUTES)
    retried = 0

    for task in stalled:
        task_id = task["id"]
        agent   = task.get("to_agent", "unknown")
        minutes_stuck = round(
            (datetime.now(timezone.utc) -
             datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00"))
            ).total_seconds() / 60,
            1,
        )

        log.warning(
            f"Stalled task detected: {task_id} for {agent} "
            f"({minutes_stuck}m stuck). Auto-retrying.",
        )

        # Self-healing: reset to pending so it gets picked up again
        update_task_status(task_id, "pending")
        retried += 1

        write_alert(
            agent_id="coo_agent",
            severity="warning",
            message=(
                f"Task {task_id} was stalled for {minutes_stuck}m "
                f"on agent '{agent}'. Auto-reset to pending."
            ),
        )

    return len(stalled), retried


def _check_dead_agents() -> list[str]:
    """
    Check agent heartbeats. Any agent with no heartbeat in the last 2 hours
    is considered dead. Returns list of dead agent IDs.
    """
    supabase  = get_supabase()
    cutoff    = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    dead      = []

    # All known agents that should be active
    expected_agents = [
        "ceo_agent", "content_agent", "qa_agent",
        "seo_agent", "ads_agent", "design_agent",
    ]

    for agent_id in expected_agents:
        response = (
            supabase.table("metrics")
            .select("recorded_at")
            .eq("agent_id", agent_id)
            .eq("metric_name", "heartbeat")
            .order("recorded_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []

        if not rows or rows[0]["recorded_at"] < cutoff:
            dead.append(agent_id)
            log.warning(f"Dead agent detected: {agent_id} (no heartbeat since {cutoff})")
            write_alert(
                agent_id="coo_agent",
                severity="high",
                message=f"Agent '{agent_id}' has no heartbeat in the last 2 hours.",
            )

    return dead


def _check_qa_rejection_rate() -> float:
    """
    Calculate QA rejection rate from the last 24 hours.
    Returns rejection rate as a float between 0.0 and 1.0.
    """
    supabase = get_supabase()
    cutoff   = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    total = (
        supabase.table("qa_queue")
        .select("id", count="exact")
        .gte("reviewed_at", cutoff)
        .execute()
    )
    rejected = (
        supabase.table("qa_queue")
        .select("id", count="exact")
        .eq("check_status", "qa_rejected")
        .gte("reviewed_at", cutoff)
        .execute()
    )

    total_count    = total.count or 0
    rejected_count = rejected.count or 0

    if total_count == 0:
        return 0.0

    rate = rejected_count / total_count
    if rate > 0.3:    # > 30% rejection rate is alarming
        write_alert(
            agent_id="coo_agent",
            severity="high",
            message=f"High QA rejection rate: {rate:.0%} ({rejected_count}/{total_count}) in last 24h.",
        )
    return rate


def _count_unresolved_alerts() -> int:
    """Count unresolved alerts from the last 24 hours."""
    supabase = get_supabase()
    cutoff   = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    response = (
        supabase.table("alerts")
        .select("id", count="exact")
        .eq("resolved", False)
        .gte("created_at", cutoff)
        .execute()
    )
    return response.count or 0


def _calculate_health_score(
    stalled_count: int,
    dead_agents: list[str],
    qa_rejection_rate: float,
    unresolved_alerts: int,
) -> int:
    """
    Compute a 0–100 agency health score.

    Penalties:
      - Each stalled task:      -3 points
      - Each dead agent:        -10 points
      - QA rejection > 30%:    -20 points
      - Each unresolved alert:  -2 points (capped at -20)
    """
    score = 100
    score -= stalled_count * 3
    score -= len(dead_agents) * 10
    if qa_rejection_rate > 0.3:
        score -= 20
    score -= min(unresolved_alerts * 2, 20)
    return max(0, score)


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def run_coo_agent(payload: dict):
    """
    COO Agent entry point. Runs all health checks and sends a Discord digest.
    Called by the FastAPI background task router.
    """
    log.info("Starting COO health cycle...")
    log.start_timer("coo_full_cycle")

    try:
        # ── 1. Stalled task detection + auto-retry ──────────────────────────
        stalled_count, retried_count = _handle_stalled_tasks()
        log.info(f"Stalled tasks: {stalled_count} found, {retried_count} auto-retried.")

        # ── 2. Dead agent detection ─────────────────────────────────────────
        dead_agents = _check_dead_agents()

        # ── 3. QA rejection rate ────────────────────────────────────────────
        qa_rejection_rate = _check_qa_rejection_rate()

        # ── 4. Alert count ──────────────────────────────────────────────────
        unresolved_alerts = _count_unresolved_alerts()

        # ── 5. Compute health score ─────────────────────────────────────────
        health_score = _calculate_health_score(
            stalled_count, dead_agents, qa_rejection_rate, unresolved_alerts
        )

        # ── 6. Log all metrics to Supabase ──────────────────────────────────
        log_metric("coo_agent", "health_score",       health_score)
        log_metric("coo_agent", "stalled_tasks",      stalled_count)
        log_metric("coo_agent", "dead_agents",        len(dead_agents))
        log_metric("coo_agent", "qa_rejection_rate",  round(qa_rejection_rate * 100, 1))
        log_metric("coo_agent", "unresolved_alerts",  unresolved_alerts)
        log_agent_heartbeat("coo_agent")

        # ── 7. Determine report severity for Discord ─────────────────────────
        if health_score >= 80:
            severity = "success"
            status_emoji = "✅"
        elif health_score >= 50:
            severity = "warning"
            status_emoji = "⚠️"
        else:
            severity = "high"
            status_emoji = "🚨"

        # ── 8. Send Discord health digest ────────────────────────────────────
        discord_fields = [
            {
                "name":   "🏥 Health Score",
                "value":  f"`{health_score}/100`",
                "inline": True,
            },
            {
                "name":   "⏳ Stalled Tasks",
                "value":  f"`{stalled_count}` ({retried_count} auto-retried)",
                "inline": True,
            },
            {
                "name":   "💀 Dead Agents",
                "value":  ", ".join(dead_agents) if dead_agents else "`None`",
                "inline": True,
            },
            {
                "name":   "🛡️ QA Rejection Rate (24h)",
                "value":  f"`{qa_rejection_rate:.0%}`",
                "inline": True,
            },
            {
                "name":   "🔔 Unresolved Alerts",
                "value":  f"`{unresolved_alerts}`",
                "inline": True,
            },
        ]

        send_discord_notify(
            title=f"{status_emoji} NovaMind COO Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            message=(
                f"Agency health is at **{health_score}/100**.\n"
                + (f"⚠️ Dead agents: `{'`, `'.join(dead_agents)}`\n" if dead_agents else "")
                + (f"🔁 {retried_count} stalled tasks were auto-retried." if retried_count else "")
            ),
            severity=severity,
            fields=discord_fields,
        )

        duration = log.end_timer("coo_full_cycle")
        log.info(f"COO cycle complete. Health: {health_score}/100. Duration: {duration}ms")

    except Exception as e:
        log.error(f"COO agent crashed: {e}", exc_info=True)
        write_alert(
            agent_id="coo_agent",
            severity="critical",
            message=f"COO agent itself crashed: {e}",
        )
        send_discord_notify(
            title="🚨 COO Agent Crashed",
            message=f"The COO monitoring agent itself has failed:\n```{e}```",
            severity="critical",
        )
