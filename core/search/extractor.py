"""Passage extraction from fetched source content."""

from __future__ import annotations

import re
from typing import List

from core.search.contracts import SourcePassage


def _tokenize(text: str) -> set[str]:
    """Simple tokenization for overlap scoring."""
    return set(re.findall(r"[a-z][a-z0-9]*", text.lower()))


def _sentence_boundaries(text: str) -> List[tuple[int, int]]:
    """Return (start, end) indices of sentences in text."""
    # Simple sentence splitting on periods followed by space or newline
    boundaries: List[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"[.!?]\s+", text):
        end = match.end()
        boundaries.append((start, end))
        start = end
    if start < len(text):
        boundaries.append((start, len(text)))
    return boundaries


def extract_passages(
    text: str,
    query: str,
    source_url: str,
    source_title: str,
    *,
    max_passages: int = 3,
    max_chars_per_passage: int = 800,
) -> List[SourcePassage]:
    """Extract the most relevant passages from a source for a query.

    Uses a simple sentence-level scoring based on token overlap with the query.
    """
    if not text or not query:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    sentences = _sentence_boundaries(text)
    scored: List[tuple[float, int, int, str]] = []

    for start, end in sentences:
        sentence = text[start:end].strip()
        if len(sentence) < 20:
            continue
        sentence_tokens = _tokenize(sentence)
        overlap = len(query_tokens & sentence_tokens)
        score = overlap / max(len(query_tokens), 1)
        # Boost exact phrase matches
        query_lower = query.lower()
        if query_lower in sentence.lower():
            score += 1.0
        scored.append((score, start, end, sentence))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    passages: List[SourcePassage] = []
    seen_starts: set[int] = set()
    for score, start, end, sentence in scored:
        if start in seen_starts:
            continue
        # Avoid overlapping passages
        if any(abs(start - s) < max_chars_per_passage for s in seen_starts):
            continue
        seen_starts.add(start)
        passages.append(
            SourcePassage(
                text=sentence[:max_chars_per_passage],
                source_url=source_url,
                source_title=source_title,
                relevance_score=round(score, 3),
            )
        )
        if len(passages) >= max_passages:
            break

    return passages
