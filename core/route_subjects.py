from __future__ import annotations

import re
from typing import Dict, List

from core.contracts import StageCard
from core.route_dates import extract_date_phrase
from core.route_patterns import (
    DATE_HINT_RE,
    DIRECT_EVENT_ASSERTION_RE,
    EVENT_WORD_RE,
    EXISTING_RECORD_HINT_RE,
    SUBJECT_HINT_PATTERNS,
    TASK_CREATE_PATTERNS,
    TASK_FOLLOWUP_HINT_RE,
    TASK_REQUEST_RE,
    VAGUE_EVENT_FOLLOWUP_RE,
)


def extract_event_subject(user_msg: str) -> str:
    text = (user_msg or "").strip().strip(".")
    match = TASK_REQUEST_RE.match(text)
    if match:
        text = match.group(1).strip()
    text = re.sub(r"(?i)^(?:no|actually|fine|okay|ok|well|alright|right)\s*,?\s*", "", text)
    text = re.sub(r"(?i)^i\s+(?:have|had|made|booked|scheduled|set\s+up|got)\s+(?:an?\s+)?", "", text)
    text = re.sub(r"(?i)^there(?:\s+is|'s)\s+(?:an?\s+)?", "", text)
    text = re.sub(r"(?i)^(?:add|create|make|schedule)\s+(?:an?\s+)?event\s+", "", text)
    text = re.sub(r"(?i)^remember\s+to\s+", "", text)
    date_phrase = extract_date_phrase(text)
    if date_phrase:
        text = re.sub(re.escape(date_phrase), "", text, flags=re.IGNORECASE).strip(" ,.-")
    text = re.sub(r"(?i)\b(on|by|for|at)\b(?:\s+the)?\s*$", "", text).strip(" ,.-")
    text = re.sub(r"(?i)\b(?:is|am|are|was|were)\s+off\b", "", text).strip(" ,.-")
    text = re.sub(r"\s+", " ", text)
    if not text or text.lower() in {"no", "actually", "is off", "am off"}:
        return ""
    return text


def looks_like_event_followup(text: str) -> bool:
    lower = (text or "").lower()
    return bool(
        EVENT_WORD_RE.search(text or "")
        or DATE_HINT_RE.search(text or "")
        or "calendar" in lower
        or "event" in lower
    )


def looks_like_task_followup(text: str) -> bool:
    return bool(TASK_FOLLOWUP_HINT_RE.search(text or ""))


def subject_looks_like_event(text: str) -> bool:
    return bool(EVENT_WORD_RE.search(text or "") or DATE_HINT_RE.search(text or ""))


def has_existing_record_context(text: str) -> bool:
    candidate = (text or "").strip()
    if not candidate:
        return False
    return bool(EXISTING_RECORD_HINT_RE.search(candidate))


def extract_task_phrase(user_msg: str) -> str:
    text = (user_msg or "").strip().strip(".")
    match = TASK_REQUEST_RE.match(text)
    if not match:
        return ""
    phrase = match.group(1).strip()
    phrase = re.sub(r"(?i)^remember\s+to\s+", "", phrase)
    phrase = re.sub(r"\s+", " ", phrase)
    return phrase


def extract_task_phrase_from_stage(stage: StageCard) -> str:
    goal = str(stage.get("stage_goal", "")).strip().strip(".")
    for pattern in TASK_CREATE_PATTERNS:
        match = pattern.match(goal)
        if match:
            return re.sub(r"\s+", " ", match.group(1).strip())
    return ""


def looks_like_task_creation(text: str) -> bool:
    candidate = (text or "").strip()
    return any(pattern.match(candidate) for pattern in TASK_CREATE_PATTERNS)


def strip_event_prefix(text: str) -> str:
    out = re.sub(r"(?i)^schedule\s+(?:the\s+)?event\s+", "", text).strip()
    out = re.sub(r"(?i)^create\s+(?:an\s+)?event\s+", "", out).strip()
    out = re.sub(r"(?i)^add\s+(?:an\s+)?event\s+", "", out).strip()
    return out.strip("'\"")


