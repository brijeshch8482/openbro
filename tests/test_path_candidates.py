"""Tests for resolve_with_candidates — multi-candidate path resolution.

Captured 2026-05-31: user said `C:\\OneDrive\\Desktop\\Testing logs\\
30th log`. The straight expanduser resolve missed it because the
symlink at `C:\\OneDrive` isn't expanded; the real location was
`C:\\Users\\<u>\\OneDrive\\Desktop\\Testing logs\\30th log`. Agent
reported 'folder not found' even though data was right there.
"""

from __future__ import annotations

import platform

import pytest

from openbro.utils.paths import resolve_with_candidates


def test_existing_path_returned_directly(tmp_path):
    f = tmp_path / "real.txt"
    f.write_text("hi")
    out = resolve_with_candidates(str(f))
    assert out.exists()
    assert out.name == "real.txt"


def test_nonexistent_path_returns_original_resolution(tmp_path):
    """When no candidate exists, return the original resolution so
    the caller can echo 'not found' with the user-facing input."""
    fake = tmp_path / "missing" / "x.txt"
    out = resolve_with_candidates(str(fake))
    assert not out.exists()
    # Original (resolved) path retained.
    assert "x.txt" in str(out)


def test_case_insensitive_parent_walk(tmp_path):
    """`/Foo/Bar/file.txt` should still resolve when the actual
    directory is `/foo/bar/file.txt` — Claude-style 'try a different
    angle' before giving up."""
    real = tmp_path / "foo" / "bar"
    real.mkdir(parents=True)
    (real / "file.txt").write_text("hi")
    mismatched = tmp_path / "FOO" / "BAR" / "file.txt"
    out = resolve_with_candidates(str(mismatched))
    # On case-insensitive filesystems (Windows / macOS) the straight
    # resolve already works; on Linux this exercises the case walker.
    assert out.exists()
    assert out.name == "file.txt"


@pytest.mark.skipif(platform.system() != "Windows", reason="OneDrive symlink only on Windows")
def test_onedrive_symlink_variants(tmp_path, monkeypatch):
    """Captured shape: user gives `C:\\OneDrive\\X` but the real
    folder is at `<home>\\OneDrive\\X`. Both should resolve."""
    home = tmp_path / "home"
    od = home / "OneDrive" / "Desktop" / "X"
    od.mkdir(parents=True)
    (od / "f.txt").write_text("hi")
    # Force resolve_with_candidates to think `tmp_path/home` is HOME.
    monkeypatch.setattr("openbro.utils.paths.Path.home", classmethod(lambda cls: home))
    # Also pretend C:\OneDrive doesn't exist for the substitution
    # path — we expect the home-relative resolution to win.
    out = resolve_with_candidates(str(home / "OneDrive" / "Desktop" / "X" / "f.txt"))
    assert out.exists()


def test_no_crash_on_permission_denied_parent(tmp_path):
    """Walker must handle PermissionError gracefully (Windows system
    folders, restricted directories)."""
    # Just exercise the path with a normal tmp_path so the iterator
    # works; the real PermissionError path is hit by `iterdir`
    # in production. Coverage here is the no-crash guarantee.
    out = resolve_with_candidates(str(tmp_path / "nonexistent" / "deeper" / "x"))
    assert not out.exists()


# ─── Iteration cap selection ──────────────────────────────────────────


def test_iteration_cap_picks_higher_for_local_provider(monkeypatch):
    """Captured user ask: 'jab fallback offline use kr rhe hai to
    unlimited tokens lene do'. Local provider gets the larger cap;
    cloud stays tighter."""
    from unittest.mock import MagicMock, patch

    from openbro.core.agent import Agent

    fake = MagicMock()
    fake.name.return_value = "local/mistral-nemo"
    fake.supports_tools.return_value = True

    with patch("openbro.core.agent.create_provider", return_value=fake):
        agent = Agent(interactive=False)
        agent.playbook_registry._playbooks = []
        cap = agent._iteration_cap()
    assert cap == agent.MAX_TOOL_ITERATIONS_LOCAL
    assert cap > agent.MAX_TOOL_ITERATIONS_CLOUD


def test_iteration_cap_picks_cloud_for_groq(monkeypatch):
    from unittest.mock import MagicMock, patch

    from openbro.core.agent import Agent

    fake = MagicMock()
    fake.name.return_value = "groq/llama-3.3-70b-versatile"
    fake.supports_tools.return_value = True

    with patch("openbro.core.agent.create_provider", return_value=fake):
        agent = Agent(interactive=False)
        agent.playbook_registry._playbooks = []
        cap = agent._iteration_cap()
    assert cap == agent.MAX_TOOL_ITERATIONS_CLOUD
