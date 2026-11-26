"""`openbro doctor`, `openbro update`, `openbro uninstall`.

Same commands on Windows, Linux and macOS — no installer one-liner needed
once OpenBro is installed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/brijeshch8482/openbro.git"
LLAMA_WHEELS = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
SUPPORTED_PY = ((3, 10), (3, 13))

# Optional feature → (modules that prove it is installed, extra to install it)
FEATURES = {
    "local LLM (offline)": (("llama_cpp",), "all"),
    "voice": (("faster_whisper", "sounddevice", "edge_tts"), "voice"),
    "document search (meaning)": (("fastembed", "sentence_transformers"), "rag"),
    "PDF / scans": (("pypdf", "pytesseract"), "docs"),
    "Word / Excel": (("docx", "openpyxl"), "office"),
    "Telegram bot": (("telegram",), "all"),
    "cloud providers": (("anthropic", "openai"), "all"),
}


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


# ─── install detection ──────────────────────────────────────────────


@dataclass
class InstallInfo:
    version: str
    location: str
    editable: bool
    source_dir: str | None
    extras: list[str]


def detect_install() -> InstallInfo:
    from openbro import __version__

    editable, source_dir = False, None
    try:
        from importlib.metadata import distribution

        raw = distribution("openbro").read_text("direct_url.json")
        if raw:
            info = json.loads(raw)
            editable = bool((info.get("dir_info") or {}).get("editable"))
            url = info.get("url", "")
            if editable and url.startswith("file:"):
                from urllib.parse import unquote, urlparse

                path = unquote(urlparse(url).path)
                if platform.system() == "Windows" and path.startswith("/"):
                    path = path[1:]
                source_dir = path
    except Exception:
        pass
    return InstallInfo(
        version=__version__,
        location=str(Path(__file__).resolve().parents[1]),
        editable=editable,
        source_dir=source_dir,
        extras=detect_extras(),
    )


def detect_extras() -> list[str]:
    """Extras that look installed, so an update keeps the same features."""
    extras = []
    if _has("llama_cpp") or _has("telegram") or _has("anthropic"):
        extras.append("all")
    if _has("faster_whisper"):
        extras.append("voice")
    if _has("fastembed"):
        extras.append("rag")
    return extras


# ─── update ─────────────────────────────────────────────────────────


def update_commands(info: InstallInfo, source: str = "git", extras: list[str] | None = None):
    """The commands `openbro update` will run."""
    if info.editable and info.source_dir:
        return [
            ["git", "-C", info.source_dir, "pull", "--ff-only"],
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-e",
                f"{info.source_dir}[{','.join(extras or info.extras) or 'dev'}]",
            ],
        ]
    chosen = extras if extras is not None else info.extras
    spec = "openbro" + (f"[{','.join(chosen)}]" if chosen else "")
    target = f"{spec} @ git+{REPO_URL}@main" if source == "git" else spec
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", target]
    if "all" in chosen:
        cmd[4:4] = ["--extra-index-url", LLAMA_WHEELS]
    return [cmd]


def run_update(source: str, extras: list[str] | None, dry_run: bool, echo) -> int:
    info = detect_install()
    echo(f"OpenBro {info.version} ({'source checkout' if info.editable else 'pip install'})")
    for cmd in update_commands(info, source, extras):
        echo("$ " + " ".join(cmd))
        if dry_run:
            continue
        code = subprocess.call(cmd)
        if code != 0:
            echo(f"Update failed (exit {code}). Your current install is unchanged.")
            return code
    if not dry_run:
        out = subprocess.run(
            [sys.executable, "-c", "import openbro; print(openbro.__version__)"],
            capture_output=True,
            text=True,
        )
        echo(f"Now on OpenBro {out.stdout.strip() or '?'}. Restart openbro to use it.")
    return 0


# ─── uninstall ──────────────────────────────────────────────────────


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def safe_to_delete(path: Path) -> bool:
    """Only delete folders that are clearly OpenBro's own data."""
    try:
        p = path.resolve()
    except OSError:
        return False
    if not p.exists() or p == Path(p.anchor) or p == Path.home().resolve():
        return False
    if len(p.parts) < 2:
        return False
    named = "openbro" in p.name.lower()
    marked = any((p / m).exists() for m in ("config.yaml", "memory", "audit.log", "docs"))
    return named or marked


def data_locations(config: dict) -> list[tuple[str, Path]]:
    from openbro.utils.config import get_config_dir

    storage = (config or {}).get("storage", {}) or {}
    places = [("config + data", get_config_dir())]
    base = Path(storage.get("base_dir") or get_config_dir())
    models = Path(storage.get("models_dir") or base / "models")
    seen = {places[0][1].resolve()}
    for label, path in (("data", base), ("models", models)):
        r = path.resolve() if path.exists() else path
        if path.exists() and r not in seen and not any(s in r.parents for s in seen):
            places.append((label, path))
            seen.add(r)
    return places


