"""
Desktop automation tools — screenshot, mouse, keyboard, clipboard, windows, apps, OCR.

Mirrors Claude's computer-use capability:
  1. Screenshot         → LLM sees the screen (vision block injected in tool result)
  2. Click / move       → LLM acts on what it sees
  3. Type / key_press   → LLM controls keyboard
  4. Scroll             → LLM navigates
  5. Clipboard          → read/write system clipboard
  6. Wait               → pause between actions
  7. Window management  → list, focus, resize, close windows
  8. Launch app         → open any program
  9. OCR find           → locate UI text on screen without hardcoding pixel coords

Screenshots are saved to data/screenshots/ and returned as file paths.
llm.py._build_messages() converts those paths to base64 vision blocks so
Claude sees the screen directly inside the tool result.

Failsafe: move mouse to top-left corner to abort any pyautogui action.
"""

import subprocess
import time
from pathlib import Path


def _pag():
    """Lazy pyautogui — 0.5s import deferred until first desktop action."""
    import pyautogui
    return pyautogui


def _pclip():
    """Lazy pyperclip — deferred until first clipboard action."""
    import pyperclip
    return pyperclip

# Tiny pause after each action so the OS can render
_ACTION_PAUSE = 0.1

_SCREENSHOT_DIR = Path(__file__).parent.parent / "data" / "screenshots"

# These extensions are recognised by llm.py as "inject as vision block"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _ensure_pause():
    _pag().PAUSE = _ACTION_PAUSE


# ── Screenshot ────────────────────────────────────────────────────────────

def screenshot(region: tuple | None = None) -> str:
    """
    Capture the screen (or a region) and save to data/screenshots/.
    Returns the absolute file path — llm.py injects this as a vision block
    so Claude can see the result.

    region: optional (left, top, width, height) tuple for a partial capture.
    """
    _ensure_pause()
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    path = _SCREENSHOT_DIR / f"screen_{ts}.png"

    img = _pag().screenshot(region=region)
    img.save(str(path))
    return str(path)


# ── Mouse ─────────────────────────────────────────────────────────────────

def mouse_move(x: int, y: int) -> str:
    """Move the mouse cursor to (x, y) in screen coordinates."""
    _pag().moveTo(x, y)
    return f"Mouse moved to ({x}, {y})."


def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """
    Click at screen coordinates (x, y).
    button: 'left' | 'right' | 'middle'
    clicks: 1 for single-click, 2 for double-click
    """
    _pag().click(x, y, button=button, clicks=clicks, interval=0.1)
    label = {1: "click", 2: "double-click"}.get(clicks, f"{clicks}-click")
    return f"{button.capitalize()} {label} at ({x}, {y})."


def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int) -> str:
    """Click and drag from (start_x, start_y) to (end_x, end_y)."""
    _pag().moveTo(start_x, start_y)
    _pag().dragTo(end_x, end_y, duration=0.4, button="left")
    return f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})."


def scroll(x: int, y: int, direction: str = "down", amount: int = 3) -> str:
    """
    Scroll at (x, y).
    direction: 'up' | 'down'
    amount: number of scroll steps (default 3)
    """
    clicks = amount if direction == "up" else -amount
    _pag().scroll(clicks, x=x, y=y)
    return f"Scrolled {direction} {amount} steps at ({x}, {y})."


# ── Keyboard ──────────────────────────────────────────────────────────────

def type_text(text: str, interval: float = 0.03) -> str:
    """
    Type a string of text using the keyboard.
    Use key_press for special keys (Enter, Ctrl+C, etc.).
    interval: seconds between keystrokes (default 0.03 — fast but reliable)
    """
    _pag().write(text, interval=interval)
    preview = text[:60] + ("..." if len(text) > 60 else "")
    return f"Typed: {preview!r}"


