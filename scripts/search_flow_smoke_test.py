from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness
from config import data_debug_path
import tools.search as search_module

SEARCH_TURN = "Search the web for Project Halcyon Lantern and tell me what you already know while it loads."
FAKE_SEARCH_DATA = """SEARCH SNIPPETS:
Title: Project Halcyon Lantern launches archival relay tests
Snippet: Project Halcyon Lantern is a fictional research effort building a lunar archive relay for remote science caches.
Title: Halcyon Lantern prototype timeline
Snippet: The first prototype window is currently described as a 2028 field deployment with sapphire relay hardware.

--- DEEP DIVE (Full Content) ---
Source: https://example.test/halcyon-lantern
Content: Project Halcyon Lantern focuses on a lunar archive relay, sapphire relay hardware, and a 2028 prototype window.
"""
DETAIL_TOKENS = ("lunar archive", "sapphire relay", "2028")
BAD_PREVIEW_FOLLOWUPS = (
    "shall i proceed",
    "would you like me to continue",
    "do you want me to continue",
    "want me to continue",
    "once they are available",
    "when they are available",
    "shall i proceed with the search results",
)


@dataclass(frozen=True)
class SearchSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    timed_out: bool
    duration_s: float
    assistant_turn_count: int
    first_assistant_text: str
    final_assistant_text: str
    search_result_event_count: int
    hidden_search_summary_present: bool
    search_report_consumed_present: bool
    preview_prompt_logged: bool
    preview_rule_logged: bool
    preview_auto_continue_rule_logged: bool
    preview_world_state_present: bool
    preview_retrieved_memory_present: bool
    preview_document_matches_present: bool
    preview_operational_state_present: bool
    preview_asks_to_proceed: bool
    reporter_prompt_logged: bool
    reporter_rule_logged: bool
    reporter_world_state_present: bool
    reporter_situational_state_present: bool
    reporter_operational_state_present: bool
    reporter_document_matches_present: bool
    query_seen_by_search: str
    stats_record_count: int
    stats_decision: str
    stats_query: str


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _run_with_fake_search(harness: PiperHarness, *, timeout: float) -> tuple[Any, str]:
    original = search_module.perform_search
    seen_query = ""

    def _fake_search(query: str, data_dir, log_callback=None, cancel_token=None):
        del data_dir, cancel_token
        nonlocal seen_query
        seen_query = str(query or "")
        if log_callback:
            log_callback(f"[fake-search] {seen_query}")
        time.sleep(0.2)
        return FAKE_SEARCH_DATA

    search_module.perform_search = _fake_search
    try:
        return harness.send_text(SEARCH_TURN, timeout_s=timeout), seen_query
    finally:
        search_module.perform_search = original


def _extract_phase_block(debug_text: str, phase_name: str) -> str:
    text = str(debug_text or "")
    marker = f"PHASE: {phase_name}"
    start = text.find(marker)
    if start < 0:
        return ""
    header_divider = text.find("\n============================================================", start + len(marker))
    if header_divider < 0:
        return text[start:]
    content_start = header_divider + len("\n============================================================")
    next_header = text.find("\n============================================================\nTIMESTAMP:", content_start)
    if next_header < 0:
        return text[start:]
    return text[start:next_header]


def _has_block_label(text: str, label: str) -> bool:
    escaped = re.escape(str(label or "").strip())
    if not escaped:
        return False
    return re.search(rf"(?m)^\s*{escaped}\s*$", str(text or "")) is not None


