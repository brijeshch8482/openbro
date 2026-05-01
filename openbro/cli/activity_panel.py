"""Live activity panel - shows what the agent is doing in real time.

Two modes:
- foreground: pops up an interactive Rich Live panel (spawn in a thread)
- background: silently appends every event to ~/.openbro/activity.log

User can toggle with 'show' / 'hide' REPL commands.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from openbro.core.activity import Event, get_bus
from openbro.utils.storage import get_storage_paths

KIND_STYLES = {
    "system": "dim",
    "user": "bold cyan",
    "thinking": "magenta",
    "tool_start": "yellow",
    "tool_end": "green",
    "permission": "bold yellow",
    "claude": "bold blue",
    "assistant": "white",
}


def render_panel(events: list[Event]) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")

    for ev in events[-20:]:
        ts = datetime.fromtimestamp(ev.ts).strftime("%H:%M:%S")
        style = KIND_STYLES.get(ev.kind, "white")
        table.add_row(
            ts,
            f"[{style}]{ev.kind}[/{style}]",
            ev.text[:200],
        )
    return Panel(table, title="🤖 OpenBro Activity", border_style="cyan")


class ActivityPanel:
    """Foreground live panel running in a thread."""

    def __init__(self):
        self.bus = get_bus()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._console = Console()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        with Live(
            render_panel(self.bus.history()),
            console=self._console,
            refresh_per_second=4,
            transient=False,
        ) as live:
            while not self._stop.is_set():
                live.update(render_panel(self.bus.history()))
                time.sleep(0.25)


_log_subscriber_unsub = None


def start_background_log() -> Path:
    """Subscribe a file logger; returns log file path."""
    global _log_subscriber_unsub
    paths = get_storage_paths()
    log_path = Path(paths.get("logs", paths["base"])) / "activity.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if _log_subscriber_unsub:
        return log_path

    def _write(ev: Event):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                ts = datetime.fromtimestamp(ev.ts).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{ts}] {ev.kind:12} {ev.text}\n")
        except Exception:
            pass

    _log_subscriber_unsub = get_bus().subscribe(_write)
    return log_path


def stop_background_log() -> None:
    global _log_subscriber_unsub
    if _log_subscriber_unsub:
        _log_subscriber_unsub()
        _log_subscriber_unsub = None


def print_recent(limit: int = 30) -> None:
    """One-shot: print last N events to terminal."""
    events = get_bus().history(limit=limit)
    if not events:
        Console().print("[dim]No activity yet.[/dim]")
        return
    Console().print(render_panel(events))
