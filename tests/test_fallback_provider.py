"""Tests for the FallbackProvider — auto-cascade on recoverable errors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openbro.llm.base import LLMResponse, Message
from openbro.llm.fallback_provider import FallbackProvider, _is_recoverable

# ─── _is_recoverable ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "err_msg",
    [
        "429 Too Many Requests",
        "Rate limit hit, please retry",
        "rate_limit_exceeded",
        "Request too large for this model",
        "Tokens per minute exceeded",
        "503 service unavailable",
        "502 Bad Gateway",
        "504 gateway timeout",
        "Model is overloaded",
        "Connection timed out",
        "Connection reset by peer",
        "Name resolution failed",
        "getaddrinfo failed",
        "RemoteDisconnected: server hung up",
        "failed to call a function",
        "Failed to parse tool call arguments",
        "Tool call validation failed: missing argument 'x'",
        "SSL handshake failed",
    ],
)
def test_recoverable_errors_trigger_fallback(err_msg):
    """Every pattern in _RECOVERABLE_PATTERNS should be classified as
    'cascade to fallback'."""
    assert _is_recoverable(Exception(err_msg)) is True


@pytest.mark.parametrize(
    "err_msg",
    [
        "401 Unauthorized: invalid API key",
        "400 Bad Request: malformed JSON",
        "Model not found: claude-fake-99",
        "Permission denied",
        "404 Not Found",
        "Unsupported parameter",
    ],
)
def test_non_recoverable_errors_propagate(err_msg):
    """Auth / 4xx / not-found errors should NOT silently fall back —
    they tell the user something needs fixing."""
    assert _is_recoverable(Exception(err_msg)) is False


def test_connection_error_class_is_recoverable():
    assert _is_recoverable(ConnectionError("anything")) is True


def test_timeout_error_class_is_recoverable():
    assert _is_recoverable(TimeoutError("nothing")) is True


# ─── FallbackProvider.chat ────────────────────────────────────────────


def _make_provider(name: str, response: LLMResponse = None, error: Exception = None):
    p = MagicMock()
    p.name.return_value = name
    p.supports_tools.return_value = True
    if error is not None:
        p.chat.side_effect = error
    else:
        p.chat.return_value = response or LLMResponse(
            content=f"from {name}", usage={"input": 10, "output": 5}
        )
    return p


def test_chat_returns_primary_when_primary_succeeds():
    primary = _make_provider("primary")
    fallback = _make_provider("fallback")
    fp = FallbackProvider(primary=primary, fallback=fallback)

    resp = fp.chat([Message(role="user", content="hi")])
    assert resp.content == "from primary"
    fallback.chat.assert_not_called()
    assert fp.last_used == "primary"
    assert fp.fallback_count == 0


def test_chat_cascades_on_rate_limit(monkeypatch):
    """Primary retries 3 times with backoff before cascading to
    fallback. Patch time.sleep so the test stays fast (don't actually
    wait 1s + 3s for each test)."""
    import openbro.llm.fallback_provider as fb_mod

    monkeypatch.setattr(fb_mod.time, "sleep", lambda *_: None)
    primary = _make_provider("primary", error=Exception("429 Rate limit hit"))
    fallback = _make_provider("fallback")
    fp = FallbackProvider(primary=primary, fallback=fallback)

    resp = fp.chat([Message(role="user", content="hi")])
    assert resp.content == "from fallback"
    # Primary attempted 3 times (1 initial + 2 retries) before cascading.
    assert primary.chat.call_count == 3
    fallback.chat.assert_called_once()
    assert fp.last_used == "fallback"
    assert fp.fallback_count == 1


def test_chat_cascades_on_network_error(monkeypatch):
    import openbro.llm.fallback_provider as fb_mod

    monkeypatch.setattr(fb_mod.time, "sleep", lambda *_: None)
    primary = _make_provider("primary", error=ConnectionError("network down"))
    fallback = _make_provider("fallback")
    fp = FallbackProvider(primary=primary, fallback=fallback)
    resp = fp.chat([Message(role="user", content="hi")])
    assert resp.content == "from fallback"


def test_chat_propagates_auth_error():
    """401/403 should be raised so the user knows to fix their key
    instead of getting silently downgraded answers."""
    primary = _make_provider("primary", error=Exception("401 Unauthorized"))
    fallback = _make_provider("fallback")
    fp = FallbackProvider(primary=primary, fallback=fallback)

    with pytest.raises(Exception, match="401"):
        fp.chat([Message(role="user", content="hi")])
    fallback.chat.assert_not_called()


def test_on_fallback_callback_fires_with_correct_args():
    primary = _make_provider("primary", error=Exception("503 service unavailable"))
    fallback = _make_provider("fallback")
    calls = []

    def cb(primary_name, fallback_name, error):
        calls.append((primary_name, fallback_name, error))

    fp = FallbackProvider(primary=primary, fallback=fallback, on_fallback=cb)
    fp.chat([Message(role="user", content="hi")])
    assert len(calls) == 1
    assert calls[0][0] == "primary"
    assert calls[0][1] == "fallback"
    assert "503" in calls[0][2]


def test_callback_exception_does_not_break_cascade():
    """A buggy UI callback shouldn't take the whole agent down."""
    primary = _make_provider("primary", error=Exception("429"))
    fallback = _make_provider("fallback")

    def bad_cb(*args, **kwargs):
        raise RuntimeError("UI bug")

    fp = FallbackProvider(primary=primary, fallback=fallback, on_fallback=bad_cb)
    # Should still get the fallback response despite the broken callback
    resp = fp.chat([Message(role="user", content="hi")])
    assert resp.content == "from fallback"


