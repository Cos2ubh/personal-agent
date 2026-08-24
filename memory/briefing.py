"""
Morning briefing — composes a concise daily summary from local sources.

Pulls from:
  - Reminders (today, overdue)
  - Gmail unread count (if authenticated, best-effort)
  - Semantic facts (used for the greeting and to look up user_home_city for weather)
  - Optional weather via web_search (only if user_home_city is known)

Never raises — each section is best-effort; a failing source becomes a
"(unavailable)" line so the rest of the briefing still ships.
"""

from datetime import datetime, timezone

from memory.reminders import Reminders, format_due
from memory.semantic import SemanticMemory


def _greeting_line(semantic: SemanticMemory) -> str:
    name = semantic.get("user_name") or semantic.get("user_first_name") or ""
    now = datetime.now().astimezone()
    hour = now.hour
    if hour < 12:
        salutation = "Good morning"
    elif hour < 17:
        salutation = "Good afternoon"
    else:
        salutation = "Good evening"
    who = f", {name}" if name else ""
    date_str = now.strftime("%A, %B %d")
    return f"{salutation}{who}. It's {date_str}."


def _reminder_lines() -> list[str]:
    rem = Reminders()
    now_iso = datetime.now(timezone.utc).isoformat()

    all_active = rem.list_all(include_completed=False)
    if not all_active:
        return ["📌 Reminders: nothing on the list."]

    overdue = [r for r in all_active if r["due_at"] <= now_iso]
    today = [r for r in rem.due_today() if r["due_at"] > now_iso]
    later = [r for r in all_active if r["due_at"] > now_iso and r not in today]

    lines = []
    if overdue:
        lines.append(f"⚠️  Overdue ({len(overdue)}):")
        for r in overdue[:5]:
            lines.append(f"    #{r['id']}  {format_due(r['due_at'])}  —  {r['text']}")

    if today:
        lines.append(f"📌 Due today ({len(today)}):")
        for r in today[:5]:
            lines.append(f"    #{r['id']}  {format_due(r['due_at'])}  —  {r['text']}")

    if not overdue and not today:
        # Nothing pressing today — show what's next
        if later:
            lines.append(f"📌 Next reminder: #{later[0]['id']} {format_due(later[0]['due_at'])} — {later[0]['text']}")
        else:
            lines.append("📌 Reminders: nothing due today.")

    return lines


def _calendar_lines() -> list[str]:
    """Best-effort today's calendar. Silent if Calendar isn't set up."""
    try:
        from tools.calendar import list_today
    except Exception:
        return []

    result = list_today()
    if result.startswith("Error:"):
        return []
    if result.startswith("Today's calendar is clear"):
        return ["📅 Calendar: nothing scheduled today"]

    # list_today already returns a nicely formatted multi-line block
    # Convert leading "Today (...) — N event(s):" into our emoji-prefixed version
    lines = result.split("\n")
    header = lines[0]
    body_lines = lines[1:]
    return [f"📅 {header}"] + body_lines


def _email_lines() -> list[str]:
    """Best-effort unread email count. Silent if Gmail isn't set up."""
    try:
        from tools.gmail import get_service, GmailAuthError, is_authenticated
    except Exception:
        return []

    if not is_authenticated():
        return []

    try:
        svc = get_service()
        # Fast query — just count, no content
        resp = svc.users().messages().list(
            userId="me", q="is:unread in:inbox", maxResults=100
        ).execute()
        count = len(resp.get("messages", []))
        # If we hit maxResults, tell the truth about the "100+" case
        est = resp.get("resultSizeEstimate", count)
        if est > count:
            return [f"📧 Inbox: {est}+ unread messages"]
        if count == 0:
            return ["📧 Inbox: no unread messages"]
        return [f"📧 Inbox: {count} unread"]
    except Exception:
        return []


def _weather_lines(semantic: SemanticMemory) -> list[str]:
    """Best-effort weather lookup for user_home_city via Tavily. Silent if either missing."""
    city = semantic.get("user_home_city") or semantic.get("user_city")
    if not city:
        return []

    try:
        from tools.web import search as web_search
    except Exception:
        return []

    result = web_search(f"weather in {city} today", max_results=1)
    if result.startswith("Error:") or "not set" in result[:80]:
        return []  # silent when Tavily isn't configured

    # Result is verbose — extract the first snippet block
    # Format is: "Search results for: X\n\n[1] Title\n    URL\n    <external_content ...>\n    snippet\n    </external_content>"
    lines = result.split("\n")
    snippet_lines = []
    in_content = False
    for line in lines:
        if "<external_content" in line:
            in_content = True
            continue
        if "</external_content>" in line:
            break
        if in_content and line.strip():
            snippet_lines.append(line.strip())
            if len(snippet_lines) >= 2:  # keep briefing tight
                break

    if not snippet_lines:
        return []
    return [f"🌤  Weather in {city}: {' '.join(snippet_lines)[:200]}"]


def compose(semantic: SemanticMemory) -> str:
    """
    Compose the morning briefing as a single formatted string.
    Sections are best-effort — a missing source just shrinks the output.
    """
    parts = [_greeting_line(semantic), ""]

    parts.extend(_reminder_lines())

    cal = _calendar_lines()
    if cal:
        parts.extend(cal)

    email = _email_lines()
    if email:
        parts.extend(email)

    weather = _weather_lines(semantic)
    if weather:
        parts.extend(weather)

    return "\n".join(parts)
