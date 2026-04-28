from __future__ import annotations

import datetime
import hashlib
import re
import time
from typing import Any, Dict, List, Optional

PROFILE_REFRESH_EVERY_CALLS = 1
_TRANSIENT_KEY_PREFIXES = ("pending_", "temporary_", "temp_", "current_", "recent_", "latest_")
_TRANSIENT_STATE_TERMS = ("sentiment", "status", "mood", "issue", "hesitation", "concern", "blocker")
_TRANSIENT_CONTEXT_TERMS = (
    "appointment",
    "event",
    "birthday",
    "deadline",
    "meeting",
    "reminder",
    "task",
    "schedule",
    "shift",
    "flight",
    "calendar",
    "plan",
)
_DEFAULT_TRANSIENT_TTL_S = 14 * 86400
_PROFILE_DISCLOSURE_PATTERNS = (
    r"\bmy name is\b",
    r"\bi am(?: also)? an?\b",
    r"\bi'm(?: also)? an?\b",
    r"\bi work as\b",
    r"\bi also work as\b",
    r"\bi work for\b",
    r"\bi fly for\b",
    r"\bi live in\b",
    r"\bi moved to\b",
    r"\bi am from\b",
    r"\bi'm from\b",
    r"\bi am in\b",
    r"\bi'm in\b",
    r"\bmy birthday is\b",
    r"\bi was born\b",
    r"\bmy favorite\b",
    r"\bi like\b",
    r"\bi love\b",
    r"\bi enjoy\b",
    r"\bi'm into\b",
    r"\bi drive\b",
    r"\bi own a\b",
    r"\bmy (daughter|son|girlfriend|boyfriend|wife|husband|partner|mother|mom|father|dad|sister|brother|friend)\b",
    r"\bi have a\b",
)
_WORLD_MODEL_CANDIDATE_PATTERNS = _PROFILE_DISCLOSURE_PATTERNS + (
    r"\bi(?:'m| am)? working on\b",
    r"\bi(?:'m| am)? building\b",
    r"\bmy (project|app|tool) (?:called|named)\b",
)


def history_digest(history: List[Dict[str, str]]) -> str:
    lines = []
    for item in history[-8:]:
        role = str(item.get("role", ""))
        content = str(item.get("content", "")).strip()
        if content:
            lines.append(f"{role}:{content}")
    if not lines:
        return ""
    joined = "\n".join(lines)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def history_text(history: List[Dict[str, str]]) -> str:
    parts = []
    for item in history[-8:]:
        content = str(item.get("content", "")).strip()
        if content:
            parts.append(content.lower())
    return "\n".join(parts)


def history_user_text(history: List[Dict[str, str]]) -> str:
    parts = []
    for item in history[-8:]:
        if str(item.get("role", "")).lower() != "user":
            continue
        content = str(item.get("content", "")).strip()
        if content:
            parts.append(content.lower())
    return "\n".join(parts)


def history_contains_explicit_profile_disclosure(history: List[Dict[str, str]]) -> bool:
    user_messages = [
        str(item.get("content", "")).strip().lower()
        for item in history[-4:]
        if str(item.get("role", "")).lower() == "user" and str(item.get("content", "")).strip()
    ]
    if not user_messages:
        return False
    latest = user_messages[-1]
    return any(re.search(pattern, latest) for pattern in _PROFILE_DISCLOSURE_PATTERNS)


def history_contains_world_model_candidate(history: List[Dict[str, str]]) -> bool:
    user_messages = [
        str(item.get("content", "")).strip().lower()
        for item in history[-6:]
        if str(item.get("role", "")).lower() == "user" and str(item.get("content", "")).strip()
    ]
    if not user_messages:
        return False
    return any(
        re.search(pattern, message)
        for message in user_messages
        for pattern in _WORLD_MODEL_CANDIDATE_PATTERNS
    )


def parse_ttl(ttl_str: str) -> Optional[int]:
    try:
        ttl_str = ttl_str.lower().strip()
        duration = 0
        if "m" in ttl_str:
            duration = int(ttl_str.replace("m", "").strip()) * 60
        elif "h" in ttl_str:
            duration = int(ttl_str.replace("h", "").strip()) * 3600
        elif "d" in ttl_str:
            duration = int(ttl_str.replace("d", "").strip()) * 86400
        else:
            return None
        return int(time.time() + duration)
    except Exception:
        return None


def resolve_fact_expiry(*, key: str, value: Any, ttl_str: Optional[str], history_text: str) -> Optional[int]:
    ttl_clean = str(ttl_str or "").strip().lower()
    transient = fact_should_default_expire(key, value, history_text)
    if ttl_clean and ttl_clean != "forever":
        return parse_ttl(ttl_clean)
    if transient:
        return default_expiry_for_transient_fact(history_text)
    return None


def fact_should_default_expire(key: str, value: Any, history_text: str) -> bool:
    key_l = str(key or "").strip().lower()
    value_l = str(value or "").strip().lower()
    if not key_l:
        return False
    if any(key_l.startswith(prefix) for prefix in _TRANSIENT_KEY_PREFIXES):
        return True

    key_tokens = set(re.findall(r"[a-z0-9]+", key_l))
    value_tokens = set(re.findall(r"[a-z0-9]+", value_l))
    combined_tokens = key_tokens | value_tokens
    has_state_marker = any(term in key_l for term in _TRANSIENT_STATE_TERMS) or bool(
        combined_tokens & set(_TRANSIENT_STATE_TERMS)
    )
    has_context_marker = any(term in key_l for term in _TRANSIENT_CONTEXT_TERMS) or bool(
        combined_tokens & set(_TRANSIENT_CONTEXT_TERMS)
    )
    return has_state_marker and (has_context_marker or history_has_temporal_reference(history_text))


def default_expiry_for_transient_fact(history_text_value: str) -> int:
    referenced = extract_referenced_timestamp(history_text_value)
    if referenced is not None:
        return max(int(time.time()) + 86400, referenced + 2 * 86400)
    return int(time.time()) + _DEFAULT_TRANSIENT_TTL_S


def history_has_temporal_reference(history_text_value: str) -> bool:
    return extract_referenced_timestamp(history_text_value) is not None


def extract_referenced_timestamp(history_text_value: str) -> Optional[int]:
    text = str(history_text_value or "").strip().lower()
    if not text:
        return None

    now = datetime.datetime.now()
    today = now.date()

    if re.search(r"\btomorrow\b", text):
        target = today + datetime.timedelta(days=1)
        return int(datetime.datetime.combine(target, datetime.time(hour=12)).timestamp())
    if re.search(r"\btoday\b", text) or re.search(r"\btonight\b", text):
        return int(datetime.datetime.combine(today, datetime.time(hour=12)).timestamp())

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        try:
            target = datetime.date.fromisoformat(iso_match.group(1))
            return int(datetime.datetime.combine(target, datetime.time(hour=12)).timestamp())
        except ValueError:
            pass

    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    patterns = (
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if match.group(1).isdigit():
            day = int(match.group(1))
            month = month_names[match.group(2)]
        else:
            month = month_names[match.group(1)]
            day = int(match.group(2))
        year = today.year
        try:
            target = datetime.date(year, month, day)
        except ValueError:
            continue
        if target < today:
            try:
                target = datetime.date(year + 1, month, day)
            except ValueError:
                continue
        return int(datetime.datetime.combine(target, datetime.time(hour=12)).timestamp())
    return None
