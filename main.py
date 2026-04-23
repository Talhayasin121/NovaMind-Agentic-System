"""
main.py — NovaMind Agents API
Registry-based router with priority handling, dead letter queue, and real-time WebSocket.
"""
import importlib
from fastapi import FastAPI, BackgroundTasks, Security, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
import os
import time
from dotenv import load_dotenv

load_dotenv()

from core.config import AGENT_API_KEY as _API_KEY, AGENT_REGISTRY
from core.message_bus import update_task_status, write_alert, send_discord_notify
from core.ws_broadcaster import get_manager, set_main_loop, emit_task_queued, emit_task_started, emit_task_completed, emit_task_failed
import asyncio
from core.task_poller import _poll_loop

from core.logger import AgentLogger
log = AgentLogger("api")

app = FastAPI(
    title="NovaMind Agents API",
    description="Autonomous AI Agency — 13-Agent Orchestration Layer",
    version="3.0.0",
)

@app.on_event("startup")
async def startup_event():
    # 1. Capture the main event loop for WS broadcasting from threads
    loop = asyncio.get_running_loop()
    set_main_loop(loop)
    log.info("Main event loop captured for WebSocket broadcaster.")

    # 2. Start the autonomous Task Poller as a background task
    # This keeps everything in one process for seamless WebSocket integration
    asyncio.create_task(_poll_loop())
    log.info("Autonomous Task Poller started in background.")

# ─── CORS (allows dashboard_v2.html running on file:// or any port) ──────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Security ─────────────────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-Agent-Secret-Key", auto_error=True)

def get_api_key(api_key: str = Security(api_key_header)):
    if api_key != _API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return api_key


# ─── Payload Schema ───────────────────────────────────────────────────────────
class TaskPayload(BaseModel):
    task_id:    str
    from_agent: str
    to_agent:   str
    task_type:  str
    priority:   str = "normal"
    input:      Dict[str, Any]
    deadline:   Optional[str] = None
    status:     str = "pending"


# ─── Registry-Based Router ─────────────────────────────────────────────────────

def _load_agent_fn(agent_name: str):
    """
    Dynamically import and return the run function for a given agent name.
    Format in AGENT_REGISTRY: "module.path:function_name"
    """
    registry_entry = AGENT_REGISTRY.get(agent_name)
    if not registry_entry:
        return None
    module_path, fn_name = registry_entry.split(":")
    module = importlib.import_module(module_path)
    return getattr(module, fn_name)


def agent_router(payload: TaskPayload):
    """
    Dispatches a task to the correct agent function.
    Handles:
      - Unknown agents → dead letter
      - Agent exceptions → dead letter + Discord alert
      - Real-time WebSocket events emitted at each state change
    """
    task_id    = payload.task_id
    agent_name = payload.to_agent
    start_ms   = int(time.time() * 1000)

    log.info(f"Routing task to '{agent_name}' [priority={payload.priority}]", task_id=task_id)
    emit_task_started(task_id, agent_name)

    agent_fn = _load_agent_fn(agent_name)

    if agent_fn is None:
        log.warning(f"Unknown agent '{agent_name}'. Sending to dead letter.", task_id=task_id)
        update_task_status(task_id, "dead_letter", error=f"No handler for agent '{agent_name}'")
        emit_task_failed(task_id, agent_name, f"No handler for agent '{agent_name}'")
        write_alert(
            agent_id="api",
            severity="warning",
            message=f"Dead letter: no handler for agent '{agent_name}' (task {task_id})",
        )
        return

    try:
        update_task_status(task_id, "in_progress")
        agent_fn(payload.model_dump())
        update_task_status(task_id, "done")
        duration_ms = int(time.time() * 1000) - start_ms
        emit_task_completed(task_id, agent_name, duration_ms)
        log.info(f"Task complete for '{agent_name}'", task_id=task_id)

    except Exception as e:
        log.error(f"Agent '{agent_name}' crashed: {e}", task_id=task_id, exc_info=True)
        update_task_status(task_id, "dead_letter", error=str(e))
        emit_task_failed(task_id, agent_name, str(e))
        write_alert(
            agent_id=agent_name,
            severity="high",
            message=f"Agent crashed on task {task_id}: {e}",
        )
        send_discord_notify(
            title=f"🚨 Agent Failure: {agent_name}",
            message=f"Task `{task_id}` failed:\n```{e}```",
            severity="high",
        )


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@app.post("/run", status_code=202)
async def run_agent(
    payload:          TaskPayload,
    background_tasks: BackgroundTasks,
    api_key:          str = Depends(get_api_key),
):
    """
    Primary webhook — accepts a task, records it in DB, and queues for background execution.
    """
    from core.supabase_client import get_supabase
    from datetime import datetime, timezone

    supabase = get_supabase()
    db_payload = {
        "id":         payload.task_id,
        "from_agent": payload.from_agent,
        "to_agent":   payload.to_agent,
        "type":       payload.task_type,
        "priority":   payload.priority,
        "payload":    payload.input,
        "status":     "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        supabase.table("tasks").insert(db_payload).execute()
        log.info(f"API Task recorded: {payload.task_id} → {payload.to_agent}")
    except Exception as e:
        log.warning(f"Failed to record API task in DB (might already exist): {e}")

    # Broadcast to dashboard
    emit_task_queued(
        task_id   = payload.task_id,
        from_agent= payload.from_agent,
        to_agent  = payload.to_agent,
        task_type = payload.task_type,
        priority  = payload.priority,
    )

    background_tasks.add_task(agent_router, payload)

    return {
        "status":   "accepted",
        "task_id":  payload.task_id,
        "message":  f"Task queued for '{payload.to_agent}'",
    }


@app.get("/health")
def health_check():
    """Liveness probe for Docker / load balancers."""
    from core.ws_broadcaster import get_manager
    return {"status": "ok", "version": "3.0.0", "ws_clients": get_manager().client_count}


@app.get("/agents")
def list_agents(api_key: str = Depends(get_api_key)):
    """Returns the list of all registered agents."""
    return {"agents": list(AGENT_REGISTRY.keys())}


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """
    Real-time WebSocket endpoint for the neural map dashboard.
    Clients connect here to receive live task, agent, alert, and evolution events.
    No auth required (read-only, no sensitive data in events).
    """
    manager = get_manager()
    await manager.connect(websocket)
    try:
        # Keep the connection alive; clients can send pings
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)


@app.get("/test_e2e")
async def trigger_test_e2e(
    background_tasks: BackgroundTasks,
    api_key:          str = Depends(get_api_key),
):
    """
    Convenience endpoint to trigger a full agency test run.
    """
    import uuid
    test_id = str(uuid.uuid4())
    payload = TaskPayload(
        task_id=test_id,
        from_agent="api_test",
        to_agent="ceo_agent",
        task_type="daily_strategy",
        input={"brief": "System Check: Full Pipeline Test", "is_test": True}
    )
    background_tasks.add_task(agent_router, payload)
    return {"status": "triggered", "test_id": test_id}
