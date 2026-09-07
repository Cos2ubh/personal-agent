"""
Document content index — semantic search over the user's documents.

Walks every doc in the read scope, extracts text, chunks it, embeds each
chunk into a ChromaDB collection. Once indexed, natural-language queries
like "find my marksheet" work even when the file is named IMG_20230415_10x.pdf.

The index is incremental:
  - Files are keyed by (absolute_path, size, mtime) — an "indexed_fingerprint"
  - On re-run, files whose fingerprint hasn't changed are skipped
  - Files that vanished from disk are pruned

Search is two-tier:
  - Tier 1: keyword-in-filename match (zero embedding cost, instant)
  - Tier 2: ChromaDB semantic search (falls back when Tier 1 has no hits)

Single-file ops (index_file / remove_file) are used by the file-system watcher
for real-time incremental updates without a full re-scan.
"""

import hashlib
import json
import os
from pathlib import Path

from tools.doc_extract import extract_text, SUPPORTED_EXTENSIONS
from tools.filesystem import _matches_sensitive
from config import get_read_paths

_CHUNK_SIZE_CHARS = 1500
_CHUNK_OVERLAP = 200
_INDEX_DIR = Path(__file__).parent.parent / "data" / "chroma_docs"
_PATH_INDEX = Path(__file__).parent.parent / "data" / "doc_path_index.json"

# Stop words stripped before filename keyword matching
_STOP = frozenset({
    "find", "my", "the", "a", "an", "show", "get", "where", "is", "are",
    "this", "that", "some", "any", "all", "me", "have", "has", "can", "i",
    "with", "in", "of", "on", "for", "to", "from", "about", "which", "what",
    "do", "does", "did", "please", "look", "search", "need", "want",
})


# ── Path index helpers ────────────────────────────────────────────────────
# Lightweight JSON: {absolute_source_path: filename}
# Used for Tier 1 filename keyword search — no embedding needed.

def _load_path_index() -> dict:
    try:
        return json.loads(_PATH_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_path_index(index: dict):
    _PATH_INDEX.parent.mkdir(parents=True, exist_ok=True)
    _PATH_INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


# ── ChromaDB helpers ──────────────────────────────────────────────────────

_chroma_client = None


def _client():
    global _chroma_client
    if _chroma_client is None:
        import chromadb  # lazy — saves ~2.5s on cold start
        _INDEX_DIR.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(_INDEX_DIR))
    return _chroma_client


def _collection():
    return _client().get_or_create_collection("document_chunks")


# ── Text helpers ──────────────────────────────────────────────────────────

def _chunk_text(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= _CHUNK_SIZE_CHARS:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + _CHUNK_SIZE_CHARS])
        start += _CHUNK_SIZE_CHARS - _CHUNK_OVERLAP
    return chunks


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _keywords(query: str) -> list[str]:
    """Extract meaningful words from a query for filename matching."""
    return [
        w.strip(".,?!'\"")
        for w in query.lower().split()
        if w not in _STOP and len(w) > 2
    ]


# ── Walk helpers ──────────────────────────────────────────────────────────

_PRUNE_DIRS = frozenset({
    "venv", ".venv", "__pycache__", "node_modules", ".tox", ".eggs",
    "chroma", "chroma_docs",
    ".git",
    "Windows", "Program Files", "Program Files (x86)",
    "$Recycle.Bin", "System Volume Information", "Recovery", "PerfLogs",
    "Temp", "tmp", "cache", "Cache",
})


def _iter_indexable_files() -> list[Path]:
    roots = get_read_paths()
    if not roots:
        return []
    files = []
    for root in roots:
        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=None):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in _PRUNE_DIRS and not d.startswith(".")
                ]
                for name in filenames:
                    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    full = Path(dirpath) / name
                    try:
                        resolved = full.resolve()
                    except OSError:
                        continue
                    if _matches_sensitive(resolved):
                        continue
                    files.append(full)
        except (PermissionError, OSError):
            continue
    return files


# ── Single-file ops (used by file-system watcher) ────────────────────────

def index_file(path_str: str) -> str:
    """
    Index or re-index a single file. Returns 'indexed' | 'skipped' | 'error: ...'.
    Updates the path index on success.
    """
    path = Path(path_str).resolve()
    if not path.is_file():
        return "error: not a file"
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "skipped: unsupported extension"
    if _matches_sensitive(path):
        return "skipped: sensitive path"

    try:
        new_fp = _fingerprint(path)
    except OSError as e:
        return f"error: stat failed: {e}"

    col = _collection()
    path_str_resolved = str(path)

    # Check if already indexed and unchanged
    try:
        existing = col.get(where={"source_path": path_str_resolved})
        if existing["ids"]:
            existing_fp = (existing["metadatas"][0] or {}).get("fingerprint", "")
            if existing_fp == new_fp:
                return "skipped"
            col.delete(ids=existing["ids"])
    except Exception:
        pass

    text = extract_text(path_str_resolved)
    if text.startswith("Error:") or text.startswith("PermissionDenied:"):
        return f"error: {text[:100]}"
    if text.startswith("(no extractable text"):
        return "skipped: no extractable text"

    chunks = _chunk_text(text)
    if not chunks:
        return "skipped: empty after chunking"

    ids = [f"{new_fp}:{j}" for j in range(len(chunks))]
    metadatas = [{
        "source_path": path_str_resolved,
        "chunk_index": j,
        "fingerprint": new_fp,
        "filename": path.name,
    } for j in range(len(chunks))]

    try:
        col.add(documents=chunks, ids=ids, metadatas=metadatas)
    except Exception as e:
        return f"error: chroma add failed: {e}"

    idx = _load_path_index()
    idx[path_str_resolved] = path.name
    _save_path_index(idx)
    return "indexed"


