"""UserProfile — the human model the brain maintains.

This is what makes OpenBro feel personal. As the user interacts, the
reflection loop updates the profile: language preference, response style,
active projects, expertise, schedule. Inject relevant slices into the
LLM context so every reply is already user-shaped.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml


@dataclass
class LanguageStats:
    primary: str = "hinglish"  # 'hi' | 'hinglish' | 'en'
    secondary: str = "english"
    counts: dict[str, int] = field(default_factory=lambda: {"hi": 0, "hinglish": 0, "en": 0})

    def record(self, lang: str) -> None:
        self.counts[lang] = self.counts.get(lang, 0) + 1
        # Recompute primary/secondary from totals
        ranked = sorted(self.counts.items(), key=lambda kv: -kv[1])
        if ranked:
            self.primary = ranked[0][0]
        if len(ranked) > 1:
            self.secondary = ranked[1][0]


@dataclass
class StylePrefs:
    verbosity: str = "short"  # short | medium | long
    technical_level: str = "high"  # low | medium | high
    emoji_preference: str = "none"  # none | sparse | rich
    tone: str = "casual"  # casual | professional


@dataclass
class Project:
    name: str
    type: str = ""  # "ai_agent", "web_app", etc.
    stack: list[str] = field(default_factory=list)
    last_touch: str = ""  # ISO date
    status: str = "active"  # active | paused | archived


@dataclass
class Schedule:
    active_hours: str = ""  # e.g. "22-04"
    timezone: str = ""


@dataclass
class UserProfile:
    user_id: str = "default"
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    language: LanguageStats = field(default_factory=LanguageStats)
    style: StylePrefs = field(default_factory=StylePrefs)
    expertise: list[str] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    schedule: Schedule = field(default_factory=Schedule)
    preferences: dict = field(default_factory=dict)
    interaction_count: int = 0

    @classmethod
    def load(cls, path: Path) -> UserProfile:
        if not path.exists():
            return cls()
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except (yaml.YAMLError, OSError):
            return cls()
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> UserProfile:
        # Hand-build nested dataclasses from yaml dict
        lang_data = data.get("language", {}) or {}
        if isinstance(lang_data, str):
            # legacy format: just a string
            lang_data = {"primary": lang_data}
        lang = LanguageStats(
            primary=lang_data.get("primary", "hinglish"),
            secondary=lang_data.get("secondary", "english"),
            counts=lang_data.get("counts", {"hi": 0, "hinglish": 0, "en": 0}),
        )

        style_data = data.get("style", {}) or {}
        style = StylePrefs(
            verbosity=style_data.get("verbosity", "short"),
            technical_level=style_data.get("technical_level", "high"),
            emoji_preference=style_data.get("emoji_preference", "none"),
            tone=style_data.get("tone", "casual"),
        )

        sched_data = data.get("schedule", {}) or {}
        schedule = Schedule(
            active_hours=sched_data.get("active_hours", ""),
            timezone=sched_data.get("timezone", ""),
        )

        projects: list[Project] = []
        for p in data.get("projects", []) or []:
            if isinstance(p, dict):
                projects.append(
                    Project(
                        name=p.get("name", ""),
                        type=p.get("type", ""),
                        stack=p.get("stack", []) or [],
                        last_touch=p.get("last_touch", ""),
                        status=p.get("status", "active"),
                    )
                )

        return cls(
            user_id=data.get("user_id", "default"),
            created=data.get("created", datetime.now(timezone.utc).date().isoformat()),
            language=lang,
            style=style,
            expertise=data.get("expertise", []) or [],
            projects=projects,
            schedule=schedule,
            preferences=data.get("preferences", {}) or {},
            interaction_count=data.get("interaction_count", 0),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True))

    def context_snippet(self) -> str:
        """A compact string injected into the LLM system prompt so replies match the user."""
        lines = [
            f"User profile: {self.user_id}",
            f"  Language preference: {self.language.primary}",
            f"  Style: {self.style.verbosity} {self.style.tone}, {self.style.technical_level}-tech",
        ]
        if self.expertise:
            lines.append(f"  Expertise: {', '.join(self.expertise[:5])}")
        active = [p.name for p in self.projects if p.status == "active"]
        if active:
            lines.append(f"  Active projects: {', '.join(active[:5])}")
        return "\n".join(lines)

    def record_interaction(self, lang: str | None = None) -> None:
        self.interaction_count += 1
        if lang:
            self.language.record(lang)

    def add_or_touch_project(self, name: str, **kwargs) -> Project:
        for p in self.projects:
            if p.name == name:
                p.last_touch = datetime.now(timezone.utc).date().isoformat()
                for k, v in kwargs.items():
                    if hasattr(p, k):
                        setattr(p, k, v)
                return p
        proj = Project(
            name=name,
            last_touch=datetime.now(timezone.utc).date().isoformat(),
            **{k: v for k, v in kwargs.items() if k in {"type", "stack", "status"}},
        )
        self.projects.append(proj)
        return proj
