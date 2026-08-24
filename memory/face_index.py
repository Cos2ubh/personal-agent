"""
Face recognition index — find photos of specific people you've registered.

Uses InsightFace (buffalo_s model, ~10 MB, ONNX-based) to detect faces in
images and compute identity embeddings. Two ChromaDB collections:
  - face_registry:   one entry per named person (registered from a sample photo)
  - face_detections: one entry per face found in any image in the read scope

To find "photos of Priya":
  1. Look up Priya's embedding in face_registry
  2. Cosine-similarity search that embedding against face_detections
  3. Return unique source paths of matching photos

Model downloads on first use to ~/.insightface/models/. Everything runs
locally on CPU — no face vector ever leaves the machine.

Not yet:
  - Auto-cluster unlabeled faces (interactive naming later)
  - Multi-face-per-person (currently one embedding per registered name)
"""

import hashlib
import uuid
from pathlib import Path

import chromadb

from tools.filesystem import _matches_sensitive
from config import get_read_paths
from memory.image_index import SUPPORTED_IMAGE_EXTS

_INDEX_DIR = Path(__file__).parent.parent / "data" / "chroma_faces"
_MODEL_NAME = "buffalo_s"       # small model — ~10 MB, fast on CPU

# Cosine-distance threshold for a "match" — tuned for buffalo_s.
# Lower = stricter. Values above ~0.55 tend to be different people.
DEFAULT_MATCH_THRESHOLD = 0.45

_face_app = None


def _get_face_app():
    """Lazy-load the InsightFace analyser."""
    global _face_app
    if _face_app is None:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(name=_MODEL_NAME, providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        _face_app = app
    return _face_app


def _client():
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(_INDEX_DIR))


def _registry():
    return _client().get_or_create_collection(
        "face_registry",
        metadata={"hnsw:space": "cosine"},
    )


def _detections():
    return _client().get_or_create_collection(
        "face_detections",
        metadata={"hnsw:space": "cosine"},
    )


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def register_face(name: str, sample_path: str) -> str:
    """Extract the largest face from a sample image and store it under `name`."""
    name = name.strip()
    if not name:
        return "Error: name is required."

    path = Path(sample_path)
    if not path.exists():
        return f"Error: sample image not found — {path}"
    if _matches_sensitive(path.resolve()):
        return f"Error: '{path}' is blocked by the sensitive-file rules."
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
        return f"Error: '{path.suffix}' is not a supported image type."

    try:
        import cv2
    except ImportError:
        return "Error: opencv-python not installed (comes with insightface)."

    img = cv2.imread(str(path))
    if img is None:
        return f"Error: could not read image {path}"

    app = _get_face_app()
    faces = app.get(img)
    if not faces:
        return f"Error: no face detected in {path.name}."

    # Take the largest face (biggest bbox area)
    def _area(f):
        x1, y1, x2, y2 = f.bbox
        return (x2 - x1) * (y2 - y1)
    face = max(faces, key=_area)
    embedding = face.normed_embedding.tolist()

    reg = _registry()
    # Overwrite if this name is already registered
    try:
        existing = reg.get(where={"name": name})
        if existing and existing.get("ids"):
            reg.delete(ids=existing["ids"])
    except Exception:
        pass

    entry_id = f"face_{uuid.uuid4().hex}"
    reg.add(
        ids=[entry_id],
        embeddings=[embedding],
        metadatas=[{"name": name, "sample_path": str(path.resolve())}],
        documents=[name],
    )

    return f"Registered face for '{name}' from {path.name}."


def list_registered() -> str:
    reg = _registry()
    try:
        data = reg.get()
    except Exception:
        return "No registered faces."
    metas = data.get("metadatas", [])
    if not metas:
        return "No registered faces. Use register_face(name, sample_path) to add one."
    names = sorted({m.get("name", "?") for m in metas})
    return "Registered faces:\n" + "\n".join(f"  - {n}" for n in names)


def _iter_indexable_images() -> list[Path]:
    """Same as image_index but re-declared to avoid coupling."""
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
                if any(part in ("chroma", "chroma_docs", "chroma_images", "chroma_faces") for part in entry.parts):
                    continue
                try:
                    if entry.stat().st_size > 25 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                images.append(entry)
        except (PermissionError, OSError):
            continue
    return images


