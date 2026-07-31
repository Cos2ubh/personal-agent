"""
Sandboxed file system access.
Every operation checks the path against the user's allow-list before proceeding.
The agent cannot read, list, or even acknowledge the existence of anything outside.

Additionally, a hardcoded blacklist blocks sensitive files (secrets, keys,
credentials, browser cookies, git internals) EVEN INSIDE allowed folders.
The blacklist is not user-configurable — these files should never be accessed
by an agent regardless of allow-list scope.
"""

import fnmatch
from pathlib import Path
from config import get_read_paths, get_write_paths


# Filename patterns (case-insensitive) that match sensitive files.
# Match is against the individual name components of the path, not the full path.
SENSITIVE_NAME_PATTERNS = [
    ".env", ".env.*",              # env vars / secrets
    "id_rsa", "id_rsa.*",          # SSH private keys
    "id_dsa", "id_ecdsa", "id_ed25519", "id_ed25519.*",
    "*.pem", "*.key", "*.pfx", "*.p12",   # private keys / certs
    "credentials", "credentials.*",       # AWS-style creds
    "*.kdbx",                             # KeePass DBs
    "cookies.sqlite", "cookies.sqlite-*",  # browser cookies
    "Wallet.dat", "wallet.dat",           # crypto wallets
    "NTUSER.DAT", "ntuser.dat",           # windows user hive
    ".git-credentials",
    "authorized_keys", "known_hosts",
    "*.password", "*.secret",
    "token_*.json", "token.json",         # OAuth refresh tokens
]

# Directory names (case-insensitive) whose contents are entirely off-limits.
SENSITIVE_DIR_NAMES = [
    ".git",         # git internals (config can hold tokens)
    ".ssh",         # ssh keys and configs
    ".aws",         # aws creds
    ".gnupg",       # GPG keys
    "AppData",      # Windows per-app data (contains cookies, tokens, session state)
]


class PermissionDenied(Exception):
    pass


def _matches_sensitive(path: Path) -> str | None:
    """
    If the path is (or is inside) a sensitive file/dir, return a short reason string.
    Otherwise return None.
    """
    # Check each path part against sensitive directory names
    for part in path.parts:
        lower = part.lower()
        for dname in SENSITIVE_DIR_NAMES:
            if lower == dname.lower():
                return f"inside sensitive directory '{dname}'"

    # Check the final name against sensitive file patterns
    name_lower = path.name.lower()
    for pattern in SENSITIVE_NAME_PATTERNS:
        if fnmatch.fnmatch(name_lower, pattern.lower()):
            return f"matches sensitive pattern '{pattern}'"

    return None


def _is_inside(path: Path, roots: list[str]) -> bool:
    """True if path is inside at least one of the given root paths."""
    resolved = path.resolve()
    for root in roots:
        if resolved.is_relative_to(Path(root).resolve()):
            return True
    return False


def _guard(path: Path, mode: str = "read"):
    """
    Raise PermissionDenied if the path is:
      1) outside the user's allow-list for the given mode (read or write), OR
      2) matches a hardcoded sensitive pattern (even inside allow-list).

    mode: 'read' checks read_paths; 'write' checks write_paths.
    """
    resolved = path.resolve()

    if mode == "write":
        scope = get_write_paths()
        scope_name = "write"
        hint = "Use /permissions to grant write access to this folder."
    else:
        scope = get_read_paths()
        scope_name = "read"
        hint = "Use /permissions to grant read access to this folder."

    if not _is_inside(path, scope):
        raise PermissionDenied(
            f"Access denied ({scope_name}): '{path}' is outside the {scope_name} scope. {hint}"
        )

    sensitive_reason = _matches_sensitive(resolved)
    if sensitive_reason:
        raise PermissionDenied(
            f"Access denied: '{path}' is blocked ({sensitive_reason}). "
            "Sensitive files are hardcoded off-limits regardless of allow-list."
        )


MAX_READ_BYTES = 10 * 1024 * 1024  # 10 MB — big enough for any sane text file, small enough that a rogue read can't blow the LLM context


