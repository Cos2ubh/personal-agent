"""
Tool registry — the single source of truth for what the LLM can invoke.

Each tool has:
  - a Gemini function declaration (schema the model sees)
  - a Python callable that executes it (respecting the FS sandbox)

To add a new tool: write the function, add a FunctionDeclaration, register it.
"""

from pathlib import Path

from google.genai import types

from tools.filesystem import read_file, list_dir, write_file, PermissionDenied
from config import get_read_paths, get_write_paths


# ── Function declarations (what Gemini sees) ─────────────────────────────

_read_file_decl = types.FunctionDeclaration(
    name="read_file",
    description=(
        "Read the full text content of a file on the user's machine. "
        "Only paths inside the user's configured allow-list are accessible. "
        "Returns file contents as a string, or an error message if the path "
        "is outside the allow-list, does not exist, or is not readable."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "path": types.Schema(
                type="STRING",
                description="Absolute file path, e.g. 'C:\\Users\\KAUSTUBH\\notes.txt'",
            ),
        },
        required=["path"],
    ),
)

_list_dir_decl = types.FunctionDeclaration(
    name="list_dir",
    description=(
        "List the files and subfolders in a directory on the user's machine. "
        "Only paths inside the user's configured allow-list are accessible. "
        "Returns a list of entry names (folders prefixed with [DIR])."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "path": types.Schema(
                type="STRING",
                description="Absolute directory path, e.g. 'C:\\CLAUDE Projects'",
            ),
        },
        required=["path"],
    ),
)

_write_file_decl = types.FunctionDeclaration(
    name="write_file",
    description=(
        "Write text content to a file on the user's machine. "
        "Overwrites the file if it exists, creates it (and parent folders) if not. "
        "Only paths inside the user's configured allow-list are accessible."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "path": types.Schema(
                type="STRING",
                description="Absolute file path to write to",
            ),
            "content": types.Schema(
                type="STRING",
                description="Text content to write",
            ),
        },
        required=["path", "content"],
    ),
)

_list_allowed_paths_decl = types.FunctionDeclaration(
    name="list_allowed_paths",
    description=(
        "Return the two permission scopes the user has configured: read_paths "
        "(folders the agent can read from and list) and write_paths (folders "
        "the agent can write to or delete inside). Write scope is usually a "
        "subset of read scope. Use this when the user asks what the agent can "
        "see or when unsure whether a path is allowed."
    ),
    parameters=types.Schema(type="OBJECT", properties={}),
)


# The Tool bundle passed to Gemini
FS_TOOL = types.Tool(function_declarations=[
    _read_file_decl,
    _list_dir_decl,
    _write_file_decl,
    _list_allowed_paths_decl,
])

ALL_TOOLS = [FS_TOOL]


# Tools that mutate the file system — the agent loop must confirm with the
# user before executing these. Keep this set narrow; adding a name here is a
# statement that the operation is destructive and non-recoverable.
DESTRUCTIVE_TOOLS = {"write_file"}


def preview_action(name: str, args: dict) -> str:
    """
    Return a short, human-readable preview of what a destructive tool would do.
    Used by the agent loop to render an approval prompt.
    """
    if name == "write_file":
        path_str = args.get("path", "?")
        content = args.get("content", "")
        try:
            existing = Path(path_str).stat().st_size
            existing_note = f"OVERWRITE existing file ({existing:,} bytes)"
        except (FileNotFoundError, OSError):
            existing_note = "create new file"

        preview_len = 300
        content_preview = content[:preview_len]
        if len(content) > preview_len:
            content_preview += f"\n... [+{len(content) - preview_len} more chars]"

        return (
            f"  path:    {path_str}\n"
            f"  action:  {existing_note}\n"
            f"  size:    {len(content):,} chars\n"
            f"  preview: |\n"
            f"    {content_preview.replace(chr(10), chr(10) + '    ')}"
        )
    return f"  (no preview available for '{name}')"


# ── Dispatcher: name → executor ──────────────────────────────────────────

def _wrap(fn):
    """Wrap a callable so PermissionDenied becomes a returnable string."""
    def inner(**kwargs):
        try:
            return fn(**kwargs)
        except PermissionDenied as e:
            return f"PermissionDenied: {e}"
        except TypeError as e:
            return f"Error: bad arguments to tool — {e}"
    return inner


def _list_allowed_paths_impl():
    read = get_read_paths()
    write = get_write_paths()
    if not read and not write:
        return "No paths configured — the agent has no file system access."
    lines = ["Read scope:"]
    lines.extend(f"  - {p}" for p in (read or ["(none)"]))
    lines.append("Write scope:")
    lines.extend(f"  - {p}" for p in (write or ["(none — read-only)"]))
    return "\n".join(lines)


TOOL_DISPATCH = {
    "read_file":         _wrap(lambda path: read_file(path)),
    "list_dir":          _wrap(lambda path: "\n".join(list_dir(path))),
    "write_file":        _wrap(lambda path, content: write_file(path, content)),
    "list_allowed_paths": _wrap(lambda: _list_allowed_paths_impl()),
}


def execute_tool(name: str, args: dict) -> str:
    """
    Execute a tool by name with the given arguments.
    Always returns a string suitable to feed back to the LLM.
    """
    if name not in TOOL_DISPATCH:
        return f"Error: unknown tool '{name}'"
    try:
        return str(TOOL_DISPATCH[name](**args))
    except Exception as e:
        return f"Error executing {name}: {type(e).__name__}: {e}"
