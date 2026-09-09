"""
Sentinel — action approval and risk classification layer.

Every outbound or mutating action flows through Sentinel before execution.
Sentinel answers three questions the user needs to make an informed decision:

  1. WHAT is about to happen (the action preview)
  2. WHY it matters (the risk tier + impact statement)
  3. HOW to stop it (deny path)

Risk tiers
──────────
  LOW    — reversible locally (write a file, create a draft)
  MEDIUM — observable externally, hard to undo (calendar invite, sheets row)
  HIGH   — leaves the machine permanently / costs money (send email, open browser for booking)

The Sentinel does NOT replace the existing approval gate in app.py — it
enriches it. The gate in app.py decides whether to PAUSE; Sentinel decides
what to SHOW during that pause.

Usage (from app.py):
    from tools.sentinel import classify, impact_statement

    risk, impact = classify(tc_name, tc_args)
    # render risk badge + impact in the approval card
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── Risk tier ────────────────────────────────────────────────────────────

LOW    = "LOW"
MEDIUM = "MEDIUM"
HIGH   = "HIGH"


@dataclass
class SentinelVerdict:
    risk: str           # LOW / MEDIUM / HIGH
    impact: str         # one-line human-readable impact statement
    reversible: bool    # can this be undone?
    scope: str          # "local" / "google" / "web" / "external"


# ── Classification rules ─────────────────────────────────────────────────

def classify(tool_name: str, args: dict) -> SentinelVerdict:
    """
    Return a SentinelVerdict for a tool call that is about to be executed.
    Called before the approval prompt is rendered.
    """
    n = tool_name
    a = args or {}

    # ── File system ──────────────────────────────────────────────────────
    if n == "write_file":
        path = a.get("path", "")
        exists = Path(path).exists() if path else False
        if exists:
            return SentinelVerdict(
                risk=MEDIUM,
                impact=f"Overwrite existing file: {Path(path).name}",
                reversible=False,
                scope="local",
            )
        return SentinelVerdict(
            risk=LOW,
            impact=f"Create new file: {Path(path).name}",
            reversible=True,
            scope="local",
        )

    # ── Gmail ────────────────────────────────────────────────────────────
    if n == "gmail_draft_new":
        to = a.get("to", "?")
        subject = a.get("subject", "?")
        return SentinelVerdict(
            risk=LOW,
            impact=f"Save draft to {to}: '{subject}' — sits in Drafts, not sent",
            reversible=True,
            scope="google",
        )

    if n == "gmail_draft_reply":
        return SentinelVerdict(
            risk=LOW,
            impact="Save reply draft — sits in Drafts, not sent",
            reversible=True,
            scope="google",
        )

    if n == "gmail_send_draft":
        return SentinelVerdict(
            risk=HIGH,
            impact="Send email — leaves your machine, cannot be recalled",
            reversible=False,
            scope="external",
        )

    # ── Calendar ─────────────────────────────────────────────────────────
    if n == "calendar_create_event":
        summary = a.get("summary", "?")
        attendees = a.get("attendees", "")
        if attendees:
            return SentinelVerdict(
                risk=MEDIUM,
                impact=f"Create event '{summary}' and send invites to: {attendees}",
                reversible=True,
                scope="google",
            )
        return SentinelVerdict(
            risk=LOW,
            impact=f"Create calendar event: '{summary}' (no attendees)",
            reversible=True,
            scope="google",
        )

    # ── Sheets ───────────────────────────────────────────────────────────
    if n == "sheets_append":
        return SentinelVerdict(
            risk=LOW,
            impact="Append rows to Google Sheet — existing data untouched",
            reversible=True,
            scope="google",
        )

    if n == "sheets_update":
        return SentinelVerdict(
            risk=MEDIUM,
            impact="Overwrite cells in Google Sheet — existing values replaced",
            reversible=False,
            scope="google",
        )

    if n == "sheets_create":
        title = a.get("title", "?")
        return SentinelVerdict(
            risk=LOW,
            impact=f"Create new spreadsheet: '{title}'",
            reversible=True,
            scope="google",
        )

    # ── Docs ─────────────────────────────────────────────────────────────
    if n == "docs_create":
        title = a.get("title", "?")
        return SentinelVerdict(
            risk=LOW,
            impact=f"Create new Google Doc: '{title}'",
            reversible=True,
            scope="google",
        )

    if n == "docs_append":
        return SentinelVerdict(
            risk=LOW,
            impact="Append text to existing Google Doc",
            reversible=True,
            scope="google",
        )

    # ── Web / browser ────────────────────────────────────────────────────
    if n == "open_url":
        from urllib.parse import urlparse
        domain = urlparse(a.get("url", "")).netloc or "?"
        return SentinelVerdict(
            risk=LOW,
            impact=f"Open {domain} in your default browser",
            reversible=True,
            scope="web",
        )

    if n == "browser_open":
        from urllib.parse import urlparse
        domain = urlparse(a.get("url", "")).netloc or "?"
        return SentinelVerdict(
            risk=HIGH,
            impact=f"Open {domain} in managed browser — agent will fill forms autonomously until payment",
            reversible=False,
            scope="web",
        )

    if n == "search_irctc_train":
        frm = a.get("from_station", "?")
        to  = a.get("to_station", "?")
        dt  = a.get("journey_date", "?")
        return SentinelVerdict(
            risk=HIGH,
            impact=f"Automate IRCTC search: {frm} → {to} on {dt} — agent will type in the browser",
            reversible=True,
            scope="web",
        )

    # ── Vault ────────────────────────────────────────────────────────────
    if n == "vault_store_secret":
        name = a.get("name", "?")
        return SentinelVerdict(
            risk=LOW,
            impact=f"Encrypt and store secret '{name}' in local vault",
            reversible=True,
            scope="local",
        )

    if n == "vault_delete_secret":
        name = a.get("name", "?")
        return SentinelVerdict(
            risk=MEDIUM,
            impact=f"Permanently delete secret '{name}' from vault — cannot be recovered",
            reversible=False,
            scope="local",
        )

    # Fallback
    return SentinelVerdict(
        risk=LOW,
        impact=f"Execute {tool_name}",
        reversible=True,
        scope="local",
    )


# ── Formatting helpers (used by app.py approval card) ────────────────────

_RISK_BADGE = {
    LOW:    "🟢 LOW RISK",
    MEDIUM: "🟡 MEDIUM RISK",
    HIGH:   "🔴 HIGH RISK",
}

_SCOPE_LABEL = {
    "local":    "Local only",
    "google":   "Google account",
    "web":      "Opens browser / internet",
    "external": "Leaves your machine",
}


def format_verdict(verdict: SentinelVerdict) -> str:
    """Return a short markdown-ready string for rendering in the approval card."""
    badge     = _RISK_BADGE.get(verdict.risk, verdict.risk)
    scope     = _SCOPE_LABEL.get(verdict.scope, verdict.scope)
    rev_label = "Reversible" if verdict.reversible else "**Not reversible**"
    return f"{badge} · {scope} · {rev_label}\n> {verdict.impact}"
