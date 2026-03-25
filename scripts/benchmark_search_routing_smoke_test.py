from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness
import tools.search as search_module


SEARCH_TURNS = (
    "search for MLPerf Inference v5.0 benchmark results",
    "search for llama.cpp benchmark results",
)


@dataclass(frozen=True)
class BenchmarkSearchTurnReport:
    query: str
    success: bool
    timed_out: bool
    duration_s: float
    assistant_turn_count: int
    first_assistant_text: str
    final_assistant_text: str
    query_seen_by_search: str
    search_result_event_count: int
    hidden_search_summary_present: bool


@dataclass(frozen=True)
class BenchmarkSearchRoutingReport:
    ready: bool
    success: bool
    turns: list[BenchmarkSearchTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _build_fake_search_result(query: str) -> str:
    clean = str(query or "").strip()
    return (
        "SEARCH SNIPPETS:\n"
        f"Title: {clean}\n"
        f"Snippet: Recent search coverage for {clean}.\n\n"
        "--- DEEP DIVE (Full Content) ---\n"
        f"Source: https://example.test/{abs(hash(clean))}\n"
        f"Content: Recent reporting for {clean} includes benchmark-oriented coverage and current external findings.\n"
    )


def run_smoke(*, timeout: float) -> BenchmarkSearchRoutingReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=False)
    boot = harness.start()
    original = search_module.perform_search
    turns: list[BenchmarkSearchTurnReport] = []
    seen_queries: list[str] = []

    def _fake_search(query: str, data_dir, log_callback=None, cancel_token=None):
        del data_dir, cancel_token
        clean = str(query or "").strip()
        seen_queries.append(clean)
        if log_callback:
            log_callback(f"[fake-search] {clean}")
        time.sleep(0.2)
        return _build_fake_search_result(clean)

    search_module.perform_search = _fake_search
    try:
        for turn in SEARCH_TURNS:
            result = harness.send_text(turn, timeout_s=timeout)
            messages = list(result.messages)
            assistant_messages = [
                str(message.get("content") or "").strip()
                for message in messages
                if str(message.get("role") or "").strip() == "assistant"
            ]
            hidden_search_summary_present = any(
                str(message.get("role") or "").strip() == "system"
                and bool(message.get("hidden"))
                and str(message.get("content") or "").startswith("[SEARCH SUMMARY FOR '")
                for message in messages
            )
            search_result_event_count = sum(
                1 for event in result.ui_events if str(event.get("kind") or "").strip() == "search_result"
            )
            first_assistant = assistant_messages[0] if assistant_messages else ""
            final_assistant = assistant_messages[-1] if assistant_messages else ""
            seen_query = seen_queries[-1] if seen_queries else ""
            final_lower = final_assistant.lower()
            first_lower = first_assistant.lower()
            success = (
                not result.timed_out
                and len(assistant_messages) == 1
                and not bool(seen_query)
                and "no matching files found" not in first_lower
                and "no matching files found" not in final_lower
                and search_result_event_count == 0
                and not hidden_search_summary_present
                and "search the web" in final_lower
                and "workspace files" in final_lower
            )
            turns.append(
                BenchmarkSearchTurnReport(
                    query=turn,
                    success=bool(success),
                    timed_out=bool(result.timed_out),
                    duration_s=float(result.duration_s),
                    assistant_turn_count=len(assistant_messages),
                    first_assistant_text=first_assistant,
                    final_assistant_text=final_assistant,
                    query_seen_by_search=seen_query,
                    search_result_event_count=search_result_event_count,
                    hidden_search_summary_present=hidden_search_summary_present,
                )
            )
    finally:
        search_module.perform_search = original
        harness.close()

    return BenchmarkSearchRoutingReport(
        ready=bool(boot.ready),
        success=bool(boot.ready) and all(turn.success for turn in turns),
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that ambiguous benchmark-style lookup turns ask web-vs-workspace clarification instead of collapsing into FILE_WORK.")
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
        for turn in report.turns:
            print(f"QUERY: {turn.query}")
            print(f"  success={turn.success} timed_out={turn.timed_out} assistant_turn_count={turn.assistant_turn_count}")
            print(f"  first={turn.first_assistant_text}")
            print(f"  final={turn.final_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
