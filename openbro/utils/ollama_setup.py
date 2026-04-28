"""Ollama auto-setup - install, pull models, and manage the Ollama service."""

import os
import platform
import shutil
import subprocess
import time

import httpx
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()

OLLAMA_API = "http://localhost:11434"

# Popular models with size info
MODELS = {
    "qwen2.5-coder:7b": {"size": "4.7 GB", "desc": "Best for coding, recommended", "ram": "8 GB"},
    "qwen2.5-coder:3b": {"size": "2.0 GB", "desc": "Lighter coding model, faster", "ram": "4 GB"},
    "qwen2.5-coder:1.5b": {"size": "1.0 GB", "desc": "Smallest coder, low-end PCs", "ram": "4 GB"},
    "llama3.2:3b": {"size": "2.0 GB", "desc": "General purpose, good quality", "ram": "4 GB"},
    "llama3.2:1b": {"size": "1.3 GB", "desc": "Tiny but capable", "ram": "4 GB"},
    "phi3:mini": {"size": "2.3 GB", "desc": "Microsoft's small model", "ram": "4 GB"},
    "mistral:7b": {"size": "4.1 GB", "desc": "Strong general purpose", "ram": "8 GB"},
    "gemma2:2b": {"size": "1.6 GB", "desc": "Google's lightweight model", "ram": "4 GB"},
}


def is_ollama_installed() -> bool:
    """Check if Ollama CLI is available."""
    return shutil.which("ollama") is not None


def is_ollama_running() -> bool:
    """Check if Ollama server is responding."""
    try:
        resp = httpx.get(f"{OLLAMA_API}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def get_installed_models() -> list[str]:
    """Get list of models already downloaded in Ollama."""
    try:
        resp = httpx.get(f"{OLLAMA_API}/api/tags", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


def is_model_available(model: str) -> bool:
    """Check if a specific model is already pulled."""
    installed = get_installed_models()
    # Match with or without tag
    for m in installed:
        if m == model or m.startswith(model.split(":")[0]):
            return True
    return False


def start_ollama_server():
    """Start Ollama serve in background."""
    if is_ollama_running():
        return True

    console.print("[dim]Starting Ollama server...[/dim]")
    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # Wait for server to be ready
        for _ in range(15):
            time.sleep(1)
            if is_ollama_running():
                console.print("[green]Ollama server started![/green]")
                return True

        console.print("[yellow]Ollama server took too long to start.[/yellow]")
        return False
    except Exception as e:
        console.print(f"[red]Failed to start Ollama: {e}[/red]")
        return False


def pull_model(model: str) -> bool:
    """Download a model with progress tracking."""
    if not is_ollama_running():
        if not start_ollama_server():
            console.print("[red]Ollama server not running. Start it with: ollama serve[/red]")
            return False

    if is_model_available(model):
        console.print(f"[green]Model '{model}' already downloaded![/green]")
        return True

    model_info = MODELS.get(model, {})
    size = model_info.get("size", "unknown size")
    console.print(f"\n[cyan]Downloading model: {model} ({size})[/cyan]")
    console.print("[dim]This may take a few minutes depending on your internet speed...[/dim]\n")

    try:
        # Use Ollama API for pull with progress
        with httpx.stream(
            "POST",
            f"{OLLAMA_API}/api/pull",
            json={"name": model, "stream": True},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            import json

            last_status = ""
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(f"Pulling {model}", total=None)

                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    status = data.get("status", "")
                    if status != last_status:
                        progress.update(task, description=status)
                        last_status = status

                    total = data.get("total")
                    completed = data.get("completed")
                    if total and completed:
                        progress.update(task, total=total, completed=completed)

        console.print(f"\n[bold green]Model '{model}' downloaded successfully![/bold green]")
        return True

    except Exception as e:
        console.print(f"\n[red]Download failed: {e}[/red]")
        console.print("[dim]Try manually: ollama pull " + model + "[/dim]")
        return False


def install_ollama() -> bool:
    """Guide or auto-install Ollama."""
    system = platform.system()

    if system == "Windows":
        console.print("\n[cyan]Installing Ollama for Windows...[/cyan]")
        console.print("[dim]This will download and run the Ollama installer.[/dim]\n")

        if not Confirm.ask("Download and install Ollama?", default=True):
            console.print("[dim]Skipped. Install manually from: https://ollama.ai[/dim]")
            return False

        try:
            # Download Ollama installer
            installer_url = "https://ollama.com/download/OllamaSetup.exe"
            installer_path = os.path.join(os.environ.get("TEMP", "."), "OllamaSetup.exe")

            console.print("[dim]Downloading Ollama installer...[/dim]")
            with httpx.stream("GET", installer_url, follow_redirects=True, timeout=120) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))

                with open(installer_path, "wb") as f:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        DownloadColumn(),
                        console=console,
                    ) as progress:
                        task = progress.add_task("Downloading Ollama", total=total or None)
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                            progress.update(task, advance=len(chunk))

            console.print("[dim]Running installer (follow the prompts)...[/dim]")
            subprocess.run([installer_path], check=True)
            console.print("[green]Ollama installed![/green]")
            return True

        except Exception as e:
            console.print(f"[red]Installation failed: {e}[/red]")
            console.print("[yellow]Install manually from: https://ollama.ai[/yellow]")
            return False

    elif system == "Linux":
        console.print("\n[cyan]Installing Ollama for Linux...[/cyan]")
        if not Confirm.ask("Run the official Ollama install script?", default=True):
            console.print(
                "[dim]Install manually: curl -fsSL https://ollama.ai/install.sh | sh[/dim]"
            )
            return False

        try:
            result = subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.ai/install.sh | sh"],
                capture_output=False,
                timeout=300,
            )
            if result.returncode == 0:
                console.print("[green]Ollama installed![/green]")
                return True
            else:
                console.print("[red]Installation failed.[/red]")
                return False
        except Exception as e:
            console.print(f"[red]Installation failed: {e}[/red]")
            return False

    elif system == "Darwin":
        console.print("\n[cyan]Installing Ollama for macOS...[/cyan]")
        console.print("[yellow]Please install Ollama from: https://ollama.ai/download[/yellow]")
        console.print("[dim]Or use Homebrew: brew install ollama[/dim]")
        return False

    else:
        console.print(f"[yellow]Unsupported OS: {system}[/yellow]")
        console.print("[dim]Install Ollama manually from: https://ollama.ai[/dim]")
        return False


