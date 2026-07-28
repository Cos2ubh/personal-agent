"""
Sandboxed file system access.
Every operation checks the path against the user's allow-list before proceeding.
The agent cannot read, list, or even acknowledge the existence of anything outside.
"""

from pathlib import Path
from config import get_allowed_paths


class PermissionDenied(Exception):
    pass


def _is_allowed(path: Path) -> bool:
    """True if path is inside at least one allowed root."""
    resolved = path.resolve()
    for root in get_allowed_paths():
        if resolved.is_relative_to(Path(root).resolve()):
            return True
    return False


def _guard(path: Path):
    if not _is_allowed(path):
        raise PermissionDenied(
            f"Access denied: '{path}' is outside your allowed folders. "
            "Use /permissions to update your allow-list."
        )


def read_file(path_str: str) -> str:
    """Read a text file. Raises PermissionDenied if outside allowed paths."""
    path = Path(path_str)
    _guard(path)
    if not path.exists():
        return f"Error: file not found — {path}"
    if not path.is_file():
        return f"Error: '{path}' is a directory, not a file."
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading file: {e}"


def list_dir(path_str: str) -> list[str]:
    """List contents of a directory. Raises PermissionDenied if outside allowed paths."""
    path = Path(path_str)
    _guard(path)
    if not path.exists():
        return [f"Error: directory not found — {path}"]
    if not path.is_dir():
        return [f"Error: '{path}' is a file, not a directory."]
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        return [f"{'[DIR] ' if e.is_dir() else '      '}{e.name}" for e in entries]
    except PermissionError:
        return [f"Error: OS-level permission denied for '{path}'."]


def write_file(path_str: str, content: str) -> str:
    """Write text to a file. Raises PermissionDenied if outside allowed paths."""
    path = Path(path_str)
    _guard(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Written: {path}"
    except OSError as e:
        return f"Error writing file: {e}"
