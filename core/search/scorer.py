"""Source scorer for ranking fetched sources by evidence quality."""

from __future__ import annotations

import re
from typing import List

from core.search.contracts import FetchedSource, SourcePassage


def score_passages(passages: List[SourcePassage]) -> List[SourcePassage]:
    """Sort passages by relevance score descending.

    Prefers exact-match evidence over generic results.
    """
    return sorted(passages, key=lambda p: p.relevance_score, reverse=True)


def _domain_bonus(url: str) -> float:
    """Small bonus for known authoritative domains."""
    lower = url.lower()
    authoritative = (
        ".gov",
        ".edu",
        "wikipedia.org",
        "github.com",
        "docs.python.org",
        "developer.mozilla.org",
        "rfc-editor.org",
        "ietf.org",
        "arxiv.org",
        "pubmed",
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "nytimes.com",
    )
    for domain in authoritative:
        if domain in lower:
            return 0.15
    return 0.0


def score_source(source: FetchedSource, query: str) -> float:
    """Score a fetched source overall.

    Factors:
    - Content length (up to a cap)
    - Query token overlap
    - Exact phrase match
    - Domain authority bonus
    """
    if source.status != "ok":
        return 0.0

    text = source.extracted_text
    score = 0.0

    # Length score (normalize to ~1.0 at 2000 chars)
    score += min(len(text) / 2000.0, 1.0) * 0.2

    # Token overlap
    query_tokens = set(re.findall(r"[a-z][a-z0-9]*", query.lower()))
    text_tokens = set(re.findall(r"[a-z][a-z0-9]*", text.lower()))
    if query_tokens:
        overlap = len(query_tokens & text_tokens) / len(query_tokens)
        score += overlap * 0.4

    # Exact phrase boost
    if query.lower() in text.lower():
        score += 0.25

    # Domain authority
    score += _domain_bonus(source.url)

    return round(min(score, 1.0), 3)
