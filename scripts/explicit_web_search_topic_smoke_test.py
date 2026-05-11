from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness
import tools.search as search_module


SEARCH_TURN = "search for the latest news on llama.cpp performance benchmarks"
FAKE_SEARCH_DATA = """SEARCH SNIPPETS:
Title: llama.cpp benchmark roundup
Snippet: Recent benchmark discussions compare prompt throughput, decode speed, and GPU offload tradeoffs for llama.cpp builds.
Title: Performance benchmarks for llama.cpp
Snippet: Several recent writeups focus on benchmark methodology, prompt processing throughput, and generation latency.

--- DEEP DIVE (Full Content) ---
Source: https://example.test/llama-cpp-benchmarks
Content: Recent llama.cpp performance benchmark coverage discusses prompt throughput, decode speed, offload settings, and benchmark methodology.
"""


@dataclass(frozen=True)
class ExplicitWebSearchTopicReport:
    ready: bool
    success: bool
    timed_out: bool
    duration_s: float
    assistant_turn_count: int
    tts_utterance_count: int
    first_assistant_text: str
    final_assistant_text: str
    query_seen_by_search: str
    search_result_event_count: int
    hidden_search_summary_present: bool


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run_with_fake_search(harness: PiperHarness, *, timeout: float) -> tuple[object, str]:
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


def run_smoke(*, timeout: float) -> ExplicitWebSearchTopicReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=False)
    boot = harness.start()
    seen_query = ""
    result = None
    assistant_messages: list[str] = []
    tts_utterances: list[dict[str, object]] = []
    search_result_event_count = 0
    hidden_search_summary_present = False
    try:
        result, seen_query = _run_with_fake_search(harness, timeout=timeout)
        messages = list(result.messages)
        assistant_messages = [
            str(message.get("content") or "").strip()
            for message in messages
            if str(message.get("role") or "").strip() == "assistant"
        ]
        tts_utterances = list(result.tts_utterances)
        search_result_event_count = sum(
            1 for event in result.ui_events if str(event.get("kind") or "").strip() == "search_result"
        )
        hidden_search_summary_present = any(
            str(message.get("role") or "").strip() == "system"
            and bool(message.get("hidden"))
            and str(message.get("content") or "").startswith("[SEARCH SUMMARY FOR '")
            for message in messages
        )
    finally:
        harness.close()

    first_assistant = assistant_messages[0] if assistant_messages else ""
    final_assistant = assistant_messages[-1] if assistant_messages else ""
    first_lower = first_assistant.lower()
    final_lower = final_assistant.lower()
    timed_out = bool(result.timed_out) if result is not None else True
    duration_s = float(result.duration_s) if result is not None else 0.0
    success = (
        bool(boot.ready)
        and not timed_out
        and "llama.cpp performance benchmarks" in seen_query.lower()
        and len(assistant_messages) == 2
        and len(tts_utterances) == 2
        and ("web" in first_lower or "search" in first_lower or "checking" in first_lower)
        and "no matching files found" not in final_lower
        and search_result_event_count >= 1
        and hidden_search_summary_present
        and ("benchmark" in final_lower or "throughput" in final_lower or "decode" in final_lower)
    )
    return ExplicitWebSearchTopicReport(
        ready=bool(boot.ready),
        success=bool(success),
        timed_out=timed_out,
        duration_s=duration_s,
        assistant_turn_count=len(assistant_messages),
        tts_utterance_count=len(tts_utterances),
        first_assistant_text=first_assistant,
        final_assistant_text=final_assistant,
        query_seen_by_search=seen_query,
        search_result_event_count=search_result_event_count,
        hidden_search_summary_present=hidden_search_summary_present,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that explicit web-topic searches do not collapse into workspace filename lookup.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-turn timeout in seconds.")
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
        print(f"QUERY_SEEN_BY_SEARCH: {report.query_seen_by_search}")
        print(f"TTS_UTTERANCE_COUNT: {report.tts_utterance_count}")
        print(f"FIRST_ASSISTANT: {report.first_assistant_text}")
        print(f"FINAL_ASSISTANT: {report.final_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
