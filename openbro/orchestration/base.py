"""Base interface for CLI agent adapters."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openbro.core.activity import get_bus
from openbro.utils.storage import get_storage_paths


@dataclass
class CliAgentResult:
    success: bool = True
    summary: str = ""
    cost_usd: float = 0.0
    files_touched: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    raw_output: str = ""

    def format(self, agent_name: str, today_total: float) -> str:
        files_str = "\n  ".join(sorted(self.files_touched)) if self.files_touched else "(none)"
        tools_str = ", ".join(self.tools_used) if self.tools_used else "(none)"
        cost_line = (
            f"Cost: ${self.cost_usd:.3f} (today: ${today_total:.2f})"
            if self.cost_usd > 0
            else "Cost: (not reported by CLI)"
        )
        return (
            f"{agent_name} finished.\n"
            f"{cost_line}\n"
            f"Tools used: {tools_str}\n"
            f"Files touched:\n  {files_str}\n\n"
            f"Summary:\n{self.summary or '(no text response)'}"
        )


def _spend_path() -> Path:
    base = Path(get_storage_paths().get("base", Path.home() / ".openbro"))
    return base / "cli_agent_spend.json"


def load_spend() -> dict:
    p = _spend_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def record_spend(agent_name: str, usd: float) -> tuple[float, float]:
    """Add usd to today's per-agent spend; return (today_total_for_agent, all_time)."""
    spend = load_spend()
    today = date.today().isoformat()
    spend.setdefault("daily", {}).setdefault(today, {})
    spend["daily"][today][agent_name] = spend["daily"][today].get(agent_name, 0.0) + usd
    spend["total"] = spend.get("total", 0.0) + usd
    try:
        _spend_path().parent.mkdir(parents=True, exist_ok=True)
        _spend_path().write_text(json.dumps(spend, indent=2))
    except Exception:
        pass
    return spend["daily"][today][agent_name], spend["total"]


def today_spend(agent_name: str) -> float:
    spend = load_spend()
    return spend.get("daily", {}).get(date.today().isoformat(), {}).get(agent_name, 0.0)


class CliAgent(ABC):
    """Adapter for an external AI coding CLI."""

    name: str = ""
    binary: str = ""  # executable name on PATH
    install_url: str = ""
    install_cmd: str = ""
    description: str = ""

    @abstractmethod
    def build_command(
        self,
        task: str,
        cwd: str,
        max_cost_usd: float | None,
    ) -> list[str]:
        """Build the subprocess argv for non-interactive run."""

    @abstractmethod
    def parse_stream(
        self,
        stdout_lines: Iterable[str],
        on_event,
    ) -> CliAgentResult:
        """Consume stdout lines, emit events via on_event(kind, text, **meta),
        and return a structured CliAgentResult."""

    def is_installed(self) -> bool:
        return shutil.which(self.binary) is not None

    def install_hint(self) -> str:
        msg = f"{self.name} CLI ({self.binary}) not found."
        if self.install_cmd:
            msg += f" Install: {self.install_cmd}"
        if self.install_url:
            msg += f" Docs: {self.install_url}"
        return msg

    def run(
        self,
        task: str,
        cwd: str,
        max_cost_usd: float | None,
        timeout: int,
    ) -> CliAgentResult:
        bus = get_bus()
        cmd = self.build_command(task, cwd, max_cost_usd)
        bus.emit("cli_agent", f"{self.name}: start", agent=self.name, cwd=cwd, cap=max_cost_usd)

        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def _drain_stderr():
            assert proc.stderr is not None
            for line in proc.stderr:
                if line.strip():
                    bus.emit("cli_agent", f"{self.name} stderr: {line.strip()[:200]}")

        t = threading.Thread(target=_drain_stderr, daemon=True)
        t.start()

        def emit(kind: str, text: str = "", **meta):
            bus.emit(kind, f"{self.name}: {text}" if text else self.name, agent=self.name, **meta)

        try:
            assert proc.stdout is not None
            result = self.parse_stream(proc.stdout, emit)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return CliAgentResult(
                success=False,
                summary=f"{self.name} timed out after {timeout}s.",
            )

        if proc.returncode != 0:
            result.success = False
        return result
