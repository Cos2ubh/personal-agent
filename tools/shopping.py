"""
Shopping automation — search Amazon India and Flipkart, compare results,
and add to cart via the managed Playwright browser.

Architecture (same as IRCTC booking):
  1. search_amazon / search_flipkart  →  returns top N product cards (web scrape)
  2. Agent presents options, user picks one
  3. browser_open(product_url) → agent fills cart autonomously → stops at payment

The search step uses DuckDuckGo + site-scoped queries so we don't need API
keys. This is intentionally read-only and fast. The add-to-cart step uses
the managed browser (browser_open) so it goes through the existing Sentinel
approval gate.
"""

from __future__ import annotations


def _ddg_search(query: str, site: str, max_results: int = 5) -> list[dict]:
    """Site-scoped DuckDuckGo search. Returns list of {title, url, snippet}."""
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(f"site:{site} {query}", max_results=max_results):
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("href", ""),
                    "snippet": r.get("body", "")[:200],
                })
        return results
    except Exception as e:
        return [{"title": "Search error", "url": "", "snippet": str(e)}]


def _format_results(results: list[dict], platform: str, query: str) -> str:
    if not results:
        return f"No results found on {platform} for '{query}'."
    lines = [f"Top {platform} results for '{query}':\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        if r["url"]:
            lines.append(f"    {r['url']}")
        if r["snippet"]:
            lines.append(f"    {r['snippet']}")
        lines.append("")
    lines.append(
        f"To buy one: tell me which item number you want, and I'll open it in "
        f"the managed browser and add it to your cart autonomously (stops at payment)."
    )
    return "\n".join(lines)


# ── Public tool functions ─────────────────────────────────────────────────

def search_amazon(query: str, max_results: int = 5) -> str:
    """
    Search Amazon India for products matching the query.
    Returns a ranked list with titles, URLs, and price snippets.
    """
    query = (query or "").strip()
    if not query:
        return "Error: search query is required."
    results = _ddg_search(query, "amazon.in", min(max_results, 10))
    # Filter to product pages only (avoid category/search pages)
    product_results = [r for r in results if "/dp/" in r.get("url", "")]
    if not product_results:
        product_results = results[:5]   # fallback to raw results
    return _format_results(product_results, "Amazon India", query)


def search_flipkart(query: str, max_results: int = 5) -> str:
    """
    Search Flipkart for products matching the query.
    Returns a ranked list with titles, URLs, and price snippets.
    """
    query = (query or "").strip()
    if not query:
        return "Error: search query is required."
    results = _ddg_search(query, "flipkart.com", min(max_results, 10))
    product_results = [r for r in results if "/p/" in r.get("url", "")]
    if not product_results:
        product_results = results[:5]
    return _format_results(product_results, "Flipkart", query)


def compare_prices(query: str) -> str:
    """
    Search both Amazon India and Flipkart for the same query and return
    results side by side for price comparison.
    """
    query = (query or "").strip()
    if not query:
        return "Error: product name is required."

    amazon   = _ddg_search(query, "amazon.in",  3)
    flipkart = _ddg_search(query, "flipkart.com", 3)

    lines = [f"Price comparison for '{query}':\n"]

    lines.append("── Amazon India ──")
    for i, r in enumerate(amazon[:3], 1):
        lines.append(f"  [{i}] {r['title'][:80]}")
        if r["snippet"]:
            lines.append(f"      {r['snippet'][:120]}")
        if r["url"]:
            lines.append(f"      {r['url']}")
    if not amazon:
        lines.append("  No results found.")

    lines.append("\n── Flipkart ──")
    for i, r in enumerate(flipkart[:3], 1):
        lines.append(f"  [{i}] {r['title'][:80]}")
        if r["snippet"]:
            lines.append(f"      {r['snippet'][:120]}")
        if r["url"]:
            lines.append(f"      {r['url']}")
    if not flipkart:
        lines.append("  No results found.")

    lines.append(
        "\nTo buy: tell me the platform and item number, and I'll open it "
        "in the managed browser to add to cart (stops at payment)."
    )
    return "\n".join(lines)
