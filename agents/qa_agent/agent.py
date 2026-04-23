"""
agents/qa_agent/agent.py — NovaMind QA Agent (The Gatekeeper)

Every piece of content MUST pass through here before publication.

Workflow:
  1. Receive content from Content, Design, SEO, or Ads agents
  2. Score on 4 criteria using Gemini 2.5 Flash (large context)
  3. Approve (score ≥ 7 on all) → update content_queue, notify Discord
  4. Reject (any score < 7) → send back to originating agent with specific feedback
  5. After MAX_QA_REJECT_CYCLES rejections → escalate to 'needs_human_review'
  6. Log everything to qa_queue table for audit trail
"""
import json
import uuid
from datetime import datetime, timezone

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import (
    send_task, write_alert, log_agent_heartbeat, log_metric,
    send_discord_notify,
)
from core.supabase_client import get_supabase
from core.config import QA_MIN_SCORE, MAX_QA_REJECT_CYCLES
from core.logger import AgentLogger
from core.prompt_evolution import get_evolver

log = AgentLogger("qa_agent")
# We no longer instantiate a single _evolver here; we get it dynamically per task.

_QA_SYSTEM = """You are the QA Director of NovaMind Digital Agency.
Your job is to evaluate content with zero bias on exactly 4 criteria.

Return ONLY valid JSON in this exact format:
{
  "accuracy": <score 1-10>,
  "brand_voice": <score 1-10>,
  "seo_optimization": <score 1-10>,
  "actionability": <score 1-10>,
  "overall_verdict": "approved" | "rejected",
  "feedback": "<concise, specific improvement instructions if rejected>"
}

Scoring rubric:
- accuracy (1-10): Facts are correct, sources are credible, no hallucinations
- brand_voice (1-10): Professional, clear, matches a premium digital agency tone
- seo_optimization (1-10): Has keywords, proper headings, meta-worthy intro
- actionability (1-10): Reader knows what to do next, CTA is clear
"""


def _score_content(content: str, content_type: str, task_id: str) -> dict:
    """Run the Gemini QA review and return structured scores."""
    prompt = (
        f"Review this {content_type} for NovaMind Digital Agency:\n\n"
        f"{content[:6000]}"  # Gemini 2.5 Flash handles large contexts well
    )
    raw = invoke_llm(
        prompt,
        system_prompt=_QA_SYSTEM,
        tier=LLMTier.DEEP,       # Use Gemini for QA — it's smarter for analysis
        temperature=0.1,          # Very low temp → consistent, strict scoring
        task_id=task_id,
    )
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        log.warning("QA returned non-JSON. Defaulting to reject.", task_id=task_id)
        return {
            "accuracy": 5, "brand_voice": 5,
            "seo_optimization": 5, "actionability": 5,
            "overall_verdict": "rejected",
            "feedback": "QA could not parse response. Manual review required.",
        }


