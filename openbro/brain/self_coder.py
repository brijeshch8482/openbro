"""Self-Coder — generate Python on demand via LLM, execute, save as skill.

Flow:
  1. User asks for X (no built-in tool, no learned skill matches)
  2. Build a code-gen prompt: 'write a Python function that does X'
  3. LLM returns code
  4. Execute as subprocess (sandbox optional via Boss mode or /safe)
  5. On success: save as a brain skill with auto-detected triggers
  6. Return the output to the user

The agent grows new tools for itself. Next time same task: skill runs
directly, no LLM call.
"""

from __future__ import annotations

import re
import textwrap

from openbro.brain.skills import SAFE_NAME_RE, SkillRegistry
from openbro.core.activity import get_bus

CODEGEN_SYSTEM = """You are a senior Python engineer.
The user describes a task. Output ONLY a Python snippet (no markdown,
no explanation) that:
- Defines `def run(**kwargs):` returning a string with the result
- Uses only the standard library + already-installed packages (httpx,
  pyyaml are usually safe)
- Handles errors gracefully (return error string, never raise)
- Is concise and self-contained
- Does NOT use input(), exec(), os.system, or shell=True
"""


def _strip_code_fence(text: str) -> str:
    """If LLM wraps code in ```python ... ```, peel it."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:python|py)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _suggest_skill_name(task: str) -> str:
    """Turn 'organize my downloads folder' → 'organize_downloads'."""
    words = re.findall(r"[a-zA-Z]+", task.lower())[:4]
    name = "_".join(w for w in words if w not in {"the", "a", "an", "my", "of", "to"})
    if not SAFE_NAME_RE.match(name):
        name = "skill_" + str(abs(hash(task)) % 100000)
    return name[:60]


def _suggest_triggers(task: str) -> list[str]:
    """Pick 2-3 keyword triggers from the task."""
    words = re.findall(r"[a-zA-Z]{4,}", task.lower())
    seen, triggers = set(), []
    for w in words:
        if w in {"please", "could", "would", "make", "create", "with", "using", "from"}:
            continue
        if w in seen:
            continue
        seen.add(w)
        triggers.append(w)
        if len(triggers) >= 3:
            break
    return triggers or [task.split()[0].lower()] if task else []


class SelfCoder:
    """Generates and executes Python skills on demand."""

    def __init__(self, llm_provider, skills: SkillRegistry):
        self.llm = llm_provider
        self.skills = skills

    def solve(
        self,
        task: str,
        sandbox: bool = False,
        save_on_success: bool = True,
    ) -> dict:
        """Generate code, run, optionally save. Returns {ok, output, code, skill_name}."""
        from openbro.llm.base import Message

        get_bus().emit("brain", f"self-coder: planning '{task[:80]}'", task=task)

        # Ask the LLM for code
        messages = [
            Message(role="system", content=CODEGEN_SYSTEM),
            Message(role="user", content=task),
        ]
        try:
            response = self.llm.chat(messages, tools=None)
            raw = response.content or ""
        except Exception as e:
            return {"ok": False, "error": f"LLM codegen failed: {e}"}

        code = _strip_code_fence(raw)
        if "def run" not in code:
            # Wrap a one-liner / loose snippet
            code = f"def run(**kwargs):\n{textwrap.indent(code, '    ')}\n    return None"

        # Save as a temporary skill, run it
        name = _suggest_skill_name(task)
        triggers = _suggest_triggers(task)
        try:
            self.skills.add(name=name, code=code, description=task, triggers=triggers)
        except ValueError as e:
            return {"ok": False, "error": str(e), "code": code}

        get_bus().emit("brain", f"self-coder: running '{name}'", sandbox=sandbox)
        result = self.skills.run(name, sandbox=sandbox)

        if not result.get("ok") and not save_on_success:
            self.skills.remove(name)
        if not result.get("ok") and save_on_success:
            # Keep failures around as low-confidence skills - reflection may improve them
            pass

        return {
            "ok": result.get("ok", False),
            "output": result.get("output", "") or result.get("error", ""),
            "code": code,
            "skill_name": name,
            "duration_ms": result.get("duration_ms", 0),
        }
