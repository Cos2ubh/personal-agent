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
import time
from pathlib import Path

# Retry policy for flaky steps (overlays, network latency, autocomplete lag).
# 10 attempts × ~5s each ≈ 50s worst-case per step before hard failure.
# Enough headroom that transient blips retry silently, but capped so a truly
# stuck operation (login modal open, DOM changed, IRCTC down) fails fast
# enough to be actionable.
_STEP_RETRIES = 10
_STEP_RETRY_DELAY = 1.5     # seconds between attempts
_STEP_TIMEOUT_MS = 4_000    # per-click timeout — short so 10 retries stay bounded


def _retry(operation_name: str, fn, retries: int = _STEP_RETRIES,
           delay: float = _STEP_RETRY_DELAY):
    """
    Retry a Playwright operation up to `retries` times with `delay` seconds
    between attempts. Returns fn() on success, raises the last error on
    persistent failure.
    """
    last_error = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(delay)
    raise RuntimeError(
        f"{operation_name}: failed after {retries} attempts "
        f"({type(last_error).__name__}: {last_error})"
    )

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


def _normalize_date(journey_date: str) -> tuple[str | None, str | None]:
    """
    Parse a journey date. Accepts natural language ('tomorrow', '2 sep',
    'next Tuesday') and structured strings (DD-MM-YYYY, DD/MM/YYYY,
    YYYY-MM-DD).

    Returns (formatted_date, None) on success — formatted as DD-MM-YYYY
    which is what IRCTC's calendar field expects.
    Returns (None, error_message) on failure (past date, unparseable).
    """
    from datetime import datetime, date

    raw = (journey_date or "").strip()
    if not raw:
        return None, "journey_date is required."

    parsed = None

    # 1. Try unambiguous structured formats first (day-month-year, ISO).
    #    Doing this before dateparser avoids DATE_ORDER guessing on strings
    #    like '2026-09-02' which dateparser might otherwise misinterpret.
    normalized = raw.replace("/", "-")
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d-%m-%y"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            break
        except ValueError:
            continue

    # 2. Fall back to dateparser for natural language ('tomorrow',
    #    'next Tuesday', '2 September'). Prefer future dates so 'sep 2'
    #    without a year resolves to the next occurrence, not the last one.
    if parsed is None:
        try:
            import dateparser
            parsed = dateparser.parse(
                raw,
                settings={
                    "PREFER_DATES_FROM": "future",
                    "STRICT_PARSING": False,
                },
            )
        except Exception:
            parsed = None

    if parsed is None:
        return None, (
            f"could not parse journey_date '{raw}'. Use DD-MM-YYYY or a "
            f"phrase like 'tomorrow' / 'next Tuesday' / '2 September'."
        )

    # Reject past dates — IRCTC silently accepts them and resets to today,
    # so we'd hand off a broken form. Better to fail fast.
    journey_d = parsed.date() if isinstance(parsed, datetime) else parsed
    today = date.today()
    if journey_d < today:
        return None, (
            f"journey_date '{raw}' parsed as {journey_d.strftime('%d-%m-%Y')} "
            f"which is in the past. IRCTC won't accept it. Use current year "
            f"(today is {today.strftime('%d-%m-%Y')}) or a future date."
        )

    return journey_d.strftime("%d-%m-%Y"), None


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
    class_code = _normalize_class(travel_class)

    if not from_input or not to_input:
        return "Error: from_station and to_station are both required."

    date_input, date_err = _normalize_date(journey_date)
    if date_err:
        return f"Error: {date_err}"

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

    # Dismiss any lingering modal / promo popup before typing.
    _dismiss_overlays(page)

    # Helper: fill a PrimeNG autocomplete field. Wrapped in _retry so
    # transient overlays or slow autocomplete network calls don't abort
    # the whole flow on the first miss.
    def _fill_station(formcontrolname: str, value: str, label: str):
        def _do():
            field = page.locator(
                f"p-autocomplete[formcontrolname='{formcontrolname}'] input"
            ).first
            field.click(timeout=_STEP_TIMEOUT_MS)
            field.fill("")
            field.type(value, delay=50)
            page.wait_for_selector(".ui-autocomplete-items li", timeout=5000)
            page.locator(".ui-autocomplete-items li").first.click(
                timeout=_STEP_TIMEOUT_MS
            )
        _retry(f"fill {label} station", _do)

    try:
        _fill_station("origin", from_input, "From")
        _fill_station("destination", to_input, "To")
    except Exception as e:
        note = _capture_error_screenshot("irctc_station")
        return (
            f"IRCTC search failed after {_STEP_RETRIES} attempts: {e}.{note} "
            f"Most common cause: a login modal or ad popup is covering the form. "
            f"Look at the browser window — dismiss any popup, log in if the "
            f"login modal is open (via ☰ menu → Login), then re-run the same "
            f"command. If the form looks unobstructed but still fails, IRCTC's "
            f"DOM may have changed — send me the screenshot at "
            f"data/irctc_station_error.png."
        )

    # ── Date field with retry ────────────────────────────────────────
    # PrimeNG's p-calendar uses an overlay picker. Typing into the input
    # doesn't reliably update the model — we have to open the calendar
    # popup and click the specific day cell, navigating months if needed.
    from datetime import datetime as _dt
    target_date = _dt.strptime(date_input, "%d-%m-%Y").date()
    target_month_header = target_date.strftime("%B %Y")   # e.g. "September 2026"
    target_day_str = str(target_date.day)

    def _fill_date():
        # 1. Click the input to open the calendar overlay
        field = page.locator("p-calendar input").first
        field.click(timeout=_STEP_TIMEOUT_MS)
        page.wait_for_selector(".ui-datepicker", timeout=5000)

        # 2. Navigate to the target month by clicking "next" until the header matches.
        #    Cap at 24 months forward so we don't loop forever if navigation breaks.
        for _ in range(24):
            try:
                current = page.locator(".ui-datepicker-title").first.text_content(timeout=1000) or ""
            except Exception:
                current = ""
            if target_month_header in current:
                break
            try:
                page.locator(".ui-datepicker-next").first.click(timeout=2000)
                page.wait_for_timeout(200)
            except Exception:
                break

        # 3. Click the day cell — filter to same-month cells only
        #    (PrimeNG shows leading/trailing days from adjacent months in gray)
        day_cell = page.locator(
            ".ui-datepicker-calendar td:not(.ui-datepicker-other-month) a"
        ).get_by_text(target_day_str, exact=True).first
        day_cell.click(timeout=_STEP_TIMEOUT_MS)

        # 4. Verify — read back the input value to confirm the pick landed
        page.wait_for_timeout(400)
        actual = field.input_value()
        expected = target_date.strftime("%d/%m/%Y")
        if actual != expected:
            raise RuntimeError(
                f"Date picker landed on '{actual}' but we wanted '{expected}'. "
                f"Calendar navigation may have overshot the target month."
            )

    try:
        _retry("set journey date", _fill_date)
    except Exception as e:
        note = _capture_error_screenshot("irctc_date")
        return (
            f"IRCTC date picker failed after {_STEP_RETRIES} attempts: {e}."
            f"{note} The calendar popup may have changed structure — send me "
            f"the screenshot and I'll update the selectors."
        )

    # ── Class dropdown (best-effort; search still runs on default) ───
    def _pick_class():
        dropdown = page.locator("p-dropdown[formcontrolname='journeyClass']").first
        dropdown.click(timeout=_STEP_TIMEOUT_MS)
        page.wait_for_selector(".ui-dropdown-items li", timeout=5000)
        page.locator(
            f".ui-dropdown-items li:has-text('({class_code})')"
        ).first.click(timeout=_STEP_TIMEOUT_MS)

    try:
        _retry("pick travel class", _pick_class)
    except Exception:
        # Non-fatal — proceed with IRCTC's default class
        pass

    # ── Click Search with retry ──────────────────────────────────────
    def _click_search():
        page.locator(
            "button:has-text('Find Trains'), button:has-text('Search Trains')"
        ).first.click(timeout=_STEP_TIMEOUT_MS)

    try:
        _retry("click search", _click_search)
    except Exception as e:
        note = _capture_error_screenshot("irctc_search_btn")
        return (
            f"Error clicking search button after {_STEP_RETRIES} attempts: "
            f"{e}.{note} Form is filled — click Search in the browser manually."
        )

    # ── Post-click state detection ───────────────────────────────────
    # After clicking Search, IRCTC does one of three things:
    #   1. Renders a list of trains below the search form (success)
    #   2. Pops up a login modal ("Please sign in to search")
    #   3. Renders "No trains available" for the given route/date
    # We race these three signals for up to 10 seconds and report back.

    try:
        page.wait_for_timeout(2000)  # give the UI a moment to react
    except Exception:
        pass

    outcome = "unknown"
    detected_msg = ""

    # Poll for a signal — check the three states over ~10 seconds
    for _ in range(10):
        try:
            # Signal A: results rendered
            if page.locator("app-train-list, .train-list, .tbl-body, table.train-heading").first.is_visible(timeout=800):
                outcome = "results"
                break
        except Exception:
            pass
        try:
            # Signal B: login modal appeared (IRCTC uses different classes across flows)
            login_modal = page.locator(
                ".login-body, [aria-label*='Login'], .ui-dialog:has-text('LOGIN'), input[placeholder*='User Name']"
            ).first
            if login_modal.is_visible(timeout=800):
                outcome = "login_required"
                break
        except Exception:
            pass
        try:
            # Signal C: "No trains" or similar empty-state text
            empty = page.locator(
                "text=/no trains|no result|please enter valid/i"
            ).first
            if empty.is_visible(timeout=800):
                outcome = "no_trains"
                try:
                    detected_msg = empty.text_content(timeout=500) or ""
                except Exception:
                    detected_msg = ""
                break
        except Exception:
            pass
        time.sleep(0.5)

    if outcome == "results":
        return (
            f"IRCTC search executed: {from_input} → {to_input} on {date_input} "
            f"({class_code}). Train results are loaded in the browser window. "
            f"Pick a train there to continue booking."
        )

    if outcome == "login_required":
        return (
            f"IRCTC search reached the login gate: {from_input} → {to_input} on "
            f"{date_input} ({class_code}). The form was filled and Search was "
            f"clicked, but IRCTC's login modal is now open in the browser. "
            f"Log in there (username + password + CAPTCHA), then re-run the "
            f"same command — the session will persist so this login is a one-time step."
        )

    if outcome == "no_trains":
        snippet = (detected_msg or "IRCTC returned no trains").strip()[:120]
        return (
            f"IRCTC returned no results for {from_input} → {to_input} on "
            f"{date_input} ({class_code}). Message from IRCTC: '{snippet}'. "
            f"Common causes: wrong date, unknown station code, or genuinely "
            f"no trains on that route. Try a different date or verify the "
            f"station names."
        )

    # Fell through the poll loop without a clear signal
    note = _capture_error_screenshot("irctc_postsearch")
    return (
        f"IRCTC search clicked but state after Search is unclear (no results "
        f"table, no login modal, no error message detected within 10s). "
        f"Check the browser window — it may have loaded slowly or IRCTC's "
        f"result markup may have changed.{note}"
    )


def _dismiss_overlays(page):
    """
    Best-effort dismissal of modals, promo popups, and ads that could block
    the form. Silent on failure — no reason to break the flow if there's
    nothing to close.
    """
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass
    for sel in (
        ".ui-dialog-titlebar-close",
        ".modal-close",
        "[aria-label='Close']",
        "button.close",
        ".pi-times",
    ):
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=1500)
                page.wait_for_timeout(200)
        except Exception:
            continue
