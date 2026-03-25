from __future__ import annotations

import re


def looks_like_live_environment_query(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if re.search(r"\bwhat(?:'s|s| is)?\s+(?:today'?s\s+)?date\b", lowered):
        return True
    if re.search(r"\bwhat(?:'s|s| is)?\s+(?:the\s+)?current\s+date\b", lowered):
        return True
    if re.search(r"\bwhat(?:'s|s| is)?\s+the\s+date\b", lowered):
        return True
    if re.search(r"\bwhat(?:'s|s| is)?\s+the\s+time\b", lowered):
        return True
    if re.search(r"\bwhat\s+time\s+is\s+it\b", lowered):
        return True
    if re.search(r"\bwhat\s+day\s+is\s+it\b", lowered):
        return True
    if re.search(r"\b(?:today|today's|current|right now|now)\b", lowered) and re.search(
        r"\b(?:date|time|day)\b", lowered
    ):
        return True
    return False
