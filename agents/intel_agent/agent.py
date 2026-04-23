"""
agents/intel_agent/agent.py — NovaMind Competitive Intelligence Agent

Competitive Intelligence War Room:
  1. Load active competitor targets from Supabase
  2. Scrape each competitor site for new content (httpx, no headless browser needed)
  3. Diff against last scrape — detect NEW pages/posts
  4. Use Gemini to generate a competitive analysis and opportunity brief
  5. Store intel in competitor_intel table
  6. Create a task for CEO Agent with the full intel brief
  7. Dispatch keyword gaps to SEO Agent for immediate exploitation
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from duckduckgo_search import DDGS

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import send_task, write_alert, log_agent_heartbeat, log_metric
from core.supabase_client import get_supabase
from core.logger import AgentLogger

log = AgentLogger("intel_agent")

# Headers that make us look like a normal browser
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ─── Scraping Utilities ────────────────────────────────────────────────────────

def _fetch_page(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch raw HTML from a URL. Returns None on failure."""
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {e}")
        return None


def _extract_links_and_titles(html: str, base_url: str) -> list[dict]:
    """
    Extract article/blog links from a page HTML.
    Simple regex-based extraction — no BS4 dependency needed.
    """
    # Find <a href="...">title</a> patterns
    pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]{10,100})</a>', re.IGNORECASE)
    matches = pattern.findall(html)

    links = []
    for href, title in matches:
        title = re.sub(r'\s+', ' ', title).strip()
        # Only include links that look like article URLs
        if not title or len(title) < 10:
            continue
        # Make absolute URL
        if href.startswith('/'):
            domain = '/'.join(base_url.split('/')[:3])
            href = domain + href
        elif not href.startswith('http'):
            continue
        links.append({"url": href, "title": title})

    return links[:30]  # Cap at 30 most recent


def _content_fingerprint(links: list[dict]) -> str:
    """Create a stable hash of page content for diffing."""
    content = "|".join(sorted(f"{l['url']}:{l['title']}" for l in links))
    return hashlib.md5(content.encode()).hexdigest()


def _load_competitors() -> list[dict]:
    """Load active competitor targets from Supabase."""
    supabase = get_supabase()
    rows = (
        supabase.table("competitor_targets")
        .select("*")
        .eq("active", True)
        .execute()
        .data or []
    )
    return rows


def _get_last_intel(competitor_id: str) -> Optional[dict]:
    """Get the most recent intel record for a competitor."""
    supabase = get_supabase()
    rows = (
        supabase.table("competitor_intel")
        .select("detected_urls, scraped_at")
        .eq("competitor_id", competitor_id)
        .order("scraped_at", desc=True)
        .limit(1)
        .execute()
        .data or []
    )
    return rows[0] if rows else None


def _find_new_content(current: list[dict], previous_urls: list[str]) -> list[dict]:
    """Detect new content by comparing current URLs to previously seen URLs."""
    prev_set = set(previous_urls)
    return [link for link in current if link["url"] not in prev_set]


def _analyze_competitor(
    competitor_name: str,
    new_content: list[dict],
    task_id: str,
) -> dict:
    """Use Gemini to generate competitive analysis from new content."""
    if not new_content:
        return {"analysis": "No new content detected.", "opportunities": []}

    content_list = "\n".join(
        f"- {item['title']} ({item['url']})" for item in new_content[:10]
    )

    prompt = (
        f"You are a competitive intelligence analyst for NovaMind, an AI digital marketing agency.\n\n"
        f"Competitor: {competitor_name}\n"
        f"New content published this cycle:\n{content_list}\n\n"
        f"Analyze this and return a JSON response with:\n"
        f"1. 'analysis': 2-3 paragraph competitive analysis (what topics they're pushing, their strategy)\n"
        f"2. 'opportunities': list of 3-5 specific content/keyword opportunities WE should exploit\n"
        f"3. 'threats': list of 2-3 areas where their new content threatens our positioning\n"
        f"4. 'counter_topics': list of 3 specific blog post titles we should write as counter-content\n\n"
        f"Return ONLY valid JSON, no markdown fences."
    )

    raw = invoke_llm(prompt, tier=LLMTier.DEEP, temperature=0.4, task_id=task_id)
    try:
        import json
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except Exception:
        return {"analysis": raw[:500], "opportunities": [], "threats": [], "counter_topics": []}


