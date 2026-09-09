"""
Long-term goal tracker.

Stores goals in SQLite (same memory.db used by reminders and semantic memory).
Each goal has a horizon (number of days), a set of weekly milestones, and a
status. The morning briefing surfaces the current week's milestone automatically.

Schema:
  goals(id, title, description, horizon_days, created_at, target_date,
        milestones TEXT,   -- JSON list of {week, text, done: bool}
        status TEXT,       -- 'active' / 'paused' / 'completed'
        progress_notes TEXT -- free-text log of updates)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(_DB_PATH))
    con.row_factory = sqlite3.Row
    _ensure_table(con)
    return con


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            description     TEXT DEFAULT '',
            horizon_days    INTEGER NOT NULL DEFAULT 90,
            created_at      TEXT NOT NULL,
            target_date     TEXT NOT NULL,
            milestones      TEXT NOT NULL DEFAULT '[]',
            status          TEXT NOT NULL DEFAULT 'active',
            progress_notes  TEXT DEFAULT ''
        )
    """)
    con.commit()


# ── Public API ────────────────────────────────────────────────────────────

def create_goal(
    title: str,
    horizon_days: int = 90,
    description: str = "",
    milestones: list[str] | None = None,
) -> str:
    """
    Create a new goal. If milestones is omitted, auto-generates weekly
    placeholders (one per week over the horizon).

    Returns a human-readable confirmation.
    """
    title = title.strip()
    if not title:
        return "Error: goal title cannot be empty."
    if horizon_days < 1 or horizon_days > 3650:
        return "Error: horizon_days must be between 1 and 3650."

    created_at  = date.today().isoformat()
    target_date = (date.today() + timedelta(days=horizon_days)).isoformat()

    # Build milestone list
    if milestones:
        ml = [{"week": i + 1, "text": m.strip(), "done": False}
              for i, m in enumerate(milestones)]
    else:
        weeks = max(1, horizon_days // 7)
        ml = [{"week": w + 1, "text": f"Week {w + 1} milestone (to be defined)", "done": False}
              for w in range(weeks)]

    with _conn() as con:
        cur = con.execute(
            """INSERT INTO goals (title, description, horizon_days, created_at,
               target_date, milestones, status, progress_notes)
               VALUES (?, ?, ?, ?, ?, ?, 'active', '')""",
            (title, description, horizon_days, created_at, target_date, json.dumps(ml)),
        )
        goal_id = cur.lastrowid
        con.commit()

    return (
        f"Goal #{goal_id} created: '{title}'\n"
        f"Target: {target_date} ({horizon_days} days)\n"
        f"Milestones: {len(ml)} weekly checkpoints"
    )


def list_goals(include_completed: bool = False) -> str:
    """Return a formatted list of goals."""
    with _conn() as con:
        if include_completed:
            rows = con.execute("SELECT * FROM goals ORDER BY id DESC").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM goals WHERE status != 'completed' ORDER BY id DESC"
            ).fetchall()

    if not rows:
        status_note = "" if include_completed else " (active only)"
        return f"No goals{status_note}. Use set_goal to add one."

    lines = []
    for r in rows:
        ml     = json.loads(r["milestones"] or "[]")
        done_n = sum(1 for m in ml if m.get("done"))
        lines.append(
            f"  #{r['id']} [{r['status'].upper()}] {r['title']}\n"
            f"      Target: {r['target_date']}  |  "
            f"Milestones: {done_n}/{len(ml)} done"
        )
    return "\n".join(lines)


def get_goal(goal_id: int) -> str:
    """Return full detail of one goal."""
    with _conn() as con:
        row = con.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not row:
        return f"Goal #{goal_id} not found."

    ml = json.loads(row["milestones"] or "[]")
    lines = [
        f"Goal #{row['id']}: {row['title']}",
        f"Status:  {row['status']}",
        f"Target:  {row['target_date']}  ({row['horizon_days']} days)",
        f"Created: {row['created_at']}",
    ]
    if row["description"]:
        lines.append(f"Notes:   {row['description']}")
    lines.append(f"\nMilestones ({len(ml)}):")
    for m in ml:
        tick = "✓" if m.get("done") else "○"
        lines.append(f"  [{tick}] Week {m['week']}: {m['text']}")
    if row["progress_notes"]:
        lines.append(f"\nProgress log:\n{row['progress_notes']}")
    return "\n".join(lines)


def update_milestone(goal_id: int, week: int, done: bool = True, new_text: str = "") -> str:
    """Mark a specific week's milestone as done (or update its text)."""
    with _conn() as con:
        row = con.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if not row:
            return f"Goal #{goal_id} not found."
        ml = json.loads(row["milestones"] or "[]")
        matched = False
        for m in ml:
            if m["week"] == week:
                m["done"] = done
                if new_text.strip():
                    m["text"] = new_text.strip()
                matched = True
                break
        if not matched:
            return f"Week {week} milestone not found in goal #{goal_id}."
        con.execute(
            "UPDATE goals SET milestones = ? WHERE id = ?",
            (json.dumps(ml), goal_id),
        )
        con.commit()
    status_word = "completed" if done else "re-opened"
    return f"Week {week} milestone {status_word} for goal #{goal_id}: '{row['title']}'"


def add_progress_note(goal_id: int, note: str) -> str:
    """Append a timestamped progress note to a goal."""
    note = note.strip()
    if not note:
        return "Error: note cannot be empty."
    ts = datetime.utcnow().strftime("%Y-%m-%d")
    with _conn() as con:
        row = con.execute("SELECT progress_notes FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if not row:
            return f"Goal #{goal_id} not found."
        existing = (row["progress_notes"] or "").strip()
        updated  = (existing + f"\n[{ts}] {note}").strip()
        con.execute("UPDATE goals SET progress_notes = ? WHERE id = ?", (updated, goal_id))
        con.commit()
    return f"Progress note added to goal #{goal_id}."


def complete_goal(goal_id: int) -> str:
    with _conn() as con:
        row = con.execute("SELECT title FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if not row:
            return f"Goal #{goal_id} not found."
        con.execute("UPDATE goals SET status = 'completed' WHERE id = ?", (goal_id,))
        con.commit()
    return f"Goal #{goal_id} '{row['title']}' marked as completed."


def current_week_milestone() -> str:
    """
    Return the current-week milestone for all active goals.
    Used by the morning briefing to surface what's due this week.
    """
    today     = date.today()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM goals WHERE status = 'active' ORDER BY id"
        ).fetchall()

    if not rows:
        return ""

    snippets = []
    for r in rows:
        created  = date.fromisoformat(r["created_at"])
        days_in  = (today - created).days
        week_num = days_in // 7 + 1
        ml       = json.loads(r["milestones"] or "[]")
        current  = next((m for m in ml if m["week"] == week_num), None)
        if current and not current.get("done"):
            snippets.append(
                f"  • {r['title']} (week {week_num}): {current['text']}"
            )

    if not snippets:
        return ""
    return "This week's goal milestones:\n" + "\n".join(snippets)
