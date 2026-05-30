"""TechResearchPlaybook — real web research on technical questions.

Captured failure (2026-05-30): user asked 'what can we do in android app
for getting MTP permission in kiosk mode?'. Agent dumped generic Android
documentation knowledge from training data, added 'I can't directly
test' disclaimer, recommended 'test on different devices'. User reply:
'it is not doing... like pro... it is basic?? why?'

The fix: when the user asks a technical/how-to question, search the
web for real sources, fetch the top results, return a synthesized
answer GROUNDED IN ACTUAL DOCUMENTATION with source links. No more
generic-training-data dumps.

This playbook is conservative about WHEN it fires:
  - Question has a technical-domain trigger (Android/Kotlin/Python/JS/
    React/Vue/Docker/AWS/SQL/etc.) OR a 'how to' pattern with a
    specific technology mentioned.
  - Query length > 15 chars (not casual questions).
  - Doesn't fire on trivial follow-ups, greetings, or already-handled
    intents (geo / time / process / file).

When it fires:
  1. web.search the query
  2. web.fetch the top N results (in parallel where possible)
  3. Extract relevant text from each (strip nav, ads, etc.)
  4. Return a markdown response with:
     - 'TL;DR' synthesized answer (heuristic, 2-3 sentences)
     - Source list with relevant excerpts
     - Code blocks pulled from the fetched pages

Output is then `pass_through_to_llm` so the LLM can refine the
synthesis using the real source content as grounding — instead of
hallucinating from training data.
"""

from __future__ import annotations

import re

from openbro.playbooks.base import Playbook, PlaybookContext

# Technology + topic keywords. The query must hit one of these for the
# playbook to fire. Keeps it from false-positiving on casual chat.
_TECH_KEYWORDS = {
    # Mobile
    "android",
    "ios",
    "swift",
    "kotlin",
    "jetpack",
    "compose",
    "flutter",
    "react native",
    # Web frontend
    "react",
    "vue",
    "angular",
    "svelte",
    "next",
    "nextjs",
    "next.js",
    "typescript",
    "javascript",
    "tailwind",
    "css",
    "html",
    "webpack",
    "vite",
    # Backend
    "node",
    "nodejs",
    "express",
    "fastapi",
    "flask",
    "django",
    "rails",
    "spring",
    "spring boot",
    "go",
    "golang",
    "rust",
    "java",
    "php",
    "laravel",
    # Data
    "sql",
    "postgres",
    "postgresql",
    "mysql",
    "sqlite",
    "mongodb",
    "redis",
    "pandas",
    "numpy",
    "polars",
    "duckdb",
    # AI/ML
    "pytorch",
    "tensorflow",
    "huggingface",
    "transformers",
    "langchain",
    "openai",
    "anthropic",
    "llm",
    "embeddings",
    # DevOps / Cloud
    "docker",
    "kubernetes",
    "k8s",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "ansible",
    "github actions",
    "ci/cd",
    "ci",
    # Concepts
    "api",
    "rest",
    "graphql",
    "websocket",
    "oauth",
    "jwt",
    "auth",
    "permission",
    "permissions",
    "ssl",
    "tls",
    "https",
    "cors",
    "csrf",
    "xss",
    "deployment",
    "deploy",
    # Tools
    "git",
    "github",
    "linux",
    "windows",
    "macos",
    "powershell",
    "bash",
    "vim",
    "vscode",
    "intellij",
    "android studio",
    "xcode",
}

# Question shapes that signal 'I want a real answer with sources'.
# We require BOTH a question shape AND a tech keyword to fire.
_QUESTION_PATTERNS = [
    re.compile(r"\bhow\s+(do|to|can|should)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(can|is|are|does)\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+(does|is|do|won'?t|can'?t)\b", re.IGNORECASE),
    re.compile(r"\bbest\s+(way|practice|approach|method)\b", re.IGNORECASE),
    re.compile(r"\bproblem\s+(with|in)\b", re.IGNORECASE),
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bfix\b", re.IGNORECASE),
    re.compile(r"\bnot\s+working\b", re.IGNORECASE),
    re.compile(r"\b(set\s+up|configure|integrate|implement|migrate)\b", re.IGNORECASE),
    re.compile(r"\bdifference\s+between\b", re.IGNORECASE),
    re.compile(r"\b(can|is\s+it\s+possible)\s+to\b", re.IGNORECASE),
]

# Phrases that mean 'don't research, just answer from context' — these
# block the playbook so a follow-up doesn't trigger a fresh web search.
_FOLLOWUP_BLOCKERS = re.compile(
    r"\b("
    r"explain (more|further|better|deeper|properly)|"
    r"tell me more|elaborate|"
    r"keep it short|tldr|"
    r"i told you|i asked|"
    r"will it work|self check|verify|"
    r"summari[sz]e"
    r")\b",
    re.IGNORECASE,
)