def index_all(progress_callback=None) -> dict:
    """
    Walk the read scope, detect faces in every image, store each face's embedding
    with source-path metadata. Incremental — unchanged files are skipped.

    Returns summary dict.
    """
    import cv2

    det = _detections()

    # Existing state, indexed by source_path
    existing_by_path = {}
    try:
        data = det.get()
        for doc_id, meta in zip(data.get("ids", []), data.get("metadatas", [])):
            src = (meta or {}).get("source_path", "")
            fp = (meta or {}).get("fingerprint", "")
            existing_by_path.setdefault(src, {"ids": [], "fingerprint": fp})
            existing_by_path[src]["ids"].append(doc_id)
    except Exception:
        existing_by_path = {}

    images = _iter_indexable_images()
    seen = set()
    summary = {"indexed": 0, "faces_found": 0, "skipped": 0, "removed": 0, "errors": []}

    app = None  # lazy-loaded on first embed

    for i, path in enumerate(images, 1):
        if progress_callback:
            progress_callback(i, len(images), path)

        path_str = str(path.resolve())
        seen.add(path_str)

        try:
            new_fp = _fingerprint(path)
        except OSError as e:
            summary["errors"].append((path_str, f"stat failed: {e}"))
            continue

        prior = existing_by_path.get(path_str)
        if prior and prior["fingerprint"] == new_fp:
            summary["skipped"] += 1
            continue

        img = cv2.imread(path_str)
        if img is None:
            summary["errors"].append((path_str, "cv2.imread returned None"))
            continue

        if app is None:
            app = _get_face_app()

        try:
            faces = app.get(img)
        except Exception as e:
            summary["errors"].append((path_str, f"face detect failed: {e}"))
            continue

        # Wipe prior detections for this file if it was seen before
        if prior:
            try:
                det.delete(ids=prior["ids"])
            except Exception:
                pass

        if not faces:
            # Store a marker so we don't re-scan the same fp
            marker_id = f"face_none_{new_fp}"
            det.add(
                ids=[marker_id],
                embeddings=[[0.0] * 512],  # placeholder — never matched (see search filter)
                metadatas=[{
                    "source_path": path_str,
                    "fingerprint": new_fp,
                    "no_face": True,
                }],
                documents=["(no face)"],
            )
            summary["indexed"] += 1
            continue

        for j, face in enumerate(faces):
            face_id = f"face_{new_fp}_{j}"
            det.add(
                ids=[face_id],
                embeddings=[face.normed_embedding.tolist()],
                metadatas=[{
                    "source_path": path_str,
                    "fingerprint": new_fp,
                    "face_index": j,
                    "no_face": False,
                }],
                documents=[path.name],
            )
            summary["faces_found"] += 1

        summary["indexed"] += 1

    # Prune vanished files
    for stale_path, meta in existing_by_path.items():
        if stale_path not in seen:
            try:
                det.delete(ids=meta["ids"])
                summary["removed"] += 1
            except Exception:
                pass

    return summary


def find_photos_of(name: str, threshold: float = DEFAULT_MATCH_THRESHOLD, n: int = 20) -> str:
    """Return unique source paths of photos likely to contain the named person."""
    name = name.strip()
    if not name:
        return "Error: name is required."

    reg = _registry()
    try:
        matches = reg.get(where={"name": name})
    except Exception:
        return f"Error: could not look up '{name}'."
    if not matches.get("embeddings"):
        return (
            f"'{name}' is not registered. Use register_face('{name}', 'path/to/sample.jpg') first."
        )

    query_vec = matches["embeddings"][0]
    det = _detections()

    try:
        count = det.count()
    except Exception:
        return "Face index is empty — run /index-faces first."
    if count == 0:
        return "Face index is empty — run /index-faces first."

    n = max(1, min(n, 100))
    try:
        results = det.query(
            query_embeddings=[query_vec],
            n_results=min(n * 3, count),  # over-fetch since one photo can have multiple faces
            where={"no_face": False},
        )
    except Exception as e:
        return f"Error: face query failed — {e}"

    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    seen_paths = set()
    hits = []
    for meta, dist in zip(metas, dists):
        if dist is None or dist > threshold:
            continue
        src = (meta or {}).get("source_path", "")
        if not src or src in seen_paths:
            continue
        seen_paths.add(src)
        hits.append((src, dist))
        if len(hits) >= n:
            break

    if not hits:
        return f"No photos of '{name}' found in the indexed images (threshold: cosine distance ≤ {threshold})."

    lines = [f"Found {len(hits)} photo(s) of '{name}':"]
    for src, dist in hits:
        lines.append(f"  - {Path(src).name}  (distance {round(dist, 3)})")
        lines.append(f"    {src}")
    return "\n".join(lines)
