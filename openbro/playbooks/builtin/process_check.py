"""ProcessCheckPlaybook — 'is X running?' answered without LLM round-trips.

Captured failure: 'mere system me Claude run kr rha hai?' triggered 4 LLM
calls + 3 tool calls, ended with 'No claude process found' even though
Claude was running as 8 PIDs inside node.exe. This playbook routes
directly to the (already-fixed) process_tool find and templates the
output as a table.
"""

from __future__ import annotations

import re

from openbro.playbooks.base import Playbook, PlaybookContext, render_table


class ProcessCheckPlaybook(Playbook):
    name = "process_check"
    description = "Is <app> running? Lists matching processes."
    triggers = [
        # 'is X running?' / 'X chal raha?' / 'X process?'
        (
            re.compile(
                r"\b(?:is\s+)?(?P<query>[A-Za-z][\w.-]{1,40})"
                r"\s+(running|chal\s+raha|process|active)\b",
                re.IGNORECASE,
            ),
            0.9,
        ),
        # 'kya X chal raha hai'
        (
            re.compile(
                r"\bkya\s+(?P<query>[A-Za-z][\w.-]{1,40})\s+chal\s+raha\b",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'check if X is running'
        (
            re.compile(
                r"\bcheck\s+(if|whether)\s+(?P<query>[A-Za-z][\w.-]{1,40})\s+(is\s+)?running\b",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'X running hai ya nahi'
        (
            re.compile(
                r"\b(?P<query>[A-Za-z][\w.-]{1,40})\s+running\s+hai\b",
                re.IGNORECASE,
            ),
            0.9,
        ),
    ]
    # No bare keywords — too easy to false-positive. Requires a regex match
    # that names the query word, so we know WHAT to look up.
    keywords: list[str] = []

    def execute(self, context: PlaybookContext) -> str:
        query = context.captures.get("query", "").strip()
        # Filter out the trigger noise word if it leaked into the capture.
        for noise in ("the", "a", "an", "my", "any"):
            if query.lower() == noise:
                query = ""
        if not query:
            return "_Couldn't pick a process name from that. Try: 'is chrome running?'_"

        tool = context.tool_registry.get_tool("process")
        if tool is None:
            return "_process tool not available._"

        result = tool.run(action="find", query=query)
        # process.find returns either a header + lines or 'No processes matching...'
        if "No processes matching" in result or "(none)" in result.lower():
            return f"**No process matching `{query}`** is running."

        # Parse the structured "PID xxxx  name.exe  |  cmdline" lines into a table.
        rows = []
        for line in result.splitlines():
            line = line.strip()
            if not line.startswith("PID"):
                continue
            m = re.match(
                r"PID\s+(?P<pid>\d+)\s+(?P<name>[\S]+)\s*(?:\|\s*(?P<cmd>.*))?",
                line,
            )
            if not m:
                continue
            cmd = (m.group("cmd") or "").strip()
            if len(cmd) > 90:
                cmd = cmd[:90] + " …"
            rows.append(
                {
                    "PID": m.group("pid"),
                    "Name": m.group("name"),
                    "Command": cmd,
                }
            )

        if not rows:
            # Fallback: surface the raw text. Better than nothing.
            return f"**Matches for `{query}`**:\n```\n{result.strip()}\n```"
        header = f"**`{query}` running — {len(rows)} process(es):**"
        return f"{header}\n\n{render_table(rows)}"
