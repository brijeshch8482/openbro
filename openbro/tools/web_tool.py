"""Web search and fetch tool."""

import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from openbro.tools.base import BaseTool, RiskLevel

# DDG HTML SERP markup: each result block has class 'result__title'
# containing a link to the actual page, and class 'result__snippet' with
# the excerpt. The URL inside the link is wrapped in a redirect: e.g.
# //duckduckgo.com/l/?uddg=<urlencoded-real-url>.
_DDG_RESULT_RE = re.compile(
    r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]+)"[^>]*>(.*?)</a>'
    r"(?:.*?<a[^>]+class=\"result__snippet\"[^>]*>(.*?)</a>)?",
    re.DOTALL | re.IGNORECASE,
)


def _strip_tags(html: str) -> str:
    """Remove HTML tags + decode entities. Used to clean DDG's title and
    snippet fragments before returning them to the caller."""
    text = re.sub(r"<[^>]+>", "", html or "")
    return unescape(text).strip()


def _unwrap_ddg_redirect(href: str) -> str:
    """DDG wraps result URLs in `//duckduckgo.com/l/?uddg=<url>&...`.
    Unwrap to the real target URL so downstream fetch sees the page
    directly. Returns the input unchanged if it's not a redirect."""
    if not href:
        return ""
    # Normalize protocol-relative URLs
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        params = parse_qs(parsed.query)
        target = params.get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


def _parse_ddg_html(html: str) -> list[tuple[str, str, str]]:
    """Extract a list of (title, url, snippet) from a DuckDuckGo HTML
    SERP page. Each result link is unwrapped from DDG's redirect
    wrapper. Returns up to ~20 items in source order."""
    out: list[tuple[str, str, str]] = []
    if not html:
        return out
    for m in _DDG_RESULT_RE.finditer(html):
        url = _unwrap_ddg_redirect(m.group(1))
        title = _strip_tags(m.group(2))
        snippet = _strip_tags(m.group(3) or "")
        if not url or not url.startswith("http"):
            continue
        if title or snippet:
            out.append((title, url, snippet))
        if len(out) >= 20:
            break
    return out


def _fallback_instant_answer(query: str) -> str:
    """If the HTML SERP scrape returned nothing (DDG blocked us, or
    the query is too obscure), fall back to the instant-answer API.
    Returns a short string with at most a summary + a few related
    topics — better than crashing."""
    try:
        resp = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1},
            timeout=8,
        )
        data = resp.json()
    except Exception as e:
        return f"Search error: {e}"
    parts = []
    if data.get("Abstract"):
        parts.append(f"Summary: {data['Abstract']}")
        if data.get("AbstractURL"):
            parts.append(f"   {data['AbstractURL']}")
    for topic in (data.get("RelatedTopics") or [])[:5]:
        if isinstance(topic, dict) and topic.get("Text"):
            url = topic.get("FirstURL") or ""
            parts.append(f"- {topic['Text'][:200]}")
            if url:
                parts.append(f"   {url}")
    return "\n".join(parts) if parts else f"No web results for '{query}'."


class WebTool(BaseTool):
    name = "web"
    description = "Search the web or fetch content from a URL"
    risk = RiskLevel.SAFE

    def run(self, action: str, url: str | None = "", query: str | None = "") -> str:
        # Coerce None -> "" so we don't crash when the LLM sends `null`
        # (Groq rejects null up front, but some providers don't).
        url = url or ""
        query = query or ""
        if action == "fetch":
            return self._fetch(url)
        elif action == "search":
            return self._search(query)
        else:
            return f"Unknown action: {action}. Available: fetch, search"

    def _fetch(self, url: str) -> str:
        if not url:
            return "URL required for fetch"
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            content = resp.text[:5000]
            return f"Status: {resp.status_code}\nContent:\n{content}"
        except Exception as e:
            return f"Fetch error: {e}"

    def _search(self, query: str) -> str:
        if not query:
            return "Query required for search"
        # Real web search via DuckDuckGo's HTML-lite endpoint. The
        # instant-answer API (api.duckduckgo.com) only returns Abstract
        # + RelatedTopics with no actual result URLs — useless for
        # agents that want to fetch and synthesize real documentation.
        # html.duckduckgo.com/html returns a SERP-style page with
        # real titles + URLs + snippets that we parse out below.
        try:
            resp = httpx.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
                timeout=15,
                follow_redirects=True,
            )
        except Exception as e:
            return f"Search error: {e}"

        results = _parse_ddg_html(resp.text)
        if not results:
            # Fallback to instant-answer (might still surface SOMETHING
            # like a Wikipedia disambiguation).
            return _fallback_instant_answer(query)

        # Format as numbered list — that's what _extract_urls in the
        # tech_research playbook expects (it greps URLs out of any
        # line-based text).
        lines = []
        for i, (title, url, snippet) in enumerate(results[:8], 1):
            lines.append(f"{i}. {title}")
            lines.append(f"   {url}")
            if snippet:
                lines.append(f"   {snippet[:200]}")
        return "\n".join(lines)

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["fetch", "search"],
                        "description": (
                            "fetch=GET a URL (returns HTML/text), "
                            "search=DuckDuckGo instant-answer (returns summary). "
                            "For search OMIT 'url'; for fetch OMIT 'query'. "
                            "Never pass null — omit instead."
                        ),
                    },
                    "url": {
                        "type": "string",
                        "description": (
                            "URL to fetch — REQUIRED for action=fetch, "
                            "OMIT entirely for action=search (do not pass null)."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query — REQUIRED for action=search, "
                            "OMIT entirely for action=fetch (do not pass null)."
                        ),
                    },
                },
                "required": ["action"],
            },
        }
