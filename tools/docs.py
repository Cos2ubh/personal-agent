"""
Google Docs integration — read text, create new docs, append content.

Reuses the OAuth flow from tools/gmail.py. Uses the Drive API to find docs
by name and the Docs API for read/write. The Docs API is quirkier than
Sheets (batch requests with position markers), so the surface is smaller:
we ship read + create + append. Rich formatting is deliberately out of
scope for v0 — the LLM works best with plain text anyway.
"""

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from tools.gmail import _load_credentials, GmailAuthError

MAX_FIND_RESULTS = 20
MAX_READ_CHARS = 50_000       # cap doc read size — huge docs would blow the context


def _get_docs_service():
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GmailAuthError(
            "Not authenticated with Google. Run /gmail-auth to sign in "
            "(you may need to re-run /gmail-auth to consent to Docs scopes)."
        )
    return build("docs", "v1", credentials=creds, cache_discovery=False)


def _get_drive_service():
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GmailAuthError(
            "Not authenticated with Google. Run /gmail-auth to sign in."
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def find(query: str, n: int = 5) -> str:
    """Search for Google Docs by name via Drive metadata."""
    if not query or not query.strip():
        return "Error: query is empty."

    n = max(1, min(n, MAX_FIND_RESULTS))

    try:
        svc = _get_drive_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    safe_query = query.replace("'", "\\'")

    try:
        resp = svc.files().list(
            q=(
                f"mimeType='application/vnd.google-apps.document' "
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
        return f"No documents matching '{query}'."

    lines = [f"Found {len(files)} document(s) matching '{query}':"]
    for f in files:
        lines.append(f"  {f['name']}")
        lines.append(f"    id:       {f['id']}")
        lines.append(f"    modified: {f.get('modifiedTime', '?')}")
        lines.append(f"    link:     {f.get('webViewLink', '')}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _extract_text(document: dict) -> str:
    """Walk the Docs document structure and pull out readable text."""
    body = document.get("body", {})
    content = body.get("content", [])
    parts = []

    for element in content:
        # Paragraphs
        if "paragraph" in element:
            para = element["paragraph"]
            para_text = ""
            for el in para.get("elements", []):
                if "textRun" in el:
                    para_text += el["textRun"].get("content", "")
            if para_text.strip():
                parts.append(para_text.rstrip("\n"))

        # Tables — flatten each row as pipe-separated cells
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                cell_texts = []
                for cell in row.get("tableCells", []):
                    cell_text = ""
                    for cell_el in cell.get("content", []):
                        if "paragraph" in cell_el:
                            for el in cell_el["paragraph"].get("elements", []):
                                if "textRun" in el:
                                    cell_text += el["textRun"].get("content", "")
                    cell_texts.append(cell_text.strip())
                if any(cell_texts):
                    parts.append(" | ".join(cell_texts))

    return "\n".join(parts).strip()


def read(doc_id: str) -> str:
    """Return the plain-text content of a Google Doc, wrapped as external_content."""
    if not doc_id:
        return "Error: doc_id is required."

    try:
        svc = _get_docs_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        document = svc.documents().get(documentId=doc_id).execute()
    except HttpError as e:
        return f"Error: docs read failed — {e}"

    title = document.get("title", "(untitled)")
    text = _extract_text(document)

    if not text:
        text = "(document is empty or contains no readable text)"
    elif len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n\n... [truncated at {MAX_READ_CHARS:,} chars]"

    return (
        f"<external_content source=\"docs:{doc_id}\">\n"
        f"Title: {title}\n"
        f"\n"
        f"{text}\n"
        f"</external_content>"
    )


def create(title: str, content: str = "") -> str:
    """Create a new Google Doc, optionally seeding with initial content."""
    title = (title or "").strip()
    if not title:
        return "Error: title is required."

    try:
        svc = _get_docs_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    try:
        doc = svc.documents().create(body={"title": title}).execute()
    except HttpError as e:
        return f"Error: docs create failed — {e}"

    doc_id = doc.get("documentId")

    if content:
        # Insert initial content at position 1 (start of body — position 0 is reserved)
        try:
            svc.documents().batchUpdate(
                documentId=doc_id,
                body={
                    "requests": [{
                        "insertText": {
                            "location": {"index": 1},
                            "text": content,
                        }
                    }]
                },
            ).execute()
        except HttpError as e:
            return (
                f"Document created ({doc_id}) but initial content insert failed: {e}. "
                f"The doc exists but is empty."
            )

    web_link = f"https://docs.google.com/document/d/{doc_id}/edit"
    return (
        f"Document created: {title}\n"
        f"  id:   {doc_id}\n"
        f"  link: {web_link}"
    )


def append(doc_id: str, text: str) -> str:
    """Append plain text at the end of an existing Google Doc."""
    if not doc_id:
        return "Error: doc_id is required."
    if not text:
        return "Error: text is required."

    try:
        svc = _get_docs_service()
    except GmailAuthError as e:
        return f"Error: {e}"

    # Docs API uses endOfSegmentLocation to point at the document tail.
    # Prepend a newline so appended content doesn't collide with existing trailing text.
    try:
        svc.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [{
                    "insertText": {
                        "endOfSegmentLocation": {},
                        "text": "\n" + text,
                    }
                }]
            },
        ).execute()
    except HttpError as e:
        return f"Error: docs append failed — {e}"

    return f"Appended {len(text)} chars to document {doc_id}."
