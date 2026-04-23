"""
agents/proposal_agent/agent.py — NovaMind Proposal Agent
Autonomous Client Onboarding:
  1. Receive lead_data (name, website, goals)
  2. Research lead's current online presence (DuckDuckGo)
  3. Generate a "God-Tier" growth roadmap and agency proposal
  4. Dispatch to QA for approval
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

log = AgentLogger("proposal_agent")
_evolver = get_evolver("proposal_agent")

_PROPOSAL_SYSTEM_DEFAULT = (
    "You are the Head of Growth at NovaMind. You create high-ticket agency proposals. "
    "Your tone is elite, visionary, and data-driven. "
    "Focus on exponential growth and AI-driven automation. "
    "Return ONLY valid JSON."
)


def _research_lead(company_name: str, url: str) -> str:
    """Gather intel on the prospect's company."""
    try:
        results = DDGS().text(f"{company_name} {url} reviews competitors", max_results=5)
        text = "\n".join([f"{r['title']}: {r['body']}" for r in results])
        return text[:4000]
    except Exception as e:
        log.warning(f"Lead research failed: {e}")
        return "No specific online intel found."


def _generate_proposal(lead_data: dict, research: str, task_id: str) -> tuple[dict, str]:
    """Craft the personalized proposal using evolved DNA."""
    dna = _evolver.get_prompt("proposal_closer", _PROPOSAL_SYSTEM_DEFAULT)
    system_prompt = dna.system_prompt
    prompt_id = dna.id
    
    user_prompt = (
        f"Lead Details: {json.dumps(lead_data, indent=2)}\n\n"
        f"Research Intel:\n{research}\n\n"
        f"Create a high-impact digital agency proposal. Include:\n"
        f"1. 'analysis': assessment of their current gaps\n"
        f"2. 'roadmap': 3-month AI growth strategy\n"
        f"3. 'pitch': 1-paragraph visionary closing statement\n"
        f"4. 'services': list of 4 specific NovaMind services they need\n"
        f"5. 'estimated_impact': expected ROI/growth metrics\n\n"
        f"Return JSON only."
    )

    raw = invoke_llm(user_prompt, system_prompt=system_prompt, tier=LLMTier.DEEP,
                     temperature=0.4, task_id=task_id)
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean), prompt_id
    except Exception:
        return {"analysis": raw[:500], "roadmap": [], "pitch": raw}, prompt_id


def run_proposal_agent(payload: dict):
    task_id    = payload.get("task_id", "unknown")
    input_data = payload.get("input", {})
    lead_name  = input_data.get("company_name", "Prospect")
    lead_url   = input_data.get("website", "")

    log.info(f"Proposal Agent drafting roadmap for '{lead_name}'", task_id=task_id)
    log.start_timer("proposal_drafting")

    try:
        # Step 1: Research
        intel = _research_lead(lead_name, lead_url)
        log.info("Lead research complete.", task_id=task_id)

        # Step 2: Generate
        proposal, prompt_id = _generate_proposal(input_data, intel, task_id)
        log.info(f"Proposal generated. DNA: {prompt_id[:8]}...", task_id=task_id)

        # Step 3: Save Output
        supabase  = get_supabase()
        output_id = str(uuid.uuid4())
        supabase.table("agent_outputs").insert({
            "id":          output_id,
            "agent_id":    "proposal_agent",
            "output_type": "proposal",
            "content":     {**proposal, "lead": lead_name, "_prompt_id": prompt_id},
            "qa_status":   "pending",
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()

        log_metric("proposal_agent", "proposal_created", 1)
        log_agent_heartbeat("proposal_agent")

        # Step 4: Send to QA
        send_task(
            from_agent="proposal_agent",
            to_agent="qa_agent",
            task_type="review_content",
            input_data={
                "content_title":    f"Proposal: {lead_name}",
                "content_body":     json.dumps(proposal, indent=2),
                "content_type":     "proposal",
                "output_id":        output_id,
                "reject_count":     0,
                "_agent_id":        "proposal_agent",
                "_agent_prompt_id": prompt_id
            },
        )

        log.end_timer("proposal_drafting", task_id=task_id)

    except Exception as e:
        log.error(f"Proposal Agent failed: {e}", task_id=task_id, exc_info=True)
        write_alert("proposal_agent", "high", f"Proposal failed for {lead_name}: {e}")
        raise
