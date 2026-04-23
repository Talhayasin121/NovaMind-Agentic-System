"""
agents/finance_agent/agent.py — NovaMind Finance Agent

Tracks the agency's operational health in financial terms.
Since we're 100% free-tier, this agent tracks:
  - "Cost equivalents" (what each API call would cost at paid rates)
  - Task throughput as revenue proxy (work output per agent)
  - API usage rates vs free-tier limits (forecast when you'll need paid tiers)
  - Monthly P&L-style report (actually: value generated vs zero-cost constraints)

Output: Monthly operations report to Notion + Discord summary.
"""
import json
from datetime import datetime, timedelta, timezone
from notion_client import Client as NotionClient

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import write_alert, log_agent_heartbeat, log_metric, send_discord_notify
from core.supabase_client import get_supabase
from core.config import NOTION_API_KEY, NOTION_PARENT_PAGE_ID
from core.logger import AgentLogger

log = AgentLogger("finance_agent")

# Estimated value per output type (based on market rates for comparable work)
VALUE_MAP = {
    "blog_post":      75,   # $75 equivalent per published article
    "social_posts":   15,   # $15 per social pack (3 platforms)
    "seo_report":     50,   # $50 per SEO analysis
    "campaign_brief": 40,   # $40 per paid campaign brief
    "lead_qualified": 20,   # $20 per qualified lead (lead gen cost equivalent)
    "email_sent":      2,   # $2 per personalized outreach email
}

_REPORT_SYSTEM = (
    "You are a CFO advisor for a scrappy AI-native digital agency. "
    "Write concise financial operations reports. "
    "Focus on: output value, free-tier runway, and scaling thresholds. "
    "Use Markdown. Keep under 400 words."
)


def _collect_financial_data(days: int = 30) -> dict:
    """Aggregate all outputs and compute value equivalents."""
    supabase = get_supabase()
    cutoff   = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Count outputs by type
    blog_posts = (
        supabase.table("content_queue").select("id", count="exact")
        .eq("status", "approved").eq("type", "blog_post")
        .gte("created_at", cutoff).execute().count or 0
    )
    social_packs = (
        supabase.table("agent_outputs").select("id", count="exact")
        .eq("output_type", "social_posts")
        .gte("created_at", cutoff).execute().count or 0
    )
    seo_reports = (
        supabase.table("agent_outputs").select("id", count="exact")
        .eq("output_type", "seo_report")
        .gte("created_at", cutoff).execute().count or 0
    )
    campaign_briefs = (
        supabase.table("agent_outputs").select("id", count="exact")
        .eq("output_type", "campaign_brief")
        .gte("created_at", cutoff).execute().count or 0
    )
    leads_qualified = (
        supabase.table("leads").select("id", count="exact")
        .gte("score", 6).gte("created_at", cutoff).execute().count or 0
    )

    # Email stats
    email_rows = (
        supabase.table("daily_limits").select("call_count")
        .eq("provider", "brevo").gte("date", cutoff[:10]).execute()
    )
    emails_sent = sum(r["call_count"] for r in (email_rows.data or []))

    # API usage tracking
    groq_rows = (
        supabase.table("daily_limits").select("call_count")
        .eq("provider", "groq").gte("date", cutoff[:10]).execute()
    )
    groq_calls = sum(r["call_count"] for r in (groq_rows.data or []))

    # Total tasks processed
    tasks_done = (
        supabase.table("tasks").select("id", count="exact")
        .eq("status", "done").gte("created_at", cutoff).execute().count or 0
    )

    # Compute value equivalents
    value_generated = (
        blog_posts      * VALUE_MAP["blog_post"]      +
        social_packs    * VALUE_MAP["social_posts"]   +
        seo_reports     * VALUE_MAP["seo_report"]     +
        campaign_briefs * VALUE_MAP["campaign_brief"] +
        leads_qualified * VALUE_MAP["lead_qualified"] +
        emails_sent     * VALUE_MAP["email_sent"]
    )

    # API usage vs limit runway
    groq_daily_avg     = groq_calls / max(days, 1)
    groq_days_left     = (1000 - groq_calls % 1000) / max(groq_daily_avg, 1) if groq_daily_avg > 0 else 999
    brevo_monthly_used = emails_sent
    brevo_monthly_cap  = 9000    # 300/day * 30

    return {
        "period_days": days,
        "outputs": {
            "blog_posts":       blog_posts,
            "social_packs":     social_packs,
            "seo_reports":      seo_reports,
            "campaign_briefs":  campaign_briefs,
            "leads_qualified":  leads_qualified,
            "emails_sent":      emails_sent,
        },
        "value_generated_usd":  value_generated,
        "actual_cost_usd":      0,
        "roi_ratio":            "∞ (zero cost)",
        "api_usage": {
            "groq_calls_period": groq_calls,
            "groq_daily_avg":    round(groq_daily_avg, 1),
            "groq_rpm_headroom": "30 RPM (current tier)",
            "brevo_used":        brevo_monthly_used,
            "brevo_cap":         brevo_monthly_cap,
            "brevo_pct_used":    round(brevo_monthly_used / brevo_monthly_cap * 100, 1),
        },
        "tasks_completed":      tasks_done,
        "generated_at":         datetime.now(timezone.utc).isoformat(),
    }


