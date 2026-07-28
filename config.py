"""
Persistent agent configuration — file system permissions and other settings.
Stored in data/agent_config.json (gitignored).
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "data" / "agent_config.json"


def _load() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def _save(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── File-system permissions ────────────────────────────────────────────────

def get_allowed_paths() -> list[str]:
    """Return list of allowed root paths (drives or folders)."""
    return _load().get("allowed_paths", [])


def set_allowed_paths(paths: list[str]):
    cfg = _load()
    cfg["allowed_paths"] = [str(Path(p)) for p in paths]
    _save(cfg)


def fs_permissions_configured() -> bool:
    return bool(get_allowed_paths())


def setup_fs_permissions():
    """
    Interactive first-run wizard.
    Lists available drives, lets user choose what the agent can access.
    """
    import string
    import os

    print("\n─── File System Access Setup ───────────────────────────────")
    print("The agent will only be able to read files inside folders you allow.")
    print("It cannot see or access anything outside your allowed list.\n")

    # Detect available drives on Windows
    available = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if Path(drive).exists():
            available.append(drive)

    if not available:
        # Fallback for non-Windows
        available = ["/"]

    print("Detected drives:")
    for i, d in enumerate(available, 1):
        print(f"  [{i}] {d}")

    print("\nYou can also type a specific folder path (e.g. C:\\Users\\KAUSTUBH\\Documents).")
    print("Enter numbers separated by commas, or type folder paths, or both.")
    print("Example: 1,3   or   C:\\Users\\KAUSTUBH\\Projects   or   2, C:\\CLAUDE Projects")
    print("Type 'skip' to grant no access.\n")

    allowed = []
    while not allowed:
        raw = input("Your choice: ").strip()

        if not raw:
            print("  Empty input — please choose at least one drive/folder, or type 'skip'.")
            continue

        if raw.lower() == "skip":
            print("  Skipped. Agent will have no file system access.")
            break

        if raw.startswith("/"):
            print(f"  '{raw}' looks like a slash command. Those only work in chat, not here.")
            print("  Enter drive numbers (like '2') or folder paths (like 'C:\\Users\\KAUSTUBH').")
            continue

        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(available):
                    allowed.append(available[idx])
                else:
                    print(f"  '{part}' is not a valid drive number.")
            else:
                p = Path(part)
                if p.exists():
                    allowed.append(str(p))
                else:
                    print(f"  '{part}' does not exist — skipping.")

        if not allowed:
            print("  No valid paths recognised in that input. Try again.\n")

    set_allowed_paths(allowed)

    print("\nAllowed paths saved:")
    for p in allowed:
        print(f"  ✓ {p}")
    print("────────────────────────────────────────────────────────────\n")
    return allowed
