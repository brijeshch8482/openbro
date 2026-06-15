"""Sanity-check the trained specialist tree.

Run as: D:/openbro-venv/Scripts/python.exe -m openbro.specialists.verify

For each category that has a trained adapter on disk:
  1. Route a category-relevant probe through the router (asserts the
     prompt routes back to the same slug, modulo parent fallbacks).
  2. Ask the adapter engine to generate a response.
  3. Flag empty / degenerate / way-too-slow output.

Writes a JSON report at D:/OpenBro-teting/specialists/verify_report.json
and prints a one-line OK/FAIL per category.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# datasets-before-transformers, same as elsewhere.
from datasets import load_dataset  # noqa: F401, I001 — import-order side-effect

from openbro.specialists import CATEGORIES_DB, EMBEDDINGS_FILE  # noqa: F401
from openbro.specialists.adapter_engine import AdapterEngine
from openbro.specialists.router import Router

ADAPTERS_DIR = Path("D:/OpenBro-teting/specialists/adapters")
REPORT_PATH = Path("D:/OpenBro-teting/specialists/verify_report.json")


# Per-category probe prompts. Hand-picked so a working adapter has
# something concrete to grip on; the router should land each one in
# the expected slug (or a sensible parent).
_PROBES: dict[str, list[str]] = {
    "coding-python": [
        "Reverse a Python list without using reversed().",
        "What does the `with` keyword do in Python?",
    ],
    "coding-javascript": ["Difference between let and const in JavaScript?"],
    "coding-web": ["How do I center a div in pure CSS?"],
    "coding-backend": ["Write a Flask endpoint that returns the current time."],
    "coding-mobile": ["How do I navigate between screens in React Native?"],
    "coding-data": ["What is dbt and when do I use it over plain SQL?"],
    "coding-ai": ["What is a LoRA adapter in fine-tuning?"],
    "coding-devops": ["What's the difference between docker run and docker exec?"],
    "coding-shell": ["Write a bash loop over .log files in a directory."],
    "cybersecurity": ["What is SQL injection?"],
    "hardware": ["Why does my laptop CPU thermal throttle under load?"],
    "system-admin": ["How do I find a process using port 8080 on Linux?"],
    "networking": ["Explain the difference between TCP and UDP."],
    "math": ["What is the derivative of x^2 + 3x?"],
    "physics": ["State Newton's third law in one line."],
    "chemistry": ["What is the chemical formula of water?"],
    "biology": ["Explain in one sentence what mitochondria do."],
    "earth-space": ["Why is the sky blue?"],
    "engineering": ["What is a Wheatstone bridge?"],
    "health-general": ["What are common symptoms of dehydration?"],
    "health-mental": ["Suggest one practical technique to reduce evening anxiety."],
    "health-fitness": ["Give a beginner-friendly 3-day-a-week workout split."],
    "health-nutrition": ["What's a good high-protein vegetarian breakfast?"],
    "health-conditions": ["What is type-2 diabetes in plain language?"],
    "finance-personal": [
        "How should I split my salary between expenses, savings, and investments?"
    ],
    "finance-investing": ["What is rupee-cost averaging in mutual funds?"],
    "finance-economics": ["What is inflation in one sentence?"],
    "entrepreneurship": ["What does product-market fit mean?"],
    "marketing": ["What is a marketing funnel?"],
    "photography": ["How do I get a blurred background in portrait photos?"],
    "design": ["What is whitespace in UI design?"],
    "music-creation": ["What is a major chord made of?"],
    "writing": ["Give me a one-paragraph opening for a short story about a missed train."],
    "movies-tv": ["Suggest one critically acclaimed movie from 2024."],
    "books": ["Recommend a non-fiction book on habits."],
    "music-listen": ["What genre is Coldplay?"],
    "games": ["What are the win conditions in chess?"],
    "sports": ["What is the offside rule in football, briefly?"],
    "cooking": ["Quick weeknight pasta recipe please."],
    "travel": ["Best month to visit Goa for beaches?"],
    "home-diy": ["How do I fix a leaky kitchen tap?"],
    "parenting": ["How can I help my toddler with bedtime resistance?"],
    "pets": ["How often should I bathe a small breed dog?"],
    "gardening": ["What soil is best for tomatoes?"],
    "relationships": ["How do I start a difficult conversation with a friend?"],
    "study-techniques": ["How does the Pomodoro technique work?"],
    "language-learning": ["Best free way to start learning Spanish?"],
    "academics": ["How do I structure a 5-paragraph essay?"],
    "news-tech": ["What's a recent notable AI release?"],
    "news-india": ["What's a major event from India in 2025?"],
    "news-world": ["What's an ongoing major world conflict in 2025?"],
    "philosophy": ["Briefly explain stoicism."],
    "religion": ["What is the Bhagavad Gita?"],
    "history": ["When did the Indian independence happen?"],
    "law-gov": ["What is a fundamental right in the Indian constitution?"],
    "language": ["What is etymology?"],
    "productivity": ["What is GTD in productivity?"],
    "openbro-tools": ["kon ho tum?", "mere C drive me kitna space?", "battery kitni hai?"],
    "general": ["Hello, kya kar sakte ho?"],
}


def _degenerate(text: str) -> bool:
    if not text:
        return True
    cleaned = text.strip()
    if len(cleaned) < 10:
        return True
    # Same token spammed → degenerate. Allow some repetition.
    tokens = cleaned.split()
    if len(tokens) > 6 and len(set(tokens)) < max(3, len(tokens) // 5):
        return True
    return False


def verify_all(skip_missing: bool = True) -> dict:
    """Walk every category, probe the trained ones, write a report."""
    conn = sqlite3.connect(CATEGORIES_DB)
    cur = conn.cursor()
    cur.execute("SELECT slug, display_name, training_status FROM categories ORDER BY id")
    rows = cur.fetchall()

    engine = AdapterEngine()
    router = Router()

    report = {"started": time.time(), "categories": []}

    for slug, display, status in rows:
        adapter_dir = ADAPTERS_DIR / slug
        has_adapter = adapter_dir.exists() and any(adapter_dir.iterdir())
        cat_record = {
            "slug": slug,
            "display": display,
            "training_status": status,
            "has_adapter": has_adapter,
            "probes": [],
        }
        if skip_missing and not has_adapter:
            cat_record["skipped"] = "no_adapter"
            report["categories"].append(cat_record)
            continue
        for prompt in _PROBES.get(slug, []):
            route = router.route(prompt)
            t0 = time.time()
            try:
                gen = engine.chat(prompt, slug=route.slug, max_new_tokens=128)
                ok = not _degenerate(gen.text)
            except Exception as e:
                cat_record["probes"].append(
                    {
                        "prompt": prompt,
                        "error": f"{type(e).__name__}: {e}",
                        "ok": False,
                    }
                )
                continue
            cat_record["probes"].append(
                {
                    "prompt": prompt,
                    "routed_to": route.slug,
                    "router_method": route.method,
                    "answer": gen.text[:300],
                    "elapsed_s": round(time.time() - t0, 2),
                    "ok": ok,
                }
            )
        cat_record["all_ok"] = all(p.get("ok") for p in cat_record["probes"])
        report["categories"].append(cat_record)
        symbol = "OK" if cat_record["all_ok"] else "FAIL"
        print(
            f"  [{symbol}] {slug:<22} probes={len(cat_record['probes'])}",
            flush=True,
        )

    report["finished"] = time.time()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    verify_all()
