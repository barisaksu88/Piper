from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from memory.storage import append_jsonl, ensure_parent


_PENDING_SEARCH_ATTR = "_piper_pending_search_turn_stats"
_PENDING_SEARCH_OWNER_ATTR = "_piper_pending_search_turn_stats_owner"
_STARTUP_CHECKED_PATHS: set[str] = set()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _duration_ms(started_at: float | None, ended_at: float | None = None) -> float:
    if started_at is None:
        return 0.0
    finish = time.perf_counter() if ended_at is None else ended_at
    return round(max(0.0, finish - started_at) * 1000.0, 3)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * max(0.0, min(100.0, percentile)) / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return round(ordered[lower], 3)
    weight = rank - lower
    value = ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)
    return round(value, 3)


def _upper_control_bound(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(float(values[0]), 3)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    stddev = math.sqrt(max(0.0, variance))
    return round(mean + (2.0 * stddev), 3)


def _phase_bucket(record: dict[str, Any]) -> dict[str, Any]:
    bucket = dict(record.get("phase_ms") or {})
    bucket["planner_total"] = record.get("planner_total_ms", 0.0)
    bucket["executor_total"] = record.get("executor_total_ms", 0.0)
    bucket["stage_count"] = len(record.get("stages") or [])
    return bucket


@dataclass
class TurnStatsState:
    turn_id: str = field(default_factory=_utc_now_iso)
    timestamp: str = field(default_factory=_utc_now_iso)
    started_at_monotonic: float = field(default_factory=time.perf_counter)
    user_msg: str = ""
    decision: str = ""
    source_scope: str = ""
    confidence: str = ""
    pre_llm_bypass: str = ""
    search_query: str = ""
    router_reroute_fired: bool = False
    latest_route_error: str = ""
    persona_error: bool = False
    outcome: str = ""
    outcome_detail: str = ""
    record_deferred: bool = False
    phase_started_at: dict[str, float] = field(default_factory=dict)
    phase_ms: dict[str, float] = field(
        default_factory=lambda: {
            "route": 0.0,
            "manager": 0.0,
            "reporter": 0.0,
            "persona": 0.0,
            "tts": 0.0,
            "total": 0.0,
        }
    )
    stages: list[dict[str, Any]] = field(default_factory=list)
    planner_total_ms: float = 0.0
    executor_total_ms: float = 0.0
    router_tokens: int | None = None
    persona_tokens: int | None = None

    def finalize(self) -> None:
        self.phase_ms["total"] = _duration_ms(self.started_at_monotonic)

    def to_record(self) -> dict[str, Any]:
        self.finalize()
        return {
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "user_msg": self.user_msg,
            "decision": self.decision or "CHAT",
            "source_scope": self.source_scope or "",
            "confidence": self.confidence or "",
            "pre_llm_bypass": self.pre_llm_bypass or "",
            "search_query": self.search_query or "",
            "router_reroute_fired": bool(self.router_reroute_fired),
            "latest_route_error": self.latest_route_error or "",
            "persona_error": bool(self.persona_error),
            "outcome": self.outcome or "VERIFIED",
            "outcome_detail": self.outcome_detail or "",
            "phase_ms": {key: round(float(value or 0.0), 3) for key, value in self.phase_ms.items()},
            "planner_total_ms": round(float(self.planner_total_ms or 0.0), 3),
            "executor_total_ms": round(float(self.executor_total_ms or 0.0), 3),
            "stages": list(self.stages),
            "llm_tokens": {
                "router": self.router_tokens,
                "persona": self.persona_tokens,
            },
        }


class StatsCollector:
    def __init__(
        self,
        stats_path: Path,
        alerts_path: Path,
        *,
        rolling_window: int = 120,
        history_limit: int = 500,
        min_samples_for_alerts: int = 8,
    ) -> None:
        self.stats_path = Path(stats_path)
        self.alerts_path = Path(alerts_path)
        self.rolling_window = max(10, int(rolling_window or 120))
        self.history_limit = max(20, int(history_limit or 500))
        self.min_samples_for_alerts = max(5, int(min_samples_for_alerts or 8))

    def startup_check_once(self) -> None:
        key = str(self.stats_path.resolve())
        if key in _STARTUP_CHECKED_PATHS:
            return
        _STARTUP_CHECKED_PATHS.add(key)
        self._check_latest_record(reason="startup")

    def resume_or_start_turn(self, cancel_token: Any = None, *, fallback_owner: Any = None) -> TurnStatsState:
        pending = None
        pending_holder = None
        if cancel_token is not None:
            pending = getattr(cancel_token, _PENDING_SEARCH_ATTR, None)
            pending_holder = cancel_token
        if pending is None and fallback_owner is not None:
            pending = getattr(fallback_owner, _PENDING_SEARCH_OWNER_ATTR, None)
            pending_holder = fallback_owner
        if isinstance(pending, TurnStatsState):
            try:
                delattr(pending_holder, _PENDING_SEARCH_ATTR)
            except Exception:
                try:
                    delattr(pending_holder, _PENDING_SEARCH_OWNER_ATTR)
                except Exception:
                    pass
            pending.record_deferred = False
            return pending
        return TurnStatsState()

    def defer_search_turn(self, state: TurnStatsState, cancel_token: Any = None, *, fallback_owner: Any = None) -> None:
        state.record_deferred = True
        if cancel_token is not None:
            setattr(cancel_token, _PENDING_SEARCH_ATTR, state)
            return
        if fallback_owner is not None:
            setattr(fallback_owner, _PENDING_SEARCH_OWNER_ATTR, state)

    def start_phase(self, state: TurnStatsState | None, phase_name: str) -> None:
        if state is None:
            return
        state.phase_started_at[str(phase_name or "").strip().lower()] = time.perf_counter()

    def end_phase(self, state: TurnStatsState | None, phase_name: str) -> float:
        if state is None:
            return 0.0
        key = str(phase_name or "").strip().lower()
        started_at = state.phase_started_at.pop(key, None)
        elapsed_ms = _duration_ms(started_at)
        if key in state.phase_ms:
            state.phase_ms[key] = round(float(state.phase_ms.get(key, 0.0) or 0.0) + elapsed_ms, 3)
        return elapsed_ms

    def note_user_msg(self, state: TurnStatsState | None, user_msg: str) -> None:
        if state is None:
            return
        cleaned = str(user_msg or "").strip()
        if cleaned:
            state.user_msg = cleaned

    def note_route(
        self,
        state: TurnStatsState | None,
        *,
        decision: str = "",
        bypass: str = "",
        source_scope: str = "",
        confidence: str = "",
        search_query: str = "",
        latest_route_error: str = "",
    ) -> None:
        if state is None:
            return
        normalized_decision = str(decision or "").strip().upper()
        if normalized_decision:
            state.decision = normalized_decision
        normalized_bypass = str(bypass or "").strip().lower()
        if normalized_bypass and not state.pre_llm_bypass:
            state.pre_llm_bypass = normalized_bypass
        normalized_scope = str(source_scope or "").strip().lower()
        if normalized_scope:
            state.source_scope = normalized_scope
        normalized_confidence = str(confidence or "").strip().lower()
        if normalized_confidence:
            state.confidence = normalized_confidence
        normalized_query = str(search_query or "").strip()
        if normalized_query:
            state.search_query = normalized_query
        if latest_route_error:
            state.latest_route_error = str(latest_route_error or "").strip()

    def note_reporter_query(self, state: TurnStatsState | None, query: str) -> None:
        if state is None:
            return
        cleaned = str(query or "").strip()
        if cleaned:
            state.search_query = cleaned
            if not state.decision:
                state.decision = "SEARCH"

    def note_router_reroute(self, state: TurnStatsState | None) -> None:
        if state is None:
            return
        state.router_reroute_fired = True

    def note_constraint_violation(self, *, stage_goal: str = "", attempt: int = 1) -> None:
        """Append a constraint schema violation entry to the alerts file.

        Called when a FILE_WORK completion is missing the required `constraints`
        block after the planner has already been given one schema reminder.
        """
        timestamp = _utc_now_iso()
        goal_snippet = str(stage_goal or "").strip()[:80]
        line = (
            f"{timestamp} | constraint_violation | attempt={int(attempt)} | "
            f"stage_goal={goal_snippet!r}"
        )
        ensure_parent(self.alerts_path)
        with self.alerts_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def note_persona_error(self, state: TurnStatsState | None, detail: str = "") -> None:
        if state is None:
            return
        state.persona_error = True
        if detail and not state.outcome_detail:
            state.outcome_detail = str(detail or "").strip()

    def note_tts_metrics(self, state: TurnStatsState | None, metrics: Iterable[dict[str, Any]] | None) -> None:
        if state is None or not metrics:
            return
        total = 0.0
        for metric in metrics:
            total += float(metric.get("tts_ms") or 0.0)
        if total > 0.0:
            state.phase_ms["tts"] = round(float(state.phase_ms.get("tts", 0.0) or 0.0) + total, 3)

    def add_stage(
        self,
        state: TurnStatsState | None,
        *,
        index: int,
        stage: dict[str, Any],
        planner_ms: float,
        executor_ms: float,
        total_ms: float,
        verification: str,
        status: str,
        effective_success: bool,
    ) -> None:
        if state is None:
            return
        state.planner_total_ms = round(float(state.planner_total_ms or 0.0) + float(planner_ms or 0.0), 3)
        state.executor_total_ms = round(float(state.executor_total_ms or 0.0) + float(executor_ms or 0.0), 3)
        state.stages.append(
            {
                "index": int(index),
                "stage_goal": str(stage.get("stage_goal", "") or "").strip(),
                "stage_type": str(stage.get("stage_type", "") or "").strip(),
                "file_stage_kind": str(stage.get("file_stage_kind", "") or "").strip(),
                "planner_ms": round(float(planner_ms or 0.0), 3),
                "executor_ms": round(float(executor_ms or 0.0), 3),
                "total_ms": round(float(total_ms or 0.0), 3),
                "verification": str(verification or "").strip().upper() or "FAILED",
                "status": str(status or "").strip(),
                "effective_success": bool(effective_success),
            }
        )

    def finalize_outcome(
        self,
        state: TurnStatsState | None,
        *,
        outcome: str | None = None,
        detail: str = "",
    ) -> None:
        if state is None:
            return
        if outcome:
            state.outcome = str(outcome or "").strip().upper()
        elif not state.outcome:
            state.outcome = self._infer_outcome(state)
        if detail:
            state.outcome_detail = str(detail or "").strip()

    def record_turn(
        self,
        state: TurnStatsState | None,
        *,
        outcome: str | None = None,
        detail: str = "",
    ) -> dict[str, Any] | None:
        if state is None or state.record_deferred:
            return None
        self.finalize_outcome(state, outcome=outcome, detail=detail)
        record = state.to_record()
        append_jsonl(self.stats_path, record)
        self._check_latest_record(reason="turn")
        return record

    def record_aborted_turn(
        self,
        state: TurnStatsState | None,
        *,
        phase: str = "",
        detail: str = "",
    ) -> dict[str, Any] | None:
        summary = str(detail or "").strip()
        if phase:
            summary = f"{phase}: {summary}".strip(": ")
        return self.record_turn(state, outcome="ABORTED", detail=summary)

    def load_records(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.stats_path.exists():
            return []
        try:
            lines = self.stats_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        if limit is not None and limit > 0:
            lines = lines[-int(limit):]
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def load_alert_lines(self, *, limit: int = 12) -> list[str]:
        if not self.alerts_path.exists():
            return []
        try:
            lines = [line.rstrip() for line in self.alerts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            return []
        return lines[-max(1, int(limit or 12)) :]

    def build_readonly_report(self, *, limit: int | None = None, alert_limit: int = 10) -> str:
        records = self.load_records(limit=limit or self.history_limit)
        alerts = self.load_alert_lines(limit=alert_limit)
        return self._build_readonly_report_from_records(records, alerts)

    def build_dashboard_snapshot(
        self,
        *,
        limit: int | None = None,
        alert_limit: int = 10,
        graph_limit: int = 60,
    ) -> dict[str, Any]:
        records = self.load_records(limit=limit or self.history_limit)
        alerts = self.load_alert_lines(limit=alert_limit)
        summary_text = self._build_readonly_report_from_records(records, alerts)
        if not records:
            return {
                "summary_text": summary_text,
                "alerts": alerts,
                "record_count": 0,
                "graph_window_count": 0,
                "turn_numbers": [],
                "turn_labels": [],
                "total_ms": [],
                "total_upper_ms": [],
                "total_outlier_x": [],
                "total_outlier_y": [],
                "route_ms": [],
                "manager_ms": [],
                "reporter_ms": [],
                "persona_ms": [],
                "tts_ms": [],
                "planner_total_ms": [],
                "executor_total_ms": [],
            }

        recent_records = records[-max(12, int(graph_limit or 60)) :]
        turn_numbers = [float(index + 1) for index in range(len(recent_records))]
        total_values = [self._record_field_value(record, "total") for record in recent_records]
        total_upper = _upper_control_bound(total_values)
        return {
            "summary_text": summary_text,
            "alerts": alerts,
            "record_count": len(records),
            "graph_window_count": len(recent_records),
            "turn_numbers": turn_numbers,
            "turn_labels": [self._short_turn_label(record) for record in recent_records],
            "total_ms": total_values,
            "total_upper_ms": [total_upper for _ in recent_records],
            "total_outlier_x": [
                turn_numbers[index]
                for index, value in enumerate(total_values)
                if total_upper > 0.0 and float(value or 0.0) > total_upper
            ],
            "total_outlier_y": [
                float(value or 0.0)
                for value in total_values
                if total_upper > 0.0 and float(value or 0.0) > total_upper
            ],
            "route_ms": [self._record_field_value(record, "route") for record in recent_records],
            "manager_ms": [self._record_field_value(record, "manager") for record in recent_records],
            "reporter_ms": [self._record_field_value(record, "reporter") for record in recent_records],
            "persona_ms": [self._record_field_value(record, "persona") for record in recent_records],
            "tts_ms": [self._record_field_value(record, "tts") for record in recent_records],
            "planner_total_ms": [self._record_field_value(record, "planner_total") for record in recent_records],
            "executor_total_ms": [self._record_field_value(record, "executor_total") for record in recent_records],
        }

    def _build_readonly_report_from_records(self, records: list[dict[str, Any]], alerts: list[str]) -> str:
        if not records:
            return "No stats recorded yet."
        lines: list[str] = []
        if alerts:
            lines.append("Alerts")
            lines.extend(f"- {line}" for line in alerts)
            lines.append("")

        lines.append("Phase Latency")
        for field in ("route", "manager", "reporter", "persona", "tts", "total", "planner_total", "executor_total"):
            values = self._field_values(records, field)
            if not values:
                continue
            lines.append(
                f"- {field}: avg {round(sum(values) / len(values), 3)} ms | p95 {_percentile(values, 95)} ms"
            )

        lines.append("")
        lines.append("Recent Turns")
        for record in records[-12:]:
            phase_ms = dict(record.get("phase_ms") or {})
            timestamp = str(record.get("timestamp") or "").replace("T", " ")[:23]
            decision = str(record.get("decision") or "CHAT")
            outcome = str(record.get("outcome") or "")
            total_ms = round(float(phase_ms.get("total") or 0.0), 3)
            bypass = str(record.get("pre_llm_bypass") or "").strip()
            reroute = " reroute" if record.get("router_reroute_fired") else ""
            suffix = f" | bypass={bypass}" if bypass else ""
            lines.append(f"- {timestamp} | {decision} | {outcome} | total {total_ms} ms{reroute}{suffix}")
        return "\n".join(lines).strip()

    def _record_field_value(self, record: dict[str, Any], field: str) -> float:
        return round(float(_phase_bucket(record).get(field) or 0.0), 3)

    def _short_turn_label(self, record: dict[str, Any]) -> str:
        timestamp = str(record.get("timestamp") or "").replace("T", " ")
        if len(timestamp) >= 19:
            return timestamp[11:19]
        return timestamp or f"Turn {record.get('turn_id') or '?'}"

    def _field_values(self, records: list[dict[str, Any]], field: str) -> list[float]:
        values: list[float] = []
        for record in records:
            value = _safe_float(_phase_bucket(record).get(field))
            if value is None:
                continue
            values.append(value)
        return values

    def _infer_outcome(self, state: TurnStatsState) -> str:
        if state.persona_error:
            return "FAILED"
        if state.stages:
            verdict = str((state.stages[-1] or {}).get("verification") or "").strip().upper()
            if verdict in {"VERIFIED", "PARTIAL", "FAILED"}:
                return verdict
            if bool((state.stages[-1] or {}).get("effective_success")):
                return "VERIFIED"
            return "FAILED"
        return "VERIFIED"

    def _check_latest_record(self, *, reason: str) -> None:
        records = self.load_records(limit=self.rolling_window + 1)
        if len(records) < self.min_samples_for_alerts + 1:
            return
        current = records[-1]
        history = records[:-1]
        for field in ("route", "manager", "reporter", "persona", "tts", "total", "planner_total", "executor_total"):
            previous_values = self._field_values(history, field)
            if len(previous_values) < self.min_samples_for_alerts:
                continue
            current_value = _safe_float(_phase_bucket(current).get(field))
            if current_value is None:
                continue
            mean = sum(previous_values) / len(previous_values)
            variance = sum((value - mean) ** 2 for value in previous_values) / len(previous_values)
            stddev = math.sqrt(max(0.0, variance))
            if stddev == 0.0:
                outlier = abs(current_value - mean) > 0.0
            else:
                outlier = abs(current_value - mean) > (2.0 * stddev)
            if not outlier:
                continue
            self._append_alert(
                timestamp=str(current.get("timestamp") or _utc_now_iso()),
                field=field,
                value=current_value,
                mean=mean,
                stddev=stddev,
                sample_count=len(previous_values),
                reason=reason,
            )

    def _append_alert(
        self,
        *,
        timestamp: str,
        field: str,
        value: float,
        mean: float,
        stddev: float,
        sample_count: int,
        reason: str,
    ) -> None:
        ensure_parent(self.alerts_path)
        lower = mean - (2.0 * stddev)
        upper = mean + (2.0 * stddev)
        line = (
            f"{timestamp} | {reason} | field={field} | value={round(value, 3)} ms | "
            f"expected={round(lower, 3)}..{round(upper, 3)} ms | mean={round(mean, 3)} | "
            f"std={round(stddev, 3)} | samples={int(sample_count)}"
        )
        with self.alerts_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
