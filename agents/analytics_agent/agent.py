"""
agents/analytics_agent/agent.py — NovaMind Analytics Agent

Queries all Supabase tables and produces a structured weekly performance report.
Uses Gemini 2.5 Flash (large context) to write an executive-level natural language summary.
Publishes report to Notion and sends a digest to Discord.

Metrics tracked:
  - Content output: articles published, social posts, QA pass rate
  - Lead pipeline: leads found, qualified, contacted, responded
  - Agent health: uptime, error rates, health score trends
  - Revenue indicators: content → lead → prospect conversion funnel
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from notion_client import Client as NotionClient

from core.llm_pool import invoke_llm, LLMTier
from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import (
    write_alert, log_agent_heartbeat, log_metric, send_discord_notify,
)
from core.supabase_client import get_supabase
from core.config import NOTION_API_KEY, NOTION_PARENT_PAGE_ID
from core.logger import AgentLogger
from core.prompt_evolution import PromptEvolver

log = AgentLogger("analytics_agent")
_evolver = PromptEvolver("analytics_agent")

_REPORT_SYSTEM = (
    "You are an executive business analyst for a digital marketing agency. "
    "Write concise, insight-driven performance reports. "
    "Focus on trends, anomalies, and actionable recommendations. "
    "Use Markdown formatting. Keep under 600 words."
)

# ─── Data Collection ───────────────────────────────────────────────────────────

def _collect_metrics(days: int = 7) -> dict:
    """Aggregate all key metrics from the last N days."""
    supabase = get_supabase()
    cutoff   = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Content metrics
    content_approved = (
        supabase.table("content_queue").select("id", count="exact")
        .eq("status", "approved").gte("created_at", cutoff).execute().count or 0
    )
    content_pending = (
        supabase.table("content_queue").select("id", count="exact")
        .eq("status", "qa_pending").gte("created_at", cutoff).execute().count or 0
    )
    content_rejected = (
        supabase.table("content_queue").select("id", count="exact")
        .in_("status", ["qa_rejected", "needs_human_review"])
        .gte("created_at", cutoff).execute().count or 0
    )

    # QA metrics
    qa_approved = (
        supabase.table("qa_queue").select("id", count="exact")
        .eq("check_status", "qa_approved").gte("reviewed_at", cutoff).execute().count or 0
    )
    qa_rejected = (
        supabase.table("qa_queue").select("id", count="exact")
        .eq("check_status", "qa_rejected").gte("reviewed_at", cutoff).execute().count or 0
    )
    qa_total = qa_approved + qa_rejected
    qa_pass_rate = round((qa_approved / qa_total * 100), 1) if qa_total > 0 else 0

    # Lead pipeline
    leads_found    = (
        supabase.table("leads").select("id", count="exact")
        .gte("created_at", cutoff).execute().count or 0
    )
    leads_contacted = (
        supabase.table("leads").select("id", count="exact")
        .eq("status", "contacted").gte("created_at", cutoff).execute().count or 0
    )
    leads_synced = (
        supabase.table("leads").select("id", count="exact")
        .eq("status", "crm_synced").gte("created_at", cutoff).execute().count or 0
    )

    # Agent health
    alerts_high = (
        supabase.table("alerts").select("id", count="exact")
        .in_("severity", ["high", "critical"]).eq("resolved", False)
        .gte("created_at", cutoff).execute().count or 0
    )

    # Latest health score
    latest_health = (
        supabase.table("metrics").select("value")
        .eq("agent_id", "coo_agent").eq("metric_name", "health_score")
        .order("recorded_at", desc=True).limit(1).execute()
    )
    health_score = latest_health.data[0]["value"] if latest_health.data else 0

    # Email stats from daily_limits
    email_rows = (
        supabase.table("daily_limits").select("call_count")
        .eq("provider", "brevo").gte("date", cutoff[:10]).execute()
    )
    emails_sent = sum(r["call_count"] for r in (email_rows.data or []))

    return {
        "period_days":       days,
        "content": {
            "approved":          content_approved,
            "pending_qa":        content_pending,
            "rejected":          content_rejected,
        },
        "qa": {
            "total_reviews":     qa_total,
            "approved":          qa_approved,
            "rejected":          qa_rejected,
            "pass_rate_pct":     qa_pass_rate,
        },
        "leads": {
            "discovered":        leads_found,
            "crm_synced":        leads_synced,
            "contacted":         leads_contacted,
        },
        "emails_sent":           emails_sent,
        "alerts_unresolved":     alerts_high,
        "agency_health_score":   int(health_score),
        "generated_at":          datetime.now(timezone.utc).isoformat(),
    }


def _generate_narrative(metrics: dict, task_id: str) -> tuple[str, dict]:
    """Use evolved DNA to write a natural language executive summary."""
    dna = _evolver.get_prompt("executive_report", default_system=_REPORT_SYSTEM, default_template=(
        "Write a weekly performance report for NovaMind Digital Agency based on this data.\n\n"
        "```json\n{metrics_json}\n```\n\n"
        "Include: key wins, areas of concern, and 3 specific recommendations for next week."
    ))

    prompt = dna.template.format(metrics_json=json.dumps(metrics, indent=2))
    narrative = invoke_llm(prompt, system_prompt=dna.system_prompt, tier=LLMTier.DEEP,
                          temperature=0.5, task_id=task_id)
    
    return narrative, {"prompt_id": dna.id, "generation": dna.generation}



def _publish_to_notion(narrative: str, metrics: dict) -> None:
    """Create a new Notion page with the weekly report."""
    if not NOTION_API_KEY or not NOTION_PARENT_PAGE_ID:
        log.warning("Notion credentials not set — skipping Notion publish.")
        return

    notion = NotionClient(auth=NOTION_API_KEY)
    week   = datetime.now(timezone.utc).strftime("%Y-W%W")

    notion.pages.create(
        parent={"page_id": NOTION_PARENT_PAGE_ID},
        properties={
            "title": [{"text": {"content": f"📊 Analytics Report — {week}"}}]
        },
        children=[
            {
                "object": "block",
                "paragraph": {
                    "rich_text": [{"text": {"content": narrative[:2000]}}]
                }
            }
        ]
    )
    log.info("Analytics report published to Notion.")


# ─── Main Entry Point ──────────────────────────────────────────────────────────

def run_analytics_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    days       = int(input_data.get("days", 7))

    log.info(f"Analytics Agent starting ({days}-day report)", task_id=task_id)
    log.start_timer("analytics_full_run")

    try:
        # Step 1: Collect all metrics
        metrics = _collect_metrics(days)
        log.info(
            f"Metrics collected — Content approved: {metrics['content']['approved']}, "
            f"Leads: {metrics['leads']['discovered']}, QA pass: {metrics['qa']['pass_rate_pct']}%",
            task_id=task_id,
        )

        # Step 2: Generate natural language summary
        narrative, evo_meta = _generate_narrative(metrics, task_id)

        # Step 3: Publish to Notion
        _publish_to_notion(narrative, metrics)

        # Step 4: Send condensed digest to Discord
        m = metrics
        send_discord_notify(
            title=f"📊 Weekly Analytics Report",
            message=narrative[:1500],
            severity="info",
            fields=[
                {"name": "✅ Content Approved", "value": f"`{m['content']['approved']}`",     "inline": True},
                {"name": "🛡️ QA Pass Rate",    "value": f"`{m['qa']['pass_rate_pct']}%`",    "inline": True},
                {"name": "🎯 Leads Found",      "value": f"`{m['leads']['discovered']}`",     "inline": True},
                {"name": "📧 Emails Sent",      "value": f"`{m['emails_sent']}`",             "inline": True},
                {"name": "💊 Health Score",     "value": f"`{m['agency_health_score']}/100`", "inline": True},
                {"name": "🚨 Open Alerts",      "value": f"`{m['alerts_unresolved']}`",       "inline": True},
            ],
        )

        # Step 5: Log metrics to Supabase
        log_metric("analytics_agent", "content_approved_weekly", metrics["content"]["approved"])
        log_metric("analytics_agent", "qa_pass_rate",             metrics["qa"]["pass_rate_pct"])
        log_metric("analytics_agent", "leads_weekly",             metrics["leads"]["discovered"])
        log_agent_heartbeat("analytics_agent")

        duration = log.end_timer("analytics_full_run", task_id=task_id)
        log.info(f"Analytics Agent complete. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"Analytics Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("analytics_agent", "high", f"Task {task_id} failed: {e}")
        raise
