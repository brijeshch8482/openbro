"""Permission rules from config (harness piece 8).

    safety:
      rules:
        allow: ["shell(git status*)", "file_ops(read*)", "grep"]
        ask:   ["shell(*pip install*)"]
        deny:  ["shell(*Remove-Item*C:/Windows*)", "elevate"]

A rule is `tool` or `tool(pattern)`. The pattern is a case-insensitive
glob matched against the call's *subject* — the argument that says what
the call does (`command` for shell, `path` for file tools, `url` for web…).
`tool` may itself be a glob (`mcp_*`).

Order: deny → ask → allow. A deny never reaches the user; an ask always
prompts (even in auto mode, even after "always allow"); an allow skips the
prompt. Calls no rule matches fall through to the permission mode.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field

_RULE_RE = re.compile(r"^\s*([\w*?.\-]+)\s*(?:\((.*)\))?\s*$", re.DOTALL)
SUBJECT_KEYS = ("command", "code", "path", "file", "url", "app_name", "query", "action")


def rule_subject(args: dict | None) -> str:
    """The argument a rule pattern is matched against."""
    args = args or {}
    for key in SUBJECT_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(args, ensure_ascii=False, sort_keys=True)


@dataclass
class Rule:
    tool: str
    pattern: str | None
    raw: str

    def matches(self, tool: str, subject: str) -> bool:
        if not fnmatch.fnmatchcase(tool.lower(), self.tool.lower()):
            return False
        if self.pattern is None:
            return True
        norm = subject.replace("\\", "/").lower()
        return fnmatch.fnmatchcase(norm, self.pattern.replace("\\", "/").lower())


def parse_rule(text: str) -> Rule | None:
    m = _RULE_RE.match(text or "")
    if not m:
        return None
    pattern = m.group(2)
    return Rule(tool=m.group(1), pattern=pattern.strip() if pattern is not None else None, raw=text)


@dataclass
class PermissionRules:
    allow: list[Rule] = field(default_factory=list)
    ask: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: dict | None) -> PermissionRules:
        raw = ((config or {}).get("safety", {}) or {}).get("rules", {}) or {}
        rules = cls()
        for bucket in ("allow", "ask", "deny"):
            for text in raw.get(bucket, []) or []:
                rule = parse_rule(str(text))
                if rule is None:
                    rules.invalid.append(str(text))
                else:
                    getattr(rules, bucket).append(rule)
        return rules

    def decide(self, tool: str, args: dict | None) -> tuple[str | None, Rule | None]:
        """('deny' | 'ask' | 'allow' | None, the rule that decided)."""
        subject = rule_subject(args)
        for bucket in ("deny", "ask", "allow"):
            for rule in getattr(self, bucket):
                if rule.matches(tool, subject):
                    return bucket, rule
        return None, None

    def __bool__(self) -> bool:
        return bool(self.allow or self.ask or self.deny)
