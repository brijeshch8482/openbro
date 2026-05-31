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

    # Captured 2026-05-31: regex matched 'abhi bhi open nhi hua hai' with
    # target='nhi hua hai' → app tool tried to launch 'nhi hua hai' and
    # reported '✓ Opened: nhi hua hai' as the answer. Bogus. Targets that
    # are pure conversational tokens (no app-name content) must be
    # rejected before dispatching.
    _CONVERSATIONAL_TARGET_RE = re.compile(
        r"^(nhi|nahi|nahin|hua|hai|ho|kya|kyo|kyu|"
        r"yaar|bhai|bro|boss|na|to|please|"
        r"ab|abhi|phir|bhi|"
        r"\?|!|\.)+(\s+(nhi|nahi|nahin|hua|hai|ho|kya|kyo|kyu|"
        r"yaar|bhai|bro|boss|na|to|please|"
        r"ab|abhi|phir|bhi|"
        r"\?|!|\.))*\s*\??\s*$",
        re.IGNORECASE,
    )

    # Past-tense narration tokens. When the captured target STARTS with
    # one of these, the user is describing a past event (not asking
    # for a launch). Captured 2026-05-31: 'maine battey backup time
    # pucha hai...ki toatal kitne ghante chala hai?' captured target
    # = 'maine battey backup time pucha hai...ki toatal kitne ghante'
    # because `chala` matched the launch verb pattern.
    _PAST_TENSE_PREFIXES = (
        "maine",
        "tune",
        "tumne",
        "aapne",
        "apne",
        "humne",
        "isne",
        "usne",
        "i ",
        "you ",
        "he ",
        "she ",
        "we ",
    )

    def execute(self, context: PlaybookContext) -> str:
        target = (context.captures.get("target") or "").strip().lower()
        if not target:
            return "_What should I open? E.g. `open chrome`._"

        # Bail on file-launch shapes — file_ops handles those better.
        for deny in self._DENY_TARGET_SUBSTRINGS:
            if deny in target:
                return ""  # empty -> registry treats as no match, falls back to LLM

        # Reject conversational fragments like 'nhi hua hai' that the
        # regex matched as a target. Let the LLM handle the actual
        # follow-up — this is feedback, not a launch request.
        if self._CONVERSATIONAL_TARGET_RE.match(target):
            return ""

        # Reject implausibly long targets. Real app names are 1-3
        # words. 'maine battey backup time pucha hai...ki toatal
        # kitne ghante' is sentence content, not an app name.
        if len(target) > 40 or len(target.split()) > 4:
            return ""

        # Reject targets containing punctuation that doesn't belong in
        # app names (`?`, `...`, `,`, `!`). A real launch command
        # ends cleanly: 'open chrome' / 'chrome kholo'.
        if any(ch in target for ch in ("?", "!", ",", "...")):
            return ""

        # Reject targets that START with a past-tense pronoun —
        # 'maine X kiya', 'tune Y dekha' — user is narrating, not
        # commanding.
        first_token = target.split()[0] if target.split() else ""
        if first_token in self._PAST_TENSE_PREFIXES:
            return ""
        # English shape: 'i pucha', 'you opened' etc — also captured
        # via the prefix list above (i ', you ').
        if any(target.startswith(prefix) for prefix in self._PAST_TENSE_PREFIXES):
            return ""

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
