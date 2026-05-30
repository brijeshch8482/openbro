"""Tests for ReflectionEscalator — multi-round strategy chain."""

from __future__ import annotations

import pytest

from openbro.core.reflection_escalator import (
    DEFAULT_STRATEGIES,
    ReflectionEscalator,
    Strategy,
)


def test_default_chain_order():
    """Default chain must be: harder_prompt → maverick swap → local
    fallback → simplify → honest_stop. Order matters — each round
    escalates the prior. Don't reshuffle without updating the
    docstring + REPL renderer."""
    names = [s.name for s in DEFAULT_STRATEGIES]
    assert names == [
        "harder_prompt",
        "model_swap_maverick",
        "fallback_local",
        "simplify_query",
        "honest_stop",
    ]


def test_escalator_advances_through_chain():
    esc = ReflectionEscalator()
    seen = []
    while True:
        s = esc.next_strategy(trigger="fabrication")
        if s is None:
            break
        seen.append(s.name)
    assert seen == [s.name for s in DEFAULT_STRATEGIES]
    assert esc.exhausted()
    assert esc.rounds_used() == len(DEFAULT_STRATEGIES)


def test_next_strategy_returns_none_after_exhaustion():
    esc = ReflectionEscalator(strategies=[Strategy("a", "A"), Strategy("b", "B")])
    assert esc.next_strategy().name == "a"
    assert esc.next_strategy().name == "b"
    assert esc.next_strategy() is None
    assert esc.exhausted()


def test_triggers_are_recorded():
    esc = ReflectionEscalator()
    esc.next_strategy(trigger="fabricated Output: block")
    esc.next_strategy(trigger="rendered tool args")
    assert esc.triggers == ["fabricated Output: block", "rendered tool args"]


def test_history_returns_copy():
    """history must be a defensive copy so callers can't mutate
    internal state."""
    esc = ReflectionEscalator()
    esc.next_strategy(trigger="x")
    h = esc.history
    h.append("HACK")
    assert "HACK" not in esc.history


def test_model_swap_strategies_carry_target():
    """Round 3 swaps to maverick; round 4 swaps to LOCAL sentinel.
    These are the only strategies with model_swap set."""
    by_name = {s.name: s for s in DEFAULT_STRATEGIES}
    assert (
        by_name["model_swap_maverick"].model_swap == "meta-llama/llama-4-maverick-17b-128e-instruct"
    )
    assert by_name["fallback_local"].model_swap == "LOCAL"
    assert by_name["harder_prompt"].model_swap is None
    assert by_name["honest_stop"].model_swap is None


def test_simplify_only_on_round_5():
    """Only simplify_query strategy strips transient context."""
    simplify_strategies = [s for s in DEFAULT_STRATEGIES if s.simplify]
    assert len(simplify_strategies) == 1
    assert simplify_strategies[0].name == "simplify_query"


def test_honest_stop_has_no_prompt_injection():
    """honest_stop should NOT inject a system message — the agent
    builds its own user-facing message via build_honest_stop_message."""
    honest = next(s for s in DEFAULT_STRATEGIES if s.is_honest_stop)
    assert honest.name == "honest_stop"
    assert honest.prompt_injection == ""


def test_build_honest_stop_message_lists_rounds_tried():
    esc = ReflectionEscalator()
    esc.next_strategy(trigger="fab1")
    esc.next_strategy(trigger="fab2")
    msg = esc.build_honest_stop_message(last_trigger="latest issue")
    assert "2 different strategies" in msg
    assert "harder_prompt" in msg
    assert "model_swap_maverick" in msg
    assert "latest issue" in msg


def test_build_honest_stop_message_with_no_rounds():
    esc = ReflectionEscalator()
    msg = esc.build_honest_stop_message()
    assert "0 different strategies" in msg or "no rounds" in msg.lower()


def test_summary_when_empty():
    esc = ReflectionEscalator()
    assert esc.summary() == "no rounds attempted"


def test_summary_comma_separated():
    esc = ReflectionEscalator()
    esc.next_strategy(trigger="x")
    esc.next_strategy(trigger="y")
    assert esc.summary() == "harder_prompt, model_swap_maverick"


def test_custom_strategy_chain():
    """Caller can pass a custom chain — useful for tests + future
    config-driven strategies."""
    custom = [
        Strategy("first", "first try"),
        Strategy("stop", "stop", is_honest_stop=True),
    ]
    esc = ReflectionEscalator(strategies=custom)
    assert esc.next_strategy().name == "first"
    assert esc.next_strategy().is_honest_stop
    assert esc.next_strategy() is None


@pytest.mark.parametrize("trigger", ["", None])
def test_empty_trigger_not_recorded(trigger):
    """An empty/None trigger doesn't get appended to triggers list —
    keeps the summary clean when the caller doesn't have a reason
    string handy."""
    esc = ReflectionEscalator()
    esc.next_strategy(trigger=trigger or "")
    assert esc.triggers == []


def test_strategies_have_required_fields():
    """Every default strategy needs name + description so the REPL
    renderer can show 'Round N/6 — <description>' without missing
    fields."""
    for s in DEFAULT_STRATEGIES:
        assert s.name, f"strategy missing name: {s}"
        assert s.description, f"strategy missing description: {s}"
