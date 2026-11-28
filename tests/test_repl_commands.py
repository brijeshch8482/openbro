"""REPL command handling, the text-tool turn path, and quiet optional imports."""

import sys
from pathlib import Path

import pytest

from openbro.cli.repl import _slash_command


def test_slash_command_strips_slash():
    assert _slash_command("/help") == "help"
    assert _slash_command("/docs add D:/notes") == "docs add D:/notes"
    assert _slash_command("/memory open") == "memory open"


def test_slash_command_leaves_other_input():
    assert _slash_command("help") is None
    assert _slash_command("find a headphone under 3k") is None
    assert _slash_command("/usr/bin/python kya hai") is None  # a path, not a command
    assert _slash_command("/") is None
    assert _slash_command("/ hello") is None


def test_repl_runs_every_turn_through_the_agent_loop():
    # Text-only providers emit <tool_call> tags that only Agent.chat parses
    # and runs. The REPL must never stream them raw to the screen.
    source = (Path(__file__).parent.parent / "openbro" / "cli" / "repl.py").read_text(
        encoding="utf-8"
    )
    assert "agent.stream_chat(" not in source
    assert "response = _run_turn(agent, user_input)" in source


def test_embedder_checks_torch_without_importing(monkeypatch):
    from openbro.rag import embedder
    from openbro.utils import optional

    present = {"sentence_transformers"}  # installed, but torch is not
    monkeypatch.setattr(optional, "has_module", lambda name: name in present)
    monkeypatch.setattr(embedder, "has_module", lambda name: name in present)
    monkeypatch.setattr(
        embedder,
        "sentence_transformers_usable",
        optional.sentence_transformers_usable,
    )
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)

    assert embedder.LocalEmbedder.available() is False
    assert "sentence_transformers" not in sys.modules

    present.add("torch")
    assert embedder.LocalEmbedder.available() is True
    assert "sentence_transformers" not in sys.modules


def test_live_status_spinner_animates():
    import time
    from unittest.mock import MagicMock

    from rich.console import Console

    from openbro.cli.repl import _LiveStatus

    status = _LiveStatus(MagicMock(_turn_tokens_in=0, _turn_tokens_out=0), Console())
    frames = []
    for _ in range(3):
        c = Console(width=100, record=True)
        c.print(status._render())
        frames.append(c.export_text().split()[0])
        time.sleep(0.12)
    assert len(set(frames)) > 1  # the spinner moves between renders


def test_has_module():
    from openbro.utils.optional import has_module

    assert has_module("json") is True
    assert has_module("definitely_not_a_module_xyz") is False


# ─── /model ──────────────────────────────────────────────────────────


def _fake_config():
    return {
        "llm": {"provider": "groq", "model": "openai/gpt-oss-120b", "fallback": None},
        "providers": {"groq": {"api_key": "k", "model": "openai/gpt-oss-120b"}},
    }


def _named(name):
    from unittest.mock import MagicMock

    p = MagicMock()
    p.name.return_value = name
    return p


def test_model_subcommands_are_not_switches(monkeypatch):
    from unittest.mock import MagicMock

    from openbro.cli import repl

    called = []
    monkeypatch.setattr(repl, "_switch_model", lambda *a: called.append(a))
    import openbro.cli.model_manager as mm

    monkeypatch.setattr(mm, "list_available", lambda *a, **k: None)
    repl._handle_command("model list", MagicMock())
    assert called == []


# ─── Ctrl+C and resume hint ──────────────────────────────────────────


class _SlowAgent:
    def __init__(self, seconds=5.0, answer="done", error=None):
        self.seconds, self.answer, self.error = seconds, answer, error

    def chat(self, text):
        import time

        time.sleep(self.seconds)
        if self.error:
            raise self.error
        return self.answer


def test_run_turn_returns_answer_and_errors():
    from openbro.cli.repl import _run_turn

    assert _run_turn(_SlowAgent(0.05), "hi", ctrl_c=lambda: False) == "done"
    with pytest.raises(ValueError):
        _run_turn(_SlowAgent(0.05, error=ValueError("boom")), "hi", ctrl_c=lambda: False)


def test_ctrl_c_key_stops_a_long_answer_at_once():
    import time

    from openbro.cli.repl import _run_turn

    presses = iter([False, False, True])
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        _run_turn(_SlowAgent(10), "hi", poll=0.05, ctrl_c=lambda: next(presses, True))
    assert time.monotonic() - t0 < 2


def test_sigint_stops_a_long_answer_at_once():
    # A real Ctrl+C arrives as SIGINT in the main thread; interrupt_main()
    # delivers it exactly like that.
    import _thread
    import threading
    import time

    from openbro.cli.repl import _run_turn

    threading.Timer(0.3, _thread.interrupt_main).start()
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        _run_turn(_SlowAgent(10), "hi", ctrl_c=lambda: False)
    assert time.monotonic() - t0 < 2


def test_resume_hint_only_after_a_message(capsys):
    from unittest.mock import MagicMock

    from openbro.cli import repl
    from openbro.llm.base import Message

    agent = MagicMock()
    agent.memory.session_id = "abc123def456"
    agent.history = [Message(role="system", content="s")]
    with repl.console.capture() as cap:
        repl._print_resume_hint(agent)
    assert cap.get() == ""
    agent.history.append(Message(role="user", content="hi"))
    with repl.console.capture() as cap:
        repl._print_resume_hint(agent)
    assert "openbro --resume abc123def456" in cap.get()


def test_token_label_in_words_and_hidden_when_unknown():
    from openbro.cli.repl import _token_label

    assert _token_label(0, 0) == ""  # Claude / Codex apps don't report them
    assert _token_label(9300, 250) == "9.3K tokens sent · 250 received"
    assert _token_label(0, 16350) == "16.4K tokens"


def test_fallback_reason_explains_failure():
    from openbro.cli.repl import _fallback_reason

    assert "too large" in _fallback_reason("Error code: 413 - Request too large")
    assert "rate limit" in _fallback_reason("429 Too Many Requests")
    assert _fallback_reason("something odd") == ""
