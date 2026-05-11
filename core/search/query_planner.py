"""Query planner: generates query variants for better coverage."""

from __future__ import annotations

import re
from typing import List

_QUOTE_RE = re.compile(r'"([^"]{10,500})"')
_LATEST_RE = re.compile(r"(?i)\b(latest|current|recent|news|today|this week|this month)\b")
_WHO_RE = re.compile(r"(?i)\b(who (said|wrote|composed|sang|performed)|attribution|author)\b")


def plan_queries(original_query: str) -> List[str]:
    """Generate query variants based on the query type.

    Rules:
    - Quote attribution: search the quoted text + attribution variants.
    - Latest/current questions: add dated variants.
    - Difficult questions: add 1-2 reformulated variants.
    - Meta follow-ups (e.g. "search more"): return empty list so caller can fall back.
    """
    original = original_query.strip()
    if not original:
        return []

    variants: List[str] = []

    # Detect quoted excerpts
    quotes = _QUOTE_RE.findall(original)
    if quotes:
        for q in quotes[:1]:
            variants.append(f'"{q}" attribution')
            variants.append(f'"{q}" source')
        return [original] + list(dict.fromkeys(variants))

    # Detect latest/current questions
    if _LATEST_RE.search(original):
        from datetime import datetime

        year = datetime.now().year
        variants.append(f"{original} {year}")
        return [original] + list(dict.fromkeys(variants))

    # Detect who-said/attribution questions
    if _WHO_RE.search(original):
        variants.append(f"{original} quote origin")
        return [original] + list(dict.fromkeys(variants))

    # General reformulation for difficult / ambiguous queries
    # Strip leading conversational filler and rephrase
    cleaned = re.sub(
        r"(?i)^\s*(?:can you\s+|please\s+|could you\s+|i want to know\s+|tell me\s+|what is\s+|what are\s+)",
        "",
        original,
    ).strip()
    if cleaned and cleaned != original and len(cleaned) > 10:
        variants.append(cleaned)

    # Meta follow-up guard: if the query looks like a meta search instruction,
    # return only the original so the caller can decide to ask for clarification.
    if re.search(r"(?i)^\s*(search\s+(more|again)|find\s+more|look\s+more)", original):
        return [original]

    return [original] + list(dict.fromkeys(variants))
