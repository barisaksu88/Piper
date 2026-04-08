from __future__ import annotations

import datetime
import re

from core.routing.route_patterns import DATE_PHRASE_RES


def extract_date_phrase(text: str) -> str:
    text = (text or "").strip()
    for pattern in DATE_PHRASE_RES:
        match = pattern.search(text)
        if match:
            return re.sub(r"\s+", " ", match.group(1).strip())
    return ""


def resolve_date_phrase(text: str) -> str:
    raw = (text or "").strip().lower()
    if not raw:
        return ""

    raw = re.sub(r"\b(on|by)\b", "", raw).strip()
    raw = re.sub(r"(?i)\bat\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\.?", "", raw).strip(" ,.-")
    today = datetime.date.today()

    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass

    raw_compact = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", raw)
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
    month_patterns = (
        r"\b(\d{1,2})\s+of\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})\b",
        r"\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b",
    )
    for pattern in month_patterns:
        match = re.search(pattern, raw_compact)
        if not match:
            continue
        if match.group(1).isdigit():
            day = int(match.group(1))
            month = month_names[match.group(2)]
        else:
            month = month_names[match.group(1)]
            day = int(match.group(2))
        try:
            candidate = datetime.date(today.year, month, day)
        except ValueError:
            continue
        if candidate < today:
            try:
                candidate = datetime.date(today.year + 1, month, day)
            except ValueError:
                continue
            return candidate.strftime("%Y-%m-%d")

    bare_day_match = re.fullmatch(r"(?:the\s+)?(\d{1,2})", raw_compact)
    if bare_day_match:
        day = int(bare_day_match.group(1))
        if 1 <= day <= 31:
            month = today.month
            year = today.year
            while True:
                try:
                    candidate = datetime.date(year, month, day)
                except ValueError:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                    continue
                if candidate < today:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                    continue
                return candidate.strftime("%Y-%m-%d")

    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    if raw.startswith("tomorrow"):
        return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if raw in {"today", "tonight"}:
        return today.strftime("%Y-%m-%d")
    if raw == "next week":
        return (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    if raw == "this week":
        return today.strftime("%Y-%m-%d")
    if raw in weekday_map:
        target = weekday_map[raw]
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7
        return (today + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
    if raw.startswith("next ") and raw[5:] in weekday_map:
        target = weekday_map[raw[5:]]
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7
        return (today + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
    if raw.startswith("this ") and raw[5:] in weekday_map:
        target = weekday_map[raw[5:]]
        delta = (target - today.weekday()) % 7
        return (today + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
    return ""