def _log_to_qa_queue(
    output_id: str | None,
    agent_id: str,
    check_status: str,
    feedback: str,
    scores: dict,
) -> None:
    """Write QA decision to the qa_queue audit table."""
    supabase = get_supabase()
    supabase.table("qa_queue").insert({
        "id":          str(uuid.uuid4()),
        "output_id":   output_id,
        "agent_id":    agent_id,
        "check_status": check_status,
        "feedback":    feedback,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def _update_content_status(content_queue_id: str | None, status: str) -> None:
    if not content_queue_id:
        return
    supabase = get_supabase()
    supabase.table("content_queue").update({
        "status": status,
    }).eq("id", content_queue_id).execute()


def run_qa_agent(payload: dict):
    task_id       = payload.get("task_id", "unknown")
    input_data    = payload.get("input", {})
    from_agent    = payload.get("from_agent", "unknown_agent")

    content_id    = input_data.get("content_queue_id")
    content_title = input_data.get("content_title", "Untitled")
    content_body  = input_data.get("content_body", "")
    reject_count  = int(input_data.get("reject_count", 0))
    content_type  = input_data.get("content_type", "blog_post")

    log.info(f"QA review starting for '{content_title}' (reject_count={reject_count})", task_id=task_id)
    log.start_timer("qa_review")

    try:
        # Hard stop if too many rejection cycles — escalate to human
        if reject_count >= MAX_QA_REJECT_CYCLES:
            log.warning(
                f"Content '{content_title}' hit max QA cycles ({reject_count}). Escalating.",
                task_id=task_id,
            )
            _update_content_status(content_id, "needs_human_review")
            _log_to_qa_queue(content_id, from_agent, "needs_human_review",
                             "Exceeded max reject cycles.", {})
            send_discord_notify(
                title="🔴 Human Review Required",
                message=f"**{content_title}** has been rejected {reject_count}x and needs manual review.",
                severity="high",
            )
            return

        # ─── Run the QA Review ────────────────────────────────────────────────
        scores = _score_content(content_body, content_type, task_id)

        accuracy        = scores.get("accuracy", 0)
        brand_voice     = scores.get("brand_voice", 0)
        seo             = scores.get("seo_optimization", 0)
        actionability   = scores.get("actionability", 0)
        feedback        = scores.get("feedback", "")
        avg_score       = round((accuracy + brand_voice + seo + actionability) / 4, 1)

        all_pass = all(s >= QA_MIN_SCORE for s in [accuracy, brand_voice, seo, actionability])

        log.info(
            f"QA Scores — Accuracy:{accuracy} Voice:{brand_voice} SEO:{seo} "
            f"Action:{actionability} | Avg:{avg_score} | Pass:{all_pass}",
            task_id=task_id,
        )

        if all_pass:
            # ── APPROVED ────────────────────────────────────────────────────
            _update_content_status(content_id, "approved")
            _log_to_qa_queue(content_id, from_agent, "qa_approved", "All criteria met.", scores)
            log_metric("qa_agent", "approved_score_avg", avg_score)
            log_agent_heartbeat("qa_agent")

            # ✨ Feed positive fitness signal back to the prompt that generated this content
            agent_id  = input_data.get("_agent_id", "content_agent")
            prompt_id = input_data.get("_agent_prompt_id") or input_data.get("_writer_prompt_id")
            
            if prompt_id:
                evolver = get_evolver(agent_id)
                evolver.record_outcome(prompt_id, avg_score)
                log.info(f"Fitness updated for {agent_id}: prompt={prompt_id[:8]} score={avg_score}", task_id=task_id)

            send_discord_notify(
                title="✅ Content Approved",
                message=f"**{content_title}** passed QA with avg score **{avg_score}/10**.",
                severity="success",
                fields=[
                    {"name": "Accuracy",       "value": f"`{accuracy}/10`",  "inline": True},
                    {"name": "Brand Voice",    "value": f"`{brand_voice}/10`","inline": True},
                    {"name": "SEO",            "value": f"`{seo}/10`",        "inline": True},
                    {"name": "Actionability",  "value": f"`{actionability}/10`","inline": True},
                ],
            )
        else:
            # ── REJECTED — send back to originating agent ────────────────────
            _update_content_status(content_id, "qa_rejected")
            _log_to_qa_queue(content_id, from_agent, "qa_rejected", feedback, scores)
            log_metric("qa_agent", "rejected_score_avg", avg_score)

            # ❌ Feed negative fitness signal — penalize the prompt that produced bad content
            agent_id  = input_data.get("_agent_id", "content_agent")
            prompt_id = input_data.get("_agent_prompt_id") or input_data.get("_writer_prompt_id")
            
            if prompt_id:
                evolver = get_evolver(agent_id)
                evolver.record_outcome(prompt_id, avg_score)
                log.info(f"Fitness penalized for {agent_id}: prompt={prompt_id[:8]} score={avg_score}", task_id=task_id)

            failing = {
                "Accuracy":      accuracy,
                "Brand Voice":   brand_voice,
                "SEO":           seo,
                "Actionability": actionability,
            }
            failing_criteria = [k for k, v in failing.items() if v < QA_MIN_SCORE]

            log.warning(
                f"REJECTED: '{content_title}'. Failing: {failing_criteria}. "
                f"Sending back to '{from_agent}'.",
                task_id=task_id,
            )

            # Re-dispatch to originating agent with feedback and incremented reject_count
            send_task(
                from_agent="qa_agent",
                to_agent=from_agent,
                task_type="rewrite_content",
                input_data={
                    **input_data,
                    "qa_feedback":       feedback,
                    "qa_failing_scores": {k: v for k, v in failing.items() if v < QA_MIN_SCORE},
                    "reject_count":      reject_count + 1,
                },
                priority="high",   # rewrites are high priority
            )

        duration = log.end_timer("qa_review", task_id=task_id)
        log.info(f"QA Agent complete. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"QA Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("qa_agent", "high", f"QA crashed on task {task_id}: {e}")
        raise