def extract_event_reference_subject(user_msg: str, card: Dict, stages: List[StageCard]) -> str:
    candidates = [
        strip_followup_wrapper(user_msg),
        str(card.get("goal", "")),
    ]
    candidates.extend(str(stage.get("stage_goal", "")) for stage in stages)
    candidates.extend(str(item) for item in card.get("context") or [])

    for candidate in candidates:
        subject = subject_from_text(candidate)
        if subject:
            return subject
    return ""


def extract_reference_subject(user_msg: str, card: Dict, stages: List[StageCard]) -> str:
    candidates = [
        strip_followup_wrapper(user_msg),
        str(card.get("goal", "")),
    ]
    candidates.extend(str(stage.get("stage_goal", "")) for stage in stages)
    candidates.extend(str(item) for item in card.get("context") or [])

    for candidate in candidates:
        subject = subject_from_text(candidate)
        if subject:
            return subject
    return ""


def strip_followup_wrapper(text: str) -> str:
    out = (text or "").strip().strip(".")
    patterns = (
        r"(?i)^i\s+haven't\s+done\s+(?:the\s+)?",
        r"(?i)^i\s+havent\s+done\s+(?:the\s+)?",
        r"(?i)^i\s+have\s+not\s+done\s+(?:the\s+)?",
        r"(?i)^i\s+didn't\s+do\s+(?:the\s+)?",
        r"(?i)^i\s+didnt\s+do\s+(?:the\s+)?",
        r"(?i)^i\s+did\s+not\s+do\s+(?:the\s+)?",
        r"(?i)^i\s+did\s+(?:the\s+)?",
        r"(?i)^i\s+finished\s+(?:the\s+)?",
        r"(?i)^i\s+completed\s+(?:the\s+)?",
        r"(?i)^i\s+handled\s+(?:the\s+)?",
        r"(?i)^i\s+took\s+care\s+of\s+(?:the\s+)?",
        r"(?i)^i\s+went\s+to\s+(?:the\s+)?",
        r"(?i)^i\s+attended\s+(?:the\s+)?",
        r"(?i)^i\s+bought\s+(?:the\s+)?",
        r"(?i)^i\s+got\s+(?:the\s+)?",
        r"(?i)^what\s+about\s+(?:the\s+)?",
        r"(?i)^check\s+(?:the\s+)?",
    )
    for pattern in patterns:
        out = re.sub(pattern, "", out).strip()
    out = re.sub(r"(?i)\bthing\b", "", out).strip(" ,.-")
    return out


def subject_from_text(text: str) -> str:
    candidate = (text or "").strip().strip(".")
    if not candidate:
        return ""

    for pattern in SUBJECT_HINT_PATTERNS:
        match = pattern.search(candidate)
        if match:
            candidate = match.group(1).strip()
            break

    candidate = strip_event_prefix(candidate)
    candidate = re.sub(r"(?i)^user\s+(?:mentioned|said)(?:\s+they)?\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^the\s+user\s+(?:mentioned|said)(?:\s+they)?\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^create\s+(?:a\s+)?task(?:\s+to)?\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^add\s+(?:a\s+)?task(?:\s+to)?\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^complete\s+(?:the\s+)?(?:task|event)\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^mark\s+(?:the\s+)?(?:task|event)\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^the user'?s calendar for\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^upcoming events for\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)^the user'?s task list for\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+as\s+completed(?:\s+for.*)?$", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+for\s+(?:today|tonight|tomorrow|next\b|this\b|\d{4}-\d{2}-\d{2}).*$", "", candidate).strip()
    candidate = re.sub(r"(?i)^the\s+", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+was\s+previously\s+set\s+for.*$", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+event(?:\s+is.*)?$", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+task(?:\s+is.*)?$", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+is\s+properly\s+handled$", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+is\s+scheduled$", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+is\s+completed$", "", candidate).strip()
    candidate = re.sub(r"(?i)\s+is\s+done$", "", candidate).strip()
    candidate = re.sub(r"\s+", " ", candidate)
    return candidate.strip("'\" ")
