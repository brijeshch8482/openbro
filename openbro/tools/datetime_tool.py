"""Date/time tool - current time, time zones, date math."""

from datetime import datetime, timedelta, timezone

from openbro.tools.base import BaseTool, RiskLevel


class DateTimeTool(BaseTool):
    name = "datetime"
    description = "Get current date/time, calculate dates, or convert timezones"
    risk = RiskLevel.SAFE

    def run(self, action: str, days: int = 0, hours: int = 0, tz_offset: int = 0) -> str:
        action = action.lower().strip()
        if action == "now":
            return self._now(tz_offset)
        elif action == "future":
            return self._add(days, hours)
        elif action == "past":
            return self._add(-days, -hours)
        elif action == "weekday":
            return self._weekday()
        else:
            return f"Unknown action: {action}. Available: now, future, past, weekday"

    def _now(self, tz_offset: int) -> str:
        tz = timezone(timedelta(hours=tz_offset)) if tz_offset else None
        now = datetime.now(tz)
        return (
            f"Date: {now.strftime('%A, %d %B %Y')}\n"
            f"Time: {now.strftime('%I:%M:%S %p')}\n"
            f"ISO: {now.isoformat()}"
        )

    def _add(self, days: int, hours: int) -> str:
        target = datetime.now() + timedelta(days=days, hours=hours)
        return f"Target: {target.strftime('%A, %d %B %Y, %I:%M %p')}\nISO: {target.isoformat()}"

    def _weekday(self) -> str:
        return f"Today is {datetime.now().strftime('%A')}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["now", "future", "past", "weekday"],
                        "description": "Time operation",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of days for future/past calculation",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "Number of hours for future/past calculation",
                    },
                    "tz_offset": {
                        "type": "integer",
                        "description": "Timezone offset in hours (e.g. 5 for IST is +5:30, use 5)",
                    },
                },
                "required": ["action"],
            },
        }
