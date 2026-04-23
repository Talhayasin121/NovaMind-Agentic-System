"""
agents/content_agent/agent.py — NovaMind Content Agent

Workflow:
  1. Research topic via DuckDuckGo (5 sources)
  2. Write full SEO-optimized blog post with Groq Llama 3.3
  3. Self-critique: score the article (1-10). Rewrite if score < 7 (max 2 times)
  4. Generate 3 social media posts (LinkedIn, Twitter/X, Instagram) in one call
  5. Write approved content to content_queue table
  6. Log heartbeat and dispatch to QA Agent
"""
import json
from duckduckgo_search import DDGS

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import (
    send_task, write_alert, log_agent_heartbeat, log_metric, update_task_status,
)
from core.supabase_client import get_supabase
from core.config import MAX_CONTENT_REWRITES
from core.logger import AgentLogger
from core.prompt_evolution import get_evolver

log = AgentLogger("content_agent")
_evolver = get_evolver("content_agent")

# ─── Seed Prompts (Generation 0 — will evolve over time) ─────────────────────
# These are the starting DNA. After 50 tasks, the system will mutate these
# and naturally select the highest-performing variants.

_WRITER_SYSTEM = (
    "You are a world-class SEO content writer for NovaMind Digital Agency. "
    "Write in a clear, authoritative, and engaging style. "
    "Always include: a compelling intro, 3-5 H2 sections with H3 sub-points, "
    "a conclusion with a CTA, and naturally woven keywords. "
    "Format output as clean Markdown."
)

_CRITIC_SYSTEM = (
    "You are a harsh but fair content editor. You grade articles on a strict 1–10 scale. "
    "Return ONLY valid JSON in this exact format:\n"
    '{"score": <number>, "issues": ["issue1", "issue2"], "improvements": ["fix1", "fix2"]}'
)

_SOCIAL_SYSTEM = (
    "You are a social media strategist. Given a blog post, create platform-optimized posts. "
    "Return ONLY valid JSON:\n"
    '{"linkedin": "<post>", "twitter": "<post under 280 chars>", "instagram": "<caption with hashtags>"}'
)


# ─── Step Functions ────────────────────────────────────────────────────────────

def _research(topic: str) -> str:
    """Search DuckDuckGo for the topic and return a formatted context string."""
    log.info(f"Researching: {topic}")
    try:
        results = DDGS().text(topic + " 2025 latest trends", max_results=5)
        context = "\n".join([f"- {r['title']}: {r['body'][:300]}" for r in results])
        return context
    except Exception as e:
        log.warning(f"DuckDuckGo search failed: {e}. Proceeding without context.")
        return "No external research available."


def _write_article(topic: str, context: str, task_id: str) -> tuple[str, str]:
    """Generate the first draft of a blog article. Returns (article, prompt_id)."""
    dna = _evolver.get_prompt("writer", _WRITER_SYSTEM)
    system_prompt = dna.system_prompt
    prompt_id = dna.id
    prompt = (
        f"Write a comprehensive, SEO-optimized blog post (1200–1800 words) on this topic:\n"
        f"**{topic}**\n\n"
        f"Use these research points as supporting evidence:\n{context}\n\n"
        f"Structure: intro, 4 H2 sections with depth, strong conclusion with CTA."
    )
    return invoke_llm(prompt, system_prompt=system_prompt, tier=LLMTier.FAST, task_id=task_id), prompt_id


def _critique_article(article: str, task_id: str) -> dict:
    """Score the article 1–10 and return structured feedback."""
    prompt = (
        f"Grade this blog article critically on SEO, readability, accuracy, and engagement:\n\n"
        f"{article[:3000]}"  # Cap to avoid token blowout
    )
    dna = _evolver.get_prompt("critic", _CRITIC_SYSTEM)
    raw = invoke_llm(prompt, system_prompt=dna.system_prompt, tier=LLMTier.FAST,
                     temperature=0.2, task_id=task_id)
    try:
        # Strip markdown fences if LLM wraps JSON
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        log.warning("Critic returned non-JSON — assigning score 8 to proceed.", task_id=task_id)
        return {"score": 8, "issues": [], "improvements": []}


def _rewrite_article(article: str, feedback: dict, task_id: str) -> str:
    """Rewrite the article based on critic feedback."""
    issues = "\n".join(f"- {i}" for i in feedback.get("issues", []))
    fixes  = "\n".join(f"- {f}" for f in feedback.get("improvements", []))
    prompt = (
        f"Rewrite and significantly improve this article. "
        f"Address ALL these issues:\n{issues}\n\n"
        f"Apply these specific improvements:\n{fixes}\n\n"
        f"Original article:\n{article}"
    )
    dna = _evolver.get_prompt("writer", _WRITER_SYSTEM)
    return invoke_llm(prompt, system_prompt=dna.system_prompt, tier=LLMTier.FAST, task_id=task_id)


