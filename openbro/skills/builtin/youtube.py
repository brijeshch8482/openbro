"""YouTube skill - search videos and fetch transcripts.

Search uses the public YouTube search page (no API key needed).
Transcript uses the public timedtext endpoint (best-effort).
"""

import json
import re
import urllib.parse

import httpx

from openbro.skills.base import BaseSkill
from openbro.tools.base import BaseTool, RiskLevel


class YouTubeTool(BaseTool):
    name = "youtube"
    description = "Search YouTube videos and fetch transcripts (no API key needed)."
    risk = RiskLevel.SAFE

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "transcript"],
                    },
                    "query": {"type": "string", "description": "Search query"},
                    "video_id": {
                        "type": "string",
                        "description": "YouTube video ID or full URL (for transcript)",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["action"],
            },
        }

    def run(self, **kwargs) -> str:
        action = kwargs.get("action")
        if action == "search":
            return self._search(kwargs.get("query", ""), kwargs.get("limit", 5))
        if action == "transcript":
            return self._transcript(kwargs.get("video_id", ""))
        return f"Unknown action: {action}"

    def _search(self, query: str, limit: int) -> str:
        if not query:
            return "Query required."
        try:
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
            html = r.text
            # YouTube embeds initial data as JSON in the page
            match = re.search(r"var ytInitialData = ({.+?});</script>", html)
            if not match:
                return "Could not parse search results."
            data = json.loads(match.group(1))
            results = []
            try:
                contents = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"][
                    "sectionListRenderer"
                ]["contents"]
                for sec in contents:
                    items = sec.get("itemSectionRenderer", {}).get("contents", [])
                    for it in items:
                        v = it.get("videoRenderer")
                        if not v:
                            continue
                        title = "".join(
                            r["text"] for r in v.get("title", {}).get("runs", []) if "text" in r
                        )
                        vid = v.get("videoId", "")
                        ch = v.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                        results.append(f"- {title} | {ch} | https://youtu.be/{vid}")
                        if len(results) >= limit:
                            break
                    if len(results) >= limit:
                        break
            except Exception:
                pass
            return "\n".join(results) if results else f"No videos found for: {query}"
        except Exception as e:
            return f"YouTube search failed: {e}"

    def _transcript(self, video_id: str) -> str:
        if not video_id:
            return "video_id required."
        # Extract ID from URL if needed
        m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", video_id)
        if m:
            video_id = m.group(1)
        try:
            url = f"https://video.google.com/timedtext?lang=en&v={video_id}"
            r = httpx.get(url, timeout=10)
            if r.status_code != 200 or not r.text.strip():
                return (
                    f"No English transcript available for video {video_id}. "
                    "(Auto-generated transcripts often need a different endpoint.)"
                )
            # Strip XML tags
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:4000] + ("..." if len(text) > 4000 else "")
        except Exception as e:
            return f"Transcript fetch failed: {e}"


class YouTubeSkill(BaseSkill):
    name = "youtube"
    description = "Search YouTube videos and fetch transcripts."
    version = "0.1.0"
    author = "openbro"
    config_keys: list[str] = []

    def is_configured(self) -> bool:
        return True

    def tools(self) -> list[BaseTool]:
        return [YouTubeTool()]
