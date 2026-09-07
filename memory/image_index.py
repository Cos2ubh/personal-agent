"""
Image content index — semantic search over photos and screenshots.

Uses CLIP (via sentence-transformers, model clip-ViT-B-32) to embed every
image in the read scope into the same 512-d vector space as text descriptions.
Once indexed, natural-language queries like 'sunset photos' or 'screenshots
of code' work regardless of filename.

Search is two-tier:
  - Tier 1: keyword-in-filename match (no CLIP inference, instant)
  - Tier 2: CLIP semantic search (falls back when Tier 1 has no hits)

Single-file ops (index_file / remove_file) are used by the file-system watcher
for real-time incremental updates without a full re-scan.
"""

import hashlib
import json
import os
from pathlib import Path

from tools.filesystem import _matches_sensitive
from config import get_read_paths

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

_CLIP_MODEL_NAME = "clip-ViT-B-32"
_INDEX_DIR = Path(__file__).parent.parent / "data" / "chroma_images"
_PATH_INDEX = Path(__file__).parent.parent / "data" / "img_path_index.json"

# Stop words stripped before filename keyword matching (shared with doc_index logic)
_STOP = frozenset({
    "find", "my", "the", "a", "an", "show", "get", "where", "is", "are",
    "this", "that", "some", "any", "all", "me", "have", "has", "can", "i",
    "with", "in", "of", "on", "for", "to", "from", "about", "which", "what",
    "do", "does", "did", "please", "look", "search", "need", "want",
    "photo", "photos", "picture", "pictures", "image", "images",
})

_model = None


# ── Path index helpers ────────────────────────────────────────────────────

def _load_path_index() -> dict:
    try:
        return json.loads(_PATH_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_path_index(index: dict):
    _PATH_INDEX.parent.mkdir(parents=True, exist_ok=True)
    _PATH_INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


# ── CLIP model helper ─────────────────────────────────────────────────────

def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_CLIP_MODEL_NAME)
    return _model


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
    return _client().get_or_create_collection(
        "image_embeddings",
        metadata={"hnsw:space": "cosine"},
    )


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _keywords(query: str) -> list[str]:
    return [
        w.strip(".,?!'\"")
        for w in query.lower().split()
        if w not in _STOP and len(w) > 2
    ]


# ── Walk helpers ──────────────────────────────────────────────────────────

_PRUNE_DIRS = frozenset({
    "venv", ".venv", "__pycache__", "node_modules", ".tox", ".eggs",
    "chroma", "chroma_docs", "chroma_images",
    ".git",
    "Windows", "Program Files", "Program Files (x86)",
    "$Recycle.Bin", "System Volume Information", "Recovery", "PerfLogs",
    "Temp", "tmp", "cache", "Cache",
})


def _iter_indexable_images() -> list[Path]:
    roots = get_read_paths()
    if not roots:
        return []
    images = []
    for root in roots:
        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=None):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in _PRUNE_DIRS and not d.startswith(".")
                ]
                for name in filenames:
                    if Path(name).suffix.lower() not in SUPPORTED_IMAGE_EXTS:
                        continue
                    full = Path(dirpath) / name
                    try:
                        resolved = full.resolve()
                    except OSError:
                        continue
                    if _matches_sensitive(resolved):
                        continue
                    try:
                        if full.stat().st_size > 25 * 1024 * 1024:
                            continue
                    except OSError:
                        continue
                    images.append(full)
        except (PermissionError, OSError):
            continue
    return images


# ── Single-file ops (used by file-system watcher) ────────────────────────

def index_file(path_str: str) -> str:
    """
    Index or re-index a single image file. Returns 'indexed' | 'skipped' | 'error: ...'.
    Updates the path index on success.
    """
    from PIL import Image as PILImage

    path = Path(path_str).resolve()
    if not path.is_file():
        return "error: not a file"
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
        return "skipped: unsupported extension"
    if _matches_sensitive(path):
        return "skipped: sensitive path"

    try:
        size = path.stat().st_size
        if size > 25 * 1024 * 1024:
            return "skipped: file too large"
        new_fp = _fingerprint(path)
    except OSError as e:
        return f"error: stat failed: {e}"

    col = _collection()
    path_str_resolved = str(path)

    try:
        existing = col.get(ids=[new_fp])
        if existing["ids"]:
            return "skipped"
    except Exception:
        pass

    # Remove old embedding if the file was re-indexed under a different fingerprint
    try:
        old = col.get(where={"source_path": path_str_resolved})
        if old["ids"]:
            col.delete(ids=old["ids"])
    except Exception:
        pass

    try:
        img = PILImage.open(path).convert("RGB")
    except Exception as e:
        return f"error: open failed: {e}"

    model = _get_model()
    try:
        vector = model.encode(img, convert_to_numpy=True, normalize_embeddings=True)
    except Exception as e:
        return f"error: embed failed: {e}"

    try:
        col.add(
            ids=[new_fp],
            embeddings=[vector.tolist()],
            metadatas=[{
                "source_path": path_str_resolved,
                "filename": path.name,
                "fingerprint": new_fp,
            }],
            documents=[path.name],
        )
    except Exception as e:
        return f"error: chroma add failed: {e}"

    idx = _load_path_index()
    idx[path_str_resolved] = path.name
    _save_path_index(idx)
    return "indexed"


