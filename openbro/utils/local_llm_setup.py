"""Local LLM setup — pick a GGUF model, fetch from HuggingFace, save to disk.

Replaces the old Ollama setup flow. No external daemon. Model files (.gguf)
live in the user's chosen storage path (set in wizard step 1) so the big
4-13 GB blobs don't dump on C: by default.

Public API:
    full_local_setup()     -> (name, path) | None   # interactive wizard helper
    download_model(name)   -> Path | None           # idempotent HF download
    import_model(src)      -> Path | None           # copy a local GGUF in
    list_installed()       -> list[Path]            # GGUFs already on disk
    model_path_for(name)   -> Path | None           # registry-name → file path
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


# Curated GGUF catalogue. Non-Chinese vendors only (project rule).
# bartowski/* = community-standard quantizations on HuggingFace; Q4_K_M is
# the best size/quality trade-off for most users.
MODELS: dict[str, dict] = {
    "llama3.1:8b": {
        "repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "size": "4.9 GB",
        "ram": "8 GB",
        "desc": "Best agent / tool-calling (Meta, recommended)",
    },
    "llama3.2:3b": {
        "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size": "2.0 GB",
        "ram": "4 GB",
        "desc": "Smaller Llama agent, solid tool calling (Meta)",
    },
    "llama3.2:1b": {
        "repo": "bartowski/Llama-3.2-1B-Instruct-GGUF",
        "file": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "size": "0.8 GB",
        "ram": "2 GB",
        "desc": "Tiny Llama for low-end PCs (Meta)",
    },
    "mistral:7b": {
        "repo": "bartowski/Mistral-7B-Instruct-v0.3-GGUF",
        "file": "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        "size": "4.4 GB",
        "ram": "8 GB",
        "desc": "Reliable all-rounder (Mistral AI, France)",
    },
    "mistral-nemo": {
        "repo": "bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        "file": "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf",
        "size": "7.5 GB",
        "ram": "12 GB",
        "desc": "128K context, excellent tool calling (Mistral)",
    },
    "codestral:22b": {
        "repo": "bartowski/Codestral-22B-v0.1-GGUF",
        "file": "Codestral-22B-v0.1-Q4_K_M.gguf",
        "size": "13 GB",
        "ram": "16 GB",
        "desc": "Mistral's code specialist, 80+ languages",
    },
    "phi3:mini": {
        "repo": "bartowski/Phi-3-mini-4k-instruct-GGUF",
        "file": "Phi-3-mini-4k-instruct-Q4_K_M.gguf",
        "size": "2.4 GB",
        "ram": "4 GB",
        "desc": "Microsoft's tiny but smart model",
    },
    "phi3:medium": {
        "repo": "bartowski/Phi-3-medium-4k-instruct-GGUF",
        "file": "Phi-3-medium-4k-instruct-Q4_K_M.gguf",
        "size": "8.6 GB",
        "ram": "16 GB",
        "desc": "Microsoft Phi-3 14B, strong reasoning",
    },
    "gemma2:2b": {
        "repo": "bartowski/gemma-2-2b-it-GGUF",
        "file": "gemma-2-2b-it-Q4_K_M.gguf",
        "size": "1.7 GB",
        "ram": "4 GB",
        "desc": "Google's lightweight chat model",
    },
    "gemma2:9b": {
        "repo": "bartowski/gemma-2-9b-it-GGUF",
        "file": "gemma-2-9b-it-Q4_K_M.gguf",
        "size": "5.8 GB",
        "ram": "8 GB",
        "desc": "Google Gemma 9B — strong general chat",
    },
}

DEFAULT_MODEL = "llama3.1:8b"


def models_dir() -> Path:
    """Return the dir where GGUFs live. Honours the storage path the user
    chose in wizard step 1 — set via OPENBRO_MODELS env var or
    config.storage.models_dir — so big files don't dump on C: by default."""
    custom = os.environ.get("OPENBRO_MODELS") or os.environ.get("OLLAMA_MODELS")
    if custom:
        return Path(custom)
    try:
        from openbro.utils.config import load_config

        cfg = load_config()
        md = (cfg.get("storage") or {}).get("models_dir")
        if md:
            return Path(md)
    except Exception:
        pass
    return Path.home() / ".openbro" / "models"


