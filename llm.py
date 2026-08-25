"""
Thin LLM abstraction. All agent code calls call_llm() — never the SDK directly.
Provider swaps happen inside this file only.

Currently backed by Anthropic's Claude API with automatic model routing:
short/simple prompts run on Sonnet (cheap), complex ones escalate to Opus.
Prompt caching is enabled on the system prompt and tool definitions to cut
input-token cost by ~90% from the second turn onward.

Return contract:
    call_llm(...) -> LLMResponse
    LLMResponse.text        : str    (may be empty if the model chose to call tools)
    LLMResponse.tool_calls  : list[ToolCall]
    LLMResponse.model       : str    (which model actually ran this call)
    LLMResponse.raw         : the raw provider response

    ToolCall.name : str            (name of the tool the model wants to call)
    ToolCall.args : dict           (arguments the model wants to pass)
    ToolCall.id   : str            (Claude tool_use ID — must round-trip
                                    into the corresponding tool_result)
"""

import os
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Model catalog ────────────────────────────────────────────────────────
SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-7"
HAIKU = "claude-haiku-4-5-20251001"

# Aliases the /model command and env var accept (case-insensitive)
_MODEL_ALIASES = {
    "sonnet": SONNET,
    "opus": OPUS,
    "haiku": HAIKU,
    SONNET: SONNET,
    OPUS: OPUS,
    HAIKU: HAIKU,
}

MAX_OUTPUT_TOKENS = 4096


# ── Auto-router heuristics ───────────────────────────────────────────────

# Signals that the user wants deeper reasoning — escalate to Opus.
_OPUS_TRIGGER_KEYWORDS = (
    "analyze", "analyse", "reason ", "reasoning",
    "deep dive", "in depth", "in detail", "thoroughly",
    "brainstorm", "strategize", "strategise",
    "compare pros and cons", "trade-off", "tradeoff",
    "figure out why", "explain why", "why does", "why is",
    "think step by step", "walk me through",
    "carefully think", "think carefully",
    "use opus", "use the good model", "use the smart model", "use claude 4.7",
)

# Signals the user wants speed / low cost — stay on Sonnet even if long.
_SONNET_TRIGGER_KEYWORDS = (
    "quickly", "briefly", "short answer", "one-liner", "one liner",
    "just tell me", "just check", "just find", "just do",
    "use sonnet", "use the cheap model", "use the fast model",
)

# Messages this long usually pack multiple asks — escalate to Opus.
_LONG_MESSAGE_THRESHOLD = 500


# ── Session-level override (set by /model command or Streamlit selector) ──

_session_forced_model: str | None = None


def set_forced_model(name: str | None) -> str | None:
    """
    Set a session-level model override. Beats heuristics; loses to CLAUDE_MODEL
    env var. Pass None or 'auto' to return to automatic routing.

    Accepts short names ('sonnet', 'opus', 'haiku') or full model IDs.
    Returns the canonical model ID that was set, or None if auto.
    """
    global _session_forced_model
    if name is None:
        _session_forced_model = None
        return None
    key = name.strip().lower()
    if key in ("", "auto"):
        _session_forced_model = None
        return None
    resolved = _MODEL_ALIASES.get(key, name)
    _session_forced_model = resolved
    return resolved


def get_forced_model() -> str | None:
    return _session_forced_model


def pick_model(user_text: str) -> str:
    """
    Choose the best model for a given user message.

    Precedence (highest wins):
      1. CLAUDE_MODEL env var (permanent lock)
      2. Session-level override (from /model or Streamlit sidebar)
      3. Heuristic routing (keywords + length)
      4. Default: Sonnet
    """
    # 1. Env-var lock (permanent, set in .env)
    env_model = os.getenv("CLAUDE_MODEL", "").strip()
    if env_model and env_model.lower() != "auto":
        return _MODEL_ALIASES.get(env_model.lower(), env_model)

    # 2. Session override
    if _session_forced_model:
        return _session_forced_model

    # 3. Heuristics
    text = (user_text or "").lower()

    if any(kw in text for kw in _SONNET_TRIGGER_KEYWORDS):
        return SONNET
    if any(kw in text for kw in _OPUS_TRIGGER_KEYWORDS):
        return OPUS
    if len(user_text or "") > _LONG_MESSAGE_THRESHOLD:
        return OPUS

    # 4. Default — cheap and capable
    return SONNET


