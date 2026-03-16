from __future__ import annotations

import re
from typing import Any, Dict, List

_GROUNDING_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "for", "from", "has", "have", "had", "he",
    "her", "hers", "him", "his", "i", "in", "informed", "is", "it", "its", "me", "my", "of",
    "on", "or", "our", "replied", "said", "she", "that", "the", "their", "them", "they", "this",
    "to", "told", "u", "user", "was", "we", "were", "will", "with", "you", "your",
}
_META_ATTRIBUTE_TOKENS = {
    "assistant",
    "chat",
    "clarified",
    "clarification",
    "confused",
    "confusion",
    "conversation",
    "corrected",
    "correction",
    "guess",
    "guessed",
    "mistake",
    "misunderstanding",
    "question",
    "request",
    "response",
    "user",
}
_SUSPICIOUS_VALUE_RE = re.compile(
    r"\buser_[a-z0-9_]+\b|"
    r"\b(?:true|false|null)\b|"
    r"(?<![<>=!])=(?![=])|"
    r"[{}\[\]]",
    re.IGNORECASE,
)
_SUSPICIOUS_KEY_RE = re.compile(
    r"^\s*(?:i\b|i['’]?m\b|im\b|we\b|we['’]?re\b|were\b)"
    r"|"
    r"\b(?:working on|watching|playing|testing|debugging|using|trying to)\b"
    r"|"
    r"\b(?:which|that)\s*$",
    re.IGNORECASE,
)


def profile_update_is_grounded(key: str, value: Any, history_text_value: str) -> bool:
    return profile_key_is_grounded(key, history_text_value) or profile_value_is_grounded(value, history_text_value)


def profile_fact_shape_is_allowed(key: str, value: Any) -> bool:
    key_text = str(key or "").strip()
    if not key_text:
        return False
    if _SUSPICIOUS_KEY_RE.search(key_text):
        return False
    key_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", key_text.lower())
        if token
    }
    if key_tokens & _META_ATTRIBUTE_TOKENS:
        return False

    candidate = str(value or "").strip()
    if not candidate:
        return False
    if _SUSPICIOUS_VALUE_RE.search(candidate):
        return False
    return True


def profile_key_is_grounded(key: str, history_text_value: str) -> bool:
    key_l = str(key).strip().lower()
    if not key_l:
        return False
    if key_l in history_text_value:
        return True

    patterns = {
        "name": (r"\bmy name is\b", r"\bi am\b", r"\bi'm\b"),
        "family/relationships": (
            r"\bmy (sister|brother|daughter|son|girlfriend|boyfriend|wife|husband|partner|mother|mom|father|dad|friend)\b",
            r"\bour (daughter|son)\b",
            r"\bi (?:also\s+)?have a\b",
        ),
        "job": (
            r"\bi work as\b",
            r"\bi also work as\b",
            r"\bmy job\b",
            r"\bi am(?: also)? an?\b",
            r"\bi'm(?: also)? an?\b",
            r"\bi fly for\b",
        ),
        "occupation": (
            r"\bi work as\b",
            r"\bi also work as\b",
            r"\bmy job\b",
            r"\bi am(?: also)? an?\b",
            r"\bi'm(?: also)? an?\b",
            r"\bi fly for\b",
        ),
        "location": (r"\bi live in\b", r"\bi moved to\b", r"\bi am in\b", r"\bi'm in\b", r"\bi'm from\b", r"\bi am from\b"),
        "likes/interests": (r"\bmy favorite\b", r"\bi like\b", r"\bi love\b", r"\bi enjoy\b", r"\bi'm into\b"),
        "vehicle": (r"\bi drive\b", r"\bmy car\b", r"\bmy vehicle\b", r"\bi own a\b"),
        "future plans": (r"\bi plan to\b", r"\bi want to\b", r"\bsaving up\b", r"\bmy goal\b", r"\bfuture plan\b"),
        "date of birth": (r"\bmy birthday is\b", r"\bi was born\b", r"\bdate of birth\b"),
        "gender": (r"\bi am male\b", r"\bi am female\b", r"\bi'm male\b", r"\bi'm female\b"),
    }
    for pattern in patterns.get(key_l, ()): 
        if re.search(pattern, history_text_value):
            return True
    return False


