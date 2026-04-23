"""
agents/ceo/agent.py — NovaMind CEO Agent

Upgraded to use:
  - core/llm_pool.py (reads GROQ_MODEL from config — never hardcodes model name)
  - core/logger.py (structured JSON logging)
  - core/memory.py (learns from each run)
  - core/message_bus.py (dispatching + heartbeat)

Workflow:
  1. Research market news via DuckDuckGo (5 results)
  2. Synthesize a strategy brief with Groq Llama 3.3 70B
  3. Write brief to Notion (if NOTION_PARENT_PAGE_ID is set)
  4. Dispatch tasks: Content, Ads, SEO, Sales, Design agents
  5. Log metrics and heartbeat
"""
import os
import json
from datetime import datetime, timezone

from duckduckgo_search import DDGS
from notion_client import Client as NotionClient

from core.llm_pool import invoke_llm, LLMTier
from core.message_bus import send_task, write_alert, log_agent_heartbeat, log_metric
from core.supabase_client import get_supabase
from core.memory import AgentMemory
from core.config import NOTION_API_KEY, NOTION_PARENT_PAGE_ID
from core.logger import AgentLogger
from core.debate_engine import get_debate_engine

log = AgentLogger("ceo_agent")
_debate_engine = get_debate_engine()

_CEO_SYSTEM = (
    "You are the autonomous CEO of NovaMind Digital Agency. "
    "You are visionary, decisive, and data-driven. "
    "Write in a concise, executive style. Use Markdown formatting."
)

_STRATEGY_PROMPT = (
    "Today's digital marketing intelligence:\n{news}\n\n"
    "Past learnings from NovaMind operations:\n{memory}\n\n"
    "Write a daily strategic brief containing:\n"
    "1. **Three Strategic Insights** — what the data says about the market today\n"
    "2. **Three Team Goals** — specific, actionable objectives for this week\n"
    "3. **One Priority Focus** — the single most important thing to execute today\n\n"
    "Keep it punchy. Maximum 400 words."
)


def _research_market(task_id: str) -> str:
    """Fetch latest digital marketing news via DuckDuckGo."""
    log.info("Searching DuckDuckGo for market signals...", task_id=task_id)
    try:
        results = DDGS().text(
            "digital marketing AI agency news 2025 latest trends",
            max_results=5,
        )
        return "\n".join([f"- {r['title']}: {r['body'][:250]}" for r in results])
    except Exception as e:
        log.warning(f"DuckDuckGo failed: {e} — proceeding without market data.", task_id=task_id)
        return "Market data unavailable — base strategy on general digital agency principles."


def _write_to_notion(title: str, content: str) -> None:
    """Publish the strategy brief to Notion."""
    # Try to use either config var or the hardcoded page ID from the original agent
    page_id = NOTION_PARENT_PAGE_ID or os.getenv("NOTION_PARENT_PAGE_ID", "349e5e58-6ec7-8024-9ef0-d3855a1e614b")
    if not NOTION_API_KEY or not page_id:
        log.warning("Notion credentials not set — skipping Notion publish.")
        return

    try:
        notion = NotionClient(auth=NOTION_API_KEY)
        notion.pages.create(
            parent={"page_id": page_id},
            properties={
                "title": [{"text": {"content": title}}]
            },
            children=[{
                "object": "block",
                "paragraph": {
                    "rich_text": [{"text": {"content": content[:2000]}}]
                }
            }]
        )
        log.info(f"Strategy brief published to Notion: '{title}'")
    except Exception as e:
        log.warning(f"Notion publish failed (non-fatal): {e}")

def _run_boardroom_debate(topic: str, context: str, task_id: str) -> str:
    """Moderates a virtual boardroom debate between agents to reach a consensus."""
    log.info(f"Opening boardroom debate: {topic}", task_id=task_id)
    
    participants = ["content_agent", "seo_agent", "ads_agent"]
    debate = _debate_engine.open_debate(
        topic=topic,
        context=context,
        moderator="ceo_agent",
        participants=participants
    )

    # Simulate participant arguments (Internal Personas)
    # In a production distributed system, these would be separate task calls.
    # For the God-Tier Demo, we run them sequentially for immediate synthesis.
    for agent_id in participants:
        persona_prompt = f"You are the {agent_id.replace('_', ' ').upper()} of NovaMind. " \
                         f"Provide a 1-paragraph strategic argument regarding: {topic}. " \
                         f"Context: {context[:1000]}"
        
        argument = invoke_llm(persona_prompt, tier=LLMTier.FAST, temperature=0.6, task_id=task_id)
        _debate_engine.submit_position(debate.id, agent_id, argument)

    # CEO Synthesizes
    log.info("Boardroom debate complete. Synthesizing consensus...", task_id=task_id)
    consensus = _debate_engine.synthesize(debate.id)
    return consensus
