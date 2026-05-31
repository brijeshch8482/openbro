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


# Explicit "go research this" / "search and tell me" phrases. When ANY
# of these appear, fire the playbook regardless of tech-keyword match —
# the user is asking for grounded sources directly.
_EXPLICIT_RESEARCH_PATTERNS = [
    re.compile(r"\bresearch\s+(krke|kar\s+ke|kar\s+ke\s+batao|and\s+tell)\b", re.IGNORECASE),
    re.compile(r"\bsearch\s+(online|krke|kar\s+ke|the\s+web|google)\b", re.IGNORECASE),
    re.compile(r"\bfind\s+online\b", re.IGNORECASE),
    re.compile(r"\b(deep|proper|detailed)\s+research\b", re.IGNORECASE),
    re.compile(r"\b(web\s+pe|online\s+pe)\s+(dekho|search|dhoondh)\b", re.IGNORECASE),
    re.compile(r"\bgo\s+research\b", re.IGNORECASE),
]


def _looks_technical(query: str) -> bool:
    """Both a tech keyword AND a question shape must be present.

    OR an explicit 'research / search the web / online dhoondh' phrase
    overrides the tech-keyword requirement — user is explicitly asking
    for grounded sources so we honour it even on non-tech questions.
    """
    if any(p.search(query) for p in _EXPLICIT_RESEARCH_PATTERNS):
        return True
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

        # Live progress events the REPL renders as bullet steps, so the
        # user SEES the research happening turn-by-turn instead of just
        # watching a spinner. Without these, a 20-second research feels
        # like the agent is frozen.
        from openbro.core.activity import get_bus

        bus = get_bus()

        # ─── Deep research: multi-round search ────────────────────────
        # Round 1: broad search. Round 2: site-augmented for the
        # detected tech domain. Round 3 (if results thin): a refined
        # query that prepends 'how to' / 'tutorial' to surface
        # walkthrough-style pages over reference docs.
        urls: list[str] = []

        bus.emit("research_step", "searching the web…", query=query)
        try:
            search_raw = web.run(action="search", query=query)
        except Exception:
            return ""
        urls.extend(_extract_urls(search_raw))

        site_query = _site_augmented_query(query)
        if site_query and site_query != query:
            bus.emit("research_step", "deep-search on official docs…", query=site_query)
            try:
                extra = web.run(action="search", query=site_query)
                for u in _extract_urls(extra):
                    if u not in urls:
                        urls.append(u)
            except Exception:
                pass

        # Refined how-to round when results are thin OR when the user
        # asked a 'how do I' question (most likely to want a tutorial).
        if len(urls) < 4 or re.search(r"\bhow\s+(do|to|can)\b", query, re.IGNORECASE):
            howto_query = _howto_refined_query(query)
            if howto_query and howto_query != query:
                bus.emit("research_step", "searching for tutorials…", query=howto_query)
                try:
                    extra = web.run(action="search", query=howto_query)
                    for u in _extract_urls(extra):
                        if u not in urls:
                            urls.append(u)
                except Exception:
                    pass

        if not urls:
            return ""

        # Rank URLs: prefer authoritative domains (dev docs / SO /
        # GitHub) over blog spam. Top 8 candidates go into the fetch
        # pool; we keep fetching until we have 6 substantive pages.
        urls = _rank_urls(urls)
        fetched: list[tuple[str, str]] = []  # (url, text)

        for i, url in enumerate(urls[:10], 1):
            bus.emit(
                "research_step",
                f"fetching ({i}/{min(10, len(urls))}): {_short_domain(url)}",
                url=url,
            )
            try:
                text = web.run(action="fetch", url=url)
            except Exception:
                continue
            if text and len(text.strip()) > 300:
                fetched.append((url, text))
            if len(fetched) >= 6:
                break

        if not fetched:
            return ""

        total_chars = sum(len(t) for _, t in fetched)
        bus.emit(
            "research_step",
            f"gathered {len(fetched)} sources ({total_chars // 1000}K chars) — synthesising…",
        )

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