def key_press(keys: str) -> str:
    """
    Press a key or key combination.
    Single key:   'enter', 'escape', 'tab', 'space', 'backspace', 'delete',
                  'f5', 'home', 'end', 'pageup', 'pagedown'
    Combination:  'ctrl+c', 'ctrl+v', 'ctrl+z', 'alt+f4', 'win+d', 'ctrl+shift+t'
    Multiple sequential keys: separate with commas — 'enter,enter,tab'
    """
    for combo in keys.split(","):
        combo = combo.strip()
        parts = [p.strip().lower() for p in combo.split("+")]
        if len(parts) == 1:
            _pag().press(parts[0])
        else:
            _pag().hotkey(*parts)
        time.sleep(0.05)
    return f"Key pressed: {keys}"


# ── Screen info ───────────────────────────────────────────────────────────

def get_screen_size() -> str:
    """Return the screen resolution in pixels."""
    w, h = _pag().size()
    return f"Screen size: {w} × {h} pixels."


def get_mouse_position() -> str:
    """Return the current mouse cursor position."""
    x, y = _pag().position()
    return f"Mouse is at ({x}, {y})."


# ── Clipboard ─────────────────────────────────────────────────────────────

def clipboard_write(text: str) -> str:
    """
    Copy text to the system clipboard.
    After this, the user (or a subsequent key_press 'ctrl+v') can paste it anywhere.
    """
    _pclip().copy(text)
    preview = text[:120] + ("..." if len(text) > 120 else "")
    return f"Copied to clipboard ({len(text)} chars): {preview!r}"


def clipboard_read() -> str:
    """
    Read whatever is currently in the system clipboard.
    Returns the text content (max 2000 chars shown).
    """
    content = _pclip().paste()
    if not content:
        return "Clipboard is empty."
    preview = content[:2000] + (f"\n... [{len(content) - 2000} more chars]" if len(content) > 2000 else "")
    return f"Clipboard content ({len(content)} chars):\n{preview}"


# ── Wait ──────────────────────────────────────────────────────────────────

def wait(seconds: float) -> str:
    """
    Pause execution for N seconds (max 15).
    Use between actions when the OS or an app needs time to respond —
    e.g. after opening a program, after a page load, after a dialog appears.
    """
    seconds = min(float(seconds), 15.0)
    time.sleep(seconds)
    return f"Waited {seconds:.1f}s."


# ── Window management ─────────────────────────────────────────────────────

def list_windows() -> str:
    """
    List all visible, non-empty windows currently open on the desktop.
    Returns title and (left, top, width, height) for each.
    Use this to find the exact window title before calling focus_window.
    """
    try:
        import pygetwindow as gw
        windows = [w for w in gw.getAllWindows() if w.title.strip()]
        if not windows:
            return "No open windows found."
        lines = [f"[{i+1}] {w.title!r}  at ({w.left},{w.top}) size {w.width}×{w.height}"
                 for i, w in enumerate(windows[:30])]
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing windows: {e}"


def focus_window(title: str) -> str:
    """
    Bring a window to the foreground by (partial) title match.
    Use list_windows first to get exact titles.
    """
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower() and w.title.strip()]
        if not matches:
            return f"No window found matching '{title}'. Run list_windows to see open windows."
        win = matches[0]
        try:
            win.restore()
        except Exception:
            pass
        try:
            win.activate()
        except Exception as e:
            # pygetwindow on Windows sometimes raises "Error code from Windows: 0"
            # which is actually success (error code 0 = ERROR_SUCCESS). Ignore it.
            if "Error code from Windows: 0" not in str(e):
                return f"Error focusing window: {e}"
        time.sleep(0.3)
        return f"Focused: {win.title!r}"
    except Exception as e:
        return f"Error focusing window: {e}"


def minimize_window(title: str) -> str:
    """Minimize a window by (partial) title match."""
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
        if not matches:
            return f"No window found matching '{title}'."
        matches[0].minimize()
        return f"Minimized: {matches[0].title!r}"
    except Exception as e:
        return f"Error: {e}"