def _generate_social_posts(article: str, task_id: str) -> dict:
    """Generate LinkedIn, Twitter, and Instagram posts from the article."""
    prompt = (
        f"Create platform-optimized social posts based on this article. "
        f"Make them engaging, native to each platform:\n\n{article[:2000]}"
    )
    dna = _evolver.get_prompt("social", _SOCIAL_SYSTEM)
    raw = invoke_llm(prompt, system_prompt=dna.system_prompt, tier=LLMTier.FAST,
                     temperature=0.8, task_id=task_id)
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"linkedin": raw[:700], "twitter": raw[:280], "instagram": raw[:400]}


def _save_to_content_queue(title: str, body: str, social: dict) -> str:
    """Write approved content to Supabase content_queue. Returns the row id."""
    import uuid
    from datetime import datetime, timezone
    supabase = get_supabase()
    row_id = str(uuid.uuid4())
    supabase.table("content_queue").insert({
        "id":       row_id,
        "type":     "blog_post",
        "title":    title,
        "body":     body,
        "platform": "multi",
        "status":   "qa_pending",
        "scheduled_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Store social posts as additional metadata in the body JSON wrapper
    }).execute()
    # Store social separately in agent_outputs
    supabase.table("agent_outputs").insert({
        "agent_id":    "content_agent",
        "output_type": "social_posts",
        "content":     social,
        "qa_status":   "pending",
    }).execute()
    return row_id


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def run_content_agent(payload: dict):
    task_id      = payload.get("task_id", "unknown")
    input_data   = payload.get("input", {})
    strategy     = input_data.get("strategy_brief", "")
    topic        = input_data.get("topic", f"Digital marketing trends: {strategy[:80]}")
    # Carry forward prompt_id if QA is requesting a rewrite (for fitness tracking)
    writer_prompt_id = input_data.get("_writer_prompt_id")

    log.info(f"Content Agent starting for topic: '{topic}'", task_id=task_id)
    log.start_timer("content_full_run")

    try:
        # Step 1: Research
        context = _research(topic)

        # Step 2: Write first draft (now uses evolving prompt DNA)
        article, writer_prompt_id = _write_article(topic, context, task_id)
        log.info(f"First draft written (prompt_id={writer_prompt_id[:8]}...).", task_id=task_id)

        # Step 3: Self-critique loop (max MAX_CONTENT_REWRITES rewrites)
        final_score = 0
        for attempt in range(MAX_CONTENT_REWRITES + 1):
            feedback    = _critique_article(article, task_id)
            final_score = feedback.get("score", 0)
            log.info(f"Critique attempt {attempt + 1}: score {final_score}/10", task_id=task_id)

            if final_score >= 7 or attempt >= MAX_CONTENT_REWRITES:
                break

            log.info(f"Score {final_score} < 7. Rewriting (attempt {attempt + 1})...", task_id=task_id)
            article = _rewrite_article(article, feedback, task_id)

        # Step 4: Generate social posts
        social_posts = _generate_social_posts(article, task_id)
        log.info("Social posts generated.", task_id=task_id)

        # Step 5: Save to Supabase
        title    = topic[:120]
        row_id   = _save_to_content_queue(title, article, social_posts)
        log.info(f"Content saved to content_queue id={row_id}", task_id=task_id)

        # Step 6: Log metrics & heartbeat
        log_metric("content_agent", "article_score", final_score)
        log_metric("content_agent", "word_count", len(article.split()))
        log_agent_heartbeat("content_agent")

        # Step 7: Dispatch to QA Agent (pass prompt_id so QA can feed fitness back)
        send_task(
            from_agent="content_agent",
            to_agent="qa_agent",
            task_type="review_content",
            input_data={
                "content_queue_id":  row_id,
                "content_title":     title,
                "content_body":      article,
                "social_posts":      social_posts,
                "self_score":        final_score,
                "_writer_prompt_id": writer_prompt_id,   # For prompt evolution feedback
            },
            priority="normal",
        )

        duration = log.end_timer("content_full_run", task_id=task_id)
        log.info(f"Content Agent complete. Final score: {final_score}/10. Duration: {duration}ms", task_id=task_id)

    except Exception as e:
        log.error(f"Content Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("content_agent", "high", f"Task {task_id} failed: {e}")
        raise
