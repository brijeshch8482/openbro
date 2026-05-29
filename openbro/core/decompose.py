"""Multi-intent decomposition — split a compound query into ordered sub-queries.

When the user types 'fee documents dhoondh aur sab open kar' or 'D drive ka
health check kar fir Brave kholo', we want to run two distinct workflows in
sequence. The agent (and the Playbook registry) currently handle ONE intent
per turn; the decomposer is the cheap, deterministic front-end that splits
the compound into atomic sub-queries the rest of the pipeline already knows.

This stays rule-based on purpose:
  - cheap (microseconds)
  - deterministic (same input -> same split every time)
  - testable (no LLM in the loop)

Heuristics:
  - Imperative conjunctions: ' aur ', ' fir ', ' phir ', ' then ', ' and then ',
    ' & ', ' && '
  - Sentence boundaries: '. ', ';', newline (only when both halves are
    imperatives — we don't want to split a question that just happens to
    contain a period)
  - Numbered lists: '1. X 2. Y 3. Z' — pulled out as N sub-queries
  - Bullet lists: '- X\n- Y\n- Z'

Split is rejected when the result would produce a too-short stub
(< MIN_FRAGMENT_CHARS), since that usually means a punctuation false
positive (e.g. 'kya time hua. abhi.' shouldn't split).
"""

from __future__ import annotations

import re

MIN_FRAGMENT_CHARS = 3
MAX_SUBTASKS = 8  # safety cap — beyond this we ask the user to slow down

# Conjunctions ordered by specificity (longer first so we don't split
# 'phir' inside 'phirana'). All match as word boundaries.
_CONJUNCTIONS = [
    r"\s+and\s+then\s+",
    r"\s+after\s+that\s+",
    r"\s+then\s+",
    r"\s+phir\s+",
    r"\s+fir\s+",
    r"\s+aur\s+phir\s+",
    r"\s+aur\s+fir\s+",
    r"\s+aur\s+",
    r"\s*&&\s*",
    r"\s+&\s+",
]
_CONJUNCTION_RE = re.compile("|".join(_CONJUNCTIONS), re.IGNORECASE)

# Numbered list pattern: '1. X 2. Y 3. Z' (or '1) X 2) Y')
_NUMBERED_LIST_RE = re.compile(r"(?:^|\s)(\d+)[.)]\s+", re.MULTILINE)

# Bullet list pattern (- or *), at start of line
_BULLET_LIST_RE = re.compile(r"(?:^|\n)\s*[-*]\s+", re.MULTILINE)


def decompose(query: str, max_subtasks: int = MAX_SUBTASKS) -> list[str]:
    """Split a compound query into ordered sub-queries.

    Returns the original query as a single-item list when the input is
    not compound. Never returns an empty list (caller can rely on at
    least one item being present).
    """
    if not query or not query.strip():
        return [""]
    q = query.strip()

    # 1. Numbered lists win — most explicit signal.
    numbered = _split_numbered(q)
    if numbered and len(numbered) > 1:
        return numbered[:max_subtasks]

    # 2. Bullet lists.
    bulleted = _split_bullets(q)
    if bulleted and len(bulleted) > 1:
        return bulleted[:max_subtasks]

    # 3. Conjunctions.
    parts = _CONJUNCTION_RE.split(q)
    parts = [p.strip(" ,.;") for p in parts if p and p.strip()]
    # Reject splits that produced a tiny fragment — usually a false
    # positive on 'aur' inside a noun ('aur kuch chahiye').
    parts = [p for p in parts if len(p) >= MIN_FRAGMENT_CHARS]
    if len(parts) > 1:
        return parts[:max_subtasks]

    # 4. Sentence boundaries, but ONLY if both halves look imperative
    # (heuristic: contain a verb-ish word in known imperative set).
    sentence_parts = _split_sentences(q)
    if sentence_parts and len(sentence_parts) > 1:
        return sentence_parts[:max_subtasks]

    # Nothing compound found.
    return [q]


def _split_numbered(q: str) -> list[str]:
    matches = list(_NUMBERED_LIST_RE.finditer(q))
    if len(matches) < 2:
        return []
    out: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(q)
        fragment = q[m.end() : end].strip()
        if len(fragment) >= MIN_FRAGMENT_CHARS:
            out.append(fragment)
    return out


def _split_bullets(q: str) -> list[str]:
    """Split a bullet list into individual items."""
    if not _BULLET_LIST_RE.search(q):
        return []
    parts = _BULLET_LIST_RE.split(q)
    parts = [p.strip() for p in parts if p and p.strip()]
    parts = [p for p in parts if len(p) >= MIN_FRAGMENT_CHARS]
    return parts if len(parts) > 1 else []


# Imperative-ish verbs we recognise in Hinglish + English. Used to gate
# sentence-level splitting so we don't accidentally cut a single question.
_IMPERATIVE_TOKENS = {
    # Hinglish
    "kar",
    "karo",
    "kr",
    "kro",
    "dekho",
    "dekh",
    "dhoondh",
    "dhundh",
    "khol",
    "kholo",
    "open",
    "band",
    "bandh",
    "bata",
    "batao",
    "btao",
    "btado",
    "chala",
    "chalu",
    "padh",
    "padho",
    "list",
    "show",
    "find",
    "search",
    "create",
    "make",
    "delete",
    "remove",
    "run",
    "check",
    "close",
    "launch",
    "start",
    "stop",
    "kill",
    "send",
    "download",
    "save",
    "lo",
    "le",
    "do",
    "de",
    "diya",
    "dijiye",
}


def _looks_imperative(text: str) -> bool:
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(tok)}\b", lower) for tok in _IMPERATIVE_TOKENS)


def _split_sentences(q: str) -> list[str]:
    # Split on sentence-ending punctuation followed by space or EOL.
    # Don't split on decimal numbers ('3.14') — require a SPACE or end
    # after the punctuation.
    raw = re.split(r"(?<=[.!?;])\s+|\n+", q)
    parts = [p.strip(" ,.;") for p in raw if p and p.strip()]
    parts = [p for p in parts if len(p) >= MIN_FRAGMENT_CHARS]
    if len(parts) < 2:
        return []
    # Both halves must read as imperatives. This protects single-sentence
    # questions that happen to contain a period.
    if not all(_looks_imperative(p) for p in parts):
        return []
    return parts
