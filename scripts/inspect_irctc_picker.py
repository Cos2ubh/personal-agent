"""
Headless DOM inspection for IRCTC's date picker.
Opens the train-search page, clicks the date input, captures a screenshot
and dumps the picker's outerHTML + a summary of matching selectors.

Not part of the shipping tool — this is a one-shot debugging helper so
we can write selectors against real markup instead of guessing.
"""

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).parent.parent / "data"
SCREENSHOT = OUT_DIR / "irctc_picker_inspect.png"
HTML_DUMP = OUT_DIR / "irctc_picker_dump.html"
SUMMARY = OUT_DIR / "irctc_picker_summary.json"

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile"
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        # Use the persistent profile (same one the agent uses) so IRCTC sees
        # existing cookies + our regular fingerprint. Headed = matches real Chrome
        # more closely. We won't interact — just load, screenshot, exit.
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()

        try:
            page.goto(
                "https://www.irctc.co.in/nget/train-search",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            page.wait_for_selector("p-autocomplete", timeout=20000)
        except Exception as e:
            print(f"Failed to load IRCTC search page: {e}")
            browser.close()
            return

        # STEP A: dismiss IRCTC's welcome / language modal — it blocks all
        # clicks until closed. Try the 'En' / 'English' buttons first.
        dismiss_selectors = (
            "button:has-text('En')",
            "button:has-text('English')",
            ".ui-dialog button:has-text('En')",
            ".ui-dialog-visible button",
        )
        dismissed = False
        for sel in dismiss_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=800):
                    btn.click(timeout=2000)
                    page.wait_for_timeout(800)
                    print(f"Dismissed welcome modal via: {sel}")
                    dismissed = True
                    break
            except Exception:
                continue
        if not dismissed:
            print("Welcome modal not detected or not dismissible via known selectors")

        # STEP B: click the date input to open the picker
        try:
            page.locator("p-calendar input").first.click(timeout=10000)
            page.wait_for_timeout(1500)
        except Exception as e:
            print(f"Failed to click date input: {e}")

        # ── Dump full page HTML while picker is open (before screenshot) ──
        # The picker may close if focus shifts, so grab everything up front.
        try:
            full_html = page.content()
            (OUT_DIR / "irctc_full_page.html").write_text(full_html, encoding="utf-8")
            print(f"Full page HTML dumped ({len(full_html):,} chars)")
        except Exception as e:
            print(f"Full HTML dump failed: {e}")

        # Screenshot after HTML dump (still hopefully within picker's open state)
        try:
            page.screenshot(path=str(SCREENSHOT), full_page=True)
            print(f"Screenshot: {SCREENSHOT}")
        except Exception as e:
            print(f"Screenshot failed: {e}")

        # Dump the picker's outerHTML (try several possible root selectors)
        picker_html = ""
        for sel in [
            ".ui-datepicker",
            ".p-datepicker",
            "p-calendar .ui-inputtext ~ *",
            "p-calendar",
            "div[role='dialog']",
        ]:
            try:
                node = page.locator(sel).first
                if node.count() > 0:
                    picker_html = node.evaluate("el => el.outerHTML")
                    if picker_html:
                        print(f"Picker DOM found via selector: {sel} ({len(picker_html)} chars)")
                        break
            except Exception:
                continue

        HTML_DUMP.write_text(picker_html or "(no picker found)", encoding="utf-8")

        # ── Find the actual picker by hunting for the month header text ──
        # Screenshot shows "August 2026" is visible — find that element and
        # walk up the DOM to understand the picker's real class names.
        explore_query = """() => {
            const monthYearRegex = /(January|February|March|April|May|June|July|August|September|October|November|December)\\s+20\\d{2}/i;
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const candidates = [];
            let node;
            while ((node = walker.nextNode())) {
                const t = (node.textContent || '').trim();
                if (monthYearRegex.test(t) && t.length < 50) {
                    // Walk up to 6 ancestors, capturing class + tag
                    let el = node.parentElement;
                    const chain = [];
                    for (let i = 0; i < 6 && el; i++) {
                        chain.push({
                            tag: el.tagName.toLowerCase(),
                            class: el.className || '',
                            id: el.id || '',
                        });
                        el = el.parentElement;
                    }
                    candidates.push({ text: t, chain });
                }
            }
            // Also list all button-like elements in the picker vicinity
            const buttons = Array.from(document.querySelectorAll('button, a[role="button"]'))
                .filter(b => {
                    const cls = (b.className || '').toLowerCase();
                    const txt = (b.textContent || '').trim();
                    return cls.includes('calendar') || cls.includes('picker') ||
                           cls.includes('datepick') || cls.includes('next') ||
                           cls.includes('prev') || /^[\\d]+$/.test(txt);
                })
                .slice(0, 40)
                .map(b => ({
                    tag: b.tagName.toLowerCase(),
                    class: b.className || '',
                    text: (b.textContent || '').trim().slice(0, 40),
                    aria: b.getAttribute('aria-label') || null,
                }));
            return { headerCandidates: candidates, pickerButtons: buttons };
        }"""
        try:
            explore = page.evaluate(explore_query)
            (OUT_DIR / "irctc_picker_explore.json").write_text(
                json.dumps(explore, indent=2), encoding="utf-8"
            )
            print(f"Exploration: {OUT_DIR / 'irctc_picker_explore.json'}")
        except Exception as e:
            print(f"Exploration failed: {e}")

        # Query a bunch of candidate selectors to see which actually resolve
        summary_query = """() => {
            const targets = [
                '.ui-datepicker',
                '.p-datepicker',
                '.ui-datepicker-title',
                '.p-datepicker-title',
                '.ui-datepicker-header',
                '.p-datepicker-header',
                '.ui-datepicker-month',
                '.p-datepicker-month',
                '.ui-datepicker-year',
                '.p-datepicker-year',
                '.ui-datepicker-next',
                '.p-datepicker-next',
                '.ui-datepicker-prev',
                '.p-datepicker-prev',
                '.ui-datepicker-calendar',
                '.p-datepicker-calendar',
                'p-calendar',
                'p-calendar input',
                'span.p-datepicker-title',
                'button.p-datepicker-next',
            ];
            const out = {};
            for (const t of targets) {
                const el = document.querySelector(t);
                if (el) {
                    out[t] = {
                        exists: true,
                        tag: el.tagName.toLowerCase(),
                        class: el.className,
                        text: (el.textContent || '').trim().slice(0, 120),
                        aria: el.getAttribute('aria-label') || null,
                    };
                } else {
                    out[t] = { exists: false };
                }
            }
            // Also grab any element whose class contains "datepicker"
            const dp = Array.from(document.querySelectorAll('*'))
                .filter(el => el.className && typeof el.className === 'string' && el.className.toLowerCase().includes('datepick'))
                .slice(0, 30)
                .map(el => ({
                    tag: el.tagName.toLowerCase(),
                    class: el.className,
                    text: (el.textContent || '').trim().slice(0, 80),
                }));
            out['_all_datepicker_matches'] = dp;
            return out;
        }"""

        try:
            summary = page.evaluate(summary_query)
        except Exception as e:
            summary = {"error": str(e)}

        SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Summary: {SUMMARY}")
        print(f"HTML dump: {HTML_DUMP}")

        context.close()

if __name__ == "__main__":
    main()
