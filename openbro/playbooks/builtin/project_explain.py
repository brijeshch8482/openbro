"""ProjectExplainPlaybook — 'kya karta hai is project me' answered without
the agent hallucinating from a `file_ops list` output alone.

Captured failure (2026-05-29): user pointed agent at D:/MapRadiusKotlin
and asked 'expain this project?'. Agent ran `file_ops list`, saw 12
entries, and wrote 4 paragraphs of 'appears to be / probably / likely'.
Then the follow-up 'i want to know what is doing' burned 22K tokens
across 3 LLM round-trips and hit the rate limit.

This playbook does the work the agent should have done the first time:
read README + manifest (pyproject.toml / package.json / build.gradle*),
read the top-level entry point, then synthesize a concrete answer from
ACTUAL file contents — no LLM round-trips, no fabrication.
"""

from __future__ import annotations

import os
import re

from openbro.playbooks.base import Playbook, PlaybookContext

# Files we read first if present, in priority order. README + manifest +
# entry points are the cheapest 'what does this do' signal.
_PRIORITY_FILES = [
    "README.md",
    "README.rst",
    "README",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "build.gradle.kts",
    "build.gradle",
    "settings.gradle.kts",
    "settings.gradle",
    "Gemfile",
    "composer.json",
    "pom.xml",
    "AndroidManifest.xml",
    "Info.plist",
    "Makefile",
    "Dockerfile",
]

# Subdirs commonly hosting the real entry point — if we don't find a
# manifest at top-level we walk one of these one level deep.
_ENTRY_SUBDIRS = ["src", "app", "lib", "openbro", "openai"]


def _read_safe(path: str, max_chars: int = 4000) -> str:
    """Read a text file safely. Returns '' on any failure."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except (OSError, UnicodeError):
        return ""


def _summarize_manifest(name: str, raw: str) -> str:
    """Extract a one-line summary from a known manifest file."""
    if not raw:
        return ""
    if name == "package.json":
        m = re.search(r'"description"\s*:\s*"([^"]+)"', raw)
        if m:
            return m.group(1)
        m = re.search(r'"name"\s*:\s*"([^"]+)"', raw)
        if m:
            return f"name: {m.group(1)}"
    if name in ("pyproject.toml",):
        m = re.search(r'description\s*=\s*"([^"]+)"', raw)
        if m:
            return m.group(1)
        m = re.search(r'name\s*=\s*"([^"]+)"', raw)
        if m:
            return f"name: {m.group(1)}"
    if name in ("Cargo.toml",):
        m = re.search(r'description\s*=\s*"([^"]+)"', raw)
        if m:
            return m.group(1)
    if name in ("build.gradle.kts", "build.gradle"):
        m = re.search(r'applicationId\s*=?\s*["\']([^"\']+)["\']', raw)
        if m:
            return f"applicationId: {m.group(1)} (Android app)"
        m = re.search(r"(android|kotlin|java)\s*\{", raw)
        if m:
            return f"{m.group(1).capitalize()} project"
    if name == "AndroidManifest.xml":
        m = re.search(r'package\s*=\s*"([^"]+)"', raw)
        if m:
            return f"Android package: {m.group(1)}"
    return ""


def _detect_language(top_files: list[str]) -> str:
    """Best-effort language tag from the file extensions found at top level."""
    exts = {os.path.splitext(f)[1].lower() for f in top_files}
    if any(e in exts for e in (".kt", ".kts")):
        return "Kotlin"
    if ".py" in exts:
        return "Python"
    if any(e in exts for e in (".ts", ".tsx", ".js", ".jsx")):
        return "TypeScript/JavaScript"
    if ".rs" in exts:
        return "Rust"
    if ".go" in exts:
        return "Go"
    if ".rb" in exts:
        return "Ruby"
    if ".java" in exts:
        return "Java"
    if any(e in exts for e in (".c", ".cpp", ".h", ".hpp")):
        return "C/C++"
    return "Unknown"


def _looks_like_path(text: str) -> str | None:
    """Extract a directory path from the user's query if present."""
    # Common shapes: 'D:\Project', '/home/x/y', './foo'
    m = re.search(
        r"([A-Za-z]:\\[^\s\"'<>|]+|/[^\s\"'<>|]+|\.{1,2}/[^\s\"'<>|]+)",
        text,
    )
    if not m:
        return None
    candidate = m.group(1).rstrip(".,;?!")
    return candidate if os.path.isdir(candidate) else None


