"""Brain Updater — pulls community patterns + probes vendor APIs for new LLMs.

Two responsibilities:

1. `fetch_community_brain()` — downloads a JSON manifest from a public URL
   that lists community-shared patterns and skills. The manifest format:
       {
         "version": "1",
         "patterns": [{"trigger": "...", "skill": "..."}, ...],
         "skills": [{"name": "...", "description": "...", "code_url": "..."}],
         "model_scores": {"claude-sonnet": 95, ...}
       }
   Hosted at https://raw.githubusercontent.com/openbro/openbro-brain/main/manifest.json

2. `fetch_latest_models()` — queries each cloud vendor's /v1/models endpoint
   so OpenBro knows about new releases the moment they ship. Used by the
   daily LLM-update check.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

DEFAULT_MANIFEST_URL = "https://raw.githubusercontent.com/openbro/openbro-brain/main/manifest.json"


def fetch_community_brain(url: str = DEFAULT_MANIFEST_URL, timeout: int = 10) -> dict | None:
    """Pull the community manifest. Returns None if offline / 404 / parse error."""
    try:
        r = httpx.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        return data
    except (httpx.HTTPError, ValueError):
        return None


def apply_manifest(brain, manifest: dict) -> dict:
    """Apply a community manifest to the brain.

    Returns a summary of what changed. Skills are added with low initial
    confidence (0.3) so users can mark them trusted later.
    """
    summary = {
        "patterns_added": 0,
        "skills_added": 0,
        "model_scores_updated": 0,
        "errors": [],
    }

    # Skills
    for sk in manifest.get("skills", []) or []:
        try:
            name = sk.get("name", "").strip()
            if not name:
                continue
            if hasattr(brain, "skills") and brain.skills.get(name):
                continue  # don't overwrite existing
            code_url = sk.get("code_url")
            if not code_url:
                continue
            try:
                code = httpx.get(code_url, timeout=10).text
            except httpx.HTTPError as e:
                summary["errors"].append(f"download failed for {name}: {e}")
                continue
            brain.skills.add(
                name=name,
                code=code,
                description=sk.get("description", ""),
                triggers=sk.get("triggers", []),
            )
            summary["skills_added"] += 1
        except Exception as e:
            summary["errors"].append(str(e))

    # Model scores — merge into in-process CAPABILITY dict
    if manifest.get("model_scores"):
        try:
            from openbro.llm import auto_select

            for k, v in manifest["model_scores"].items():
                auto_select.CAPABILITY[k] = int(v)
            summary["model_scores_updated"] = len(manifest["model_scores"])
        except Exception as e:
            summary["errors"].append(f"model_scores: {e}")

    # Patterns — count only; pattern application is done lazily via reflection
    summary["patterns_added"] = len(manifest.get("patterns", []) or [])

    # Persist last-sync timestamp
    brain.storage.update_meta(last_community_sync=datetime.now(timezone.utc).isoformat())
    return summary


# ─── Live LLM-vendor probing ────────────────────────────────────────


def fetch_anthropic_models(api_key: str) -> list[str]:
    """Get the list of Claude models available to this key."""
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return [m["id"] for m in r.json().get("data", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def fetch_openai_models(api_key: str) -> list[str]:
    try:
        r = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return [m["id"] for m in r.json().get("data", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def fetch_groq_models(api_key: str) -> list[str]:
    try:
        r = httpx.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return [m["id"] for m in r.json().get("data", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def fetch_google_models(api_key: str) -> list[str]:
    try:
        r = httpx.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return [m["name"].split("/")[-1] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def fetch_latest_models(config: dict) -> dict:
    """Query each cloud vendor where the user has a key, return the latest IDs."""
    keys = config.get("providers", {}) or {}
    result: dict[str, list[str]] = {}

    for prov, fetcher in (
        ("anthropic", fetch_anthropic_models),
        ("openai", fetch_openai_models),
        ("groq", fetch_groq_models),
        ("google", fetch_google_models),
    ):
        api_key = (keys.get(prov) or {}).get("api_key")
        if not api_key:
            continue
        models = fetcher(api_key)
        if models:
            result[prov] = models
    return result


def detect_new_releases(config: dict, current: tuple[str, str]) -> list[dict]:
    """Compare freshly-fetched models with our CAPABILITY scores.

    Returns models that look newer than what the user is currently using.
    Heuristic: same provider as current, but a higher version-number suffix.
    """
    cur_provider, cur_model = current
    fresh = fetch_latest_models(config)
    suggestions = []
    available_models = fresh.get(cur_provider, [])

    # Simple version detection: claude-sonnet-4 < claude-sonnet-5
    import re as _re

    cur_ver_match = _re.search(r"(\d+)(?:[.-]\d+)*", cur_model)
    cur_ver = int(cur_ver_match.group(1)) if cur_ver_match else 0

    for m in available_models:
        m_ver_match = _re.search(r"(\d+)(?:[.-]\d+)*", m)
        m_ver = int(m_ver_match.group(1)) if m_ver_match else 0
        if m_ver > cur_ver and m != cur_model:
            suggestions.append(
                {
                    "provider": cur_provider,
                    "model": m,
                    "reason": f"newer version ({m_ver} > current {cur_ver})",
                }
            )
    return suggestions
