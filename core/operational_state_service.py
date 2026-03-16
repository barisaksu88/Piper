from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import List

from core.routing.route_dates import extract_date_phrase, resolve_date_phrase
from memory.state_owner import SharedStateOwner


@dataclass(frozen=True)
class OperationalSnapshot:
    events: List[dict[str, str]] = field(default_factory=list)
    tasks: List[dict[str, str]] = field(default_factory=list)


class OperationalStateService:
    def __init__(self, state_owner: SharedStateOwner) -> None:
        self.state_owner = state_owner

    @staticmethod
    def _query_tokens(query: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
            if len(token) > 2
        }

    @staticmethod
    def _event_matches_query(event: dict[str, str], *, query: str) -> bool:
        tokens = OperationalStateService._query_tokens(query)
        if not tokens:
            return False
        blob = " ".join(str(value or "").lower() for value in event.values())
        return any(token in blob for token in tokens)

    def snapshot(self, *, query: str = "", horizon_days: int = 45) -> OperationalSnapshot:
        today = dt.datetime.now().date()
        upcoming_events = []
        for item in self.state_owner.event_store.upcoming():
            date_text = str(item.get("date") or "").strip()
            include = True
            try:
                event_date = dt.datetime.strptime(date_text, "%Y-%m-%d").date()
                delta_days = (event_date - today).days
                include = 0 <= delta_days <= max(int(horizon_days), 0)
            except Exception:
                include = True
            if include or self._event_matches_query(item, query=query):
                upcoming_events.append(item)
        return OperationalSnapshot(
            events=upcoming_events,
            tasks=self.state_owner.task_store.as_structured(),
        )

    @staticmethod
    def _extract_event_date_scope(query: str) -> str:
        phrase = extract_date_phrase(str(query or ""))
        if not phrase:
            return ""
        return resolve_date_phrase(phrase)

    @staticmethod
    def _filter_events_by_date(events: List[dict[str, str]], *, target_date: str) -> List[dict[str, str]]:
        if not str(target_date or "").strip():
            return list(events)
        return [
            dict(item)
            for item in events
            if str(item.get("date") or "").strip() == str(target_date).strip()
        ]

    def render_block(self, *, query: str = "", horizon_days: int = 45) -> str:
        snapshot = self.snapshot(query=query, horizon_days=horizon_days)
        payload = {}
        if snapshot.events:
            payload["events"] = snapshot.events
        if snapshot.tasks:
            payload["tasks"] = snapshot.tasks
        if not payload:
            return ""
        return f"[OPERATIONAL STATE]\n{json.dumps(payload, indent=2)}"

    def build_readonly_answer(self, query: str) -> str:
        text = str(query or "").strip()
        if not text:
            return ""
        lower = text.lower()
        wants_tasks = bool(re.search(r"\b(task|tasks|to-?do|to-?dos|pending)\b", lower))
        wants_events = bool(re.search(r"\b(event|events|calendar|schedule|schedules|scheduled)\b", lower))
        wants_event_countdown = bool(
            wants_events
            and (
                re.search(r"\bhow many days\b", lower)
                or re.search(r"\bdays? left\b", lower)
                or re.search(r"\bhow long until\b", lower)
            )
        )
        readonly_query = bool(
            re.search(
                r"\b(?:what|which|show|list|tell me|do i have|what do i have|what's|whats|how many days|days? left|how long until|are there|anything on|anything in|any)\b",
                lower,
            )
        )
        state_assertion = any(
            phrase in lower
            for phrase in (
                "no tasks",
                "no task",
                "no events",
                "no event",
                "no tasks or events",
                "no pending tasks",
                "no upcoming events",
                "there should be",
                "there should still be",
                "should be",
                "should have",
                "still have",
                "still be",
                "supposed to have",
                "there are",
                "there aren't",
                "there are not",
                "i have",
                "i don't have",
                "i dont have",
            )
        )
        if not (wants_tasks or wants_events):
            return ""
        if not readonly_query and not state_assertion and not re.search(r"^(?:show|list)\b", lower):
            return ""

        snapshot = self.snapshot(query=text, horizon_days=3650)
        scoped_events = snapshot.events
        event_date_scope = self._extract_event_date_scope(text) if wants_events else ""
        if event_date_scope:
            scoped_events = self._filter_events_by_date(snapshot.events, target_date=event_date_scope)
        parts: list[str] = []

        if wants_event_countdown and not wants_tasks:
            parts.append(self._render_event_countdown_answer(scoped_events))
        elif wants_tasks and not wants_events:
            parts.append(self._render_task_answer(snapshot.tasks))
        elif wants_events and not wants_tasks:
            parts.append(self._render_event_answer(scoped_events))
        else:
            parts.append(self._render_task_answer(snapshot.tasks))
            parts.append(self._render_event_answer(scoped_events))

        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _render_task_answer(tasks: List[dict[str, str]]) -> str:
        names = [str(item.get("name") or "").strip() for item in tasks if str(item.get("name") or "").strip()]
        if not names:
            return "No pending tasks."
        if len(names) <= 6:
            return "Pending tasks: " + "; ".join(names) + "."
        return f"Pending tasks ({len(names)}): " + "; ".join(names[:6]) + "; ..."

    @staticmethod
    def _render_event_answer(events: List[dict[str, str]]) -> str:
        items = [
            f"{str(item.get('name') or '').strip()} on {str(item.get('date') or '').strip()}"
            for item in events
            if str(item.get("name") or "").strip() and str(item.get("date") or "").strip()
        ]
        if not items:
            return "No upcoming events."
        if len(items) <= 6:
            return "Upcoming events: " + "; ".join(items) + "."
        return f"Upcoming events ({len(items)}): " + "; ".join(items[:6]) + "; ..."

    @staticmethod
    def _render_event_countdown_answer(events: List[dict[str, str]]) -> str:
        if not events:
            return "No upcoming events."

        first = next(
            (
                item
                for item in events
                if str(item.get("name") or "").strip() and str(item.get("date") or "").strip()
            ),
            None,
        )
        if not first:
            return "No upcoming events."

        date_text = str(first.get("date") or "").strip()
        try:
            event_date = dt.datetime.strptime(date_text, "%Y-%m-%d").date()
        except Exception:
            return OperationalStateService._render_event_answer(events)

        delta_days = max((event_date - dt.datetime.now().date()).days, 0)
        day_label = "day" if delta_days == 1 else "days"
        name = str(first.get("name") or "").strip()
        return f"Your first upcoming event is {name} in {delta_days} {day_label}."
