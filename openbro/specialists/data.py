"""Per-category dataset builder.

For every leaf category we want a JSONL training file at
D:/OpenBro-teting/specialists/datasets/<slug>.jsonl in the simple
{input, output} shape the trainer expects.

Sources, in priority order:
  1. Curated HF instruction datasets, filtered by keyword (Dolly,
     Alpaca, OpenOrca, OpenHermes — all small enough to load fully
     into RAM and keyword-filter in Python).
  2. Stack Exchange topic-relevant sites (live API, slow — used for
     categories with thin HF coverage).
  3. Manual / synthetic patterns hand-written for OpenBro-specific
     leaves (system tools, identity) — already proven on yesterday's
     v0.2 run.

Output ranges per category:
  * Categories with rich keyword overlap (coding-python,
    health-general, finance-personal): 5,000 – 25,000 examples.
  * Niche leaves: as many as keyword filtering yields, padded with
    synthetic where possible.
  * Caps are sized for what an RTX 3050 4 GB can realistically train
    in a few hours per category. The plumbing supports any size; the
    overnight orchestrator picks per-category caps.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# datasets must come before transformers/etc. on Windows or imports
# silently segfault (captured 2026-06-10).
from datasets import load_dataset  # noqa: I001

from openbro.specialists.db import init_db

DATASETS_DIR = Path("D:/OpenBro-teting/specialists/datasets")


# ─── HF dataset recipes ──────────────────────────────────────────────


def _stream_dolly() -> Iterable[tuple[str, str]]:
    ds = load_dataset("databricks/databricks-dolly-15k", split="train")
    for row in ds:
        yield row.get("instruction", "") or "", row.get("response", "") or ""


def _stream_alpaca() -> Iterable[tuple[str, str]]:
    ds = load_dataset("tatsu-lab/alpaca", split="train")
    for row in ds:
        prompt = row.get("instruction", "") or ""
        if row.get("input"):
            prompt = f"{prompt}\n\n{row['input']}"
        yield prompt, row.get("output", "") or ""


def _stream_openhermes() -> Iterable[tuple[str, str]]:
    try:
        ds = load_dataset("teknium/OpenHermes-2.5", split="train[:200000]")
    except Exception:
        return
    for row in ds:
        conv = row.get("conversations", []) or []
        if not isinstance(conv, list):
            continue
        user_msg = ""
        asst_msg = ""
        for m in conv:
            role = (m.get("from") or m.get("role") or "").lower()
            content = m.get("value") or m.get("content") or ""
            if role in ("user", "human") and not user_msg:
                user_msg = content
            elif role in ("gpt", "assistant", "ai") and not asst_msg:
                asst_msg = content
            if user_msg and asst_msg:
                break
        if user_msg and asst_msg:
            yield user_msg, asst_msg


def _stream_orca() -> Iterable[tuple[str, str]]:
    try:
        ds = load_dataset("Open-Orca/SlimOrca", split="train[:200000]")
    except Exception:
        return
    for row in ds:
        conv = row.get("conversations", []) or []
        if not isinstance(conv, list):
            continue
        user_msg = ""
        asst_msg = ""
        for m in conv:
            role = (m.get("from") or m.get("role") or "").lower()
            content = m.get("value") or m.get("content") or ""
            if role in ("user", "human") and not user_msg:
                user_msg = content
            elif role in ("gpt", "assistant", "ai") and not asst_msg:
                asst_msg = content
            if user_msg and asst_msg:
                break
        if user_msg and asst_msg:
            yield user_msg, asst_msg


@dataclass
class _CatRecipe:
    """One leaf category and the patterns we use to filter examples."""

    slug: str
    keywords: tuple[str, ...]


def _leaf_recipes(conn: sqlite3.Connection) -> list[_CatRecipe]:
    """Pull every leaf (no children) category and its keywords."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.slug, c.keywords
          FROM categories c
         WHERE c.id NOT IN (
                 SELECT DISTINCT parent_id FROM categories
                  WHERE parent_id IS NOT NULL
              )
           AND c.keywords <> ''
        """
    )
    out: list[_CatRecipe] = []
    for slug, kw in cur.fetchall():
        keywords = tuple(k.strip().lower() for k in (kw or "").split("|") if k.strip())
        if keywords:
            out.append(_CatRecipe(slug=slug, keywords=keywords))
    return out


def _matches(text: str, keywords: tuple[str, ...]) -> bool:
    """Substring match (case-insensitive). Cheap and good-enough for
    coarse classification at the dataset-building stage."""
    t = text.lower()
    return any(kw in t for kw in keywords)


def _length_ok(prompt: str, response: str) -> bool:
    return 10 < len(prompt) < 2000 and 30 < len(response) < 4000


def build_all(
    cap_per_category: int = 25_000,
    sources: tuple[str, ...] = ("dolly", "alpaca", "orca", "openhermes"),
) -> dict[str, int]:
    """Build a JSONL per leaf category, return counts per slug."""
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db()
    recipes = _leaf_recipes(conn)
    print(f"Building datasets for {len(recipes)} leaf categories…", flush=True)

    # Open every output file once and write as we stream sources.
    writers: dict[str, tuple[Path, int]] = {}
    counts: dict[str, int] = {r.slug: 0 for r in recipes}
    seen: dict[str, set] = {r.slug: set() for r in recipes}

    def _emit(slug: str, prompt: str, response: str) -> None:
        if counts[slug] >= cap_per_category:
            return
        key = hash((prompt[:120], response[:120]))
        if key in seen[slug]:
            return
        seen[slug].add(key)
        path, _ = writers.setdefault(slug, (DATASETS_DIR / f"{slug}.jsonl", 0))
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"input": prompt, "output": response, "_slug": slug},
                    ensure_ascii=False,
                )
                + "\n"
            )
        counts[slug] += 1

    # Wipe prior content so re-running is deterministic.
    for r in recipes:
        p = DATASETS_DIR / f"{r.slug}.jsonl"
        if p.exists():
            p.unlink()

    streams = []
    if "dolly" in sources:
        streams.append(("dolly", _stream_dolly))
    if "alpaca" in sources:
        streams.append(("alpaca", _stream_alpaca))
    if "orca" in sources:
        streams.append(("orca", _stream_orca))
    if "openhermes" in sources:
        streams.append(("openhermes", _stream_openhermes))

    total_rows = 0
    for src_name, factory in streams:
        try:
            stream = factory()
        except Exception as e:
            print(f"  {src_name}: failed ({type(e).__name__}: {e})", flush=True)
            continue
        n_src = 0
        for prompt, response in stream:
            n_src += 1
            if not _length_ok(prompt, response):
                continue
            # Try every leaf — multi-label is fine, same example can
            # land in two adjacent specialists.
            for r in recipes:
                if counts[r.slug] >= cap_per_category:
                    continue
                if _matches(prompt, r.keywords) or _matches(response, r.keywords):
                    _emit(r.slug, prompt, response)
            if n_src % 10000 == 0:
                print(f"  {src_name}: scanned {n_src}", flush=True)
        total_rows += n_src
        print(f"  {src_name}: done ({n_src} rows)", flush=True)

    # Persist counts back into the DB.
    cur = conn.cursor()
    for slug, n in counts.items():
        cur.execute(
            "UPDATE categories SET n_examples = ?, training_status = ? WHERE slug = ?",
            (n, "data_ready" if n >= 100 else "pending", slug),
        )
    conn.commit()
    return counts


# ─── Synthetic top-up for OpenBro-tools (always small) ──────────────


_OPENBRO_SYNTHETIC = [
    ("kon ho tum?", "OpenBro hu bhai — tera personal AI agent, fully offline kaam karta."),
    (
        "openbro kya hai?",
        "OpenBro ek open-source personal AI agent hai jo tera laptop "
        "control kar sakta hai — files, apps, system.",
    ),
    (
        "mere C drive me kitna space hai?",
        'Mai system_health tool chalata. <tool_call name="system_health">{"drive":"C"}</tool_call>',
    ),
    ("mere D drive ka health?", '<tool_call name="system_health">{"drive":"D"}</tool_call>'),
    ("battery kitni hai?", '<tool_call name="battery_status">{}</tool_call>'),
    ("chrome chal raha kya?", '<tool_call name="process_check">{"name":"chrome"}</tool_call>'),
    ("spotify kholo", '<tool_call name="open_app">{"name":"spotify"}</tool_call>'),
    ("browser band karo", '<tool_call name="close_app">{"name":"chrome"}</tool_call>'),
    ("screenshot lo", '<tool_call name="screenshot">{}</tool_call>'),
    (
        "kitne PDFs Desktop par hain?",
        '<tool_call name="file_search">{"path":"~/Desktop","pattern":"*.pdf"}</tool_call>',
    ),
    ("email check karo", '<tool_call name="email_check">{"unread_only":true}</tool_call>'),
    ("kal ka meeting check", '<tool_call name="calendar_check">{"date":"tomorrow"}</tool_call>'),
    (
        "Word me letter likho",
        '<tool_call name="create_document">{"app":"word","topic":"<topic>"}</tool_call>',
    ),
    ("Excel kholo", '<tool_call name="open_app">{"name":"excel"}</tool_call>'),
    ("system health check karo", '<tool_call name="system_health">{}</tool_call>'),
    (
        "openbro kya kya kar sakta hai?",
        "Files dhundh sakta, apps khol/band kar sakta, system health "
        "bata sakta, screenshots le sakta, email/calendar check kar "
        "sakta, code likh sakta.",
    ),
    (
        "kya tum web search kar sakte ho?",
        "Haan bhai, web_search tool hai. "
        '<tool_call name="web_search">{"query":"<query>"}</tool_call>',
    ),
    ("tu kaisa hai?", "Bilkul theek bhai, kaam ke liye ready. Tu bata kya karna hai?"),
    ("hello", "Hello bhai! OpenBro hu, kya help chahiye?"),
    ("hi", "Hi! Bata kya kaam karna hai."),
]


def topup_openbro_tools() -> int:
    """Write the synthetic OpenBro examples repeated enough times to
    give the model a strong identity signal even on a small leaf."""
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    path = DATASETS_DIR / "openbro-tools.jsonl"
    # Repeat each pattern 50× so the leaf gets ~1000 examples without
    # blowing up the dataset.
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for prompt, response in _OPENBRO_SYNTHETIC * 50:
            f.write(
                json.dumps(
                    {"input": prompt, "output": response, "_slug": "openbro-tools"},
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    conn = init_db()
    conn.execute(
        "UPDATE categories SET n_examples = ?, training_status = 'data_ready' "
        "WHERE slug = 'openbro-tools'",
        (n,),
    )
    conn.commit()
    return n


# ─── Sanity guard ────────────────────────────────────────────────────


def normalise_url(s: str) -> str:
    """Strip query strings and fragments from URLs so the same Q&A
    isn't kept twice just because of tracking params."""
    return re.sub(r"[?#].*$", "", s)
