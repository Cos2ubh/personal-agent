"""
Google Calendar integration — reads today's/upcoming events and creates new ones.

Reuses the OAuth flow from tools/gmail.py — same Google account, same
credentials.json, same token file (token_gmail.json is a historical name;
it now covers Gmail + Calendar scopes).

Because we changed SCOPES in gmail.py, the existing token is missing the new
calendar scopes. First calendar call after this change will fail auth — the
user needs to re-run /gmail-auth to re-consent with the expanded scope set.
"""

from datetime import datetime, timezone, timedelta

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Reuse auth helpers from gmail — same token, same account, same OAuth flow
from tools.gmail import _load_credentials, GmailAuthError


def get_calendar_service():
    """Return an authenticated Calendar API service. Raises GmailAuthError if not signed in."""
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GmailAuthError(
            "Not authenticated with Google. Run /gmail-auth to sign in "
            "(needs data/credentials.json from your Google Cloud project). "
            "You may need to re-run /gmail-auth to re-consent with the "
            "expanded calendar scopes."
        )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _format_event(ev: dict) -> str:
    """Compact one-line event render."""
    summary = ev.get("summary", "(untitled)")
    start = ev.get("start", {})
    end = ev.get("end", {})

    # Two flavours: 'dateTime' (timed) and 'date' (all-day)
    if "dateTime" in start:
        try:
            s = datetime.fromisoformat(start["dateTime"]).astimezone()
            e = datetime.fromisoformat(end["dateTime"]).astimezone()
            when = f"{s.strftime('%a %b %d, %I:%M %p')} – {e.strftime('%I:%M %p')}"
            when = when.replace(" 0", " ")
        except Exception:
            when = start.get("dateTime", "?")
    else:
        when = f"{start.get('date', '?')} (all day)"

    location = ev.get("location", "").strip()
    attendees = ev.get("attendees", [])
    attendee_count = len(attendees) if attendees else 0

    parts = [f"  {when}  —  {summary}"]
    if location:
        parts.append(f"      📍 {location}")
    if attendee_count > 1:
        parts.append(f"      👥 {attendee_count} attendees")
    return "\n".join(parts)


def list_today() -> str:
    """List calendar events happening today (local timezone)."""
    try:
        svc = get_calendar_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    now = datetime.now().astimezone()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    try:
        events = svc.events().list(
            calendarId="primary",
            timeMin=start_of_day.astimezone(timezone.utc).isoformat(),
            timeMax=end_of_day.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute().get("items", [])
    except HttpError as e:
        return f"Error: Calendar list failed — {e}"

    if not events:
        return "Today's calendar is clear."

    lines = [f"Today ({now.strftime('%A %b %d')}) — {len(events)} event(s):"]
    for ev in events:
        lines.append(_format_event(ev))
    return "\n".join(lines)


def list_upcoming(days: int = 7) -> str:
    """List calendar events in the next N days (default 7)."""
    days = max(1, min(days, 30))

    try:
        svc = get_calendar_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    now = datetime.now().astimezone()
    end = now + timedelta(days=days)

    try:
        events = svc.events().list(
            calendarId="primary",
            timeMin=now.astimezone(timezone.utc).isoformat(),
            timeMax=end.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        ).execute().get("items", [])
    except HttpError as e:
        return f"Error: Calendar list failed — {e}"

    if not events:
        return f"No events in the next {days} day(s)."

    lines = [f"Next {days} day(s) — {len(events)} event(s):"]
    for ev in events:
        lines.append(_format_event(ev))
    return "\n".join(lines)


def create_event(summary: str, start: str, end: str = "",
                 description: str = "", location: str = "",
                 attendees: str = "") -> str:
    """
    Create a calendar event.

    start / end: ISO 8601 or natural-language time. Parsed with dateparser.
                 If end is omitted, event is 1 hour long by default.
    attendees:   comma-separated email addresses (invites will be sent)
    """
    if not summary or not start:
        return "Error: summary and start are required."

    from memory.reminders import parse_time
    start_iso = parse_time(start)
    if not start_iso:
        return f"Error: could not parse start time '{start}'."

    if end:
        end_iso = parse_time(end)
        if not end_iso:
            return f"Error: could not parse end time '{end}'."
    else:
        # Default: 1-hour event
        start_dt = datetime.fromisoformat(start_iso)
        end_iso = (start_dt + timedelta(hours=1)).isoformat()

    try:
        svc = get_calendar_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    body = {
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        attendee_list = [a.strip() for a in attendees.split(",") if a.strip()]
        body["attendees"] = [{"email": a} for a in attendee_list]

    try:
        created = svc.events().insert(calendarId="primary", body=body).execute()
    except HttpError as e:
        return f"Error: Calendar create failed — {e}"

    link = created.get("htmlLink", "")
    return (
        f"Event created: {created.get('summary')} at {start_iso}.\n"
        f"View: {link}"
    )
