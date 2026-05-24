"""Web search and fetch tool."""

import httpx

from openbro.tools.base import BaseTool, RiskLevel


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
        # Basic DuckDuckGo instant answer API (no API key needed)
        try:
            resp = httpx.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
                timeout=10,
            )
            data = resp.json()
            results = []
            if data.get("Abstract"):
                results.append(f"Summary: {data['Abstract']}")
                results.append(f"Source: {data.get('AbstractURL', '')}")
            for topic in data.get("RelatedTopics", [])[:5]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(f"- {topic['Text'][:200]}")
            if not results:
                return f"No instant results for '{query}'. Try a more specific query."
            return "\n".join(results)
        except Exception as e:
            return f"Search error: {e}"

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