def run_uninstall(yes: bool, keep_data: bool, keep_models: bool, confirm, echo) -> int:
    from openbro.utils.config import load_config

    config = load_config()
    targets = []
    if not keep_data:
        for label, path in data_locations(config):
            if label == "models" and keep_models:
                continue
            targets.append((label, path))
    echo("This will remove:")
    echo("  - the openbro Python package")
    for label, path in targets:
        echo(f"  - {label}: {path} ({human(_size(path))})")
    if keep_data:
        echo("  (keeping your config, memory, documents index and models)")
    elif keep_models:
        echo("  (keeping downloaded models)")
    if not yes and not confirm("Continue?"):
        echo("Cancelled — nothing removed.")
        return 1

    for label, path in targets:
        if keep_models and label != "models":
            models = Path(((config.get("storage") or {}).get("models_dir")) or path / "models")
            if models.exists() and path in models.resolve().parents:
                for child in path.iterdir():
                    if child.resolve() != models.resolve():
                        _remove(child, echo)
                continue
        if safe_to_delete(path):
            _remove(path, echo)
        else:
            echo(f"  skipped {path} — doesn't look like an OpenBro folder")

    code = subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "openbro"])
    if code == 0:
        echo("OpenBro removed. Thanks for trying it.")
    else:
        echo(f"pip uninstall exited {code}. Run: {sys.executable} -m pip uninstall openbro")
    return code


def _remove(path: Path, echo) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        echo(f"  removed {path}")
    except OSError as e:
        echo(f"  couldn't remove {path}: {e}")


# ─── doctor ─────────────────────────────────────────────────────────


def doctor_checks() -> list[tuple[str, str, str]]:
    """(check, status ok|warn|fail, detail) rows."""
    rows: list[tuple[str, str, str]] = []
    v = sys.version_info
    ok_py = SUPPORTED_PY[0] <= (v.major, v.minor) <= SUPPORTED_PY[1]
    rows.append(
        ("Python", "ok" if ok_py else "fail", f"{v.major}.{v.minor}.{v.micro} — {sys.executable}")
    )

    info = detect_install()
    kind = f"source checkout at {info.source_dir}" if info.editable else "pip install"
    rows.append(("OpenBro", "ok", f"{info.version} ({kind})"))

    try:
        from openbro.utils.config import get_config_path, load_config

        config = load_config()
        path = get_config_path()
        rows.append(
            (
                "Config",
                "ok" if path.exists() else "warn",
                str(path) if path.exists() else "not created yet — run `openbro --setup`",
            )
        )
    except Exception as e:
        config = {}
        rows.append(("Config", "fail", f"can't load: {e}"))

    llm = config.get("llm") or {}
    provider = llm.get("provider") or "?"
    pcfg = (config.get("providers") or {}).get(provider, {}) or {}
    needs_key = provider not in ("local", "ollama", "claude", "codex", "specialist", "?")
    has_key = bool(pcfg.get("api_key") or os.environ.get(f"{provider.upper()}_API_KEY"))
    status = "ok" if (not needs_key or has_key) else "fail"
    detail = f"{provider}" + (f" / {llm.get('model')}" if llm.get("model") else "")
    if needs_key and not has_key:
        detail += " — no API key (openbro config set providers.%s.api_key …)" % provider
    rows.append(("LLM provider", status, detail))
    if llm.get("fallback"):
        rows.append(("Fallback", "ok", str(llm["fallback"])))

    base = Path(((config.get("storage") or {}).get("base_dir")) or Path.home() / ".openbro")
    try:
        free = shutil.disk_usage(base if base.exists() else base.anchor or "/").free
        rows.append(
            ("Storage", "ok" if free > 2 * 1024**3 else "warn", f"{base} — {human(free)} free")
        )
    except OSError as e:
        rows.append(("Storage", "warn", f"{base} — {e}"))

    for name, (mods, extra) in FEATURES.items():
        present = [m for m in mods if _has(m)]
        if name.startswith("document search"):
            from openbro.rag.embedder import LocalEmbedder

            present = ["embedder"] if LocalEmbedder.available() else []
        if present:
            rows.append((name, "ok", "installed"))
        else:
            rows.append((name, "warn", f"not installed — pip install 'openbro[{extra}]'"))

    try:
        from openbro.rag import get_docs_service

        st = get_docs_service().status()
        rows.append(
            (
                "Documents index",
                "ok",
                f"{st['documents']} docs, {st['chunks']} passages — {st['mode']}",
            )
        )
    except Exception as e:
        rows.append(("Documents index", "warn", f"unavailable: {e}"))

    try:
        from openbro.core.permission_rules import PermissionRules

        rules = PermissionRules.from_config(config)
        n = len(rules.allow) + len(rules.ask) + len(rules.deny)
        rows.append(
            (
                "Permission rules",
                "fail" if rules.invalid else "ok",
                f"{n} rules" + (f", invalid: {rules.invalid}" if rules.invalid else ""),
            )
        )
        hooks = sum(
            len(v or []) for v in (config.get("hooks") or {}).values() if isinstance(v, list)
        )
        rows.append(("Hooks", "ok", f"{hooks} configured"))
    except Exception as e:
        rows.append(("Permission rules", "warn", str(e)))

    for tool, why in (
        ("git", "updates from source"),
        ("rg", "faster grep"),
        ("node", "MCP servers"),
    ):
        found = shutil.which(tool)
        rows.append((f"{tool} on PATH", "ok" if found else "warn", found or f"not found ({why})"))
    return rows
