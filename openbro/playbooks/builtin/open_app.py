"""OpenAppPlaybook — 'open chrome' / 'X kholo' without LLM."""

from __future__ import annotations

import re

from openbro.playbooks.base import Playbook, PlaybookContext


class OpenAppPlaybook(Playbook):
    name = "open_app"
    description = "Launch an app (chrome, vscode, notepad, etc)."
    triggers = [
        (
            re.compile(
                r"\bopen\s+(?P<target>[\w \-.+]+?)\s*$",
                re.IGNORECASE,
            ),
            0.95,
        ),
        (
            re.compile(
                r"\blaunch\s+(?P<target>[\w \-.+]+?)\s*$",
                re.IGNORECASE,
            ),
            0.95,
        ),
        # 'chrome kholo' / 'vscode khol' / 'X chala do'
        (
            re.compile(
                r"\b(?P<target>[\w \-.+]+?)\s+"
                r"(khol|kholo|chala|chalu|start)\s*"
                r"(do|de|kar|karo|kr)?\b",
                re.IGNORECASE,
            ),
            1.0,
        ),
        (
            re.compile(
                r"\bstart\s+(?P<target>[\w \-.+]+?)\s*$",
                re.IGNORECASE,
            ),
            0.8,
        ),
    ]
    keywords: list[str] = []

    # Phrases that look like 'open X' but aren't actually a launch request.
    # 'open the file in D:\foo' should NOT trigger this — file_ops handles
    # that. Keeping the deny-list short and specific so we don't suppress
    # legitimate matches.
    _DENY_TARGET_SUBSTRINGS = (
        "file ",
        "pdf ",
        "the file",
        "browser",  # 'open browser' is too vague — let LLM disambiguate or use file_ops
    )

    def execute(self, context: PlaybookContext) -> str:
        target = (context.captures.get("target") or "").strip().lower()
        if not target:
            return "_What should I open? E.g. `open chrome`._"

        # Bail on file-launch shapes — file_ops handles those better.
        for deny in self._DENY_TARGET_SUBSTRINGS:
            if deny in target:
                return ""  # empty -> registry treats as no match, falls back to LLM

        # If the target has a file extension or a path separator, it's a
        # file open, not an app launch. Let file_ops handle it via LLM.
        if "." in target.split()[-1] and not target.endswith(("exe",)):
            # 'open foo.pdf' / 'open D:/x.pdf'
            return ""
        if "/" in target or "\\" in target:
            return ""

        tool = context.tool_registry.get_tool("app")
        if tool is None:
            return ""

        result = tool.run(action="open", app_name=target)
        if result.startswith("Opened"):
            return f"✓ {result}"
        if "not found" in result.lower():
            # Don't claim we did anything — be honest, suggest the find action
            return f"⚠ Couldn't find `{target}`. Try `find {target}` to locate it."
        return result
