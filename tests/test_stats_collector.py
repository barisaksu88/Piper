"""Guard tests for StatsCollector and TurnStatsState.

These tests lock behavior for `StatsCollector` and `TurnStatsState`.
They require no LLM, no web search, no threading, and no external services.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.services.stats_collector import StatsCollector, TurnStatsState


# ── helpers ──────────────────────────────────────────────────────────


def _make_state(*, persona_ms: float = 0.0, total_ms: float = 0.0, outcome: str = "VERIFIED") -> TurnStatsState:
    state = TurnStatsState()
    state.decision = "CHAT"
    state.user_msg = "hello"
    state.outcome = outcome
    if total_ms > 0.0:
        state.started_at_monotonic = time.perf_counter() - (float(total_ms) / 1000.0)
    state.phase_ms["persona"] = float(persona_ms)
    state.phase_ms["total"] = float(total_ms)
    return state


def _record_state(collector: StatsCollector, **kwargs) -> None:
    collector.record_turn(_make_state(**kwargs))


# ── 1. TurnStatsState shape/timing ───────────────────────────────────


class TestTurnStatsState:
    def test_turn_stats_state_to_record_shape(self) -> None:
        state = TurnStatsState()
        state.decision = "TASK"
        state.source_scope = "user"
        state.confidence = "high"
        state.pre_llm_bypass = "explain"
        state.search_query = "weather"
        state.router_reroute_fired = True
        state.latest_route_error = "timeout"
        state.persona_error = False
        state.outcome = "VERIFIED"
        state.outcome_detail = "ok"
        state.router_tokens = 42
        state.persona_tokens = 128
        record = state.to_record()

        assert record["turn_id"] == state.turn_id
        assert record["timestamp"] == state.timestamp
        assert record["user_msg"] == ""
        assert record["decision"] == "TASK"
        assert record["source_scope"] == "user"
        assert record["confidence"] == "high"
        assert record["pre_llm_bypass"] == "explain"
        assert record["search_query"] == "weather"
        assert record["router_reroute_fired"] is True
        assert record["latest_route_error"] == "timeout"
        assert record["persona_error"] is False
        assert record["outcome"] == "VERIFIED"
        assert record["outcome_detail"] == "ok"
        assert isinstance(record["phase_ms"], dict)
        assert set(record["phase_ms"].keys()) == {"route", "manager", "reporter", "persona", "tts", "total"}
        assert record["planner_total_ms"] == 0.0
        assert record["executor_total_ms"] == 0.0
        assert record["stages"] == []
        assert record["llm_tokens"] == {"router": 42, "persona": 128}

    def test_turn_stats_state_finalize_sets_total(self) -> None:
        state = TurnStatsState()
        state.started_at_monotonic = time.perf_counter() - 0.05
        state.finalize()
        assert state.phase_ms["total"] > 0.0


# ── 2. phase timing ──────────────────────────────────────────────────


class TestPhaseTiming:
    def test_start_phase_end_phase_accumulation(self) -> None:
        collector = StatsCollector(Path("/dev/null"), Path("/dev/null"))
        state = TurnStatsState()
        state.phase_started_at["route"] = time.perf_counter() - 0.01
        elapsed = collector.end_phase(state, "route")
        assert elapsed > 0.0
        assert state.phase_ms["route"] > 0.0

        # second accumulation
        state.phase_started_at["route"] = time.perf_counter() - 0.005
        elapsed2 = collector.end_phase(state, "route")
        assert elapsed2 > 0.0
        assert state.phase_ms["route"] > elapsed


# ── 3. record_turn behavior ──────────────────────────────────────────


class TestRecordTurn:
    def test_record_turn_appends_jsonl_and_prunes_history(self, tmp_path: Path) -> None:
        # history_limit is clamped to a minimum of 20 in __init__
        stats_path = tmp_path / "stats.jsonl"
        collector = StatsCollector(stats_path, tmp_path / "alerts.log", history_limit=20)
        for i in range(22):
            state = TurnStatsState()
            state.user_msg = f"msg{i}"
            collector.record_turn(state)
        lines = stats_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 20
        assert json.loads(lines[-1])["user_msg"] == "msg21"

    def test_record_turn_returns_none_when_deferred(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "stats.jsonl", tmp_path / "alerts.log")
        state = TurnStatsState()
        state.record_deferred = True
        assert collector.record_turn(state) is None

    def test_record_aborted_turn_records_aborted(self, tmp_path: Path) -> None:
        stats_path = tmp_path / "stats.jsonl"
        collector = StatsCollector(stats_path, tmp_path / "alerts.log")
        state = TurnStatsState()
        collector.record_aborted_turn(state, phase="route", detail="crash")
        lines = stats_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["outcome"] == "ABORTED"
        assert "crash" in record["outcome_detail"]


# ── 4. loading behavior ──────────────────────────────────────────────


class TestLoading:
    def test_load_records_handles_missing_file(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "missing.jsonl", tmp_path / "alerts.log")
        assert collector.load_records() == []

    def test_load_records_handles_corrupt_lines(self, tmp_path: Path) -> None:
        stats_path = tmp_path / "stats.jsonl"
        stats_path.write_text('{"ok": true}\nnot json\n{"ok": false}\n', encoding="utf-8")
        collector = StatsCollector(stats_path, tmp_path / "alerts.log")
        records = collector.load_records()
        assert len(records) == 2
        assert records[0]["ok"] is True
        assert records[1]["ok"] is False

    def test_load_records_respects_limit(self, tmp_path: Path) -> None:
        stats_path = tmp_path / "stats.jsonl"
        stats_path.write_text('{"n":1}\n{"n":2}\n{"n":3}\n', encoding="utf-8")
        collector = StatsCollector(stats_path, tmp_path / "alerts.log")
        records = collector.load_records(limit=2)
        assert len(records) == 2
        assert records[-1]["n"] == 3

    def test_load_alert_lines_handles_missing_file(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "stats.jsonl", tmp_path / "missing_alerts.log")
        assert collector.load_alert_lines() == []

    def test_load_alert_lines_respects_limit(self, tmp_path: Path) -> None:
        alerts_path = tmp_path / "alerts.log"
        alerts_path.write_text("a\nb\nc\nd\n", encoding="utf-8")
        collector = StatsCollector(tmp_path / "stats.jsonl", alerts_path)
        lines = collector.load_alert_lines(limit=2)
        assert len(lines) == 2
        assert lines[-1] == "d"


# ── 5. dashboard/report behavior ─────────────────────────────────────


class TestDashboardReport:
    def test_build_dashboard_snapshot_empty(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "stats.jsonl", tmp_path / "alerts.log")
        snapshot = collector.build_dashboard_snapshot()
        assert snapshot["record_count"] == 0
        assert snapshot["graph_window_count"] == 0
        assert snapshot["turn_numbers"] == []
        assert snapshot["total_ms"] == []
        assert snapshot["total_outlier_x"] == []
        assert snapshot["total_outlier_y"] == []

    def test_build_dashboard_snapshot_non_empty_shape(self, tmp_path: Path) -> None:
        stats_path = tmp_path / "stats.jsonl"
        collector = StatsCollector(stats_path, tmp_path / "alerts.log")
        for _ in range(5):
            _record_state(collector, persona_ms=100.0, total_ms=200.0)
        snapshot = collector.build_dashboard_snapshot(graph_limit=10)
        assert snapshot["record_count"] == 5
        assert snapshot["graph_window_count"] == 5
        assert len(snapshot["turn_numbers"]) == 5
        assert len(snapshot["total_ms"]) == 5
        assert len(snapshot["route_ms"]) == 5
        assert len(snapshot["persona_ms"]) == 5

    def test_build_readonly_report_includes_phase_latency_and_recent_turns(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "stats.jsonl", tmp_path / "alerts.log")
        _record_state(collector, persona_ms=100.0, total_ms=200.0)
        report = collector.build_readonly_report()
        assert "Phase Latency" in report
        assert "Recent Turns" in report


# ── 6. alert behavior ────────────────────────────────────────────────


class TestAlerts:
    def test_alert_creation_when_latency_outlier_detected(self, tmp_path: Path) -> None:
        # min_samples_for_alerts is clamped to a minimum of 5 in __init__
        stats_path = tmp_path / "stats.jsonl"
        alerts_path = tmp_path / "alerts.log"
        collector = StatsCollector(stats_path, alerts_path, rolling_window=12, min_samples_for_alerts=5)
        for _ in range(5):
            _record_state(collector, persona_ms=100.0, total_ms=200.0)
        _record_state(collector, persona_ms=1000.0, total_ms=1100.0)
        alert_lines = collector.load_alert_lines(limit=20)
        assert any("field=persona" in line for line in alert_lines)
        assert any("samples=" in line for line in alert_lines)

    def test_note_constraint_violation_appends_alert(self, tmp_path: Path) -> None:
        alerts_path = tmp_path / "alerts.log"
        collector = StatsCollector(tmp_path / "stats.jsonl", alerts_path)
        collector.note_constraint_violation(stage_goal="Edit main.py", attempt=2)
        lines = collector.load_alert_lines()
        assert len(lines) == 1
        assert "constraint_violation" in lines[0]
        assert "Edit main.py" in lines[0]
        assert "attempt=2" in lines[0]


# ── 7. deferred search state ─────────────────────────────────────────


class TestDeferredSearch:
    def test_defer_search_turn_and_resume_or_start_turn(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "stats.jsonl", tmp_path / "alerts.log")
        state = TurnStatsState()
        state.user_msg = "deferred"
        token = SimpleNamespace()
        collector.defer_search_turn(state, cancel_token=token)
        resumed = collector.resume_or_start_turn(cancel_token=token)
        assert resumed.user_msg == "deferred"
        assert not hasattr(token, "_piper_pending_search_turn_stats")

    def test_resume_or_start_turn_returns_new_state_when_no_pending(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "stats.jsonl", tmp_path / "alerts.log")
        state = collector.resume_or_start_turn()
        assert isinstance(state, TurnStatsState)
        assert state.user_msg == ""


# ── 8. route/outcome helpers ─────────────────────────────────────────


class TestRouteOutcome:
    def test_note_route_sets_decision_and_bypass(self) -> None:
        collector = StatsCollector(Path("/dev/null"), Path("/dev/null"))
        state = TurnStatsState()
        collector.note_route(state, decision="task", bypass="Explain", source_scope="USER", confidence="HIGH")
        assert state.decision == "TASK"
        assert state.pre_llm_bypass == "explain"
        assert state.source_scope == "user"
        assert state.confidence == "high"

    def test_infer_outcome_from_stages(self, tmp_path: Path) -> None:
        collector = StatsCollector(tmp_path / "stats.jsonl", tmp_path / "alerts.log")

        # timeout
        state = TurnStatsState()
        state.stages.append({"timeout_hit": True, "verification": "VERIFIED", "effective_success": True})
        collector.finalize_outcome(state)
        assert state.outcome == "TIMEOUT"

        # verified
        state = TurnStatsState()
        state.stages.append({"timeout_hit": False, "verification": "VERIFIED", "effective_success": True})
        collector.finalize_outcome(state)
        assert state.outcome == "VERIFIED"

        # failed
        state = TurnStatsState()
        state.stages.append({"timeout_hit": False, "verification": "FAILED", "effective_success": False})
        collector.finalize_outcome(state)
        assert state.outcome == "FAILED"

        # persona error overrides
        state = TurnStatsState()
        state.persona_error = True
        state.stages.append({"verification": "VERIFIED"})
        collector.finalize_outcome(state)
        assert state.outcome == "FAILED"

        # no stages
        state = TurnStatsState()
        collector.finalize_outcome(state)
        assert state.outcome == "VERIFIED"


# ── 9. startup dedup ─────────────────────────────────────────────────


class TestStartup:
    def test_startup_check_once_deduplicates_by_path(self, tmp_path: Path) -> None:
        from core.services import stats_collector as sc_module

        stats_path = tmp_path / "stats.jsonl"
        alerts_path = tmp_path / "alerts.log"
        collector = StatsCollector(stats_path, alerts_path)

        call_count = 0
        original = collector._check_latest_record

        def _counting_check(*, reason: str) -> None:
            nonlocal call_count
            call_count += 1
            original(reason=reason)

        collector._check_latest_record = _counting_check

        collector.startup_check_once()
        collector.startup_check_once()

        assert call_count == 1

        # clean up so other tests are not affected
        resolved = str(stats_path.resolve())
        sc_module._STARTUP_CHECKED_PATHS.discard(resolved)
