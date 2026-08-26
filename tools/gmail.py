"""
Gmail integration — OAuth2 auth + read/search/draft tools.

Setup (one-time, ~5 min):
  1. Create a Google Cloud project at https://console.cloud.google.com/
  2. Enable the Gmail API for the project
  3. OAuth consent screen: pick "External", add yourself as a test user
  4. Credentials → Create Credentials → OAuth client ID → Application type: Desktop
  5. Download the JSON, save it as  data/credentials.json  in this project
  6. In chat, run:  /gmail-auth
  7. Browser opens, consent to the requested scopes → token is saved locally

The saved token auto-refreshes; you only re-authenticate if you revoke access
or delete data/token_gmail.json.

Scopes requested:
  gmail.readonly — read + search inbox
  gmail.compose  — create drafts (NOT send — sending requires gmail.send,
                   which we'll add in a later session with an approval gate)
"""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Scopes are baked in — changing them requires deleting token_gmail.json and
# re-authenticating so the user re-consents to the new scope set.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

_DATA_DIR = Path(__file__).parent.parent / "data"
CREDENTIALS_PATH = _DATA_DIR / "credentials.json"
TOKEN_PATH = _DATA_DIR / "token_gmail.json"


class GmailAuthError(Exception):
    """Raised when we can't obtain a valid Gmail service (missing setup, denied consent, etc.)."""


def _load_credentials() -> Credentials | None:
    """Load saved token if present, refresh if expired. Returns None if no token yet."""
    if not TOKEN_PATH.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            raise GmailAuthError(f"Token refresh failed — you may need to re-run /gmail-auth: {e}")
    return creds


def run_oauth_flow() -> Credentials:
    """
    Run the interactive OAuth flow. Opens a browser, waits for consent,
    saves the resulting token. Called by /gmail-auth.
    """
    if not CREDENTIALS_PATH.exists():
        raise GmailAuthError(
            f"credentials.json not found at {CREDENTIALS_PATH}. "
            "Follow the setup steps at the top of tools/gmail.py to create it."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)  # port=0 lets the OS pick a free port
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_service():
    """
    Return an authenticated Gmail API service object.
    Raises GmailAuthError if not authenticated — user needs to run /gmail-auth.
    """
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GmailAuthError(
            "Not authenticated with Gmail. Run /gmail-auth to sign in "
            "(needs data/credentials.json from your Google Cloud project)."
        )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def is_authenticated() -> bool:
    """True if we have a valid or refreshable token for Gmail."""
    try:
        creds = _load_credentials()
        return bool(creds and (creds.valid or (creds.expired and creds.refresh_token)))
    except GmailAuthError:
        return False


# ── Read tools ────────────────────────────────────────────────────────────

import base64
from email import message_from_bytes
from email.policy import default as email_default_policy

MAX_EMAIL_BODY_CHARS = 20_000   # per-email cap in returned text
MAX_LIST_RESULTS = 50


