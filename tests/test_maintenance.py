"""openbro doctor / update / uninstall."""

import sys
from pathlib import Path

from click.testing import CliRunner

from openbro.cli import maintenance as m
from openbro.cli.main import main


def _info(**kw):
    base = {
        "version": "1.1.0",
        "location": "x",
        "editable": False,
        "source_dir": None,
        "extras": [],
    }
    base.update(kw)
    return m.InstallInfo(**base)


def test_update_command_pip_git_keeps_extras():
    (cmd,) = m.update_commands(_info(extras=["all", "voice"]))
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "--extra-index-url" in cmd  # llama-cpp wheels for the 'all' extra
    assert cmd[-1] == f"openbro[all,voice] @ git+{m.REPO_URL}@main"


def test_update_command_pypi_without_extras():
    (cmd,) = m.update_commands(_info(), source="pypi")
    assert cmd[-1] == "openbro" and "--extra-index-url" not in cmd


def test_update_command_source_checkout_pulls():
    cmds = m.update_commands(_info(editable=True, source_dir="D:/OpenBro"))
    assert cmds[0] == ["git", "-C", "D:/OpenBro", "pull", "--ff-only"]
    assert cmds[1][-2:] == ["-e", "D:/OpenBro[dev]"]


def test_detect_extras(monkeypatch):
    monkeypatch.setattr(m, "_has", lambda mod: mod in {"faster_whisper", "fastembed"})
    assert m.detect_extras() == ["voice", "rag"]


def test_safe_to_delete_guards(tmp_path):
    assert not m.safe_to_delete(Path(tmp_path.anchor))
    assert not m.safe_to_delete(Path.home())
    plain = tmp_path / "photos"
    plain.mkdir()
    assert not m.safe_to_delete(plain)
    named = tmp_path / "OpenBro-Data"
    named.mkdir()
    assert m.safe_to_delete(named)
    marked = tmp_path / "stuff"
    marked.mkdir()
    (marked / "config.yaml").write_text("x", encoding="utf-8")
    assert m.safe_to_delete(marked)


def test_human_sizes():
    assert m.human(512) == "512 B"
    assert m.human(2048) == "2.0 KB"
    assert m.human(3 * 1024**3) == "3.0 GB"


def test_uninstall_cancel_removes_nothing(tmp_path, monkeypatch):
    data = tmp_path / ".openbro"
    data.mkdir()
    (data / "config.yaml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(m, "data_locations", lambda cfg: [("config + data", data)])
    calls = []
    monkeypatch.setattr(m.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    out = []
    code = m.run_uninstall(False, False, False, confirm=lambda q: False, echo=out.append)
    assert code == 1 and data.exists() and not calls
    assert any("Cancelled" in line for line in out)


def test_uninstall_removes_data_and_package(tmp_path, monkeypatch):
    data = tmp_path / ".openbro"
    data.mkdir()
    (data / "memory").mkdir()
    monkeypatch.setattr(m, "data_locations", lambda cfg: [("config + data", data)])
    calls = []
    monkeypatch.setattr(m.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    code = m.run_uninstall(True, False, False, confirm=lambda q: True, echo=lambda s: None)
    assert code == 0 and not data.exists()
    assert calls[0][-3:] == ["uninstall", "-y", "openbro"]


def test_uninstall_keep_data(tmp_path, monkeypatch):
    data = tmp_path / ".openbro"
    data.mkdir()
    monkeypatch.setattr(m, "data_locations", lambda cfg: [("config + data", data)])
    monkeypatch.setattr(m.subprocess, "call", lambda cmd: 0)
    m.run_uninstall(True, True, False, confirm=lambda q: True, echo=lambda s: None)
    assert data.exists()


def test_doctor_rows_cover_the_basics():
    names = [r[0] for r in m.doctor_checks()]
    for want in ("Python", "OpenBro", "Config", "LLM provider", "Storage", "Documents index"):
        assert want in names
    assert all(r[1] in ("ok", "warn", "fail") for r in m.doctor_checks())


def test_cli_update_dry_run():
    result = CliRunner().invoke(main, ["update", "--dry-run", "--extras", "rag"])
    assert result.exit_code == 0
    assert "pip install" in result.output and "openbro[rag]" in result.output


def test_cli_doctor_runs():
    result = CliRunner().invoke(main, ["doctor"])
    assert "OpenBro doctor" in result.output
