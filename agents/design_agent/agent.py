"""
agents/design_agent/agent.py — NovaMind Design Agent

Uses Pollinations.ai (free, no API key) to generate:
  - Blog hero images (1200x630)
  - Social media graphics (1080x1080)
  - Twitter/X card images (1200x628)

Workflow:
  1. Receive content brief from CEO or Content Agent
  2. Use Groq to craft an optimized image generation prompt
  3. Build Pollinations.ai URLs (no API call — pure HTTP GET)
  4. Save image URLs to agent_outputs
  5. Send to QA for review
"""
import uuid
from urllib.parse import quote
from datetime import datetime, timezone

import requests

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import (
    send_task, write_alert, log_agent_heartbeat, log_metric,
)
from core.supabase_client import get_supabase
from core.logger import AgentLogger

log = AgentLogger("design_agent")

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

_PROMPT_ENGINEER_SYSTEM = (
    "You are an expert AI image prompt engineer. "
    "Create highly detailed, visual image prompts for Stable Diffusion. "
    "Focus on: photography style, lighting, color palette, composition. "
    "Keep prompts under 200 characters. "
    "Return ONLY the prompt text, no explanation."
)


def _build_image_url(prompt: str, width: int, height: int, seed: int = 42) -> str:
    """Build a Pollinations.ai URL. No API key needed."""
    encoded = quote(prompt)
    return f"{POLLINATIONS_BASE}/{encoded}?width={width}&height={height}&seed={seed}&nologo=true"


def _craft_prompt(brief: str, style: str, task_id: str) -> str:
    """Use Groq to generate an optimized image prompt from a content brief."""
    prompt = (
        f"Create a {style} image prompt for: {brief[:200]}\n"
        f"Make it visually stunning, professional, and appropriate for a digital agency brand."
    )
    return invoke_llm(
        prompt,
        system_prompt=_PROMPT_ENGINEER_SYSTEM,
        tier=LLMTier.FAST,
        temperature=0.9,
        task_id=task_id,
    ).strip()


def _verify_image_accessible(url: str) -> bool:
    """Check if Pollinations returned a valid image (non-blocking, 5s timeout)."""
    try:
        r = requests.head(url, timeout=5, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def run_design_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    brief      = input_data.get("content_brief", input_data.get("strategy_brief", "Digital marketing"))
    title      = input_data.get("content_title", brief[:60])

    log.info(f"Design Agent starting for: '{title}'", task_id=task_id)
    log.start_timer("design_full_run")

    try:
        # ── 1. Craft optimized image prompts ──────────────────────────────────
        hero_prompt    = _craft_prompt(brief, "blog hero banner, cinematic photography", task_id)
        social_prompt  = _craft_prompt(brief, "social media post, bold typography, vibrant", task_id)

        # ── 2. Build Pollinations URLs (no API calls — just URLs) ─────────────
        hero_url       = _build_image_url(hero_prompt,   1200, 630,  seed=101)
        social_url     = _build_image_url(social_prompt, 1080, 1080, seed=202)
        twitter_url    = _build_image_url(hero_prompt,   1200, 628,  seed=303)

        assets = {
            "hero_image":    {"url": hero_url,    "width": 1200, "height": 630,  "prompt": hero_prompt},
            "social_image":  {"url": social_url,  "width": 1080, "height": 1080, "prompt": social_prompt},
            "twitter_image": {"url": twitter_url, "width": 1200, "height": 628,  "prompt": hero_prompt},
        }

        log.info(f"Generated {len(assets)} image URLs via Pollinations.ai", task_id=task_id)

        # ── 3. Save to agent_outputs ──────────────────────────────────────────
        supabase = get_supabase()
        output_id = str(uuid.uuid4())
        supabase.table("agent_outputs").insert({
            "id":          output_id,
            "agent_id":    "design_agent",
            "output_type": "visual_assets",
            "content":     assets,
            "qa_status":   "pending",
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()

        log_metric("design_agent", "assets_generated", len(assets))
        log_agent_heartbeat("design_agent")

        # ── 4. Dispatch to QA ─────────────────────────────────────────────────
        send_task(
            from_agent="design_agent",
            to_agent="qa_agent",
            task_type="review_content",
            input_data={
                "content_queue_id": None,
                "content_title":    f"Visual Assets: {title}",
                "content_body":     str(assets),
                "content_type":     "visual_assets",
                "output_id":        output_id,
                "reject_count":     0,
            },
        )

        duration = log.end_timer("design_full_run", task_id=task_id)
        log.info(f"Design Agent complete. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"Design Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("design_agent", "high", f"Task {task_id} failed: {e}")
        raise
