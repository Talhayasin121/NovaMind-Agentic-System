"""
core/memory.py — Agent Memory System for NovaMind

Gives each agent persistent memory stored in Supabase agent_memory table.
No vector DB needed — pattern matching + Groq summarization.

Usage:
    from core.memory import AgentMemory
    mem = AgentMemory("content_agent")
    mem.remember("article_about_seo scored 9/10 with topic cluster approach")
    context = mem.recall(limit=5)   # Returns last 5 memories as formatted string
    mem.learn_from_outcome(task_type="write_blog", outcome_score=9.0, notes="...")
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.supabase_client import get_supabase
from core.logger import AgentLogger

log = AgentLogger("memory")


class AgentMemory:
    """
    Persistent per-agent memory backed by Supabase.

    Memory types:
      - 'learning'  : general takeaways ("topic clusters increase QA scores")
      - 'decision'  : specific choices made and their outcomes
      - 'pattern'   : recurring observations across many tasks
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._supabase = get_supabase()

    def remember(self, content: str, memory_type: str = "learning") -> None:
        """Store a new memory entry."""
        self._supabase.table("agent_memory").insert({
            "id":          str(uuid.uuid4()),
            "agent_id":    self.agent_id,
            "memory_type": memory_type,
            "content":     {"text": content},
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        log.debug(f"Memory stored for {self.agent_id}: {content[:80]}...")

    def recall(self, limit: int = 5, memory_type: Optional[str] = None) -> str:
        """
        Fetch recent memories and return as a formatted context string.
        Ready to inject directly into LLM prompts.
        """
        query = (
            self._supabase.table("agent_memory")
            .select("memory_type, content, created_at")
            .eq("agent_id", self.agent_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if memory_type:
            query = query.eq("memory_type", memory_type)

        rows = query.execute().data or []
        if not rows:
            return ""

        lines = []
        for r in reversed(rows):   # Chronological order for LLM
            text = r["content"].get("text", "") if isinstance(r["content"], dict) else str(r["content"])
            lines.append(f"[{r['memory_type'].upper()}] {text}")

        return "\n".join(lines)

    def learn_from_outcome(
        self,
        task_type: str,
        outcome_score: float,
        notes: str = "",
    ) -> None:
        """
        Store a structured learning from a completed task outcome.
        Used to improve future performance.
        """
        self.remember(
            content=f"Task '{task_type}' scored {outcome_score}/10. Notes: {notes}",
            memory_type="decision",
        )

    def get_best_practices(self, limit: int = 3) -> str:
        """
        Return the highest-scoring patterns from memory.
        Call this at the START of a task to prime the agent.
        """
        # Fetch all decision memories
        rows = (
            self._supabase.table("agent_memory")
            .select("content")
            .eq("agent_id", self.agent_id)
            .eq("memory_type", "decision")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
            .data or []
        )

        # Parse and sort by score
        scored = []
        for r in rows:
            content = r["content"]
            text    = content.get("text", "") if isinstance(content, dict) else str(content)
            try:
                score_part = text.split("scored")[1].split("/10")[0].strip()
                score      = float(score_part)
                scored.append((score, text))
            except Exception:
                continue

        scored.sort(reverse=True)
        top = scored[:limit]

        if not top:
            return ""

        return "Best practices from past experience:\n" + "\n".join(
            f"- {text}" for _, text in top
        )
