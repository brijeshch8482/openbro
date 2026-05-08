"""Reflection — extract patterns from interactions, update brain over time.

This is what makes the agent actually *learn*: every interaction goes
through the Reflector, which:
  - detects user signal (thanks / correction / retry / silence)
  - updates skill confidence based on outcome
  - extracts repeated patterns (e.g. user often asks for X at time Y)
  - suggests auto-skills when a successful free-form solve happens twice
  - updates the user profile (verbosity preference, expertise drift)

Public API:
    Reflector(brain).reflect(interaction)
    Reflector(brain).extract_patterns(window=50)
    Reflector(brain).top_skills(by="confidence")
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass

# Words / patterns that signal user satisfaction or correction.
POSITIVE_SIGNALS = re.compile(
    r"\b(thanks|thank\s*you|theek|sahi|perfect|good|nice|wah|shukriya|achha|"
    r"acha|thik|haan\s*ji|ok\s*bro|done)\b",
    re.IGNORECASE,
)
NEGATIVE_SIGNALS = re.compile(
    r"\b(no\s*not|wrong|galat|nahi\s*samjha|fir\s*se|retry|again|nope|"
    r"that's\s*not|that\s*is\s*not|kuch\s*aur)\b",
    re.IGNORECASE,
)


@dataclass
class ReflectionResult:
    signal: str  # "positive" | "negative" | "neutral"
    confidence_delta: float
    patterns_found: list[str]
    suggestions: list[str]  # human-readable changes the brain made


class Reflector:
    """Per-interaction learning. Stateless — reads/writes through brain."""

    def __init__(self, brain):
        self.brain = brain

    # ─── public API ────────────────────────────────────────────────

    def reflect(
        self,
        prompt: str,
        response: str,
        used_skill: str | None = None,
        followup: str | None = None,
        success: bool = True,
    ) -> ReflectionResult:
        """Called by the reasoning pipeline after every turn."""
        signal = self._classify_signal(followup or "")
        delta = 0.0
        patterns: list[str] = []
        suggestions: list[str] = []

        # 1. Skill confidence: signal + success drive the delta
        if used_skill:
            if signal == "positive":
                delta = 0.10
            elif signal == "negative":
                delta = -0.20
            elif success:
                delta = 0.02
            else:
                delta = -0.05
            self._adjust_skill_confidence(used_skill, delta)
            suggestions.append(f"skill {used_skill} confidence {delta:+.2f}")

        # 2. Profile drift: language, expertise hints, schedule
        try:
            from openbro.utils.language import detect_language

            lang = detect_language(prompt)
            self.brain.profile.record_interaction(lang=lang)
        except Exception:
            pass

        # 3. Project mention detection — if user references a project
        proj_hint = self._detect_project_mention(prompt)
        if proj_hint:
            self.brain.profile.add_or_touch_project(proj_hint)
            suggestions.append(f"profile.projects touched: {proj_hint}")
            patterns.append(f"mentions project '{proj_hint}'")

        # 4. Pattern detection — if a similar prompt exists in memory + skill
        #    used both times, mark for promotion
        try:
            similar = self._find_similar_prior(prompt, kind="user", limit=3)
            if used_skill and len(similar) >= 1:
                patterns.append(f"recurring intent → skill '{used_skill}'")
        except Exception:
            pass

        # 5. Persist the reflection event
        self.brain.storage.append_learning(
            {
                "type": "reflection",
                "signal": signal,
                "used_skill": used_skill,
                "delta": delta,
                "patterns": patterns,
            }
        )
        self.brain.save()

        return ReflectionResult(
            signal=signal,
            confidence_delta=delta,
            patterns_found=patterns,
            suggestions=suggestions,
        )

    def extract_patterns(self, window: int = 100) -> list[dict]:
        """Scan recent learnings for repeated intents/skills."""
        events = self.brain.storage.read_learnings(limit=window)
        skill_counts: Counter = Counter()
        for ev in events:
            sk = ev.get("used_skill")
            if sk:
                skill_counts[sk] += 1
        return [{"skill": skill, "uses": count} for skill, count in skill_counts.most_common(10)]

    def top_skills(self, by: str = "uses") -> list[dict]:
        """Return skills ranked by usage or confidence."""
        if not hasattr(self.brain, "skills") or not self.brain.skills:
            return []
        skills = self.brain.skills.list()
        if by == "confidence":
            ranked = sorted(
                skills,
                key=lambda s: -self._skill_confidence(s.name),
            )
        else:
            ranked = sorted(
                skills,
                key=lambda s: -(s.success_count + s.fail_count),
            )
        return [
            {
                "name": s.name,
                "uses": s.success_count + s.fail_count,
                "success_rate": (
                    s.success_count / (s.success_count + s.fail_count)
                    if (s.success_count + s.fail_count) > 0
                    else None
                ),
                "confidence": self._skill_confidence(s.name),
            }
            for s in ranked
        ]

    # ─── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _classify_signal(text: str) -> str:
        if not text:
            return "neutral"
        if NEGATIVE_SIGNALS.search(text):
            return "negative"
        if POSITIVE_SIGNALS.search(text):
            return "positive"
        return "neutral"

    def _adjust_skill_confidence(self, name: str, delta: float) -> None:
        """Confidence is stored under brain meta as a small dict.

        Range: 0.0 - 1.0, starts at 0.5 for new skills, decays toward 0.5 over
        time if the skill isn't used (handled by extract_patterns compaction).
        """
        meta = self.brain.storage.read_meta()
        confs = meta.get("skill_confidence", {})
        cur = confs.get(name, 0.5)
        new = max(0.0, min(1.0, cur + delta))
        confs[name] = round(new, 3)
        self.brain.storage.update_meta(skill_confidence=confs)

    def _skill_confidence(self, name: str) -> float:
        meta = self.brain.storage.read_meta()
        return meta.get("skill_confidence", {}).get(name, 0.5)

    @staticmethod
    def _detect_project_mention(text: str) -> str | None:
        """Cheap heuristic: 'mera <name> project' or capitalised camelcase."""
        m = re.search(
            r"\b(?:mera|my|the)\s+(\w+)\s+(?:project|app|repo|tool)\b",
            text,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).lower()
        # Camelcase / PascalCase project names: 'OpenBro', 'MyApp'
        m2 = re.search(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text)
        return m2.group(1) if m2 else None

    def _find_similar_prior(self, text: str, kind: str | None = None, limit: int = 3) -> list[dict]:
        if hasattr(self.brain, "memory") and self.brain.memory:
            return self.brain.memory.search(text, limit=limit, kind=kind)
        return []


# Convenience helper — used by the reasoning pipeline
def reflect_now(brain, **kwargs) -> ReflectionResult:
    return Reflector(brain).reflect(**kwargs)


# Background reflection compaction — call periodically (e.g. once a day)
def compact_brain(brain, days: int = 90) -> dict:
    """Periodic maintenance: trim old memory + decay unused skill confidence."""
    summary = {"started_at": time.time()}
    if hasattr(brain, "memory") and brain.memory:
        summary["memory_dropped"] = brain.memory.compact(keep_recent_days=days)
    # Decay unused skill confidence toward 0.5
    meta = brain.storage.read_meta()
    confs = meta.get("skill_confidence", {})
    for name in list(confs.keys()):
        cur = confs[name]
        # Pull each value 5% closer to 0.5
        confs[name] = round(cur + (0.5 - cur) * 0.05, 3)
    brain.storage.update_meta(skill_confidence=confs)
    summary["confidence_decayed"] = len(confs)
    return summary
