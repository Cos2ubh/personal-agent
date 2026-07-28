"""
Audit log for file system operations.

Every FS tool call (allowed or denied) is written to data/access_log.jsonl
as one JSON object per line. Structured so it's grep-able, parseable, and
appendable without holding a lock.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).parent.parent / "data" / "access_log.jsonl"


def log(tool: str, args: dict, outcome: str, result_summary: str = ""):
    """
    Append one operation to the audit log.

    tool:            tool name (e.g. 'read_file', 'write_file')
    args:            arguments the tool was called with
    outcome:         one of 'ok', 'denied', 'error'
    result_summary:  short human-readable summary (first ~120 chars of result)
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": args,
        "outcome": outcome,
        "summary": result_summary[:120],
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def tail(n: int = 10) -> list[dict]:
    """Return the last n log entries as dicts. Empty list if no log yet."""
    if not LOG_PATH.exists():
        return []
    with LOG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in lines[-n:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def format_tail(n: int = 10) -> str:
    """Human-readable render of the last n entries."""
    entries = tail(n)
    if not entries:
        return "  No file system operations logged yet."
    lines = []
    for e in entries:
        ts = e.get("ts", "?")[:19].replace("T", " ")  # trim microseconds & TZ
        tool = e.get("tool", "?")
        outcome = e.get("outcome", "?")
        args_str = ", ".join(f"{k}={v!r}" for k, v in (e.get("args") or {}).items())
        summary = e.get("summary", "")
        marker = {"ok": "✓", "denied": "✗", "error": "!"}.get(outcome, "?")
        lines.append(f"  {marker} {ts}  {tool}({args_str})")
        if summary:
            lines.append(f"      → {summary}")
    return "\n".join(lines)
