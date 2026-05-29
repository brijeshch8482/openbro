"""Tests for the multi-intent decomposer."""

from __future__ import annotations

import pytest

from openbro.core.decompose import decompose

# ─── Single-intent (no split) ──────────────────────────────────────────


@pytest.mark.parametrize(
    "q",
    [
        "mai kaha hu",
        "kya time hua",
        "D drive me kitne pdfs hain",
        "close my browser",
        "open chrome",
        "",
        "single sentence with no conjunctions",
    ],
)
def test_single_intent_not_split(q):
    out = decompose(q)
    assert len(out) == 1


# ─── Hinglish conjunctions ─────────────────────────────────────────────


def test_aur_splits_two_clauses():
    out = decompose("D drive me fee pdfs dhoondh aur sab open kar")
    assert len(out) == 2
    assert "fee pdfs" in out[0].lower()
    assert "open" in out[1].lower()


def test_phir_splits():
    out = decompose("browser band kar phir vscode khol")
    assert len(out) == 2


def test_fir_splits():
    out = decompose("D drive ka health check kar fir Brave kholo")
    assert len(out) == 2
    assert "health" in out[0].lower()
    assert "brave" in out[1].lower()


def test_aur_phir_splits():
    out = decompose("logs dekho aur phir error fix kro")
    assert len(out) == 2


# ─── English conjunctions ──────────────────────────────────────────────


def test_then_splits():
    out = decompose("close chrome then open firefox")
    assert len(out) == 2


def test_and_then_splits():
    out = decompose("download report.pdf and then open it in browser")
    assert len(out) == 2


def test_after_that_splits():
    out = decompose("show running processes after that close chrome")
    assert len(out) == 2


def test_double_ampersand_splits():
    out = decompose("git pull && git status")
    assert len(out) == 2


# ─── Lists ─────────────────────────────────────────────────────────────


def test_numbered_list_split():
    out = decompose("1. close chrome 2. open vscode 3. read README.md")
    assert len(out) == 3
    assert "close" in out[0].lower()
    assert "vscode" in out[1].lower()
    assert "readme" in out[2].lower()


def test_numbered_list_with_parens():
    out = decompose("1) check disk 2) check memory 3) check cpu")
    assert len(out) == 3


def test_bullet_list_split():
    out = decompose("- open chrome\n- close vscode\n- run tests")
    assert len(out) == 3


# ─── Sentence boundaries (only when imperative) ────────────────────────


def test_two_imperative_sentences_split():
    out = decompose("Open chrome. Close vscode.")
    assert len(out) == 2


def test_question_sentences_not_split():
    """A question like 'kya time hua. mai kaha hu' shouldn't blindly split
    on '.' — the decomposer requires imperative verbs."""
    out = decompose("kya time hua. abhi.")
    # The second fragment 'abhi.' has no imperative verb -> no split.
    assert len(out) == 1


# ─── Edge cases ────────────────────────────────────────────────────────


def test_aur_inside_word_does_not_split():
    """'aurat' should not split because 'aur' is inside a token."""
    out = decompose("aurat ka phone number dho")
    assert len(out) == 1


def test_aur_at_start_ignored():
    """'aur kuch bhi' isn't a compound — 'aur' isn't between two clauses."""
    out = decompose("aur kuch karna hai")
    assert len(out) == 1


def test_too_short_fragment_rejects_split():
    """Splitting that produces a 2-char stub is suspicious."""
    out = decompose("a aur b")
    # Both halves are sub-3 chars -> filtered to 0 fragments -> falls
    # through to the single-intent path
    assert len(out) == 1


def test_max_subtasks_cap():
    # 10 sub-queries, cap at default 8
    q = " then ".join(f"step{i}" for i in range(10))
    out = decompose(q)
    assert len(out) <= 8


def test_custom_max_subtasks():
    # Use fragments >= MIN_FRAGMENT_CHARS so none get filtered out
    q = "open chrome then open firefox then open vscode then open word then open excel"
    out = decompose(q, max_subtasks=3)
    assert len(out) == 3


def test_whitespace_only_returns_single_empty():
    assert decompose("   ") == [""]


def test_real_world_compound_examples():
    cases = [
        ("D drive me pdf dhoondh aur Brave open kar", 2),
        ("git status check kar fir push kr", 2),
        ("close chrome. open firefox.", 2),
        ("1. close chrome 2. open firefox 3. run tests", 3),
        ("system health check kar aur phir process list dikha", 2),
    ]
    for q, expected in cases:
        out = decompose(q)
        assert len(out) == expected, f"{q!r} -> {out}"
