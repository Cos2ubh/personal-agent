"""
Personal Agent — main entry point.
Run:  python agent.py

Special commands (type during chat):
  /remember key = value   — save a fact to semantic memory
  /forget key             — delete a fact from semantic memory
  /facts                  — list all stored facts
  /permissions            — re-run the file system access wizard
  /help                   — show this command list
  /quit  or  quit         — exit
"""

from config import fs_permissions_configured, setup_fs_permissions, get_allowed_paths
from llm import call_llm
from memory.semantic import SemanticMemory
from memory.episodic import EpisodicMemory
from tools.registry import ALL_TOOLS, execute_tool

MAX_TOOL_ITERATIONS = 6  # hard cap on tool-call chains per user turn

SYSTEM_BASE = """You are a personal AI assistant — a Chief of Staff for the user.
You are installed locally on their machine. You remember facts about them and refer to
past conversations when relevant. Be concise, direct, and genuinely helpful.

You have TOOLS for reading files, listing folders, and writing files on the user's machine.
Use them proactively when a task requires actual file system access — don't guess file
contents or fabricate directory listings. If a tool returns PermissionDenied, explain to
the user which path was blocked and suggest they add it via /permissions.

Never make up information. If you don't know something, say so."""


def build_system_prompt(user_msg: str, semantic: SemanticMemory, episodic: EpisodicMemory) -> str:
    parts = [SYSTEM_BASE]

    allowed = get_allowed_paths()
    if allowed:
        parts.append("## Current file system allow-list\n" + "\n".join(f"- {p}" for p in allowed))
    else:
        parts.append("## Current file system allow-list\n(none — no file access configured)")

    facts_block = semantic.as_prompt_block()
    if facts_block:
        parts.append(facts_block)
    history_block = episodic.as_prompt_block(user_msg)
    if history_block:
        parts.append(history_block)
    return "\n\n".join(parts)


def handle_command(raw: str, semantic: SemanticMemory) -> bool:
    """
    Handle slash commands. Returns True if the input was a command (so the
    main loop skips sending it to the LLM).
    """
    cmd = raw.strip()

    if cmd.startswith("/remember "):
        rest = cmd[len("/remember "):].strip()
        if "=" in rest:
            key, _, value = rest.partition("=")
            semantic.set(key.strip(), value.strip())
            print(f"  Remembered: {key.strip()} = {value.strip()}")
        else:
            print("  Usage: /remember key = value")
        return True

    if cmd.startswith("/forget "):
        key = cmd[len("/forget "):].strip()
        semantic.delete(key)
        print(f"  Forgot: {key}")
        return True

    if cmd == "/facts":
        facts = semantic.all()
        if facts:
            print("  Stored facts:")
            for k, v in facts.items():
                print(f"    {k}: {v}")
        else:
            print("  No facts stored yet. Use /remember key = value to add one.")
        return True

    if cmd == "/permissions":
        setup_fs_permissions()
        return True

    if cmd == "/help":
        print(__doc__)
        return True

    return False


def run_agentic_turn(user_input: str, history: list[dict], system: str) -> str:
    """
    Handle one user turn — may involve multiple tool calls before final text.

    Mutates history in place: appends the user turn, any tool_call/tool pairs,
    and the final model reply.
    Returns the final model text.
    """
    history.append({"role": "user", "content": user_input})

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = call_llm(history, system=system, tools=ALL_TOOLS)

        # If model returned tool calls, execute them and loop
        if response.tool_calls:
            for tc in response.tool_calls:
                print(f"  [tool: {tc.name}({', '.join(f'{k}={v!r}' for k, v in tc.args.items())})]")
                # Record the model's intent to call the tool
                history.append({
                    "role": "tool_call",
                    "name": tc.name,
                    "args": tc.args,
                })
                # Execute and record the result
                result = execute_tool(tc.name, tc.args)
                # Truncate long results in the console preview only (LLM still sees full)
                preview = result[:200] + ("..." if len(result) > 200 else "")
                print(f"  [result: {preview}]")
                history.append({
                    "role": "tool",
                    "name": tc.name,
                    "content": result,
                })
            continue  # loop: let the model see tool results

        # No tool calls — final answer
        final_text = response.text or "(agent returned no text)"
        history.append({"role": "model", "content": final_text})
        return final_text

    # Hit the iteration cap
    msg = f"(agent exceeded {MAX_TOOL_ITERATIONS} tool-call iterations — stopping)"
    history.append({"role": "model", "content": msg})
    return msg


def main():
    print("─── Personal Agent ─────────────────────────────────────────")
    print("Type /help for commands, 'quit' to exit.\n")

    if not fs_permissions_configured():
        print("First run detected — let's configure file system access.\n")
        setup_fs_permissions()

    semantic = SemanticMemory()
    episodic = EpisodicMemory()
    history: list[dict] = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "/quit"):
            print("Goodbye.")
            break

        if handle_command(user_input, semantic):
            continue

        system = build_system_prompt(user_input, semantic, episodic)

        try:
            reply = run_agentic_turn(user_input, history, system)
        except Exception as e:
            print(f"  [LLM error: {e}]")
            # Roll back the incomplete turn so history stays consistent
            while history and history[-1]["role"] != "model":
                history.pop()
            continue

        print(f"\nAgent: {reply}\n")

        # Persist to episodic memory (only the user text + final agent reply)
        episodic.save_turn(user_input, reply)


if __name__ == "__main__":
    main()
