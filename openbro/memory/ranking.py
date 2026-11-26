"""Small BM25 ranker for in-process text search (memories, fallbacks).

Corpora here are small (hundreds of notes), so a pure-Python BM25 is
fast enough and needs no index to keep in sync.

    score(d, q) = Σ IDF(w) · f·(k1+1) / (f + k1·(1 − b + b·|d|/avgdl))
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 1 or t.isdigit()]


def bm25_scores(query: str, docs: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    """Score every doc against the query. 0.0 = no query term present."""
    q_terms = set(tokenize(query))
    if not q_terms or not docs:
        return [0.0] * len(docs)
    tokenized = [tokenize(d) for d in docs]
    n = len(tokenized)
    avgdl = (sum(len(t) for t in tokenized) / n) or 1.0
    df = Counter()
    for toks in tokenized:
        for term in set(toks) & q_terms:
            df[term] += 1
    scores = []
    for toks in tokenized:
        tf = Counter(toks)
        dl = len(toks) or 1
        s = 0.0
        for term in q_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores
