"""
core/llm_pool.py — Intelligent LLM router for NovaMind.

Routing logic:
  - Short/fast tasks (classification, scoring, social posts) → Groq Llama 3.3 70B
  - Long-form / QA / large context tasks → Gemini 2.5 Flash
  - Auto-retry with exponential backoff on 429 / rate-limit errors
  - Groq ↔ Gemini automatic failover if a service is down
"""
import asyncio
import time
from enum import Enum
from typing import Optional

from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import (
    GROQ_API_KEY, GEMINI_API_KEY,
    GROQ_MODEL, GEMINI_MODEL,
    GROQ_RPM_LIMIT,
)
from core.logger import AgentLogger

log = AgentLogger("llm_pool")

# ─── Task Complexity Tiers ─────────────────────────────────────────────────────
class LLMTier(str, Enum):
    FAST    = "fast"     # Groq: short tasks, scoring, classification
    DEEP    = "deep"     # Gemini: long-form writing, QA review, analysis


# ─── Rate-limit semaphore for Groq (30 RPM = 0.5 req/s) ──────────────────────
# We allow 25 concurrent slots to leave headroom for retries.
_groq_semaphore = asyncio.Semaphore(25)
_groq_last_call: list[float] = []          # rolling window of call timestamps
_GROQ_WINDOW_SECONDS = 60


def _get_groq_llm(temperature: float = 0.7) -> ChatGroq:
    return ChatGroq(
        temperature=temperature,
        model_name=GROQ_MODEL,
        groq_api_key=GROQ_API_KEY,
    )


def _get_gemini_llm(temperature: float = 0.7) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        temperature=temperature,
        google_api_key=GEMINI_API_KEY,
    )


# ─── Core Invoke Function ─────────────────────────────────────────────────────

def invoke_llm(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    tier: LLMTier = LLMTier.FAST,
    temperature: float = 0.7,
    max_retries: int = 3,
    task_id: Optional[str] = None,
) -> str:
    """
    Synchronous LLM call with automatic retry and failover.

    Args:
        prompt: The user-facing prompt.
        system_prompt: Optional system context.
        tier: FAST (Groq) or DEEP (Gemini).
        temperature: 0.0–1.0 creativity dial.
        max_retries: Number of attempts before giving up.
        task_id: For structured logging.

    Returns:
        The LLM's response content as a string.

    Raises:
        RuntimeError if all retry attempts and failover attempts fail.
    """
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))

    # Determine primary and fallback LLMs
    if tier == LLMTier.FAST:
        primary_llm   = _get_groq_llm(temperature)
        fallback_llm  = _get_gemini_llm(temperature)
        primary_name  = "groq"
        fallback_name = "gemini"
    else:
        primary_llm   = _get_gemini_llm(temperature)
        fallback_llm  = _get_groq_llm(temperature)
        primary_name  = "gemini"
        fallback_name = "groq"

    for attempt in range(1, max_retries + 1):
        try:
            log.info(
                f"LLM call [{primary_name}] attempt {attempt}/{max_retries}",
                task_id=task_id,
            )
            log.start_timer(f"llm_{task_id or 'unknown'}")
            response = primary_llm.invoke(messages)
            log.end_timer(f"llm_{task_id or 'unknown'}", task_id=task_id)
            return response.content

        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = "429" in err_str or "rate limit" in err_str or "quota" in err_str

            if is_rate_limit:
                wait = 2 ** attempt   # Exponential backoff: 2s, 4s, 8s
                log.warning(
                    f"Rate limit hit on {primary_name}. Waiting {wait}s (attempt {attempt})",
                    task_id=task_id,
                )
                time.sleep(wait)
            else:
                # Non-rate-limit error — try fallback immediately
                log.warning(
                    f"{primary_name} failed ({e}). Trying {fallback_name} fallback.",
                    task_id=task_id,
                )
                try:
                    response = fallback_llm.invoke(messages)
                    return response.content
                except Exception as fallback_e:
                    log.error(
                        f"Fallback {fallback_name} also failed: {fallback_e}",
                        task_id=task_id,
                    )
                    raise RuntimeError(
                        f"Both {primary_name} and {fallback_name} failed. Last error: {fallback_e}"
                    ) from fallback_e

    raise RuntimeError(
        f"LLM [{primary_name}] exhausted all {max_retries} retries."
    )
