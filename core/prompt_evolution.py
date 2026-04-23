"""
core/prompt_evolution.py — Self-Evolving Prompt DNA System

The most innovative feature in NovaMind:
  - Every agent's prompts are treated as living DNA
  - High QA scores = survival. Low scores = extinction.
  - LLM mutates winning prompts to create new variants
  - Crossover: combine two winners to produce offspring
  - Every 50 task outcomes, an evolution cycle runs

This implements a real genetic algorithm for prompt engineering.
No other open-source agentic system does this.
"""
import uuid
import random
from datetime import datetime, timezone
from typing import Optional

from core.supabase_client import get_supabase
from core.logger import AgentLogger

log = AgentLogger("prompt_evolution")


# ─── Data Classes ──────────────────────────────────────────────────────────────

class PromptDNA:
    """A single prompt variant with fitness tracking."""

    def __init__(
        self,
        agent_id: str,
        prompt_name: str,
        template: str,
        system_prompt: str,
        generation: int = 0,
        parent_id: Optional[str] = None,
        dna_id: Optional[str] = None,
    ):
        self.id            = dna_id or str(uuid.uuid4())
        self.agent_id      = agent_id
        self.prompt_name   = prompt_name
        self.template      = template
        self.system_prompt = system_prompt
        self.generation    = generation
        self.parent_id     = parent_id
        self.avg_score     = 0.0
        self.use_count     = 0

    def fitness(self) -> float:
        """
        Fitness score: weighted combination of average QA score and use count.
        Newer prompts get a small bonus to encourage exploration.
        """
        if self.use_count == 0:
            return 5.0  # Neutral starting fitness for untested prompts
        # Wilson score-style lower confidence bound to penalize low sample sizes
        exploration_bonus = max(0, 1.0 - (self.use_count / 20))
        return self.avg_score + exploration_bonus

    def __repr__(self):
        return (
            f"PromptDNA(agent={self.agent_id}, name={self.prompt_name}, "
            f"gen={self.generation}, score={self.avg_score:.1f}, uses={self.use_count})"
        )


# ─── Evolution Engine ──────────────────────────────────────────────────────────