def read_file(path_str: str) -> str:
    """Read a text file. Raises PermissionDenied if outside read scope."""
    path = Path(path_str)
    _guard(path, mode="read")
    if not path.exists():
        return f"Error: file not found — {path}"
    if not path.is_file():
        return f"Error: '{path}' is a directory, not a file."
    try:
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            return (
                f"Error: file too large — {size:,} bytes "
                f"(limit is {MAX_READ_BYTES:,} bytes / 10 MB). "
                f"Ask me to summarize with a targeted search or grep instead."
            )
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading file: {e}"


def list_dir(path_str: str) -> list[str]:
    """List contents of a directory. Raises PermissionDenied if outside read scope."""
    path = Path(path_str)
    _guard(path, mode="read")
    if not path.exists():
        return [f"Error: directory not found — {path}"]
    if not path.is_dir():
        return [f"Error: '{path}' is a file, not a directory."]
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        return [f"{'[DIR] ' if e.is_dir() else '      '}{e.name}" for e in entries]
    except PermissionError:
        return [f"Error: OS-level permission denied for '{path}'."]


MAX_FIND_RESULTS = 200  # cap per query — a broad glob across C:\ can return millions


def find_files(pattern: str, extension: str = "", path_hint: str = "") -> list[str]:
    """
    Search for files by filename pattern across all read-scoped folders.

    pattern:    substring or glob to match against filenames (case-insensitive).
                Examples: 'marksheet', '*resume*', 'IMG_202?'.
    extension:  optional filter like '.pdf', '.jpg' — matched case-insensitive.
                Leading dot optional.
    path_hint:  optional folder path to restrict the search to.
                Must be inside read scope. If empty, searches all read-scoped roots.

    Returns a list of matching absolute file paths (capped at MAX_FIND_RESULTS).
    Sensitive files (blacklisted) are never included even if they match.
    """
    from config import get_read_paths

    # Normalise extension filter
    ext = extension.strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext

    # Normalise pattern into a case-insensitive fnmatch glob
    pat = pattern.strip()
    if not pat:
        return ["Error: pattern is empty. Provide a substring or glob like 'marksheet' or '*.pdf'."]
    if "*" not in pat and "?" not in pat and "[" not in pat:
        # No wildcards — treat as substring match by wrapping with *
        pat = f"*{pat}*"
    pat_lower = pat.lower()

    # Determine which roots to search
    read_roots = get_read_paths()
    if not read_roots:
        return ["Error: no read scope configured. Use /permissions to grant read access."]

    if path_hint:
        hint_path = Path(path_hint).resolve()
        if not _is_inside(hint_path, read_roots):
            return [f"Error: path_hint '{path_hint}' is outside the read scope."]
        roots = [str(hint_path)]
    else:
        roots = read_roots

    results = []
    truncated = False

    for root in roots:
        root_path = Path(root)
        try:
            for entry in root_path.rglob("*"):
                if not entry.is_file():
                    continue
                name_lower = entry.name.lower()
                if not fnmatch.fnmatch(name_lower, pat_lower):
                    continue
                if ext and not name_lower.endswith(ext):
                    continue
                if _matches_sensitive(entry.resolve()):
                    continue
                results.append(str(entry))
                if len(results) >= MAX_FIND_RESULTS:
                    truncated = True
                    break
        except (PermissionError, OSError):
            # Skip roots we can't walk (e.g. OS-restricted dirs); don't leak the error
            continue
        if truncated:
            break

    if not results:
        return [f"No files matched pattern '{pattern}'" + (f" with extension '{extension}'" if extension else "")]

    if truncated:
        results.append(f"... ({MAX_FIND_RESULTS} results reached — narrow the query with path_hint or a more specific pattern)")

    return results


def write_file(path_str: str, content: str) -> str:
    """Write text to a file. Raises PermissionDenied if outside write scope."""
    path = Path(path_str)
    _guard(path, mode="write")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Written: {path}"
    except OSError as e:
        return f"Error writing file: {e}"