def _generate_report(data: dict, task_id: str) -> str:
    prompt = (
        f"Write a monthly financial operations summary for NovaMind AI agency:\n\n"
        f"```json\n{json.dumps(data, indent=2)}\n```\n\n"
        f"Highlight: value generated at zero cost, API usage projections, "
        f"and when we'll need to upgrade to paid tiers."
    )
    return invoke_llm(prompt, system_prompt=_REPORT_SYSTEM, tier=LLMTier.FAST,
                      temperature=0.4, task_id=task_id)


def _publish_report_to_notion(report: str, data: dict) -> None:
    if not NOTION_API_KEY or not NOTION_PARENT_PAGE_ID:
        log.warning("Notion credentials not set — skipping Notion publish.")
        return
    notion = NotionClient(auth=NOTION_API_KEY)
    month  = datetime.now(timezone.utc).strftime("%B %Y")
    notion.pages.create(
        parent={"page_id": NOTION_PARENT_PAGE_ID},
        properties={"title": [{"text": {"content": f"💰 Finance Report — {month}"}}]},
        children=[{"object": "block", "paragraph": {
            "rich_text": [{"text": {"content": report[:2000]}}]
        }}]
    )


def run_finance_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    days       = int(input_data.get("days", 30))

    log.info(f"Finance Agent starting ({days}-day report)", task_id=task_id)
    log.start_timer("finance_full_run")

    try:
        data   = _collect_financial_data(days)
        report = _generate_report(data, task_id)

        _publish_report_to_notion(report, data)

        d = data
        send_discord_notify(
            title="💰 Monthly Finance Operations Report",
            message=report[:1500],
            severity="info",
            fields=[
                {"name": "📈 Value Generated", "value": f"`${d['value_generated_usd']}`",          "inline": True},
                {"name": "💸 Actual Cost",     "value": "`$0.00`",                                 "inline": True},
                {"name": "🔄 Tasks Done",      "value": f"`{d['tasks_completed']}`",               "inline": True},
                {"name": "📧 Brevo Used",      "value": f"`{d['api_usage']['brevo_pct_used']}%`",  "inline": True},
                {"name": "🤖 Groq Calls",      "value": f"`{d['api_usage']['groq_calls_period']}`","inline": True},
            ],
        )

        log_metric("finance_agent", "value_generated_usd", data["value_generated_usd"])
        log_metric("finance_agent", "tasks_completed",      data["tasks_completed"])
        log_agent_heartbeat("finance_agent")

        duration = log.end_timer("finance_full_run", task_id=task_id)
        log.info(f"Finance Agent complete. Value generated: ${data['value_generated_usd']}. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"Finance Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("finance_agent", "high", f"Task {task_id} failed: {e}")
        raise
