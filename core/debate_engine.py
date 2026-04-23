"""
core/debate_engine.py — Multi-Agent Debate Protocol

The boardroom simulation: agents argue, challenge, and reach consensus.

How it works:
  1. A "moderator" (usually CEO Agent) opens a debate on a topic
  2. Participant agents each submit a position/argument
  3. Up to MAX_ROUNDS of rebuttals happen
  4. Moderator synthesizes the debate into a final decision
  5. Full transcript is saved to Supabase

This is genuinely novel — no other open-source agentic system has
inter-agent debate with consensus synthesis.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core.supabase_client import get_supabase
from core.logger import AgentLogger

log = AgentLogger("debate_engine")


# ─── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class DebatePosition:
    agent_id:  str
    argument:  str
    round_num: int
    submitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class DebateRound:
    id:           str
    topic:        str
    context:      str
    moderator:    str
    participants: list[str]
    positions:    list[DebatePosition]  = field(default_factory=list)
    consensus:    Optional[str]         = None
    round_num:    int                   = 0
    max_rounds:   int                   = 3
    status:       str                   = "open"    # open | deliberating | resolved
    created_at:   str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at:  Optional[str]         = None


# ─── Debate Engine ─────────────────────────────────────────────────────────────

class DebateEngine:
    """
    Orchestrates multi-agent debates and stores full transcripts.

    Usage:
        engine = DebateEngine()
        debate = engine.open_debate(
            topic="Should we target SMB or enterprise clients this quarter?",
            context=market_data,
            moderator="ceo_agent",
            participants=["content_agent", "seo_agent", "ads_agent"],
        )
        # Each participant submits their position (called from their agent logic)
        engine.submit_position(debate.id, "content_agent", "We should target SMB because...")
        engine.submit_position(debate.id, "seo_agent", "Enterprise has higher keyword value...")
        engine.submit_position(debate.id, "ads_agent", "SMB has lower CAC in our niche...")

        # Moderator synthesizes
        decision = engine.synthesize(debate.id)
    """

    def __init__(self):
        self._supabase  = get_supabase()
        self._debates: dict[str, DebateRound] = {}   # in-memory cache

    # ── Public API ──────────────────────────────────────────────────────────────

    def open_debate(
        self,
        topic:        str,
        context:      str,
        moderator:    str,
        participants: list[str],
        max_rounds:   int = 3,
    ) -> DebateRound:
        """Open a new debate session and persist it to Supabase."""
        debate = DebateRound(
            id           = str(uuid.uuid4()),
            topic        = topic,
            context      = context[:4000],   # Trim context for DB
            moderator    = moderator,
            participants = participants,
            max_rounds   = max_rounds,
        )

        self._supabase.table("debates").insert({
            "id":           debate.id,
            "topic":        debate.topic,
            "context":      debate.context,
            "moderator":    debate.moderator,
            "participants": debate.participants,
            "max_rounds":   debate.max_rounds,
            "status":       debate.status,
            "created_at":   debate.created_at,
        }).execute()

        self._debates[debate.id] = debate

        # Emit WebSocket event
        try:
            from core.ws_broadcaster import emit_debate_round
            emit_debate_round(debate.id, topic, 0, participants)
        except Exception:
            pass

        log.info(
            f"Debate opened: '{topic[:60]}...' | "
            f"Moderator: {moderator} | Participants: {participants}"
        )
        return debate

    def submit_position(
        self,
        debate_id: str,
        agent_id:  str,
        argument:  str,
    ) -> None:
        """An agent submits its position/argument to the debate."""
        debate = self._get_debate(debate_id)
        if not debate:
            log.warning(f"[submit_position] Debate '{debate_id}' not found.")
            return

        if agent_id not in debate.participants and agent_id != debate.moderator:
            log.warning(f"Agent '{agent_id}' is not a participant in debate '{debate_id}'.")
            return

        position = DebatePosition(
            agent_id  = agent_id,
            argument  = argument,
            round_num = debate.round_num,
        )
        debate.positions.append(position)

        # Persist the position
        self._supabase.table("debate_positions").insert({
            "id":         str(uuid.uuid4()),
            "debate_id":  debate_id,
            "agent_id":   agent_id,
            "argument":   argument,
            "round_num":  debate.round_num,
            "created_at": position.submitted_at,
        }).execute()

        log.info(
            f"[{agent_id}] Position submitted to debate '{debate_id[:8]}...' "
            f"(round {debate.round_num}): '{argument[:80]}...'"
        )

        # Auto-synthesize if all participants have submitted for this round
        submitted_this_round = {
            p.agent_id for p in debate.positions if p.round_num == debate.round_num
        }
        if set(debate.participants).issubset(submitted_this_round):
            if debate.round_num < debate.max_rounds - 1:
                debate.round_num += 1
                log.info(f"All positions received. Moving to round {debate.round_num}.")
                try:
                    from core.ws_broadcaster import emit_debate_round
                    emit_debate_round(debate_id, debate.topic, debate.round_num, debate.participants)
                except Exception:
                    pass

    def synthesize(self, debate_id: str) -> str:
        """
        Moderator synthesizes all debate positions into a final decision.
        This is the most important call in the debate lifecycle.
        """
        from core.llm_pool import invoke_llm, LLMTier

        debate = self._get_debate(debate_id)
        if not debate:
            return "Unable to synthesize: debate not found."

        # Build the full debate transcript
        transcript = self._build_transcript(debate)

        synthesis_prompt = f"""You are the CEO/moderator of an autonomous AI agency.
