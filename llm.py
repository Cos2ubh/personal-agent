"""
Thin LLM abstraction. All agent code calls call_llm() — never the SDK directly.
To swap Gemini → Claude: change only this file.

Return contract:
    call_llm(...) -> LLMResponse
    LLMResponse.text       : str    (may be empty if the model chose to call tools)
    LLMResponse.tool_calls : list[ToolCall]
    LLMResponse.raw        : the raw provider response (for advanced use)

    ToolCall.name : str            (name of the tool the model wants to call)
    ToolCall.args : dict           (arguments the model wants to pass)
    ToolCall.id   : str            (opaque id used to correlate the result)
"""

import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
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
    raw: Any = None


def call_llm(
    messages: list[dict],
    system: str = "",
    tools: list[types.Tool] | None = None,
) -> LLMResponse:
    """
    Send a conversation to the LLM and return an LLMResponse.

    messages: list of {"role": "user"|"model"|"tool", "content": "...", ...}
              For tool results, use role="tool" with extra keys:
                  {"role": "tool", "name": "read_file", "content": "...file contents..."}
    system:   optional system prompt injected before the conversation
    tools:    list of google.genai.types.Tool descriptors. When present, the model
              may respond with tool_calls instead of text.
    """
    client = _get_client()

    contents = []
    for msg in messages:
        role = msg["role"]
        if role == "tool":
            # Tool result — Gemini expects this as a Part with function_response
            part = types.Part.from_function_response(
                name=msg["name"],
                response={"result": msg["content"]},
            )
            contents.append(types.Content(role="user", parts=[part]))
        elif role == "tool_call":
            # A prior turn where the model requested a tool call — replay it as a model turn
            part = types.Part.from_function_call(
                name=msg["name"],
                args=msg["args"],
            )
            contents.append(types.Content(role="model", parts=[part]))
        else:
            # Plain text turn (user or model)
            contents.append(
                types.Content(role=role, parts=[types.Part(text=msg["content"])])
            )

    config = types.GenerateContentConfig(
        system_instruction=system if system else None,
        temperature=0.7,
        tools=tools if tools else None,
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=config,
    )

    # Parse response: could be text, tool calls, or both
    out = LLMResponse(raw=response)

    # Gemini surfaces function_calls as a convenience attribute
    if getattr(response, "function_calls", None):
        for fc in response.function_calls:
            out.tool_calls.append(
                ToolCall(name=fc.name, args=dict(fc.args) if fc.args else {})
            )

    # Extract text if present (even alongside tool calls)
    try:
        out.text = response.text or ""
    except Exception:
        # response.text raises when the response is *only* tool calls
        out.text = ""

    return out
