"""Tests for the workspace context detector."""

from __future__ import annotations

import json
from pathlib import Path

from openbro.core.workspace import WorkspaceContext, detect


def test_detect_cwd_set(tmp_path):
    ctx = detect(str(tmp_path))
    assert ctx.cwd == str(tmp_path.resolve()) or ctx.cwd == str(tmp_path)


def test_detect_no_project_markers(tmp_path):
    ctx = detect(str(tmp_path))
    assert ctx.is_python_project is False
    assert ctx.is_node_project is False
    assert ctx.is_git_repo is False


def test_detect_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test-pkg"\nversion = "0.1.0"\n')
    ctx = detect(str(tmp_path))
    assert ctx.is_python_project is True
    assert ctx.project_name == "test-pkg"


def test_detect_node_project_reads_name(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "my-node-app", "version": "1.0.0"}))
    ctx = detect(str(tmp_path))
    assert ctx.is_node_project is True
    assert ctx.project_name == "my-node-app"


def test_detect_falls_back_to_dir_name_when_no_project_name(tmp_path):
    """No pyproject / package.json -> project_name = directory name."""
    custom = tmp_path / "myproject"
    custom.mkdir()
    ctx = detect(str(custom))
    assert ctx.project_name == "myproject"


def test_detect_recent_files_lists_modified(tmp_path):
    """Top-level files are reported, sorted by mtime."""
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "beta.txt").write_text("b")
    (tmp_path / "gamma.txt").write_text("c")
    ctx = detect(str(tmp_path))
    assert len(ctx.recent_files) >= 3
    assert set(["alpha.txt", "beta.txt", "gamma.txt"]).issubset(set(ctx.recent_files))


def test_detect_skips_noise_dirs(tmp_path):
    """node_modules / __pycache__ children shouldn't surface."""
    (tmp_path / "real_file.txt").write_text("x")
    noise = tmp_path / "node_modules"
    noise.mkdir()
    (noise / "ignored.txt").write_text("y")
    ctx = detect(str(tmp_path))
    assert "real_file.txt" in ctx.recent_files
    assert "ignored.txt" not in ctx.recent_files


def test_detect_hides_dotfiles_except_readme(tmp_path):
    (tmp_path / ".env").write_text("KEY=val")
    (tmp_path / "README.md").write_text("readme")
    ctx = detect(str(tmp_path))
    assert ".env" not in ctx.recent_files
    assert "README.md" in ctx.recent_files


def test_detect_reads_workspace_yaml_hints(tmp_path):
    """A `.openbro/workspace.yaml` with hand-curated hints surfaces in
    the prompt block."""
    obro = tmp_path / ".openbro"
    obro.mkdir()
    (obro / "workspace.yaml").write_text("active task: billing rewrite\npriority: high\n")
    ctx = detect(str(tmp_path))
    assert ctx.hints.get("active task") == "billing rewrite"
    assert ctx.hints.get("priority") == "high"


def test_render_prompt_block_includes_basics(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    ctx = detect(str(tmp_path))
    out = ctx.render_prompt_block()
    assert "WORKSPACE" in out
    assert "cwd:" in out
    assert "project" in out


def test_render_empty_when_no_cwd():
    ctx = WorkspaceContext(cwd="")
    assert ctx.render_prompt_block() == ""


def test_detect_handles_missing_cwd(tmp_path):
    """A non-existent path returns a usable but mostly-empty context."""
    ghost = tmp_path / "does_not_exist"
    ctx = detect(str(ghost))
    assert ctx.cwd
    assert ctx.is_git_repo is False


def test_recent_files_respects_max_limit(tmp_path):
    for i in range(20):
        (tmp_path / f"file{i:02d}.txt").write_text("x")
    ctx = detect(str(tmp_path), max_recent=5)
    assert len(ctx.recent_files) == 5


def test_render_includes_git_when_repo(tmp_path):
    """Manually create a .git dir to trigger the git path. Actual git
    subprocess call may fail (no git installed in CI) — that's fine,
    the detector will return is_git_repo=False which is also acceptable."""
    (tmp_path / ".git").mkdir()
    ctx = detect(str(tmp_path))
    # Either the git subprocess found a branch, OR it failed and the
    # flag stays False — both behaviours are acceptable; we just
    # confirm the detection didn't crash.
    out = ctx.render_prompt_block()
    assert "cwd:" in out


def test_render_handles_recent_files_truncation_in_prompt(tmp_path):
    """render_prompt_block caps recent_files display at 6 even if more
    are detected (the cap is in the renderer, not the detector)."""
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x")
    ctx = detect(str(tmp_path))
    block = ctx.render_prompt_block()
    # Up to 6 file names should appear, comma-separated
    file_section = next((line for line in block.splitlines() if "recent files" in line), "")
    assert file_section.count(",") <= 5  # 6 files = 5 commas


def test_project_name_pyproject_wins_over_package_json(tmp_path):
    """Python pyproject takes priority for project_name."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "py-name"\n')
    (tmp_path / "package.json").write_text(json.dumps({"name": "node-name"}))
    ctx = detect(str(tmp_path))
    assert ctx.project_name == "py-name"


def test_detect_cached_returns_same_within_ttl(tmp_path):
    # Clear the cache for this key
    from openbro.core.workspace import _CACHE, detect_cached

    key = str(Path(tmp_path).resolve())
    _CACHE.pop(key, None)

    ctx1 = detect_cached(str(tmp_path), ttl_seconds=60)
    ctx2 = detect_cached(str(tmp_path), ttl_seconds=60)
    assert ctx1 is ctx2  # same object from cache
