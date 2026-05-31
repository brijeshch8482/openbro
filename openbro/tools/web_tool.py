"""Web search and fetch tool."""

import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from openbro.tools.base import BaseTool, RiskLevel

# Browsers used as User-Agent. Some search engines (Bing especially)
# return 403/empty when they detect a Python httpx client.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

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


# ─── Bing search ──────────────────────────────────────────────────────
#
# Captured 2026-05-31 user ask: 'agar user ne koi task diya hai...aur
# use 4-5 website par nhi mila...to self wo khud se decide krna ki
# task poora krna hai...llm se baat krke..aur phir new other websites
# par khoje'. The ExpansiveResearchPlaybook orchestrates the rounds;
# the engine functions below give it more places to look.

_BING_RESULT_RE = re.compile(
    # Each Bing result lives in <li class="b_algo"> containing:
    #   <h2><a href="URL">Title</a></h2>
    #   <p class="b_lineclamp..."><span>Snippet</span></p>
    r'<li[^>]*class="b_algo"[^>]*>'
    r'.*?<h2[^>]*><a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a></h2>'
    r"(?:.*?<p[^>]*>(?P<snippet>.*?)</p>)?",
    re.DOTALL | re.IGNORECASE,
)


def _parse_bing_html(html: str) -> list[tuple[str, str, str]]:
    """Extract (title, url, snippet) tuples from a Bing SERP page."""
    out: list[tuple[str, str, str]] = []
    if not html:
        return out
    for m in _BING_RESULT_RE.finditer(html):
        url = m.group("url")
        if not url or not url.startswith("http"):
            continue
        title = _strip_tags(m.group("title") or "")
        snippet = _strip_tags(m.group("snippet") or "")
        if title or snippet:
            out.append((title, url, snippet))
        if len(out) >= 20:
            break
    return out


def _bing_search(query: str) -> list[tuple[str, str, str]]:
    """Run a Bing search and return (title, url, snippet) tuples."""
    try:
        resp = httpx.get(
            "https://www.bing.com/search",
            params={"q": query},
            headers={"User-Agent": _BROWSER_UA},
            timeout=15,
            follow_redirects=True,
        )
    except Exception:
        return []
    return _parse_bing_html(resp.text)


# ─── Reddit search ────────────────────────────────────────────────────


def _reddit_search(query: str) -> list[tuple[str, str, str]]:
    """Search Reddit via its public JSON endpoint. Returns top items
    as (title, url, snippet) tuples. Reddit's search ranks by
    relevance + recency; useful for community/anecdotal sources the
    main engines downrank."""
    try:
        resp = httpx.get(
            "https://www.reddit.com/search.json",
            params={"q": query, "limit": 15, "sort": "relevance"},
            headers={"User-Agent": _BROWSER_UA},
            timeout=15,
            follow_redirects=True,
        )
        data = resp.json()
    except Exception:
        return []
    out: list[tuple[str, str, str]] = []
    for child in (data.get("data", {}).get("children") or [])[:20]:
        d = child.get("data") or {}
        title = d.get("title") or ""
        permalink = d.get("permalink") or ""
        if permalink:
            url = f"https://www.reddit.com{permalink}"
        else:
            url = d.get("url") or ""
        snippet = (d.get("selftext") or "")[:300]
        if title and url:
            out.append((title, url, snippet))
    return out


# ─── archive.org search ───────────────────────────────────────────────


def _archive_search(query: str) -> list[tuple[str, str, str]]:
    """Search archive.org's advanced search over its full collection
    of items (books, captured webpages, papers). Useful when modern
    engines have buried old or dead-but-archived sources."""
    try:
        resp = httpx.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": query,
                "fl[]": ["identifier", "title", "description", "mediatype"],
                "rows": 15,
                "page": 1,
                "output": "json",
            },
            headers={"User-Agent": _BROWSER_UA},
            timeout=15,
            follow_redirects=True,
        )
        data = resp.json()
    except Exception:
        return []
    out: list[tuple[str, str, str]] = []
    for doc in (data.get("response", {}).get("docs") or [])[:20]:
        ident = doc.get("identifier") or ""
        title = doc.get("title") or ident
        snippet = (doc.get("description") or "")[:300]
        if isinstance(title, list):
            title = " ".join(str(t) for t in title)
        if isinstance(snippet, list):
            snippet = " ".join(str(s) for s in snippet)
        if not ident:
            continue
        url = f"https://archive.org/details/{ident}"
        out.append((str(title), url, str(snippet)))
    return out


# ─── engine registry ──────────────────────────────────────────────────


_ENGINE_FUNCS = {
    "ddg": None,  # handled inline in _search (existing DDG path)
    "bing": _bing_search,
    "reddit": _reddit_search,
    "archive": _archive_search,
}


def _render_results(query: str, results: list[tuple[str, str, str]], engine: str = "ddg") -> str:
    """Render a result list (title, url, snippet) as the numbered
    text format an LLM consumer expects. Returns
    a friendly 'no results' line when empty."""
    if not results:
        return f"No web results for '{query}' on {engine}."
    lines = [f"[{engine}]"]
    for i, (title, url, snippet) in enumerate(results[:8], 1):
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet[:200]}")
    return "\n".join(lines)


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

    def run(
        self,
        action: str,
        url: str | None = "",
        query: str | None = "",
        engine: str | None = "",
    ) -> str:
        # Coerce None -> "" so we don't crash when the LLM sends `null`
        # (Groq rejects null up front, but some providers don't).
        url = url or ""
        query = query or ""
        engine = (engine or "ddg").lower()
        if action == "fetch":
            return self._fetch(url)
        elif action == "search":
            return self._search(query, engine=engine)
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

    def _search(self, query: str, engine: str = "ddg") -> str:
        if not query:
            return "Query required for search"
        # Dispatch to the engine. DDG is the default historical path
        # (kept inline so the existing test surface doesn't shift);
        # Bing/Reddit/archive are handled via the _ENGINE_FUNCS map.
        if engine in _ENGINE_FUNCS and _ENGINE_FUNCS[engine] is not None:
            results = _ENGINE_FUNCS[engine](query)
            return _render_results(query, results, engine=engine)
        # Default: DuckDuckGo HTML-lite SERP scrape.
        try:
            resp = httpx.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": _BROWSER_UA},
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
        return _render_results(query, results, engine="ddg")

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
                    "engine": {
                        "type": "string",
                        "enum": ["ddg", "bing", "reddit", "archive"],
                        "description": (
                            "Search engine. Default 'ddg' (DuckDuckGo, "
                            "broad web). 'bing' = second opinion, 'reddit' "
                            "= community/anecdotal, 'archive' = archive.org "
                            "items (old/dead-but-archived). For action=fetch "
                            "this is ignored."
                        ),
                    },
                },
                "required": ["action"],
            },
        }