def run_smoke(*, timeout: float, keep_data_copy: bool) -> SearchSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    boot = harness.start()
    seen_query = ""
    result = None
    assistant_messages: list[str] = []
    search_result_event_count = 0
    hidden_search_summary_present = False
    search_report_consumed_present = False
    preview_prompt_logged = False
    preview_rule_logged = False
    preview_auto_continue_rule_logged = False
    preview_world_state_present = False
    preview_retrieved_memory_present = False
    preview_document_matches_present = False
    preview_operational_state_present = False
    preview_asks_to_proceed = False
    reporter_prompt_logged = False
    reporter_rule_logged = False
    reporter_world_state_present = False
    reporter_situational_state_present = False
    reporter_operational_state_present = False
    reporter_document_matches_present = False
    stats_record_count = 0
    stats_decision = ""
    stats_query = ""
    try:
        result, seen_query = _run_with_fake_search(harness, timeout=timeout)
        messages = list(result.messages)
        assistant_messages = [
            str(message.get("content") or "").strip()
            for message in messages
            if str(message.get("role") or "").strip() == "assistant"
        ]
        search_result_events = [
            event for event in result.ui_events
            if str(event.get("kind") or "").strip() == "search_result"
        ]
        search_result_event_count = len(search_result_events)
        hidden_search_summary_present = any(
            str(message.get("role") or "").strip() == "system"
            and bool(message.get("hidden"))
            and str(message.get("content") or "").startswith("[SEARCH SUMMARY FOR '")
            for message in messages
        )
        search_report_consumed_present = any(
            str(message.get("role") or "").strip() == "system"
            and bool(message.get("hidden"))
            and str(message.get("content") or "").startswith("[SEARCH REPORT CONSUMED FOR '")
            for message in messages
        )
        persona_debug = data_debug_path(harness.data_dir, "persona_debug.txt")
        persona_debug_text = persona_debug.read_text(encoding="utf-8") if persona_debug.exists() else ""
        preview_prompt_logged = "PHASE: SEARCH_FIRST_PASS" in persona_debug_text
        preview_rule_logged = _has_block_label(persona_debug_text, "[SEARCH_FIRST_PASS_RULE]")
        preview_block = _extract_phase_block(persona_debug_text, "SEARCH_FIRST_PASS")
        preview_auto_continue_rule_logged = (
            "The runtime will automatically deliver the completed search results on this same turn as soon as the search finishes."
            in preview_block
        )
        preview_world_state_present = _has_block_label(preview_block, "[WORLD STATE]")
        preview_retrieved_memory_present = _has_block_label(preview_block, "[RETRIEVED MEMORY]")
        preview_document_matches_present = _has_block_label(preview_block, "[DOCUMENT MATCHES]")
        preview_operational_state_present = _has_block_label(preview_block, "[OPERATIONAL STATE]")
        reporter_block = _extract_phase_block(persona_debug_text, "PERSONA")
        reporter_prompt_logged = bool(reporter_block)
        reporter_rule_logged = _has_block_label(reporter_block, "[SEARCH_REPORT_RULE]")
        reporter_world_state_present = _has_block_label(reporter_block, "[WORLD STATE]")
        reporter_situational_state_present = _has_block_label(reporter_block, "[SITUATIONAL STATE]")
        reporter_operational_state_present = _has_block_label(reporter_block, "[OPERATIONAL STATE]")
        reporter_document_matches_present = _has_block_label(reporter_block, "[DOCUMENT MATCHES]")
        stats_path = Path(harness.data_dir) / "stats.jsonl"
        stats_records = [
            json.loads(line)
            for line in (stats_path.read_text(encoding="utf-8").splitlines() if stats_path.exists() else [])
            if str(line or "").strip()
        ]
        stats_record_count = len(stats_records)
        if stats_records:
            stats_decision = str(stats_records[-1].get("decision") or "")
            stats_query = str(stats_records[-1].get("search_query") or "")
    finally:
        harness.close()

    first_assistant = assistant_messages[0] if assistant_messages else ""
    final_assistant = assistant_messages[-1] if assistant_messages else ""
    preview_asks_to_proceed = any(
        phrase in first_assistant.lower()
        for phrase in BAD_PREVIEW_FOLLOWUPS
    )
    final_lower = final_assistant.lower()
    timed_out = bool(result.timed_out) if result is not None else True
    duration_s = float(result.duration_s) if result is not None else 0.0
    success = bool(boot.ready) and not timed_out and bool(seen_query.strip()) and (
        "project halcyon lantern" in seen_query.lower()
    ) and len(assistant_messages) >= 2 and bool(first_assistant) and bool(final_assistant) and (
        first_assistant != final_assistant
    ) and search_result_event_count >= 1 and hidden_search_summary_present and search_report_consumed_present and preview_prompt_logged and preview_rule_logged and preview_auto_continue_rule_logged and preview_world_state_present and not preview_document_matches_present and not preview_operational_state_present and not preview_asks_to_proceed and reporter_prompt_logged and reporter_rule_logged and not reporter_world_state_present and not reporter_situational_state_present and not reporter_operational_state_present and not reporter_document_matches_present and stats_record_count == 1 and stats_decision == "SEARCH" and "project halcyon lantern" in stats_query.lower() and any(
        token in final_lower for token in DETAIL_TOKENS
    )
    return SearchSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        timed_out=timed_out,
        duration_s=duration_s,
        assistant_turn_count=len(assistant_messages),
        first_assistant_text=first_assistant,
        final_assistant_text=final_assistant,
        search_result_event_count=search_result_event_count,
        hidden_search_summary_present=hidden_search_summary_present,
        search_report_consumed_present=search_report_consumed_present,
        preview_prompt_logged=preview_prompt_logged,
        preview_rule_logged=preview_rule_logged,
        preview_auto_continue_rule_logged=preview_auto_continue_rule_logged,
        preview_world_state_present=preview_world_state_present,
        preview_retrieved_memory_present=preview_retrieved_memory_present,
        preview_document_matches_present=preview_document_matches_present,
        preview_operational_state_present=preview_operational_state_present,
        preview_asks_to_proceed=preview_asks_to_proceed,
        reporter_prompt_logged=reporter_prompt_logged,
        reporter_rule_logged=reporter_rule_logged,
        reporter_world_state_present=reporter_world_state_present,
        reporter_situational_state_present=reporter_situational_state_present,
        reporter_operational_state_present=reporter_operational_state_present,
        reporter_document_matches_present=reporter_document_matches_present,
        query_seen_by_search=seen_query,
        stats_record_count=stats_record_count,
        stats_decision=stats_decision,
        stats_query=stats_query,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an isolated end-to-end async search flow smoke test through the Piper harness.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-turn timeout in seconds.")
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
        print(f"DATA_DIR: {report.data_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        print(f"ASSISTANT_TURN_COUNT: {report.assistant_turn_count}")
        print(f"SEARCH_RESULT_EVENT_COUNT: {report.search_result_event_count}")
        print(f"PREVIEW_PROMPT_LOGGED: {report.preview_prompt_logged}")
        print(f"PREVIEW_RULE_LOGGED: {report.preview_rule_logged}")
        print(f"QUERY_SEEN_BY_SEARCH: {report.query_seen_by_search}")
        print(f"FIRST_ASSISTANT: {report.first_assistant_text}")
        print(f"FINAL_ASSISTANT: {report.final_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