def _header(headers: list[dict], name: str) -> str:
    """Case-insensitive header lookup from Gmail's headers list."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _decode_body(msg_part: dict) -> str:
    """Recursively find and decode the plain-text body of a Gmail message part."""
    mime = msg_part.get("mimeType", "")
    body = msg_part.get("body", {})

    # Direct plain-text body
    if mime == "text/plain" and body.get("data"):
        raw = base64.urlsafe_b64decode(body["data"] + "===")
        return raw.decode("utf-8", errors="replace")

    # Multipart — recurse into parts
    for part in msg_part.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text

    # Fallback: HTML body if no plain-text found
    if mime == "text/html" and body.get("data"):
        raw = base64.urlsafe_b64decode(body["data"] + "===")
        # Strip tags crudely — for LLM consumption we want plain text
        import re
        text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))
        text = re.sub(r"\s+", " ", text).strip()
        return text

    return ""


def list_recent(n: int = 10) -> str:
    """Return a formatted list of the N most recent inbox messages."""
    n = max(1, min(n, MAX_LIST_RESULTS))
    try:
        svc = get_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        resp = svc.users().messages().list(userId="me", maxResults=n, labelIds=["INBOX"]).execute()
    except HttpError as e:
        return f"Error: Gmail list request failed — {e}"

    messages = resp.get("messages", [])
    if not messages:
        return "Inbox is empty (or no messages match the filter)."

    lines = [f"Most recent {len(messages)} inbox messages:\n"]
    for i, m in enumerate(messages, 1):
        try:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
        except HttpError as e:
            lines.append(f"[{i}] (failed to fetch metadata: {e})")
            continue

        headers = full.get("payload", {}).get("headers", [])
        frm = _header(headers, "From")
        subj = _header(headers, "Subject") or "(no subject)"
        date = _header(headers, "Date")
        snippet = full.get("snippet", "").strip()
        is_unread = "UNREAD" in full.get("labelIds", [])
        marker = "●" if is_unread else " "

        lines.append(f"[{i}] {marker} id={m['id']}")
        lines.append(f"    From:    {frm}")
        lines.append(f"    Subject: {subj}")
        lines.append(f"    Date:    {date}")
        if snippet:
            lines.append(f"    Snippet: {snippet[:200]}")
        lines.append("")

    return "\n".join(lines).rstrip()


def read_email(email_id: str) -> str:
    """Return the full body + headers of one email, wrapped as external content."""
    if not email_id:
        return "Error: email_id is empty."

    try:
        svc = get_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        msg = svc.users().messages().get(userId="me", id=email_id, format="full").execute()
    except HttpError as e:
        return f"Error: Gmail get request failed — {e}"

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    frm = _header(headers, "From")
    to = _header(headers, "To")
    subj = _header(headers, "Subject") or "(no subject)"
    date = _header(headers, "Date")
    body = _decode_body(payload) or "(no readable body)"

    if len(body) > MAX_EMAIL_BODY_CHARS:
        body = body[:MAX_EMAIL_BODY_CHARS] + f"\n\n... [truncated at {MAX_EMAIL_BODY_CHARS:,} chars]"

    return (
        f'<external_content source="gmail:{email_id}">\n'
        f"From:    {frm}\n"
        f"To:      {to}\n"
        f"Subject: {subj}\n"
        f"Date:    {date}\n"
        f"\n"
        f"{body}\n"
        f"</external_content>"
    )


def search(query: str, n: int = 10) -> str:
    """
    Search Gmail using Gmail's native query language.
    Examples: 'from:mom', 'is:unread subject:invoice', 'has:attachment after:2026/01/01'.
    """
    if not query:
        return "Error: query is empty."
    n = max(1, min(n, MAX_LIST_RESULTS))

    try:
        svc = get_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        resp = svc.users().messages().list(userId="me", q=query, maxResults=n).execute()
    except HttpError as e:
        return f"Error: Gmail search failed — {e}"

    messages = resp.get("messages", [])
    if not messages:
        return f"No emails match query: '{query}'"

    lines = [f"Search results for '{query}' — {len(messages)} match(es):\n"]
    for i, m in enumerate(messages, 1):
        try:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
        except HttpError as e:
            lines.append(f"[{i}] (failed to fetch metadata: {e})")
            continue

        headers = full.get("payload", {}).get("headers", [])
        frm = _header(headers, "From")
        subj = _header(headers, "Subject") or "(no subject)"
        date = _header(headers, "Date")
        snippet = full.get("snippet", "").strip()

        lines.append(f"[{i}] id={m['id']}")
        lines.append(f"    From:    {frm}")
        lines.append(f"    Subject: {subj}")
        lines.append(f"    Date:    {date}")
        if snippet:
            lines.append(f"    Snippet: {snippet[:200]}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ── Draft tools ───────────────────────────────────────────────────────────

from email.message import EmailMessage


def _build_raw_message(to: str, subject: str, body: str,
                       in_reply_to: str = "", references: str = "",
                       cc: str = "") -> str:
    """Assemble an RFC-2822 message and return it base64-url-encoded for Gmail."""
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return raw


def draft_new(to: str, subject: str, body: str, cc: str = "") -> str:
    """Create a new Gmail draft. Does NOT send — draft lives in the Drafts folder."""
    if not to or not subject or not body:
        return "Error: to, subject, and body are all required."

    try:
        svc = get_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    raw = _build_raw_message(to=to, subject=subject, body=body, cc=cc)
    try:
        draft = svc.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()
    except HttpError as e:
        return f"Error: draft create failed — {e}"

    return f"Draft created (id={draft.get('id')}). Review in Gmail's Drafts folder."


def draft_reply(email_id: str, body: str) -> str:
    """Create a Gmail draft reply to an existing message. Preserves threading."""
    if not email_id or not body:
        return "Error: email_id and body are both required."

    try:
        svc = get_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        orig = svc.users().messages().get(
            userId="me", id=email_id, format="metadata",
            metadataHeaders=["From", "Subject", "Message-ID", "References"],
        ).execute()
    except HttpError as e:
        return f"Error: could not fetch original email {email_id} — {e}"

    headers = orig.get("payload", {}).get("headers", [])
    orig_from = _header(headers, "From")
    orig_subject = _header(headers, "Subject")
    orig_msg_id = _header(headers, "Message-ID")
    orig_refs = _header(headers, "References")
    thread_id = orig.get("threadId")

    reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    references = f"{orig_refs} {orig_msg_id}".strip() if orig_refs else orig_msg_id

    raw = _build_raw_message(
        to=orig_from,
        subject=reply_subject,
        body=body,
        in_reply_to=orig_msg_id,
        references=references,
    )
    try:
        draft = svc.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()
    except HttpError as e:
        return f"Error: draft-reply create failed — {e}"

    return (
        f"Draft reply created (id={draft.get('id')}) in thread {thread_id}. "
        f"Review in Gmail's Drafts folder."
    )


def get_draft_preview(draft_id: str) -> dict:
    """
    Fetch a draft's To / Subject / body-snippet for the approval preview.
    Returns a dict with keys: to, subject, body_snippet, error.
    """
    if not draft_id:
        return {"error": "draft_id is empty"}
    try:
        svc = get_service()
    except GmailAuthError as e:
        return {"error": str(e)}

    try:
        draft = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    except HttpError as e:
        return {"error": f"Gmail get-draft failed — {e}"}

    msg = draft.get("message", {})
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    body = _decode_body(payload) or "(no readable body)"
    if len(body) > 500:
        body = body[:500] + "..."

    return {
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject") or "(no subject)",
        "body_snippet": body,
        "error": "",
    }


def send_draft(draft_id: str) -> str:
    """
    Send an existing draft. This is the ONLY send path — you cannot send a
    message without first creating a draft. That way the user always has a
    chance to review the exact bytes that will hit the wire.
    """
    if not draft_id:
        return "Error: draft_id is empty."

    try:
        svc = get_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        result = svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    except HttpError as e:
        return f"Error: send failed — {e}"

    return (
        f"Sent. Gmail message id={result.get('id')}, "
        f"thread={result.get('threadId')}. "
        f"Check your Sent folder."
    )
