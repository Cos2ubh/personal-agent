"""
Browser automation via Playwright — Layer 3 of the ticket-booking stack.

Opens a real Chrome window with a persistent user profile at
data/browser_profile/, so:
  - You log into IRCTC (or BookMyShow, MMT, etc.) once; cookies persist
    across agent restarts.
  - Playwright drives the browser (typing, clicking, waiting for elements).
  - You handle CAPTCHAs and OTPs when the flow pauses. The browser is
    visible on screen — the agent isn't hiding anything.

Currently automates: IRCTC train search form. Extend as needed for other
sites. Each site's DOM is specific enough that generic "book a ticket" is
hard; per-site workflows are the pragmatic choice.

Design notes:
- HEADED mode (headless=False) — IRCTC actively detects headless Chrome.
- Persistent context (launch_persistent_context) — cookies survive restarts.
- Basic stealth via playwright-stealth — hides navigator.webdriver and a
  handful of other common bot-signals. Not bulletproof; sites that harden
  CAPTCHA + fingerprinting will still detect us.
- Selectors are as of 2026-08 build date. If IRCTC redesigns the form,
  selectors need updating — use the screenshot-on-error path to inspect
  the current DOM.

First-run setup:
    .\\venv\\Scripts\\playwright.exe install chromium

After that, the browser binary lives in ~/AppData/Local/ms-playwright/ and
is reused across all Playwright projects on this machine.
"""

import atexit
from pathlib import Path

# Playwright is imported lazily so the module loads even if the browser
# binary isn't installed yet (e.g. on a fresh clone before setup).
_pw = None
_context = None
_page = None

PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile"
_SCREENSHOT_ON_ERROR = Path(__file__).parent.parent / "data" / "browser_error.png"


def _import_playwright():
    """Import playwright.sync_api on demand; return None if not installed."""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        return None


def _ensure_browser():
    """
    Start Playwright + a persistent Chrome context if we don't have one.
    Reuses the existing page across tool calls within a single agent session.
    Returns the Page, or raises RuntimeError with a friendly message on failure.
    """
    global _pw, _context, _page

    if _page is not None and not _page.is_closed():
        return _page

    sync_playwright = _import_playwright()
    if sync_playwright is None:
        raise RuntimeError(
            "Playwright is not installed. Run:\n"
            "  .\\venv\\Scripts\\pip install playwright playwright-stealth\n"
            "  .\\venv\\Scripts\\playwright install chromium"
        )

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    if _pw is None:
        _pw = sync_playwright().start()

    try:
        _context = _pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport=None,
            args=["--start-maximized"],
        )
    except Exception as e:
        raise RuntimeError(
            f"Could not launch Chromium: {e}. "
            f"You may need to run: .\\venv\\Scripts\\playwright install chromium"
        )

    _page = _context.pages[0] if _context.pages else _context.new_page()

    # Apply stealth patches (best-effort — API varies across versions)
    try:
        from playwright_stealth import Stealth
        Stealth().apply_stealth_sync(_page.context)
    except Exception:
        # Older playwright-stealth API
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(_page)
        except Exception:
            pass  # continue without stealth if the API changed again

    atexit.register(_shutdown)
    return _page


def _shutdown():
    """Close the browser context cleanly on agent exit."""
    global _pw, _context, _page
    try:
        if _context is not None:
            _context.close()
    except Exception:
        pass
    try:
        if _pw is not None:
            _pw.stop()
    except Exception:
        pass
    _context = _page = _pw = None


def _capture_error_screenshot(prefix: str = "browser") -> str:
    """Save a screenshot of the current browser state. Returns short note."""
    try:
        if _page is not None and not _page.is_closed():
            path = _SCREENSHOT_ON_ERROR.parent / f"{prefix}_error.png"
            _page.screenshot(path=str(path), full_page=False)
            return f" Screenshot saved to {path}."
    except Exception:
        pass
    return ""


# ── Public tool: browser_open ────────────────────────────────────────────

def open_page(url: str) -> str:
    """
    Open a URL in the managed Playwright browser and return the page title.
    Persistent session — cookies from previous logins are still present.
    """
    url = (url or "").strip()
    if not url:
        return "Error: url is empty."
    if not (url.startswith("http://") or url.startswith("https://")):
        return "Error: URL must start with http:// or https://"

    try:
        page = _ensure_browser()
    except RuntimeError as e:
        return f"Error: {e}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        return f"Error navigating to {url}: {type(e).__name__}: {e}"

    try:
        title = page.title() or "(untitled)"
    except Exception:
        title = "(could not read title)"

    return f"Opened {url} in managed browser. Page title: {title}"


# ── IRCTC train search ──────────────────────────────────────────────────

_IRCTC_URL = "https://www.irctc.co.in/nget/train-search"