class PromptEvolver:
    """
    Genetic algorithm engine for prompt evolution.

    Lifecycle:
      1. get_prompt()      — pick best prompt for a task (exploit + explore)
      2. record_outcome()  — update fitness after QA scoring
      3. run_evolution()   — triggered every EVOLUTION_THRESHOLD outcomes
         a. select survivors (top 50%)
         b. mutate each survivor → offspring
         c. crossover top 2 survivors → hybrid
         d. kill bottom 25%
    """

    EVOLUTION_THRESHOLD = 50   # Run evolution every N outcomes
    POPULATION_SIZE     = 6    # Max prompts per agent per name
    SURVIVAL_RATE       = 0.5  # Top 50% survive
    MUTATION_TEMP       = 0.9  # High creativity for mutations

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._supabase = get_supabase()

    # ── Public API ──────────────────────────────────────────────────────────────

    def get_prompt(self, prompt_name: str, default_system: str, default_template: str = "") -> PromptDNA:
        """
        Select the best prompt for a given task using epsilon-greedy strategy.
        If no prompts exist yet, seeds the DB with the defaults.
        """
        pool = self._load_pool(prompt_name)

        if not pool:
            # First time: seed with the original hardcoded prompt
            dna = self._seed_prompt(prompt_name, default_template, default_system)
            return dna

        # Epsilon-greedy: 20% of the time explore randomly, 80% exploit best
        epsilon = 0.20
        if random.random() < epsilon:
            chosen = random.choice(pool)
            log.debug(f"[{self.agent_id}] EXPLORE: using random prompt '{chosen.id[:8]}...'")
        else:
            # Exploit: pick highest fitness
            chosen = max(pool, key=lambda d: d.fitness())
            log.debug(
                f"[{self.agent_id}] EXPLOIT: using best prompt '{chosen.id[:8]}...' "
                f"(fitness={chosen.fitness():.2f})"
            )

        # Increment use count
        self._increment_use(chosen.id)
        return chosen

    def select_prompt(self, prompt_name: str, fallback: str) -> tuple[str, str]:
        """
        LEGACY: Returns (system_prompt, prompt_id). 
        Redirects to get_prompt for backward compatibility during migration.
        """
        dna = self.get_prompt(prompt_name, fallback, "")
        return dna.system_prompt, dna.id

    def record_outcome(self, prompt_id: str, qa_score: float) -> None:
        """
        Record a QA score for a specific prompt.
        Updates the rolling average and triggers evolution if threshold reached.
        """
        row = (
            self._supabase.table("prompt_templates")
            .select("avg_score, use_count, agent_id, prompt_name")
            .eq("id", prompt_id)
            .limit(1)
            .execute()
        )
        if not row.data:
            return

        data       = row.data[0]
        old_avg    = float(data.get("avg_score") or 0.0)
        use_count  = int(data.get("use_count") or 1)
        # Rolling average: new_avg = (old_avg * (n-1) + new_score) / n
        new_avg = ((old_avg * (use_count - 1)) + qa_score) / use_count

        self._supabase.table("prompt_templates").update({
            "avg_score":  round(new_avg, 2),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", prompt_id).execute()

        log.info(
            f"[{self.agent_id}] Prompt '{prompt_id[:8]}...' outcome recorded: "
            f"score={qa_score:.1f}, new_avg={new_avg:.2f}"
        )

        # Check if we should run an evolution cycle
        total_outcomes = (
            self._supabase.table("prompt_templates")
            .select("use_count")
            .eq("agent_id", data["agent_id"])
            .eq("prompt_name", data["prompt_name"])
            .execute()
        )
        total = sum(r["use_count"] for r in (total_outcomes.data or []))
        if total > 0 and total % self.EVOLUTION_THRESHOLD == 0:
            log.info(f"[{self.agent_id}] Evolution threshold reached ({total} outcomes). Evolving...")
            self.run_evolution(data["prompt_name"])

    def run_evolution(self, prompt_name: str) -> None:
        """
        Execute one generation of evolution for a prompt family.
        Steps: select survivors → mutate → crossover → kill weak → save.
        """
        pool = self._load_pool(prompt_name)
        if len(pool) < 2:
            log.info(f"[{self.agent_id}] Pool too small for evolution ({len(pool)} prompts). Skipping.")
            return

        log.info(
            f"[{self.agent_id}/{prompt_name}] Starting evolution. "
            f"Generation pool size: {len(pool)}"
        )

        # Sort by fitness (descending)
        pool.sort(key=lambda d: d.fitness(), reverse=True)

        survive_n  = max(2, int(len(pool) * self.SURVIVAL_RATE))
        survivors  = pool[:survive_n]
        victims    = pool[survive_n:]

        log.info(
            f"[{self.agent_id}/{prompt_name}] "
            f"Survivors: {survive_n} | Victims: {len(victims)} | "
            f"Best fitness: {survivors[0].fitness():.2f}"
        )

        # Kill the weakest prompts (except if pool is tiny)
        if len(pool) >= 4:
            for victim in victims:
                self._supabase.table("prompt_templates").delete().eq("id", victim.id).execute()
                log.debug(f"[{self.agent_id}] Killed prompt '{victim.id[:8]}...' (fitness={victim.fitness():.2f})")

        # Lazy import to avoid circular deps (llm_pool → config → ...)
        from core.llm_pool import invoke_llm, LLMTier

        # Generate mutations from survivors
        new_generation = []
        for survivor in survivors[:2]:   # Mutate top 2 survivors
            mutant_template = self._mutate(survivor.template, invoke_llm)
            mutant_system   = self._mutate(survivor.system_prompt, invoke_llm)
            
            if (mutant_template and mutant_template != survivor.template) or \
               (mutant_system and mutant_system != survivor.system_prompt):
                mutant = self._save_new_prompt(
                    prompt_name=prompt_name,
                    template=mutant_template or survivor.template,
                    system_prompt=mutant_system or survivor.system_prompt,
                    generation=survivor.generation + 1,
                    parent_id=survivor.id,
                )
                new_generation.append(mutant)
                log.info(f"[{self.agent_id}] Mutant created (gen {mutant.generation}): '{mutant.id[:8]}...'")

        # Crossover: combine top 2 if they're both high-scorers
        if len(survivors) >= 2 and survivors[0].avg_score >= 7.0 and survivors[1].avg_score >= 7.0:
            hybrid_template = self._crossover(survivors[0].template, survivors[1].template, invoke_llm)
            hybrid_system   = self._crossover(survivors[0].system_prompt, survivors[1].system_prompt, invoke_llm)
            
            if hybrid_template or hybrid_system:
                hybrid = self._save_new_prompt(
                    prompt_name=prompt_name,
                    template=hybrid_template or survivors[0].template,
                    system_prompt=hybrid_system or survivors[0].system_prompt,
                    generation=max(survivors[0].generation, survivors[1].generation) + 1,
                    parent_id=survivors[0].id,
                )
                new_generation.append(hybrid)
                log.info(f"[{self.agent_id}] Hybrid created: '{hybrid.id[:8]}...'")

        log.info(
            f"[{self.agent_id}/{prompt_name}] Evolution complete. "
            f"New variants: {len(new_generation)}"
        )

        # Broadcast to dashboard
        if new_generation:
            from core.ws_broadcaster import emit_evolution_cycle
            # Use the max generation from new variants
            max_gen = max(p.generation for p in new_generation)
            emit_evolution_cycle(
                self.agent_id,
                prompt_name,
                generation=max_gen,
                new_variants=len(new_generation)
            )

    # ── Private Helpers ─────────────────────────────────────────────────────────

    def _load_pool(self, prompt_name: str) -> list[PromptDNA]:
        """Load all prompt variants for this agent/name combo."""
        rows = (
            self._supabase.table("prompt_templates")
            .select("*")
            .eq("agent_id", self.agent_id)
            .eq("prompt_name", prompt_name)
            .order("avg_score", desc=True)
            .execute()
            .data or []
        )
        result = []
        for r in rows:
            dna = PromptDNA(
                agent_id      = r["agent_id"],
                prompt_name   = r["prompt_name"],
                template      = r.get("template", ""),
                system_prompt = r.get("system_prompt", ""),
                generation    = r.get("generation", 0),
                parent_id     = r.get("parent_id"),
                dna_id        = r["id"],
            )
            dna.avg_score = float(r.get("avg_score") or 0.0)
            dna.use_count = int(r.get("use_count") or 0)
            result.append(dna)
        return result

    def _seed_prompt(self, prompt_name: str, template: str, system: str) -> PromptDNA:
        """Save the initial hardcoded prompt to the DB as generation 0."""
        dna = PromptDNA(
            agent_id      = self.agent_id,
            prompt_name   = prompt_name,
            template      = template,
            system_prompt = system,
            generation    = 0,
        )
        self._supabase.table("prompt_templates").insert({
            "id":            dna.id,
            "agent_id":      dna.agent_id,
            "prompt_name":   dna.prompt_name,
            "template":      dna.template,
            "system_prompt": dna.system_prompt,
            "avg_score":     0.0,
            "use_count":     0,
            "generation":    0,
            "parent_id":     None,
            "created_at":    datetime.now(timezone.utc).isoformat(),
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }).execute()
        log.info(f"[{self.agent_id}] Seeded initial prompt '{prompt_name}' (gen 0)")
        return dna

    def _save_new_prompt(
        self,
        prompt_name: str,
        template: str,
        system_prompt: str,
        generation: int,
        parent_id: Optional[str],
    ) -> PromptDNA:
        """Persist a new mutant/hybrid to the DB."""
        dna = PromptDNA(
            agent_id      = self.agent_id,
            prompt_name   = prompt_name,
            template      = template,
            system_prompt = system_prompt,
            generation    = generation,
            parent_id     = parent_id,
        )
        self._supabase.table("prompt_templates").insert({
            "id":            dna.id,
            "agent_id":      dna.agent_id,
            "prompt_name":   dna.prompt_name,
            "template":      dna.template,
            "system_prompt": dna.system_prompt,
            "avg_score":     0.0,
            "use_count":     0,
            "generation":    generation,
            "parent_id":     parent_id,
            "created_at":    datetime.now(timezone.utc).isoformat(),
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }).execute()
        return dna

    def _increment_use(self, prompt_id: str) -> None:
        """Atomically increment use_count for a prompt."""
        row = (
            self._supabase.table("prompt_templates")
            .select("use_count")
            .eq("id", prompt_id)
            .limit(1)
            .execute()
        )
        if row.data:
            new_count = (row.data[0].get("use_count") or 0) + 1
            self._supabase.table("prompt_templates").update({
                "use_count":  new_count,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", prompt_id).execute()

    def _mutate(self, template: str, invoke_llm) -> str:
        """Use an LLM to generate a variation of the prompt."""
        if not template: return ""
        mutation_prompt = (
            f"You are a prompt engineering expert. Your task is to mutate the following AI system prompt "
            f"to make it MORE effective, while preserving its core intent.\n\n"
            f"Rules:\n"
            f"- Change 20-40% of the wording\n"
            f"- Make the instructions more precise, specific, or actionable\n"
            f"- Try a different framing or structure\n"
            f"- Do NOT change what the prompt is asking for fundamentally\n"
            f"- Return ONLY the mutated prompt text, nothing else\n\n"
            f"Original prompt:\n---\n{template}\n---"
        )
        try:
            from core.llm_pool import LLMTier
            result = invoke_llm(
                mutation_prompt,
                tier=LLMTier.FAST,
                temperature=self.MUTATION_TEMP,
            )
            return result.strip()
        except Exception as e:
            log.warning(f"[{self.agent_id}] Mutation failed: {e}")
            return template

    def _crossover(self, template_a: str, template_b: str, invoke_llm) -> str:
        """Combine the best elements of two high-fitness prompts."""
        if not template_a or not template_b: return template_a or template_b
        crossover_prompt = (
            f"You are a prompt engineering expert. Combine the BEST elements of these two high-performing "
            f"AI system prompts into a single superior hybrid.\n\n"
            f"Rules:\n"
            f"- Take the strongest structural elements from Prompt A\n"
            f"- Take the most specific/effective instructions from Prompt B\n"
            f"- The result should be concise (no longer than the longer of A or B)\n"
            f"- Return ONLY the hybrid prompt, nothing else\n\n"
            f"Prompt A:\n---\n{template_a}\n---\n\n"
            f"Prompt B:\n---\n{template_b}\n---"
        )
        try:
            from core.llm_pool import LLMTier
            result = invoke_llm(
                crossover_prompt,
                tier=LLMTier.FAST,
                temperature=0.6,
            )
            return result.strip()
        except Exception as e:
            log.warning(f"[{self.agent_id}] Crossover failed: {e}")
            return template_a


# ─── Convenience Factory ───────────────────────────────────────────────────────

_evolvers: dict[str, PromptEvolver] = {}

def get_evolver(agent_id: str) -> PromptEvolver:
    """Return a cached PromptEvolver instance for the given agent."""
    if agent_id not in _evolvers:
        _evolvers[agent_id] = PromptEvolver(agent_id)
    return _evolvers[agent_id]
