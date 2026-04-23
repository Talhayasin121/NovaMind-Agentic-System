"""
core/message_bus.py — Enhanced async message bus for NovaMind.

New additions over v1:
  - update_task_status()    — mark a task in_progress / done / failed
  - get_pending_tasks()     — poll for tasks destined for a specific agent
  - log_metric()            — convenience wrapper for metrics table
  - send_discord_notify()   — send rich embeds to Discord webhook
  - log_agent_heartbeat()   — COO uses these to detect dead agents
"""
import uuid
import json
import requests
from datetime import datetime, timezone

from core.supabase_client import get_supabase
from core.logger import AgentLogger

log = AgentLogger("message_bus")


# ─── Task Operations ──────────────────────────────────────────────────────────

def send_task(
    from_agent: str,
    to_agent: str,
    task_type: str,
    input_data: dict,
    priority: str = "normal",
) -> dict:
    """Create a new pending task in Supabase."""
    supabase = get_supabase()
    task_id = str(uuid.uuid4())
    payload = {
        "id":         task_id,
        "from_agent": from_agent,
        "to_agent":   to_agent,
        "type":       task_type,
        "priority":   priority,
        "payload":    input_data,
        "status":     "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = supabase.table("tasks").insert(payload).execute()
    log.info(f"Task dispatched → {to_agent} [{task_type}] id={task_id}")
    return response.data[0] if response.data else {}


def update_task_status(task_id: str, status: str, error: str | None = None) -> None:
    """Update the status of an existing task (in_progress, done, failed, dead_letter)."""
    supabase = get_supabase()
    update_data = {
        "status":     status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        update_data["error_message"] = error[:500]  # cap length
    supabase.table("tasks").update(update_data).eq("id", task_id).execute()
    log.info(f"Task {task_id} → {status}")


def get_pending_tasks(agent_name: str, limit: int = 10) -> list[dict]:
    """
    Atomically claim pending tasks for a given agent.
    Returns a list of task rows or [].
    """
    supabase = get_supabase()
    # Fetch pending tasks for this agent, ordered by priority then created_at
    response = (
        supabase.table("tasks")
        .select("*")
        .eq("to_agent", agent_name)
        .eq("status", "pending")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    tasks = response.data or []
    # Immediately mark them in_progress to prevent double-pickup
    for task in tasks:
        update_task_status(task["id"], "in_progress")
    return tasks


def get_stalled_tasks(threshold_minutes: int = 30) -> list[dict]:
    """
    Return tasks stuck in 'in_progress' for longer than threshold_minutes.
    Used by the COO agent for self-healing.
    """
    from datetime import timedelta
    supabase = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)).isoformat()
    response = (
        supabase.table("tasks")
        .select("*")
        .eq("status", "in_progress")
        .lt("updated_at", cutoff)
        .execute()
    )
    return response.data or []


# ─── Metrics & Alerts ─────────────────────────────────────────────────────────

def log_metric(agent_id: str, metric_name: str, value: float) -> None:
    """Write a single metric reading to the metrics table."""
    supabase = get_supabase()
    supabase.table("metrics").insert({
        "id":          str(uuid.uuid4()),
        "agent_id":    agent_id,
        "metric_name": metric_name,
        "value":       value,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def write_alert(agent_id: str, severity: str, message: str) -> None:
    """Log an error / warning to the alerts table."""
    supabase = get_supabase()
    supabase.table("alerts").insert({
        "id":         str(uuid.uuid4()),
        "agent_id":   agent_id,
        "severity":   severity,
        "message":    message,
        "resolved":   False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    log.warning(f"[{severity.upper()}] {agent_id}: {message}")


def log_agent_heartbeat(agent_id: str) -> None:
    """Record a heartbeat so COO can detect dead agents."""
    log_metric(agent_id, "heartbeat", 1.0)


# ─── Discord Notifications ─────────────────────────────────────────────────────

# Discord severity → embed color (decimal)
_DISCORD_COLORS = {
    "info":     3447003,   # blue
    "success":  3066993,   # green
    "warning":  16776960,  # yellow
    "high":     15158332,  # red
    "critical": 10038562,  # dark red
}

def send_discord_notify(
    title: str,
    message: str,
    severity: str = "info",
    fields: list[dict] | None = None,
) -> None:
    """
    Send a rich embed notification to the Discord webhook.

    Args:
        title:    Embed title.
        message:  Embed description (supports Markdown).
        severity: info | success | warning | high | critical
        fields:   Optional list of {"name": ..., "value": ..., "inline": bool}
    """
    from core.config import DISCORD_WEBHOOK_URL
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set — skipping notification.")
        return

    embed = {
        "title":       title,
        "description": message[:2000],
        "color":       _DISCORD_COLORS.get(severity, 3447003),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "footer":      {"text": "NovaMind Agency"},
    }
    if fields:
        embed["fields"] = fields[:25]   # Discord allows max 25 fields

    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=5,
        )
        r.raise_for_status()
        log.info(f"Discord notification sent: {title}")
    except Exception as e:
        log.error(f"Discord notification failed: {e}")
