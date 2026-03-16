from __future__ import annotations

import re
from typing import Any, Sequence

from core.route_subjects import extract_event_subject, extract_reference_subject, extract_task_phrase
from core.runtime_context import extract_latest_runtime_context_fields


_TASK_EVENT_LIST_LINE_RE = re.compile(r"(?im)^(pending tasks|upcoming events):\s*(.+)$")


def parse_listed_subjects(payload: str, *, is_event: bool) -> list[str]:
    text = str(payload or "").strip()
    if not text:
        return []
    lowered = text.lower().strip()
    if lowered in {"no pending tasks.", "no upcoming events."}:
        return []
    if not is_event:
        return [part.strip(" .") for part in re.split(r"[;,]", text) if part.strip(" .")]

    subjects = [
        str(match.group(1) or "").strip(" .")
        for match in re.finditer(r"(.+?)\s+on\s+\d{4}-\d{2}-\d{2}(?:[;,]|$)", text)
    ]
    if subjects:
        return subjects
    return [text.strip(" .")]


def extract_tasks_from_text(text: str) -> list[str]:
    match = re.search(r"(?i)pending tasks:\s*(.+)", str(text or ""))
    if not match:
        return []
    tail = match.group(1).strip()
    tail = tail.split("\n", 1)[0].strip().rstrip(".")
    return [part.strip() for part in re.split(r"\s*;\s*|\s*,\s*", tail) if part.strip()]


def extract_events_from_text(text: str) -> list[str]:
    match = re.search(r"(?i)upcoming events:\s*(.+)", str(text or ""))
    if not match:
        return []
    tail = match.group(1).strip()
    tail = tail.split("\n", 1)[0].strip().rstrip(".")
    names: list[str] = []
    for part in re.split(r"\s*;\s*", tail):
        item = part.strip()
        if not item:
            continue
        event_match = re.match(r"(.+?)\s+on\s+\d{4}-\d{2}-\d{2}\s*$", item)
        names.append((event_match.group(1) if event_match else item).strip())
    return [name for name in names if name]


def extract_recent_visible_targets(
    recent_history: Sequence[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    tasks: list[str] = []
    events: list[str] = []
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"assistant", "system"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if not tasks:
            tasks = extract_tasks_from_text(content)
        if not events:
            events = extract_events_from_text(content)
        if tasks or events:
            break
    return tasks, events


def extract_recent_list_subjects(
    recent_history: Sequence[dict[str, Any]] | None,
    *,
    is_event: bool,
) -> list[str]:
    target_label = "upcoming events" if is_event else "pending tasks"
    for message in reversed(list(recent_history or [])):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in {"assistant", "system"}:
            continue
        content = str(message.get("content") or "")
        for label, payload in reversed(_TASK_EVENT_LIST_LINE_RE.findall(content)):
            if str(label or "").strip().lower() != target_label:
                continue
            parsed = parse_listed_subjects(payload, is_event=is_event)
            if parsed:
                return parsed
    return []


def extract_runtime_followup_subject(
    recent_history: Sequence[dict[str, Any]] | None,
    *,
    is_event: bool,
) -> str:
    runtime = extract_latest_runtime_context_fields(recent_history)
    execution_status = str(runtime.get("execution_status") or "").strip().upper()
    if execution_status:
        if is_event and execution_status.startswith("TASK"):
            return ""
        if not is_event and execution_status.startswith("EVENT"):
            return ""
    runtime_note = str(runtime.get("runtime_note") or "").strip()
    if is_event and runtime_note.startswith("Event scheduled:"):
        subject = runtime_note.partition("Event scheduled:")[2].strip()
        subject = re.sub(r"\s+on\s+\d{4}-\d{2}-\d{2}$", "", subject).strip()
        return subject
    if not is_event and runtime_note.startswith("Task added:"):
        return runtime_note.partition("Task added:")[2].strip()
    last_log = str(runtime.get("last_log") or "").strip()
    if not is_event and last_log.startswith("Pending tasks:"):
        listed = extract_recent_list_subjects(recent_history, is_event=False)
        if len(listed) == 1:
            return listed[0]
    if is_event and last_log.startswith("Upcoming events:"):
        listed = extract_recent_list_subjects(recent_history, is_event=True)
        if len(listed) == 1:
            return listed[0]

    task_goal = str(runtime.get("task_goal") or "").strip()
    if not task_goal:
        return ""
    if is_event:
        return extract_event_subject(task_goal)
    return extract_task_phrase(task_goal) or extract_reference_subject(
        task_goal,
        {"goal": task_goal, "context": []},
        [],
    )


def extract_latest_task_event_candidates(
    recent_history: Sequence[dict[str, Any]] | None,
    *,
    is_event: bool,
) -> list[str]:
    target_label = "upcoming events" if is_event else "pending tasks"
    for message in reversed(list(recent_history or [])):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "")
        if not content:
            continue
        if role == "system" and content.startswith("[LATEST_RUNTIME_CONTEXT]"):
            runtime = extract_latest_runtime_context_fields([message])
            runtime_note = str(runtime.get("last_log") or runtime.get("runtime_note") or "").strip()
            list_prefix = "Upcoming events:" if is_event else "Pending tasks:"
            if runtime_note.startswith(list_prefix):
                parsed = parse_listed_subjects(runtime_note.partition(":")[2], is_event=is_event)
                if parsed:
                    return parsed
            subject = extract_runtime_followup_subject([message], is_event=is_event)
            if subject:
                return [subject]
            continue
        if role not in {"assistant", "system"}:
            continue
        for label, payload in reversed(_TASK_EVENT_LIST_LINE_RE.findall(content)):
            if str(label or "").strip().lower() != target_label:
                continue
            parsed = parse_listed_subjects(payload, is_event=is_event)
            if parsed:
                return parsed
    return []