def remove_file(path_str: str) -> int:
    """
    Remove all chunks for a file from the index. Returns number of chunks removed.
    Updates the path index.
    """
    resolved = str(Path(path_str).resolve())
    col = _collection()
    removed = 0
    try:
        existing = col.get(where={"source_path": resolved})
        if existing["ids"]:
            col.delete(ids=existing["ids"])
            removed = len(existing["ids"])
    except Exception:
        pass

    idx = _load_path_index()
    if resolved in idx:
        del idx[resolved]
        _save_path_index(idx)

    return removed


# ── Full re-index ─────────────────────────────────────────────────────────

def index_all(progress_callback=None) -> dict:
    """
    Index all indexable files in the read scope. Incremental — skips unchanged files.

    Returns: {"indexed": N, "skipped": N, "removed": N, "errors": [(path, err), ...]}
    """
    col = _collection()

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
    path_idx = _load_path_index()
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

        prior = existing.get(path_str)
        if prior and prior["fingerprint"] == new_fp:
            summary["skipped"] += 1
            continue

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

        if prior:
            try:
                col.delete(ids=prior["chunks"])
            except Exception:
                pass

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
            path_idx[path_str] = path.name
        except Exception as e:
            summary["errors"].append((path_str, f"chroma add failed: {e}"))

    # Prune stale files
    for stale_path, meta in existing.items():
        if stale_path not in seen_paths:
            try:
                col.delete(ids=meta["chunks"])
                summary["removed"] += 1
                path_idx.pop(stale_path, None)
            except Exception:
                pass

    _save_path_index(path_idx)
    return summary


# ── Search ────────────────────────────────────────────────────────────────

def search_documents(query: str, n: int = 5) -> list[dict]:
    """
    Two-tier search over indexed documents.

    Tier 1: keyword-in-filename match against the path index (no embedding, instant).
    Tier 2: ChromaDB semantic search (only runs when Tier 1 finds nothing).

    Returns list of {path, filename, chunk_index, snippet, distance, match_type}.
    """
    if not query.strip():
        return []

    n = max(1, min(n, 20))

    # ── Tier 1: filename keyword match ────────────────────────────────────
    kws = _keywords(query)
    if kws:
        path_idx = _load_path_index()
        hits = []
        seen = set()
        for path_str, fname in path_idx.items():
            fname_lower = fname.lower()
            if any(kw in fname_lower for kw in kws) and path_str not in seen:
                seen.add(path_str)
                hits.append({
                    "path": path_str,
                    "filename": fname,
                    "chunk_index": 0,
                    "snippet": "(filename match — use extract_text to read contents)",
                    "distance": 0.0,
                    "match_type": "filename",
                })
                if len(hits) >= n:
                    break
        if hits:
            return hits

    # ── Tier 2: semantic search ───────────────────────────────────────────
    col = _collection()
    try:
        count = col.count()
    except Exception:
        return []

    if count == 0:
        return []

    try:
        results = col.query(query_texts=[query], n_results=min(n, count))
    except Exception:
        return []

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    return [
        {
            "path": (meta or {}).get("source_path", ""),
            "filename": (meta or {}).get("filename", ""),
            "chunk_index": (meta or {}).get("chunk_index", 0),
            "snippet": doc[:400] + ("..." if len(doc) > 400 else ""),
            "distance": round(dist, 4) if dist is not None else None,
            "match_type": "semantic",
        }
        for doc, meta, dist in zip(docs, metas, dists)
    ]


def format_search_results(query: str, hits: list[dict]) -> str:
    if not hits:
        return f"No document matches for '{query}'. Run /index-docs if you haven't indexed yet."

    lines = [f"Top {len(hits)} document matches for: '{query}'\n"]
    for i, h in enumerate(hits, 1):
        tier = h.get("match_type", "semantic")
        dist_str = f"distance {h['distance']}" if tier == "semantic" else "filename match"
        lines.append(f"[{i}] {h['filename']}  ({dist_str})")
        lines.append(f"    Path: {h['path']}")
        lines.append(f"    Snippet: {h['snippet']}")
        lines.append("")
    return "\n".join(lines).rstrip()
