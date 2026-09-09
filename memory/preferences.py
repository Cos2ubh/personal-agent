"""
Preference learning — stores how the user likes things done.

Unlike semantic facts (which are discrete key=value pairs), preferences are
categorised defaults that shape HOW the agent behaves, not WHAT it knows.

Examples:
  category=email,  key=tone,            value=concise
  category=travel, key=seat,            value=window
  category=travel, key=train_class,     value=3A
  category=food,   key=dietary,         value=vegetarian
  category=calendar, key=meeting_buffer, value=15 min before
  category=response, key=length,        value=short

Stored in SQLite (same memory.db). Injected into the system prompt as a
"User preferences" block so the agent applies them automatically.

Agent tools:
  set_preference(category, key, value)  → store / update
  list_preferences()                    → human-readable list
  get_preference(category, key)         → single value lookup
  delete_preference(category, key)      → remove
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(_DB_PATH))
    con.row_factory = sqlite3.Row
    _ensure_table(con)
    return con


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT NOT NULL,
            key          TEXT NOT NULL,
            value        TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(category, key)
        )
    """)
    con.commit()


# ── Public API ────────────────────────────────────────────────────────────

def set_preference(category: str, key: str, value: str) -> str:
    category = category.strip().lower()
    key      = key.strip().lower()
    value    = value.strip()
    if not category or not key or not value:
        return "Error: category, key, and value are all required."
    with _conn() as con:
        con.execute("""
            INSERT INTO preferences (category, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(category, key) DO UPDATE SET value=excluded.value,
                updated_at=datetime('now')
        """, (category, key, value))
        con.commit()
    return f"Preference set: [{category}] {key} = {value}"


def get_preference(category: str, key: str) -> str:
    category = category.strip().lower()
    key      = key.strip().lower()
    with _conn() as con:
        row = con.execute(
            "SELECT value FROM preferences WHERE category=? AND key=?",
            (category, key),
        ).fetchone()
    if not row:
        return f"No preference set for [{category}] {key}."
    return row["value"]


def list_preferences() -> str:
    with _conn() as con:
        rows = con.execute(
            "SELECT category, key, value FROM preferences ORDER BY category, key"
        ).fetchall()
    if not rows:
        return "No preferences saved yet."
    current_cat = None
    lines = []
    for r in rows:
        if r["category"] != current_cat:
            current_cat = r["category"]
            lines.append(f"\n[{current_cat}]")
        lines.append(f"  {r['key']}: {r['value']}")
    return "User preferences:" + "\n".join(lines)


def delete_preference(category: str, key: str) -> str:
    category = category.strip().lower()
    key      = key.strip().lower()
    with _conn() as con:
        n = con.execute(
            "DELETE FROM preferences WHERE category=? AND key=?",
            (category, key),
        ).rowcount
        con.commit()
    if n == 0:
        return f"No preference found for [{category}] {key}."
    return f"Preference [{category}] {key} deleted."


def as_prompt_block() -> str:
    """
    Return a formatted block for injection into the system prompt.
    Returns empty string if no preferences are set.
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT category, key, value FROM preferences ORDER BY category, key"
        ).fetchall()
    if not rows:
        return ""
    current_cat = None
    lines = ["## User preferences (apply these automatically)"]
    for r in rows:
        if r["category"] != current_cat:
            current_cat = r["category"]
            lines.append(f"[{current_cat}]")
        lines.append(f"  {r['key']}: {r['value']}")
    return "\n".join(lines)