def test_supports_tools_requires_both_providers():
    primary = MagicMock()
    primary.supports_tools.return_value = True
    fallback = MagicMock()
    fallback.supports_tools.return_value = False
    fp = FallbackProvider(primary=primary, fallback=fallback)
    assert fp.supports_tools() is False


def test_name_combines_both_provider_names():
    primary = _make_provider("groq/llama-4")
    fallback = _make_provider("local/llama-3.2")
    fp = FallbackProvider(primary=primary, fallback=fallback)
    assert "groq" in fp.name()
    assert "local" in fp.name()


# ─── Streaming ────────────────────────────────────────────────────────


def test_stream_uses_primary_when_first_chunk_arrives():
    primary = MagicMock()
    primary.name.return_value = "primary"
    primary.supports_tools.return_value = True
    primary.stream.return_value = iter(["hello ", "world"])

    fallback = MagicMock()
    fallback.name.return_value = "fallback"
    fallback.supports_tools.return_value = True

    fp = FallbackProvider(primary=primary, fallback=fallback)
    chunks = list(fp.stream([Message(role="user", content="hi")]))
    assert chunks == ["hello ", "world"]
    fallback.stream.assert_not_called()
    assert fp.last_used == "primary"


def test_stream_cascades_when_primary_raises_before_first_chunk():
    primary = MagicMock()
    primary.name.return_value = "primary"
    primary.supports_tools.return_value = True

    def primary_stream(messages, tools=None):
        raise Exception("429 rate limit")
        yield  # makes this a generator

    primary.stream.side_effect = lambda *a, **k: primary_stream(*a, **k)

    fallback = MagicMock()
    fallback.name.return_value = "fallback"
    fallback.supports_tools.return_value = True
    fallback.stream.return_value = iter(["a", "b"])

    fp = FallbackProvider(primary=primary, fallback=fallback)
    chunks = list(fp.stream([Message(role="user", content="hi")]))
    assert chunks == ["a", "b"]
    assert fp.last_used == "fallback"


# ─── Router integration ──────────────────────────────────────────────


def test_router_returns_fallback_wrapped_provider_when_configured(monkeypatch):
    """End-to-end: when llm.fallback is set in config, create_provider
    returns a FallbackProvider wrapping the primary."""
    from openbro.llm import router

    fake_primary = MagicMock(spec=router.LLMProvider)
    fake_fallback = MagicMock(spec=router.LLMProvider)

    def fake_build_one(name, config, providers_config):
        return fake_primary if name == "groq" else fake_fallback

    monkeypatch.setattr(router, "_build_one", fake_build_one)
    monkeypatch.setattr(
        router,
        "load_config",
        lambda: {
            "llm": {"provider": "groq", "fallback": "local"},
            "providers": {"groq": {"api_key": "x"}, "local": {}},
        },
    )

    provider = router.create_provider()
    assert isinstance(provider, FallbackProvider)
    assert provider.primary is fake_primary
    assert provider.fallback is fake_fallback


