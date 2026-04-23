"""
core/ws_broadcaster.py — WebSocket Event Broadcaster for NovaMind

Manages all active WebSocket connections and broadcasts real-time events
to the neural map dashboard. Thread-safe singleton.

Events emitted:
  - task_queued      : new task entered the system
  - task_started     : agent picked up the task
  - task_completed   : task finished successfully
  - task_failed      : task hit dead_letter
  - agent_heartbeat  : agent reported alive
  - debate_round     : agents are debating
  - alert_fired      : new alert written
  - metric_logged    : agent emitted a metric
"""
import json
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from core.logger import AgentLogger

log = AgentLogger("ws_broadcaster")

# ─── Connection Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    """Thread-safe WebSocket connection manager."""

    def __init__(self):
        self._connections: set = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        log.info(f"WebSocket client connected. Total: {len(self._connections)}")

    async def disconnect(self, websocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        log.info(f"WebSocket client disconnected. Total: {len(self._connections)}")

    async def broadcast(self, event: dict) -> None:
        """Broadcast an event to all connected dashboard clients."""
        if not self._connections:
            return  # No clients, skip silently

        message = json.dumps({
            **event,
            "_ts": datetime.now(timezone.utc).isoformat(),
        })

        dead_connections = set()
        async with self._lock:
            connections_snapshot = set(self._connections)

        for ws in connections_snapshot:
            try:
                await ws.send_text(message)
                log.info(f"Broadcasted {event.get('type')} to client.")
            except Exception:
                dead_connections.add(ws)

        if dead_connections:
            async with self._lock:
                self._connections -= dead_connections

    @property
    def client_count(self) -> int:
        return len(self._connections)


# ─── Global Singleton ──────────────────────────────────────────────────────────

_manager = ConnectionManager()


def get_manager() -> ConnectionManager:
    """Return the global WebSocket connection manager."""
    return _manager


# ─── Sync Broadcast Helpers ────────────────────────────────────────────────────
# These allow synchronous agent code (running in thread pool) to emit events
# by scheduling coroutines on the running event loop.

_main_loop: Optional[asyncio.AbstractEventLoop] = None

def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Set the main event loop to use for broadcasting from other threads."""
    global _main_loop
    _main_loop = loop

def _emit(event_type: str, data: dict[str, Any]) -> None:
    """
    Fire-and-forget broadcast from synchronous code.
    Safe to call from any thread.
    """
    event = {"type": event_type, **data}
    try:
        # Try to get the loop from our global storage first (set during startup)
        target_loop = _main_loop
        
        # Fallback to current running loop if available
        if not target_loop:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        
        if target_loop:
            target_loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_manager.broadcast(event))
            )
        else:
            # Last ditch effort for standalone scripts (not recommended for production)
            log.warning(f"WS broadcast failed: No event loop found for {event_type}")
    except Exception as e:
        log.warning(f"WS broadcast failed (non-fatal): {e}")


def emit_task_queued(task_id: str, from_agent: str, to_agent: str,
                     task_type: str, priority: str = "normal") -> None:
    _emit("task_queued", {
        "task_id": task_id, "from": from_agent, "to": to_agent,
        "task_type": task_type, "priority": priority,
    })


def emit_task_started(task_id: str, agent: str) -> None:
    _emit("task_started", {"task_id": task_id, "agent": agent})


def emit_task_completed(task_id: str, agent: str, duration_ms: int = 0) -> None:
    _emit("task_completed", {"task_id": task_id, "agent": agent, "duration_ms": duration_ms})


def emit_task_failed(task_id: str, agent: str, error: str) -> None:
    _emit("task_failed", {"task_id": task_id, "agent": agent, "error": error[:200]})


def emit_agent_heartbeat(agent_id: str) -> None:
    _emit("agent_heartbeat", {"agent": agent_id})


def emit_alert(agent_id: str, severity: str, message: str) -> None:
    _emit("alert_fired", {
        "agent": agent_id, "severity": severity, "message": message[:300],
    })


def emit_metric(agent_id: str, metric_name: str, value: float) -> None:
    _emit("metric_logged", {
        "agent": agent_id, "metric": metric_name, "value": value,
    })


def emit_debate_round(debate_id: str, topic: str, round_num: int,
                      participants: list[str]) -> None:
    _emit("debate_round", {
        "debate_id": debate_id, "topic": topic[:100],
        "round": round_num, "participants": participants,
    })


def emit_evolution_cycle(agent_id: str, prompt_name: str,
                          generation: int, new_variants: int) -> None:
    _emit("evolution_cycle", {
        "agent": agent_id, "prompt": prompt_name,
        "generation": generation, "new_variants": new_variants,
    })
