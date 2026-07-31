"""
Auto fact extraction.

After every user turn, run a lightweight LLM pass that scans the user's message
for durable personal facts and returns them as JSON. The agent loop then saves
any new facts to semantic memory silently, with a tiny indicator so the user
knows what got captured.

Errors in extraction never propagate — this is best-effort background polish,
not a critical path. If the extractor fails, the main conversation continues.
"""

import json
import re

from llm import call_llm


EXTRACTOR_SYSTEM = """You extract DURABLE personal facts from a user's message.

Durable means: likely to remain true over months or years, and useful for a
personal assistant to remember about the user.

DO extract:
  - Name, age, birthday, home city, occupation, employer
  - Family and close relationships (spouse, kids, parents, siblings) with names
  - Long-term preferences (food, music, brands, workflow habits)
  - Allergies, dietary restrictions, medical conditions
  - Skills, credentials, languages spoken, tools they use
  - Goals or milestones with clear time horizons

DO NOT extract:
  - Current mood, weather, transient plans ("I'm going to the gym today")
  - Questions ("what's the weather?", "how do I do X?")
  - Hypotheticals ("if I were to...", "let's say...")
  - Facts about third parties who aren't close family
  - Anything already implied by keys already present in the "existing facts" list

Output format — return ONLY valid JSON, nothing else:
  {"facts": [{"key": "user_name", "value": "Kaustubh"}, ...]}

If nothing durable is present, return: {"facts": []}

Key conventions:
  - snake_case
  - prefix with user_ (e.g. user_birthday, user_allergy_peanuts, user_home_city)
  - use suffixes for scoped facts (user_sister_name, user_prefers_coffee)
Value conventions:
  - shortest string that preserves the fact
  - dates as "Month Day" or "Month Day, Year" — never ISO
  - preferences as the thing preferred, not sentences ("mango lassi", not "loves mango lassi")
"""


_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _parse_response(text: str) -> list[tuple[str, str]]:
    """Extract (key, value) pairs from a JSON response. Robust to code-fence wrappers."""
    if not text:
        return []

    # Strip ```json fences if present
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []

    facts = data.get("facts", [])
    if not isinstance(facts, list):
        return []

    out = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip().lower()
        value = str(item.get("value", "")).strip()
        if not key or not value:
            continue
        if not _KEY_PATTERN.match(key):
            continue
        # Cap value length so a hallucinated essay can't fill semantic memory
        if len(value) > 300:
            value = value[:300]
        out.append((key, value))
    return out


def extract_facts(user_message: str, existing_facts: dict[str, str]) -> list[tuple[str, str]]:
    """
    Look for durable facts in a user message.

    user_message:    the raw user turn text
    existing_facts:  the current semantic-memory dict (used for dedup context in the prompt)

    Returns list of (key, value) tuples for facts that are NEW or CHANGED
    relative to existing_facts. Never raises — errors return [].
    """
    if not user_message or not user_message.strip():
        return []

    # Build the extraction prompt — include existing facts so the model can dedup
    existing_block = (
        "Existing facts already stored (do not re-extract if the value is unchanged):\n"
        + "\n".join(f"  {k}: {v}" for k, v in existing_facts.items())
        if existing_facts
        else "Existing facts: (none yet)"
    )

    messages = [{
        "role": "user",
        "content": f"{existing_block}\n\nUser message to analyze:\n{user_message}",
    }]

    try:
        response = call_llm(messages, system=EXTRACTOR_SYSTEM)
    except Exception:
        return []

    candidates = _parse_response(response.text)

    # Filter: only return facts that are new OR have a different value than stored
    new_or_changed = []
    for key, value in candidates:
        if existing_facts.get(key) != value:
            new_or_changed.append((key, value))
    return new_or_changed
