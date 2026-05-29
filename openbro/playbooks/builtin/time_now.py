"""TimeNowPlaybook — 'kya time hua' / 'what time is it' without an LLM call."""

from __future__ import annotations

import re
from datetime import datetime

from openbro.playbooks.base import Playbook, PlaybookContext


class TimeNowPlaybook(Playbook):
    name = "time_now"
    description = "Current local time and date."
    triggers = [
        (re.compile(r"\bkya\s+time\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bkitna\s+(time|baj)\b", re.IGNORECASE), 1.0),
        (re.compile(r"\babhi\s+(kya\s+)?time\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bwhat('?s|\s+is)?\s+the\s+time\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bcurrent\s+(time|date|day)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\baaj\s+kya\s+(din|date|tareekh)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\bwhat\s+day\s+is\s+(it|today)\b", re.IGNORECASE), 0.9),
    ]
    keywords = ["kya time", "what time", "current time"]

    def execute(self, context: PlaybookContext) -> str:
        # Local time is enough — no need for the datetime tool round-trip.
        now = datetime.now().astimezone()
        # %A = weekday name, %d %B %Y = day month year, %I:%M %p = 12hr clock
        line_time = now.strftime("%I:%M %p")
        line_date = now.strftime("%A, %d %B %Y")
        tz = str(now.tzinfo) if now.tzinfo else "(no tz info)"
        return f"**{line_time}** · {line_date}\n_(timezone: {tz})_"
