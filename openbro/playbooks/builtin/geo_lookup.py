"""GeoLookupPlaybook — answers 'kaha hu mai?' in one tool call, no LLM.

Captured failure that motivated this: agent took 3 LLM round-trips +
2 tool calls + ~25K tokens to figure out the user's city by IP. That's
absurd for a deterministic 2-step lookup (get IP -> hit ipapi.co).
Playbook does both calls itself and templates the answer.
"""

from __future__ import annotations

import re

from openbro.playbooks.base import Playbook, PlaybookContext


class GeoLookupPlaybook(Playbook):
    name = "geo_lookup"
    description = "Where am I (IP-based geolocation)."
    triggers = [
        (re.compile(r"\b(mai|main)\s+(kaha|kahan)\s+hu\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bmeri\s+(city|location|jagah)\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bwhere\s+am\s+i\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bmy\s+(location|city|country)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\bcurrent\s+location\b", re.IGNORECASE), 0.9),
        (re.compile(r"\bgeoloc(ation|ate)\b", re.IGNORECASE), 0.8),
    ]
    keywords = ["kaha hu", "kahan hu", "where am i", "my location"]

    def execute(self, context: PlaybookContext) -> str:
        # Step 1: get public IP via the network tool.
        network = context.tool_registry.get_tool("network")
        if network is None:
            return "_network tool not available — can't geolocate._"
        ip_result = network.run(action="ip")
        # network tool returns 'Public IP: x.x.x.x' or an error line
        ip = self._extract_ip(ip_result)
        if not ip:
            return f"Couldn't fetch public IP (network tool said: {ip_result}). Internet down?"

        # Step 2: hit ipapi.co directly via httpx — no second LLM call,
        # no python-tool round-trip.
        geo = self._lookup_geo(ip)
        if not geo:
            return f"Got IP {ip} but ipapi.co lookup failed. Internet might be flaky."

        city = geo.get("city") or "?"
        region = geo.get("region") or ""
        country = geo.get("country_name") or geo.get("country") or "?"
        org = (geo.get("org") or "").strip()
        postal = geo.get("postal") or ""
        timezone = geo.get("timezone") or ""

        # Templated response — single source of truth, no hallucination.
        lines = [
            f"Tu **{city}, {region}, {country}** me hai. 📍",
            "",
            f"- **IP**: `{ip}`",
        ]
        if postal:
            lines.append(f"- **Postal**: {postal}")
        if timezone:
            lines.append(f"- **Timezone**: {timezone}")
        if org:
            lines.append(f"- **ISP**: {org}")
        lines.append("")
        lines.append("_(IP-based, VPN ya proxy on ho to off ho sakta hai.)_")
        return "\n".join(lines)

    @staticmethod
    def _extract_ip(text: str) -> str:
        m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text or "")
        return m.group(1) if m else ""

    @staticmethod
    def _lookup_geo(ip: str) -> dict:
        try:
            import httpx
        except ImportError:
            return {}
        try:
            r = httpx.get(f"https://ipapi.co/{ip}/json/", timeout=8.0)
            if r.status_code != 200:
                return {}
            data = r.json()
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:
            return {}
