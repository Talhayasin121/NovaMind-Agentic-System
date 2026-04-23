"""
agents/ads_agent/agent.py — NovaMind Ads Agent

Campaign advisor that analyzes content + metrics data and generates:
  - Ad copy variations (3 per content piece)
  - Budget allocation recommendations
  - Audience targeting suggestions
  - A/B test hypotheses

Note: This is an advisor — it generates recommendations, not actual ad buys.
All output is advisory and stored for human/future automation review.
"""
import json
import uuid
from datetime import datetime, timezone

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import send_task, write_alert, log_agent_heartbeat, log_metric
from core.supabase_client import get_supabase
from core.logger import AgentLogger
from core.prompt_evolution import PromptEvolver

log = AgentLogger("ads_agent")
_evolver = PromptEvolver("ads_agent")


def _get_recent_content_performance(limit: int = 5) -> list[dict]:
    """Fetch recent approved content from content_queue to base ads on."""
    supabase = get_supabase()
    response = (
        supabase.table("content_queue")
        .select("id, title, type, status")
        .eq("status", "approved")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def _generate_ad_campaign(content_pieces: list, urgency: str, task_id: str) -> tuple[dict, dict]:
    """Generate a full ad campaign recommendation using evolved DNA."""
    content_summary = "\n".join(
        [f"- {c.get('title', 'Untitled')} ({c.get('type', 'content')})"
         for c in content_pieces]
    ) or "No approved content yet — generate placeholder recommendations."

    # Get Evolved DNA
    dna = _evolver.get_prompt("campaign_generation", default_system=(
        "You are a performance marketing strategist for NovaMind Digital Agency. "
        "Create data-driven ad recommendations. "
        "Return ONLY valid JSON. No markdown fences or explanation."
    ), default_template=(
        "Create a complete digital ad campaign strategy for these content pieces:\n"
        "{content_summary}\n\n"
        "Urgency level: {urgency}\n\n"
        "Return JSON:\n"
        "{{\"campaign_theme\": \"...\", "
        "\"ad_copies\": [{{\"headline\": \"...\", \"body\": \"...\", \"cta\": \"...\"}}], "
        "\"budget_split\": {{\"linkedin\": \"X%\", \"google_search\": \"X%\", \"meta\": \"X%\"}}, "
        "\"target_audiences\": [\"...\"], "
        "\"ab_test_hypothesis\": \"...\"}}"
    ))

    prompt = dna.template.format(content_summary=content_summary, urgency=urgency)
    
    raw = invoke_llm(prompt, system_prompt=dna.system_prompt, tier=LLMTier.FAST,
                     temperature=0.7, task_id=task_id)
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean), {"prompt_id": dna.id, "generation": dna.generation}
    except Exception:
        return {"campaign_theme": "General awareness", "ad_copies": [], "budget_split": {},
                "target_audiences": [], "ab_test_hypothesis": "N/A"}, {"prompt_id": dna.id, "generation": dna.generation}


def run_ads_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    urgency    = input_data.get("urgency", "daily_check")

    log.info(f"Ads Agent starting (urgency={urgency})", task_id=task_id)
    log.start_timer("ads_full_run")

    try:
        # Step 1: Get recent approved content to base ads on
        content_pieces = _get_recent_content_performance()
        log.info(f"Found {len(content_pieces)} approved content pieces to work with.", task_id=task_id)

        # Step 2: Generate campaign
        campaign, evolution_meta = _generate_ad_campaign(content_pieces, urgency, task_id)

        # Step 3: Save to agent_outputs
        supabase  = get_supabase()
        output_id = str(uuid.uuid4())
        supabase.table("agent_outputs").insert({
            "id":          output_id,
            "agent_id":    "ads_agent",
            "output_type": "campaign_brief",
            "content":     campaign,
            "qa_status":   "pending",
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()

        log_metric("ads_agent", "ad_copies_generated", len(campaign.get("ad_copies", [])))
        log_agent_heartbeat("ads_agent")

        # Step 4: Send to QA
        send_task(
            from_agent="ads_agent",
            to_agent="qa_agent",
            task_type="review_content",
            input_data={
                "content_queue_id": None,
                "content_title":    f"Campaign Brief: {campaign.get('campaign_theme', 'Unknown')}",
                "content_body":     json.dumps(campaign, indent=2),
                "content_type":     "campaign_brief",
                "output_id":        output_id,
                "reject_count":     0,
                "evolution_meta":   evolution_meta  # CRITICAL for feedback loop
            },
        )

        duration = log.end_timer("ads_full_run", task_id=task_id)
        log.info(f"Ads Agent complete. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"Ads Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("ads_agent", "high", f"Task {task_id} failed: {e}")
        raise
