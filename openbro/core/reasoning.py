"""Reasoning pipeline — recall → context → skill match → plan → execute → reflect.

This is what turns OpenBro from 'tool router' into 'true agent'. Every user
prompt goes through the pipeline; the LLM is just the reasoning step.

Public API:
    pipe = ReasoningPipeline(brain, agent)
    result = pipe.handle(user_prompt)   # full pipeline, returns text reply
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

from openbro.core.activity import get_bus
from openbro.utils.language import detect_language


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
    memory_hits: int = 0
    duration_ms: int = 0
    steps: list[str] = field(default_factory=list)


class ReasoningPipeline:
    def __init__(self, brain, agent):
        self.brain = brain
        self.agent = agent  # existing Agent — used for LLM reasoning step
        self.bus = get_bus()

    def handle(self, prompt: str) -> PipelineResult:
        start = time.time()
        steps: list[str] = []
        lang = detect_language(prompt)

        # Hybrid agent: detect connectivity once per turn so downstream
        # reasoning can decide whether to consult the web.
        online = is_online()
        steps.append("online" if online else "offline")

        # 1. Recall — semantic search past memories
        memory_ctx = ""
        memory_hits = 0
        if hasattr(self.brain, "memory") and self.brain.memory:
            try:
                memory_ctx = self.brain.memory.context_for(prompt, limit=5)
                memory_hits = memory_ctx.count("\n  [")  # rough count
                steps.append(f"recall: {memory_hits} relevant memories")
            except Exception as e:
                steps.append(f"recall: error ({e})")

        # 2. Skill match — does a learned skill fire?
        skill = None
        if hasattr(self.brain, "skills") and self.brain.skills:
            try:
                skill = self.brain.skills.match(prompt)
                if skill:
                    steps.append(f"skill match: {skill.name}")
            except Exception:
                pass

        # 3. If skill matched: run it directly (no LLM needed for known tasks)
        if skill:
            self.bus.emit("brain", f"running known skill: {skill.name}")
            result = self.brain.skills.run(skill.name)
            if result.get("ok"):
                self._record(prompt, result.get("output", ""), lang, used_skill=skill.name)
                return PipelineResult(
                    reply=result.get("output", "(skill ran successfully)"),
                    used_skill=skill.name,
                    memory_hits=memory_hits,
                    duration_ms=int((time.time() - start) * 1000),
                    steps=steps + ["executed skill"],
                )
            steps.append(f"skill failed: {result.get('error', '?')}")

        # 4. Inject memory + profile context into the agent's system prompt
        original_history = list(self.agent.history)
        try:
            if memory_ctx or hasattr(self.brain, "profile"):
                profile_ctx = self.brain.profile.context_snippet() if self.brain.profile else ""
                extra = "\n".join(p for p in (profile_ctx, memory_ctx) if p)
                if extra:
                    # Append to the system message (index 0)
                    sys_msg = self.agent.history[0]
                    sys_msg.content = f"{sys_msg.content}\n\n{extra}"
                    steps.append("context: profile + memory injected")

            # 5. LLM reasoning step (existing agent does the heavy lifting)
            reply = self.agent.chat(prompt)
        finally:
            # Reset system prompt so injected context doesn't accumulate
            self.agent.history = original_history

        # 6. Reflect — record interaction in brain
        self._record(prompt, reply, lang, used_skill=None)

        return PipelineResult(
            reply=reply,
            memory_hits=memory_hits,
            duration_ms=int((time.time() - start) * 1000),
            steps=steps + ["LLM reasoning"],
        )

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
        except Exception as e:
            self.bus.emit("brain", f"record_interaction error: {e}")