def _save_intel(competitor: dict, new_content: list[dict], all_links: list[dict], analysis: dict) -> None:
    """Persist competitive intel to Supabase."""
    supabase = get_supabase()

    intel_id = str(uuid.uuid4())
    supabase.table("competitor_intel").insert({
        "id":               intel_id,
        "competitor_id":    competitor["id"],
        "competitor_name":  competitor["name"],
        "detected_urls":    new_content,
        "content_diff":     f"{len(new_content)} new pieces detected",
        "analysis":         analysis.get("analysis", ""),
        "opportunities":    analysis.get("opportunities", []),
        "scraped_at":       datetime.now(timezone.utc).isoformat(),
    }).execute()

    # Update last_scraped timestamp
    supabase.table("competitor_targets").update({
        "last_scraped": datetime.now(timezone.utc).isoformat()
    }).eq("id", competitor["id"]).execute()


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def run_intel_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    log.info("Intel Agent starting competitive sweep.", task_id=task_id)
    log.start_timer("intel_sweep")

    try:
        competitors = _load_competitors()
        if not competitors:
            log.warning("No active competitors configured. Add entries to competitor_targets table.", task_id=task_id)
            return

        log.info(f"Scanning {len(competitors)} competitors.", task_id=task_id)

        all_opportunities  = []
        all_counter_topics = []
        intel_summary      = []

        for competitor in competitors:
            name = competitor["name"]
            url  = competitor["url"]
            log.info(f"Scraping: {name} ({url})", task_id=task_id)

            html = _fetch_page(url)
            if not html:
                continue

            current_links = _extract_links_and_titles(html, url)
            last_intel    = _get_last_intel(competitor["id"])

            if last_intel:
                prev_urls   = [l.get("url", "") for l in (last_intel.get("detected_urls") or [])]
                new_content = _find_new_content(current_links, prev_urls)
                log.info(f"{name}: {len(new_content)} new pieces found.", task_id=task_id)
            else:
                # First time scraping — everything is "new" but log it baseline
                new_content = current_links[:5]   # Seed with recent 5
                log.info(f"{name}: First scrape (baseline). Seeding with {len(new_content)} items.", task_id=task_id)

            if not new_content:
                log.info(f"{name}: No new content since last scan. Skipping analysis.", task_id=task_id)
                _save_intel(competitor, [], current_links, {"analysis": "No change detected."})
                continue

            # Analyze with Gemini
            analysis = _analyze_competitor(name, new_content, task_id)
            _save_intel(competitor, new_content, current_links, analysis)

            all_opportunities.extend(analysis.get("opportunities", []))
            all_counter_topics.extend(analysis.get("counter_topics", []))
            intel_summary.append({
                "competitor": name,
                "new_pieces": len(new_content),
                "analysis_snippet": analysis.get("analysis", "")[:200],
            })

            log_metric("intel_agent", f"new_content_{name.lower().replace(' ', '_')}", len(new_content))

        # Dispatch CEO brief
        if intel_summary:
            send_task(
                from_agent="intel_agent",
                to_agent="ceo_agent",
                task_type="competitive_brief",
                input_data={
                    "intel_summary":    intel_summary,
                    "opportunities":    all_opportunities[:10],
                    "counter_topics":   all_counter_topics[:6],
                    "source":           "competitive_intelligence",
                },
                priority="normal",
            )
            log.info(f"CEO brief dispatched: {len(intel_summary)} competitors analyzed.", task_id=task_id)

        # Dispatch top keyword opportunities to SEO Agent
        if all_opportunities:
            send_task(
                from_agent="intel_agent",
                to_agent="seo_agent",
                task_type="competitor_keyword_gaps",
                input_data={
                    "keyword_opportunities": all_opportunities[:5],
                    "counter_topics":        all_counter_topics[:3],
                    "source":                "intel_agent",
                },
                priority="normal",
            )

        log_agent_heartbeat("intel_agent")
        duration = log.end_timer("intel_sweep", task_id=task_id)
        log.info(
            f"Intel sweep complete. {len(competitors)} competitors scanned. Duration: {duration}ms",
            task_id=task_id,
        )

    except Exception as e:
        log.error(f"Intel Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("intel_agent", "high", f"Intel sweep failed: {e}")
        raise
