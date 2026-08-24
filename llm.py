"""
Thin LLM abstraction. All agent code calls call_llm() — never the SDK directly.
Provider swaps happen inside this file only.

Currently backed by Anthropic's Claude API. Prompt caching is enabled on the
system prompt and tool definitions — those don't change turn-to-turn, so
caching cuts input-token cost by roughly 90% on the second turn onward.

Return contract:
    call_llm(...) -> LLMResponse
    LLMResponse.text        : str    (may be empty if the model chose to call tools)
    LLMResponse.tool_calls  : list[ToolCall]
    LLMResponse.raw         : the raw provider response

    ToolCall.name : str            (name of the tool the model wants to call)
    ToolCall.args : dict           (arguments the model wants to pass)
    ToolCall.id   : str            (Claude tool_use ID — MUST round-trip
                                    into the corresponding tool_result)
"""

import os
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# Model choice — Sonnet 4.6 is the sweet spot of capability vs cost for a
# personal agent. Override with CLAUDE_MODEL env var if you want to try
# Opus 4.7 ('claude-opus-4-7') or Haiku 4.5 ('claude-haiku-4-5-20251001').
DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_OUTPUT_TOKENS = 4096

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
    id: str = ""   # tool_use ID — must be preserved for the matching tool_result


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


def _build_messages(history: list[dict]) -> list[dict]:
    """
    Translate the agent's internal history format into Claude's messages API.

    Internal history uses these role tags:
      "user"       → plain-text user turn
      "model"      → plain-text assistant turn
      "tool_call"  → model requested a tool invocation. Must include name, args, id.
      "tool"       → tool execution result. Must include name, id, content.

    Claude's format collapses consecutive assistant blocks into one message
    with multiple content blocks. We coalesce as we walk the history.
    """
    messages: list[dict] = []

    def _last_role() -> str | None:
        return messages[-1]["role"] if messages else None

    def _append_block(role: str, block: dict):
        # Coalesce successive blocks of the same role
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


def call_llm(
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
) -> LLMResponse:
    """
    Send a conversation to Claude and return an LLMResponse.

    messages: internal-format history (see _build_messages docstring)
    system:   system prompt string (cached on subsequent calls)
    tools:    list of tool declarations in Claude's format (see registry.py).
              Cached on subsequent calls.
    """
    client = _get_client()
    claude_messages = _build_messages(messages)

    # Prompt caching — put a cache breakpoint on the last tool and on the
    # system prompt. Claude caches everything up to each breakpoint. After
    # the first request, subsequent calls read from cache (~10% of normal
    # input-token cost) until the 5-min TTL expires.
    system_param: Any = ""
    if system:
        system_param = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]

    tools_param: Any = None
    if tools:
        # Copy so we don't mutate the shared registry list
        tools_param = [dict(t) for t in tools]
        if tools_param:
            tools_param[-1] = {**tools_param[-1], "cache_control": {"type": "ephemeral"}}

    create_kwargs: dict = {
        "model": DEFAULT_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": claude_messages,
    }
    if system_param:
        create_kwargs["system"] = system_param
    if tools_param:
        create_kwargs["tools"] = tools_param

    response = client.messages.create(**create_kwargs)

    out = LLMResponse(raw=response)
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