def test_router_returns_primary_alone_when_fallback_build_fails(monkeypatch):
    """If the fallback provider can't be built (no model on disk yet),
    the router gracefully degrades to primary-only."""
    from openbro.llm import router

    fake_primary = MagicMock(spec=router.LLMProvider)

    def fake_build_one(name, config, providers_config):
        if name == "groq":
            return fake_primary
        raise ValueError("local model not downloaded yet")

    monkeypatch.setattr(router, "_build_one", fake_build_one)
    monkeypatch.setattr(
        router,
        "load_config",
        lambda: {
            "llm": {"provider": "groq", "fallback": "local"},
            "providers": {"groq": {"api_key": "x"}, "local": {}},
        },
    )

    provider = router.create_provider()
    # Should be the primary directly, not a FallbackProvider.
    assert provider is fake_primary


def test_router_no_wrap_when_fallback_unset(monkeypatch):
    from openbro.llm import router

    fake_primary = MagicMock(spec=router.LLMProvider)
    monkeypatch.setattr(router, "_build_one", lambda *a: fake_primary)
    monkeypatch.setattr(
        router,
        "load_config",
        lambda: {
            "llm": {"provider": "groq"},  # no fallback key
            "providers": {"groq": {"api_key": "x"}},
        },
    )
    provider = router.create_provider()
    assert provider is fake_primary


def test_router_local_fallback_uses_providers_local_model_not_llm_model(monkeypatch):
    """Captured bug: when local is the FALLBACK behind a cloud primary,
    config['llm']['model'] holds the cloud model name (e.g. 'meta-llama/
    llama-4-scout-...') — looking that up in the local GGUF catalogue
    fails immediately and the fallback never activates. The router must
    use providers.local.model in this case."""
    from openbro.llm import router

    captured_local_model_name = []

    class FakeLocalProvider:
        def __init__(self, **kwargs):
            captured_local_model_name.append(kwargs.get("model_name"))

        def supports_tools(self):
            return True

        def name(self):
            return "local"

    monkeypatch.setattr(
        "openbro.llm.local_provider.LocalLLMProvider",
        FakeLocalProvider,
    )
    # Pretend the GGUF is on disk so the resolver doesn't raise
    monkeypatch.setattr(
        "openbro.utils.local_llm_setup.find_installed_match",
        lambda name: "/fake/path/" + name + ".gguf" if name == "llama3.2:3b" else None,
    )

    config = {
        "llm": {
            "provider": "groq",
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",  # cloud name
            "fallback": "local",
        },
        "providers": {
            "groq": {"api_key": "x"},
            "local": {"model": "llama3.2:3b"},  # local's OWN model
        },
    }
    router._build_one("local", config, config["providers"])
    # The local provider should have been built with its OWN model
    # name, NOT the Groq model name.
    assert captured_local_model_name == ["llama3.2:3b"]


def test_router_local_fallback_falls_back_to_default_when_unset(monkeypatch):
    """If providers.local.model is missing AND llm.provider is NOT local
    (so llm.model is some cloud name), the router uses DEFAULT_MODEL
    instead of crashing on the cloud model name."""
    from openbro.llm import router

    captured = []

    class FakeLocalProvider:
        def __init__(self, **kwargs):
            captured.append(kwargs.get("model_name"))

        def supports_tools(self):
            return True

        def name(self):
            return "local"

    monkeypatch.setattr(
        "openbro.llm.local_provider.LocalLLMProvider",
        FakeLocalProvider,
    )
    monkeypatch.setattr(
        "openbro.utils.local_llm_setup.find_installed_match",
        lambda name: "/fake/" + name + ".gguf",
    )

    config = {
        "llm": {
            "provider": "groq",
            "model": "some-cloud-model",  # primary's name; NOT for local
        },
        "providers": {
            "groq": {"api_key": "x"},
            "local": {},  # no model set
        },
    }
    router._build_one("local", config, config["providers"])
    # Must fall back to DEFAULT_MODEL (llama3.2:3b), not the cloud name.
    assert captured == ["llama3.2:3b"]


def test_router_local_primary_still_uses_llm_model(monkeypatch):
    """Regression guard: when the user explicitly picks local as PRIMARY,
    llm.model holds a real local model name and we should respect it."""
    from openbro.llm import router

    captured = []

    class FakeLocalProvider:
        def __init__(self, **kwargs):
            captured.append(kwargs.get("model_name"))

        def supports_tools(self):
            return True

        def name(self):
            return "local"

    monkeypatch.setattr(
        "openbro.llm.local_provider.LocalLLMProvider",
        FakeLocalProvider,
    )
    monkeypatch.setattr(
        "openbro.utils.local_llm_setup.find_installed_match",
        lambda name: "/fake/" + name + ".gguf",
    )

    config = {
        "llm": {
            "provider": "local",
            "model": "phi3:mini",  # user wants phi3 as primary
        },
        "providers": {"local": {}},
    }
    router._build_one("local", config, config["providers"])
    # llm.model wins when local IS primary
    assert captured == ["phi3:mini"]