# Class-code map — IRCTC uses these short codes in the class dropdown.
# Accepts common aliases; falls back to sleeper class (SL) if unrecognized.
_CLASS_ALIASES = {
    "sl": "SL", "sleeper": "SL",
    "3a": "3A", "3ac": "3A", "third ac": "3A",
    "2a": "2A", "2ac": "2A", "second ac": "2A",
    "1a": "1A", "1ac": "1A", "first ac": "1A",
    "cc": "CC", "chair car": "CC",
    "2s": "2S", "second sitting": "2S",
    "ec": "EC", "executive chair car": "EC",
    "fc": "FC", "first class": "FC",
}


def _normalize_class(travel_class: str) -> str:
    return _CLASS_ALIASES.get(travel_class.strip().lower(), "SL")


def _normalize_date(journey_date: str) -> str:
    """Accept DD/MM/YYYY or DD-MM-YYYY, return DD-MM-YYYY (IRCTC's format)."""
    d = (journey_date or "").strip().replace("/", "-")
    return d


def search_irctc_train(
    from_station: str,
    to_station: str,
    journey_date: str,
    travel_class: str = "SL",
) -> str:
    """
    Fill IRCTC's train search form and submit.

    Args:
        from_station: Full name or code (e.g. "Dehradun", "DDN")
        to_station:   Same
        journey_date: DD-MM-YYYY or DD/MM/YYYY
        travel_class: SL / 3A / 2A / 1A / CC / 2S / EC / FC (default SL)

    Returns a status string describing what happened.
    """
    from_input = (from_station or "").strip()
    to_input = (to_station or "").strip()
    date_input = _normalize_date(journey_date)
    class_code = _normalize_class(travel_class)

    if not from_input or not to_input:
        return "Error: from_station and to_station are both required."
    if not date_input:
        return "Error: journey_date is required (format DD-MM-YYYY)."

    try:
        page = _ensure_browser()
    except RuntimeError as e:
        return f"Error: {e}"

    try:
        page.goto(_IRCTC_URL, wait_until="domcontentloaded", timeout=30000)
        # PrimeNG autocomplete renders p-autocomplete components; wait for one
        page.wait_for_selector("p-autocomplete", timeout=15000)
    except Exception as e:
        note = _capture_error_screenshot("irctc_load")
        return (
            f"Error loading IRCTC search page: {type(e).__name__}: {e}."
            f"{note} IRCTC may be down or slow — try again."
        )

    # Helper: fill a PrimeNG autocomplete field by typing then clicking the
    # first suggestion. IRCTC uses formcontrolname='origin' and 'destination'.
    def _fill_station(formcontrolname: str, value: str, label: str):
        field = page.locator(f"p-autocomplete[formcontrolname='{formcontrolname}'] input").first
        field.click()
        field.fill("")   # clear any existing value
        field.type(value, delay=50)  # simulate human typing
        # Wait for the autocomplete panel and pick first match
        page.wait_for_selector(".ui-autocomplete-items li", timeout=5000)
        page.locator(".ui-autocomplete-items li").first.click()

    try:
        _fill_station("origin", from_input, "From")
        _fill_station("destination", to_input, "To")
    except Exception as e:
        note = _capture_error_screenshot("irctc_station")
        return (
            f"Error filling station fields: {type(e).__name__}: {e}."
            f"{note} IRCTC's autocomplete may have changed — solve the search "
            f"manually in the open browser window."
        )

    # Date field — IRCTC's p-calendar accepts typed dates
    try:
        date_field = page.locator("p-calendar input").first
        date_field.click()
        date_field.fill(date_input)
        page.keyboard.press("Escape")   # close the calendar popup if open
    except Exception as e:
        note = _capture_error_screenshot("irctc_date")
        return (
            f"Error setting journey date: {type(e).__name__}: {e}.{note}"
        )

    # Class dropdown — PrimeNG p-dropdown
    try:
        class_dropdown = page.locator("p-dropdown[formcontrolname='journeyClass']").first
        class_dropdown.click()
        page.wait_for_selector(".ui-dropdown-items li", timeout=5000)
        # PrimeNG dropdown items have text like "Sleeper (SL)"; match on the code
        option = page.locator(
            f".ui-dropdown-items li:has-text('({class_code})')"
        ).first
        option.click()
    except Exception as e:
        # Non-fatal — search can proceed with default class
        pass

    # Search button — text "Find Trains" or "Search Trains"
    try:
        button = page.locator("button:has-text('Find Trains'), button:has-text('Search Trains')").first
        button.click()
    except Exception as e:
        note = _capture_error_screenshot("irctc_search_btn")
        return (
            f"Error clicking search button: {type(e).__name__}: {e}."
            f"{note} Form was filled — click Search in the browser to see results."
        )

    # Give results a moment to render
    try:
        page.wait_for_timeout(3000)
    except Exception:
        pass

    return (
        f"IRCTC search executed: {from_input} → {to_input} on {date_input} "
        f"({class_code}). Results loaded in the browser window. "
        f"If you see a CAPTCHA on the results page, solve it there and click "
        f"Search again. Pick a train and continue booking in the browser."
    )
