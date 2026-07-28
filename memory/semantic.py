"""
Semantic memory — structured facts about the user stored in SQLite.
Schema: facts(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)

Usage:
    from memory.semantic import SemanticMemory
    mem = SemanticMemory()
    mem.set("user_name", "Kaustubh")
    mem.get("user_name")          # "Kaustubh"
    mem.as_prompt_block()         # formatted string for injection into system prompt
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"


class SemanticMemory:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._bootstrap()

    def _bootstrap(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def set(self, key: str, value: str):
        """Write or overwrite a fact."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO facts(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now),
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        """Read a fact by key. Returns None if not found."""
        row = self._conn.execute(
            "SELECT value FROM facts WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def delete(self, key: str):
        self._conn.execute("DELETE FROM facts WHERE key=?", (key,))
        self._conn.commit()

    def all(self) -> dict[str, str]:
        """Return all facts as a plain dict."""
        rows = self._conn.execute("SELECT key, value FROM facts").fetchall()
        return {k: v for k, v in rows}

    def as_prompt_block(self) -> str:
        """
        Returns a formatted string ready to be injected into the system prompt.
        Empty string if no facts are stored yet.
        """
        facts = self.all()
        if not facts:
            return ""
        lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
        return f"## What I know about the user\n{lines}"
