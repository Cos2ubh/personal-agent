"""
Audit log for all tool executions.

Every tool call (allowed or denied) is written to data/access_log.jsonl as
one JSON object per line. Structured so it's grep-able, parseable, and
appendable without holding a lock.

Schema (per entry):
  ts          ISO-8601 UTC timestamp
  session_id  UUID4 generated once per process start — groups entries per run
  tool        tool name
  args        arguments passed (sensitive values from vault tools are redacted)
  risk        Sentinel risk tier: LOW / MEDIUM / HIGH (or "" if not classified)
  outcome     "ok" / "denied" / "error"
  summary     first 120 chars of result
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).parent.parent / "data" / "access_log.jsonl"

# One UUID per agent process — groups all log entries from a single run
_SESSION_ID = str(uuid.uuid4())


def get_session_id() -> str:
    return _SESSION_ID


def log(
    tool: str,
    args: dict,
    outcome: str,
    result_summary: str = "",
    risk: str = "",
) -> None:
    """
    Append one operation to the audit log.

    tool:           tool name (e.g. 'read_file', 'write_file')
    args:           arguments — vault secret values are auto-redacted
    outcome:        one of 'ok', 'denied', 'error'
    result_summary: short human-readable summary (first ~120 chars of result)
    risk:           Sentinel tier 'LOW'/'MEDIUM'/'HIGH' (empty if not applicable)
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe_args = _redact(tool, args)
    entry = {
        "ts":         datetime.now(timezone.utc).isoformat(),
        "session_id": _SESSION_ID,
        "tool":       tool,
        "args":       safe_args,
        "risk":       risk,
        "outcome":    outcome,
        "summary":    result_summary[:120],
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _redact(tool: str, args: dict) -> dict:
    """Never log secret values from vault operations."""
    if tool == "vault_store_secret":
        return {k: ("[REDACTED]" if k == "value" else v) for k, v in (args or {}).items()}
    if tool == "vault_unlock":
        return {k: ("[REDACTED]" if k == "passphrase" else v) for k, v in (args or {}).items()}
    return args or {}


# ── Readers ───────────────────────────────────────────────────────────────

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


def query_by_tool(tool_name: str, limit: int = 50) -> list[dict]:
    """Return the most recent `limit` entries for a specific tool."""
    if not LOG_PATH.exists():
        return []
    results = []
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("tool") == tool_name:
                    results.append(entry)
            except json.JSONDecodeError:
                continue
    return results[-limit:]


def today_summary() -> dict:
    """
    Count today's tool calls grouped by outcome and risk tier.
    Returns a dict usable for the morning briefing or sidebar display.
    """
    if not LOG_PATH.exists():
        return {"total": 0, "ok": 0, "denied": 0, "error": 0, "high_risk": 0}

    today = datetime.now(timezone.utc).date().isoformat()
    counts = {"total": 0, "ok": 0, "denied": 0, "error": 0, "high_risk": 0}

    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = e.get("ts", "")
            if not ts.startswith(today):
                continue
            counts["total"] += 1
            outcome = e.get("outcome", "")
            if outcome in counts:
                counts[outcome] += 1
            if e.get("risk") == "HIGH":
                counts["high_risk"] += 1

    return counts


def format_tail(n: int = 10) -> str:
    """Human-readable render of the last n entries."""
    entries = tail(n)
    if not entries:
        return "  No operations logged yet."
    lines = []
    for e in entries:
        ts      = e.get("ts", "?")[:19].replace("T", " ")
        tool    = e.get("tool", "?")
        outcome = e.get("outcome", "?")
        risk    = e.get("risk", "")
        args_str = ", ".join(f"{k}={v!r}" for k, v in (e.get("args") or {}).items())
        summary  = e.get("summary", "")
        marker   = {"ok": "✓", "denied": "✗", "error": "!"}.get(outcome, "?")
        risk_tag = f" [{risk}]" if risk else ""
        lines.append(f"  {marker} {ts}{risk_tag}  {tool}({args_str[:60]})")
        if summary:
            lines.append(f"      → {summary[:100]}")
    return "\n".join(lines)