def _howto_refined_query(query: str) -> str:
    """Refine the query to surface walkthrough/tutorial pages over
    reference docs. Cheap heuristic: if not already a 'how to', prefix
    with 'tutorial'; strip trailing '?' since search engines downrank
    question marks.
    """
    q = query.strip().rstrip("?")
    if re.search(r"\bhow\s+(do|to|can)\b", q, re.IGNORECASE):
        return q + " tutorial example"
    if re.search(r"\bwhat\b", q, re.IGNORECASE):
        return "how to " + q.replace("what can we do for", "").replace("what is", "").strip()
    return q + " tutorial example"


# Authoritative domains we want to surface first. Bigger weight = higher
# rank. Matched as substring on the URL's netloc.
_DOMAIN_WEIGHTS = {
    "developer.android.com": 10,
    "developer.apple.com": 10,
    "kotlinlang.org": 10,
    "docs.python.org": 10,
    "docs.djangoproject.com": 10,
    "fastapi.tiangolo.com": 10,
    "react.dev": 10,
    "nextjs.org": 10,
    "vuejs.org": 10,
    "docs.docker.com": 10,
    "kubernetes.io": 10,
    "docs.aws.amazon.com": 10,
    "cloud.google.com": 10,
    "learn.microsoft.com": 10,
    "developer.mozilla.org": 10,
    "git-scm.com": 10,
    "go.dev": 10,
    "doc.rust-lang.org": 10,
    "stackoverflow.com": 8,
    "github.com": 7,
    "medium.com": 3,
    "dev.to": 4,
}


def _rank_urls(urls: list[str]) -> list[str]:
    """Sort URLs so authoritative docs come first. Ties keep original
    order so DDG's relevance signal still counts as a secondary key."""
    from urllib.parse import urlparse

    scored: list[tuple[int, int, str]] = []
    for i, u in enumerate(urls):
        netloc = urlparse(u).netloc.lower()
        score = 0
        for domain, weight in _DOMAIN_WEIGHTS.items():
            if domain in netloc:
                score = max(score, weight)
        scored.append((-score, i, u))  # negative so higher score sorts first
    scored.sort()
    return [u for _, _, u in scored]


def _short_domain(url: str) -> str:
    """Strip protocol + 'www.' for status-line display."""
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or url[:40]


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
    # Training-data disclaimers
    "i cannot directly test",
    "i can't directly test",
    "i don't have the capability to directly",
    "i was trained on",
    "i have been trained on",
    # 'test it yourself' filler
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
    # Meta-commentary instead of action (captured 2026-05-30): user
    # asked 'i think you did not call you llm', agent responded with
    # 7 numbered steps explaining how it's 'designed to use the
    # available tools' instead of just calling them.
    "i'm designed to use",
    "i am designed to use",
    "my primary goal is to follow",
    "without a specific task, it's challenging",
    "without a specific task, it is challenging",
    "could you please provide more context",
    "could you provide more context",
    "please let me know how i can assist",
    "i'm here to help",
    "i am here to help",
    "i have access to the following tools",
    # 'helpful suggestion' filler
    "you may want to consider",
    "you can try the following",
    "consult with",
    "seek professional help",
    "contacting the file's creator",
)

# Patterns that signal "the model wrote code AND a fake Output: block
# in chat instead of actually calling the python/shell tool". Captured
# failure: user asked to read D:\\desktop\\NDLS_FDB. Llama 4 Scout
# wrote `import os; print(os.listdir(...))` in chat text + invented
# `Output: ['NDLS_FDB', 'other_file.txt']` with NO tool_call ever
# made (0 tool_start events on the bus, single LLM step at 2180↓).
# The reflection layer now catches this and retries with a forced-
# tool-use instruction.
_CODE_FENCE = re.compile(r"```[a-z]*\n", re.IGNORECASE)
_FAKE_OUTPUT_PATTERN = re.compile(
    r"\n\s*(Output|Result|Returns?)\s*[:\-]\s*\n*\s*```", re.IGNORECASE
)

