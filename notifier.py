"""
Reminder notifier — polls the reminders DB and fires Windows toast notifications
for anything newly due.

Designed to be invoked by Windows Task Scheduler on a short interval (e.g. every
5 minutes). Each invocation:
  - Queries Reminders.due_now() (past due + not yet notified)
  - Shows a Windows toast for each
  - Marks each as notified so the next invocation doesn't repeat

Run manually to test:
    .\\venv\\Scripts\\python.exe notifier.py

To wire up automatic background firing, see docs/notifier_setup.md.
Exits with code 0 on success (even if nothing was due), non-zero on hard errors.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so we can import memory.reminders
_PROJ_ROOT = Path(__file__).parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from memory.reminders import Reminders, format_due  # noqa: E402


def _toast(title: str, body: str) -> bool:
    """Show a Windows toast. Returns True on success."""
    try:
        from winotify import Notification, audio
    except ImportError:
        print("[notifier] winotify not installed — falling back to console print", file=sys.stderr)
        print(f"[REMINDER] {title}: {body}")
        return False

    try:
        n = Notification(
            app_id="Personal Agent",
            title=title,
            msg=body,
            duration="long",
        )
        n.set_audio(audio.Default, loop=False)
        n.show()
        return True
    except Exception as e:
        print(f"[notifier] toast failed: {e}", file=sys.stderr)
        print(f"[REMINDER] {title}: {body}")
        return False


def main() -> int:
    try:
        rem = Reminders()
    except Exception as e:
        print(f"[notifier] could not open reminders DB: {e}", file=sys.stderr)
        return 2

    due = rem.due_now()
    if not due:
        return 0

    fired = 0
    for r in due:
        title = f"Reminder #{r['id']}"
        body = f"{r['text']}\n(due {format_due(r['due_at'])})"
        _toast(title, body)
        rem.mark_notified(r["id"])
        fired += 1

    print(f"[notifier] fired {fired} reminder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
