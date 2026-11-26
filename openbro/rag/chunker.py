"""Recursive chunker.

Split on headings; anything still over `chunk_tokens` splits on
paragraphs, then sentences, then words. Neighbouring chunks overlap by
`overlap_tokens` so a sentence cut at a boundary is whole in one of them.
Every chunk keeps its page number and the heading it sits under.
Tables (spreadsheets / CSV) split by row blocks with the header row
repeated at the top of each chunk.

Tokens are estimated as characters / 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")


@dataclass
class Piece:
    text: str
    page: int | None
    heading: str

    @property
    def tokens(self) -> int:
        return max(1, len(self.text) // 4)


def _tok(text: str) -> int:
    return len(text) // 4


def _split_to_fit(text: str, max_chars: int) -> list[str]:
    """Paragraphs → sentences → words, until every part fits."""
    if len(text) <= max_chars:
        return [text]
    for splitter in (lambda t: re.split(r"\n\s*\n", t), _SENTENCE_RE.split, str.split):
        parts = [p for p in splitter(text) if p.strip()]
        if len(parts) > 1:
            out: list[str] = []
            for p in parts:
                out.extend(_split_to_fit(p, max_chars))
            return out
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) blocks; text before the first heading has heading ''."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if _HEADING_RE.match(line):
            sections.append((line.strip().lstrip("#").strip(), [line]))
        else:
            sections[-1][1].append(line)
    return [(h, "\n".join(lines).strip()) for h, lines in sections if "\n".join(lines).strip()]


def _tail(text: str, chars: int) -> str:
    if chars <= 0 or len(text) <= chars:
        return text if chars > 0 else ""
    cut = text[-chars:]
    space = cut.find(" ")
    return cut[space + 1 :] if 0 <= space < len(cut) // 2 else cut


def chunk_pages(
    pages: list[tuple[int | None, str]],
    chunk_tokens: int = 500,
    overlap_tokens: int = 50,
    kind: str = "text",
) -> list[Piece]:
    if kind == "table":
        return _chunk_table(pages, chunk_tokens)
    max_chars = chunk_tokens * 4
    overlap_chars = overlap_tokens * 4
    pieces: list[Piece] = []
    for page, text in pages:
        for heading, body in _sections(text or ""):
            current = ""
            for part in _split_to_fit(body, max_chars - overlap_chars):
                candidate = f"{current}\n\n{part}".strip() if current else part
                if len(candidate) <= max_chars:
                    current = candidate
                    continue
                pieces.append(Piece(current, page, heading))
                current = f"{_tail(current, overlap_chars)} {part}".strip()
            if current.strip():
                pieces.append(Piece(current, page, heading))
    return pieces


def _chunk_table(pages: list[tuple[int | None, str]], chunk_tokens: int) -> list[Piece]:
    max_chars = chunk_tokens * 4
    pieces: list[Piece] = []
    for page, text in pages:
        lines = [line for line in (text or "").splitlines() if line.strip()]
        if not lines:
            continue
        header, rows = lines[0], lines[1:]
        block: list[str] = []
        size = len(header)
        for row in rows:
            if block and size + len(row) + 1 > max_chars:
                pieces.append(Piece("\n".join([header, *block]), page, header[:80]))
                block, size = [], len(header)
            block.append(row[:max_chars])
            size += len(row) + 1
        if block or not rows:
            pieces.append(Piece("\n".join([header, *block]), page, header[:80]))
    return pieces
