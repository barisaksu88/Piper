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

_LEADING_ADVERB_RE = re.compile(r"(?i)^\s*(?:now|then|so|well|okay|ok)[,\s]+")

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
_REFOCUS_QUERY_RE = re.compile(
    r"(?is)^\s*(?:(?:no|wait|sorry|but)[,\s]+)?(?:actually[,\s]+)?(?:"
    r"i\s+(?:was\s+)?(?:asking|looking|talking|thinking)\s+(?:more\s+)?(?:about|for|on)\s+"
    r"|i\s+(?:mean|meant|wanted|want)\s+(?:to\s+(?:ask|search|know)\s+)?(?:more\s+)?(?:about|for|on)?\s*"
    r"|it\s+got\s+cut\s+off[,\s]+i\s+(?:mean|meant)\s+"
    r")(?P<topic>[^?!]{2,180})[.?!]*\s*$"
)
_REFOCUS_TOPIC_PREFIX_RE = re.compile(
    r"(?is)^\s*(?:more\s+)?(?:about|for|on)\s+|^\s*research\s+(?:about|on)\s+(?:the\s+)?"
)

# Generic + context patterns: "search for recent models" where prior topic is "AI"
_GENERIC_TOPIC_MERGE_RE = re.compile(
    r"(?i)^\s*(?:search\s+(?:for\s+)?|look\s+(?:for\s+)?|find\s+)?"
    r"(?P<generic>(?:(?:recent|latest|current|new|old|best|top|popular)\s+)?\w+)\s*$"
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

_SEARCH_REQUEST_TAIL_RE = re.compile(
    r"(?is)\s+(?:"
    r"and\s+(?:then\s+)?tell\s+me\s+what\s+you\s+(?:already\s+)?know(?:\s+about\s+it)?(?:\s+while\s+it\s+loads)?"
    r"|and\s+(?:then\s+)?tell\s+me\s+what\s+you\s+find"
    r"|and\s+(?:then\s+)?let\s+me\s+know\s+what\s+you\s+find"
    r"|and\s+(?:then\s+)?give\s+me\s+(?:an\s+)?update"
    r"|and\s+(?:then\s+)?keep\s+me\s+posted"
    r"|while\s+it\s+loads"
    r"|while\s+you(?:'re|\s+are)?\s+searching"
    r"|while\s+the\s+search\s+runs"
    r")\s*$"
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

_GENERIC_QUERY_TERMS = {
    "article",
    "articles",
    "benchmark",
    "benchmarks",
    "breakthrough",
    "breakthroughs",
    "current",
    "detail",
    "details",
    "development",
    "developments",
    "info",
    "information",
    "latest",
    "model",
    "models",
    "news",
    "new",
    "old",
    "popular",
    "recent",
    "release",
    "releases",
    "report",
    "reports",
    "result",
    "results",
    "ranking",
    "rankings",
    "top",
    "update",
    "updates",
    "version",
    "versions",
}

_CONTEXT_STOPWORDS = {
    "a",
    "about",
    "actually",
    "all",
    "always",
    "an",
    "and",
    "are",
    "as",
    "asked",
    "asking",
    "at",
    "be",
    "been",
    "being",
    "but",
    "by",
    "can",
    "could",
    "do",
    "does",
    "doing",
    "for",
    "from",
    "goes",
    "going",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "its",
    "know",
    "like",
    "look",
    "me",
    "mean",
    "meant",
    "more",
    "never",
    "no",
    "not",
    "of",
    "on",
    "online",
    "please",
    "search",
    "searching",
    "that",
    "the",
    "them",
    "these",
    "this",
    "those",
    "to",
    "up",
    "want",
    "wanted",
    "wanting",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "who",
    "why",
    "with",
    "would",
    "you",
    "your",
}

_CONTEXT_ASPECT_MAP = {
    "benchmark": "benchmarks",
    "benchmarks": "benchmarks",
    "developing": "developments",
    "development": "developments",
    "developments": "developments",
    "improving": "improvements",
    "improvement": "improvements",
    "improvements": "improvements",
    "model": "models",
    "models": "models",
    "news": "news",
    "release": "releases",
    "releases": "releases",
    "result": "results",
    "results": "results",
    "update": "updates",
    "updates": "updates",
    "version": "versions",
    "versions": "versions",
}


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
    cleaned = _LEADING_ADVERB_RE.sub("", cleaned)
    cleaned = _SEARCH_FILLER_PREFIX_RE.sub("", cleaned)
    if strip_conversational:
        cleaned = _CONVERSATIONAL_PREFIX_RE.sub("", cleaned)
    while cleaned:
        trimmed = _SEARCH_REQUEST_TAIL_RE.sub("", cleaned).strip("\"'.,;:!?").strip()
        if trimmed == cleaned:
            break
        cleaned = trimmed
    # Strip trailing filler
    cleaned = _strip_trailing_filler(cleaned)
    cleaned = " ".join(cleaned.split()).strip("\"'.,;:!?")
    if len(cleaned) < 2:
        return ""
    return cleaned[:180]


def _word_tokens(text: str) -> list[tuple[str, str]]:
    """Return (surface, normalized) word tokens while preserving acronyms."""
    tokens: list[tuple[str, str]] = []
    for match in re.finditer(r"[A-Za-z0-9][A-Za-z0-9.+#-]*", str(text or "")):
        surface = match.group(0).strip(".")
        normalized = surface.lower().strip(".")
        if surface and normalized:
            tokens.append((surface, normalized))
    return tokens


def _is_underspecified_query(text: str) -> bool:
    cleaned = _clean_query(text)
    if not cleaned:
        return True
    if _is_pronoun_query(cleaned):
        return True
    tokens = _word_tokens(cleaned)
    if not tokens:
        return True
    meaningful = [
        norm
        for _surface, norm in tokens
        if norm not in _CONTEXT_STOPWORDS
    ]
    if not meaningful:
        return True
    return all(token in _GENERIC_QUERY_TERMS for token in meaningful)


def _context_terms(text: str) -> tuple[list[str], list[str], str]:
    cleaned = _clean_query(text, strip_conversational=True)
    if not cleaned or _is_pronoun_query(cleaned) or _is_stale_greeting(cleaned):
        return [], [], ""

    entities: list[str] = []
    aspects: list[str] = []
    for surface, norm in _word_tokens(cleaned):
        if norm in _CONTEXT_STOPWORDS:
            continue
        aspect = _CONTEXT_ASPECT_MAP.get(norm)
        if aspect:
            if aspect not in aspects:
                aspects.append(aspect)
            continue
        if norm in {"latest", "recent", "current", "new", "old", "top", "best", "popular"}:
            continue
        token = surface.upper() if surface.isupper() else surface
        if token not in entities:
            entities.append(token)
    return entities, aspects, cleaned


def _context_topic_from_text(text: str) -> str:
    entities, aspects, cleaned = _context_terms(text)
    if not entities:
        return ""
    if aspects:
        return " ".join([*entities, aspects[0]]).strip()
    return cleaned


def _context_entity_from_text(text: str) -> str:
    entities, _aspects, _cleaned = _context_terms(text)
    return " ".join(entities).strip()


def _context_sources(
    recent_history: Sequence[dict[str, str]],
    current_text: str,
    *,
    previous_user_request: str = "",
    last_search_query: str = "",
) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    if previous_user_request:
        sources.append(("previous_user_request", previous_user_request))
    if last_search_query and not _is_underspecified_query(last_search_query):
        sources.append(("last_search_query", last_search_query))

    user_sources: list[tuple[str, str]] = []
    assistant_sources: list[tuple[str, str]] = []
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content or content == current_text:
            continue
        if role == "user":
            user_sources.append(("history", content))
        elif role == "assistant":
            assistant_sources.append(("assistant_history", content))
    sources.extend(user_sources)
    sources.extend(assistant_sources)
    return sources


def _best_context_topic(
    recent_history: Sequence[dict[str, str]],
    current_text: str,
    *,
    previous_user_request: str = "",
    last_search_query: str = "",
) -> tuple[str, str]:
    for source_name, source_value in _context_sources(
        recent_history,
        current_text,
        previous_user_request=previous_user_request,
        last_search_query=last_search_query,
    ):
        topic = _context_topic_from_text(source_value)
        if topic:
            return topic, source_name
    return "", ""


def _best_context_entity(
    recent_history: Sequence[dict[str, str]],
    current_text: str,
    *,
    previous_user_request: str = "",
    last_search_query: str = "",
) -> tuple[str, str]:
    for source_name, source_value in _context_sources(
        recent_history,
        current_text,
        previous_user_request=previous_user_request,
        last_search_query=last_search_query,
    ):
        entity = _context_entity_from_text(source_value)
        if entity:
            return entity, source_name
    return "", ""


def _merge_query_with_context(query: str, context_entity: str) -> str:
    clean_query = _clean_query(query)
    clean_context = _clean_query(context_entity, strip_conversational=True)
    if not clean_query:
        return clean_context
    if not clean_context:
        return clean_query

    query_tokens = [surface for surface, norm in _word_tokens(clean_query) if norm not in _CONTEXT_STOPWORDS]
    if not query_tokens:
        return clean_context

    qualifier = ""
    if query_tokens[0].lower() in {"recent", "latest", "current", "new", "old", "best", "top", "popular"}:
        qualifier = query_tokens.pop(0).lower()

    context_lower = clean_context.lower()
    tail = [token for token in query_tokens if token.lower() not in context_lower.split()]
    parts = [part for part in (qualifier, clean_context, " ".join(tail)) if part]
    return " ".join(parts).strip()


def _extract_correction(text: str) -> str:
    """Detect 'no, I meant X' and return X."""
    match = _CORRECTION_RE.search(text)
    if match:
        return _clean_query(match.group("corrected"))
    return ""


def _extract_refocus_query(text: str) -> str:
    """Detect conversational search refocus text and return the intended topic."""
    match = _REFOCUS_QUERY_RE.match(str(text or ""))
    if not match:
        return ""
    topic = str(match.group("topic") or "").strip()
    previous = None
    while previous != topic:
        previous = topic
        topic = _REFOCUS_TOPIC_PREFIX_RE.sub("", topic).strip()
    return _clean_query(topic, strip_conversational=True)


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

    # 2b. Conversational refocus after a search/report turn:
    # "actually I was asking more about models" should become "AI models",
    # not the whole sentence.
    refocus = _extract_refocus_query(text)
    if refocus:
        if _is_underspecified_query(refocus):
            context_entity, source_name = _best_context_entity(
                recent_history,
                text,
                previous_user_request=previous_user_request,
                last_search_query=last_search_query,
            )
            if context_entity:
                merged = _merge_query_with_context(refocus, context_entity)
                used_context.append(source_name)
                return SearchTopicResolution(
                    query=merged,
                    confidence="medium",
                    needs_clarification=False,
                    clarification_question="",
                    reason="refocus_merged_with_context",
                    used_context=tuple(used_context),
                )
            return SearchTopicResolution(
                query="",
                confidence="low",
                needs_clarification=True,
                clarification_question=f"What kind of {refocus} should I search for?",
                reason="refocus_underspecified_no_context",
                used_context=tuple(),
            )
        return SearchTopicResolution(
            query=refocus,
            confidence="high",
            needs_clarification=False,
            clarification_question="",
            reason="refocus_detected",
            used_context=tuple(),
        )

    # 3. Detect pronoun / generic follow-up BEFORE explicit extraction
    if _is_pronoun_query(text):
        topic, source_name = _best_context_topic(
            recent_history,
            text,
            previous_user_request=previous_user_request,
            last_search_query=last_search_query,
        )
        if topic:
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

    # 5. Underspecified + context merge (e.g. "models" when prior topic was "AI")
    if explicit and _is_underspecified_query(explicit):
        context_entity, source_name = _best_context_entity(
            recent_history,
            text,
            previous_user_request=previous_user_request,
            last_search_query=last_search_query,
        )
        if context_entity:
            merged = _merge_query_with_context(explicit, context_entity)
            used_context.append(source_name)
            return SearchTopicResolution(
                query=merged,
                confidence="medium",
                needs_clarification=False,
                clarification_question="",
                reason="underspecified_merged_with_context",
                used_context=tuple(used_context),
            )
        return SearchTopicResolution(
            query="",
            confidence="low",
            needs_clarification=True,
            clarification_question=f"What kind of {explicit} should I search for?",
            reason="underspecified_query_no_context",
            used_context=tuple(),
        )

    # 6. Generic + context merge (legacy qualifier pattern)
    if explicit:
        generic_match = _GENERIC_TOPIC_MERGE_RE.match(text)
        if generic_match and _is_underspecified_query(generic_match.group("generic")):
            context_entity, source_name = _best_context_entity(
                recent_history,
                text,
                previous_user_request=previous_user_request,
                last_search_query=last_search_query,
            )
            if context_entity:
                merged = _merge_query_with_context(explicit, context_entity)
                used_context.append(source_name)
                return SearchTopicResolution(
                    query=merged,
                    confidence="medium",
                    needs_clarification=False,
                    clarification_question="",
                    reason="generic_merged_with_context",
                    used_context=tuple(used_context),
                )

    # 7. If we have an explicit query, use it
    if explicit:
        return SearchTopicResolution(
            query=explicit,
            confidence="high",
            needs_clarification=False,
            clarification_question="",
            reason="explicit_query_extracted",
            used_context=tuple(),
        )

    # 8. Fallback: try to use previous request as topic if it looks like a search intent
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

    # 9. Nothing worked — ask for clarification
    return SearchTopicResolution(
        query="",
        confidence="low",
        needs_clarification=True,
        clarification_question="What would you like me to search for?",
        reason="no_query_identified",
        used_context=tuple(),
    )
