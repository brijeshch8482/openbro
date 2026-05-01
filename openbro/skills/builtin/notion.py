"""Notion skill - search pages, read page, create page.

Requires: skills.notion.token (Notion integration token).
"""

import httpx

from openbro.skills.base import BaseSkill
from openbro.tools.base import BaseTool, RiskLevel

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


class NotionTool(BaseTool):
    name = "notion"
    description = (
        "Search Notion pages, read a page, or create a new page in a parent page. "
        "Requires Notion integration token in config."
    )
    risk = RiskLevel.MODERATE

    def __init__(self, token: str | None = None):
        self.token = token

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "read_page", "create_page"],
                    },
                    "query": {"type": "string", "description": "Search query (for search)"},
                    "page_id": {
                        "type": "string",
                        "description": (
                            "Page ID (for read_page) or parent page ID (for create_page)"
                        ),
                    },
                    "title": {"type": "string", "description": "Page title (for create_page)"},
                    "content": {
                        "type": "string",
                        "description": "Page body text (for create_page)",
                    },
                },
                "required": ["action"],
            },
        }

    def run(self, **kwargs) -> str:
        if not self.token:
            return "skills.notion.token required in config."
        action = kwargs.get("action")
        if action == "search":
            return self._search(kwargs.get("query", ""))
        if action == "read_page":
            return self._read(kwargs.get("page_id", ""))
        if action == "create_page":
            return self._create(
                kwargs.get("page_id", ""),
                kwargs.get("title", ""),
                kwargs.get("content", ""),
            )
        return f"Unknown action: {action}"

    def _search(self, query: str) -> str:
        try:
            r = httpx.post(
                f"{NOTION_API}/search",
                headers=_headers(self.token),
                json={"query": query, "page_size": 10},
                timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return f"No Notion pages found for: {query}"
            lines = []
            for it in results:
                pid = it.get("id", "")
                title = self._extract_title(it)
                lines.append(f"- {title} | id: {pid}")
            return "\n".join(lines)
        except Exception as e:
            return f"Notion search failed: {e}"

    def _read(self, page_id: str) -> str:
        if not page_id:
            return "page_id required."
        try:
            r = httpx.get(
                f"{NOTION_API}/blocks/{page_id}/children",
                headers=_headers(self.token),
                params={"page_size": 50},
                timeout=15,
            )
            r.raise_for_status()
            blocks = r.json().get("results", [])
            text_parts = []
            for b in blocks:
                t = b.get("type", "")
                content = b.get(t, {})
                rich = content.get("rich_text", [])
                line = "".join(rt.get("plain_text", "") for rt in rich)
                if line:
                    text_parts.append(line)
            return "\n".join(text_parts) if text_parts else "(empty page)"
        except Exception as e:
            return f"Notion read failed: {e}"

    def _create(self, parent_id: str, title: str, content: str) -> str:
        if not parent_id or not title:
            return "page_id (parent) and title required."
        try:
            payload = {
                "parent": {"page_id": parent_id},
                "properties": {"title": [{"type": "text", "text": {"content": title}}]},
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content}}]
                        },
                    }
                ]
                if content
                else [],
            }
            r = httpx.post(
                f"{NOTION_API}/pages",
                headers=_headers(self.token),
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            return f"Created page: {r.json().get('url', '(no url)')}"
        except Exception as e:
            return f"Notion create failed: {e}"

    @staticmethod
    def _extract_title(item: dict) -> str:
        props = item.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                title_arr = prop.get("title", [])
                return "".join(t.get("plain_text", "") for t in title_arr) or "(untitled)"
        # fallback for child_page block-style
        return item.get("title", "(untitled)")


class NotionSkill(BaseSkill):
    name = "notion"
    description = "Search, read, and create Notion pages."
    version = "0.1.0"
    author = "openbro"
    config_keys = ["skills.notion.token"]

    def tools(self) -> list[BaseTool]:
        token = self._get_nested(self.config, "skills.notion.token")
        return [NotionTool(token=token)]
