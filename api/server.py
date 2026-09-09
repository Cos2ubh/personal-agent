"""
FastAPI REST server — external access layer for the personal agent.

Endpoints:
  GET  /health          liveness probe
  POST /chat            send a message, get a response (used by WhatsApp bridge)
  POST /push            push a proactive message to a session (used by notifier.py)
  GET  /sessions        list active session IDs
  DELETE /sessions/{id} clear a session's history

Run with:
    venv\\Scripts\\uvicorn.exe api.server:app --host 0.0.0.0 --port 8502 --reload

The WhatsApp bridge (whatsapp/bridge.js) forwards incoming messages to
POST /chat and sends the response text back to the user.
"""

import sys
from pathlib import Path

# Make sure project root is on sys.path so local modules resolve
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.session import get_or_create, get, clear, all_ids
from api.loop import run_turn

app = FastAPI(
    title="Personal Agent API",
    description="REST interface to the personal AI Chief of Staff",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response models ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    text: str
    needs_approval: bool
    ts: str


class PushRequest(BaseModel):
    session_id: str
    message: str   # pre-composed message (e.g. morning briefing text)


# ── Outbox: messages queued for WhatsApp bridge to pick up ────────────────
# Simple in-process queue — bridge polls GET /outbox?session_id=...

_outbox: dict[str, list[str]] = {}


def _enqueue(session_id: str, text: str) -> None:
    _outbox.setdefault(session_id, []).append(text)


def _dequeue(session_id: str) -> list[str]:
    msgs = _outbox.pop(session_id, [])
    return msgs


# ── Endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    session = get_or_create(req.session_id)
    try:
        result = run_turn(session, req.message.strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")

    return ChatResponse(
        session_id=req.session_id,
        text=result.text,
        needs_approval=result.needs_approval,
        ts=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/push")
def push(req: PushRequest):
    """
    Queue a proactive message for a session. The WhatsApp bridge polls
    GET /outbox?session_id=... to pick it up and send it.
    """
    _enqueue(req.session_id, req.message)
    return {"queued": True, "session_id": req.session_id}


@app.get("/outbox")
def outbox(session_id: str):
    """Return and clear all queued messages for a session."""
    return {"messages": _dequeue(session_id)}


@app.get("/sessions")
def sessions():
    return {"sessions": all_ids()}


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    clear(session_id)
    return {"cleared": session_id}


# ── Morning briefing push helper ──────────────────────────────────────────

@app.post("/briefing/{session_id}")
def trigger_briefing(session_id: str):
    """
    Compose and queue a morning briefing for the given session.
    Called by notifier.py or a scheduled task.
    """
    from memory.semantic import SemanticMemory
    from memory.briefing import compose as compose_briefing

    session = get_or_create(session_id)
    text = compose_briefing(session.semantic)
    _enqueue(session_id, text)
    return {"queued": True, "preview": text[:120]}
