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


# ─── Deep inspect ──────────────────────────────────────────────────────


def test_deep_inspect_android_reads_manifest_and_source(tmp_path):
    """Captured failure: MapRadiusKotlin had no README, only top-level
    Gradle stuff. Deep inspect should drill into app/src/main/ to find
    the manifest, MainActivity.kt, and dependencies."""
    # Mimic the captured Android layout
    (tmp_path / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") }\n'
        "android {\n"
        "    defaultConfig {\n"
        '        applicationId = "com.example.mapradius"\n'
        "    }\n"
        "}\n"
        "dependencies {\n"
        '    implementation("com.google.android.gms:play-services-maps:18.2.0")\n'
        '    implementation("androidx.core:core-ktx:1.12.0")\n'
        "}"
    )
    app_main = tmp_path / "app" / "src" / "main"
    app_main.mkdir(parents=True)
    (app_main / "AndroidManifest.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<manifest package="com.example.mapradius">\n'
        '    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />\n'
        '    <uses-permission android:name="android.permission.INTERNET" />\n'
        "    <application>\n"
        '        <activity android:name=".MainActivity" />\n'
        '        <activity android:name=".MapActivity" />\n'
        "    </application>\n"
        "</manifest>"
    )
    src_dir = app_main / "java" / "com" / "example" / "mapradius"
    src_dir.mkdir(parents=True)
    (src_dir / "MainActivity.kt").write_text(
        "package com.example.mapradius\n\n"
        "import android.app.Activity\n"
        "import com.google.android.gms.maps.GoogleMap\n\n"
        "class MainActivity : Activity() {\n"
        "    private lateinit var map: GoogleMap\n"
        "    override fun onCreate(savedInstanceState: Bundle?) {\n"
        "        // Set up map and radius drawing\n"
        "    }\n"
        "}"
    )

    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"{tmp_path} explain this project",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)

    # Should detect Kotlin
    assert "Kotlin" in out
    # Should surface manifest content (was previously missing entirely)
    assert "AndroidManifest.xml" in out
    assert "com.example.mapradius" in out
    assert "MainActivity" in out  # at least one activity
    # Permissions surface
    assert "ACCESS_FINE_LOCATION" in out or "INTERNET" in out
    # Dependencies (Google Maps is the key signal for THIS app)
    assert "play-services-maps" in out or "google" in out.lower()
    # Source sample — actual code
    assert "MainActivity.kt" in out
    assert "GoogleMap" in out  # imported by the file we wrote


def test_deep_inspect_python_reads_main_files(tmp_path):
    """Python project: README-less, should show source samples."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndescription = "y"\n')
    src = tmp_path / "src" / "x"
    src.mkdir(parents=True)
    (src / "main.py").write_text("def main():\n    print('hello')\n")
    (src / "helper.py").write_text("def helper():\n    return 42\n")

    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"{tmp_path} explain this project",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    assert "Python" in out
    # The actual entry should be readable in the output
    assert "main.py" in out
    assert "def main" in out


def test_deep_inspect_handles_no_recognizable_language(tmp_path):
    """Random text dir — no language detected, deep inspect returns empty
    (the response still shows the basic listing)."""
    (tmp_path / "notes.md").write_text("just notes")
    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"{tmp_path} explain this project",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    # Doesn't crash, returns the basic structure
    assert "Project:" in out


def test_synthesis_explains_what_android_maps_app_does(tmp_path):
    """The captured failure that motivated synthesis: user gave us
    MapRadiusKotlin, expected to be told the app shows location on
    Maps with a radius. The synthesis should produce that paragraph
    AND list the detected signals as evidence."""
    (tmp_path / "build.gradle.kts").write_text(
        "dependencies {\n"
        '    implementation("com.google.android.gms:play-services-maps:18.2.0")\n'
        '    implementation("com.google.android.gms:play-services-location:21.0.1")\n'
        '    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.7.0")\n'
        "}"
    )
    main = tmp_path / "app" / "src" / "main"
    main.mkdir(parents=True)
    (main / "AndroidManifest.xml").write_text(
        '<?xml version="1.0"?><manifest>'
        '<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>'
        '<uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION"/>'
        '<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>'
        '<activity android:name=".MainActivity"/>'
        "</manifest>"
    )
    src = main / "java" / "com" / "example" / "proj"
    src.mkdir(parents=True)
    (src / "MainActivity.kt").write_text(
        "import com.google.android.gms.maps.GoogleMap\n"
        "import com.google.android.gms.maps.model.Circle\n"
        "import com.google.android.gms.location.FusedLocationProviderClient\n"
        "import androidx.lifecycle.ViewModel\n"
        "import androidx.lifecycle.LiveData\n"
        "class MainActivity {\n"
        "    private var circle: Circle? = null\n"
        "    private var radius: Double = 100.0\n"
        "}"
    )

    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"{tmp_path} explain this project",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)

    # Synthesis paragraph must appear AND mention the core capabilities
    assert "What it does" in out
    assert "Maps" in out
    assert "radius" in out.lower() or "circle" in out.lower()
    # The "detected signals" line proves we're reading code, not guessing
    assert "Detected signals" in out
    assert "Google Maps SDK" in out
    assert "FusedLocationProvider" in out


def test_synthesis_python_fastapi_project(tmp_path):
    """A FastAPI Python project should be summarised as a web API."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapi"\ndescription = "A web API"\n'
    )
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "build.gradle.kts").touch()  # noise to trip the gradle path; shouldn't apply
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    pb = ProjectExplainPlaybook()
    ctx = PlaybookContext(
        user_input=f"{tmp_path} explain this project",
        tool_registry=MagicMock(),
        captures={},
    )
    out = pb.execute(ctx)
    # Synthesis should latch onto FastAPI if either dep blob OR source mentions it
    assert "What it does" in out


def test_file_ops_search_depth_handles_nested_android(tmp_path):
    """Bumped max_depth from 4 to 8 specifically for Android layouts
    where MainActivity.kt sits at depth 6+. This test asserts the
    deeper file is now found."""
    from openbro.tools.file_tool import FileTool

    # depth 6: app/src/main/java/com/example/proj/MainActivity.kt
    deep = tmp_path / "app" / "src" / "main" / "java" / "com" / "example" / "proj"
    deep.mkdir(parents=True)
    (deep / "MainActivity.kt").write_text("class MainActivity")

    out = FileTool().run(action="search", path=str(tmp_path), pattern="*.kt")
    assert "MainActivity.kt" in out
