"""Audit logger for tool executions."""

import json
from datetime import datetime
from pathlib import Path

from openbro.utils.storage import get_storage_paths


def log_tool_execution(
    tool_name: str,
    args: dict,
    result: str,
    risk: str = "safe",
    confirmed: bool = False,
):
    """Append a tool execution entry to the audit log."""
    try:
        paths = get_storage_paths()
        log_dir = paths["logs"]
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / "audit.jsonl"
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "args": _truncate(args),
            "result_preview": _truncate_text(result, 500),
            "risk": risk,
            "confirmed": confirmed,
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        # Don't crash if audit fails
        pass


def get_recent_logs(limit: int = 20) -> list[dict]:
    """Read recent audit log entries."""
    try:
        paths = get_storage_paths()
        log_file = Path(paths["logs"]) / "audit.jsonl"
        if not log_file.exists():
            return []

        with open(log_file, encoding="utf-8") as f:
            lines = f.readlines()

        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
    except Exception:
        return []


def _truncate(obj):
    if isinstance(obj, dict):
        return {k: _truncate_text(str(v), 200) for k, v in obj.items()}
    return _truncate_text(str(obj), 200)


def _truncate_text(text: str, max_len: int) -> str:
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text
