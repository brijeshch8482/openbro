"""Sign-in detection + auto-login flow for external CLI agents.

When the user says 'Claude se bolo X kar' (via voice or text), OpenBro:
  1. checks if the agent's CLI is signed in
  2. if not, runs the agent's login flow (or surfaces the URL for manual)
  3. then dispatches the actual task

Each agent has its own auth model:
  - Claude Code     — `claude` reads settings.json + keychain; `claude /login`
                      opens a browser flow
  - Codex           — OpenAI sign-in via `codex login` (browser)
  - Aider           — uses OPENAI_API_KEY env var; not really 'sign in'
  - Gemini CLI      — `gemini auth login` (browser)
"""

from __future__ import annotations

import os
import shutil
import subprocess

from openbro.core.activity import get_bus

# Per-agent: probe command (returns True if signed in), sign-in command
SIGN_IN_PROBES: dict[str, dict] = {
    "claude": {
        "probe_cmd": ["claude", "--print", "ping"],
        "auth_error_markers": [
            "not authenticated",
            "please run claude login",
            "no api key",
            "invalid api key",
            "401",
        ],
        "login_cmd": ["claude", "/login"],
        "manual_url": "https://claude.com/login",
    },
    "codex": {
        "probe_cmd": ["codex", "exec", "--quiet", "ping"],
        "auth_error_markers": [
            "not authenticated",
            "please run codex login",
            "401",
            "invalid api key",
        ],
        "login_cmd": ["codex", "login"],
        "manual_url": "https://platform.openai.com/account/api-keys",
    },
    "aider": {
        # Aider relies on OPENAI_API_KEY / ANTHROPIC_API_KEY; "sign in" =
        # making sure those env vars are set. We just check.
        "probe_cmd": None,
        "auth_error_markers": [],
        "login_cmd": None,
        "manual_url": "https://aider.chat/docs/install.html",
    },
    "gemini": {
        "probe_cmd": ["gemini", "-p", "ping", "--yolo"],
        "auth_error_markers": [
            "not authenticated",
            "please authenticate",
            "401",
        ],
        "login_cmd": ["gemini", "auth", "login"],
        "manual_url": "https://aistudio.google.com/apikey",
    },
}


def is_cli_installed(agent: str) -> bool:
    """True if the binary exists on PATH."""
    return shutil.which(agent) is not None


def is_signed_in(agent: str) -> tuple[bool, str]:
    """Probe the CLI; return (signed_in, reason)."""
    spec = SIGN_IN_PROBES.get(agent)
    if not spec:
        return True, "no probe configured"

    if agent == "aider":
        # Env-var based — check at least one provider key is present
        keys = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
        if any(os.environ.get(k) for k in keys):
            return True, "env key found"
        return False, "no provider env var (OPENAI_API_KEY etc.)"

    if not spec["probe_cmd"]:
        return True, "no probe"

    try:
        proc = subprocess.run(
            spec["probe_cmd"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "probe timed out"
    except FileNotFoundError:
        return False, f"binary '{agent}' not found on PATH"
    except Exception as e:
        return False, f"probe error: {e}"

    output = (proc.stdout + " " + proc.stderr).lower()
    for marker in spec["auth_error_markers"]:
        if marker.lower() in output:
            return False, f"auth marker hit: {marker}"

    if proc.returncode == 0:
        return True, "probe ok"
    # Non-zero but no auth marker → might be other issue, treat as signed-in
    return True, f"probe exit {proc.returncode}, no auth marker"


def trigger_sign_in(agent: str) -> dict:
    """Try to spawn the agent's interactive login flow.

    Returns {launched: bool, url: str, message: str}. We don't block waiting
    for the user to finish login — we surface the URL and let them complete
    in a browser.
    """
    spec = SIGN_IN_PROBES.get(agent, {})
    bus = get_bus()
    bus.emit("cli_agent", f"sign-in: launching for {agent}")

    if not spec.get("login_cmd"):
        return {
            "launched": False,
            "url": spec.get("manual_url", ""),
            "message": (
                f"Set the API key env var manually for {agent}, then retry. "
                f"See: {spec.get('manual_url', '')}"
            ),
        }

    try:
        # Spawn login flow in a new process; don't block
        subprocess.Popen(spec["login_cmd"])
        return {
            "launched": True,
            "url": spec.get("manual_url", ""),
            "message": (
                f"Opened {' '.join(spec['login_cmd'])} - complete sign-in, "
                "then ask me to retry your request."
            ),
        }
    except FileNotFoundError:
        return {
            "launched": False,
            "url": spec.get("manual_url", ""),
            "message": f"Binary '{agent}' not found. Install it first.",
        }
    except Exception as e:
        return {
            "launched": False,
            "url": spec.get("manual_url", ""),
            "message": f"Could not start login: {e}",
        }


def ensure_signed_in(agent: str) -> dict:
    """One-shot: probe + auto-trigger sign-in if needed.

    Returns {ready: bool, message: str} — caller decides whether to proceed
    with the actual task.
    """
    if not is_cli_installed(agent):
        spec = SIGN_IN_PROBES.get(agent, {})
        return {
            "ready": False,
            "message": (
                f"{agent} CLI is not installed. See: {spec.get('manual_url', 'https://github.com')}"
            ),
        }

    signed, reason = is_signed_in(agent)
    if signed:
        return {"ready": True, "message": f"{agent}: signed in ({reason})"}

    result = trigger_sign_in(agent)
    return {
        "ready": False,
        "message": result["message"],
        "url": result.get("url", ""),
        "launched": result.get("launched", False),
    }
