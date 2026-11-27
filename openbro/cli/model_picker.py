"""`/model` picker: choose the LLM from a list; connect it if needed.

Flow for one choice:
  1. Ready (CLI installed, API key saved, local model downloaded) → switch.
  2. API provider without a key → ask for the key (hidden), test it with a
     real one-line request, and only if that works save it and switch.
     A key that fails is not saved and nothing changes.
  3. CLI / local provider that is not set up → say what to install; no switch.
"""

from __future__ import annotations

import copy
import sys
import threading
from dataclasses import dataclass

from rich.console import Console


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    label: str
    kind: str  # "cli" | "api" | "local"
    setup: str  # what is needed, shown when it is not ready


PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        "claude",
        "Claude subscription (claude CLI)",
        "cli",
        "Install the `claude` CLI and run `claude login` once.",
    ),
    ProviderInfo(
        "codex",
        "ChatGPT subscription (codex CLI)",
        "cli",
        "Install the `codex` CLI and run `codex login` once.",
    ),
    ProviderInfo(
        "groq", "Groq — free tier, fast", "api", "Free key: https://console.groq.com/keys"
    ),
    ProviderInfo(
        "google", "Google Gemini — free tier", "api", "Free key: https://aistudio.google.com/apikey"
    ),
    ProviderInfo(
        "anthropic", "Anthropic API", "api", "Key: https://console.anthropic.com/settings/keys"
    ),
    ProviderInfo("openai", "OpenAI API", "api", "Key: https://platform.openai.com/api-keys"),
    ProviderInfo("deepseek", "DeepSeek API", "api", "Key: https://platform.deepseek.com/api_keys"),
    ProviderInfo(
        "local",
        "Offline model on this computer",
        "local",
        "Download one first: type `pull` (or `openbro model download …`).",
    ),
)
BY_NAME = {p.name: p for p in PROVIDERS}

TEST_PROMPT = "Connection test. Reply with the single word: ok"
TEST_TIMEOUT_S = 60


def provider_ready(name: str, config: dict) -> bool:
    """Usable without asking anything: CLI installed, key saved, model downloaded."""
    import shutil

    providers = config.get("providers", {}) or {}
    info = BY_NAME.get(name)
    if info is None:
        return False
    if info.kind == "cli":
        binary = (providers.get(name) or {}).get("binary", name)
        return shutil.which(binary) is not None
    if info.kind == "local":
        try:
            from openbro.utils.local_llm_setup import list_installed

            return bool(list_installed())
        except Exception:
            return False
    return bool((providers.get(name) or {}).get("api_key"))


def test_connection(name: str, config: dict) -> tuple[bool, str]:
    """Build `name` from `config` alone (no fallback) and send one tiny request.

    Returns (ok, detail). Never raises; gives up after TEST_TIMEOUT_S."""
    from openbro.llm.base import Message
    from openbro.llm.router import _build_one

    result: dict = {}

    def _run() -> None:
        try:
            provider = _build_one(name, config, config.get("providers", {}) or {})
            reply = provider.chat([Message(role="user", content=TEST_PROMPT)], tools=None)
            text = (reply.content or "").strip()
            result["ok"] = bool(text)
            result["detail"] = provider.name() if text else "the model sent an empty reply"
        except Exception as e:  # bad key, network, unknown model…
            result["ok"] = False
            result["detail"] = _short_error(e)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(TEST_TIMEOUT_S)
    if worker.is_alive():
        return False, f"no answer within {TEST_TIMEOUT_S} s"
    return result.get("ok", False), result.get("detail", "unknown error")


_SECRET_RE = None


def _redact(text: str) -> str:
    """Remove anything that looks like a key before it is shown or logged."""
    import re

    global _SECRET_RE
    if _SECRET_RE is None:
        _SECRET_RE = re.compile(
            r"((?:api[_-]?)?key=)[^&\s'\"]+|\b(?:gsk|sk|AIza)[-_A-Za-z0-9]{12,}",
            re.IGNORECASE,
        )
    return _SECRET_RE.sub(lambda m: (m.group(1) or "") + "[hidden]", text)


def _short_error(e: Exception) -> str:
    raw = str(e).strip()
    text = _redact(raw.splitlines()[0]) if raw else type(e).__name__
    low = text.lower()
    rejected = ("unauthorized", "invalid api key", "invalid x-api-key", "api key not valid")
    if "401" in text or any(word in low for word in rejected):
        return "the key was rejected (401) — check it and try again"
    if "403" in text:
        return "access denied (403) — the key may lack permission for this model"
    return text[:200]


