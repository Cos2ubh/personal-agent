"""
Google Sheets integration — read/write cells, create new spreadsheets.

Reuses the OAuth flow from tools/gmail.py. Uses the Drive API (metadata
scope) to find sheets by name, and the Sheets API for cell operations.

Because we added new scopes in gmail.py's SCOPES list, the existing token
is missing sheets + drive.metadata.readonly. First sheets call after this
change will fail auth — re-run /gmail-auth to re-consent.
"""

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from tools.gmail import _load_credentials, GmailAuthError

MAX_ROWS_SHOWN = 500        # cap rows returned per read to keep context sane
MAX_FIND_RESULTS = 20


def _get_sheets_service():
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GmailAuthError(
            "Not authenticated with Google. Run /gmail-auth to sign in "
            "(you may also need to re-run /gmail-auth to consent to the new "
            "Sheets scopes)."
        )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_drive_service():
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GmailAuthError(
            "Not authenticated with Google. Run /gmail-auth to sign in."
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def find(query: str, n: int = 5) -> str:
    """
    Search for spreadsheets by name via Drive metadata.
    Returns matches with ID, name, modified time, and web link.
    """
    if not query or not query.strip():
        return "Error: query is empty."

    n = max(1, min(n, MAX_FIND_RESULTS))

    try:
        svc = _get_drive_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    # Escape single quotes in the query since we're building a Drive q= string
    safe_query = query.replace("'", "\\'")

    try:
        resp = svc.files().list(
            q=(
                f"mimeType='application/vnd.google-apps.spreadsheet' "
                f"and name contains '{safe_query}' "
                f"and trashed=false"
            ),
            pageSize=n,
            fields="files(id, name, modifiedTime, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()
    except HttpError as e:
        return f"Error: Drive search failed — {e}"

    files = resp.get("files", [])
    if not files:
        return f"No spreadsheets matching '{query}'."

    lines = [f"Found {len(files)} spreadsheet(s) matching '{query}':"]
    for f in files:
        lines.append(f"  {f['name']}")
        lines.append(f"    id:       {f['id']}")
        lines.append(f"    modified: {f.get('modifiedTime', '?')}")
        lines.append(f"    link:     {f.get('webViewLink', '')}")
        lines.append("")
    return "\n".join(lines).rstrip()


def read(spreadsheet_id: str, range_a1: str = "") -> str:
    """
    Read cells from a spreadsheet. If range is omitted, reads the first
    sheet's used range (up to Z1000). Result is wrapped in external_content
    since spreadsheet contents come from files that could contain injected
    text (imported CSVs, formula-generated content, etc.)
    """
    if not spreadsheet_id:
        return "Error: spreadsheet_id is required."

    try:
        svc = _get_sheets_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    range_to_use = range_a1.strip() if range_a1 else "A1:Z1000"

    try:
        resp = svc.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_to_use,
        ).execute()
    except HttpError as e:
        return f"Error: sheets read failed — {e}"

    values = resp.get("values", [])
    if not values:
        return f"(no data in range '{range_to_use}')"

    # Truncate if too many rows
    truncated = ""
    if len(values) > MAX_ROWS_SHOWN:
        truncated = f"\n... [truncated at {MAX_ROWS_SHOWN} rows of {len(values)}]"
        values = values[:MAX_ROWS_SHOWN]

    # Compute column widths for alignment
    max_cols = max(len(row) for row in values)
    max_widths = [0] * max_cols
    for row in values:
        for i, cell in enumerate(row):
            max_widths[i] = max(max_widths[i], len(str(cell)))
    max_widths = [min(w, 40) for w in max_widths]  # cap column width

    lines = []
    for row in values:
        padded = []
        for i in range(max_cols):
            cell = str(row[i]) if i < len(row) else ""
            if len(cell) > 40:
                cell = cell[:37] + "..."
            padded.append(cell.ljust(max_widths[i]))
        lines.append(" | ".join(padded))

    body = "\n".join(lines) + truncated
    return (
        f"<external_content source=\"sheets:{spreadsheet_id}:{range_to_use}\">\n"
        f"{body}\n"
        f"</external_content>"
    )


def append(spreadsheet_id: str, range_a1: str, values: list) -> str:
    """Append rows to a spreadsheet."""
    if not spreadsheet_id or not range_a1:
        return "Error: spreadsheet_id and range are required."
    if not values or not isinstance(values, list):
        return "Error: values must be a non-empty list of rows."

    try:
        svc = _get_sheets_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    # Normalize: allow a list of scalars as a single row
    if values and not isinstance(values[0], (list, tuple)):
        values = [values]

    try:
        result = svc.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_a1,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
    except HttpError as e:
        return f"Error: sheets append failed — {e}"

    updates = result.get("updates", {})
    rows_added = updates.get("updatedRows", len(values))
    return (
        f"Appended {rows_added} row(s) to {spreadsheet_id}. "
        f"Updated range: {updates.get('updatedRange', range_a1)}."
    )


def update(spreadsheet_id: str, range_a1: str, values: list) -> str:
    """Overwrite cells at the given range."""
    if not spreadsheet_id or not range_a1:
        return "Error: spreadsheet_id and range are required."
    if not values or not isinstance(values, list):
        return "Error: values must be a non-empty list of rows."

    try:
        svc = _get_sheets_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    if values and not isinstance(values[0], (list, tuple)):
        values = [values]

    try:
        result = svc.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_a1,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
    except HttpError as e:
        return f"Error: sheets update failed — {e}"

    return (
        f"Updated {result.get('updatedCells', 0)} cells in {spreadsheet_id} "
        f"(range: {result.get('updatedRange', range_a1)})."
    )


def create(title: str) -> str:
    """Create a new spreadsheet. Returns the new ID + link."""
    title = (title or "").strip()
    if not title:
        return "Error: title is required."

    try:
        svc = _get_sheets_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        created = svc.spreadsheets().create(
            body={"properties": {"title": title}},
            fields="spreadsheetId,spreadsheetUrl",
        ).execute()
    except HttpError as e:
        return f"Error: sheets create failed — {e}"

    return (
        f"Spreadsheet created: {title}\n"
        f"  id:   {created.get('spreadsheetId')}\n"
        f"  link: {created.get('spreadsheetUrl', '')}"
    )