def _looks_technical(query: str) -> bool:
    """Both a tech keyword AND a question shape must be present."""
    q_lower = query.lower()
    has_keyword = any(kw in q_lower for kw in _TECH_KEYWORDS)
    has_question = any(p.search(query) for p in _QUESTION_PATTERNS)
    return has_keyword and has_question


class TechResearchPlaybook(Playbook):
    name = "tech_research"
    description = "Web-research technical how-to / problem questions; cite real sources."
    # Plays through to the LLM so it can synthesize from real fetched
    # content instead of training-data hallucinations.
    pass_through_to_llm = True
    triggers: list[tuple[re.Pattern, float]] = []
    keywords: list[str] = []

    def match(self, query: str):
        from openbro.playbooks.base import PlaybookMatch

        if not query or len(query) < 15:
            return None
        if _FOLLOWUP_BLOCKERS.search(query):
            return None
        if not _looks_technical(query):
            return None
        return PlaybookMatch(playbook=self, confidence=0.85, captures={})

    def execute(self, context: PlaybookContext) -> str:
        query = context.user_input.strip()
        web = context.tool_registry.get_tool("web")
        if web is None:
            return ""  # decline so the LLM still gets a turn

        # ─── Deep research: search + fetch top 5-7 sources ────────────
        # User explicitly asked for deeper than 3 sources. We pull more
        # search results, fetch up to 6 substantive pages, and pass them
        # all into the LLM's context — the model picks what's relevant.
        try:
            search_raw = web.run(action="search", query=query, max_results=8)
        except Exception:
            return ""
        urls = _extract_urls(search_raw)
        if not urls:
            return ""

        # ─── Augment with site-specific deep search ───────────────────
        # For Android/Kotlin questions, prefer developer.android.com +
        # stackoverflow.com results. For others, the generic search
        # already has the right shape. We do ONE additional targeted
        # search to grab authoritative docs that often miss the generic
        # top-of-results.
        site_query = _site_augmented_query(query)
        if site_query and site_query != query:
            try:
                extra = web.run(action="search", query=site_query, max_results=5)
                for u in _extract_urls(extra):
                    if u not in urls:
                        urls.append(u)
            except Exception:
                pass

        fetched: list[tuple[str, str]] = []  # (url, text)
        for url in urls[:6]:
            try:
                text = web.run(action="fetch", url=url)
            except Exception:
                continue
            if text and len(text.strip()) > 300:
                fetched.append((url, text))
            if len(fetched) >= 5:
                break

        if not fetched:
            return ""

        return _render_research(query, fetched)


_SITE_AUGMENT_MAP = {
    # Match keyword -> 'site:' qualifier that surfaces authoritative docs
    "android": "site:developer.android.com OR site:stackoverflow.com",
    "kotlin": "site:kotlinlang.org OR site:developer.android.com",
    "ios": "site:developer.apple.com OR site:stackoverflow.com",
    "swift": "site:developer.apple.com OR site:swift.org",
    "react": "site:react.dev OR site:stackoverflow.com",
    "nextjs": "site:nextjs.org OR site:stackoverflow.com",
    "next.js": "site:nextjs.org",
    "vue": "site:vuejs.org",
    "django": "site:docs.djangoproject.com",
    "fastapi": "site:fastapi.tiangolo.com",
    "flask": "site:flask.palletsprojects.com",
    "postgres": "site:postgresql.org/docs OR site:stackoverflow.com",
    "docker": "site:docs.docker.com",
    "kubernetes": "site:kubernetes.io/docs",
    "k8s": "site:kubernetes.io/docs",
    "aws": "site:docs.aws.amazon.com OR site:stackoverflow.com",
    "gcp": "site:cloud.google.com/docs",
    "azure": "site:learn.microsoft.com/azure",
    "rust": "site:doc.rust-lang.org OR site:stackoverflow.com",
    "go": "site:go.dev OR site:stackoverflow.com",
    "golang": "site:go.dev OR site:stackoverflow.com",
    "python": "site:docs.python.org OR site:stackoverflow.com",
    "typescript": "site:typescriptlang.org OR site:stackoverflow.com",
    "javascript": "site:developer.mozilla.org",
    "github": "site:docs.github.com OR site:github.com",
    "git": "site:git-scm.com/docs OR site:stackoverflow.com",
}


def _site_augmented_query(query: str) -> str | None:
    """Add a `site:` qualifier when the query mentions a known tech.

    Used to surface authoritative docs (developer.android.com,
    react.dev, etc.) that the generic search engine often buries
    under blog spam.
    """
    q_lower = query.lower()
    for kw, site in _SITE_AUGMENT_MAP.items():
        if kw in q_lower:
            return f"{query} {site}"
    return None


def _extract_urls(search_output: str) -> list[str]:
    """Pull URLs from the web tool's search result text."""
    # Web tool typically returns lines like '1. Title - https://url'
    out: list[str] = []
    for line in (search_output or "").splitlines():
        m = re.search(r"https?://[^\s<>\"'`)]+", line)
        if m:
            url = m.group(0).rstrip(".,;:!?)]\"'")
            if url not in out:
                out.append(url)
    return out