# ── Anthropic client ─────────────────────────────────────────────────────

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()   # reads ANTHROPIC_API_KEY from env
    return _client


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str = ""


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    raw: Any = None


# ── History translation ──────────────────────────────────────────────────

def _build_messages(history: list[dict]) -> list[dict]:
    """
    Translate the agent's internal history into Claude's messages format.

    Internal role tags:
      "user"       → plain-text user turn
      "model"      → plain-text assistant turn
      "tool_call"  → model requested a tool invocation. Must include name, args, id.
      "tool"       → tool execution result. Must include name, id, content.

    Claude collapses consecutive same-role blocks into one message with
    multiple content blocks — we coalesce as we walk.
    """
    messages: list[dict] = []

    def _last_role() -> str | None:
        return messages[-1]["role"] if messages else None

    def _append_block(role: str, block: dict):
        if _last_role() == role and isinstance(messages[-1]["content"], list):
            messages[-1]["content"].append(block)
        else:
            messages.append({"role": role, "content": [block]})

    for msg in history:
        role = msg["role"]

        if role == "user":
            _append_block("user", {"type": "text", "text": msg["content"]})

        elif role == "model":
            text = msg["content"]
            if not text:
                continue
            _append_block("assistant", {"type": "text", "text": text})

        elif role == "tool_call":
            _append_block("assistant", {
                "type": "tool_use",
                "id": msg["id"],
                "name": msg["name"],
                "input": dict(msg.get("args", {})),
            })

        elif role == "tool":
            _append_block("user", {
                "type": "tool_result",
                "tool_use_id": msg["id"],
                "content": msg["content"],
            })

    return messages


def _last_user_text(history: list[dict]) -> str:
    """Find the most recent user turn's text (for routing)."""
    for msg in reversed(history):
        if msg["role"] == "user":
            return msg.get("content", "") or ""
    return ""


# ── Main entrypoint ──────────────────────────────────────────────────────

def call_llm(
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
    model: str | None = None,
) -> LLMResponse:
    """
    Send a conversation to Claude and return an LLMResponse.

    messages: internal-format history (see _build_messages docstring)
    system:   system prompt string (cached on subsequent calls)
    tools:    list of tool declarations in Claude's format (see registry.py).
              Cached on subsequent calls.
    model:    optional explicit model. When None (default), auto-router picks
              based on the last user message + env/session overrides.
    """
    if model is None:
        model = pick_model(_last_user_text(messages))
    else:
        model = _MODEL_ALIASES.get(model.lower(), model)

    client = _get_client()
    claude_messages = _build_messages(messages)

    # Prompt caching — cache breakpoints on system prompt and on the last tool.
    system_param: Any = ""
    if system:
        system_param = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]

    tools_param: Any = None
    if tools:
        tools_param = [dict(t) for t in tools]
        if tools_param:
            tools_param[-1] = {**tools_param[-1], "cache_control": {"type": "ephemeral"}}

    create_kwargs: dict = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": claude_messages,
    }
    if system_param:
        create_kwargs["system"] = system_param
    if tools_param:
        create_kwargs["tools"] = tools_param

    response = client.messages.create(**create_kwargs)

    out = LLMResponse(raw=response, model=model)
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            out.text += block.text
        elif block_type == "tool_use":
            out.tool_calls.append(ToolCall(
                name=block.name,
                args=dict(block.input) if block.input else {},
                id=block.id,
            ))

    return out