def test_prompt_fallback_setup_skips_when_already_asked(monkeypatch, tmp_path):
    """Once the user has been prompted, never prompt again — even if
    the model isn't downloaded yet (they could have an interrupted
    download or chose to download manually later)."""
    from openbro.utils import local_llm_setup as ll

    cfg = {
        "llm": {"fallback": "local"},
        "agent": {"fallback_prompted": True},
        "providers": {"local": {"model": "llama3.2:3b"}},
    }
    monkeypatch.setattr(ll, "load_config", lambda: cfg)
    monkeypatch.setattr(ll, "save_config", lambda c: None)
    monkeypatch.setattr(ll, "is_fallback_ready", lambda *a, **k: False)

    result = ll.prompt_fallback_setup()
    assert result == "already_asked"


def test_prompt_fallback_setup_skips_when_no_fallback_configured(monkeypatch):
    """User who configured llm.fallback = null should never see the
    prompt at all."""
    from openbro.utils import local_llm_setup as ll

    cfg = {
        "llm": {"fallback": None},
        "agent": {},
        "providers": {"local": {}},
    }
    monkeypatch.setattr(ll, "load_config", lambda: cfg)
    monkeypatch.setattr(ll, "save_config", lambda c: None)
    monkeypatch.setattr(ll, "is_fallback_ready", lambda *a, **k: False)

    result = ll.prompt_fallback_setup()
    assert result == "skipped"


def test_prompt_fallback_setup_skips_in_non_interactive_run(monkeypatch):
    """CI / ask-mode (no stdin TTY) shouldn't block waiting for input."""
    import sys

    from openbro.utils import local_llm_setup as ll

    cfg = {
        "llm": {"fallback": "local"},
        "agent": {},
        "providers": {"local": {"model": "llama3.2:3b"}},
    }
    monkeypatch.setattr(ll, "load_config", lambda: cfg)
    monkeypatch.setattr(ll, "save_config", lambda c: None)
    monkeypatch.setattr(ll, "is_fallback_ready", lambda *a, **k: False)

    class _NoTty:
        @staticmethod
        def isatty():
            return False

    monkeypatch.setattr(sys, "stdin", _NoTty())
    result = ll.prompt_fallback_setup()
    assert result == "skipped"


def test_prompt_fallback_setup_marks_ready_when_model_present(monkeypatch):
    """If the model is already on disk before the user has ever been
    prompted (maybe they imported it manually), set the prompted flag
    so we don't pester them later."""
    from openbro.utils import local_llm_setup as ll

    saved = []
    cfg = {
        "llm": {"fallback": "local"},
        "agent": {},
        "providers": {"local": {"model": "llama3.2:3b"}},
    }
    monkeypatch.setattr(ll, "load_config", lambda: cfg)
    monkeypatch.setattr(ll, "save_config", lambda c: saved.append(c))
    monkeypatch.setattr(ll, "is_fallback_ready", lambda *a, **k: True)

    result = ll.prompt_fallback_setup()
    assert result == "ready"
    assert saved[0]["agent"]["fallback_prompted"] is True


def test_config_migration_fills_local_model_default():
    """Existing users whose providers.local block predates the 'model'
    key get the default filled in on next load."""
    from openbro.utils.config import _merge_defaults, _migrate_config, default_config

    legacy = {
        "providers": {
            "local": {
                "model_path": None,
                "n_ctx": 8192,
                "n_gpu_layers": -1,
            }
        }
    }
    migrated = _migrate_config(_merge_defaults(default_config(), legacy))
    assert migrated["providers"]["local"]["model"] == "llama3.2:3b"


def test_router_no_wrap_when_fallback_same_as_primary(monkeypatch):
    """Defensive: if a user accidentally configures fallback=groq with
    primary=groq, don't wrap (which would just cascade groq -> groq)."""
    from openbro.llm import router

    fake_primary = MagicMock(spec=router.LLMProvider)
    monkeypatch.setattr(router, "_build_one", lambda *a: fake_primary)
    monkeypatch.setattr(
        router,
        "load_config",
        lambda: {
            "llm": {"provider": "groq", "fallback": "groq"},
            "providers": {"groq": {"api_key": "x"}},
        },
    )
    provider = router.create_provider()
    assert provider is fake_primary