def _render_research(query: str, sources: list[tuple[str, str]]) -> str:
    """Render the fetched research as a Claude/ChatGPT-Pro style brief
    that the LLM uses to synthesize a real answer.

    The output is what the LLM will see as the playbook's response.
    Because `pass_through_to_llm=True`, the agent runs ONE more LLM
    call after this with the fetched content in context — so the
    model synthesizes a final answer grounded in real sources.

    The instruction block is deliberately demanding: 'Pro-grade answer
    or none' — to nudge weaker LLMs (Llama 4 Scout) away from generic
    training-data fallback and into actually USING the fetched sources.
    """
    lines: list[str] = []
    lines.append(f"### Research brief — _{query}_")
    lines.append("")
    lines.append(
        f"_{len(sources)} authoritative sources fetched. Synthesize a "
        "**pro-grade answer** from THIS CONTENT, not training-data. "
        "Output shape required:_"
    )
    lines.append("")
    lines.append("```")
    lines.append("## Answer")
    lines.append("[3-6 lines: the actual answer, specific, concrete,")
    lines.append(" no hedge words. Cite [Source N] inline for each claim.]")
    lines.append("")
    lines.append("## Steps")
    lines.append("1. [Step] — [why, from source N]")
    lines.append("2. ...")
    lines.append("")
    lines.append("## Code")
    lines.append("```language")
    lines.append("// real code from sources, adapted to the question")
    lines.append("```")
    lines.append("")
    lines.append("## Caveats")
    lines.append("- [Real gotchas from sources — version, edge cases, etc.]")
    lines.append("")
    lines.append("## Sources")
    lines.append("- [1] URL — what it covers")
    lines.append("- [2] URL — ...")
    lines.append("```")
    lines.append("")
    lines.append(
        "**FORBIDDEN phrases (will be rejected and retried):** 'I cannot "
        "directly test', 'I was trained on', 'test on different devices', "
        "'consider using a library', 'depending on your requirements', "
        "'make sure to handle different Android versions', generic 'best "
        "practices' / 'recommendations' that aren't anchored in a source."
    )
    lines.append("")

    for i, (url, text) in enumerate(sources, 1):
        excerpt = _clean_text(text)[:3500]
        code_blocks = _extract_code_blocks(text)
        lines.append(f"#### Source {i}: {url}")
        lines.append("")
        lines.append("```text")
        lines.append(excerpt)
        lines.append("```")
        if code_blocks:
            for cb in code_blocks[:3]:
                lines.append("")
                lines.append("```")
                lines.append(cb[:1500])
                lines.append("```")
        lines.append("")
    return "\n".join(lines)


# Phrases that mark a response as "lazy training-data dump". The reflection
# layer in the agent looks for these and retries with a stronger prompt.
LAZY_RESPONSE_MARKERS = (
    "i cannot directly test",
    "i can't directly test",
    "i don't have the capability to directly",
    "i was trained on",
    "i have been trained on",
    "test on different devices",
    "test on various devices",
    "consider using a library",
    "depending on your specific requirements",
    "depending on your requirements",
    "exact implementation may vary",
    "make sure to handle different",
    "different android versions",
    "iterate through the following steps",
    "test and verify",
    "refine and adjust",
)


def detect_lazy_response(text: str) -> list[str]:
    """Return the list of lazy markers found in `text`. Empty list = OK.
    Used by the agent's reflection layer to decide whether to retry."""
    if not text:
        return []
    low = text.lower()
    return [m for m in LAZY_RESPONSE_MARKERS if m in low]


def _clean_text(s: str) -> str:
    """Strip the worst noise (script/style remnants, multiple blank lines)
    so the LLM gets a clean excerpt to reason over."""
    if not s:
        return ""
    # Remove HTML tags that survived the fetch.
    s = re.sub(r"<[^>]+>", "", s)
    # Drop runs of blank lines.
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    # Drop the common 'cookie banner' / 'subscribe' noise.
    for noise in [
        "Cookie Settings",
        "Accept All Cookies",
        "Manage Preferences",
        "Subscribe to our newsletter",
        "Sign up for",
    ]:
        s = s.replace(noise, "")
    return s.strip()


def _extract_code_blocks(text: str) -> list[str]:
    """Pull markdown code blocks (```...```) or HTML <pre><code>
    survivors out of fetched page content. Stack Overflow + most
    Android/Kotlin docs use one or the other."""
    blocks = re.findall(r"```[a-z]*\n([\s\S]+?)```", text, re.IGNORECASE)
    if not blocks:
        # Long indented blocks as a fallback signal
        blocks = re.findall(r"(?:^|\n)((?:    [^\n]+\n){3,})", text)
    return [b.strip() for b in blocks if b.strip()]
