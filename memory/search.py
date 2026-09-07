"""
Unified smart search — intent classification + cross-index routing.

classify_query() reads the query and decides which index to hit:
  "image"  → only search image_index (CLIP embeddings)
  "doc"    → only search doc_index (text chunks)
  "both"   → search both, merge results

search_unified() is the main entry point used by the smart_search tool.
It classifies first, then calls the right index(es) — so a query about
photographs never touches the document collection and vice versa.
"""

from memory.doc_index import search_documents, format_search_results as _fmt_doc
from memory.image_index import search_images, format_search_results as _fmt_img

# Keywords that strongly signal the user wants an image / photo
_IMAGE_SIGNALS = frozenset({
    "photo", "photos", "picture", "pictures", "image", "images",
    "screenshot", "screenshots", "selfie", "selfies", "photograph",
    "photographs", "gallery", "jpg", "jpeg", "png", "snap", "shot",
    "pic", "pics", "wallpaper", "thumbnail", "scan", "scanned",
})

# Keywords that strongly signal the user wants a text document
_DOC_SIGNALS = frozenset({
    "pdf", "document", "documents", "resume", "cv", "marksheet", "transcript",
    "certificate", "report", "invoice", "receipt", "form", "letter", "contract",
    "docx", "doc", "file", "files", "sheet", "spreadsheet", "record", "records",
    "statement", "application", "admit", "card", "hall", "ticket", "policy",
    "agreement", "deed", "memo", "note", "notes", "text",
})


def classify_query(query: str) -> str:
    """
    Return "image" | "doc" | "both" based on intent signals in the query.

    Strategy: word-level intersection with signal sets. If a query has
    image signals but no doc signals → image only. If doc signals but no
    image signals → doc only. Mixed or neither → both.
    """
    words = frozenset(query.lower().split())
    has_image = bool(words & _IMAGE_SIGNALS)
    has_doc   = bool(words & _DOC_SIGNALS)

    if has_image and not has_doc:
        return "image"
    if has_doc and not has_image:
        return "doc"
    return "both"


def search_unified(query: str, n: int = 5) -> dict:
    """
    Route the query to the right index(es) and return a combined result dict:

      {
        "intent":  "image" | "doc" | "both",
        "docs":    [...],   # list[dict] from search_documents — empty if intent=="image"
        "images":  [...],   # list[dict] from search_images   — empty if intent=="doc"
      }
    """
    intent = classify_query(query)

    docs   = search_documents(query, n) if intent in ("doc",   "both") else []
    images = search_images(query, n)    if intent in ("image", "both") else []

    return {"intent": intent, "docs": docs, "images": images}


def format_unified_results(query: str, result: dict) -> str:
    """
    Human/LLM-friendly string for the output of search_unified().
    Sections are only rendered when they have hits.
    """
    intent  = result.get("intent", "both")
    docs    = result.get("docs", [])
    images  = result.get("images", [])

    parts = []

    if intent == "image" and not images:
        parts.append(
            f"No image matches for '{query}'. "
            "Run /index-images if you haven't indexed yet."
        )
    elif intent == "doc" and not docs:
        parts.append(
            f"No document matches for '{query}'. "
            "Run /index-docs if you haven't indexed yet."
        )
    elif intent == "both" and not docs and not images:
        parts.append(
            f"No matches for '{query}' in documents or images. "
            "Run /index-docs and /index-images if you haven't indexed yet."
        )

    if docs:
        parts.append(_fmt_doc(query, docs))

    if images:
        parts.append(_fmt_img(query, images))

    if intent != "both":
        skipped = "documents" if intent == "image" else "images"
        parts.append(f"(Skipped {skipped} index — query looks like a {intent} request.)")

    return "\n\n".join(parts)
