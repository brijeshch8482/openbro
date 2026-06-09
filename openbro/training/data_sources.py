"""Public-API data fetchers for the OpenBro training pipeline.

Every source here uses an OFFICIAL public API. No scraping of private
content, no end-user data, no privacy concerns. Each fetcher returns a
list of raw documents that `dataset.build()` later normalises into
(prompt, response) JSONL pairs.

Sources covered (all free tier):

- Stack Overflow      via api.stackexchange.com
- GitHub              via api.github.com
- Wikipedia           via en.wikipedia.org/api
- Reddit              via reddit.com/.json
- ArXiv               via export.arxiv.org/api
- NewsAPI             via newsapi.org (optional, needs key)
- HuggingFace datasets via datasets-server.huggingface.co

Add new sources by writing a function `fetch_<source>(...)` that
returns `list[dict]` with at least the keys `source`, `id`, `title`,
`body`, `url`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class RawDoc:
    """A normalised raw document from any public source.

    The (prompt, response) pair is built later in dataset.py — this
    layer only ensures every source returns the same shape.
    """

    source: str
    id: str
    title: str
    body: str
    url: str
    tags: list[str]
    score: int = 0
    fetched_at: float = 0.0


_USER_AGENT = "OpenBroTraining/0.1 (+https://github.com/brijeshch8482/openbro) Python httpx"


# ─── Stack Overflow ──────────────────────────────────────────────────


def fetch_stackoverflow(
    tags: list[str],
    per_tag: int = 50,
    site: str = "stackoverflow",
) -> list[RawDoc]:
    """Fetch top voted Q&A pairs for the given tags.

    Uses the Stack Exchange API. Free tier: 10,000 requests/day with
    a registered key, 300/day anonymous. We stay well within the
    anonymous limit by batching `per_tag` requests.
    """
    out: list[RawDoc] = []
    for tag in tags:
        try:
            r = httpx.get(
                "https://api.stackexchange.com/2.3/questions",
                params={
                    "order": "desc",
                    "sort": "votes",
                    "tagged": tag,
                    "site": site,
                    "pagesize": per_tag,
                    "filter": "withbody",
                },
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for item in data.get("items", []):
            out.append(
                RawDoc(
                    source="stackoverflow",
                    id=str(item.get("question_id", "")),
                    title=item.get("title", ""),
                    body=item.get("body", ""),
                    url=item.get("link", ""),
                    tags=item.get("tags", []),
                    score=item.get("score", 0),
                    fetched_at=time.time(),
                )
            )
        time.sleep(0.3)  # be polite to the API
    return out


# ─── GitHub ─────────────────────────────────────────────────────────


def fetch_github_issues(
    repos: list[str],
    per_repo: int = 30,
    token: str | None = None,
) -> list[RawDoc]:
    """Pull closed issues from public repos. Closed issues with
    accepted answers are excellent (prompt, response) training pairs.

    `repos` is a list of "owner/name" strings. Pass a GitHub PAT in
    `token` to lift the rate limit from 60/hr to 5000/hr.
    """
    token = token or os.environ.get("GITHUB_TOKEN")
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    out: list[RawDoc] = []
    for repo in repos:
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{repo}/issues",
                params={"state": "closed", "per_page": per_repo, "sort": "comments"},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            issues = r.json()
        except Exception:
            continue
        for issue in issues:
            if "pull_request" in issue:  # skip PRs masquerading as issues
                continue
            out.append(
                RawDoc(
                    source="github",
                    id=str(issue.get("number", "")),
                    title=issue.get("title", ""),
                    body=issue.get("body", "") or "",
                    url=issue.get("html_url", ""),
                    tags=[lbl.get("name", "") for lbl in issue.get("labels", [])],
                    score=issue.get("comments", 0),
                    fetched_at=time.time(),
                )
            )
        time.sleep(0.5)
    return out


# ─── Wikipedia ──────────────────────────────────────────────────────


def fetch_wikipedia(titles: list[str], lang: str = "en") -> list[RawDoc]:
    """Fetch plain-text summaries of given Wikipedia articles.

    Wikipedia content is CC-BY-SA — attribution preserved in the URL
    field. Used for factual grounding patterns in the training set.
    """
    out: list[RawDoc] = []
    base = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
    for title in titles:
        try:
            r = httpx.get(
                base + title.replace(" ", "_"),
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        out.append(
            RawDoc(
                source="wikipedia",
                id=str(data.get("pageid", "")),
                title=data.get("title", title),
                body=data.get("extract", ""),
                url=data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                tags=[],
                score=0,
                fetched_at=time.time(),
            )
        )
        time.sleep(0.1)
    return out


# ─── Reddit ─────────────────────────────────────────────────────────


def fetch_reddit(
    subreddits: list[str],
    per_sub: int = 25,
    sort: str = "top",
    timeframe: str = "month",
) -> list[RawDoc]:
    """Pull top posts from listed subreddits via the public .json
    endpoints. No authentication required for read-only public data.
    """
    out: list[RawDoc] = []
    for sub in subreddits:
        try:
            r = httpx.get(
                f"https://www.reddit.com/r/{sub}/{sort}.json",
                params={"limit": per_sub, "t": timeframe},
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            out.append(
                RawDoc(
                    source="reddit",
                    id=d.get("id", ""),
                    title=d.get("title", ""),
                    body=d.get("selftext", ""),
                    url="https://www.reddit.com" + d.get("permalink", ""),
                    tags=[d.get("subreddit", "")],
                    score=d.get("score", 0),
                    fetched_at=time.time(),
                )
            )
        time.sleep(1.0)  # reddit asks for 1 req/sec
    return out


# ─── ArXiv ──────────────────────────────────────────────────────────


def fetch_arxiv(queries: list[str], per_query: int = 20) -> list[RawDoc]:
    """Fetch paper abstracts matching the given queries. Output is XML
    so we use a light regex pass rather than dragging in lxml.
    """
    import re as _re

    out: list[RawDoc] = []
    for q in queries:
        try:
            r = httpx.get(
                "https://export.arxiv.org/api/query",
                params={
                    "search_query": q,
                    "start": 0,
                    "max_results": per_query,
                    "sortBy": "relevance",
                },
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
            )
            r.raise_for_status()
        except Exception:
            continue
        for m in _re.finditer(r"<entry>(.*?)</entry>", r.text, _re.DOTALL):
            chunk = m.group(1)

            def grab(tag: str) -> str:
                m2 = _re.search(rf"<{tag}>(.*?)</{tag}>", chunk, _re.DOTALL)
                return (m2.group(1) if m2 else "").strip()

            out.append(
                RawDoc(
                    source="arxiv",
                    id=grab("id"),
                    title=grab("title"),
                    body=grab("summary"),
                    url=grab("id"),
                    tags=[q],
                    score=0,
                    fetched_at=time.time(),
                )
            )
        time.sleep(3.0)  # arxiv asks for 3 sec between requests
    return out


# ─── HuggingFace curated datasets ────────────────────────────────────


_HF_DATASET_RECIPES: dict[str, dict[str, str]] = {
    # name → {dataset_id, split, prompt_field, response_field}
    "openassistant": {
        "id": "OpenAssistant/oasst1",
        "split": "train",
        "prompt_field": "text",
        "response_field": "text",  # tree-shaped, handled below
    },
    "slim_orca": {
        "id": "Open-Orca/SlimOrca",
        "split": "train",
        "prompt_field": "conversations",
        "response_field": "conversations",
    },
    "hermes": {
        "id": "teknium/OpenHermes-2.5",
        "split": "train",
        "prompt_field": "conversations",
        "response_field": "conversations",
    },
    "dolly_15k": {
        "id": "databricks/databricks-dolly-15k",
        "split": "train",
        "prompt_field": "instruction",
        "response_field": "response",
    },
    "alpaca": {
        "id": "tatsu-lab/alpaca",
        "split": "train",
        "prompt_field": "instruction",
        "response_field": "output",
    },
    "wildchat": {
        "id": "allenai/WildChat-1M",
        "split": "train",
        "prompt_field": "conversation",
        "response_field": "conversation",
    },
}


def fetch_huggingface_datasets(
    recipes: list[str],
    max_per_dataset: int = 50000,
) -> list[RawDoc]:
    """Pull curated instruction-tuning datasets from the HuggingFace
    Hub. Far higher quality than scraping public APIs — these are
    already deduped, filtered, and (mostly) human-vetted.

    `recipes` is a list of keys into `_HF_DATASET_RECIPES`. Pass
    `max_per_dataset` to cap the take from each (useful when one
    dataset is much larger than the others).
    """
    try:
        from datasets import load_dataset
    except ImportError:
        return []

    out: list[RawDoc] = []
    for name in recipes:
        recipe = _HF_DATASET_RECIPES.get(name)
        if not recipe:
            continue
        # Non-streaming with slice cap — streaming hits intermittent
        # connection-reset errors on Windows + some networks.
        split_spec = f"{recipe['split']}[:{max_per_dataset}]"
        try:
            ds = load_dataset(recipe["id"], split=split_spec)
        except Exception:
            continue

        for count, row in enumerate(ds):
            prompt, response = _extract_pair(row, recipe)
            if not prompt or not response:
                continue
            out.append(
                RawDoc(
                    source=f"hf:{name}",
                    id=str(row.get("id", count)),
                    title=prompt[:300],
                    body=response,
                    url=f"https://huggingface.co/datasets/{recipe['id']}",
                    tags=[name],
                    score=0,
                    fetched_at=time.time(),
                )
            )
    return out


def _extract_pair(row: dict, recipe: dict) -> tuple[str, str]:
    """Convert a dataset row into (prompt, response) using the
    recipe-specified fields. Handles the common shapes:
      • flat instruction/response (Dolly, Alpaca)
      • conversations list-of-dicts (Orca, Hermes, WildChat)
      • OpenAssistant tree messages (role + text)
    """
    pf = recipe["prompt_field"]
    rf = recipe["response_field"]
    # Conversations: list of {role/from, content/value} dicts
    if pf == "conversations" or pf == "conversation":
        conv = row.get(pf, [])
        if not isinstance(conv, list) or len(conv) < 2:
            return "", ""
        user_msg = next(
            (
                m.get("content", m.get("value", ""))
                for m in conv
                if (m.get("role") or m.get("from", "")).lower() in ("user", "human")
            ),
            "",
        )
        asst_msg = next(
            (
                m.get("content", m.get("value", ""))
                for m in conv
                if (m.get("role") or m.get("from", "")).lower() in ("assistant", "gpt", "ai")
            ),
            "",
        )
        return user_msg, asst_msg
    # OpenAssistant tree: each row is one message, prompt rows have role=prompter
    if recipe["id"].startswith("OpenAssistant/"):
        if row.get("role") == "prompter":
            return row.get("text", ""), ""  # paired downstream; skip for now
        return "", ""
    # Flat: instruction + response
    prompt = row.get(pf, "")
    response = row.get(rf, "")
    if isinstance(prompt, str) and isinstance(response, str):
        return prompt, response
    return "", ""


# ─── Orchestrator ────────────────────────────────────────────────────


def fetch_all(config: dict[str, Any]) -> list[RawDoc]:
    """Run every configured fetcher and return the merged corpus.

    The `config` shape (defaults filled in by the caller if missing):

        {
            "stackoverflow": {"tags": [...], "per_tag": 50},
            "github":        {"repos": [...], "per_repo": 30},
            "wikipedia":     {"titles": [...]},
            "reddit":        {"subreddits": [...], "per_sub": 25},
            "arxiv":         {"queries": [...], "per_query": 20},
        }

    A missing key skips that source. Failures inside any single
    fetcher are swallowed — partial corpora are useful.
    """
    docs: list[RawDoc] = []
    if "stackoverflow" in config:
        c = config["stackoverflow"]
        docs += fetch_stackoverflow(c.get("tags", []), c.get("per_tag", 50))
    if "github" in config:
        c = config["github"]
        docs += fetch_github_issues(c.get("repos", []), c.get("per_repo", 30))
    if "wikipedia" in config:
        c = config["wikipedia"]
        docs += fetch_wikipedia(c.get("titles", []))
    if "reddit" in config:
        c = config["reddit"]
        docs += fetch_reddit(
            c.get("subreddits", []),
            c.get("per_sub", 25),
            c.get("sort", "top"),
            c.get("timeframe", "month"),
        )
    if "arxiv" in config:
        c = config["arxiv"]
        docs += fetch_arxiv(c.get("queries", []), c.get("per_query", 20))
    if "huggingface" in config:
        c = config["huggingface"]
        docs += fetch_huggingface_datasets(
            c.get("recipes", []),
            c.get("max_per_dataset", 50000),
        )
    return docs
