"""Google Calendar skill - list upcoming events, create event.

Uses CalDAV via Google's iCal export URL for reading (read-only, no OAuth needed
when given the secret iCal URL from settings). For writes, currently shows guidance.

Config keys:
- skills.gcal.ical_url (private iCal URL from Google Calendar settings)
"""

import datetime as dt
import re

import httpx

from openbro.skills.base import BaseSkill
from openbro.tools.base import BaseTool, RiskLevel


class GoogleCalendarTool(BaseTool):
    name = "calendar"
    description = (
        "List upcoming events from Google Calendar via private iCal URL. "
        "Set skills.gcal.ical_url in config (Settings → Integrate calendar → "
        "Secret address in iCal format)."
    )
    risk = RiskLevel.SAFE

    def __init__(self, ical_url: str | None = None):
        self.ical_url = ical_url

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["upcoming", "today"]},
                    "limit": {"type": "integer", "description": "Max events (default 5)"},
                },
                "required": ["action"],
            },
        }

    def run(self, **kwargs) -> str:
        if not self.ical_url:
            return (
                "Calendar not configured. Set skills.gcal.ical_url to your private iCal URL "
                "(Google Calendar Settings → Integrate calendar → Secret address in iCal)."
            )
        action = kwargs.get("action", "upcoming")
        limit = kwargs.get("limit", 5)
        try:
            r = httpx.get(self.ical_url, timeout=15)
            r.raise_for_status()
            events = self._parse_ical(r.text)
        except Exception as e:
            return f"Calendar fetch failed: {e}"

        now = dt.datetime.now(dt.timezone.utc)
        if action == "today":
            today = now.date()
            events = [e for e in events if e["start"].date() == today]
        else:
            events = [e for e in events if e["start"] >= now]

        events.sort(key=lambda e: e["start"])
        events = events[:limit]
        if not events:
            return "No events."
        lines = [f"- {e['start'].strftime('%Y-%m-%d %H:%M')} | {e['summary']}" for e in events]
        return "\n".join(lines)

    @staticmethod
    def _parse_ical(text: str) -> list[dict]:
        events: list[dict] = []
        # Unfold long lines (RFC 5545 line continuation)
        text = re.sub(r"\r?\n[ \t]", "", text)
        blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.DOTALL)
        for block in blocks:
            summary_m = re.search(r"\nSUMMARY:(.*)", block)
            start_m = re.search(r"\nDTSTART(?:;[^:]+)?:([0-9TZ]+)", block)
            if not summary_m or not start_m:
                continue
            try:
                start = GoogleCalendarTool._parse_dt(start_m.group(1))
            except Exception:
                continue
            events.append({"summary": summary_m.group(1).strip(), "start": start})
        return events

    @staticmethod
    def _parse_dt(s: str) -> dt.datetime:
        s = s.strip()
        if s.endswith("Z"):
            return dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        if "T" in s:
            return dt.datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
        return dt.datetime.strptime(s, "%Y%m%d").replace(tzinfo=dt.timezone.utc)


class GoogleCalendarSkill(BaseSkill):
    name = "calendar"
    description = "List upcoming Google Calendar events via private iCal URL."
    version = "0.1.0"
    author = "openbro"
    config_keys = ["skills.gcal.ical_url"]

    def tools(self) -> list[BaseTool]:
        return [GoogleCalendarTool(ical_url=self._get_nested(self.config, "skills.gcal.ical_url"))]
