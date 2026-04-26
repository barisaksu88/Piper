from __future__ import annotations

import re
from pathlib import Path
from typing import Any


EVENT_SPEECH_OFF = "off"
EVENT_SPEECH_IMPORTANT = "important"
EVENT_SPEECH_ALL = "all"
EVENT_SPEECH_NOISY = "noisy"

_EVENT_SPEECH_MODE_ORDER = (
    EVENT_SPEECH_OFF,
    EVENT_SPEECH_IMPORTANT,
    EVENT_SPEECH_ALL,
    EVENT_SPEECH_NOISY,
)

_EVENT_SPEECH_MODE_LABELS = {
    EVENT_SPEECH_OFF: "Events: Off",
    EVENT_SPEECH_IMPORTANT: "Events: Important",
    EVENT_SPEECH_ALL: "Events: All",
    EVENT_SPEECH_NOISY: "Events: Noisy",
}

_MODE_ALIASES = {
    EVENT_SPEECH_OFF: EVENT_SPEECH_OFF,
    EVENT_SPEECH_IMPORTANT: EVENT_SPEECH_IMPORTANT,
    EVENT_SPEECH_ALL: EVENT_SPEECH_ALL,
    EVENT_SPEECH_NOISY: EVENT_SPEECH_NOISY,
    "events off": EVENT_SPEECH_OFF,
    "event speech off": EVENT_SPEECH_OFF,
    "events important": EVENT_SPEECH_IMPORTANT,
    "event speech important": EVENT_SPEECH_IMPORTANT,
    "events all": EVENT_SPEECH_ALL,
    "event speech all": EVENT_SPEECH_ALL,
    "events noisy": EVENT_SPEECH_NOISY,
    "events: noisy": EVENT_SPEECH_NOISY,
    "event speech noisy": EVENT_SPEECH_NOISY,
    "events noisy test": EVENT_SPEECH_NOISY,
    "events: noisy test": EVENT_SPEECH_NOISY,
    "event speech noisy test": EVENT_SPEECH_NOISY,
}
for _mode, _label in _EVENT_SPEECH_MODE_LABELS.items():
    _MODE_ALIASES[_label.lower()] = _mode


def event_speech_mode_options() -> tuple[str, ...]:
    return tuple(_EVENT_SPEECH_MODE_LABELS[mode] for mode in _EVENT_SPEECH_MODE_ORDER)


def normalize_event_speech_mode(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return EVENT_SPEECH_OFF
    return _MODE_ALIASES.get(text, EVENT_SPEECH_OFF)


def event_speech_mode_label(mode: object) -> str:
    normalized = normalize_event_speech_mode(mode)
    return _EVENT_SPEECH_MODE_LABELS[normalized]


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, *, limit: int = 180) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def event_speech_message(kind: object, payload: Any, *, mode: object) -> dict[str, str] | None:
    normalized_mode = normalize_event_speech_mode(mode)
    if normalized_mode == EVENT_SPEECH_OFF:
        return None

    event_kind = str(kind or "").strip()
    event_key = event_kind.lower()
    text = ""
    dedupe_key = event_key

    if event_key == "boot_ready":
        text = "Systems online."
    elif event_key == "error":
        text = _truncate(payload)
        dedupe_key = f"error:{text.lower()}"
    elif event_key == "search_result":
        if normalized_mode in {EVENT_SPEECH_IMPORTANT, EVENT_SPEECH_ALL, EVENT_SPEECH_NOISY}:
            query = ""
            if isinstance(payload, dict):
                query = _clean_text(payload.get("query") or "")
            text = f"Search complete for {query}." if query else "Search complete."
            dedupe_key = f"search:{query.lower() or 'complete'}"
    elif event_key == "code_session_launch":
        if normalized_mode in {EVENT_SPEECH_ALL, EVENT_SPEECH_NOISY}:
            path = ""
            if isinstance(payload, dict):
                path = str(payload.get("path") or "").strip()
            name = Path(path).name if path else "script"
            text = f"Launching {name}."
            dedupe_key = f"launch:{name.lower()}"
    elif event_key == "code_session_status":
        status_text = _truncate(payload)
        low = status_text.lower()
        if normalized_mode == EVENT_SPEECH_IMPORTANT:
            if any(term in low for term in ("failed", "error", "exited", "stopped", "no active process")):
                text = status_text
                dedupe_key = f"code_status:{low}"
        elif normalized_mode in {EVENT_SPEECH_ALL, EVENT_SPEECH_NOISY}:
            text = status_text
            dedupe_key = f"code_status:{low}"
    elif event_key == "status":
        status_text = _truncate(payload)
        low = status_text.lower()
        if normalized_mode == EVENT_SPEECH_IMPORTANT:
            if low in {"error", "canceled", "restarting...", "mic error"} or "error" in low:
                text = status_text
                dedupe_key = f"status:{low}"
        elif normalized_mode in {EVENT_SPEECH_ALL, EVENT_SPEECH_NOISY}:
            if low and low != "idle":
                text = f"Status: {status_text}"
                dedupe_key = f"status:{low}"
    elif event_key == "status_widget_dashboard_activity":
        if normalized_mode in {EVENT_SPEECH_ALL, EVENT_SPEECH_NOISY}:
            activity = _truncate(payload)
            if activity:
                text = activity
                dedupe_key = f"activity:{activity.lower()}"
    elif event_key == "boot_log":
        if normalized_mode == EVENT_SPEECH_NOISY:
            entry = _truncate(payload)
            if entry:
                text = entry
                dedupe_key = f"boot:{entry.lower()}"
    elif event_key == "vision_snapshot_note":
        if normalized_mode == EVENT_SPEECH_NOISY:
            if isinstance(payload, dict):
                note = _truncate(payload.get("text") or "")
            else:
                note = _truncate(payload)
            if note:
                text = note
                dedupe_key = f"vision:{note.lower()}"
    elif event_key == "agent_log":
        if normalized_mode == EVENT_SPEECH_NOISY:
            entry = _truncate(payload)
            if entry:
                text = entry
                dedupe_key = f"log:{entry.lower()}"

    if not text:
        return None
    return {"text": text, "key": dedupe_key}
