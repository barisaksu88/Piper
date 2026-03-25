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

    def find_references(self, path: str) -> list[dict]:
        """Return active tasks/events whose stored text contains ``path``.

        Used by FileWorkEngine to detect cross-domain dependencies before a
        DELETE or MOVE operation proceeds.  Matches are case-insensitive and
        substring-based so partial path segments (e.g. a filename) will match.

        Both the full relative path and the bare filename (basename) are tested
        so that a task referencing "alpha.txt" is found even when the tool tag
        specifies "docs/alpha.txt".

        Returns a list of conflict dicts, each with a ``kind`` key of
        ``"task"`` or ``"event"`` plus whatever fields the store provides.
        An empty list means the path is safe to delete or move.
        """
        target = str(path or "").strip().lower()
        if not target:
            return []
        # Derive the bare filename as a secondary search term.  Skip when it
        # equals target (already a bare filename) to avoid double-matching.
        import posixpath as _pp
        basename = _pp.basename(target.replace("\\", "/"))
        search_terms = [target]
        if basename and basename != target:
            search_terms.append(basename)

        # Use a wide horizon so long-running tasks/events are included.
        snapshot = self.snapshot(horizon_days=3650)
        refs: list[dict] = []
        for task in snapshot.tasks:
            blob = " ".join(str(v or "").lower() for v in task.values())
            if any(term in blob for term in search_terms):
                refs.append({"kind": "task", **task})
        for event in snapshot.events:
            blob = " ".join(str(v or "").lower() for v in event.values())
            if any(term in blob for term in search_terms):
                refs.append({"kind": "event", **event})
        return refs

    def render_block(self, *, query: str = "", horizon_days: int = 45) -> str:
        snapshot = self.snapshot(query=query, horizon_days=horizon_days)
        # Always emit the block so the model sees an explicit empty state rather
        # than having no operational context at all (which causes it to guess).
        events = list(snapshot.events) if snapshot.events else None
        tasks = list(snapshot.tasks) if snapshot.tasks else None
        lines = ["[OPERATIONAL STATE]"]
        lines.append(f"Tasks: {json.dumps(tasks) if tasks else 'No pending tasks'}")
        lines.append(f"Events: {json.dumps(events) if events else 'No upcoming events'}")
        return "\n".join(lines)

    def build_readonly_answer(self, query: str) -> str:
        text = str(query or "").strip()
        if not text:
            return ""
        lower = text.lower()
        wants_tasks = bool(re.search(r"\b(task|tasks|to-?do|to-?dos|pending)\b", lower))
        wants_events = bool(
            re.search(
                r"\b(event|events|calendar|schedule|schedules|scheduled|appointment|appointments|deadline|deadlines|reminder|reminders)\b",
                lower,
            )
        )
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
                r"\b(?:what|which|when|show|list|tell me|do i have|what do i have|what's|whats|how many days|days? left|how long until|are there|anything on|anything in|any)\b",
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
    def _format_event_label(item: dict[str, str]) -> str:
        name = str(item.get("name") or "").strip()
        date = str(item.get("date") or "").strip()
        time = str(item.get("time") or "").strip()
        if not name or not date:
            return ""
        return f"{name} on {date} at {time}" if time else f"{name} on {date}"

    @staticmethod
    def _render_event_answer(events: List[dict[str, str]]) -> str:
        items = [
            OperationalStateService._format_event_label(item)
            for item in events
            if OperationalStateService._format_event_label(item)
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
        time = str(first.get("time") or "").strip()
        suffix = f" at {time}" if time else ""
        return f"Your first upcoming event is {name} in {delta_days} {day_label}{suffix}."