# ─── terminal interaction (kept thin so the logic above stays testable) ──


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def choose_provider(config: dict, current: str) -> str | None:
    """Arrow-key list (Esc / Ctrl+C cancels). Numbered prompt as a fallback."""
    options = []
    for p in PROVIDERS:
        mark = "✓" if provider_ready(p.name, config) else "·"
        here = "   ← current" if p.name == current else ""
        options.append((p.name, f"{mark} {p.name:<9} {p.label}{here}"))
    try:
        if not _interactive():
            raise RuntimeError("not a terminal")
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.shortcuts import choice

        keys = KeyBindings()

        @keys.add("escape", eager=True)
        def _cancel(event) -> None:
            event.app.exit(result=None)

        return choice(
            "Choose the LLM (↑/↓, Enter · Esc to cancel) — ✓ = ready:",
            options=options,
            default=current if current in BY_NAME else None,
            key_bindings=keys,
        )
    except KeyboardInterrupt:
        return None
    except (ImportError, RuntimeError, TypeError):
        return _choose_numbered(options)


def _choose_numbered(options: list[tuple[str, str]]) -> str | None:
    console = Console()
    for i, (_, label) in enumerate(options, start=1):
        console.print(f"  {i}. {label}", highlight=False)
    try:
        raw = input("Number (Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1][0]
    return None


def ask_secret(message: str) -> str | None:
    try:
        from prompt_toolkit import prompt

        value = prompt(message, is_password=True)
    except (EOFError, KeyboardInterrupt):
        return None
    except Exception:
        import getpass

        try:
            value = getpass.getpass(message)
        except (EOFError, KeyboardInterrupt):
            return None
    return value.strip() or None


def connect_and_switch(name: str, agent, console: Console, load, save) -> bool:
    """Make `name` the running provider, asking for and testing a key if needed.

    `load` / `save` read and write the config (injected for tests).
    Returns True when the switch happened."""
    from openbro.llm.router import create_provider

    info = BY_NAME.get(name)
    if info is None:
        console.print(f"[red]Unknown provider: {name}[/red]")
        return False
    config = load()

    if not provider_ready(name, config):
        if info.kind != "api":
            console.print(f"[yellow]{name} is not set up on this computer.[/yellow] {info.setup}\n")
            return False
        console.print(f"[cyan]{info.label}[/cyan] needs an API key. {info.setup}")
        while True:
            key = ask_secret(f"{name} API key (hidden, Enter to cancel): ")
            if not key:
                console.print("[dim]Cancelled — nothing changed.[/dim]\n")
                return False
            trial = copy.deepcopy(config)
            trial.setdefault("providers", {}).setdefault(name, {})["api_key"] = key
            with console.status(f"Checking the {name} connection…"):
                ok, detail = test_connection(name, trial)
            if ok:
                console.print(f"[green]✓ Connected[/green] — {detail}")
                config = trial
                break
            console.print(f"[red]✗ Not connected:[/red] {detail}")
            console.print(
                "[dim]The key was not saved. Paste it again, or press Enter to cancel.[/dim]"
            )

    # create_provider reads the saved config, so save first; if building the
    # provider still fails, put the previous provider back (a key that just
    # passed its test stays saved).
    previous_provider = (config.get("llm", {}) or {}).get("provider")
    config.setdefault("llm", {})["provider"] = name
    save(config)
    try:
        new_provider = create_provider(name)
    except Exception as e:
        config["llm"]["provider"] = previous_provider
        save(config)
        console.print(f"[red]Can't switch to {name}:[/red] {_short_error(e)}\n")
        return False
    agent.provider = new_provider
    console.print(f"[green]Switched to {name}[/green] — now running {new_provider.name()}\n")
    return True


def run_picker(agent, console: Console, load, save) -> None:
    config = load()
    current = (config.get("llm", {}) or {}).get("provider", "")
    name = choose_provider(config, current)
    if not name:
        console.print("[dim]No change.[/dim]\n")
        return
    if name == current:
        console.print(f"[dim]Already using {name}.[/dim]\n")
        return
    connect_and_switch(name, agent, console, load, save)
