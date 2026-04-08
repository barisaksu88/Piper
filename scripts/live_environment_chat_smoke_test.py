from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness


DATE_TURN = "whats todays date"


@dataclass(frozen=True)
class LiveEnvironmentChatReport:
    ready: bool
    success: bool
    timed_out: bool
    duration_s: float
    assistant_turn_count: int
    final_assistant_text: str
    search_event_count: int
    search_summary_present: bool
    search_preview_logged: bool
    routing_status_present: bool
    secretary_log_present: bool
    stats_record_present: bool
    stats_decision: str
    stats_bypass: str


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run_smoke(*, timeout: float) -> LiveEnvironmentChatReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=False)
    boot = harness.start()
    stats_payload: dict = {}
    try:
        result = harness.send_text(DATE_TURN, timeout_s=timeout)
        assistant_messages = [
            str(message.get("content") or "").strip()
            for message in result.messages
            if str(message.get("role") or "").strip() == "assistant"
        ]
        final_assistant = assistant_messages[-1] if assistant_messages else ""
        search_events = [
            event for event in result.ui_events
            if str(event.get("kind") or "").strip() == "search_result"
        ]
        search_summary_present = any(
            str(message.get("role") or "").strip() == "system"
            and str(message.get("content") or "").startswith("[SEARCH SUMMARY FOR '")
            for message in result.messages
        )
        search_preview_logged = any(
            str(message.get("role") or "").strip() == "system"
            and "[SEARCH_FIRST_PASS_RULE]" in str(message.get("content") or "")
            for message in result.messages
        )
        routing_status_present = any(
            str(status or "").strip() == "Routing..."
            for status in result.status_history
        )
        secretary_log_present = any(
            str(event.get("kind") or "").strip() == "agent_log"
            and "SECRETARY (Router LLM)" in str(event.get("payload") or "")
            for event in result.ui_events
        )
        stats_path = Path(harness.data_dir) / "stats.jsonl"
        stats_lines = stats_path.read_text(encoding="utf-8").splitlines() if stats_path.exists() else []
        stats_payload = json.loads(stats_lines[-1]) if stats_lines else {}
    finally:
        harness.close()

    final_lower = final_assistant.lower()
    looks_like_direct_date_answer = bool(
        re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", final_lower)
        or re.search(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b", final_lower)
        or re.search(r"\b20\d{2}\b", final_lower)
    )
    success = (
        bool(boot.ready)
        and not bool(result.timed_out)
        and bool(final_assistant)
        and looks_like_direct_date_answer
        and not search_events
        and not search_summary_present
        and not search_preview_logged
        and not routing_status_present
        and not secretary_log_present
        and bool(stats_payload)
        and str(stats_payload.get("decision") or "").strip() == "CHAT"
        and str(stats_payload.get("pre_llm_bypass") or "").strip() == "environment_query"
    )
    return LiveEnvironmentChatReport(
        ready=bool(boot.ready),
        success=bool(success),
        timed_out=bool(result.timed_out),
        duration_s=float(result.duration_s),
        assistant_turn_count=len(assistant_messages),
        final_assistant_text=final_assistant,
        search_event_count=len(search_events),
        search_summary_present=bool(search_summary_present),
        search_preview_logged=bool(search_preview_logged),
        routing_status_present=bool(routing_status_present),
        secretary_log_present=bool(secretary_log_present),
        stats_record_present=bool(stats_payload),
        stats_decision=str(stats_payload.get("decision") or ""),
        stats_bypass=str(stats_payload.get("pre_llm_bypass") or ""),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that live date/time chat turns answer directly without invoking search.")
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"ASSISTANT_TURN_COUNT: {report.assistant_turn_count}")
        print(f"SEARCH_EVENT_COUNT: {report.search_event_count}")
        print(f"SEARCH_SUMMARY_PRESENT: {report.search_summary_present}")
        print(f"SEARCH_PREVIEW_LOGGED: {report.search_preview_logged}")
        print(f"ROUTING_STATUS_PRESENT: {report.routing_status_present}")
        print(f"SECRETARY_LOG_PRESENT: {report.secretary_log_present}")
        print(f"FINAL_ASSISTANT: {report.final_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
