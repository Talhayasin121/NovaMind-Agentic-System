"""
agents/sales_agent/agent.py — NovaMind Sales Agent (Lead Generation)

Workflow:
  1. Receive lead_gen task from CEO
  2. DuckDuckGo for companies matching the target criteria
  3. Scrape company websites for contact emails via BeautifulSoup
  4. Score each lead 1-10 with Groq (fit criteria: size, industry, pain points)
  5. Write qualified leads (score >= 6) to leads table
  6. Dispatch high-value leads (score >= 8) to CRM Agent
"""
import json
import uuid
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import send_task, write_alert, log_agent_heartbeat, log_metric
from core.supabase_client import get_supabase
from core.logger import AgentLogger
from core.prompt_evolution import PromptEvolver

log = AgentLogger("sales_agent")
_evolver = PromptEvolver("sales_agent")

_SCORER_SYSTEM = (
    "You are a B2B sales qualification expert. "
    "Score leads on how likely they are to need digital marketing agency services. "
    "Return ONLY valid JSON: "
    '{"score": <1-10>, "company_size": "<estimate>", "pain_points": ["..."], "pitch_angle": "..."}'
)

_EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)


# ─── Lead Discovery ────────────────────────────────────────────────────────────

def _search_target_companies(target: str, n: int = 6) -> list[dict]:
    """Find companies matching the target niche via DuckDuckGo."""
    try:
        query   = f"{target} company website contact"
        results = DDGS().text(query, max_results=n)
        return results or []
    except Exception as e:
        log.warning(f"DuckDuckGo company search failed: {e}")
        return []


def _extract_email_from_url(url: str) -> str | None:
    """
    Try to scrape an email address from a company's contact page.
    Tries /contact, /contact-us, /about — gracefully falls back.
    """
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    contact_paths = ["/contact", "/contact-us", "/about", "/about-us", ""]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
    }

    for path in contact_paths:
        try:
            resp = httpx.get(base + path, timeout=5, headers=headers, follow_redirects=True)
            if resp.status_code == 200:
                emails = _EMAIL_REGEX.findall(resp.text)
                # Filter out common non-useful emails
                filtered = [
                    e for e in set(emails)
                    if not any(skip in e for skip in
                               ["sentry", "example", "noreply", "no-reply", "support@sentry",
                                "wixpress", "schema", ".png", ".jpg", "webpack"])
                ]
                if filtered:
                    return filtered[0]   # Return first real email found
        except Exception:
            continue
    return None


def _score_lead(company_name: str, snippet: str, task_id: str) -> tuple[dict, dict]:
    """Use evolved DNA to score and qualify a lead."""
    dna = _evolver.get_prompt("lead_scoring", default_system=_SCORER_SYSTEM, default_template=(
        "Qualify this company as a digital marketing agency lead:\n"
        "Company: {company_name}\n"
        "Info: {snippet}\n\n"
        "Score based on: likely marketing budget, digital presence gaps, company maturity."
    ))

    prompt = dna.template.format(company_name=company_name, snippet=snippet[:400])
    raw = invoke_llm(prompt, system_prompt=dna.system_prompt, tier=LLMTier.FAST,
                     temperature=0.3, task_id=task_id)
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean), {"prompt_id": dna.id, "generation": dna.generation}
    except Exception:
        return {"score": 5, "company_size": "unknown", "pain_points": [], "pitch_angle": ""}, {"prompt_id": dna.id, "generation": dna.generation}



# ─── Main Entry Point ──────────────────────────────────────────────────────────

def run_sales_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    target     = input_data.get("target", "small digital marketing businesses")

    log.info(f"Sales Agent starting — target: '{target}'", task_id=task_id)
    log.start_timer("sales_full_run")

    supabase       = get_supabase()
    qualified      = 0
    high_value     = 0

    try:
        companies = _search_target_companies(target)
        log.info(f"Found {len(companies)} candidate companies.", task_id=task_id)

        for company in companies:
            url   = company.get("href", "")
            name  = company.get("title", "Unknown Company")[:100]
            body  = company.get("body", "")

            if not url:
                continue

            # Score the lead
            scoring, evolution_meta = _score_lead(name, body, task_id)
            score = scoring.get("score", 0)

            log.info(f"Lead '{name}' scored {score}/10", task_id=task_id)

            if score < 6:
                continue    # Not worth pursuing

            # Try to find a contact email
            email = _extract_email_from_url(url)

            # Write to leads table
            lead_id = str(uuid.uuid4())
            supabase.table("leads").insert({
                "id":            lead_id,
                "name":          name,
                "email":         email or "",
                "company":       name,
                "score":         score,
                "status":        "new",
                "outreach_step": "discovery",
                "created_at":    datetime.now(timezone.utc).isoformat(),
            }).execute()

            qualified += 1

            # High-value leads go to CRM immediately
            if score >= 8:
                high_value += 1
                send_task(
                    from_agent="sales_agent",
                    to_agent="crm_agent",
                    task_type="sync_lead",
                    input_data={
                        "lead_id":     lead_id,
                        "name":        name,
                        "email":       email or "",
                        "score":       score,
                        "pain_points": scoring.get("pain_points", []),
                        "pitch_angle": scoring.get("pitch_angle", ""),
                    },
                    priority="high",
                )

        log_metric("sales_agent", "leads_qualified", qualified)
        log_metric("sales_agent", "leads_high_value", high_value)
        log_agent_heartbeat("sales_agent")

        duration = log.end_timer("sales_full_run", task_id=task_id)
        log.info(
            f"Sales Agent complete. Qualified: {qualified}, High-value: {high_value}. "
            f"Duration: {duration}ms",
            task_id=task_id,
        )

    except Exception as e:
        log.error(f"Sales Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("sales_agent", "high", f"Task {task_id} failed: {e}")
        raise