def maximize_window(title: str) -> str:
    """Maximize a window by (partial) title match."""
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
        if not matches:
            return f"No window found matching '{title}'."
        matches[0].maximize()
        return f"Maximized: {matches[0].title!r}"
    except Exception as e:
        return f"Error: {e}"


def close_window(title: str) -> str:
    """Close a window by (partial) title match. Sends a close signal — the app may prompt to save."""
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
        if not matches:
            return f"No window found matching '{title}'."
        name = matches[0].title
        matches[0].close()
        return f"Close signal sent to: {name!r}"
    except Exception as e:
        return f"Error: {e}"


def screenshot_window(title: str) -> str:
    """
    Screenshot a specific window by (partial) title match.
    Returns the image path (injected as a vision block so Claude sees it).
    Use list_windows first if unsure of the title.
    """
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower() and w.title.strip()]
        if not matches:
            return f"No window found matching '{title}'."
        win = matches[0]
        region = (win.left, win.top, win.width, win.height)
        return screenshot(region=region)
    except Exception as e:
        return f"Error: {e}"


# ── Launch application ────────────────────────────────────────────────────

def launch_app(command: str) -> str:
    """
    Launch an application or open a file/URL.
    Examples:
      'notepad'                       → opens Notepad
      'calc'                          → opens Calculator
      'chrome'                        → opens Chrome
      'C:\\\\path\\\\to\\\\app.exe'   → opens any executable
      'https://google.com'            → opens URL in default browser
      'C:\\\\Users\\\\me\\\\doc.pdf'  → opens file in default app

    The app launches in the background — use wait() then screenshot() to
    confirm it opened before interacting with it.
    """
    try:
        if command.startswith("http://") or command.startswith("https://"):
            import webbrowser
            webbrowser.open(command)
            return f"Opened URL in default browser: {command}"
        subprocess.Popen(command, shell=True)
        return f"Launched: {command!r}. Use wait(2) then screenshot() to confirm it opened."
    except Exception as e:
        return f"Error launching '{command}': {e}"


# ── OCR element finder ────────────────────────────────────────────────────

def find_text_on_screen(text: str, screenshot_path: str | None = None) -> str:
    """
    Find where specific text appears on the current screen using OCR.
    Returns the (x, y) center coordinates of the first match — ready to pass
    to mouse_click without manually reading pixel coordinates from a screenshot.

    screenshot_path: if provided, searches that image instead of taking a new one.

    Requires: pip install easyocr  (downloads ~200MB model on first use)
    """
    try:
        import warnings
        import easyocr
        import numpy as np
        from PIL import Image as PILImage
    except ImportError:
        return (
            "easyocr not installed. Run: pip install easyocr\n"
            "This enables finding UI elements by text without hardcoding coordinates."
        )

    # Take screenshot if not provided
    if screenshot_path:
        img_path = screenshot_path
    else:
        img_path = screenshot()

    try:
        img = np.array(PILImage.open(img_path).convert("RGB"))
    except Exception as e:
        return f"Error loading image: {e}"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    results = reader.readtext(img)

    text_lower = text.lower()
    matches = []
    for (bbox, detected, confidence) in results:
        if text_lower in detected.lower() and confidence > 0.3:
            # bbox is [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / 4)
            cy = int(sum(ys) / 4)
            matches.append({
                "text": detected,
                "confidence": round(confidence, 2),
                "center": (cx, cy),
                "bbox": (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
            })

    if not matches:
        return f"Text '{text}' not found on screen. Try screenshot() to see current state."

    lines = [f"Found {len(matches)} match(es) for '{text}':"]
    for i, m in enumerate(matches):
        cx, cy = m["center"]
        lines.append(
            f"  [{i+1}] '{m['text']}' — center ({cx}, {cy}), confidence {m['confidence']}"
        )
    lines.append(f"\nTo click the first match: mouse_click(x={matches[0]['center'][0]}, y={matches[0]['center'][1]})")
    return "\n".join(lines)
