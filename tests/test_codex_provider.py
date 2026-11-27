"""CodexProvider: Codex answers as the model only, and its token count is read."""

from openbro.llm.base import Message
from openbro.llm.codex_provider import CodexProvider


class _FakeProc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self._out = (stdout, stderr)
        self.prompt = None

    def communicate(self, prompt=None, timeout=None):
        self.prompt = prompt
        return self._out

    def kill(self):
        pass


def _patch(monkeypatch, results):
    """Fake `codex exec`: each run pops the next (returncode, stdout, stderr)."""
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(*results.pop(0))

    monkeypatch.setattr("openbro.llm.codex_provider.shutil.which", lambda b: "codex")
    monkeypatch.setattr("openbro.llm.codex_provider.subprocess.Popen", fake_popen)
    monkeypatch.setattr(CodexProvider, "_unknown_features", set())
    return calls


def test_codex_runs_without_its_own_tools(monkeypatch):
    # Codex's shell and image generator would act outside OpenBro: no
    # permission check, and files saved where the user never hears about.
    calls = _patch(monkeypatch, [(0, "hello", "codex\nhello\ntokens used\n1,234\n")])
    out = CodexProvider().chat([Message(role="user", content="hi")])
    cmd = calls[0]
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    disabled = {cmd[i + 1] for i, a in enumerate(cmd) if a == "--disable"}
    assert {"shell_tool", "image_generation", "apps"} <= disabled
    assert 'web_search="disabled"' in cmd
    assert cmd[-1] == "-"
    assert out.content == "hello"


def test_codex_token_count_read_from_stderr(monkeypatch):
    _patch(monkeypatch, [(0, "hello", "codex\nhello\ntokens used\n16,350\n")])
    out = CodexProvider().chat([Message(role="user", content="hi")])
    assert out.usage["total"] == 16350


def test_codex_drops_feature_flags_it_does_not_know(monkeypatch):
    calls = _patch(
        monkeypatch,
        [
            (1, "", "Error: Unknown feature flag: computer_use"),
            (0, "ok", ""),
        ],
    )
    out = CodexProvider().chat([Message(role="user", content="hi")])
    assert out.content == "ok"
    assert "computer_use" in calls[0]
    assert "computer_use" not in calls[1]
    assert "shell_tool" in calls[1]


def test_codex_other_errors_raise(monkeypatch):
    import pytest

    _patch(monkeypatch, [(2, "", "not signed in")])
    with pytest.raises(RuntimeError, match="not signed in"):
        CodexProvider().chat([Message(role="user", content="hi")])
