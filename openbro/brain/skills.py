"""Skill registry — auto-generated executable Python workflows.

A skill is a Python file under brain/skills/ with:
  - A module docstring (free-form description + tags)
  - A run(**kwargs) -> str function
  - Optional triggers list (regex/keywords for "when to fire this")

The brain matches user prompts against skills before falling back to LLM
+ self-coder. Found skills run directly: zero LLM call, instant.

Public API:
    reg = SkillRegistry(skills_dir)
    reg.list()                  # all skills
    skill = reg.match(prompt)   # best matching skill or None
    out = reg.run(skill_name)   # execute a skill
    reg.add(name, code, triggers)  # save a new skill (e.g. from self-coder)
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

from openbro.core.activity import get_bus

SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass
class Skill:
    name: str
    path: Path
    description: str = ""
    triggers: list[str] = None  # type: ignore
    success_count: int = 0
    fail_count: int = 0

    def matches(self, prompt: str) -> int:
        """Return how strongly this skill matches the prompt (0 = no match)."""
        if not self.triggers:
            return 0
        p = prompt.lower()
        score = 0
        for t in self.triggers:
            t_lower = t.lower()
            if t_lower in p:
                score += 2
            try:
                if re.search(t, p, re.IGNORECASE):
                    score += 1
            except re.error:
                pass
        return score


class SkillRegistry:
    def __init__(self, skills_dir: Path):
        self.dir = Path(skills_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        # Make sure it's importable as a "package" by Python helpers
        init = self.dir / "__init__.py"
        if not init.exists():
            init.write_text("# OpenBro skills (auto-generated)\n")
        self._skills: dict[str, Skill] = {}
        self._load_all()

    # ─── loading ──────────────────────────────────────────────────

    def _load_all(self) -> None:
        self._skills.clear()
        for f in self.dir.glob("*.py"):
            if f.name.startswith("_"):
                continue
            try:
                self._skills[f.stem] = self._inspect(f)
            except Exception:
                continue

    def _inspect(self, path: Path) -> Skill:
        text = path.read_text(encoding="utf-8")
        # Pull the docstring as description
        m = re.search(r'^"""(.*?)"""', text, re.DOTALL | re.MULTILINE)
        desc = (m.group(1).strip() if m else "")[:300]
        # Pull triggers from a TRIGGERS = [...] line
        triggers: list[str] = []
        tm = re.search(r"^TRIGGERS\s*=\s*\[([^\]]+)\]", text, re.MULTILINE)
        if tm:
            for raw in tm.group(1).split(","):
                raw = raw.strip().strip("'\"")
                if raw:
                    triggers.append(raw)
        return Skill(name=path.stem, path=path, description=desc, triggers=triggers)

    # ─── public API ────────────────────────────────────────────────

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def match(self, prompt: str) -> Skill | None:
        """Return the highest-scoring matching skill, or None if none score > 0."""
        best: tuple[int, Skill] | None = None
        for skill in self._skills.values():
            score = skill.matches(prompt)
            if score > 0 and (best is None or score > best[0]):
                best = (score, skill)
        return best[1] if best else None

    def run(self, name: str, sandbox: bool = False, **kwargs) -> dict:
        """Execute a skill. Returns {ok, output, error, duration_ms}."""
        skill = self._skills.get(name)
        if not skill:
            return {"ok": False, "error": f"unknown skill: {name}"}

        get_bus().emit("brain", f"skill run: {name}", skill=name, sandbox=sandbox)
        start = time.time()
        try:
            if sandbox:
                result = self._run_subprocess(skill, kwargs)
            else:
                result = self._run_inproc(skill, kwargs)
            duration = int((time.time() - start) * 1000)
            if result.get("ok"):
                skill.success_count += 1
            else:
                skill.fail_count += 1
            result["duration_ms"] = duration
            get_bus().emit(
                "brain",
                f"skill done: {name} ({'ok' if result.get('ok') else 'fail'}, {duration}ms)",
            )
            return result
        except Exception as e:
            skill.fail_count += 1
            return {
                "ok": False,
                "error": str(e),
                "duration_ms": int((time.time() - start) * 1000),
            }

    def _run_inproc(self, skill: Skill, kwargs: dict) -> dict:
        spec = importlib.util.spec_from_file_location(f"skill_{skill.name}", skill.path)
        if not spec or not spec.loader:
            return {"ok": False, "error": "could not load skill module"}
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if not hasattr(module, "run"):
            return {"ok": False, "error": "skill has no run() function"}
        out = module.run(**kwargs)
        return {"ok": True, "output": str(out) if out is not None else ""}

    def _run_subprocess(self, skill: Skill, kwargs: dict) -> dict:
        # Sandbox mode: spawn python -c that imports + calls run() with kwargs as JSON
        import json

        code = (
            "import importlib.util,sys,json,os;"
            f"spec=importlib.util.spec_from_file_location('s', r'{skill.path}');"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
            f"r=m.run(**json.loads(r'''{json.dumps(kwargs)}'''));"
            "print(r if r is not None else '')"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": proc.stderr.strip()[-500:]}
            return {"ok": True, "output": proc.stdout.strip()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "skill timed out (60s)"}

    def add(
        self,
        name: str,
        code: str,
        description: str = "",
        triggers: list[str] | None = None,
    ) -> Skill:
        if not SAFE_NAME_RE.match(name):
            raise ValueError(f"invalid skill name: {name!r} (use lowercase + underscores)")
        path = self.dir / f"{name}.py"
        body = self._format_skill_file(name, code, description, triggers or [])
        path.write_text(body, encoding="utf-8")
        skill = self._inspect(path)
        self._skills[name] = skill
        get_bus().emit("brain", f"skill saved: {name}")
        return skill

    @staticmethod
    def _format_skill_file(name: str, code: str, description: str, triggers: list[str]) -> str:
        triggers_repr = "[" + ", ".join(repr(t) for t in triggers) + "]"
        # If user-supplied code already defines run(), keep as-is.
        # Otherwise wrap it in a def run().
        has_run = re.search(r"^def\s+run\s*\(", code, re.MULTILINE)
        if not has_run:
            indented = textwrap.indent(code.strip(), "    ")
            code = f"def run(**kwargs):\n{indented}\n    return None"
        header = f'"""{description or name} (auto-generated skill)"""\n'
        return f"{header}\nTRIGGERS = {triggers_repr}\n\n{code}\n"

    def remove(self, name: str) -> bool:
        skill = self._skills.pop(name, None)
        if not skill:
            return False
        try:
            skill.path.unlink()
        except OSError:
            return False
        return True
