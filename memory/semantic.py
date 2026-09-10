"""
Semantic memory — structured facts about the user stored in SQLite.

Schema: facts(namespace TEXT, key TEXT, value TEXT, updated_at TEXT)
        PRIMARY KEY (namespace, key)

Each user (owner, WhatsApp caller, etc.) gets their own namespace so
facts never bleed across sessions. The owner namespace is 'owner'.

Usage:
    mem = SemanticMemory()                        # owner facts
    mem = SemanticMemory(namespace="wa:9197...")  # per-user facts (API)
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"


class SemanticMemory:
    def __init__(self, db_path: Path = DB_PATH, namespace: str = "owner"):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._ns   = namespace
        self._bootstrap()

    def _bootstrap(self):
        # Create table with namespace support
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                namespace  TEXT NOT NULL DEFAULT 'owner',
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            )
        """)
        # Migrate legacy single-namespace table (no namespace column)
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(facts)").fetchall()]
        if "namespace" not in cols:
            self._conn.execute("ALTER TABLE facts ADD COLUMN namespace TEXT NOT NULL DEFAULT 'owner'")
            # Re-create primary key constraint isn't possible in SQLite ALTER,
            # but the default value ensures existing rows land in 'owner' namespace.
        self._conn.commit()

    def set(self, key: str, value: str):
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO facts(namespace, key, value, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (self._ns, key, value, now),
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM facts WHERE namespace=? AND key=?", (self._ns, key)
        ).fetchone()
        return row[0] if row else None

    def delete(self, key: str):
        self._conn.execute("DELETE FROM facts WHERE namespace=? AND key=?", (self._ns, key))
        self._conn.commit()

    def all(self) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM facts WHERE namespace=?", (self._ns,)
        ).fetchall()
        return {k: v for k, v in rows}

    def as_prompt_block(self) -> str:
        facts = self.all()
        if not facts:
            return ""
        lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
        return f"## What I know about the user\n{lines}"