# Captured 2026-05-30: user asked 'iss time mera phone laptop se
# connected hai ya nhi?'. Reflection retry fired correctly on round 1.
# Round 2 then produced this:
#   'Chal, network tool se apne device ka connection status dekhte
#    hain. Connection Status. network action='ip''
# i.e. it RENDERED tool args as chat text — `network action='ip'` —
# but never emitted a real tool call. The Output: detector missed
# this because there was no fake output block. We now catch the
# rendered-args pattern explicitly.
_RENDERED_TOOL_ARGS = re.compile(
    # Two shapes:
    #   1. `network action='ip'` — original key=value (with space)
    #   2. `file_ops{"action": "read", ...}` — bare JSON args (no
    #      space). Captured 2026-05-30 from llama-3.3-70b when user
    #      asked to debug D:\MapRadiusKotlin: agent typed
    #      `file_ops{"action": "read", "path": "..."}` as chat text
    #      and the rescue lift didn't trigger.
    r"\b(network|python|shell|file_ops|web|browser|app|cli_agent|memory|document|"
    r"tech_research|recap|playbook|notification|process|system_info|system_control|"
    r"clipboard|screenshot|sticky_notes|datetime|download|word|excel)"
    r"(?:\s+(action|command|tool|path|url|query|target)\s*=|"
    r"\s*\{\s*[\"'](action|command|tool|path|url|query|target|file|name)[\"']\s*:)",
    re.IGNORECASE,
)

# 'Let me check X' / 'dekhte hain' style promises. Only counts as
# fabrication when no tool_calls were made — if a real call follows,
# the promise was honest.
_PROMISE_WITHOUT_ACTION = re.compile(
    r"\b(let me (check|try|run|use|call|see|look|verify|test)|"
    r"i('?ll| will) (check|run|try|use|call|see|look|verify|test)|"
    r"dekhte hain|check krta hau|check karta hu|"
    r"chal\s+\w+\s+(tool|se))\b",
    re.IGNORECASE,
)

# Captured 2026-05-31: user asked 'aaj delhi ka temp kya hai?'. Model
# responded 'Aaj Delhi ka temperature 28°C hai... Ye data maine web
# tool se fetch kiya hai.' — NO tool call. Pure fabrication of the
# temperature AND the claim of having called the tool.
# Then: 'mere desktop yt open kro' → 'haan boss, YouTube open kar
# diya hai. Maine browser tool use kiya...' — no tool call, no app
# opened. Same lie.
# Then: 'photoshop open kr doge?' → same shape.
#
# These are CLAIMS-OF-COMPLETION without execution. Detector fires
# when (tool_calls_made == 0 this turn) AND the response uses past-
# tense or completion phrasing. The reflection layer then re-prompts
# the model to ACTUALLY emit the tool call.
_FALSE_COMPLETION_CLAIMS = re.compile(
    # Hindi / Hinglish past-tense action claims
    r"\b(kar diya hai|kr diya hai|ho gaya hai|hogaya hai|"
    r"khol diya|khol diya hai|open kar diya|open kr diya|"
    r"fetch kiya|fetch kr liya|tool use kiya|tool se fetch|"
    r"tool se kiya|tool se khola|tool ka use kiya|"
    r"maine\s+\w+\s+(tool\s+)?(use\s+kiya|use\s+kr\s+liya|"
    r"chala|chalaya|run kiya|khola|kholi|kholega)|"
    # Past-tense factual claims that look like tool output
    r"yaha hai|ye raha|ye hai aapka|ye data maine|temperature\s+\d|"
    r"humidity\s+\d|location is|"
    # English claims
    r"\b(i('?ve| have) (opened|run|fetched|called|checked|verified|"
    r"used|invoked|launched)|i (called|ran|fetched|opened|launched|"
    r"checked|verified|used|invoked) (the\s+)?\w+\s+(tool|api|command)|"
    r"successfully (opened|launched|ran|fetched|completed|invoked)|"
    r"using the\s+\w+\s+tool i (got|found|fetched|opened)))\b",
    re.IGNORECASE,
)


def _is_pure_code_ask_response(text: str, user_prompt: str | None) -> bool:
    """Helper: when the user asked for code and the response is
    structurally an explanation/answer (not a tool-claim), don't
    flag completion phrases — they're describing the code, not
    claiming a tool ran. Conservative: only suppresses when
    response contains code fences."""
    if not user_prompt or not user_asked_for_code(user_prompt):
        return False
    return "```" in text


