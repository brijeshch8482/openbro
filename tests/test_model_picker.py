"""`/model` picker: status, readiness, key test before switching."""

import copy
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from openbro.cli import model_picker as mp
from openbro.llm.base import LLMResponse


def _named(name):
    p = MagicMock()
    p.name.return_value = name
    return p


def _config():
    return {
        "llm": {"provider": "claude", "fallback": None},
        "providers": {"groq": {"api_key": "saved-key"}, "google": {}},
    }


class Store:
    """Stands in for load_config / save_config."""

    def __init__(self, config):
        self.config = config
        self.saves = []

    def load(self):
        return copy.deepcopy(self.config)

    def save(self, config):
        self.config = copy.deepcopy(config)
        self.saves.append(copy.deepcopy(config))


@pytest.fixture
def console():
    return Console(record=True, width=120)


def test_status_lines_show_main_backup_and_last_answer():
    from openbro.cli import repl
    from openbro.llm.fallback_provider import FallbackProvider

    fb = FallbackProvider(_named("claude/code-cli"), _named("codex/chatgpt"))
    fb.last_used, fb.fallback_count = "fallback", 1
    text = "\n".join(repl._model_status_lines(MagicMock(provider=fb)))
    assert "claude/code-cli" in text and "codex/chatgpt" in text
    assert "Last answer from:[/cyan] codex/chatgpt" in text


def test_provider_ready(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda b: "/bin/claude" if b == "claude" else None)
    cfg = _config()
    assert mp.provider_ready("claude", cfg) is True
    assert mp.provider_ready("codex", cfg) is False
    assert mp.provider_ready("groq", cfg) is True
    assert mp.provider_ready("google", cfg) is False
    assert mp.provider_ready("nope", cfg) is False


def test_ready_provider_switches_without_asking(monkeypatch, console):
    from openbro.llm import router

    store = Store(_config())
    monkeypatch.setattr(router, "create_provider", lambda name=None: _named(f"{name}/m"))
    monkeypatch.setattr(mp, "ask_secret", lambda m: pytest.fail("should not ask for a key"))
    agent = MagicMock()
    assert mp.connect_and_switch("groq", agent, console, store.load, store.save) is True
    assert store.config["llm"]["provider"] == "groq"
    assert agent.provider.name() == "groq/m"


def test_new_key_is_tested_before_it_is_saved(monkeypatch, console):
    from openbro.llm import router

    store = Store(_config())
    keys = iter(["bad-key", "good-key"])
    monkeypatch.setattr(mp, "ask_secret", lambda m: next(keys))
    tested = []

    def fake_test(name, config):
        key = config["providers"][name]["api_key"]
        tested.append(key)
        return (key == "good-key"), ("google/gemini" if key == "good-key" else "rejected (401)")

    monkeypatch.setattr(mp, "test_connection", fake_test)
    monkeypatch.setattr(router, "create_provider", lambda name=None: _named("google/gemini"))
    agent = MagicMock()
    assert mp.connect_and_switch("google", agent, console, store.load, store.save) is True
    assert tested == ["bad-key", "good-key"]
    # Only the working key was ever written.
    assert all(s["providers"]["google"].get("api_key") != "bad-key" for s in store.saves)
    assert store.config["providers"]["google"]["api_key"] == "good-key"
    assert store.config["llm"]["provider"] == "google"
    out = console.export_text()
    assert "Not connected" in out and "Connected" in out


def test_cancel_or_failed_key_changes_nothing(monkeypatch, console):
    store = Store(_config())
    answers = iter(["bad-key", None])  # wrong key, then Enter to cancel
    monkeypatch.setattr(mp, "ask_secret", lambda m: next(answers))
    monkeypatch.setattr(mp, "test_connection", lambda n, c: (False, "rejected (401)"))
    agent = MagicMock()
    before = agent.provider
    assert mp.connect_and_switch("google", agent, console, store.load, store.save) is False
    assert store.saves == [] and agent.provider is before


def test_cli_provider_not_installed_only_explains(monkeypatch, console):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda b: None)
    store = Store(_config())
    monkeypatch.setattr(mp, "ask_secret", lambda m: pytest.fail("no key for CLI providers"))
    assert mp.connect_and_switch("codex", MagicMock(), console, store.load, store.save) is False
    assert store.saves == []
    assert "codex login" in console.export_text()