class ProjectExplainPlaybook(Playbook):
    name = "project_explain"
    description = "Summarize what a project does by reading its key files."
    triggers = [
        (
            re.compile(
                r"\b(explain|describe|tell me about|what does|what is|"
                r"what's|kya\s+karta\s+hai|kya\s+kr\s+raha|kya\s+kaam|"
                r"summarise|summarize)\b.*\b(project|repo|code|codebase)\b",
                re.IGNORECASE,
            ),
            1.0,
        ),
        (
            re.compile(
                r"\b(project|repo|codebase)\b.*\b(explain|describe|samjha|"
                r"samjhao|kya\s+karta)\b",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'D:\X expain this project' shape (typo of explain)
        (
            re.compile(
                r"[A-Za-z]:\\\S+.*\b(exp(l)?ain|describe|tell|samjha)\b",
                re.IGNORECASE,
            ),
            0.9,
        ),
    ]
    keywords: list[str] = []

    def execute(self, context: PlaybookContext) -> str:
        # Resolve target directory. Priority:
        # 1. A path mentioned in the user input
        # 2. The current working directory
        target = _looks_like_path(context.user_input) or os.getcwd()
        if not os.path.isdir(target):
            return f"_Not a directory: `{target}`. Try `project_explain D:/path`._"

        sections: list[str] = []
        # Top-level listing (so the answer cites real names, not invented ones).
        try:
            top = sorted(os.listdir(target))
        except OSError as e:
            return f"_Couldn't list `{target}`: {e}_"

        top_files = [f for f in top if os.path.isfile(os.path.join(target, f))]
        top_dirs = [f for f in top if os.path.isdir(os.path.join(target, f))]

        # README — first, full read (truncated to 4 KB).
        readme = ""
        readme_name = ""
        for cand in _PRIORITY_FILES[:3]:
            full = os.path.join(target, cand)
            if os.path.isfile(full):
                readme = _read_safe(full, max_chars=4000)
                readme_name = cand
                break

        # Manifest summary
        manifest_summaries: list[tuple[str, str]] = []
        for cand in _PRIORITY_FILES[3:]:
            full = os.path.join(target, cand)
            if os.path.isfile(full):
                raw = _read_safe(full, max_chars=2000)
                line = _summarize_manifest(cand, raw)
                if line:
                    manifest_summaries.append((cand, line))

        language = _detect_language(top_files + top_dirs)

        # Build the response.
        sections.append(f"### Project: `{os.path.basename(target.rstrip(os.sep)) or target}`")
        sections.append(f"- **Language**: {language}")
        sections.append(f"- **Path**: `{target}`")
        if manifest_summaries:
            sections.append("- **Manifests**:")
            for name, line in manifest_summaries[:6]:
                sections.append(f"  - `{name}` — {line}")

        if readme:
            sections.append(f"\n### {readme_name}\n")
            # Strip noisy markdown shields/badges/HTML
            clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", readme)
            clean = re.sub(r"<[^>]+>", "", clean)
            sections.append(clean.strip()[:2500])
        else:
            sections.append("\n_No README found at top level. Listing structure for context:_")
            if top_dirs:
                sections.append(
                    "\n**Top-level directories**: " + ", ".join(f"`{d}`" for d in top_dirs[:12])
                )
            if top_files:
                sections.append(
                    "**Top-level files**: " + ", ".join(f"`{f}`" for f in top_files[:12])
                )

        # Add a quick "next" hint so the model (if it follows up) knows
        # what's worth reading deeper.
        sections.append("")
        sections.append(
            "_(Read further with `file_ops read <path>` — "
            "good next targets: README, manifests, top-level source files.)_"
        )

        return "\n".join(sections)