# Captured 2026-05-30: user asked 'bro mujhe full implementation
# chahiye to tum full code likho' for kiosk mode in Android Studio.
# Model wrote 4-5 Java code blocks (Manifest, KioskActivity, etc.) —
# THE ANSWER IS THE CODE. No tool call needed. The 2+ code blocks
# rule false-positived → escalator fired → maverick unavailable →
# local context overflow → error shown. Detector now skips the
# multiple-code-blocks check when the user prompt explicitly asks
# for code/implementation/example.
_USER_ASKED_FOR_CODE = re.compile(
    # 'write/show/give me [a python] function/code/example/...'
    r"\b(write|show|give|paste|likh(o|do|na)?|de|do)\s+(me\s+)?"
    r"(the\s+|a\s+|some\s+|complete\s+|full\s+)?(\w+\s+)?"
    r"(code|implementation|example|snippet|script|function|class|file|"
    r"sample|source)\b|"
    # Direct phrases (English + Hindi)
    r"\b(full\s+(code|implementation|example|snippet|source)|"
    r"code\s+likh(o|do|na)?|implementation\s+chahiye|"
    r"chahiye.*implementation|full\s+source|sample\s+code|"
    r"example\s+code|complete\s+code|code\s+chahiye|code\s+do)\b|"
    # 'how do I implement/write/build X'
    r"\b(how (do|can) (i|we|you) (write|implement|build|create))\b",
    re.IGNORECASE,
)


def user_asked_for_code(user_prompt: str | None) -> bool:
    """Return True when the latest user prompt explicitly asks for
    code/implementation/sample. Used to short-circuit the
    multiple-code-blocks fabrication check (false positive captured
    when user said 'full code likh' and model wrote 4 java files)."""
    if not user_prompt:
        return False
    return bool(_USER_ASKED_FOR_CODE.search(user_prompt))


def detect_fabricated_tool_call(
    text: str,
    tool_calls_made: int,
    user_prompt: str | None = None,
) -> str | None:
    """Detect responses that LOOK like a tool ran but actually didn't.

    Returns a reason string if detected, None otherwise. Skips the
    check when at least one real tool call was made in this turn —
    the model is allowed to render its tool args as code-styled chat
    AFTER the tool actually ran.

    `user_prompt` is the latest user message. When the user
    explicitly asked for code/implementation/example, the multiple-
    code-blocks rule is skipped — code IS the answer, not
    fabrication.

    Four shapes are caught:
      1. fence + fake `Output:` / `Result:` block (NDLS_FDB failure)
      2. rendered tool args like `network action='ip'` (phone-conn
         failure)
      3. multiple chat-text code blocks with no call — unless the
         user explicitly asked for code (kiosk-mode failure)
      4. 'Let me check X' / 'dekhte hain' promise with no call (drop-
         and-run failure)
    """
    if not text or tool_calls_made > 0:
        return None
    fence_count = len(_CODE_FENCE.findall(text))
    # 1. Has Code AND a fake Output/Result/Returns: block right after.
    if fence_count >= 1 and _FAKE_OUTPUT_PATTERN.search(text):
        return "fabricated 'Output:' block after chat-text code"
    # 2. Rendered tool args without making the call. Strong signal —
    # model literally typed out the tool invocation.
    if _RENDERED_TOOL_ARGS.search(text):
        return "rendered tool-args (e.g. `network action='ip'`) without making the call"
    # 3. Two+ code blocks with no real tool call = highly suspect —
    # UNLESS the user explicitly asked for code, in which case the
    # code blocks ARE the answer (not a fabricated tool call).
    if fence_count >= 2 and not user_asked_for_code(user_prompt):
        return "multiple chat-text code blocks with no tool calls made"
    # 4. 'Let me check X' promise without execution. Cap on length so
    # we don't flag long honest answers that happen to include the
    # phrase.
    if _PROMISE_WITHOUT_ACTION.search(text) and len(text) < 600:
        return "promised an action ('let me check / dekhte hain') but no tool call made"
    # 5. Claims of completion ('kar diya hai', 'YouTube open kar
    # diya', 'web tool se fetch kiya hai') with no tool call made.
    # Suppress when user explicitly asked for code AND response is
    # code-shaped — those are honest answers describing code, not
    # tool-execution claims.
    if _FALSE_COMPLETION_CLAIMS.search(text) and not _is_pure_code_ask_response(text, user_prompt):
        return "claimed action completion ('kar diya hai' / 'fetched') without making any tool call"
    return None


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