Your team of specialized agents has been debating the following strategic question:

TOPIC: {debate.topic}

CONTEXT:
{debate.context}

DEBATE TRANSCRIPT:
{transcript}

Your task: Synthesize these arguments into a FINAL DECISION.
Structure your response as:
1. KEY POINTS OF AGREEMENT: (what everyone agreed on)
2. KEY POINTS OF DISAGREEMENT: (where agents differed)
3. FINAL DECISION: (clear, actionable decision)
4. RATIONALE: (why this decision, referencing specific agent arguments)
5. ACTION ITEMS: (concrete next steps for each participating agent)

Be decisive. A good decision now beats a perfect decision never."""

        try:
            consensus = invoke_llm(
                synthesis_prompt,
                tier=LLMTier.DEEP,   # Use best model for synthesis
                temperature=0.3,      # Low temperature for consistency
            )
        except Exception as e:
            consensus = f"Synthesis failed: {e}. Defaulting to first position."
            log.error(f"Debate synthesis failed: {e}")

        # Persist consensus
        debate.consensus   = consensus
        debate.status      = "resolved"
        debate.resolved_at = datetime.now(timezone.utc).isoformat()

        self._supabase.table("debates").update({
            "consensus":   consensus,
            "status":      "resolved",
            "resolved_at": debate.resolved_at,
        }).eq("id", debate_id).execute()

        log.info(
            f"Debate '{debate_id[:8]}...' resolved. "
            f"Consensus: '{consensus[:100]}...'"
        )
        return consensus

    def get_debate(self, debate_id: str) -> Optional[DebateRound]:
        """Public getter for a debate by ID."""
        return self._get_debate(debate_id)

    # ── Private Helpers ─────────────────────────────────────────────────────────

    def _get_debate(self, debate_id: str) -> Optional[DebateRound]:
        """Load debate from cache or Supabase."""
        if debate_id in self._debates:
            return self._debates[debate_id]

        row = (
            self._supabase.table("debates")
            .select("*")
            .eq("id", debate_id)
            .limit(1)
            .execute()
        )
        if not row.data:
            return None

        data   = row.data[0]
        debate = DebateRound(
            id           = data["id"],
            topic        = data["topic"],
            context      = data.get("context", ""),
            moderator    = data["moderator"],
            participants = data.get("participants", []),
            max_rounds   = data.get("max_rounds", 3),
            status       = data.get("status", "open"),
            created_at   = data.get("created_at", ""),
            consensus    = data.get("consensus"),
            resolved_at  = data.get("resolved_at"),
        )

        # Load positions from DB
        pos_rows = (
            self._supabase.table("debate_positions")
            .select("*")
            .eq("debate_id", debate_id)
            .order("created_at")
            .execute()
            .data or []
        )
        for p in pos_rows:
            debate.positions.append(DebatePosition(
                agent_id  = p["agent_id"],
                argument  = p["argument"],
                round_num = p.get("round_num", 0),
                submitted_at = p.get("created_at", ""),
            ))

        self._debates[debate_id] = debate
        return debate

    def _build_transcript(self, debate: DebateRound) -> str:
        """Format all debate positions into a readable transcript."""
        if not debate.positions:
            return "(No positions submitted)"

        lines = []
        current_round = -1
        for pos in sorted(debate.positions, key=lambda p: (p.round_num, p.submitted_at)):
            if pos.round_num != current_round:
                current_round = pos.round_num
                lines.append(f"\n--- ROUND {current_round + 1} ---")
            lines.append(f"\n[{pos.agent_id.upper()}]:\n{pos.argument}")

        return "\n".join(lines)


# ─── Global Singleton ──────────────────────────────────────────────────────────

_engine: Optional[DebateEngine] = None

def get_debate_engine() -> DebateEngine:
    global _engine
    if _engine is None:
        _engine = DebateEngine()
    return _engine
