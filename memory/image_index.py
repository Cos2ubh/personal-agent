"""
Image content index — semantic search over photos and screenshots.

Uses CLIP (via sentence-transformers, model clip-ViT-B-32) to embed every
image in the read scope into the same 512-d vector space as text descriptions.
Once indexed, natural-language queries like 'sunset photos' or 'screenshots
of code' work regardless of filename.

The CLIP model is downloaded on first use (~180MB, cached to
~/.cache/huggingface/). Everything runs locally on CPU — no cloud calls.

Index is incremental — files are keyed by (absolute_path, size, mtime).
Re-runs only embed changed or new images, and prune deletions.

Not yet:
  - Face recognition (identity-based photo search) — separate module
  - GPU acceleration (would speed indexing 5-10x on a laptop with CUDA)
"""

import hashlib
from pathlib import Path

import chromadb

from tools.filesystem import _matches_sensitive
from config import get_read_paths

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

_CLIP_MODEL_NAME = "clip-ViT-B-32"
_INDEX_DIR = Path(__file__).parent.parent / "data" / "chroma_images"

_model = None


def _get_model():
    """Lazy-load CLIP so import-time is fast and offline-friendly."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_CLIP_MODEL_NAME)
    return _model


def _client():
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(_INDEX_DIR))


def _collection():
    # Use cosine metric — CLIP embeddings are typically normalized for cosine similarity
    return _client().get_or_create_collection(
        "image_embeddings",
        metadata={"hnsw:space": "cosine"},
    )


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _iter_indexable_images() -> list[Path]:
    roots = get_read_paths()
    if not roots:
        return []

    images = []
    for root in roots:
        root_path = Path(root)
        try:
            for entry in root_path.rglob("*"):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
                    continue
                if _matches_sensitive(entry.resolve()):
                    continue
                if any(part in ("venv", "__pycache__", "node_modules", ".venv") for part in entry.parts):
                    continue
                if any(part in ("chroma", "chroma_docs", "chroma_images") for part in entry.parts):
                    continue
                # Skip anything huge — very likely not user photos and would slow indexing
                try:
                    if entry.stat().st_size > 25 * 1024 * 1024:  # 25 MB
                        continue
                except OSError:
                    continue
                images.append(entry)
        except (PermissionError, OSError):
            continue
    return images


def index_all(progress_callback=None) -> dict:
    """
    Index all images in the read scope. Incremental — unchanged files are skipped,
    deleted files are pruned.

    Returns: {"indexed": N, "skipped": N, "removed": N, "errors": [(path, msg), ...]}
    """
    from PIL import Image

    col = _collection()

    # Existing state
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
    summary = {"indexed": 0, "skipped": 0, "removed": 0, "errors": []}

    # Load model lazily and only if there's work to do
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

        # Load + embed
        try:
            img = Image.open(path).convert("RGB")
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

        # Replace prior embedding if this file was seen before
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
                documents=[path.name],   # searchable label; real match is via embedding
            )
            summary["indexed"] += 1
        except Exception as e:
            summary["errors"].append((path_str, f"chroma add failed: {e}"))

    # Prune vanished files
    for stale_path, meta in existing.items():
        if stale_path not in seen_paths:
            try:
                col.delete(ids=[meta["id"]])
                summary["removed"] += 1
            except Exception:
                pass

    return summary


def search_images(query: str, n: int = 5) -> list[dict]:
    """Semantic image search using CLIP text embedding of the query."""
    if not query.strip():
        return []

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

    n = max(1, min(n, 20))
    try:
        results = col.query(
            query_embeddings=[text_vec.tolist()],
            n_results=min(n, count),
        )
    except Exception:
        return []

    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    hits = []
    for meta, dist in zip(metas, dists):
        meta = meta or {}
        hits.append({
            "path": meta.get("source_path", ""),
            "filename": meta.get("filename", ""),
            "distance": round(dist, 4) if dist is not None else None,
        })
    return hits


def format_search_results(query: str, hits: list[dict]) -> str:
    if not hits:
        return f"No image matches for query: '{query}'. Try /index-images if you haven't indexed yet."
    lines = [f"Top {len(hits)} image matches for: '{query}'\n"]
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h['filename']}  (cosine distance {h['distance']})")
        lines.append(f"    Path: {h['path']}")
        lines.append("")
    return "\n".join(lines).rstrip()
