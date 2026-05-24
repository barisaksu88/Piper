"""Fast deterministic guard tests for core/engines/proactive_monitor.py.

These tests require no LLM, no web search, no real app startup, and no
long-running background loops.  They lock behavior before any future
refactor moves reminder helpers to core/services/.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.engines import proactive_monitor as pm
from core.engines.tail_block_registry import TailBlockContext
from core.contracts import PersonaRuntimePack


# ── 1. ReminderStore storage behavior ────────────────────────────────

class TestReminderStoreLoad:
    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "missing.json")
        assert store.load() == []

    def test_corrupt_json_returns_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.json"
        path.write_text("not json", encoding="utf-8")
        store = pm.ReminderStore(path)
        assert store.load() == []

    def test_non_list_json_returns_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "dict.json"
        path.write_text('{"key": "value"}', encoding="utf-8")
        store = pm.ReminderStore(path)
        assert store.load() == []

    def test_non_dict_items_filtered(self, tmp_path: Path) -> None:
        path = tmp_path / "mixed.json"
        path.write_text(json.dumps([{"ok": True}, "string", 123, None]), encoding="utf-8")
        store = pm.ReminderStore(path)
        loaded = store.load()
        assert loaded == [{"ok": True}]


class TestReminderStoreAdd:
    def test_add_writes_entry_with_expected_fields(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        entry = store.add(message="test msg", fire_at_utc="2026-05-24T12:00:00Z")
        assert "id" in entry
        assert len(str(entry["id"])) > 0
        assert entry["message"] == "test msg"
        assert entry["fire_at"] == "2026-05-24T12:00:00Z"
        assert entry["fired"] is False

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        store.add(message="a", fire_at_utc="2026-01-01T00:00:00Z")
        store.add(message="b", fire_at_utc="2026-01-02T00:00:00Z")
        loaded = store.load()
        assert len(loaded) == 2
        assert loaded[0]["message"] == "a"
        assert loaded[1]["message"] == "b"


class TestReminderStoreDueEntries:
    def test_excludes_already_fired(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        store.add(message="fired", fire_at_utc="2000-01-01T00:00:00Z")
        store.add(message="not fired", fire_at_utc="2000-01-01T00:00:00Z")
        loaded = store.load()
        loaded[0]["fired"] = True
        store.save(loaded)
        now = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        due = store.due_entries(now_utc=now)
        assert len(due) == 1
        assert due[0]["message"] == "not fired"

    def test_excludes_unparseable_fire_at(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        store.add(message="bad", fire_at_utc="not-a-date")
        now = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        due = store.due_entries(now_utc=now)
        assert due == []

    def test_returns_due_reminders_sorted_by_fire_at(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        store.add(message="second", fire_at_utc="2026-05-24T14:00:00Z")
        store.add(message="first", fire_at_utc="2026-05-24T12:00:00Z")
        store.add(message="third", fire_at_utc="2026-05-24T16:00:00Z")
        now = dt.datetime(2026, 5, 24, 18, 0, tzinfo=dt.timezone.utc)
        due = store.due_entries(now_utc=now)
        assert [d["message"] for d in due] == ["first", "second", "third"]


class TestReminderStoreMarkFired:
    def test_returns_true_on_first_mark(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        entry = store.add(message="x", fire_at_utc="2026-01-01T00:00:00Z")
        assert store.mark_fired(str(entry["id"])) is True
        loaded = store.load()
        assert loaded[0]["fired"] is True

    def test_returns_false_on_already_fired(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        entry = store.add(message="x", fire_at_utc="2026-01-01T00:00:00Z")
        store.mark_fired(str(entry["id"]))
        assert store.mark_fired(str(entry["id"])) is False

    def test_returns_false_for_unknown_id(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        assert store.mark_fired("does-not-exist") is False


# ── 2. Reminder parsing behavior ─────────────────────────────────────

NOW_LOCAL = dt.datetime(2026, 5, 26, 12, 0, tzinfo=dt.timezone.utc)


class TestParseReminderRequest:
    def test_relative_time_success(self) -> None:
        parsed = pm.parse_reminder_request(
            "remind me to check the oven in 10 minutes",
            now_local=NOW_LOCAL,
        )
        assert parsed.ok is True
        assert "check the oven" in parsed.message
        assert parsed.fire_at_utc != ""
        assert parsed.fire_at_local != ""
        assert parsed.error == ""

    def test_non_reminder_text_returns_error(self) -> None:
        parsed = pm.parse_reminder_request("what is the weather", now_local=NOW_LOCAL)
        assert parsed.ok is False
        assert parsed.error != ""

    def test_no_subject_returns_error(self) -> None:
        parsed = pm.parse_reminder_request("remind me to in 10 minutes", now_local=NOW_LOCAL)
        assert parsed.ok is False
        assert parsed.error != ""

    def test_date_without_time_returns_error(self) -> None:
        parsed = pm.parse_reminder_request(
            "remind me to call mom tomorrow",
            now_local=NOW_LOCAL,
        )
        assert parsed.ok is False
        assert "time" in parsed.error.lower()

    def test_past_time_returns_error(self) -> None:
        parsed = pm.parse_reminder_request(
            "remind me to check the oven today at 9:00 am",
            now_local=NOW_LOCAL,
        )
        assert parsed.ok is False
        assert "past" in parsed.error.lower()

    def test_no_time_info_returns_error(self) -> None:
        parsed = pm.parse_reminder_request(
            "remind me to check the oven",
            now_local=NOW_LOCAL,
        )
        assert parsed.ok is False
        assert parsed.error != ""


# ── 3. Display and message serialization ─────────────────────────────

class TestDisplayFireAtLocal:
    def test_valid_iso_returns_readable_string(self) -> None:
        result = pm.display_fire_at_local("2026-05-24T14:30:00Z")
        assert result != ""
        assert "2026" in result or "May" in result or "24" in result

    def test_invalid_input_returns_raw_fallback(self) -> None:
        result = pm.display_fire_at_local("not-a-date")
        assert result == "not-a-date"


class TestProactiveTriggerMessageRoundtrip:
    def test_build_then_parse(self) -> None:
        entry = {"id": "abc-123", "fire_at": "2026-05-24T12:00:00Z", "message": "stretch"}
        built = pm.build_proactive_trigger_message(entry)
        parsed = pm.parse_proactive_trigger_message(built)
        assert parsed is not None
        assert parsed["id"] == "abc-123"
        assert parsed["fire_at"] == "2026-05-24T12:00:00Z"
        assert parsed["message"] == "stretch"

    def test_parse_malformed_returns_none(self) -> None:
        assert pm.parse_proactive_trigger_message("not a trigger") is None
        assert pm.parse_proactive_trigger_message("[PROACTIVE_TRIGGER]") is None
        assert pm.parse_proactive_trigger_message("[PROACTIVE_TRIGGER] not json") is None
        assert pm.parse_proactive_trigger_message("[PROACTIVE_TRIGGER] []") is None


class TestBuildProactiveConsumedMessage:
    def test_includes_prefix_and_id(self) -> None:
        entry = {"id": "abc-123"}
        msg = pm.build_proactive_consumed_message(entry)
        assert msg.startswith("[PROACTIVE_TRIGGER CONSUMED]")
        assert "abc-123" in msg

    def test_unknown_when_no_id(self) -> None:
        msg = pm.build_proactive_consumed_message({})
        assert "unknown" in msg


# ── 4. Route interceptor behavior ────────────────────────────────────

class TestRegisteredReminderSetInterceptor:
    def test_parsable_timed_reminder_returns_reminder_set(self) -> None:
        result = pm._registered_reminder_set_interceptor(
            "remind me to stretch in 20 minutes",
            recent_history=[],
        )
        assert result is not None
        assert result["kind"] == "REMINDER_SET"
        assert result["next_stage"] == "REMINDER_SET"
        assert result["bypass"] == "reminder_set"
        assert result["stats_decision"] == "CHAT"

    def test_dated_without_time_returns_task_event(self) -> None:
        result = pm._registered_reminder_set_interceptor(
            "remind me to call mom tomorrow",
            recent_history=[],
        )
        assert result is not None
        assert result["kind"] == "REMINDER_TASK_EVENT"
        assert result["next_stage"] == "MANAGER"
        assert result["stats_decision"] == "TASK"
        route = result["route_decision"]
        assert route["decision"] == "TASK"
        assert route["card"]["stages"][0]["stage_type"] == "TASK_EVENT_WORK"
        assert "ADD_EVENT" in route["card"]["stages"][0]["allowed_tools"]

    def test_undated_without_time_returns_task(self) -> None:
        result = pm._registered_reminder_set_interceptor(
            "remind me to check the oven",
            recent_history=[],
        )
        assert result is not None
        assert result["kind"] == "REMINDER_TASK_EVENT"
        assert result["next_stage"] == "MANAGER"
        assert result["stats_decision"] == "TASK"
        route = result["route_decision"]
        assert route["decision"] == "TASK"
        assert route["card"]["stages"][0]["stage_type"] == "TASK_EVENT_WORK"
        assert "ADD_TASK" in route["card"]["stages"][0]["allowed_tools"]

    def test_non_reminder_returns_none(self) -> None:
        result = pm._registered_reminder_set_interceptor(
            "what is the weather",
            recent_history=[],
        )
        assert result is None


# ── 5. Tail block behavior ───────────────────────────────────────────

def _make_tail_ctx(route: dict[str, Any]) -> TailBlockContext:
    return TailBlockContext(
        route=route,
        runtime=PersonaRuntimePack(),
        ingested_document_chat=False,
        document_focus_active=False,
        reporter_just_ran=False,
        skill={},
    )


class TestTailBlockProactiveTrigger:
    def test_returns_nonempty_for_matching_notice(self) -> None:
        ctx = _make_tail_ctx({
            "system_notice": {
                "kind": "proactive_trigger",
                "message": "test reminder",
                "fire_at_local": "now",
            },
        })
        block = pm._tail_block_proactive_trigger(ctx)
        assert "[PROACTIVE_TRIGGER]" in block
        assert "test reminder" in block
        assert "now" in block

    def test_returns_empty_for_non_matching_notice(self) -> None:
        ctx = _make_tail_ctx({"system_notice": {"kind": "other"}})
        assert pm._tail_block_proactive_trigger(ctx) == ""


class TestTailBlockReminderSetResult:
    def test_scheduled_block(self) -> None:
        ctx = _make_tail_ctx({
            "system_notice": {
                "kind": "reminder_set_result",
                "status": "scheduled",
                "message": "stretch",
                "fire_at_local": "later",
            },
        })
        block = pm._tail_block_reminder_set_result(ctx)
        assert "[REMINDER_SET_RESULT]" in block
        assert "A reminder was written successfully." in block
        assert "stretch" in block
        assert "later" in block

    def test_error_block(self) -> None:
        ctx = _make_tail_ctx({
            "system_notice": {
                "kind": "reminder_set_result",
                "status": "error",
                "error": "bad time",
            },
        })
        block = pm._tail_block_reminder_set_result(ctx)
        assert "[REMINDER_SET_RESULT]" in block
        assert "The reminder was not created." in block
        assert "bad time" in block

    def test_returns_empty_when_no_error_and_not_scheduled(self) -> None:
        ctx = _make_tail_ctx({
            "system_notice": {
                "kind": "reminder_set_result",
                "status": "pending",
            },
        })
        assert pm._tail_block_reminder_set_result(ctx) == ""


# ── 6. Hook finalization behavior ────────────────────────────────────

@dataclass
class _FakeTurnStats:
    persona_error: bool = False


@dataclass
class _FakeChat:
    calls: list[tuple[str, dict[str, Any]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def replace_last_system_message(self, raw_message: str, consumed: dict[str, Any]) -> None:
        self.calls.append((raw_message, consumed))


@dataclass
class _FakeOrc:
    route_decision: dict[str, Any] | None = None
    turn_stats: _FakeTurnStats | None = None
    next_stage: str = ""
    chat: _FakeChat = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.chat is None:
            self.chat = _FakeChat()


class TestHookFinalizeProactiveTrigger:
    def test_marks_fired_and_replaces_message_when_finished(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pm.CFG, "REMINDERS_PATH", tmp_path / "reminders.json")
        store = pm.ReminderStore(pm.CFG.REMINDERS_PATH)
        entry = store.add(message="stretch", fire_at_utc="2026-01-01T00:00:00Z")

        orc = _FakeOrc(
            route_decision={
                "system_notice": {
                    "kind": "proactive_trigger",
                    "id": entry["id"],
                    "raw_message": "[PROACTIVE_TRIGGER] old",
                },
            },
            turn_stats=_FakeTurnStats(persona_error=False),
            next_stage="FINISHED",
        )

        pm._hook_finalize_proactive_trigger(orc, reporter_just_ran=False)

        loaded = store.load()
        assert loaded[0]["fired"] is True
        assert len(orc.chat.calls) == 1
        raw_msg, consumed = orc.chat.calls[0]
        assert raw_msg == "[PROACTIVE_TRIGGER] old"
        assert consumed["role"] == "system"
        assert consumed["hidden"] is True
        assert "[PROACTIVE_TRIGGER CONSUMED]" in consumed["content"]

    def test_noop_when_not_proactive_trigger(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pm.CFG, "REMINDERS_PATH", tmp_path / "reminders.json")
        orc = _FakeOrc(
            route_decision={"system_notice": {"kind": "other"}},
            turn_stats=_FakeTurnStats(persona_error=False),
            next_stage="FINISHED",
        )
        pm._hook_finalize_proactive_trigger(orc, reporter_just_ran=False)
        assert orc.chat.calls == []

    def test_noop_when_persona_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pm.CFG, "REMINDERS_PATH", tmp_path / "reminders.json")
        orc = _FakeOrc(
            route_decision={
                "system_notice": {
                    "kind": "proactive_trigger",
                    "id": "abc",
                    "raw_message": "msg",
                },
            },
            turn_stats=_FakeTurnStats(persona_error=True),
            next_stage="FINISHED",
        )
        pm._hook_finalize_proactive_trigger(orc, reporter_just_ran=False)
        assert orc.chat.calls == []

    def test_noop_when_next_stage_not_finished(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pm.CFG, "REMINDERS_PATH", tmp_path / "reminders.json")
        orc = _FakeOrc(
            route_decision={
                "system_notice": {
                    "kind": "proactive_trigger",
                    "id": "abc",
                    "raw_message": "msg",
                },
            },
            turn_stats=_FakeTurnStats(persona_error=False),
            next_stage="ROUTE",
        )
        pm._hook_finalize_proactive_trigger(orc, reporter_just_ran=False)
        assert orc.chat.calls == []


# ── 7. ProactiveMonitor lifecycle behavior ───────────────────────────

class OneShotStopEvent:
    """Fake stop event that flips to set after one wait() call."""

    def __init__(self) -> None:
        self.wait_calls = 0
        self._set_called = False

    def is_set(self) -> bool:
        return self.wait_calls > 0 or self._set_called

    def set(self) -> None:
        self._set_called = True

    def wait(self, seconds: float) -> bool:
        self.wait_calls += 1
        return True


class TestProactiveMonitorLifecycle:
    def test_start_does_not_double_start(self, tmp_path: Path) -> None:
        monitor = pm.ProactiveMonitor(
            tmp_path / "reminders.json",
            can_dispatch=lambda: False,
            is_inflight=lambda _id: False,
            dispatch_callback=lambda _entry: True,
        )
        monitor.start()
        try:
            first_thread = monitor._thread
            monitor.start()
            assert monitor._thread is first_thread
        finally:
            monitor.stop()

    def test_stop_sets_stop_event_and_joins(self, tmp_path: Path) -> None:
        monitor = pm.ProactiveMonitor(
            tmp_path / "reminders.json",
            can_dispatch=lambda: False,
            is_inflight=lambda _id: False,
            dispatch_callback=lambda _entry: True,
        )
        monitor.start()
        monitor.stop()
        assert monitor._stop_event.is_set() is True

    def test_run_loop_respects_can_dispatch_false(self, tmp_path: Path) -> None:
        monitor = pm.ProactiveMonitor(
            tmp_path / "reminders.json",
            can_dispatch=lambda: False,
            is_inflight=lambda _id: False,
            dispatch_callback=lambda _entry: True,
        )
        monitor._stop_event = OneShotStopEvent()
        monitor._run_loop()
        assert monitor._stop_event.wait_calls == 1

    def test_run_loop_respects_is_inflight_true(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        store.add(message="x", fire_at_utc="2000-01-01T00:00:00Z")

        dispatched: list[dict[str, Any]] = []

        monitor = pm.ProactiveMonitor(
            tmp_path / "reminders.json",
            can_dispatch=lambda: True,
            is_inflight=lambda _id: True,
            dispatch_callback=lambda entry: dispatched.append(entry) or True,
        )
        monitor._stop_event = OneShotStopEvent()
        monitor._run_loop()
        assert dispatched == []

    def test_run_loop_breaks_after_first_successful_dispatch(self, tmp_path: Path) -> None:
        store = pm.ReminderStore(tmp_path / "reminders.json")
        store.add(message="first", fire_at_utc="2000-01-01T00:00:00Z")
        store.add(message="second", fire_at_utc="2000-01-01T00:00:00Z")

        dispatched: list[dict[str, Any]] = []

        monitor = pm.ProactiveMonitor(
            tmp_path / "reminders.json",
            can_dispatch=lambda: True,
            is_inflight=lambda _id: False,
            dispatch_callback=lambda entry: dispatched.append(entry) or True,
        )
        monitor._stop_event = OneShotStopEvent()
        monitor._run_loop()
        assert len(dispatched) == 1
        assert dispatched[0]["message"] == "first"
