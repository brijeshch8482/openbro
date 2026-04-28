"""Web search and fetch tool."""

import httpx

from openbro.tools.base import BaseTool


class WebTool(BaseTool):
    name = "web"
    description = "Search the web or fetch content from a URL"

    def run(self, action: str, url: str = "", query: str = "") -> str:
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
            return "\n".join(results) if results else f"No instant results for '{query}'. Try a more specific query."
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
                        "description": "Action: fetch a URL or search the web",
                    },
                    "url": {"type": "string", "description": "URL to fetch (for fetch action)"},
                    "query": {"type": "string", "description": "Search query (for search action)"},
                },
                "required": ["action"],
            },
        }
