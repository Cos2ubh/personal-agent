"""
Persistent agent configuration — file system permissions and other settings.
Stored in data/agent_config.json (gitignored).

Permissions are split into two scopes:
  read_paths  — folders the agent can read from and list
  write_paths — folders the agent can write to or delete inside

Write scope is usually a subset of read scope. Legacy configs that used a single
`allowed_paths` field are migrated on load: both scopes get the legacy list.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "data" / "agent_config.json"


def _load() -> dict:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        cfg = {}
    # Legacy migration: single allowed_paths → both scopes
    if "allowed_paths" in cfg and "read_paths" not in cfg:
        cfg["read_paths"] = cfg["allowed_paths"]
        cfg["write_paths"] = cfg["allowed_paths"]
        del cfg["allowed_paths"]
        _save(cfg)
    return cfg


def _save(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── File-system permissions ────────────────────────────────────────────────

def get_read_paths() -> list[str]:
    return _load().get("read_paths", [])


def get_write_paths() -> list[str]:
    return _load().get("write_paths", [])


def set_read_paths(paths: list[str]):
    cfg = _load()
    cfg["read_paths"] = [str(Path(p)) for p in paths]
    _save(cfg)


def set_write_paths(paths: list[str]):
    cfg = _load()
    cfg["write_paths"] = [str(Path(p)) for p in paths]
    _save(cfg)


# Backwards-compat shims — treat legacy get/set allowed_paths as read scope
def get_allowed_paths() -> list[str]:
    return get_read_paths()


def set_allowed_paths(paths: list[str]):
    cfg = _load()
    cfg["read_paths"] = [str(Path(p)) for p in paths]
    cfg["write_paths"] = [str(Path(p)) for p in paths]
    _save(cfg)


def fs_permissions_configured() -> bool:
    return bool(get_read_paths())


# ── Wizard ────────────────────────────────────────────────────────────────

def _detect_drives() -> list[str]:
    """Return list of accessible drive roots (Windows) or ['/'] fallback."""
    import string
    available = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if Path(drive).exists():
            available.append(drive)
    return available or ["/"]


def _parse_path_input(raw: str, available: list[str]) -> list[str]:
    """Parse a wizard input line into concrete paths. Warnings printed inline."""
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(available):
                result.append(available[idx])
            else:
                print(f"  '{part}' is not a valid drive number.")
        else:
            p = Path(part)
            if p.exists():
                result.append(str(p))
            else:
                print(f"  '{part}' does not exist — skipping.")
    return result


def _prompt_scope(scope_name: str, available: list[str], read_scope: list[str] | None = None) -> list[str]:
    """
    Prompt the user for one scope (read or write).
    If read_scope is provided, offers 'same' (mirror read scope) and 'none' shortcuts.
    """
    print(f"\nWhich folders can the agent {scope_name.upper()}?")
    if read_scope is not None:
        print("  Type 'same' to use the same folders as read scope.")
        print("  Type 'none' for no write access (read-only agent).")
    print("  Type 'skip' to grant no access at all.")
    print("  Otherwise: drive numbers (e.g. '2'), folder paths, or both, comma-separated.\n")

    while True:
        raw = input(f"{scope_name.capitalize()} scope: ").strip()

        if not raw:
            print(f"  Empty input — please pick at least one drive/folder, or type 'skip'.")
            continue

        low = raw.lower()

        if low == "skip":
            print(f"  Skipped — no {scope_name} access.")
            return []

        if read_scope is not None and low == "same":
            print(f"  Using same paths as read scope: {read_scope}")
            return list(read_scope)

        if read_scope is not None and low == "none":
            print(f"  No write access — agent will be read-only.")
            return []

        if raw.startswith("/"):
            print(f"  '{raw}' looks like a slash command. Those only work in chat, not here.")
            continue

        chosen = _parse_path_input(raw, available)
        if not chosen:
            print("  No valid paths in that input. Try again.\n")
            continue
        return chosen


def setup_fs_permissions():
    """
    Interactive wizard — asks separately for READ and WRITE scope.
    Writing is guarded more tightly; most users want write to be a subset of read.
    """
    print("\n─── File System Access Setup ───────────────────────────────")
    print("The agent will only see files inside folders you allow.")
    print("Reads and writes are configured separately — you can let the agent")
    print("read broadly but only write into a narrow scratch folder.\n")

    available = _detect_drives()
    print("Detected drives:")
    for i, d in enumerate(available, 1):
        print(f"  [{i}] {d}")

    print("\nExample: '1,3' for two drives, or 'C:\\Users\\KAUSTUBH\\Projects' for a folder,")
    print("or '2, C:\\CLAUDE Projects' to mix.")

    # Read scope first
    read_paths = _prompt_scope("read", available)

    # Write scope second — with 'same' shortcut relative to read
    if read_paths:
        write_paths = _prompt_scope("write", available, read_scope=read_paths)
    else:
        print("\n(Skipping write scope — read scope is empty, so write would be too.)")
        write_paths = []

    set_read_paths(read_paths)
    set_write_paths(write_paths)

    print("\n─── Configured ─────────────────────────────────────────────")
    print("Read scope:")
    for p in read_paths or ["  (none)"]:
        print(f"  ✓ {p}")
    print("Write scope:")
    for p in write_paths or ["  (none — read-only)"]:
        print(f"  ✓ {p}")
    print("────────────────────────────────────────────────────────────\n")
    return read_paths, write_paths
