"""
File-system watcher — real-time incremental index updates.

Starts a background watchdog Observer that monitors all read-scoped directories.
When a file is created or modified, the appropriate index (doc or image) is
updated for that single file — no full re-scan needed.
When a file is deleted, it's pruned from whichever index it was in.

Usage:
    from memory.watcher import start_watcher, stop_watcher
    start_watcher()   # call once at app startup; idempotent

Requires:
    pip install watchdog
"""

import logging
import threading
from pathlib import Path

from tools.doc_extract import SUPPORTED_EXTENSIONS
from memory.image_index import SUPPORTED_IMAGE_EXTS
from config import get_read_paths

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_observer = None      # watchdog Observer instance
_started = False      # guard against double-start across Streamlit reruns


# ── Event handler ─────────────────────────────────────────────────────────

class _IndexHandler:
    """
    Watchdog event handler. Dispatches each FS event to the right index op.
    Imported lazily so the module is importable even without watchdog installed.
    """

    def dispatch(self, event):
        """Called by watchdog for every FS event."""
        if event.is_directory:
            return
        path = getattr(event, "dest_path", None) or event.src_path
        self._handle(event.event_type, path)

    def _handle(self, event_type: str, path: str):
        p = Path(path)
        ext = p.suffix.lower()

        if event_type in ("created", "modified"):
            if ext in SUPPORTED_EXTENSIONS:
                self._index_doc(path)
            elif ext in SUPPORTED_IMAGE_EXTS:
                self._index_img(path)

        elif event_type == "deleted":
            if ext in SUPPORTED_EXTENSIONS:
                self._remove_doc(path)
            elif ext in SUPPORTED_IMAGE_EXTS:
                self._remove_img(path)

        # "moved" fires with src_path (old) and dest_path (new)
        elif event_type == "moved":
            src = getattr(self, "_last_src", path)
            src_ext = Path(src).suffix.lower()
            if src_ext in SUPPORTED_EXTENSIONS:
                self._remove_doc(src)
                self._index_doc(path)
            elif src_ext in SUPPORTED_IMAGE_EXTS:
                self._remove_img(src)
                self._index_img(path)

    # ── Wrappers with logging ─────────────────────────────────────────────

    @staticmethod
    def _index_doc(path: str):
        try:
            from memory.doc_index import index_file
            result = index_file(path)
            if result == "indexed":
                logger.info("watcher: doc indexed — %s", path)
        except Exception as e:
            logger.warning("watcher: doc index error for %s: %s", path, e)

    @staticmethod
    def _remove_doc(path: str):
        try:
            from memory.doc_index import remove_file
            n = remove_file(path)
            if n:
                logger.info("watcher: doc removed (%d chunks) — %s", n, path)
        except Exception as e:
            logger.warning("watcher: doc remove error for %s: %s", path, e)

    @staticmethod
    def _index_img(path: str):
        try:
            from memory.image_index import index_file
            result = index_file(path)
            if result == "indexed":
                logger.info("watcher: image indexed — %s", path)
        except Exception as e:
            logger.warning("watcher: image index error for %s: %s", path, e)

    @staticmethod
    def _remove_img(path: str):
        try:
            from memory.image_index import remove_file
            n = remove_file(path)
            if n:
                logger.info("watcher: image removed — %s", path)
        except Exception as e:
            logger.warning("watcher: image remove error for %s: %s", path, e)


# ── Public API ────────────────────────────────────────────────────────────

def start_watcher() -> bool:
    """
    Start the background watchdog observer. Idempotent — safe to call multiple
    times (e.g. on every Streamlit rerun); only the first call actually starts it.

    Returns True if started now, False if already running or watchdog not installed.
    """
    global _observer, _started

    with _lock:
        if _started:
            return False

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning(
                "watcher: watchdog not installed — real-time indexing disabled. "
                "Run: pip install watchdog"
            )
            return False

        roots = get_read_paths()
        if not roots:
            logger.info("watcher: no read paths configured — not starting")
            return False

        # Wrap our plain handler in a FileSystemEventHandler subclass
        class _WatchdogAdapter(FileSystemEventHandler):
            def __init__(self):
                super().__init__()
                self._h = _IndexHandler()

            def on_any_event(self, event):
                self._h.dispatch(event)

        observer = Observer()
        handler = _WatchdogAdapter()

        for root in roots:
            try:
                observer.schedule(handler, root, recursive=True)
                logger.info("watcher: watching %s", root)
            except Exception as e:
                logger.warning("watcher: could not watch %s: %s", root, e)

        observer.daemon = True   # dies with the main process
        observer.start()
        _observer = observer
        _started = True
        logger.info("watcher: started")
        return True


def stop_watcher():
    """Stop the watchdog observer. Mainly for testing / clean shutdown."""
    global _observer, _started
    with _lock:
        if _observer is not None:
            try:
                _observer.stop()
                _observer.join(timeout=2)
            except Exception:
                pass
            _observer = None
        _started = False


def is_running() -> bool:
    """Return True if the watcher background thread is alive."""
    with _lock:
        return _started and _observer is not None and _observer.is_alive()


def watcher_status() -> str:
    """Human-readable status string for the sidebar / debug info."""
    if is_running():
        roots = get_read_paths()
        return f"active — watching {len(roots)} root(s)"
    return "inactive"
