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

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path so we can import memory.reminders
_PROJ_ROOT = Path(__file__).parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from dotenv import load_dotenv
load_dotenv()

from memory.reminders import Reminders, format_due  # noqa: E402

_API_URL      = os.getenv("AGENT_API_URL", "http://localhost:8502").rstrip("/")
_OWNER_NUMBER = os.getenv("OWNER_NUMBER", "")
_BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "8"))   # send briefing at this hour


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


def _push_briefing_to_whatsapp() -> bool:
    """
    If the API server is running and OWNER_NUMBER is set, queue a morning
    briefing in the outbox so the WhatsApp bridge picks it up.
    Only fires once — at the configured BRIEFING_HOUR (default 8am).
    """
    if not _OWNER_NUMBER:
        return False
    now_hour = datetime.now(timezone.utc).astimezone().hour
    if now_hour != _BRIEFING_HOUR:
        return False

    try:
        import httpx
        resp = httpx.post(
            f"{_API_URL}/briefing/wa:{_OWNER_NUMBER}@c.us",
            timeout=10,
        )
        if resp.status_code == 200:
            print("[notifier] morning briefing queued for WhatsApp")
            return True
    except Exception as e:
        print(f"[notifier] WhatsApp push failed (server may not be running): {e}", file=sys.stderr)
    return False


def _push_reminder_to_whatsapp(title: str, body: str) -> bool:
    """Queue a reminder notification to the WhatsApp outbox."""
    if not _OWNER_NUMBER:
        return False
    try:
        import httpx
        sid = f"wa:{_OWNER_NUMBER}@c.us"
        httpx.post(
            f"{_API_URL}/push",
            json={"session_id": sid, "message": f"⏰ {title}\n{body}"},
            timeout=5,
        )
        return True
    except Exception:
        return False


def main() -> int:
    try:
        rem = Reminders()
    except Exception as e:
        print(f"[notifier] could not open reminders DB: {e}", file=sys.stderr)
        return 2

    # Morning briefing push (fires at configured hour)
    _push_briefing_to_whatsapp()

    due = rem.due_now()
    if not due:
        return 0

    fired = 0
    for r in due:
        title = f"Reminder #{r['id']}"
        body  = f"{r['text']}\n(due {format_due(r['due_at'])})"
        _toast(title, body)
        _push_reminder_to_whatsapp(title, body)
        rem.mark_notified(r["id"])
        fired += 1

    print(f"[notifier] fired {fired} reminder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