def remove_file(path_str: str) -> int:
    """Remove an image's embedding from the index. Returns 1 if removed, 0 if not found."""
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
    Index all images in the read scope. Incremental — unchanged files are skipped.

    Returns: {"indexed": N, "skipped": N, "removed": N, "errors": [(path, msg), ...]}
    """
    from PIL import Image as PILImage

    col = _collection()

    existing = {}
    try:
        all_data = col.get()
        for doc_id, meta in zip(all_data.get("ids", []), all_data.get("metadatas", [])):
            src = (meta or {}).get("source_path", "")
            fp = (meta or {}).get("fingerprint", "")
            if src:
                existing[src] = {"id": doc_id, "fingerprint": fp}
    except Exception:
        existing = {}

    images = _iter_indexable_images()
    seen_paths = set()
    path_idx = _load_path_index()
    summary = {"indexed": 0, "skipped": 0, "removed": 0, "errors": []}
    model = None

    for i, path in enumerate(images, 1):
        if progress_callback:
            progress_callback(i, len(images), path)

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

        try:
            img = PILImage.open(path).convert("RGB")
        except Exception as e:
            summary["errors"].append((path_str, f"open failed: {e}"))
            continue

        if model is None:
            model = _get_model()

        try:
            vector = model.encode(img, convert_to_numpy=True, normalize_embeddings=True)
        except Exception as e:
            summary["errors"].append((path_str, f"embed failed: {e}"))
            continue

        if prior:
            try:
                col.delete(ids=[prior["id"]])
            except Exception:
                pass

        try:
            col.add(
                ids=[new_fp],
                embeddings=[vector.tolist()],
                metadatas=[{
                    "source_path": path_str,
                    "filename": path.name,
                    "fingerprint": new_fp,
                }],
                documents=[path.name],
            )
            summary["indexed"] += 1
            path_idx[path_str] = path.name
        except Exception as e:
            summary["errors"].append((path_str, f"chroma add failed: {e}"))

    # Prune vanished files
    for stale_path, meta in existing.items():
        if stale_path not in seen_paths:
            try:
                col.delete(ids=[meta["id"]])
                summary["removed"] += 1
                path_idx.pop(stale_path, None)
            except Exception:
                pass

    _save_path_index(path_idx)
    return summary


# ── Search ────────────────────────────────────────────────────────────────

def search_images(query: str, n: int = 5) -> list[dict]:
    """
    Two-tier semantic image search.

    Tier 1: keyword-in-filename match (no CLIP inference needed, instant).
    Tier 2: CLIP text-embedding search (only runs when Tier 1 finds nothing).

    Returns list of {path, filename, distance, match_type}.
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
                    "distance": 0.0,
                    "match_type": "filename",
                })
                if len(hits) >= n:
                    break
        if hits:
            return hits

    # ── Tier 2: CLIP semantic search ──────────────────────────────────────
    col = _collection()
    try:
        count = col.count()
    except Exception:
        return []

    if count == 0:
        return []

    model = _get_model()
    try:
        text_vec = model.encode(query, convert_to_numpy=True, normalize_embeddings=True)
    except Exception:
        return []

    try:
        results = col.query(
            query_embeddings=[text_vec.tolist()],
            n_results=min(n, count),
        )
    except Exception:
        return []

    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    return [
        {
            "path": (meta or {}).get("source_path", ""),
            "filename": (meta or {}).get("filename", ""),
            "distance": round(dist, 4) if dist is not None else None,
            "match_type": "semantic",
        }
        for meta, dist in zip(metas, dists)
    ]


def format_search_results(query: str, hits: list[dict]) -> str:
    if not hits:
        return f"No image matches for '{query}'. Run /index-images if you haven't indexed yet."
    lines = [f"Top {len(hits)} image matches for: '{query}'\n"]
    for i, h in enumerate(hits, 1):
        tier = h.get("match_type", "semantic")
        dist_str = f"cosine distance {h['distance']}" if tier == "semantic" else "filename match"
        lines.append(f"[{i}] {h['filename']}  ({dist_str})")
        lines.append(f"    Path: {h['path']}")
        lines.append("")
    return "\n".join(lines).rstrip()