def run_ceo_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    task_type  = payload.get("type", "daily_strategy")
    input_data = payload.get("input", {})
    
    log.info(f"CEO Agent starting task: {task_type}", task_id=task_id)
    log.start_timer("ceo_full_run")

    try:
        # ─── Case A: Competitive Intel Response (Triggers Debate) ────────────
        if task_type == "competitive_brief":
            intel_summary = input_data.get("intel_summary", [])
            context = json.dumps(intel_summary, indent=2)
            topic = f"Counter-Strategy against {len(intel_summary)} competitors"
            
            consensus = _run_boardroom_debate(topic, context, task_id)
            
            # Record decision in memory
            mem = AgentMemory("ceo_agent")
            mem.remember(f"Consensus reached on {topic}: {consensus[:200]}...", "decision")
            
            # Dispatch follow-ups based on consensus
            send_task(
                from_agent="ceo_agent", to_agent="content_agent",
                task_type="write_counter_content",
                input_data={"consensus": consensus, "intel": intel_summary},
            )
            return

        # ─── Case B: Daily Strategy Cycle (Normal Routine) ────────────────────
        # Step 1: Research
        market_news = _research_market(task_id)

        # Step 2: Pull agent memory for context
        mem     = AgentMemory("ceo_agent")
        memory  = mem.get_best_practices(limit=3) or "No prior learnings yet."

        # Step 3: Synthesize strategy
        log.info("Synthesizing strategy with Groq...", task_id=task_id)
        prompt = _STRATEGY_PROMPT.format(
            news=market_news,
            memory=memory,
        )
        brief_hint = input_data.get("brief", input_data.get("strategy_brief", ""))
        if brief_hint:
            prompt = f"User brief: {brief_hint}\n\n" + prompt

        strategy = invoke_llm(
            prompt,
            system_prompt=_CEO_SYSTEM,
            tier=LLMTier.FAST,
            temperature=0.7,
            task_id=task_id,
        )
        log.info("Strategy brief synthesized.", task_id=task_id)

        # Step 4: Write to Notion
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        title = f"CEO Brief — {today}"
        if input_data.get("is_test"):
            title = f"[TEST] {title}"
        _write_to_notion(title, strategy)

        # Step 5: Dispatch to all department agents
        log.info("Dispatching team tasks...", task_id=task_id)

        topic = brief_hint or "digital marketing trends for AI-powered agencies 2025"
        is_test = input_data.get("is_test", False)

        send_task(
            from_agent="ceo_agent", to_agent="content_agent",
            task_type="write_strategy_blog",
            input_data={"strategy_brief": strategy, "topic": topic, "is_test": is_test},
        )
        send_task(
            from_agent="ceo_agent", to_agent="seo_agent",
            task_type="gap_analysis",
            input_data={"topic": topic, "is_test": is_test},
        )
        send_task(
            from_agent="ceo_agent", to_agent="design_agent",
            task_type="generate_assets",
            input_data={"content_brief": topic, "content_title": topic[:60], "is_test": is_test},
        )
        send_task(
            from_agent="ceo_agent", to_agent="ads_agent",
            task_type="review_roas",
            input_data={"urgency": "daily_check", "is_test": is_test},
        )
        send_task(
            from_agent="ceo_agent", to_agent="sales_agent",
            task_type="lead_gen",
            input_data={"target": "digital marketing agencies seeking AI automation", "is_test": is_test},
        )

        # Step 6: Save memory so next run learns from this
        mem.remember(
            f"Strategy brief on '{topic}' dispatched to 5 agents on {today}",
            memory_type="decision",
        )

        # Step 7: Log metrics + heartbeat
        log_metric("ceo_agent", "daily_brief_sent", 1)
        log_metric("ceo_agent", "agents_dispatched", 5)
        log_agent_heartbeat("ceo_agent")

        duration = log.end_timer("ceo_full_run", task_id=task_id)
        log.info(
            f"CEO Agent complete. Dispatched 5 agents. Duration: {duration}ms",
            task_id=task_id,
        )

    except Exception as e:
        log.error(f"CEO Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("ceo_agent", "high", f"CEO Agent crashed on task {task_id}: {e}")
        raise
