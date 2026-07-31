"""
Document content index — semantic search over the user's documents.

Walks every doc in the read scope, extracts text, chunks it, embeds each
chunk into a ChromaDB collection. Once indexed, natural-language queries
like "find my marksheet" work even when the file is named IMG_20230415_10x.pdf.

The index is incremental:
  - Files are keyed by (absolute_path, size, mtime) — an "indexed_fingerprint"
  - On re-run, files whose fingerprint hasn't changed are skipped
  - Files that vanished from disk are pruned

Not yet incremental:
  - OCR of scanned PDFs (extract_text returns empty for image PDFs — those get skipped with a warning)
  - Watch-based re-indexing (indexer is /index-docs on demand for now)
"""

import hashlib
from pathlib import Path

import chromadb

from tools.doc_extract import extract_text, SUPPORTED_EXTENSIONS
from tools.filesystem import _is_inside, _matches_sensitive
from config import get_read_paths

_CHUNK_SIZE_CHARS = 1500       # per-chunk text length — ~400 tokens
_CHUNK_OVERLAP = 200           # overlap between chunks so semantic units don't split
_INDEX_DIR = Path(__file__).parent.parent / "data" / "chroma_docs"


def _client():
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(_INDEX_DIR))


def _collection():
    return _client().get_or_create_collection("document_chunks")


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks. Returns [] for empty/short input."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= _CHUNK_SIZE_CHARS:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_SIZE_CHARS
        chunks.append(text[start:end])
        start += _CHUNK_SIZE_CHARS - _CHUNK_OVERLAP
    return chunks


def _fingerprint(path: Path) -> str:
    """Cheap change-detection fingerprint. Same file → same fingerprint."""
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _iter_indexable_files() -> list[Path]:
    """Walk all read-scoped roots, return files with supported extensions."""
    roots = get_read_paths()
    if not roots:
        return []

    files = []
    for root in roots:
        root_path = Path(root)
        try:
            for entry in root_path.rglob("*"):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                if _matches_sensitive(entry.resolve()):
                    continue
                # Skip files inside venv/, __pycache__, node_modules — these are dep artifacts, never user docs
                if any(part in ("venv", "__pycache__", "node_modules", ".venv") for part in entry.parts):
                    continue
                # Skip the agent's own persistence — chromadb sqlite, tokens, logs
                if any(part in ("chroma", "chroma_docs") for part in entry.parts):
                    continue
                files.append(entry)
        except (PermissionError, OSError):
            continue
    return files


def index_all(progress_callback=None) -> dict:
    """
    Index all indexable files in the read scope. Incremental — skips unchanged files.

    Returns a summary dict:
      {"indexed": N, "skipped": N, "removed": N, "errors": [(path, err), ...]}

    If progress_callback is provided, it's called as progress_callback(i, total, path).
    """
    col = _collection()

    # Get existing document IDs and their fingerprints
    existing = {}
    try:
        all_data = col.get()
        for doc_id, meta in zip(all_data.get("ids", []), all_data.get("metadatas", [])):
            fp = (meta or {}).get("fingerprint", "")
            src = (meta or {}).get("source_path", "")
            existing.setdefault(src, {"chunks": [], "fingerprint": fp})
            existing[src]["chunks"].append(doc_id)
    except Exception:
        existing = {}

    files = _iter_indexable_files()
    seen_paths = set()

    summary = {"indexed": 0, "skipped": 0, "removed": 0, "errors": []}

    for i, path in enumerate(files, 1):
        if progress_callback:
            progress_callback(i, len(files), path)

        path_str = str(path.resolve())
        seen_paths.add(path_str)

        try:
            new_fp = _fingerprint(path)
        except OSError as e:
            summary["errors"].append((path_str, f"stat failed: {e}"))
            continue

        # Skip if unchanged
        prior = existing.get(path_str)
        if prior and prior["fingerprint"] == new_fp:
            summary["skipped"] += 1
            continue

        # Extract text
        text = extract_text(path_str)
        if text.startswith("Error:") or text.startswith("PermissionDenied:"):
            summary["errors"].append((path_str, text[:120]))
            continue
        if text.startswith("(no extractable text"):
            summary["errors"].append((path_str, "no extractable text"))
            continue

        chunks = _chunk_text(text)
        if not chunks:
            continue

        # Remove old chunks for this file if it was re-indexed
        if prior:
            try:
                col.delete(ids=prior["chunks"])
            except Exception:
                pass

        # Add new chunks
        ids = [f"{new_fp}:{j}" for j in range(len(chunks))]
        metadatas = [{
            "source_path": path_str,
            "chunk_index": j,
            "fingerprint": new_fp,
            "filename": path.name,
        } for j in range(len(chunks))]

        try:
            col.add(documents=chunks, ids=ids, metadatas=metadatas)
            summary["indexed"] += 1
        except Exception as e:
            summary["errors"].append((path_str, f"chroma add failed: {e}"))

    # Prune files that no longer exist
    for stale_path, meta in existing.items():
        if stale_path not in seen_paths:
            try:
                col.delete(ids=meta["chunks"])
                summary["removed"] += 1
            except Exception:
                pass

    return summary


def search_documents(query: str, n: int = 5) -> list[dict]:
    """
    Semantic search over indexed documents.
    Returns list of {path, filename, chunk_index, snippet, distance} for the top-N chunks.
    """
    if not query.strip():
        return []

    col = _collection()
    try:
        count = col.count()
    except Exception:
        return []

    if count == 0:
        return []

    n = max(1, min(n, 20))
    try:
        results = col.query(query_texts=[query], n_results=min(n, count))
    except Exception:
        return []

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    hits = []
    for doc, meta, dist in zip(docs, metas, dists):
        meta = meta or {}
        # Truncate snippet for display
        snippet = doc[:400] + ("..." if len(doc) > 400 else "")
        hits.append({
            "path": meta.get("source_path", ""),
            "filename": meta.get("filename", ""),
            "chunk_index": meta.get("chunk_index", 0),
            "snippet": snippet,
            "distance": round(dist, 4) if dist is not None else None,
        })
    return hits


def format_search_results(query: str, hits: list[dict]) -> str:
    """Human/LLM-friendly formatting of search results."""
    if not hits:
        return f"No document matches for query: '{query}'. Try /index-docs if you haven't indexed yet."

    lines = [f"Top {len(hits)} document matches for: '{query}'\n"]
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h['filename']}  (chunk {h['chunk_index']}, distance {h['distance']})")
        lines.append(f"    Path: {h['path']}")
        lines.append(f"    Snippet: {h['snippet']}")
        lines.append("")
    return "\n".join(lines).rstrip()
