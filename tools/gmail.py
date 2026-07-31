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
