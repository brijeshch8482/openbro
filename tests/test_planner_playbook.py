"""Tests for PlannerPlaybook — complexity-driven planning hint."""

from __future__ import annotations

import pytest

from openbro.playbooks.base import PlaybookContext
from openbro.playbooks.builtin.planner import PlannerPlaybook


@pytest.fixture
def pb():
    return PlannerPlaybook()


# ─── Explicit plan requests — always fire ──────────────────────────


@pytest.mark.parametrize(
    "q",
    [
        "step by step batao kya kya kar sakte hain",
        "step-by-step explain the auth flow",
        "break this down into steps for me",
        "make a plan to migrate the database",
        "plan banao for refactoring",
        "ek ek karke explain kar yeh code",
        "deeply explain how django auth works",
        "walk me through setting up CI",
        "compare these two files and explain which is better",
    ],
)
def test_explicit_plan_phrases_match(pb, q):
    m = pb.match(q)
    assert m is not None, f"explicit plan phrase should match: {q!r}"
    assert m.playbook is pb


# ─── Implicit complexity — multi-verb long queries ─────────────────


def test_long_multi_verb_query_matches(pb):
    q = (
        "Tell me how to setup the django auth system, then verify "
        "the migration works, configure the JWT settings and run "
        "the test suite to check everything passes?"
    )
    m = pb.match(q)
    assert m is not None


def test_short_simple_query_does_not_match(pb):
    """Quick questions shouldn't trigger the planner — extra context
    would just bloat the LLM call."""
    for q in [
        "kya time hua",
        "what is python",
        "show me the date",
        "list files",
    ]:
        assert pb.match(q) is None, f"short query should NOT match: {q!r}"


def test_long_but_single_verb_query_does_not_match(pb):
    """Long but conceptual single-action query — planner adds no
    value, ReAct loop handles it directly."""
    q = "Tell me the history of the django web framework please."
    assert pb.match(q) is None


def test_empty_query_does_not_crash(pb):
    assert pb.match("") is None
    assert pb.match("   ") is None
    assert pb.match(None) is None  # type: ignore[arg-type]


# ─── Output is the planning marker ────────────────────────────────


def test_execute_returns_transient_plan_marker(pb):
    """The playbook's output must start with [TRANSIENT_PLAN] so the
    agent's pass-through layer recognises it and the post-synthesis
    prune removes it before the next turn."""
    ctx = PlaybookContext(user_input="step by step batao", tool_registry=None)  # type: ignore[arg-type]
    out = pb.execute(ctx)
    assert out.startswith("[TRANSIENT_PLAN]")
    assert "numbered plan" in out.lower()
    # Must instruct the LLM to keep step count tight.
    assert "1-5" in out


def test_pass_through_to_llm_is_set(pb):
    """Without this flag, the agent would return the planner's hint
    as the final response — which is just the instruction text, not
    an actual answer."""
    assert pb.pass_through_to_llm is True


# ─── Regression cases from captured user messages ──────────────────


def test_question_from_2026_05_30_matches(pb):
    """User asked: 'bhai openbro kyo nhi question ko break kr ke
    answer ka ek path bna kr step by step krta?? jaise claude krta
    hai?' — paraphrase of this in the planner's domain should fire."""
    for q in [
        "openbro ko question break karke step by step karwana hai",
        "break it down into a step-by-step plan please",
        "kya tu step-by-step kar sakta hai is task ko?",
    ]:
        assert pb.match(q) is not None, f"should match: {q!r}"


def test_compare_and_explain_pattern_matches(pb):
    """Compare + explain is a 2-step intent — fires the planner so
    the LLM emits 'Step 1: compare; Step 2: explain' instead of
    diving into half an answer."""
    q = "Compare these two log files and explain which one has the bug"
    assert pb.match(q) is not None
