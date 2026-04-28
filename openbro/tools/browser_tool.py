"""Browser control tool - open URLs, search the web in the user's browser."""

import webbrowser
from urllib.parse import quote_plus

from openbro.tools.base import BaseTool, RiskLevel

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
    "bing": "https://www.bing.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "github": "https://github.com/search?q={}",
    "stackoverflow": "https://stackoverflow.com/search?q={}",
    "wikipedia": "https://en.wikipedia.org/wiki/Special:Search?search={}",
    "amazon": "https://www.amazon.in/s?k={}",
    "flipkart": "https://www.flipkart.com/search?q={}",
    "twitter": "https://twitter.com/search?q={}",
    "x": "https://twitter.com/search?q={}",
    "reddit": "https://www.reddit.com/search/?q={}",
}


class BrowserTool(BaseTool):
    name = "browser"
    description = (
        "Open URLs or search the web in the user's default browser. "
        "Supports Google, YouTube, GitHub, StackOverflow, Amazon, Flipkart, etc."
    )
    risk = RiskLevel.MODERATE

    def run(
        self,
        action: str,
        url: str = "",
        query: str = "",
        engine: str = "google",
    ) -> str:
        if action == "open":
            return self._open_url(url)
        elif action == "search":
            return self._search(query, engine)
        else:
            return f"Unknown action: {action}. Available: open, search"

    def _open_url(self, url: str) -> str:
        if not url:
            return "URL required"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            webbrowser.open(url, new=2)
            return f"Opened in browser: {url}"
        except Exception as e:
            return f"Failed to open browser: {e}"

    def _search(self, query: str, engine: str = "google") -> str:
        if not query:
            return "Search query required"

        engine_lower = engine.lower().strip()
        url_template = SEARCH_ENGINES.get(engine_lower)
        if not url_template:
            available = ", ".join(SEARCH_ENGINES.keys())
            return f"Unknown engine: {engine}. Available: {available}"

        url = url_template.format(quote_plus(query))
        try:
            webbrowser.open(url, new=2)
            return f"Searching '{query}' on {engine_lower}"
        except Exception as e:
            return f"Failed to open browser: {e}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "search"],
                        "description": "Action: open a URL or search the web",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to open (for 'open' action)",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (for 'search' action)",
                    },
                    "engine": {
                        "type": "string",
                        "description": (
                            "Search engine: google, duckduckgo, bing, youtube, github, "
                            "stackoverflow, wikipedia, amazon, flipkart, twitter, reddit. "
                            "Default: google"
                        ),
                    },
                },
                "required": ["action"],
            },
        }
