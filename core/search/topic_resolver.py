"""Deterministic search topic resolver.

Recovers a concrete search query from user text and conversation context.
No LLM calls — pure regex/heuristic logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence


@dataclass(frozen=True)
class SearchTopicResolution:
    query: str
    confidence: Literal["high", "medium", "low"]
    needs_clarification: bool
    clarification_question: str
    reason: str
    used_context: tuple[str, ...]


# ── Regex constants ──────────────────────────────────────────────────────

_SEARCH_FILLER_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:search\s+(?:the\s+)?(?:web|internet|online)?\s*(?:for\s+)?|"
    r"look\s+(?:it\s+)?up(?:\s+for)?\s*|"
    r"find\s+(?:me\s+)?|"
    r"look\s+for\s+|"
    r"locate\s+)"
)

# Broader pronoun follow-up detection (catches "search for it please online")
_PRONOUN_FOLLOWUP_RE = re.compile(
    r"(?i)\b(?:search\s+(?:for\s+)?|look\s+(?:for\s+)?|find\s+|check\s+)"
    r"(it|this|that|them|those|these)\b"
)

# Simple standalone pronoun queries after prefix stripping
_PRONOUN_ONLY_QUERY_RE = re.compile(
    r"(?i)^\s*(?:it|this|that|them|those|these)\s*$"
)

_CORRECTION_RE = re.compile(
    r"(?i)\bno[,\s]+(?:i\s+meant|i\s+mean|actually|wait)\b"
    r"[:\s]*['\"]?(?P<corrected>[^'\"?!]{2,120})['\"]?"
)

# Generic + context patterns: "search for recent models" where prior topic is "AI"
_GENERIC_TOPIC_MERGE_RE = re.compile(
    r"(?i)^\s*(?:search\s+(?:for\s+)?|look\s+(?:for\s+)?|find\s+)?"
    r"(?P<generic>(?:recent|latest|current|new|old|best|top|popular)\s+\w+)\s*$"
)

_STALE_GREETING_RE = re.compile(
    r"(?i)^\s*(?:hi+|hello+|hey+|thanks?(?:\s+you)?|ty|ok(?:ay)?|cool|nice|great|good)\s*[.?!]*\s*$"
)

# Conversational prefixes that are not part of the topic
_CONVERSATIONAL_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:tell\s+me\s+(?:about|more\s+about)?|"
    r"what\s+(?:do\s+you\s+know\s+about|is|are)\s+|"
    r"how\s+(?:does|do|is|are)\s+|"
    r"why\s+(?:is|are|does|do)\s+|"
    r"where\s+(?:is|are)\s+|"
    r"when\s+(?:is|are|did|does)\s+|"
    r"who\s+(?:is|are|was|were)\s+|"
    r"can\s+you\s+(?:tell\s+me\s+(?:about)?|explain)\s+)"
)

# Words/phrases to strip from the end of a query
_TRAILING_FILLER_WORDS = (
    "please",
    "thanks",
    "thank you",
    "for me",
    "if you can",
    "if you could",
    "if you would",
    "when you can",
    "when you could",
    "when you would",
    "appreciate it",
    "online",
    "on the web",
    "on web",
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _strip_trailing_filler(text: str) -> str:
    """Remove known trailing politeness / filler words."""
    cleaned = text.strip().strip("\"'.,;:!?").strip()
    changed = True
    while changed:
        changed = False
        lowered = cleaned.lower().rstrip("\"'.,;:!?").strip()
        for phrase in _TRAILING_FILLER_WORDS:
            if lowered.endswith(phrase):
                cleaned = cleaned[: -len(phrase)].strip().strip("\"'.,;:!?").strip()
                changed = True
                break
    return cleaned


def _clean_query(text: str, *, strip_conversational: bool = False) -> str:
    """Normalize and strip filler from a candidate query."""
    cleaned = " ".join(str(text or "").split()).strip("\"'.,;:!?")
    if not cleaned:
        return ""
    # Strip leading filler
    cleaned = _SEARCH_FILLER_PREFIX_RE.sub("", cleaned)
    if strip_conversational:
        cleaned = _CONVERSATIONAL_PREFIX_RE.sub("", cleaned)
    # Strip trailing filler
    cleaned = _strip_trailing_filler(cleaned)
    cleaned = " ".join(cleaned.split()).strip("\"'.,;:!?")
    if len(cleaned) < 2:
        return ""
    return cleaned[:180]


def _extract_correction(text: str) -> str:
    """Detect 'no, I meant X' and return X."""
    match = _CORRECTION_RE.search(text)
    if match:
        return _clean_query(match.group("corrected"))
    return ""


def _is_pronoun_query(text: str) -> bool:
    """True if the query is just a pronoun (or pronoun + minimal filler) that needs context."""
    if not text:
        return False
    # Direct pronoun-only
    if _PRONOUN_ONLY_QUERY_RE.match(text):
        return True
    # Contains a pronoun reference like "search for it"
    if _PRONOUN_FOLLOWUP_RE.search(text):
        # Check if after cleaning it's still pronoun-ish or very short
        cleaned = _clean_query(text)
        if not cleaned or _PRONOUN_ONLY_QUERY_RE.match(cleaned):
            return True
        # If cleaned still contains a pronoun follow-up and little else
        if _PRONOUN_FOLLOWUP_RE.search(cleaned):
            remainder = _PRONOUN_FOLLOWUP_RE.sub("", cleaned).strip()
            if not remainder or len(remainder) < 4:
                return True
    return False


def _is_stale_greeting(text: str) -> bool:
    return bool(_STALE_GREETING_RE.match(text))


def _extract_last_user_topic(
    recent_history: Sequence[dict[str, str]],
    current_text: str,
) -> str:
    """Walk backwards through history to find the last non-trivial user message."""
    for item in reversed(list(recent_history)):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content or content == current_text:
            continue
        if _is_stale_greeting(content):
            continue
        cleaned = _clean_query(content, strip_conversational=True)
        if cleaned and not _is_pronoun_query(cleaned):
            return cleaned
    return ""


def _extract_topic_from_previous_request(previous_user_request: str) -> str:
    cleaned = _clean_query(previous_user_request, strip_conversational=True)
    if cleaned and not _is_pronoun_query(cleaned):
        return cleaned
    return ""


def _merge_generic_with_context(generic_query: str, context_topic: str) -> str:
    """Merge a generic qualifier with a prior topic, e.g. 'recent models' + 'AI' -> 'recent AI'."""
    generic_lower = generic_query.lower()
    context_lower = context_topic.lower()

    # Avoid duplication if context already contains the generic
    if context_lower in generic_lower or generic_lower in context_lower:
        return context_topic

    words = generic_query.split()
    if len(words) >= 2:
        qualifier = words[0].lower()
        if qualifier in {"recent", "latest", "current", "new", "old", "best", "top", "popular"}:
            return f"{qualifier} {context_topic}"
    return f"{generic_query} {context_topic}"


# ── Public API ───────────────────────────────────────────────────────────

def resolve_search_topic(
    user_text: str,
    recent_history: Sequence[dict[str, str]],
    *,
    previous_user_request: str = "",
    last_search_query: str = "",
    last_search_status: str = "",
) -> SearchTopicResolution:
    """Resolve a concrete search query from user text and context.

    Returns ``needs_clarification=True`` when the topic cannot be determined
    deterministically.  Never makes up a query.
    """
    text = str(user_text or "").strip()
    used_context: list[str] = []

    # 1. Stale greeting guard
    if _is_stale_greeting(text):
        return SearchTopicResolution(
            query="",
            confidence="low",
            needs_clarification=True,
            clarification_question="What would you like me to search for?",
            reason="stale_greeting",
            used_context=tuple(),
        )

    # 2. Correction detection (highest priority)
    corrected = _extract_correction(text)
    if corrected:
        return SearchTopicResolution(
            query=corrected,
            confidence="high",
            needs_clarification=False,
            clarification_question="",
            reason="correction_detected",
            used_context=tuple(),
        )

    # 3. Detect pronoun / generic follow-up BEFORE explicit extraction
    if _is_pronoun_query(text):
        context_sources = [
            ("previous_user_request", previous_user_request),
            ("last_search_query", last_search_query),
            ("history", _extract_last_user_topic(recent_history, text)),
        ]
        for source_name, source_value in context_sources:
            topic = (
                _extract_topic_from_previous_request(source_value)
                if source_name == "previous_user_request"
                else _clean_query(source_value)
            )
            if topic and not _is_pronoun_query(topic):
                used_context.append(source_name)
                return SearchTopicResolution(
                    query=topic,
                    confidence="medium",
                    needs_clarification=False,
                    clarification_question="",
                    reason="pronoun_resolved_from_context",
                    used_context=tuple(used_context),
                )
        # No context available -> ask for clarification
        return SearchTopicResolution(
            query="",
            confidence="low",
            needs_clarification=True,
            clarification_question="What should I search for?",
            reason="ambiguous_pronoun_no_context",
            used_context=tuple(),
        )

    # 4. Explicit query extraction
    explicit = _clean_query(text)

    # 5. Generic + context merge (e.g. "search for recent models" when prior topic was "AI")
    if explicit:
        generic_match = _GENERIC_TOPIC_MERGE_RE.match(text)
        if generic_match:
            context_sources = [
                ("previous_user_request", previous_user_request),
                ("last_search_query", last_search_query),
                ("history", _extract_last_user_topic(recent_history, text)),
            ]
            for source_name, source_value in context_sources:
                topic = (
                    _extract_topic_from_previous_request(source_value)
                    if source_name == "previous_user_request"
                    else _clean_query(source_value)
                )
                if topic and not _is_pronoun_query(topic):
                    merged = _merge_generic_with_context(explicit, topic)
                    used_context.append(source_name)
                    return SearchTopicResolution(
                        query=merged,
                        confidence="medium",
                        needs_clarification=False,
                        clarification_question="",
                        reason="generic_merged_with_context",
                        used_context=tuple(used_context),
                    )

    # 6. If we have an explicit query, use it
    if explicit:
        return SearchTopicResolution(
            query=explicit,
            confidence="high",
            needs_clarification=False,
            clarification_question="",
            reason="explicit_query_extracted",
            used_context=tuple(),
        )

    # 7. Fallback: try to use previous request as topic if it looks like a search intent
    prior_topic = _extract_topic_from_previous_request(previous_user_request)
    if prior_topic:
        return SearchTopicResolution(
            query=prior_topic,
            confidence="low",
            needs_clarification=False,
            clarification_question="",
            reason="fallback_to_previous_request",
            used_context=("previous_user_request",),
        )

    # 8. Nothing worked — ask for clarification
    return SearchTopicResolution(
        query="",
        confidence="low",
        needs_clarification=True,
        clarification_question="What would you like me to search for?",
        reason="no_query_identified",
        used_context=tuple(),
    )
