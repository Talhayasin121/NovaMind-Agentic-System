"""
core/task_poller.py — Standalone async task poller for NovaMind.

Runs the ENTIRE agency as a single Python process — no n8n required.
Polls Supabase every N seconds, picks up pending tasks, routes to agents.

To run standalone:
    python -m core.task_poller

Or as a background service alongside FastAPI:
    uvicorn main:app --host 0.0.0.0 --port 8000 &
    python -m core.task_poller
"""
import asyncio
import importlib
import signal
import sys
from datetime import datetime, timezone

from core.config import AGENT_REGISTRY
from core.message_bus import (
    get_pending_tasks, update_task_status,
    write_alert, send_discord_notify,
)
from core.logger import AgentLogger

log = AgentLogger("task_poller")

# ─── Configuration ─────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 10      # How often to check for new tasks
MAX_CONCURRENT_TASKS  = 3       # Max tasks running at once (respects Groq 30 RPM)
POLLER_AGENT_NAMES    = list(AGENT_REGISTRY.keys())  # Poll for ALL registered agents

# ─── Graceful Shutdown ─────────────────────────────────────────────────────────
_shutdown_event = asyncio.Event()


def _handle_shutdown(sig, frame):
    log.info(f"Shutdown signal received ({sig}). Finishing in-flight tasks...")
    _shutdown_event.set()


# ─── Agent Loader ──────────────────────────────────────────────────────────────

def _load_agent_fn(agent_name: str):
    """Dynamically load the agent run function from the registry."""
    entry = AGENT_REGISTRY.get(agent_name)
    if not entry:
        return None
    module_path, fn_name = entry.split(":")
    try:
        module = importlib.import_module(module_path)
        return getattr(module, fn_name)
    except (ImportError, AttributeError) as e:
        log.error(f"Could not load agent '{agent_name}': {e}")
        return None


# ─── Task Execution ────────────────────────────────────────────────────────────

async def _execute_task(task: dict) -> None:
    """Run a single task in a thread pool executor (non-blocking)."""
    task_id    = task.get("id", "unknown")
    agent_name = task.get("to_agent", "")
    task_type  = task.get("type", "")

    log.info(f"Executing task {task_id} → {agent_name} [{task_type}]", task_id=task_id)
    from core.ws_broadcaster import emit_task_started, emit_task_completed, emit_task_failed
    emit_task_started(task_id, agent_name)

    agent_fn = _load_agent_fn(agent_name)
    if not agent_fn:
        update_task_status(task_id, "dead_letter", error=f"No handler for '{agent_name}'")
        emit_task_failed(task_id, agent_name, f"No handler for '{agent_name}'")
        log.warning(f"Dead letter: no handler for '{agent_name}'", task_id=task_id)
        return

    # Build a payload dict matching FastAPI TaskPayload format
    payload = {
        "task_id":    task_id,
        "from_agent": task.get("from_agent", "poller"),
        "to_agent":   agent_name,
        "task_type":  task_type,
        "priority":   task.get("priority", "normal"),
        "input":      task.get("payload", {}),
        "status":     "in_progress",
    }

    try:
        # Run synchronous agent in thread pool to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, agent_fn, payload)
        update_task_status(task_id, "done")
        emit_task_completed(task_id, agent_name)
        log.info(f"Task {task_id} completed ✓", task_id=task_id)

    except Exception as e:
        update_task_status(task_id, "dead_letter", error=str(e)[:500])
        emit_task_failed(task_id, agent_name, str(e))
        write_alert(agent_name, "high", f"Task {task_id} failed: {e}")
        log.error(f"Task {task_id} failed: {e}", task_id=task_id, exc_info=True)


# ─── Main Poll Loop ────────────────────────────────────────────────────────────

async def _poll_loop() -> None:
    """Main async loop: poll Supabase, pick up tasks, execute concurrently."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    log.info(
        f"Task Poller started. "
        f"Polling {len(POLLER_AGENT_NAMES)} agents every {POLL_INTERVAL_SECONDS}s. "
        f"Max concurrency: {MAX_CONCURRENT_TASKS}"
    )

    send_discord_notify(
        title="🚀 NovaMind Agency Started",
        message=(
            f"Task Poller is online. Monitoring **{len(POLLER_AGENT_NAMES)}** agents.\n"
            f"Poll interval: {POLL_INTERVAL_SECONDS}s | Max concurrent: {MAX_CONCURRENT_TASKS}"
        ),
        severity="success",
    )

    while not _shutdown_event.is_set():
        try:
            # Collect pending tasks across all agents (prioritize 'critical' naturally)
            all_pending: list[dict] = []
            for agent_name in POLLER_AGENT_NAMES:
                tasks = get_pending_tasks(agent_name, limit=2)
                all_pending.extend(tasks)

            # Sort: critical first, then normal, then by created_at
            priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
            all_pending.sort(
                key=lambda t: (priority_order.get(t.get("priority", "normal"), 2), t.get("created_at", ""))
            )

            if all_pending:
                log.info(f"Found {len(all_pending)} pending tasks. Dispatching...")

                async def _guarded_run(task):
                    async with semaphore:
                        await _execute_task(task)

                await asyncio.gather(*[_guarded_run(t) for t in all_pending])

        except Exception as e:
            log.error(f"Poller loop error: {e}", exc_info=True)
            write_alert("task_poller", "high", f"Poller loop crashed: {e}")

        # Wait for next poll (or exit if shutdown)
        try:
            await asyncio.wait_for(
                _shutdown_event.wait(),
                timeout=POLL_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass   # Normal — just means no shutdown during the wait

    log.info("Task Poller shutting down cleanly.")
    send_discord_notify(
        title="🛑 NovaMind Agency Stopped",
        message="Task Poller has shut down gracefully.",
        severity="warning",
    )


def run() -> None:
    """Entry point — sets up signals and starts the event loop."""
    signal.signal(signal.SIGINT,  _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    asyncio.run(_poll_loop())


if __name__ == "__main__":
    run()
