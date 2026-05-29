"""SystemHealthPlaybook — disk / RAM / CPU snapshot in one call, no LLM.

Captured failure: 'D drive ka health check' triggered an LLM-driven loop
that ran wmic, fabricated a second wmic output it never ran, and added a
'Recommendations' section nobody asked for. 584 output tokens for 2
lines of actual info. This playbook delivers the same answer in 0 LLM
calls.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess

from openbro.playbooks.base import Playbook, PlaybookContext, render_table


class SystemHealthPlaybook(Playbook):
    name = "system_health"
    description = "Disk health, drive space, RAM/CPU snapshot."
    triggers = [
        (re.compile(r"\b(drive|disk)\s+(health|status|check)\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bsmart\s+(check|status|tool)\b", re.IGNORECASE), 0.95),
        (re.compile(r"\bdisk\s+space\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bdrive\s+space\b", re.IGNORECASE), 1.0),
        (re.compile(r"\bfree\s+space\b", re.IGNORECASE), 0.9),
        (re.compile(r"\bsystem\s+(health|status|info)\b", re.IGNORECASE), 1.0),
        (re.compile(r"\b(ram|memory)\s+(usage|kitna|kitni)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\bcpu\s+(usage|load)\b", re.IGNORECASE), 0.9),
        (re.compile(r"\b(C|D|E|F)\s*drive\s+(ka\s+)?(space|status|health)\b", re.IGNORECASE), 1.0),
    ]
    keywords = ["drive health", "disk space", "system health"]

    def execute(self, context: PlaybookContext) -> str:
        sections = []

        # 1. Disk status (wmic, Windows) — real source of truth, no LLM fabrication
        disk_status = self._get_disk_status()
        if disk_status:
            sections.append("## Disk Status\n" + disk_status)

        # 2. Drive space (every mounted drive)
        drive_table = self._get_drive_space()
        if drive_table:
            sections.append("## Drive Space\n" + drive_table)

        # 3. RAM / CPU snapshot
        ram_cpu = self._get_ram_cpu()
        if ram_cpu:
            sections.append("## Memory & CPU\n" + ram_cpu)

        if not sections:
            return "_System health snapshot failed — no tools available._"
        return "\n\n".join(sections)

    @staticmethod
    def _get_disk_status() -> str:
        """Windows: `wmic diskdrive get model, status, size`. Parses to table."""
        if platform.system() != "Windows":
            return ""
        try:
            r = subprocess.run(
                ["wmic", "diskdrive", "get", "Model,Status,Size", "/format:csv"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except Exception:
            return ""
        if r.returncode != 0:
            return ""
        # Parse CSV: Node,Model,Size,Status
        rows = []
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split(",")
            if len(parts) < 4 or parts[0].strip() in ("", "Node"):
                continue
            model = parts[1].strip() or "?"
            size_raw = parts[2].strip()
            status = parts[3].strip() or "?"
            try:
                size_gb = f"{int(size_raw) / (1024**3):.0f} GB" if size_raw else ""
            except ValueError:
                size_gb = ""
            rows.append({"Model": model, "Size": size_gb, "Status": status})
        if not rows:
            return ""
        return render_table(rows)

    @staticmethod
    def _get_drive_space() -> str:
        """Every mounted drive: free / used / total with healthy/warning marker."""
        if platform.system() == "Windows":
            roots = []
            for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:\\"
                try:
                    if shutil.disk_usage(root):
                        roots.append(root)
                except Exception:
                    continue
        else:
            roots = ["/"]
        rows = []
        for root in roots:
            try:
                total, used, free = shutil.disk_usage(root)
            except Exception:
                continue
            pct = used / total * 100 if total else 0
            marker = "✓" if pct < 80 else ("⚠" if pct < 95 else "✗")
            rows.append(
                {
                    "Drive": root,
                    "Free": f"{free / (1024**3):.1f} GB",
                    "Used": f"{pct:.0f}%",
                    "Total": f"{total / (1024**3):.1f} GB",
                    "": marker,
                }
            )
        if not rows:
            return ""
        return render_table(rows)

    @staticmethod
    def _get_ram_cpu() -> str:
        try:
            import psutil
        except ImportError:
            return ""
        try:
            vm = psutil.virtual_memory()
            ram_used_gb = vm.used / (1024**3)
            ram_total_gb = vm.total / (1024**3)
            cpu_pct = psutil.cpu_percent(interval=0.3)
            cpu_count = psutil.cpu_count(logical=True)
        except Exception:
            return ""
        return (
            f"- **RAM**: {ram_used_gb:.1f} / {ram_total_gb:.1f} GB "
            f"({vm.percent:.0f}% used)\n"
            f"- **CPU**: {cpu_pct:.0f}% ({cpu_count} cores)"
        )
