from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Sequence

from config import CFG
from core.engines.tail_block_registry import TailBlockContext, register_tail_block
from core.feature_hooks import register_hook
from core.routing.route_dates import extract_date_phrase, resolve_date_phrase
from core.routing.route_normalizer import register_route_interceptor
from core.routing.route_patterns import REMINDER_REQUEST_RE
from core.services.reminders import (
    ReminderStore,
    _build_task_event_fallback_route,
    _parse_time_of_day,
    finalize_proactive_trigger_turn,
    parse_reminder_request,
)


class ProactiveMonitor:
    def __init__(
        self,
        reminders_path: Path,
        *,
        poll_interval_s: float = 15.0,
        can_dispatch: Callable[[], bool],
        is_inflight: Callable[[str], bool],
        dispatch_callback: Callable[[dict[str, Any]], bool],
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.store = ReminderStore(reminders_path)
        self.poll_interval_s = max(0.05, float(poll_interval_s or 15.0))
        self.can_dispatch = can_dispatch
        self.is_inflight = is_inflight
        self.dispatch_callback = dispatch_callback
        self.log_callback = log_callback or (lambda text: None)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ProactiveMonitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=min(self.poll_interval_s, 1.0))

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.can_dispatch():
                    due = self.store.due_entries()
                    for entry in due:
                        reminder_id = str(entry.get("id") or "").strip()
                        if reminder_id and self.is_inflight(reminder_id):
                            continue
                        if self.dispatch_callback(dict(entry)):
                            break
            except Exception as exc:
                self.log_callback(f"[PROACTIVE MONITOR] {exc}")
            self._stop_event.wait(self.poll_interval_s)


@register_route_interceptor
def _registered_reminder_set_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    del recent_history
    text = str(user_msg or "").strip()
    if not REMINDER_REQUEST_RE.search(text):
        return None
    parsed = parse_reminder_request(text)
    if parsed.ok:
        return {
            "kind": "REMINDER_SET",
            "next_stage": "REMINDER_SET",
            "stats_decision": "CHAT",
            "bypass": "reminder_set",
            "log_message": "   -> Reminder-set interceptor matched. Skipping Secretary/router LLM.",
        }

    date_phrase = extract_date_phrase(text)
    resolved_date = resolve_date_phrase(date_phrase) if date_phrase else ""
    time_of_day = _parse_time_of_day(text)
    if resolved_date and time_of_day is None:
        fallback = _build_task_event_fallback_route(text)
        if fallback is not None:
            return {
                "kind": "REMINDER_TASK_EVENT",
                "next_stage": "MANAGER",
                "stats_decision": "TASK",
                "bypass": "reminder_task_event",
                "log_message": "   -> Dated reminder without a fire time. Routing to task/event event handling.",
                "route_decision": fallback,
            }
    if not resolved_date and time_of_day is None:
        fallback = _build_task_event_fallback_route(text)
        if fallback is not None:
            return {
                "kind": "REMINDER_TASK_EVENT",
                "next_stage": "MANAGER",
                "stats_decision": "TASK",
                "bypass": "reminder_task_event",
                "log_message": "   -> Untimed reminder request. Routing to task/event task handling.",
                "route_decision": fallback,
            }

    return {
        "kind": "REMINDER_SET",
        "next_stage": "REMINDER_SET",
        "stats_decision": "CHAT",
        "bypass": "reminder_set",
        "log_message": "   -> Reminder-set interceptor matched. Skipping Secretary/router LLM.",
    }


@register_tail_block
def _tail_block_proactive_trigger(ctx: TailBlockContext) -> str:
    notice = dict((ctx.route or {}).get("system_notice") or {})
    if str(notice.get("kind") or "").strip().lower() != "proactive_trigger":
        return ""
    message = str(notice.get("message") or "").strip()
    fire_at_local = str(notice.get("fire_at_local") or "").strip()
    lines = [
        "[PROACTIVE_TRIGGER]",
        "A scheduled reminder fired in the background while the user was idle.",
    ]
    if message:
        lines.append(f"Reminder: {message}")
    if fire_at_local:
        lines.append(f"Scheduled for: {fire_at_local}")
    lines.extend(
        [
            "Speak the reminder briefly and naturally.",
            "Do not say the user just messaged you.",
            "Do not ask for confirmation unless the reminder itself requires it.",
            "Do not emit [ROUTER].",
        ]
    )
    return "\n".join(lines)


@register_tail_block
def _tail_block_reminder_set_result(ctx: TailBlockContext) -> str:
    notice = dict((ctx.route or {}).get("system_notice") or {})
    if str(notice.get("kind") or "").strip().lower() != "reminder_set_result":
        return ""
    status = str(notice.get("status") or "").strip().lower()
    message = str(notice.get("message") or "").strip()
    fire_at_local = str(notice.get("fire_at_local") or "").strip()
    error = str(notice.get("error") or "").strip()
    if status == "scheduled":
        lines = [
            "[REMINDER_SET_RESULT]",
            "A reminder was written successfully.",
        ]
        if message:
            lines.append(f"Reminder: {message}")
        if fire_at_local:
            lines.append(f"Fire time: {fire_at_local}")
        lines.extend(
            [
                "Confirm the reminder briefly and directly.",
                "Do not mention internal stores or JSON files.",
                "Do not emit [ROUTER].",
            ]
        )
        return "\n".join(lines)
    if not error:
        return ""
    return "\n".join(
        [
            "[REMINDER_SET_RESULT]",
            "The reminder was not created.",
            f"Reason: {error}",
            "Explain the issue briefly and ask for the missing timing detail if needed.",
            "Do not emit [ROUTER].",
        ]
    )


@register_hook("on_turn_end")
def _hook_finalize_proactive_trigger(orc, *, reporter_just_ran: bool = False) -> None:
    del reporter_just_ran
    route = dict(getattr(orc, "route_decision", {}) or {})
    notice = dict(route.get("system_notice") or {})
    if str(notice.get("kind") or "").strip().lower() != "proactive_trigger":
        return
    if bool(getattr(getattr(orc, "turn_stats", None), "persona_error", False)):
        return
    if str(getattr(orc, "next_stage", "") or "").strip().upper() != "FINISHED":
        return
    finalize_proactive_trigger_turn(
        notice=notice,
        reminders_path=CFG.REMINDERS_PATH,
        chat=orc.chat,
    )
