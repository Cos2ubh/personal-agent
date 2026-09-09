"""
In-memory session store for the REST API.

Each session (identified by a phone number or UUID) tracks:
  - conversation history (same format as app.py / agent.py)
  - a pending destructive-tool approval (if any)
  - the SemanticMemory instance for that user

Sessions are kept in a module-level dict — they persist for the life of the
uvicorn process. A future version can pickle to SQLite for restarts.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Session:
    session_id: str
    history: list[dict] = field(default_factory=list)
    pending_approval: Optional[dict] = None   # {tc_name, tc_args, tc_id}
    semantic: object = None                    # SemanticMemory, lazy init

    def has_pending(self) -> bool:
        return self.pending_approval is not None

    def clear_pending(self) -> None:
        self.pending_approval = None


_sessions: dict[str, Session] = {}


def get_or_create(session_id: str) -> Session:
    if session_id not in _sessions:
        from memory.semantic import SemanticMemory
        _sessions[session_id] = Session(
            session_id=session_id,
            semantic=SemanticMemory(),
        )
    return _sessions[session_id]


def get(session_id: str) -> Optional[Session]:
    return _sessions.get(session_id)


def clear(session_id: str) -> None:
    _sessions.pop(session_id, None)


def all_ids() -> list[str]:
    return list(_sessions.keys())