def test_connection_test_uses_one_real_request(monkeypatch):
    from openbro.llm import router

    provider = MagicMock()
    provider.name.return_value = "groq/openai/gpt-oss-120b"
    provider.chat.return_value = LLMResponse(content="ok")
    monkeypatch.setattr(router, "_build_one", lambda name, cfg, prov: provider)
    assert mp.test_connection("groq", _config()) == (True, "groq/openai/gpt-oss-120b")
    assert provider.chat.call_args.kwargs == {"tools": None}

    provider.chat.side_effect = RuntimeError("Groq 401: Invalid API Key")
    ok, detail = mp.test_connection("groq", _config())
    assert not ok and "rejected (401)" in detail


def test_numbered_fallback_and_picker_cancel(monkeypatch, console):
    monkeypatch.setattr(mp, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "3")
    assert mp.choose_provider(_config(), "claude") == "groq"
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert mp.choose_provider(_config(), "claude") is None

    store = Store(_config())
    monkeypatch.setattr(mp, "choose_provider", lambda cfg, cur: None)
    mp.run_picker(MagicMock(), console, store.load, store.save)
    assert store.saves == [] and "No change" in console.export_text()


def test_google_key_goes_in_header_not_url(monkeypatch):
    from openbro.llm import google_provider
    from openbro.llm.base import Message

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"], seen["headers"] = url, headers
        resp = MagicMock()
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        return resp

    monkeypatch.setattr(google_provider.httpx, "post", fake_post)
    google_provider.GoogleProvider("AIza-secret-key").chat([Message(role="user", content="hi")])
    assert "AIza-secret-key" not in seen["url"] and "key=" not in seen["url"]
    assert seen["headers"] == {"x-goog-api-key": "AIza-secret-key"}


def test_errors_never_show_keys():
    err = RuntimeError("404 for url https://x/models/m:generate?key=AIzaSyAbcdef1234567890")
    assert "AIza" not in mp._short_error(err)
    assert "gsk_" not in mp._short_error(RuntimeError("bad gsk_abcdefghijklmnop123 key"))


def test_groq_reports_the_model_that_answered_and_skips_retired(monkeypatch):
    from openbro.llm.groq_provider import GroqProvider

    provider = GroqProvider("k", model="llama-3.3-70b-versatile")
    calls = []

    def fake(model, messages, tools):
        calls.append(model)
        if model == "llama-3.3-70b-versatile":
            raise RuntimeError("Groq 404: model does not exist")
        return LLMResponse(content="ok")

    monkeypatch.setattr(provider, "_chat_with_model", fake)
    provider.chat([], None)
    provider.chat([], None)
    assert provider.name() == "groq/openai/gpt-oss-120b"
    assert calls == ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-120b"]


def test_running_model_is_in_every_turn(monkeypatch):
    from openbro.core.agent import Agent

    provider = MagicMock()
    provider.name.return_value = "google/gemini-2.5-flash"
    provider.supports_tools.return_value = True
    provider.chat.return_value = LLMResponse(content="hi")
    agent = Agent(interactive=False, provider=provider)
    agent.playbooks_enabled = False
    agent.chat("which model are you?")
    sent_last = provider.chat.call_args[0][0][-1].content
    assert "RUNNING MODEL" in sent_last and "google/gemini-2.5-flash" in sent_last


def test_config_migrates_stale_model_identity_and_retired_groq():
    from openbro.utils.config import (
        RUNNING_MODEL_SENTENCE,
        STALE_MODEL_SENTENCES,
        _migrate_config,
        default_config,
    )

    cfg = default_config()
    cfg["agent"]["system_prompt"] = "My own rules. " + STALE_MODEL_SENTENCES[0] + " More."
    cfg["providers"]["groq"]["model"] = "llama-3.3-70b-versatile"
    out = _migrate_config(cfg)
    assert out["agent"]["system_prompt"].startswith("My own rules. ")  # user edits kept
    assert RUNNING_MODEL_SENTENCE in out["agent"]["system_prompt"]
    assert "Groq pe Llama" not in out["agent"]["system_prompt"]
    assert out["providers"]["groq"]["model"] == "openai/gpt-oss-120b"
