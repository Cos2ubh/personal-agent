"""
Reminders — persistent, SQLite-backed.

Stored alongside semantic memory in data/memory.db (new table `reminders`).
Times are stored in ISO 8601 with local timezone info so display is intuitive.

Not yet:
  - Background firing (needs a daemon or scheduled task)
  - Recurrence rules (daily/weekly)
Both are worth adding later; today reminders surface on agent start and via
/reminders and /briefing.
"""

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import dateparser

DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"


class Reminders:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._bootstrap()

    def _bootstrap(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                text         TEXT NOT NULL,
                due_at       TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                completed_at TEXT,
                notified_at  TEXT
            )
        """)
        self._conn.commit()

    def add(self, text: str, due_at_iso: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO reminders(text, due_at, created_at) VALUES(?,?,?)",
            (text, due_at_iso, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_all(self, include_completed: bool = False) -> list[dict]:
        if include_completed:
            rows = self._conn.execute(
                "SELECT id, text, due_at, created_at, completed_at, notified_at "
                "FROM reminders ORDER BY due_at ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, text, due_at, created_at, completed_at, notified_at "
                "FROM reminders WHERE completed_at IS NULL ORDER BY due_at ASC"
            ).fetchall()
        return [
            {
                "id": r[0], "text": r[1], "due_at": r[2],
                "created_at": r[3], "completed_at": r[4], "notified_at": r[5],
            }
            for r in rows
        ]

    def due_now(self) -> list[dict]:
        """Reminders whose due time has passed and that haven't been notified yet."""
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = self._conn.execute(
            "SELECT id, text, due_at FROM reminders "
            "WHERE completed_at IS NULL AND notified_at IS NULL AND due_at <= ? "
            "ORDER BY due_at ASC",
            (now_iso,),
        ).fetchall()
        return [{"id": r[0], "text": r[1], "due_at": r[2]} for r in rows]

    def mark_notified(self, reminder_id: int):
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE reminders SET notified_at=? WHERE id=?",
            (now, reminder_id),
        )
        self._conn.commit()

    def complete(self, reminder_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE reminders SET completed_at=? WHERE id=? AND completed_at IS NULL",
            (now, reminder_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete(self, reminder_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def due_today(self) -> list[dict]:
        """Reminders due before end of local-today (regardless of notified state)."""
        now = datetime.now().astimezone()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        end_iso = end_of_day.astimezone(timezone.utc).isoformat()
        rows = self._conn.execute(
            "SELECT id, text, due_at FROM reminders "
            "WHERE completed_at IS NULL AND due_at <= ? "
            "ORDER BY due_at ASC",
            (end_iso,),
        ).fetchall()
        return [{"id": r[0], "text": r[1], "due_at": r[2]} for r in rows]


# ── Time parsing ──────────────────────────────────────────────────────────

_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
_RELATIVE_PREFIX = re.compile(
    r"^(next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

def parse_time(when: str) -> str | None:
    """
    Turn a natural-language time expression into an ISO 8601 UTC string.

    Examples that work:
      'tomorrow 6pm', 'next Thursday 2pm', 'this Friday', 'in 2 hours',
      'July 15 9am', '2026-08-15T14:00', 'Monday morning'.

    Returns None if the string can't be parsed.
    """
    if not when or not when.strip():
        return None

    _settings = {
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "local",
    }

    parsed = dateparser.parse(when, settings=_settings)

    # dateparser chokes on "next <weekday>" and "this <weekday>" — strip the
    # leading "next"/"this" and retry since PREFER_DATES_FROM: future already
    # resolves bare weekday names to the upcoming occurrence.
    if not parsed and _RELATIVE_PREFIX.match(when):
        stripped = _RELATIVE_PREFIX.sub(lambda m: m.group(2), when, count=1)
        parsed = dateparser.parse(stripped, settings=_settings)

    if not parsed:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def format_due(due_at_iso: str) -> str:
    """Human-friendly display of an ISO due time in the user's local timezone."""
    try:
        dt = datetime.fromisoformat(due_at_iso).astimezone()
    except ValueError:
        return due_at_iso
    return dt.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
