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


def _find_first(
    root: str,
    target_names: tuple[str, ...],
    max_depth: int = 8,
) -> str | None:
    """BFS for the first file with a name in target_names. Skips noise dirs.

    Android repos hide AndroidManifest.xml at app/src/main/AndroidManifest.xml
    (depth 4) and MainActivity.kt at app/src/main/java/com/example/PROJECT/
    MainActivity.kt (depth 6+). Need to walk deeper than the regular
    file_ops cap to actually find the code.
    """
    skip = {
        ".git",
        ".gradle",
        ".idea",
        "build",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".cache",
    }
    target_lower = {n.lower() for n in target_names}
    queue: list[tuple[str, int]] = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        if depth > max_depth:
            continue
        try:
            entries = os.listdir(current)
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(current, entry)
            if entry.lower() in target_lower and os.path.isfile(full):
                return full
        for entry in entries:
            full = os.path.join(current, entry)
            if entry.lower() in skip:
                continue
            if os.path.isdir(full) and depth < max_depth:
                queue.append((full, depth + 1))
    return None


def _find_source_files(
    root: str,
    extensions: tuple[str, ...],
    max_files: int = 8,
    max_depth: int = 8,
) -> list[str]:
    """BFS collect up to max_files source files with the given extensions.

    Used by deep_inspect to grab the main source code samples from
    nested project layouts (Android's app/src/main/java/.../).
    """
    skip = {
        ".git",
        ".gradle",
        ".idea",
        "build",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".cache",
        "test",
        "tests",
        "androidTest",
    }
    out: list[str] = []
    queue: list[tuple[str, int]] = [(root, 0)]
    while queue and len(out) < max_files:
        current, depth = queue.pop(0)
        if depth > max_depth:
            continue
        try:
            entries = os.listdir(current)
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(current, entry)
            if os.path.isfile(full):
                if any(entry.lower().endswith(e) for e in extensions):
                    out.append(full)
                    if len(out) >= max_files:
                        return out
            elif os.path.isdir(full) and entry.lower() not in skip and depth < max_depth:
                queue.append((full, depth + 1))
    return out


def _parse_gradle_deps(text: str) -> list[str]:
    """Pull notable dependencies / plugins out of a build.gradle{,.kts} file.

    Cheap regex pass — we just want the meaningful 'this app uses
    Google Maps SDK / Compose / Retrofit' signals so the response
    can tell the user what TECH the project pulls in."""
    deps: list[str] = []
    for m in re.finditer(
        r"(?:implementation|api|kapt|classpath|plugin)\s*[\(\"']\s*[\"']?"
        r"([a-zA-Z0-9._\-:]+)[\"']?",
        text,
    ):
        deps.append(m.group(1))
    # De-dup while preserving order
    seen = set()
    out: list[str] = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _summarize_android_manifest(raw: str) -> dict:
    """Extract package, activities, and permissions from AndroidManifest.xml."""
    out: dict = {}
    m = re.search(r'package\s*=\s*"([^"]+)"', raw)
    if m:
        out["package"] = m.group(1)
    activities = re.findall(r'<activity\b[^>]*android:name="([^"]+)"', raw)
    if activities:
        out["activities"] = activities[:10]
    permissions = re.findall(
        r'<uses-permission\s+android:name="([^"]+)"',
        raw,
    )
    if permissions:
        # Strip the common prefix for readability
        out["permissions"] = [p.replace("android.permission.", "") for p in permissions[:10]]
    features = re.findall(r'<uses-feature\s+android:name="([^"]+)"', raw)
    if features:
        out["features"] = features[:6]
    return out


