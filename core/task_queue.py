"""
Async task queue — run long agent tasks in the background.

For tasks that take more than a few seconds (deep research, multi-step
automation), the user should not have to stare at a spinner. Instead:

  1. User asks for a long task
  2. Agent says "I'm on it — I'll notify you when it's done"
  3. Task runs in a background thread
  4. On completion, the result is posted to the FastAPI outbox (→ WhatsApp)
     AND stored so the Streamlit sidebar can show it

Usage (from app.py or agent.py):
    from core.task_queue import enqueue, get_results, TaskStatus

    task_id = enqueue("Summarise all emails from this week", session_id="wa:...")
    # ... user carries on ...
    results = get_results()   # list of completed TaskResult objects
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


class TaskStatus(str, Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"


@dataclass
class Task:
    id:         str
    label:      str
    fn:         Callable[[], str]   # callable that returns the result string
    session_id: str                 # who to notify on completion
    status:     TaskStatus = TaskStatus.QUEUED
    result:     str = ""
    error:      str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""


# ── In-process registry ───────────────────────────────────────────────────

_tasks: dict[str, Task] = {}
_lock  = threading.Lock()


def enqueue(label: str, fn: Callable[[], str], session_id: str = "") -> str:
    """
    Add a task to the queue and start it in a daemon thread.
    Returns the task ID.

    label:      Human-readable name shown in the sidebar and notification
    fn:         Zero-argument callable that performs the work and returns a string
    session_id: Optional — if set, completion is pushed to FastAPI /push endpoint
    """
    tid = str(uuid.uuid4())[:8]
    task = Task(id=tid, label=label, fn=fn, session_id=session_id)
    with _lock:
        _tasks[tid] = task

    t = threading.Thread(target=_run, args=(tid,), daemon=True)
    t.start()
    return tid


def _run(tid: str) -> None:
    with _lock:
        task = _tasks.get(tid)
    if task is None:
        return

    task.status = TaskStatus.RUNNING
    try:
        result = task.fn()
        task.result = result or "(completed)"
        task.status = TaskStatus.DONE
    except Exception as e:
        task.error  = f"{type(e).__name__}: {e}"
        task.status = TaskStatus.FAILED

    task.finished_at = datetime.now(timezone.utc).isoformat()
    _notify(task)


def _notify(task: Task) -> None:
    """Push a completion notification to the FastAPI outbox (→ WhatsApp)."""
    if not task.session_id:
        return
    try:
        import httpx, os
        api = os.getenv("AGENT_API_URL", "http://localhost:8502").rstrip("/")
        emoji = "✅" if task.status == TaskStatus.DONE else "❌"
        text = (
            f"{emoji} Task done: {task.label}\n"
            f"{task.result[:500] if task.status == TaskStatus.DONE else task.error}"
        )
        httpx.post(
            f"{api}/push",
            json={"session_id": task.session_id, "message": text},
            timeout=5,
        )
    except Exception:
        pass   # server may not be running; failure is silent


# ── Readers ───────────────────────────────────────────────────────────────

def get_task(tid: str) -> Task | None:
    return _tasks.get(tid)


def list_tasks(status: TaskStatus | None = None) -> list[Task]:
    with _lock:
        tasks = list(_tasks.values())
    if status:
        tasks = [t for t in tasks if t.status == status]
    return sorted(tasks, key=lambda t: t.created_at, reverse=True)


def get_results() -> list[Task]:
    """Return all completed or failed tasks (for sidebar display)."""
    return list_tasks(TaskStatus.DONE) + list_tasks(TaskStatus.FAILED)


def clear_done() -> int:
    """Remove finished tasks from the registry. Returns count removed."""
    with _lock:
        done_ids = [tid for tid, t in _tasks.items()
                    if t.status in (TaskStatus.DONE, TaskStatus.FAILED)]
        for tid in done_ids:
            del _tasks[tid]
    return len(done_ids)


def format_status() -> str:
    """One-line status string for the Streamlit sidebar."""
    with _lock:
        total   = len(_tasks)
        running = sum(1 for t in _tasks.values() if t.status == TaskStatus.RUNNING)
        done    = sum(1 for t in _tasks.values() if t.status == TaskStatus.DONE)
        failed  = sum(1 for t in _tasks.values() if t.status == TaskStatus.FAILED)
    if total == 0:
        return "No background tasks."
    parts = []
    if running: parts.append(f"{running} running")
    if done:    parts.append(f"{done} done")
    if failed:  parts.append(f"{failed} failed")
    return f"Tasks: {', '.join(parts)}"
