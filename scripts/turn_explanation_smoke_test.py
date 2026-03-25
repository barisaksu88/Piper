from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from core.routing.route_normalizer import detect_route_interceptor
from core.turn_explanation import (
    activate_last_turn_explanation_snapshot,
    build_last_turn_explanation_message,
    extract_last_turn_explanation_snapshot,
    parse_last_turn_explanation_message,
)


@dataclass(frozen=True)
class TurnExplanationSmokeReport:
    ready: bool
    success: bool
    parse_roundtrip_ok: bool
    explicit_interceptor_ok: bool
    followup_interceptor_ok: bool
    snapshot_recorded_after_turn_one: bool
    snapshot_active_after_explain: bool
    explain_turn_timed_out: bool
    explain_assistant_text: str
    explain_secretary_log_present: bool
    explain_routing_status_present: bool
    stats_record_present: bool
    stats_decision: str
    stats_bypass: str
    data_dir: str
    kept_data_dir: str | None


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run_unit_checks() -> tuple[bool, bool, bool]:
    snapshot = {
        "turn_id": "unit-turn",
        "user_request": "what day is it today?",
        "route_decision": "CHAT",
        "route_source": "bypass:environment_query",
        "outcome": "VERIFIED",
        "outcome_detail": "",
        "phase_ms": {"route": 1.0, "persona": 2.0, "total": 3.0},
        "explain_active": False,
    }
    payload = build_last_turn_explanation_message(snapshot)
    parsed = parse_last_turn_explanation_message(payload)
    parse_roundtrip_ok = bool(parsed == snapshot)

    explicit = detect_route_interceptor(
        "why did you do that?",
        [{"role": "system", "content": payload, "hidden": True}],
    )
    explicit_interceptor_ok = bool(
        explicit
        and str(explicit.get("kind") or "") == "EXPLAIN"
        and str(((explicit.get("route_decision") or {}).get("system_notice") or {}).get("detail_level") or "") == "default"
        and bool(((explicit.get("route_decision") or {}).get("system_notice") or {}).get("available"))
    )

    active_payload = build_last_turn_explanation_message(
        activate_last_turn_explanation_snapshot(snapshot) or {}
    )
    followup = detect_route_interceptor(
        "more detail",
        [{"role": "system", "content": active_payload, "hidden": True}],
    )
    followup_interceptor_ok = bool(
        followup
        and str(followup.get("kind") or "") == "EXPLAIN"
        and str(((followup.get("route_decision") or {}).get("system_notice") or {}).get("detail_level") or "") == "detailed"
    )
    return parse_roundtrip_ok, explicit_interceptor_ok, followup_interceptor_ok


def _clear_harness_state(data_dir: Path) -> None:
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "memory.jsonl").write_text("", encoding="utf-8")
    for path in (
        data_dir / "conversation_summary.json",
        data_dir / "stats.jsonl",
    ):
        if path.exists():
            path.unlink()


def run_smoke(*, timeout: float, keep_data_copy: bool) -> TurnExplanationSmokeReport:
    parse_roundtrip_ok, explicit_interceptor_ok, followup_interceptor_ok = _run_unit_checks()
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_harness_state(harness.data_dir)
    boot = harness.start()
    snapshot_recorded_after_turn_one = False
    snapshot_active_after_explain = False
    explain_assistant_text = ""
    explain_turn_timed_out = True
    explain_secretary_log_present = False
    explain_routing_status_present = False
    stats_payload: dict[str, object] = {}
    try:
        first_turn = harness.send_text("what day is it today?", timeout_s=timeout)
        first_snapshot = extract_last_turn_explanation_snapshot(first_turn.messages)
        snapshot_recorded_after_turn_one = bool(
            first_snapshot
            and str(first_snapshot.get("user_request") or "").strip().lower() == "what day is it today?"
            and str(first_snapshot.get("route_decision") or "").strip().upper() == "CHAT"
            and not bool(first_snapshot.get("explain_active"))
        )

        explain_turn = harness.send_text("why did you do that?", timeout_s=timeout)
        explain_turn_timed_out = bool(explain_turn.timed_out)
        explain_assistant_text = explain_turn.assistant_text
        explain_snapshot = extract_last_turn_explanation_snapshot(
            harness.chat_state.get_messages_snapshot()
        )
        snapshot_active_after_explain = bool(
            explain_snapshot
            and str(explain_snapshot.get("user_request") or "").strip().lower() == "what day is it today?"
            and bool(explain_snapshot.get("explain_active"))
        )
        explain_secretary_log_present = any(
            str(event.get("kind") or "").strip() == "agent_log"
            and "SECRETARY (Router LLM)" in str(event.get("payload") or "")
            for event in explain_turn.ui_events
        )
        explain_routing_status_present = any(
            str(status or "").strip() == "Routing..."
            for status in explain_turn.status_history
        )
        stats_path = Path(harness.data_dir) / "stats.jsonl"
        stats_lines = stats_path.read_text(encoding="utf-8").splitlines() if stats_path.exists() else []
        stats_payload = json.loads(stats_lines[-1]) if stats_lines else {}
    finally:
        harness.close()

    explain_lower = explain_assistant_text.lower()
    explain_text_ok = bool(explain_assistant_text) and any(
        token in explain_lower for token in ("today", "date", "day", "environment", "search", "route")
    )
    stats_record_present = bool(stats_payload)
    success = all(
        (
            boot.ready,
            parse_roundtrip_ok,
            explicit_interceptor_ok,
            followup_interceptor_ok,
            snapshot_recorded_after_turn_one,
            snapshot_active_after_explain,
            not explain_turn_timed_out,
            explain_text_ok,
            not explain_secretary_log_present,
            not explain_routing_status_present,
            stats_record_present,
            str(stats_payload.get("decision") or "").strip() == "CHAT",
            str(stats_payload.get("pre_llm_bypass") or "").strip() == "explain_last_turn",
        )
    )
    return TurnExplanationSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        parse_roundtrip_ok=bool(parse_roundtrip_ok),
        explicit_interceptor_ok=bool(explicit_interceptor_ok),
        followup_interceptor_ok=bool(followup_interceptor_ok),
        snapshot_recorded_after_turn_one=bool(snapshot_recorded_after_turn_one),
        snapshot_active_after_explain=bool(snapshot_active_after_explain),
        explain_turn_timed_out=bool(explain_turn_timed_out),
        explain_assistant_text=explain_assistant_text,
        explain_secretary_log_present=bool(explain_secretary_log_present),
        explain_routing_status_present=bool(explain_routing_status_present),
        stats_record_present=bool(stats_record_present),
        stats_decision=str(stats_payload.get("decision") or ""),
        stats_bypass=str(stats_payload.get("pre_llm_bypass") or ""),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test last-turn explanation routing and persona context.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"EXPLAIN_ASSISTANT: {report.explain_assistant_text}")
        print(f"STATS_DECISION: {report.stats_decision}")
        print(f"STATS_BYPASS: {report.stats_bypass}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