def show_model_picker() -> str | None:
    """Interactive model selection with size and RAM info."""
    console.print("\n[bold]Available offline models:[/bold]\n")

    table = Table(border_style="cyan")
    table.add_column("#", style="cyan", width=3)
    table.add_column("Model", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("RAM Needed", justify="right")
    table.add_column("Description")

    model_list = list(MODELS.items())
    for i, (name, info) in enumerate(model_list, 1):
        recommended = " [green]<-- recommended[/green]" if i == 1 else ""
        table.add_row(
            str(i),
            name,
            info["size"],
            info["ram"],
            info["desc"] + recommended,
        )

    console.print(table)
    console.print()

    # Show system RAM to help user decide
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
            total_ram = stat.ullTotalPhys / (1024**3)
            console.print(f"[dim]Your system RAM: {total_ram:.1f} GB[/dim]")

            if total_ram < 8:
                console.print(
                    "[yellow]Low RAM detected - pick a smaller model (1.5b or 3b)[/yellow]"
                )
            console.print()
    except Exception:
        pass

    choices = [str(i) for i in range(1, len(model_list) + 1)]
    choice = Prompt.ask("Select model", choices=choices, default="1")

    return model_list[int(choice) - 1][0]


def full_ollama_setup() -> str | None:
    """Complete Ollama setup flow: install -> start -> pick model -> pull.
    Returns the selected model name or None if setup was skipped.
    """
    console.print("\n[bold yellow]Offline Model Setup[/bold yellow]")
    console.print(
        "[dim]Ollama lets you run AI models locally - no internet needed, fully private.[/dim]\n"
    )

    # Step 1: Check/Install Ollama
    if not is_ollama_installed():
        console.print("[yellow]Ollama is not installed.[/yellow]")
        if not Confirm.ask("Install Ollama now? (needed for offline mode)", default=True):
            console.print(
                "[dim]Skipped. You can install later and use cloud providers for now.[/dim]"
            )
            return None
        if not install_ollama():
            return None

    # Step 2: Start server
    if not is_ollama_running():
        start_ollama_server()

    # Step 3: Check existing models
    if is_ollama_running():
        installed = get_installed_models()
        if installed:
            console.print(f"\n[green]Models already installed: {', '.join(installed)}[/green]")
            if not Confirm.ask("Download a different model?", default=False):
                return installed[0]

    # Step 4: Pick model
    model = show_model_picker()
    if not model:
        return None

    # Step 5: Pull model
    if is_ollama_running():
        pull_model(model)
    else:
        console.print(
            f"[yellow]Ollama not running. Download model later: ollama pull {model}[/yellow]"
        )

    return model