def _deep_inspect(target: str, language: str) -> str:
    """Read source files and manifests one level deep, return markdown.

    For Android/Gradle projects: AndroidManifest.xml + dependencies +
    MainActivity source. For Python: top-level entry __main__.py /
    main.py. For Node: index.{js,ts}. Adapts to whatever's present.
    """
    sections: list[str] = []

    # ─── Android-specific ────────────────────────────────────────
    if language in ("Kotlin", "Java"):
        manifest_path = _find_first(target, ("AndroidManifest.xml",))
        if manifest_path:
            raw = _read_safe(manifest_path, max_chars=8000)
            info = _summarize_android_manifest(raw)
            if info:
                sections.append("### AndroidManifest.xml")
                rel = os.path.relpath(manifest_path, target)
                sections.append(f"_at `{rel}`_")
                sections.append("")
                if info.get("package"):
                    sections.append(f"- **Package**: `{info['package']}`")
                if info.get("activities"):
                    sections.append("- **Activities**:")
                    for a in info["activities"]:
                        sections.append(f"  - `{a}`")
                if info.get("permissions"):
                    sections.append(
                        "- **Permissions**: " + ", ".join(f"`{p}`" for p in info["permissions"])
                    )
                if info.get("features"):
                    sections.append(
                        "- **Features**: " + ", ".join(f"`{f}`" for f in info["features"])
                    )

        # Gradle dependencies
        for gradle_name in ("build.gradle.kts", "build.gradle"):
            gradle_path = _find_first(target, (gradle_name,), max_depth=4)
            if not gradle_path:
                continue
            raw = _read_safe(gradle_path, max_chars=8000)
            deps = _parse_gradle_deps(raw)
            if deps:
                sections.append("")
                sections.append(
                    f"### Dependencies _(from `{os.path.relpath(gradle_path, target)}`)_"
                )
                sections.append("")
                for d in deps[:20]:
                    sections.append(f"- `{d}`")
                if len(deps) > 20:
                    sections.append(f"- _(+{len(deps) - 20} more)_")
                break  # one gradle file's enough

    # ─── Source code samples ─────────────────────────────────────
    if language == "Kotlin":
        sources = _find_source_files(target, (".kt",))
    elif language == "Java":
        sources = _find_source_files(target, (".java",))
    elif language == "Python":
        sources = _find_source_files(target, (".py",), max_files=5)
    elif language == "TypeScript/JavaScript":
        sources = _find_source_files(target, (".ts", ".tsx", ".js", ".jsx"), max_files=5)
    elif language == "Go":
        sources = _find_source_files(target, (".go",), max_files=5)
    elif language == "Rust":
        sources = _find_source_files(target, (".rs",), max_files=5)
    else:
        sources = []

    # Prefer entry-point shaped names first (MainActivity.kt, App.kt,
    # main.py, index.ts, etc.). The bare BFS already gives us depth-
    # sorted; we just reorder by name preference.
    def _entry_score(p: str) -> int:
        name = os.path.basename(p).lower()
        if name.startswith("main"):
            return 0
        if "app" in name or "activity" in name:
            return 1
        return 2

    sources.sort(key=_entry_score)

    if sources:
        sections.append("")
        sections.append(f"### Source samples _(first {min(3, len(sources))} files)_")
        for src in sources[:3]:
            rel = os.path.relpath(src, target)
            content = _read_safe(src, max_chars=1500)
            sections.append("")
            sections.append(f"**`{rel}`**")
            sections.append("```")
            # Trim trailing whitespace for a tighter render
            sections.append(content.rstrip())
            sections.append("```")
        if len(sources) > 3:
            sections.append(
                f"\n_({len(sources) - 3} more source file(s) — read with `file_ops read <path>`.)_"
            )

    return "\n".join(sections)


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
        # If the top level had no language-marker extensions but a
        # manifest names the ecosystem, infer from that. Captured case:
        # Python project with only pyproject.toml + a src/ subdir
        # reported 'Unknown' even though we had a clear signal.
        if language == "Unknown":
            manifest_names = {n.lower() for n, _ in manifest_summaries}
            if "pyproject.toml" in manifest_names:
                language = "Python"
            elif "package.json" in manifest_names:
                language = "TypeScript/JavaScript"
            elif "cargo.toml" in manifest_names:
                language = "Rust"
            elif "go.mod" in manifest_names:
                language = "Go"
            elif any(
                n.startswith("build.gradle") or n == "settings.gradle" for n in manifest_names
            ):
                language = "Kotlin"
            elif "pom.xml" in manifest_names:
                language = "Java"

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
            sections.append("\n_No README at top level._")
            if top_dirs:
                sections.append(
                    "**Top-level directories**: " + ", ".join(f"`{d}`" for d in top_dirs[:12])
                )
            if top_files:
                sections.append(
                    "**Top-level files**: " + ", ".join(f"`{f}`" for f in top_files[:12])
                )

        # ─── Deep inspect — actually read source/manifests ─────────────
        # Without this, a README-less project (Android repo, captured
        # case) gets a useless 'here's the file listing' response. Deep
        # inspect goes one or two levels in: AndroidManifest, main
        # entry point source, gradle dependencies, etc.
        deep = _deep_inspect(target, language)
        if deep:
            sections.append("")
            sections.append(deep)

        sections.append("")
        sections.append("_(Want more? `file_ops read <path>` on any file above.)_")

        return "\n".join(sections)
