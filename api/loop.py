"""
Headless agentic loop — shared by api/server.py and any future non-UI client.

Unlike the Streamlit loop in app.py, this version:
  - Does NOT stream (returns the full response text at once)
  - Cannot render approval cards
  - Handles destructive tools via a PAUSE protocol:
      returns {needs_approval: True, pending_action: {...}} instead of executing
      caller sends the approval decision back as the next message
  - Is safe to call from a thread (no Streamlit state)

The approval PAUSE protocol:
  1. Loop detects a destructive tool call
  2. Returns immediately with needs_approval=True + pending_action
  3. The API caller (bridge) shows the action to the user and waits for YES/NO
  4. Next call to run_turn() with message == "APPROVE"/"DENY" resumes from saved state
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from llm import call_llm
from tools.registry import ALL_TOOLS, execute_tool, DESTRUCTIVE_TOOLS, record_declined
from tools.sentinel import classify as sentinel_classify, format_verdict

MAX_ITERATIONS = 15

_APPROVE_WORDS = {"yes", "approve", "ok", "go ahead", "proceed", "do it"}
_DENY_WORDS    = {"no", "deny", "cancel", "stop", "don't", "abort"}


@dataclass
class LoopResult:
    text: str
    needs_approval: bool = False
    pending_action: Optional[dict] = None   # {name, args, id, verdict_text}


def _build_system(session) -> str:
    from config import get_read_paths, get_write_paths
    parts = [
        "You are a personal AI Chief of Staff. Be concise — responses will be read "
        "in WhatsApp, so keep them short and plain-text. No markdown formatting. "
        "You have access to the user's email, calendar, files, and web search. "
        "For booking / desktop tasks, the user will approve each outbound action."
    ]
    read_scope = get_read_paths()
    write_scope = get_write_paths()
    parts.append(
        f"Read scope: {', '.join(read_scope) or '(none)'}. "
        f"Write scope: {', '.join(write_scope) or '(none)'}"
    )
    facts_block = session.semantic.as_prompt_block()
    if facts_block:
        parts.append(facts_block)
    return "\n\n".join(parts)


def run_turn(session, user_message: str) -> LoopResult:
    """
    Process one user message through the agentic loop.
    Returns a LoopResult. Mutates session.history in-place.
    """
    # ── Handle approval reply for a pending destructive tool ──────────────
    if session.has_pending():
        normalized = user_message.strip().lower()
        pending    = session.pending_approval

        if normalized in _APPROVE_WORDS:
            session.clear_pending()
            result = execute_tool(pending["name"], pending["args"])
            session.history.append({
                "role": "tool",
                "name": pending["name"],
                "id":   pending["id"],
                "content": result,
            })
            return _continue_loop(session)

        if normalized in _DENY_WORDS:
            record_declined(pending["name"], pending["args"])
            deny_result = f"User declined the {pending['name']} operation."
            session.history.append({
                "role": "tool",
                "name": pending["name"],
                "id":   pending["id"],
                "content": deny_result,
            })
            session.clear_pending()
            return _continue_loop(session)

        # Not a clear approval/denial — re-show the pending action
        v = pending.get("verdict_text", "")
        return LoopResult(
            text=(
                f"Waiting for your decision on the pending action.\n{v}\n"
                f"Reply YES to approve or NO to cancel."
            ),
            needs_approval=True,
            pending_action=pending,
        )

    # ── Normal turn: append user message and run loop ─────────────────────
    session.history.append({"role": "user", "content": user_message})
    return _continue_loop(session)


def _continue_loop(session) -> LoopResult:
    """Drive the LLM → tool → LLM loop until a final text answer or a pause."""
    system = _build_system(session)

    for _ in range(MAX_ITERATIONS):
        response = call_llm(session.history, system=system, tools=ALL_TOOLS)

        if response.tool_calls:
            # Record any text preamble the model emitted before tool calls
            if response.text.strip():
                session.history.append({"role": "model", "content": response.text})

            for tc in response.tool_calls:
                session.history.append({
                    "role": "tool_call",
                    "name": tc.name,
                    "args": tc.args,
                    "id":   tc.id,
                })

                if tc.name in DESTRUCTIVE_TOOLS:
                    # Pause — save pending and ask the user
                    verdict   = sentinel_classify(tc.name, tc.args)
                    v_text    = format_verdict(verdict)
                    preview   = _short_preview(tc.name, tc.args)
                    session.pending_approval = {
                        "name":        tc.name,
                        "args":        dict(tc.args),
                        "id":          tc.id,
                        "verdict_text": v_text,
                    }
                    return LoopResult(
                        text=(
                            f"I need to perform an action — please approve:\n\n"
                            f"{v_text}\n{preview}\n\n"
                            f"Reply YES to approve or NO to cancel."
                        ),
                        needs_approval=True,
                        pending_action=session.pending_approval,
                    )

                result = execute_tool(tc.name, tc.args)
                session.history.append({
                    "role": "tool",
                    "name": tc.name,
                    "id":   tc.id,
                    "content": result,
                })
            continue

        # Final text response
        final = response.text or "(no response)"
        session.history.append({"role": "model", "content": final})
        return LoopResult(text=final)

    return LoopResult(text="(agent hit iteration limit — please try again)")


def _short_preview(name: str, args: dict) -> str:
    """One-line tool preview for WhatsApp (plain text, no code block)."""
    if name == "browser_open":
        return f"Open: {args.get('url', '?')}"
    if name == "write_file":
        return f"Write: {args.get('path', '?')}"
    if name == "gmail_send_draft":
        return f"Send draft: {args.get('draft_id', '?')}"
    if name == "calendar_create_event":
        return f"Create event: {args.get('summary', '?')} at {args.get('start', '?')}"
    return f"{name}: {', '.join(f'{k}={v}' for k, v in list(args.items())[:2])}"
