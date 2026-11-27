"""REPL command handling, the text-tool turn path, and quiet optional imports."""

import sys
from pathlib import Path

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
    assert "response = agent.chat(user_input)" in source


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
