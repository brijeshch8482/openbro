"""Hooks — the user's own commands at fixed points of a turn (harness piece 8).

    hooks:
      pre_tool_use:
        - matcher: "shell"          # glob over the tool name, default "*"
          command: "python D:/hooks/check_shell.py"
          timeout: 30
      post_tool_use: [...]
      user_prompt_submit: [...]
      stop: [...]

Each hook gets a JSON object on stdin (event, session_id, cwd, plus tool /
args / result / prompt / response depending on the event).

Exit codes:
- 0  ok. stdout of `user_prompt_submit` is added to this turn's context;
     stdout of `post_tool_use` is appended to the tool result the model sees.
- 2  block. `pre_tool_use`: the tool does not run and stderr is returned to
     the model as the result. `user_prompt_submit`: the turn is refused and
     stderr is shown. `stop`: the agent is NOT allowed to stop yet — stderr
     goes back to the model as feedback and the loop continues (at most
     `max_stop_continues` times per turn).
- anything else: the hook failed; it is reported and ignored.

Hooks are commands the user wrote into their own config; they run with
the user's permissions, like any script they run themselves.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field

EVENTS = ("user_prompt_submit", "pre_tool_use", "post_tool_use", "stop")


@dataclass
class Hook:
    command: str
    matcher: str = "*"
    timeout: float = 30.0


@dataclass
class HookOutcome:
    blocked: bool = False
    message: str = ""  # stderr when blocked
    context: str = ""  # stdout from successful hooks, joined
    errors: list[str] = field(default_factory=list)


class HookRunner:
    def __init__(self, hooks: dict[str, list[Hook]] | None = None, max_stop_continues: int = 3):
        self.hooks = hooks or {}
        self.max_stop_continues = max_stop_continues

    @classmethod
    def from_config(cls, config: dict | None) -> HookRunner:
        raw = (config or {}).get("hooks", {}) or {}
        hooks: dict[str, list[Hook]] = {}
        for event in EVENTS:
            for item in raw.get(event, []) or []:
                if isinstance(item, str):
                    item = {"command": item}
                if not isinstance(item, dict) or not item.get("command"):
                    continue
                hooks.setdefault(event, []).append(
                    Hook(
                        command=str(item["command"]),
                        matcher=str(item.get("matcher", "*") or "*"),
                        timeout=float(item.get("timeout", 30)),
                    )
                )
        return cls(hooks, int(raw.get("max_stop_continues", 3)))

    def has(self, event: str) -> bool:
        return bool(self.hooks.get(event))

    def run(self, event: str, payload: dict, tool: str = "") -> HookOutcome:
        outcome = HookOutcome()
        contexts = []
        for hook in self.hooks.get(event, []):
            if tool and not fnmatch.fnmatchcase(tool.lower(), hook.matcher.lower()):
                continue
            data = json.dumps({"event": event, "cwd": os.getcwd(), **payload}, default=str)
            try:
                proc = subprocess.run(
                    hook.command,
                    shell=True,
                    input=data,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=hook.timeout,
                )
            except subprocess.TimeoutExpired:
                outcome.errors.append(f"{hook.command}: timed out after {hook.timeout:g}s")
                continue
            except OSError as e:
                outcome.errors.append(f"{hook.command}: {e}")
                continue
            if proc.returncode == 0:
                if proc.stdout.strip():
                    contexts.append(proc.stdout.strip())
            elif proc.returncode == 2:
                outcome.blocked = True
                outcome.message = (proc.stderr or proc.stdout or "blocked by hook").strip()
                break
            else:
                err = (proc.stderr or proc.stdout).strip()[:300]
                outcome.errors.append(f"{hook.command}: exit {proc.returncode} {err}")
        outcome.context = "\n".join(contexts)
        return outcome
