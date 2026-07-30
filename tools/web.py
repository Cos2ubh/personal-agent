"""
Web access — internet-facing tools for the agent.

Every request is time-bounded and size-capped so a slow/hostile server can't
hang the agent or blow the context. Fetched content is returned as clean text
(HTML nav/ads/scripts stripped by trafilatura).

Callers should wrap returned content with the <external_content> marker before
handing it to the LLM — see sub-chunk 2 for the prompt-injection defense.
"""

import httpx
import trafilatura

DEFAULT_TIMEOUT_SECS = 15
MAX_FETCH_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_RETURN_CHARS = 100_000           # ~25k tokens, sane for a single fetch

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "PersonalAgent/0.1"
)


def fetch(url: str, max_chars: int = MAX_RETURN_CHARS) -> str:
    """
    Fetch a URL and return clean readable text.

    Returns one of:
      - Extracted text (up to max_chars, truncated with a note if longer)
      - "Error: <reason>" string on failure

    Does NOT raise — all failures come back as strings so the LLM can reason
    about them the same way it reasons about file errors.
    """
    if not url or not isinstance(url, str):
        return "Error: url is empty."

    if not (url.startswith("http://") or url.startswith("https://")):
        return f"Error: URL must start with http:// or https:// — got '{url}'."

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT_SECS,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = client.get(url)
    except httpx.TimeoutException:
        return f"Error: request timed out after {DEFAULT_TIMEOUT_SECS}s — {url}"
    except httpx.HTTPError as e:
        return f"Error: HTTP request failed — {type(e).__name__}: {e}"

    if resp.status_code >= 400:
        return f"Error: server returned {resp.status_code} — {url}"

    # Enforce byte cap on raw response
    if len(resp.content) > MAX_FETCH_BYTES:
        return (
            f"Error: response too large ({len(resp.content):,} bytes, "
            f"cap is {MAX_FETCH_BYTES:,} bytes)."
        )

    # Extract clean text — trafilatura handles HTML → readable text
    # Fallback to raw text if extraction returns nothing (e.g. plain-text response)
    extracted = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )

    if not extracted:
        # Might be a non-HTML response; return raw text as fallback
        extracted = resp.text.strip()

    if not extracted:
        return f"Error: no readable content extracted from {url}"

    # Cap final size
    if len(extracted) > max_chars:
        extracted = extracted[:max_chars] + f"\n\n... [truncated at {max_chars:,} chars]"

    # Wrap in explicit external-content markers so the LLM treats it as data,
    # not instructions. See the system prompt clause in agent.py for the
    # matching guardrail.
    return (
        f"<external_content source=\"{resp.url}\">\n"
        f"{extracted}\n"
        f"</external_content>"
    )
