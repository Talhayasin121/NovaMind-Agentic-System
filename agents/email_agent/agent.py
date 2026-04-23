"""
agents/email_agent/agent.py — NovaMind Email Agent (Automated Outreach)

Uses Brevo SMTP API (300 emails/day free — no credit card needed).
Built-in daily counter to NEVER exceed the 300/day limit.

3-step outreach sequence:
  Step 1: Personalized introduction
  Step 2: Value proposition (sent if step 1 was delivered)
  Step 3: Gentle follow-up with social proof

Each email is personalized by Groq using lead data (pain points, pitch angle).
"""
import os
import json
import uuid
import requests
from datetime import datetime, date, timezone

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import write_alert, log_agent_heartbeat, log_metric
from core.supabase_client import get_supabase
from core.logger import AgentLogger
from core.prompt_evolution import PromptEvolver

log = AgentLogger("email_agent")
_evolver = PromptEvolver("email_agent")

BREVO_API_KEY   = os.getenv("BREVO_API_KEY", "")
SENDER_EMAIL    = os.getenv("SENDER_EMAIL", "hello@novamind.ai")
SENDER_NAME     = os.getenv("SENDER_NAME",  "Alex @ NovaMind")
DAILY_LIMIT     = 290   # Stay 10 below Brevo's 300/day hard limit (safety buffer)

BREVO_SEND_URL  = "https://api.brevo.com/v3/smtp/email"

# ─── Daily Limit Tracking ──────────────────────────────────────────────────────

def _get_today_count() -> int:
    """Check how many emails were sent today via Supabase daily_limits table."""
    supabase = get_supabase()
    today    = date.today().isoformat()
    response = (
        supabase.table("daily_limits")
        .select("call_count")
        .eq("provider", "brevo")
        .eq("date", today)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0]["call_count"] if rows else 0


def _increment_today_count() -> None:
    """Upsert today's Brevo email count."""
    supabase = get_supabase()
    today    = date.today().isoformat()
    # Try to increment; if row doesn't exist, insert it
    existing = (
        supabase.table("daily_limits")
        .select("id, call_count")
        .eq("provider", "brevo")
        .eq("date", today)
        .limit(1)
        .execute()
    )
    if existing.data:
        row_id    = existing.data[0]["id"]
        new_count = existing.data[0]["call_count"] + 1
        supabase.table("daily_limits").update({"call_count": new_count}).eq("id", row_id).execute()
    else:
        supabase.table("daily_limits").insert({
            "id": str(uuid.uuid4()), "provider": "brevo",
            "date": today, "call_count": 1,
        }).execute()


# ─── Email Composition ─────────────────────────────────────────────────────────

_SEQUENCE_SUBJECTS = {
    1: "Quick question about {company}'s digital growth",
    2: "How NovaMind helped similar agencies 3x their leads",
    3: "Last note from NovaMind (genuinely useful)",
}

_DEFAULT_SYSTEM = (
    "You are a skilled B2B sales email writer specializing in digital agency outreach. "
    "Write concise (< 180 words), personalized, non-spammy emails. "
    "Humanize the tone — NOT corporate. Sound like a real person who did their research. "
    "Return ONLY the email body as plain text. No subject line, no sign-off (added separately)."
)

_DEFAULT_TEMPLATES = {
    1: (
        "Write a cold outreach email to {name} at {company}. "
        "Their main pain points are: {pain_points}. "
        "Angle: {pitch_angle}. "
        "Goal: introduce NovaMind AI agency, ask for a 15-min call. "
        "Keep it under 150 words."
    ),
    2: (
        "Write a follow-up email to {name} at {company} who didn't reply. "
        "Mention a specific result (e.g. '47% increase in organic traffic for a similar client'). "
        "Their pain points: {pain_points}. "
        "Be genuinely helpful. Under 120 words."
    ),
    3: (
        "Write a final follow-up 'breakup' email to {name} at {company}. "
        "Acknowledge they may not be interested. "
        "Leave with a useful resource or tip related to: {pain_points}. "
        "Under 100 words. Warm, not pushy."
    ),
}