def list_installed() -> list[Path]:
    """All .gguf files in the models dir."""
    md = models_dir()
    if not md.exists():
        return []
    return sorted(md.glob("*.gguf"))


def model_path_for(name: str) -> Path | None:
    """Return path to the named model if downloaded, else None."""
    info = MODELS.get(name)
    if not info:
        return None
    p = models_dir() / info["file"]
    return p if p.exists() else None


def find_installed_match(name: str) -> Path | None:
    """Best-effort: find any GGUF whose filename matches the given short name."""
    p = model_path_for(name)
    if p:
        return p
    family = name.split(":")[0].lower()
    for f in list_installed():
        if family in f.name.lower():
            return f
    return None


# ─── HuggingFace download ─────────────────────────────────────────────


def download_model(name: str) -> Path | None:
    """Download a GGUF model from HuggingFace into the user's models dir.

    Idempotent: if the file already exists, returns its path immediately.
    """
    info = MODELS.get(name)
    if not info:
        console.print(f"[red]Unknown model: {name}[/red]")
        console.print(f"[dim]Available: {', '.join(MODELS.keys())}[/dim]")
        return None

    md = models_dir()
    md.mkdir(parents=True, exist_ok=True)
    target = md / info["file"]

    if target.exists():
        console.print(f"[green]Already downloaded: {target}[/green]")
        return target

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        console.print("[red]huggingface_hub missing.[/red] Run: pip install 'openbro[local]'")
        return None

    console.print(f"\n[cyan]Downloading {name} ({info['size']}) from HuggingFace...[/cyan]")
    console.print(f"[dim]Source: {info['repo']}/{info['file']}[/dim]")
    console.print(f"[dim]Saving to: {target}[/dim]\n")

    try:
        # hf_hub_download has its own tqdm progress bar.
        downloaded = hf_hub_download(
            repo_id=info["repo"],
            filename=info["file"],
            local_dir=str(md),
        )
        downloaded_p = Path(downloaded)
        if downloaded_p.resolve() != target.resolve():
            # HF sometimes lands the file in a subdir; pull it up.
            shutil.move(str(downloaded_p), str(target))
        console.print(f"\n[bold green]Model saved: {target}[/bold green]")
        return target
    except Exception as e:
        console.print(f"\n[red]Download failed: {e}[/red]")
        console.print(
            "[dim]Network issue? Try manual import:\n"
            f"  1. Get {info['file']} from huggingface.co/{info['repo']}\n"
            "  2. Run: openbro model import <path-to-file>[/dim]"
        )
        return None


def import_model(src_path: str | Path) -> Path | None:
    """Copy a GGUF file from anywhere into the models dir.

    Use case: air-gapped PCs where the file came in via USB / network share.
    """
    src = Path(src_path).expanduser()
    if not src.exists():
        console.print(f"[red]Not found: {src}[/red]")
        return None
    if src.suffix.lower() != ".gguf":
        console.print(
            f"[red]Not a GGUF file: {src}[/red] [dim](OpenBro requires .gguf format)[/dim]"
        )
        return None

    md = models_dir()
    md.mkdir(parents=True, exist_ok=True)
    dest = md / src.name

    if dest.exists() and not Confirm.ask(f"{dest.name} already exists. Overwrite?", default=False):
        return dest

    size_gb = src.stat().st_size / 1e9
    console.print(f"[cyan]Copying {src.name} ({size_gb:.1f} GB)...[/cyan]")
    try:
        shutil.copy2(src, dest)
        console.print(f"[green]Imported: {dest}[/green]")
        return dest
    except Exception as e:
        console.print(f"[red]Copy failed: {e}[/red]")
        return None


# ─── library + UI ─────────────────────────────────────────────────────


