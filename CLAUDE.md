# CLAUDE.md — Working notes for AI assistants

Context for future Claude Code sessions working on this project. Human contributors — see README.md instead.

## What this project is

`personal-agent` is an AI personal assistant designed to complete tasks end-to-end (not just recommend). Full USP and roadmap live in README.md. This file focuses on implementation context and working conventions.

## Current state (Session 1 complete, 2026-07-27)

- Python 3.13 venv at `./venv/`
- Dependencies installed: `google-genai`, `python-dotenv` (no `requirements.txt` yet — add during Session 2)
- `test_key.py` verifies Gemini API connectivity using key from `.env`
- README.md, `.env.example`, `.gitignore` in place
- Live on GitHub: https://github.com/Cos2ubh/personal-agent (public)

## Next session — chat loop + memory

Building persistent conversation with hybrid memory:

- **Chat loop:** input → LLM call → tool execution (when needed) → response → repeat. Graceful exit on `quit` / Ctrl+C.
- **Memory tiers:**
  - Semantic (SQLite table) — structured facts about the user
  - Episodic (ChromaDB embedded) — past conversations, timestamped, retrievable by similarity
  - Working (in-memory) — current task state
  - Procedural (rules table) — deferred to later session
- **Retrieval strategy:** at each turn, pull top-N relevant memories via vector similarity + explicit high-salience facts, inject into system prompt.

Then in subsequent weeks: Gmail + Calendar OAuth (Week 2), reminders + morning briefing (Week 3), Streamlit UI + demo video (Week 4).

## Architectural constraints

**LLM abstraction:** Gemini 2.5 Flash used during dev (free tier). All LLM calls should go through a thin `call_llm(messages, tools)` interface so switching to Claude 4.7 is a one-file change. Model-specific quirks (Gemini's function-calling format, Claude's tool-use format) hidden behind this interface.

**No framework wrapper:** Hand-rolled agent loop (~50 lines) — not Claude Agent SDK, not LangGraph, not LlamaIndex. This is a pedagogical choice: developer wants to understand every layer for FO interview defensibility. Do NOT introduce agent frameworks as shortcuts.

**Secrets:** `.env` (gitignored), loaded via `python-dotenv`. Never hardcode keys. Never commit real keys.

## Working conventions for AI assistants

1. **Step-by-step teaching mode.** Developer is a beginner intentionally building from first principles. Break every technical addition into 5-10 min chunks: concept → action → explanation of what happened → verify → next. Use TodoWrite to track progress visibly.

2. **No `Co-Authored-By: Claude` on commits.** Portfolio integrity concern — this project is meant to be defensible in interviews as his work.

3. **Verify empirically, don't assume.** If uncertain about something (like the API key format was — turned out to be a newer Google format), say so and test. Don't confidently assert.

4. **Commit style:** clear single-purpose commits, message describes *why* not just *what*. Push at end of each session.

## Environment

- OS: Windows 11
- Shell: PowerShell
- Python: 3.13.6 default (`python`), 3.14.2 available (`py`)
- `gh` CLI: 2.96.0, installed via winget. **Not on PATH in fresh shells** — use `& "C:\Program Files\GitHub CLI\gh.exe"` in tool calls, or open a new PowerShell window manually.
- Git config: `user.name = "Cos2ubh"`, `user.email = "22050320@coer.ac.in"` (college address — commits may not visually attribute to GitHub profile unless email is added there)
- Line endings: CRLF/LF warnings from git are normal on Windows, harmless

## References

- GitHub: https://github.com/Cos2ubh/personal-agent
- USP: see README.md