def _compose_email(
    step: int, name: str, company: str,
    pain_points: list, pitch: str, task_id: str
) -> tuple[str, str, dict]:
    """Returns (subject, body, evolution_meta) for the given sequence step."""
    pain_str = ", ".join(pain_points[:3]) or "growth and lead generation"
    subject  = _SEQUENCE_SUBJECTS.get(step, "Following up from NovaMind").format(
        name=name, company=company,
    )
    
    # Get Evolved DNA for this specific step
    prompt_name = f"outreach_step_{step}"
    dna = _evolver.get_prompt(prompt_name, default_system=_DEFAULT_SYSTEM, 
                             default_template=_DEFAULT_TEMPLATES.get(step, _DEFAULT_TEMPLATES[1]))
    
    prompt = dna.template.format(
        name=name, company=company,
        pain_points=pain_str, pitch_angle=pitch,
    )
    
    body = invoke_llm(prompt, system_prompt=dna.system_prompt,
                      tier=LLMTier.FAST, temperature=0.8, task_id=task_id)
    
    sign_off = f"\n\nBest,\n{SENDER_NAME}\nNovaMind Digital Agency"
    evolution_meta = {"prompt_id": dna.id, "generation": dna.generation}
    
    return subject, body.strip() + sign_off, evolution_meta



def _send_via_brevo(to_email: str, to_name: str, subject: str, body: str) -> bool:
    """Send email via Brevo transactional API. Returns True on success."""
    if not BREVO_API_KEY:
        log.warning("BREVO_API_KEY not set — email send skipped (dry run).")
        return False

    payload = {
        "sender":      {"email": SENDER_EMAIL, "name": SENDER_NAME},
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     subject,
        "textContent": body,
    }
    try:
        r = requests.post(
            BREVO_SEND_URL,
            headers={
                "api-key":      BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Brevo send failed: {e}")
        return False


def _update_lead_outreach_step(lead_id: str, step: int) -> None:
    if not lead_id:
        return
    get_supabase().table("leads").update({
        "outreach_step": f"step_{step}_sent",
        "status":        "contacted",
    }).eq("id", lead_id).execute()


# ─── Main Entry Point ──────────────────────────────────────────────────────────

def run_email_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})

    name         = input_data.get("name", "there")
    email        = input_data.get("email", "")
    company      = input_data.get("name", name)
    pain_points  = input_data.get("pain_points", [])
    pitch        = input_data.get("pitch_angle", "")
    lead_id      = input_data.get("lead_id", "")
    step         = int(input_data.get("sequence_step", 1))

    log.info(f"Email Agent: step {step} for '{name}' <{email}>", task_id=task_id)

    if not email:
        log.warning("No email address provided — skipping.", task_id=task_id)
        return

    try:
        # Step 1: Check daily send limit
        today_count = _get_today_count()
        if today_count >= DAILY_LIMIT:
            log.warning(
                f"Daily Brevo limit reached ({today_count}/{DAILY_LIMIT}). "
                f"Skipping email to {email}.",
                task_id=task_id,
            )
            write_alert("email_agent", "warning",
                        f"Brevo daily limit reached ({today_count}). Emails paused until tomorrow.")
            return

        # Step 2: Compose email using Groq
        subject, body, evolution_meta = _compose_email(step, name, company, pain_points, pitch, task_id)

        # Step 3: Send via Brevo
        sent = _send_via_brevo(email, name, subject, body)

        if sent:
            _increment_today_count()
            _update_lead_outreach_step(lead_id, step)
            log_metric("email_agent", "emails_sent_today", _get_today_count())
            log.info(f"Email sent to {email} (step {step}). Daily count: {_get_today_count()}", task_id=task_id)
        else:
            log.warning(f"Email to {email} was not sent (API not configured or failed).", task_id=task_id)

        log_agent_heartbeat("email_agent")

    except Exception as e:
        log.error(f"Email Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("email_agent", "high", f"Task {task_id} failed: {e}")
        raise
