from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts import EscalationDecision, RouteDecision, RuntimeSignal


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tail_lines(text: str, *, max_lines: int) -> list[str]:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return lines
    return lines[-max_lines:]


def _latest_stage_entries(scratchpad: list[str]) -> list[str]:
    if not scratchpad:
        return []
    last_start_index = 0
    for index, entry in enumerate(scratchpad):
        text = str(entry or "").lstrip()
        if text.startswith("=== STAGE ") and " START ===" in text:
            last_start_index = index
    return [str(entry or "") for entry in scratchpad[last_start_index:]]


def _normalized_signal(signal: RuntimeSignal) -> RuntimeSignal:
    payload: RuntimeSignal = {
        "kind": str(signal.get("kind", "")).strip().lower() or "unknown",
        "severity": str(signal.get("severity", "warning")).strip().lower() or "warning",
        "source": str(signal.get("source", "")).strip() or "runtime",
        "summary": str(signal.get("summary", "")).strip(),
        "details": str(signal.get("details", "")).strip(),
        "stage_goal": str(signal.get("stage_goal", "")).strip(),
        "stage_type": str(signal.get("stage_type", "")).strip(),
        "tool": str(signal.get("tool", "")).strip(),
        "count": int(signal.get("count", 0) or 0),
        "evidence_files": [str(item).strip() for item in (signal.get("evidence_files") or []) if str(item).strip()],
    }
    step = signal.get("step")
    if isinstance(step, int):
        payload["step"] = step
    return payload


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_codex_support_payload(
    *,
    reason: str,
    summary: str,
    user_msg: str,
    route_decision: RouteDecision | dict[str, Any] | None,
    context_card: dict[str, Any] | None,
    scratchpad: list[str] | None,
    history_tail: list[dict[str, Any]] | None,
    recent_signals: list[RuntimeSignal] | None,
    manual: bool,
    source: str,
    note: str = "",
    trigger_signal: RuntimeSignal | None = None,
    monitor_tail: list[str] | None = None,
    dashboard_tail: list[str] | None = None,
    status_snapshot: str = "",
) -> dict[str, Any]:
    scratchpad_tail = _latest_stage_entries(list(scratchpad or []))[-14:]
    return {
        "timestamp_utc": _utc_timestamp(),
        "manual": bool(manual),
        "source": str(source or "").strip() or "runtime",
        "reason": str(reason or "").strip(),
        "summary": str(summary or "").strip(),
        "note": str(note or "").strip(),
        "status_snapshot": str(status_snapshot or "").strip(),
        "user_msg": str(user_msg or "").strip(),
        "route_decision": route_decision or {},
        "context_card": context_card or {},
        "history_tail": list(history_tail or [])[-8:],
        "recent_signals": list(recent_signals or [])[-8:],
        "trigger_signal": trigger_signal or {},
        "scratchpad_tail": scratchpad_tail,
        "monitor_tail": list(monitor_tail or [])[-20:],
        "dashboard_tail": list(dashboard_tail or [])[-12:],
    }


