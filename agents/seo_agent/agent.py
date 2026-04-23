"""
agents/seo_agent/agent.py — NovaMind SEO Agent

Workflow:
  1. Receive gap_analysis task from CEO
  2. Search DuckDuckGo for competitor content on the target topic
  3. Extract and cluster keywords using Groq
  4. Compare against existing content_queue to find gaps
  5. Generate meta titles, descriptions, and heading structures
  6. Save SEO report to agent_outputs and dispatch to QA
"""
import json
import uuid
from datetime import datetime, timezone

from duckduckgo_search import DDGS

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import send_task, write_alert, log_agent_heartbeat, log_metric
from core.supabase_client import get_supabase
from core.logger import AgentLogger
from core.prompt_evolution import get_evolver

log = AgentLogger("seo_agent")
_evolver = get_evolver("seo_agent")

# Default system prompts (used if no evolved templates exist yet)
_SEO_SYSTEM_DEFAULT = (
    "You are an expert SEO strategist. Analyze competitor content and extract actionable insights. "
    "Return ONLY valid JSON. No markdown fences."
)

_META_SYSTEM_DEFAULT = (
    "You are an SEO copywriter. Generate meta tags and content structure. "
    "Return ONLY valid JSON with keys: title, meta_description, h1, h2_sections (list of strings)."
)


def _search_competitors(topic: str, n: int = 8) -> list[dict]:
    """Find competitor articles on DuckDuckGo."""
    try:
        results = DDGS().text(topic + " guide tips strategy", max_results=n)
        return results or []
    except Exception as e:
        log.warning(f"DuckDuckGo failed: {e}")
        return []


def _extract_keyword_clusters(competitor_texts: str, topic: str, task_id: str) -> tuple[dict, str]:
    """Use Groq to extract keyword clusters. Returns (data, prompt_id)."""
    dna = _evolver.get_prompt("seo_analyst", _SEO_SYSTEM_DEFAULT)
    system_prompt = dna.system_prompt
    prompt_id = dna.id
    
    user_prompt = (
        f"Analyze these competitor article titles and snippets about '{topic}'.\n\n"
        f"{competitor_texts[:3000]}\n\n"
        f"Extract: primary keywords, long-tail variations, topic clusters, and content gaps. "
        f"Return JSON: {{\"primary_keywords\": [], \"long_tail\": [], \"topic_clusters\": [], "
        f"\"content_gaps\": []}}"
    )
    raw = invoke_llm(user_prompt, system_prompt=system_prompt, tier=LLMTier.FAST,
                     temperature=0.3, task_id=task_id)
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean), prompt_id
    except Exception:
        return {"primary_keywords": [], "long_tail": [], "topic_clusters": [], "content_gaps": []}, prompt_id


def _generate_meta_tags(topic: str, keywords: list, task_id: str) -> tuple[dict, str]:
    """Generate SEO-optimized meta tags and heading structure. Returns (data, prompt_id)."""
    dna = _evolver.get_prompt("seo_copywriter", _META_SYSTEM_DEFAULT)
    system_prompt = dna.system_prompt
    prompt_id = dna.id
    
    kw_str = ", ".join(keywords[:10])
    user_prompt = (
        f"Create SEO-optimized meta tags for a piece about '{topic}'. "
        f"Target keywords: {kw_str}. "
        f"Generate: title (50-60 chars), meta_description (150-160 chars), H1, and 4 H2 sections."
    )
    raw = invoke_llm(user_prompt, system_prompt=system_prompt, tier=LLMTier.FAST,
                     temperature=0.5, task_id=task_id)
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean), prompt_id
    except Exception:
        return {"title": topic, "meta_description": "", "h1": topic, "h2_sections": []}, prompt_id


def run_seo_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    topic      = input_data.get("topic", "digital marketing trends 2025")

    log.info(f"SEO Agent starting for topic: '{topic}'", task_id=task_id)
    log.start_timer("seo_full_run")

    try:
        # Step 1: Research competitors
        competitors = _search_competitors(topic)
        comp_text   = "\n".join([f"- {r['title']}: {r['body'][:200]}" for r in competitors])
        log.info(f"Found {len(competitors)} competitor articles.", task_id=task_id)

        # Step 2: Extract keywords
        clusters, analyst_prompt_id = _extract_keyword_clusters(comp_text, topic, task_id)
        log.info(f"Extracted keywords. PromptDNA: {analyst_prompt_id[:8]}...", task_id=task_id)

        # Step 3: Generate meta tags
        meta, copywriter_prompt_id = _generate_meta_tags(topic, clusters.get("primary_keywords", []), task_id)
        log.info(f"Generated meta tags. PromptDNA: {copywriter_prompt_id[:8]}...", task_id=task_id)

        # Step 4: Build final SEO report
        seo_report = {
            "topic":           topic,
            "keyword_clusters": clusters,
            "meta_tags":       meta,
            "competitor_count": len(competitors),
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "_analyst_prompt_id": analyst_prompt_id,
            "_copywriter_prompt_id": copywriter_prompt_id
        }

        # Step 5: Save to agent_outputs
        supabase  = get_supabase()
        output_id = str(uuid.uuid4())
        supabase.table("agent_outputs").insert({
            "id":          output_id,
            "agent_id":    "seo_agent",
            "output_type": "seo_report",
            "content":     seo_report,
            "qa_status":   "pending",
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()

        log_metric("seo_agent", "keywords_found", len(clusters.get("primary_keywords", [])))
        log_metric("seo_agent", "gaps_identified", len(clusters.get("content_gaps", [])))
        log_agent_heartbeat("seo_agent")

        # Step 6: Send to QA
        send_task(
            from_agent="seo_agent",
            to_agent="qa_agent",
            task_type="review_content",
            input_data={
                "content_queue_id": None,
                "content_title":    f"SEO Report: {topic}",
                "content_body":     json.dumps(seo_report, indent=2),
                "content_type":     "seo_report",
                "output_id":        output_id,
                "reject_count":     0,
                "_agent_prompt_id": analyst_prompt_id, # Using analyst prompt as primary fitness signal
                "_agent_id":        "seo_agent"
            },
        )

        duration = log.end_timer("seo_full_run", task_id=task_id)
        log.info(f"SEO Agent complete. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"SEO Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("seo_agent", "high", f"Task {task_id} failed: {e}")
        raise
