"""Reasoning pipeline — recall → context → skill match → plan → execute → verify → reflect.

This is what turns OpenBro from 'tool router' into 'true agent'. Every user
prompt goes through the pipeline; the LLM is just one tool among many.

Public API:
    pipe = ReasoningPipeline(brain, agent)
    result = pipe.handle(user_prompt)   # full pipeline, returns text reply
"""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass, field

from openbro.core.activity import get_bus
from openbro.utils.language import detect_language

# Time-sensitive keywords — when one of these appears AND we're online, the
# pipeline auto-fetches fresh web context before invoking the LLM.
FRESH_DATA_KEYWORDS = re.compile(
    r"\b(today|tomorrow|yesterday|now|tonight|aaj|kal|abhi|currently|"
    r"latest|breaking|news|weather|forecast|temperature|stock|price|score|"
    r"live|trending|update)\b",
    re.IGNORECASE,
)


def is_online(timeout: float = 0.5) -> bool:
    """Quick online check used by the hybrid agent.

    Returns True if a known host resolves within `timeout` seconds.
    """
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname("github.com")
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


@dataclass
class PipelineResult:
    reply: str
    used_skill: str | None = None
    used_self_coder: bool = False
    used_planner: bool = False
    used_verifier: bool = False
    used_web: bool = False
    online: bool = True
    memory_hits: int = 0
    duration_ms: int = 0
    steps: list[str] = field(default_factory=list)


class ReasoningPipeline:
    def __init__(self, brain, agent):
        self.brain = brain
        self.agent = agent  # existing Agent — used for LLM reasoning step
        self.bus = get_bus()

    # ─── public ────────────────────────────────────────────────────

    def handle(self, prompt: str) -> PipelineResult:
        start = time.time()
        steps: list[str] = []
        lang = detect_language(prompt)
        online = is_online()
        steps.append("online" if online else "offline")

        result = PipelineResult(reply="", online=online)

        # 1. Recall — semantic search past memories
        memory_ctx, memory_hits = self._recall(prompt, steps)
        result.memory_hits = memory_hits

        # 2. Skill match — does a learned skill fire?
        skill = self._match_skill(prompt, steps)
        if skill:
            ran = self.brain.skills.run(skill.name)
            if ran.get("ok"):
                self._record(prompt, ran.get("output", ""), lang, used_skill=skill.name)
                result.reply = ran.get("output", "(skill ran successfully)")
                result.used_skill = skill.name
                result.duration_ms = int((time.time() - start) * 1000)
                result.steps = steps + ["executed skill"]
                return result
            steps.append(f"skill failed: {ran.get('error', '?')}")

        # 3. Auto-fetch fresh web context for time-sensitive queries
        web_ctx = ""
        if online and FRESH_DATA_KEYWORDS.search(prompt):
            web_ctx = self._fetch_fresh_data(prompt)
            if web_ctx:
                steps.append("web: injected fresh data")
                result.used_web = True

        # 4. Inject context (profile + memory + web) into agent system prompt
        original_history = list(self.agent.history)
        original_sys = self.agent.history[0].content if self.agent.history else ""
        injected = []
        try:
            if hasattr(self.brain, "profile") and self.brain.profile:
                injected.append(self.brain.profile.context_snippet())
            if memory_ctx:
                injected.append(memory_ctx)
            if web_ctx:
                injected.append(web_ctx)
            if injected and self.agent.history:
                extra = "\n\n".join(p for p in injected if p)
                self.agent.history[0].content = f"{original_sys}\n\n{extra}"
                steps.append("context: profile + memory + web injected")

            # 5. Multi-role planner/verifier for complex prompts; simple chat
            #    for everything else.
            from openbro.core.multi_role import needs_planning, plan, verify

            if needs_planning(prompt):
                steps.append("planner: invoking")
                plan_steps = plan(self.agent.provider, prompt)
                if plan_steps:
                    result.used_planner = True
                    self.bus.emit("brain", f"plan: {len(plan_steps)} steps")
                    steps.append(f"plan: {len(plan_steps)} steps")
                    # Inject plan into the prompt so the executor can follow it
                    augmented = f"{prompt}\n\n[Internal plan to follow]:\n" + "\n".join(
                        f"  {i + 1}. {s}" for i, s in enumerate(plan_steps)
                    )
                    reply = self.agent.chat(augmented)
                    # Verifier check
                    ok, note = verify(self.agent.provider, plan_steps, reply)
                    result.used_verifier = True
                    steps.append(f"verifier: {'pass' if ok else 'fail'}")
                    if not ok:
                        self.bus.emit("brain", f"verifier flagged: {note}")
                else:
                    reply = self.agent.chat(prompt)
            else:
                reply = self.agent.chat(prompt)
        finally:
            # Restore agent state — don't let injection accumulate across turns
            self.agent.history = original_history
            if self.agent.history:
                self.agent.history[0].content = original_sys

        # 6. Reflect
        self._record(prompt, reply, lang, used_skill=None)

        result.reply = reply
        result.duration_ms = int((time.time() - start) * 1000)
        result.steps = steps + ["LLM reasoning"]
        return result

    # ─── helpers ───────────────────────────────────────────────────

    def _recall(self, prompt: str, steps: list[str]) -> tuple[str, int]:
        if not (hasattr(self.brain, "memory") and self.brain.memory):
            return "", 0
        try:
            ctx = self.brain.memory.context_for(prompt, limit=5)
            hits = ctx.count("\n  [")
            steps.append(f"recall: {hits} relevant memories")
            return ctx, hits
        except Exception as e:
            steps.append(f"recall: error ({e})")
            return "", 0

    def _match_skill(self, prompt: str, steps: list[str]):
        if not (hasattr(self.brain, "skills") and self.brain.skills):
            return None
        try:
            skill = self.brain.skills.match(prompt)
            if skill:
                steps.append(f"skill match: {skill.name}")
            return skill
        except Exception:
            return None

    def _fetch_fresh_data(self, prompt: str) -> str:
        """Use the web tool to grab fresh context for time-sensitive prompts."""
        try:
            tool = self.agent.tool_registry.get_tool("web")
            if not tool:
                return ""
            # Cheap: search the user's prompt verbatim and stuff the top results
            result = tool.run(action="search", query=prompt[:200])
            if result and not result.startswith("Error"):
                return f"Fresh web search for '{prompt[:60]}':\n{result[:1500]}"
        except Exception:
            pass
        return ""

    def _record(self, prompt: str, reply: str, lang: str, used_skill: str | None) -> None:
        try:
            self.brain.record_interaction(
                prompt=prompt,
                response=reply,
                language=lang,
                tools_used=[used_skill] if used_skill else [],
                success=True,
            )
            if hasattr(self.brain, "memory") and self.brain.memory:
                self.brain.memory.add(prompt, kind="user", meta={"lang": lang})
                self.brain.memory.add(reply, kind="assistant", meta={"lang": lang})

            from openbro.brain.reflection import Reflector

            Reflector(self.brain).reflect(
                prompt=prompt,
                response=reply,
                used_skill=used_skill,
                success=True,
            )
        except Exception as e:
            self.bus.emit("brain", f"record_interaction error: {e}")