def ensure_llama_cpp_python() -> bool:
    """Install llama-cpp-python + huggingface_hub if missing.

    Uses --only-binary=:all: like the voice deps so we never silently fall
    into a 30-minute source compile that segfaults.
    """
    have_llama = have_hf = False
    try:
        import llama_cpp  # noqa: F401

        have_llama = True
    except ImportError:
        pass
    try:
        import huggingface_hub  # noqa: F401

        have_hf = True
    except ImportError:
        pass

    if have_llama and have_hf:
        return True

    console.print(
        "\n[yellow]Local LLM deps not installed.[/yellow] "
        "[dim](llama-cpp-python + huggingface_hub, ~150 MB)[/dim]"
    )
    if not Confirm.ask("Install now?", default=True):
        return False

    import subprocess

    console.print("[dim]Installing llama-cpp-python (1-3 min, wheel-only)...[/dim]")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--no-warn-script-location",
                "--only-binary=:all:",
                "llama-cpp-python>=0.3.0",
                "huggingface-hub>=0.20",
            ],
            check=False,
            timeout=600,
        )
        if result.returncode != 0:
            console.print(
                f"[red]Install returned exit {result.returncode}.[/red]\n"
                "[dim]Most common cause: no compatible wheel for your "
                f"Python ({sys.version_info.major}.{sys.version_info.minor}). "
                "Try Python 3.10-3.13.[/dim]"
            )
            return False
        console.print("[green]Local LLM deps installed.[/green]")
        return True
    except subprocess.TimeoutExpired:
        console.print("[red]Install timed out (>10 min).[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Install error: {e}[/red]")
        return False


def _system_ram_gb() -> float | None:
    try:
        if platform.system() == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024**3)
        # macOS / Linux: best effort, no easy stdlib API. Skip the hint.
        return None
    except Exception:
        return None


def show_model_picker() -> str | None:
    """Interactive picker. Returns model registry key, or None if cancelled."""
    console.print("\n[bold]Available offline models:[/bold]\n")
    table = Table(border_style="cyan")
    table.add_column("#", style="cyan", width=3)
    table.add_column("Model", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("RAM", justify="right")
    table.add_column("Description")

    items = list(MODELS.items())
    for i, (name, info) in enumerate(items, 1):
        recommended = " [green]<-- recommended[/green]" if name == DEFAULT_MODEL else ""
        table.add_row(
            str(i),
            name,
            info["size"],
            info["ram"],
            info["desc"] + recommended,
        )
    console.print(table)

    ram_gb = _system_ram_gb()
    if ram_gb:
        console.print(f"\n[dim]Your system RAM: {ram_gb:.1f} GB[/dim]")
        if ram_gb < 8:
            console.print(
                "[yellow]Low RAM — recommend a 1-3 B model "
                "(llama3.2:1b, llama3.2:3b, gemma2:2b, phi3:mini)[/yellow]"
            )

    console.print()
    choices = [str(i) for i in range(1, len(items) + 1)]
    default_idx = next(
        (i for i, (n, _) in enumerate(items, 1) if n == DEFAULT_MODEL),
        1,
    )
    choice = Prompt.ask("Select model", choices=choices, default=str(default_idx))
    return items[int(choice) - 1][0]


def full_local_setup() -> tuple[str, Path] | None:
    """Wizard helper: install lib → pick model → download. Returns (name, path)."""
    console.print("\n[bold yellow]Local LLM Setup[/bold yellow]")
    console.print(
        "[dim]Run AI on YOUR hardware. Internet needed once for the model "
        "download; everything after that is offline.[/dim]"
    )
    md = models_dir()
    console.print(f"[dim]Models will live at: {md}[/dim]\n")

    if not ensure_llama_cpp_python():
        console.print(
            "[yellow]Local LLM skipped. You can pick a cloud provider for now "
            "and add a local model later via 'openbro model download <name>'.[/yellow]"
        )
        return None

    installed = list_installed()
    if installed:
        console.print("[green]Already on disk:[/green]")
        for f in installed:
            size_gb = f.stat().st_size / 1e9
            console.print(f"  - {f.name} ({size_gb:.1f} GB)")
        if not Confirm.ask("Download a different model too?", default=False):
            # Match an installed file back to a registry name when possible
            for n, info in MODELS.items():
                p = md / info["file"]
                if p.exists():
                    return n, p
            # Manually-imported file with a name we don't know — return as-is
            return installed[0].stem, installed[0]

    name = show_model_picker()
    if not name:
        return None
    path = download_model(name)
    if not path:
        return None
    return name, path