def profile_value_is_grounded(value: Any, history_text_value: str) -> bool:
    candidate = str(value or "").strip().lower()
    if not candidate or not history_text_value:
        return False
    normalized = re.sub(r"\s+", " ", candidate)
    if normalized in history_text_value:
        return True

    stopwords = {
        "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at",
        "my", "is", "are", "was", "were", "be", "being", "been",
    }
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) > 2 and token not in stopwords
    ]
    if not tokens:
        return False
    return all(token in history_text_value for token in tokens)


def should_merge_additive_update(key: str, history_text_value: str, existing_value: str, new_value: Any) -> bool:
    if is_strictly_singular_key(key):
        return False
    if not str(existing_value or "").strip():
        return False
    new_text = str(new_value or "").strip()
    if not new_text:
        return False
    if normalize_fact_value(existing_value) == normalize_fact_value(new_text):
        return False
    if history_indicates_addition(history_text_value):
        return True
    if history_indicates_replacement(history_text_value):
        return False
    return False


def merge_fact_values(key: str, existing_value: str, new_value: str) -> str:
    separator = "; " if str(key).strip().lower() in {"occupation", "job"} else ", "
    parts = []
    seen = set()
    for chunk in re.split(r"\s*[;,]\s*", f"{existing_value}{separator}{new_value}"):
        normalized = normalize_fact_value(chunk)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parts.append(chunk.strip())
    return separator.join(parts)


def normalize_fact_value(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^(a|an|the)\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def is_strictly_singular_key(key: str) -> bool:
    return str(key).strip().lower() in {
        "name",
        "date of birth",
        "gender",
        "location",
    }


def history_indicates_addition(history_text_value: str) -> bool:
    additive_patterns = (
        r"\balso\b",
        r"\bas well\b",
        r"\btoo\b",
        r"\bin addition\b",
        r"\banother\b",
        r"\bsecond\b",
        r"\badditional\b",
        r"\bone more\b",
        r"\bextra\b",
        r"\bmore than one\b",
    )
    return any(re.search(pattern, history_text_value) for pattern in additive_patterns)


def history_indicates_replacement(history_text_value: str) -> bool:
    replacement_patterns = (
        r"\bno longer\b",
        r"\bnot anymore\b",
        r"\binstead\b",
        r"\bused to\b",
        r"\bformerly\b",
        r"\bpreviously\b",
        r"\bchanged to\b",
        r"\bchange it to\b",
        r"\bupdate it to\b",
        r"\bcorrection\b",
        r"\bactually\b",
    )
    return any(re.search(pattern, history_text_value) for pattern in replacement_patterns)


def fact_is_grounded(fact: str, history: List[Dict[str, str]]) -> bool:
    text = str(fact or "").strip()
    if not text:
        return False
    if not text.lower().startswith("(u)"):
        return True

    user_text = " ".join(
        str(item.get("content", "")).strip().lower()
        for item in history
        if str(item.get("role", "")).lower() == "user" and item.get("content")
    )
    if not user_text:
        return False

    fact_body = re.sub(r"^\(u\)\s*", "", text.lower()).strip()
    fact_tokens = {
        token
        for token in re.findall(r"[a-z0-9']+", fact_body)
        if len(token) > 2 and token not in _GROUNDING_STOPWORDS
    }
    if not fact_tokens:
        return False

    user_tokens = {
        token
        for token in re.findall(r"[a-z0-9']+", user_text)
        if len(token) > 2 and token not in _GROUNDING_STOPWORDS
    }
    overlap = len(fact_tokens & user_tokens)
    required = 1 if len(fact_tokens) <= 2 else 2
    return overlap >= required
