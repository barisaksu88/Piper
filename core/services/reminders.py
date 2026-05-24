from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.routing.route_dates import extract_date_phrase, resolve_date_phrase
from core.routing.route_patterns import REMINDER_REQUEST_RE


PROACTIVE_TRIGGER_PREFIX = "[PROACTIVE_TRIGGER]"
PROACTIVE_TRIGGER_CONSUMED_PREFIX = "[PROACTIVE_TRIGGER CONSUMED]"

_RELATIVE_OFFSET_RE = re.compile(
    r"(?i)\bin\s+(?P<amount>\d+)\s+(?P<unit>seconds?|minutes?|hours?|days?)\b"
)
_TIME_OF_DAY_RE = re.compile(
    r"(?i)\bat\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?\b"
)
_LEADING_REMINDER_RE = re.compile(
    r"(?i)^.*?\b(remind me to|remember to|set a reminder to|set reminder to|remind me about|set a reminder for|remind me that)\s+"
)


def _local_now(now: dt.datetime | None = None) -> dt.datetime:
    current = now or dt.datetime.now().astimezone()
    if current.tzinfo is None:
        return current.astimezone()
    return current


def _utc_iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _display_local_time(value: dt.datetime) -> str:
    return value.astimezone().strftime("%A, %B %d, %Y at %I:%M %p")


def display_fire_at_local(fire_at_utc: str) -> str:
    raw = str(fire_at_utc or "").strip()
    if not raw:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return _display_local_time(parsed)
    except Exception:
        return raw.replace("T", " ").replace("Z", " UTC")


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}.",
        suffix=f"{path.suffix}.tmp",
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@dataclass(frozen=True)
class ReminderParseResult:
    ok: bool
    message: str = ""
    fire_at_utc: str = ""
    fire_at_local: str = ""
    error: str = ""


class ReminderStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            return _load_json_list(self.path)

    def save(self, entries: list[dict[str, Any]]) -> None:
        with self._lock:
            _atomic_write_json(self.path, list(entries))

    def add(self, *, message: str, fire_at_utc: str) -> dict[str, Any]:
        entry = {
            "id": str(uuid.uuid4()),
            "fire_at": str(fire_at_utc or "").strip(),
            "message": str(message or "").strip(),
            "fired": False,
        }
        entries = self.load()
        entries.append(entry)
        self.save(entries)
        return entry

    def due_entries(self, *, now_utc: dt.datetime | None = None) -> list[dict[str, Any]]:
        current = (now_utc or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        due: list[dict[str, Any]] = []
        for entry in self.load():
            if bool(entry.get("fired")):
                continue
            raw_fire_at = str(entry.get("fire_at") or "").strip()
            if not raw_fire_at:
                continue
            try:
                fire_at = dt.datetime.fromisoformat(raw_fire_at.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
            except Exception:
                continue
            if fire_at <= current:
                due.append(entry)
        due.sort(key=lambda item: str(item.get("fire_at") or ""))
        return due

    def mark_fired(self, reminder_id: str) -> bool:
        clean_id = str(reminder_id or "").strip()
        if not clean_id:
            return False
        entries = self.load()
        changed = False
        for entry in entries:
            if str(entry.get("id") or "").strip() != clean_id:
                continue
            if not bool(entry.get("fired")):
                entry["fired"] = True
                changed = True
            break
        if changed:
            self.save(entries)
        return changed


def _strip_reminder_prefix(text: str) -> str:
    return _LEADING_REMINDER_RE.sub("", str(text or "").strip(), count=1).strip()


def _extract_reminder_subject(text: str) -> str:
    subject = _strip_reminder_prefix(text)
    if not subject:
        return ""
    date_phrase = extract_date_phrase(text)
    if date_phrase:
        subject = re.sub(re.escape(date_phrase), "", subject, flags=re.IGNORECASE).strip(" ,.-")
    subject = _RELATIVE_OFFSET_RE.sub("", subject).strip(" ,.-")
    subject = _TIME_OF_DAY_RE.sub("", subject).strip(" ,.-")
    subject = re.sub(r"\s+", " ", subject)
    return subject.strip("'\" ")


def _parse_relative_fire_time(text: str, *, now_local: dt.datetime) -> dt.datetime | None:
    match = _RELATIVE_OFFSET_RE.search(str(text or ""))
    if not match:
        return None
    amount = int(match.group("amount") or 0)
    unit = str(match.group("unit") or "").lower()
    if amount <= 0:
        return None
    if unit.startswith("second"):
        delta = dt.timedelta(seconds=amount)
    elif unit.startswith("minute"):
        delta = dt.timedelta(minutes=amount)
    elif unit.startswith("hour"):
        delta = dt.timedelta(hours=amount)
    else:
        delta = dt.timedelta(days=amount)
    return (now_local + delta).replace(microsecond=0)


def _parse_time_of_day(text: str) -> tuple[int, int] | None:
    match = _TIME_OF_DAY_RE.search(str(text or ""))
    if not match:
        return None
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    ampm = str(match.group("ampm") or "").lower().replace(".", "")
    if minute < 0 or minute > 59:
        return None
    if ampm:
        if hour < 1 or hour > 12:
            return None
        if ampm.startswith("p") and hour != 12:
            hour += 12
        if ampm.startswith("a") and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return hour, minute


def parse_reminder_request(user_msg: str, *, now_local: dt.datetime | None = None) -> ReminderParseResult:
    text = str(user_msg or "").strip()
    if not text or not REMINDER_REQUEST_RE.search(text):
        return ReminderParseResult(ok=False, error="That does not look like a reminder request.")
    subject = _extract_reminder_subject(text)
    if not subject:
        return ReminderParseResult(ok=False, error="I need the reminder message as well as the time.")

    current_local = _local_now(now_local)
    relative_fire_at = _parse_relative_fire_time(text, now_local=current_local)
    if relative_fire_at is not None:
        reminder_text = f"remind the user to {subject}"
        return ReminderParseResult(
            ok=True,
            message=reminder_text,
            fire_at_utc=_utc_iso(relative_fire_at),
            fire_at_local=_display_local_time(relative_fire_at),
        )

    date_phrase = extract_date_phrase(text)
    resolved_date = resolve_date_phrase(date_phrase) if date_phrase else ""
    time_of_day = _parse_time_of_day(text)
    if resolved_date and time_of_day is None:
        return ReminderParseResult(
            ok=False,
            error="I can set that reminder, but I need a specific time.",
        )
    if resolved_date and time_of_day is not None:
        try:
            target_date = dt.date.fromisoformat(resolved_date)
        except Exception:
            return ReminderParseResult(ok=False, error="I couldn't resolve the reminder date.")
        hour, minute = time_of_day
        fire_local = current_local.replace(
            year=target_date.year,
            month=target_date.month,
            day=target_date.day,
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if fire_local <= current_local:
            return ReminderParseResult(
                ok=False,
                error="That reminder time is already in the past.",
            )
        reminder_text = f"remind the user to {subject}"
        return ReminderParseResult(
            ok=True,
            message=reminder_text,
            fire_at_utc=_utc_iso(fire_local),
            fire_at_local=_display_local_time(fire_local),
        )

    return ReminderParseResult(
        ok=False,
        error="I can set that reminder, but I need to know when it should fire.",
    )


def _build_task_event_fallback_route(user_msg: str) -> dict[str, Any] | None:
    text = str(user_msg or "").strip()
    if not text or not REMINDER_REQUEST_RE.search(text):
        return None
    subject = _extract_reminder_subject(text)
    if not subject:
        return None

    date_phrase = extract_date_phrase(text)
    resolved_date = resolve_date_phrase(date_phrase) if date_phrase else ""
    if resolved_date:
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Add an event for {subject} on {resolved_date}",
                "context": [
                    "The user asked for a dated reminder without a precise fire time.",
                    "Treat dated reminders as events in the task/event system.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Schedule the event '{subject}' for {resolved_date}",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": "Event is created once with the requested date",
                        "allowed_tools": ["ADD_EVENT"],
                        "mutation": {
                            "state_owner": "task_event",
                            "entity_kind": "event",
                            "action": "schedule",
                            "target": subject,
                            "scheduled_date": resolved_date,
                        },
                    }
                ],
            },
        }

    return {
        "decision": "TASK",
        "card": {
            "goal": f"Add a task to {subject}",
            "context": [
                "The user asked for an undated reminder without a precise fire time.",
                "Treat undated reminders as tasks in the task/event system.",
            ],
            "stages": [
                {
                    "stage_goal": f"Create a task to {subject}",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Task is created once with the requested details",
                    "allowed_tools": ["ADD_TASK"],
                    "mutation": {
                        "state_owner": "task_event",
                        "entity_kind": "task",
                        "action": "add",
                        "target": subject,
                    },
                }
            ],
        },
    }


def build_proactive_trigger_message(entry: dict[str, Any]) -> str:
    payload = {
        "id": str(entry.get("id") or "").strip(),
        "fire_at": str(entry.get("fire_at") or "").strip(),
        "message": str(entry.get("message") or "").strip(),
    }
    return PROACTIVE_TRIGGER_PREFIX + "\n" + json.dumps(payload, ensure_ascii=False)


def parse_proactive_trigger_message(content: str) -> dict[str, Any] | None:
    raw = str(content or "").strip()
    if not raw.startswith(PROACTIVE_TRIGGER_PREFIX):
        return None
    payload_text = raw[len(PROACTIVE_TRIGGER_PREFIX):].strip()
    if not payload_text:
        return None
    try:
        payload = json.loads(payload_text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def build_proactive_consumed_message(entry: dict[str, Any]) -> str:
    reminder_id = str(entry.get("id") or "").strip()
    label = reminder_id or "unknown"
    return f"{PROACTIVE_TRIGGER_CONSUMED_PREFIX} {label}"