class EngineeringEscalationDetector:
    def __init__(self, log_path: Path, *, max_signals: int = 80) -> None:
        self.log_path = Path(log_path)
        self.max_signals = max(12, int(max_signals))
        self.signals: list[RuntimeSignal] = []
        self.latest_decision: EscalationDecision | None = None
        self._last_auto_signature = ""

    def record_signal(
        self,
        signal: RuntimeSignal,
        *,
        user_msg: str,
        route_decision: RouteDecision | dict[str, Any] | None,
        context_card: dict[str, Any] | None,
        scratchpad: list[str] | None,
        history_tail: list[dict[str, Any]] | None = None,
    ) -> EscalationDecision | None:
        normalized = _normalized_signal(signal)
        self.signals.append(normalized)
        if len(self.signals) > self.max_signals:
            self.signals = self.signals[-self.max_signals :]
        return self._maybe_auto_escalate(
            normalized,
            user_msg=user_msg,
            route_decision=route_decision,
            context_card=context_card,
            scratchpad=scratchpad,
            history_tail=history_tail,
        )

    def manual_snapshot(
        self,
        *,
        note: str,
        user_msg: str,
        history_tail: list[dict[str, Any]] | None,
        monitor_tail: list[str] | None = None,
        dashboard_tail: list[str] | None = None,
        status_snapshot: str = "",
        route_decision: RouteDecision | dict[str, Any] | None = None,
        context_card: dict[str, Any] | None = None,
        scratchpad: list[str] | None = None,
        source: str = "manual",
    ) -> EscalationDecision:
        summary = "Manual Codex support snapshot requested."
        return self._write_escalation(
            reason="manual_codex_request",
            summary=summary,
            user_msg=user_msg,
            route_decision=route_decision,
            context_card=context_card,
            scratchpad=scratchpad,
            history_tail=history_tail,
            manual=True,
            source=source,
            note=note,
            monitor_tail=monitor_tail,
            dashboard_tail=dashboard_tail,
            status_snapshot=status_snapshot,
        )

    def _recent_count(self, kind: str, *, stage_goal: str = "") -> int:
        target_kind = str(kind or "").strip().lower()
        target_stage = str(stage_goal or "").strip()
        count = 0
        for signal in reversed(self.signals[-12:]):
            if str(signal.get("kind", "")).strip().lower() != target_kind:
                continue
            if target_stage and str(signal.get("stage_goal", "")).strip() != target_stage:
                continue
            count += 1
        return count

    def _maybe_auto_escalate(
        self,
        signal: RuntimeSignal,
        *,
        user_msg: str,
        route_decision: RouteDecision | dict[str, Any] | None,
        context_card: dict[str, Any] | None,
        scratchpad: list[str] | None,
        history_tail: list[dict[str, Any]] | None,
    ) -> EscalationDecision | None:
        kind = str(signal.get("kind", "")).strip().lower()
        stage_goal = str(signal.get("stage_goal", "")).strip()
        severity = str(signal.get("severity", "warning")).strip().lower()
        source = str(signal.get("source", "runtime")).strip() or "runtime"

        summary = ""
        reason = ""
        if severity == "error" and kind in {"planner_error", "persona_error", "route_error", "search_error", "runtime_error"}:
            reason = kind
            summary = str(signal.get("summary", "")).strip() or "A runtime error requires engineering support."
        elif kind == "verification_block" and self._recent_count(kind, stage_goal=stage_goal) >= 2:
            reason = kind
            summary = f"FILE_WORK verification is blocked repeatedly for stage: {stage_goal or 'unknown stage'}."
        elif kind == "planner_repeat" and self._recent_count(kind, stage_goal=stage_goal) >= 2:
            reason = kind
            summary = f"Planner is looping without progress for stage: {stage_goal or 'unknown stage'}."
        elif kind == "file_checker_failed" and self._recent_count(kind, stage_goal=stage_goal) >= 2:
            # Terminal "file not found" failures are not recoverable by retrying —
            # escalating would only confuse the user.  Skip escalation if the checker
            # details indicate the target file simply doesn't exist.
            details_lower = str(signal.get("details", "")).lower()
            _terminal_missing = ("is missing at", "does not exist at", "not found", "was not found", "no such file")
            if not any(phrase in details_lower for phrase in _terminal_missing):
                reason = kind
                summary = f"FILE_CHECKER is failing repeatedly for stage: {stage_goal or 'unknown stage'}."
        elif kind == "mutation_no_effect" and self._recent_count(kind, stage_goal=stage_goal) >= 2:
            reason = kind
            summary = f"Mutating file operations are not changing workspace state for stage: {stage_goal or 'unknown stage'}."

        if not reason:
            return None

        signature = "|".join(part for part in (reason, source, stage_goal, str(signal.get("tool", ""))) if part)
        if signature and signature == self._last_auto_signature:
            return None
        self._last_auto_signature = signature

        return self._write_escalation(
            reason=reason,
            summary=summary,
            user_msg=user_msg,
            route_decision=route_decision,
            context_card=context_card,
            scratchpad=scratchpad,
            history_tail=history_tail,
            manual=False,
            source=source,
            trigger_signal=signal,
        )

    def _write_escalation(
        self,
        *,
        reason: str,
        summary: str,
        user_msg: str,
        route_decision: RouteDecision | dict[str, Any] | None,
        context_card: dict[str, Any] | None,
        scratchpad: list[str] | None,
        history_tail: list[dict[str, Any]] | None,
        manual: bool,
        source: str,
        note: str = "",
        trigger_signal: RuntimeSignal | None = None,
        monitor_tail: list[str] | None = None,
        dashboard_tail: list[str] | None = None,
        status_snapshot: str = "",
    ) -> EscalationDecision:
        payload = build_codex_support_payload(
            reason=reason,
            summary=summary,
            user_msg=user_msg,
            route_decision=route_decision,
            context_card=context_card,
            scratchpad=scratchpad,
            history_tail=history_tail,
            recent_signals=self.signals,
            manual=manual,
            source=source,
            note=note,
            trigger_signal=trigger_signal,
            monitor_tail=monitor_tail,
            dashboard_tail=dashboard_tail,
            status_snapshot=status_snapshot,
        )
        _append_jsonl(self.log_path, payload)
        decision: EscalationDecision = {
            "decision": "ask_codex",
            "reason": reason,
            "summary": summary,
            "brief_path": str(self.log_path),
            "manual": bool(manual),
            "signal_count": len(payload.get("recent_signals") or []),
            "trigger_kind": str((trigger_signal or {}).get("kind", reason) or reason),
        }
        self.latest_decision = decision
        return decision


def build_manual_codex_snapshot(
    *,
    log_path: Path,
    note: str,
    user_msg: str,
    history_tail: list[dict[str, Any]] | None,
    monitor_text: str = "",
    dashboard_text: str = "",
    status_snapshot: str = "",
    route_decision: RouteDecision | dict[str, Any] | None = None,
    context_card: dict[str, Any] | None = None,
    scratchpad: list[str] | None = None,
    recent_signals: list[RuntimeSignal] | None = None,
    source: str = "manual",
) -> EscalationDecision:
    detector = EngineeringEscalationDetector(log_path)
    for signal in recent_signals or []:
        detector.signals.append(_normalized_signal(signal))
    return detector.manual_snapshot(
        note=note,
        user_msg=user_msg,
        history_tail=history_tail,
        monitor_tail=_tail_lines(monitor_text, max_lines=20),
        dashboard_tail=_tail_lines(dashboard_text, max_lines=12),
        status_snapshot=status_snapshot,
        route_decision=route_decision,
        context_card=context_card,
        scratchpad=scratchpad,
        source=source,
    )
