"""Tests for the new LLM providers (Google, DeepSeek) + router wiring."""

from unittest.mock import MagicMock, patch

import pytest

from openbro.llm.base import Message
from openbro.llm.deepseek_provider import DeepSeekProvider
from openbro.llm.google_provider import GoogleProvider

# ─── GoogleProvider ─────────────────────────────────────────────


def test_google_name():
    p = GoogleProvider(api_key="x", model="gemini-1.5-flash")
    assert p.name() == "google/gemini-1.5-flash"


def test_google_supports_tools():
    assert GoogleProvider("x").supports_tools() is True


def test_google_to_gemini_messages_extracts_system():
    msgs = [
        Message(role="system", content="you are a bot"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ]
    system, contents = GoogleProvider._to_gemini_messages(msgs)
    assert system == "you are a bot"
    assert len(contents) == 2
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"


def test_google_chat_handles_http_error():
    p = GoogleProvider(api_key="bad")
    with patch("openbro.llm.google_provider.httpx.post") as mock_post:
        import httpx

        resp = MagicMock()
        resp.status_code = 401
        mock_post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=resp
        )
        out = p.chat([Message(role="user", content="hi")])
    assert "401" in out.content


def test_google_chat_parses_text_response():
    p = GoogleProvider(api_key="x")
    fake_data = {"candidates": [{"content": {"parts": [{"text": "Hello bhai!"}]}}]}
    fake_resp = MagicMock()
    fake_resp.json.return_value = fake_data
    fake_resp.raise_for_status = MagicMock()
    with patch("openbro.llm.google_provider.httpx.post", return_value=fake_resp):
        out = p.chat([Message(role="user", content="hi")])
    assert "Hello bhai!" in out.content
    assert out.tool_calls == []


def test_google_chat_parses_tool_calls():
    p = GoogleProvider(api_key="x")
    fake_data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "datetime",
                                "args": {"action": "now"},
                            }
                        }
                    ]
                }
            }
        ]
    }
    fake_resp = MagicMock()
    fake_resp.json.return_value = fake_data
    fake_resp.raise_for_status = MagicMock()
    with patch("openbro.llm.google_provider.httpx.post", return_value=fake_resp):
        out = p.chat([Message(role="user", content="time?")], tools=[{"name": "datetime"}])
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0]["function"]["name"] == "datetime"


# ─── DeepSeekProvider ───────────────────────────────────────────


def test_deepseek_name():
    p = DeepSeekProvider(api_key="x", model="deepseek-chat")
    assert p.name() == "deepseek/deepseek-chat"


def test_deepseek_supports_tools():
    assert DeepSeekProvider("x").supports_tools() is True


def test_deepseek_get_client_lazy():
    p = DeepSeekProvider("key")
    assert p._client is None  # not loaded yet


def test_deepseek_chat_handles_error():
    p = DeepSeekProvider("k")
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("boom")
    p._client = fake_client
    out = p.chat([Message(role="user", content="hi")])
    assert "DeepSeek error" in out.content


def test_deepseek_chat_extracts_tool_calls():
    p = DeepSeekProvider("k")
    fake_msg = MagicMock()
    fake_msg.content = "calling tool"
    fake_tc = MagicMock()
    fake_tc.function.name = "datetime"
    fake_tc.function.arguments = '{"action":"now"}'
    fake_msg.tool_calls = [fake_tc]
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=fake_msg)]
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp
    p._client = fake_client
    out = p.chat([Message(role="user", content="time?")])
    assert "calling tool" in out.content
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0]["function"]["name"] == "datetime"


# ─── Router wiring ──────────────────────────────────────────────


def test_router_creates_google_provider(tmp_path, monkeypatch):
    fake_cfg = {
        "llm": {"provider": "google", "model": "gemini-1.5-flash"},
        "providers": {"google": {"api_key": "AIza_x", "model": "gemini-1.5-flash"}},
    }
    monkeypatch.setattr("openbro.llm.router.load_config", lambda: fake_cfg)
    from openbro.llm.router import create_provider

    p = create_provider()
    assert isinstance(p, GoogleProvider)


def test_router_creates_deepseek_provider(monkeypatch):
    fake_cfg = {
        "llm": {"provider": "deepseek", "model": "deepseek-chat"},
        "providers": {"deepseek": {"api_key": "sk_x", "model": "deepseek-chat"}},
    }
    monkeypatch.setattr("openbro.llm.router.load_config", lambda: fake_cfg)
    from openbro.llm.router import create_provider

    p = create_provider()
    assert isinstance(p, DeepSeekProvider)


def test_router_google_missing_key_raises(monkeypatch):
    fake_cfg = {
        "llm": {"provider": "google", "model": "gemini-1.5-flash"},
        "providers": {"google": {"api_key": None}},
    }
    monkeypatch.setattr("openbro.llm.router.load_config", lambda: fake_cfg)
    from openbro.llm.router import create_provider

    with pytest.raises(ValueError, match="Google API key"):
        create_provider()


# ─── auto_select with new providers ────────────────────────────


def test_auto_select_includes_google_and_deepseek():
    from openbro.llm.auto_select import probe_available

    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        candidates = probe_available()
    providers = {c["provider"] for c in candidates}
    assert "google" in providers
    assert "deepseek" in providers


def test_auto_select_picks_anthropic_when_all_keys_set():
    """Anthropic Claude scores highest in the catalog."""
    from openbro.llm.auto_select import best_available

    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        cfg = {
            "providers": {
                "groq": {"api_key": "gsk"},
                "google": {"api_key": "AIza"},
                "anthropic": {"api_key": "sk-ant"},
                "openai": {"api_key": "sk-"},
                "deepseek": {"api_key": "sk-d"},
            }
        }
        result = best_available(cfg)
    assert result["provider"] == "anthropic"


# ─── Brain.check_for_better_llm ─────────────────────────────────


def test_brain_daily_check_respects_cooldown(tmp_path, monkeypatch):
    """Within 24h of last check, returns None even if upgrade is available."""

    def fake_paths():
        return {"base": tmp_path}

    monkeypatch.setattr("openbro.brain.storage.get_storage_paths", fake_paths)
    from openbro.brain import Brain

    brain = Brain.load()
    # First call - should run the check
    cfg = {"providers": {"anthropic": {"api_key": "sk"}}}
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        first = brain.check_for_better_llm(("local", "phi3:mini"), cfg)
    # Should have suggested an upgrade (anthropic claude is way better than phi3)
    assert first is not None

    # Second call within 24h - returns None
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        second = brain.check_for_better_llm(("local", "phi3:mini"), cfg)
    assert second is None

    # Force=True bypasses cooldown
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        third = brain.check_for_better_llm(("local", "phi3:mini"), cfg, force=True)
    assert third is not None
