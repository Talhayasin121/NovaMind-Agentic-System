"""
agents/crm_agent/agent.py — NovaMind CRM Agent (HubSpot Sync)

Uses HubSpot Free CRM API (250K req/day, 100 req/10s burst limit).
No cost. No credit card. Just a HubSpot private app token.

Workflow:
  1. Receive sync_lead task from Sales Agent
  2. Check if contact already exists in HubSpot (dedup by email)
  3. Create or update the Contact in HubSpot
  4. Log the HubSpot contact ID back to Supabase leads table
  5. Dispatch to Email Agent for first outreach sequence
"""
import os
import json
import time
import requests
from datetime import datetime, timezone

from core.message_bus import send_task, write_alert, log_agent_heartbeat, log_metric
from core.supabase_client import get_supabase
from core.config import GROQ_API_KEY  # just to confirm env is loading
from core.logger import AgentLogger

log = AgentLogger("crm_agent")

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
HUBSPOT_BASE  = "https://api.hubapi.com"
HEADERS = lambda: {
    "Authorization": f"Bearer {HUBSPOT_TOKEN}",
    "Content-Type":  "application/json",
}


# ─── HubSpot API Helpers ───────────────────────────────────────────────────────

def _hs_request(method: str, path: str, data: dict | None = None, retries: int = 3) -> dict | None:
    """
    Make a HubSpot API call with retry and exponential backoff for 429 errors.
    """
    if not HUBSPOT_TOKEN:
        log.warning("HUBSPOT_TOKEN not set — CRM operations will be skipped.")
        return None

    url = f"{HUBSPOT_BASE}{path}"
    for attempt in range(1, retries + 1):
        try:
            if method == "GET":
                r = requests.get(url, headers=HEADERS(), timeout=10)
            elif method == "POST":
                r = requests.post(url, headers=HEADERS(), json=data, timeout=10)
            elif method == "PATCH":
                r = requests.patch(url, headers=HEADERS(), json=data, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if r.status_code == 429:
                wait = 2 ** attempt
                log.warning(f"HubSpot rate limit hit. Waiting {wait}s (attempt {attempt})")
                time.sleep(wait)
                continue

            r.raise_for_status()
            return r.json() if r.content else {}

        except requests.exceptions.RequestException as e:
            log.error(f"HubSpot API error (attempt {attempt}): {e}")
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)

    return None


def _find_contact_by_email(email: str) -> str | None:
    """Return existing HubSpot contact ID if email exists, else None."""
    if not email:
        return None
    result = _hs_request(
        "POST",
        "/crm/v3/objects/contacts/search",
        data={
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator":     "EQ",
                    "value":        email,
                }]
            }],
            "properties": ["email", "firstname", "company"],
            "limit": 1,
        }
    )
    if result and result.get("total", 0) > 0:
        return result["results"][0]["id"]
    return None


def _create_contact(name: str, email: str, company: str, score: int, pitch: str) -> str | None:
    """Create a new HubSpot contact. Returns the new contact ID."""
    parts     = name.split(" ", 1)
    firstname = parts[0]
    lastname  = parts[1] if len(parts) > 1 else ""

    result = _hs_request(
        "POST",
        "/crm/v3/objects/contacts",
        data={
            "properties": {
                "email":     email,
                "firstname": firstname,
                "lastname":  lastname,
                "company":   company,
                "leadsource": "NovaMind AI Sales Agent",
                "description": f"Lead score: {score}/10. Pitch: {pitch}",
            }
        }
    )
    return result.get("id") if result else None


def _update_contact(contact_id: str, score: int, pitch: str) -> None:
    """Update an existing contact with latest scoring data."""
    _hs_request(
        "PATCH",
        f"/crm/v3/objects/contacts/{contact_id}",
        data={
            "properties": {
                "description": f"Lead score: {score}/10. Pitch angle: {pitch}. Updated by NovaMind.",
            }
        }
    )


def _update_supabase_lead(lead_id: str, hubspot_id: str) -> None:
    """Write the HubSpot contact ID back to the Supabase leads record."""
    if not lead_id:
        return
    get_supabase().table("leads").update({
        "hubspot_id": hubspot_id,
        "status":     "crm_synced",
    }).eq("id", lead_id).execute()


# ─── Main Entry Point ──────────────────────────────────────────────────────────

def run_crm_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})

    name        = input_data.get("name", "Unknown")
    email       = input_data.get("email", "")
    company     = input_data.get("name", name)
    score       = input_data.get("score", 5)
    pain_points = input_data.get("pain_points", [])
    pitch       = input_data.get("pitch_angle", "")
    lead_id     = input_data.get("lead_id", "")

    log.info(f"CRM Agent: syncing '{name}' ({email}) to HubSpot", task_id=task_id)
    log.start_timer("crm_sync")

    try:
        # Step 1: Check for duplicates
        existing_id = _find_contact_by_email(email)

        if existing_id:
            log.info(f"Contact exists in HubSpot (id={existing_id}). Updating.", task_id=task_id)
            _update_contact(existing_id, score, pitch)
            hub_id = existing_id
        else:
            log.info(f"Creating new HubSpot contact for '{name}'.", task_id=task_id)
            hub_id = _create_contact(name, email, company, score, pitch)
            if hub_id:
                log.info(f"HubSpot contact created: id={hub_id}", task_id=task_id)
            else:
                log.warning("HubSpot contact creation returned no ID (token may not be set).", task_id=task_id)

        # Step 2: Update Supabase
        if hub_id and lead_id:
            _update_supabase_lead(lead_id, hub_id)

        # Step 3: Trigger Email Agent for first outreach
        if email:
            send_task(
                from_agent="crm_agent",
                to_agent="email_agent",
                task_type="send_outreach",
                input_data={
                    "lead_id":     lead_id,
                    "name":        name,
                    "email":       email,
                    "score":       score,
                    "pain_points": pain_points,
                    "pitch_angle": pitch,
                    "sequence_step": 1,
                },
                priority="normal",
            )
            log.info(f"Email Agent dispatch triggered for {email}", task_id=task_id)

        log_metric("crm_agent", "contacts_synced", 1)
        log_agent_heartbeat("crm_agent")

        duration = log.end_timer("crm_sync", task_id=task_id)
        log.info(f"CRM Agent complete. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"CRM Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("crm_agent", "high", f"Task {task_id} failed: {e}")
        raise
