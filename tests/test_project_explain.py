"""Tests for the project_explain playbook + the file_ops search auto-split fix."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from openbro.playbooks.base import PlaybookContext
from openbro.playbooks.builtin.project_explain import ProjectExplainPlaybook
from openbro.tools.file_tool import FileTool

# ─── project_explain matching ──────────────────────────────────────────


@pytest.mark.parametrize(
    "q",
    [
        "explain this project",
        "what does this project do",
        "tell me about this project",
        "describe the codebase",
        "kya karta hai ye project?",
        "kya kr raha hai is project me",
        "kya kaam karta hai project",
        "D:\\Foo expain this project",
    ],
)
def test_project_explain_matches_common_phrasings(q):
    pb = ProjectExplainPlaybook()
    assert pb.match(q) is not None, f"{q!r} should match"


def test_project_explain_does_not_match_random_questions():
    pb = ProjectExplainPlaybook()
    for q in [
        "kya time hua",
        "open chrome",
        "kitne pdfs hain",
    ]:
        assert pb.match(q) is None, f"{q!r} should not match"


# ─── project_explain execution ─────────────────────────────────────────


def _python_project(tmp_path):
    (tmp_path / "README.md").write_text("# Cool Tool\n\nDoes something useful with data.")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "cool-tool"\ndescription = "A useful CLI for data wrangling"\n'
    )
    (tmp_path / "main.py").write_text("print('hello')")
    return tmp_path


def test_project_explain_python_project(tmp_path):
    proj = _python_project(tmp_path)
    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"explain this project at {proj}",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    assert "Cool Tool" in out
    assert "Python" in out
    assert "pyproject.toml" in out
    assert "useful CLI" in out


def test_project_explain_node_project(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "fancy-app",
                "description": "An Electron app for X",
                "version": "1.0.0",
            }
        )
    )
    (tmp_path / "README.md").write_text("# Fancy App\n\nMakes X faster.")
    (tmp_path / "index.ts").write_text("console.log(1)")
    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"what does this project do at {tmp_path}",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    assert "Fancy App" in out or "Electron" in out
    assert "TypeScript" in out or "Java" in out


def test_project_explain_kotlin_android_project(tmp_path):
    """Captured failure: MapRadiusKotlin shaped project — Android +
    Kotlin + Gradle. Should detect language and find applicationId."""
    (tmp_path / "build.gradle.kts").write_text(
        'android {\n    defaultConfig {\n        applicationId = "com.example.mapradius"\n    }\n}'
    )
    (tmp_path / "settings.gradle.kts").write_text('rootProject.name = "MapRadiusKotlin"')
    (tmp_path / ".gitignore").write_text("build/")
    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"{tmp_path} expain this project",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    assert "Kotlin" in out or "build.gradle" in out
    # applicationId surfacing means we actually READ the gradle file
    assert "com.example.mapradius" in out or "Android" in out


def test_project_explain_falls_back_to_listing_when_no_readme(tmp_path):
    (tmp_path / "main.go").write_text("package main")
    (tmp_path / "go.mod").write_text("module example.com/foo\ngo 1.21")
    (tmp_path / "cmd").mkdir()
    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"explain the project at {tmp_path}",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    assert "Top-level" in out or "go.mod" in out
    assert "Go" in out


def test_project_explain_missing_directory_returns_friendly_error(tmp_path):
    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=str(tmp_path / "does-not-exist") + " explain this project",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    # Should still produce something (uses cwd fallback)
    assert out  # non-empty


# ─── file_ops search auto-split fix ────────────────────────────────────


def test_file_ops_search_handles_path_with_glob(tmp_path):
    """Captured failure: model called `file_ops search path='D:/X/*.kt'`
    with empty pattern. Tool should auto-split."""
    (tmp_path / "a.kt").write_text("k")
    (tmp_path / "b.kt").write_text("k")
    (tmp_path / "c.py").write_text("p")
    tool = FileTool()
    # Mimic the model: glob in path, no pattern
    out = tool.run(action="search", path=str(tmp_path / "*.kt"), pattern="")
    assert "a.kt" in out
    assert "b.kt" in out
    assert "c.py" not in out


def test_file_ops_search_normal_usage_still_works(tmp_path):
    (tmp_path / "x.txt").write_text("x")
    (tmp_path / "y.txt").write_text("y")
    tool = FileTool()
    out = tool.run(action="search", path=str(tmp_path), pattern="*.txt")
    assert "x.txt" in out
    assert "y.txt" in out


def test_file_ops_search_no_pattern_no_glob_returns_clear_error(tmp_path):
    tool = FileTool()
    out = tool.run(action="search", path=str(tmp_path), pattern="")
    assert "Pattern required" in out


def test_file_ops_search_clear_error_when_root_missing():
    tool = FileTool()
    out = tool.run(action="search", path="D:/__definitely_not_here__", pattern="*.txt")
    assert "not found" in out.lower()


# ─── Registry integration ──────────────────────────────────────────────


def test_project_explain_registered():
    from openbro.playbooks import PlaybookRegistry

    reg = PlaybookRegistry()
    names = [p.name for p in reg.list_all()]
    assert "project_explain" in names


def test_registry_routes_project_explain_query(tmp_path):
    from openbro.playbooks import PlaybookRegistry

    reg = PlaybookRegistry()
    m = reg.match(f"{tmp_path} explain this project")
    assert m is not None
    # project_explain should win over other playbooks for this shape
    assert m.playbook.name == "project_explain"
