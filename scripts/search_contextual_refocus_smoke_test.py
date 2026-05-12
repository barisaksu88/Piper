#!/usr/bin/env python3
"""Live-path smoke for contextual search refocus follow-ups."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness
import tools.search as search_module


INITIAL_TURN = "Search online for recent developments in AI."
REFOCUS_TURN = "actually i was asking more about models"
EXPECTED_REFOCUS_QUERY = "AI models"


@dataclass(frozen=True)
class ContextualRefocusReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    first_query_seen_by_search: str
    refocus_query_seen_by_search: str
    refocus_search_result_event_count: int
    refocus_hidden_summary_present: bool
    initial_timed_out: bool
    refocus_timed_out: bool


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _build_fake_search_result(query: str) -> str:
    clean = str(query or "").strip()
    if clean.lower() == "ai models":
        return (
            "SEARCH SNIPPETS:\n"
            "Title: AI model releases and benchmarks\n"
            "Snippet: Recent AI model coverage compares Gemini, Claude, DeepSeek, and open models.\n\n"
            "--- DEEP DIVE (Full Content) ---\n"
            "Source: https://example.test/ai-models\n"
            "Content: Recent AI model reporting discusses model releases, benchmark comparisons, "
            "multimodal capabilities, and open-weight model competition.\n"
        )
    return (
        "SEARCH SNIPPETS:\n"
        f"Title: Recent developments in AI\n"
        f"Snippet: Recent developments in AI include model releases, agent tooling, and governance.\n\n"
        "--- DEEP DIVE (Full Content) ---\n"
        f"Source: https://example.test/{abs(hash(clean))}\n"
        f"Content: Recent developments in AI include model releases, agent tooling, governance, "
        f"and multimodal systems. Query: {clean}.\n"
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> ContextualRefocusReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    boot = harness.start()
    queries_seen: list[str] = []
    refocus_search_result_event_count = 0
    refocus_hidden_summary_present = False
    initial_timed_out = False
    refocus_timed_out = False
    original = search_module.perform_search

    def _fake_search(query: str, data_dir, log_callback=None, cancel_token=None):
        del data_dir, cancel_token
        clean_query = str(query or "").strip()
        queries_seen.append(clean_query)
        if log_callback:
            log_callback(f"[fake-search] {clean_query}")
        time.sleep(0.2)
        return _build_fake_search_result(clean_query)

    search_module.perform_search = _fake_search
    try:
        initial = harness.send_text(INITIAL_TURN, timeout_s=timeout)
        initial_timed_out = bool(initial.timed_out)

        refocus = harness.send_text(REFOCUS_TURN, timeout_s=timeout)
        refocus_timed_out = bool(refocus.timed_out)
        refocus_search_result_event_count = sum(
            1 for event in refocus.ui_events if str(event.get("kind") or "").strip() == "search_result"
        )
        refocus_hidden_summary_present = any(
            str(message.get("role") or "").strip() == "system"
            and bool(message.get("hidden"))
            and str(message.get("content") or "").startswith("[SEARCH SUMMARY FOR 'AI models']")
            for message in refocus.messages
        )
    finally:
        search_module.perform_search = original
        harness.close()

    first_query = queries_seen[0] if queries_seen else ""
    refocus_query = queries_seen[-1] if len(queries_seen) >= 2 else ""
    success = (
        bool(boot.ready)
        and not initial_timed_out
        and not refocus_timed_out
        and first_query.lower() == "recent developments in ai"
        and refocus_query == EXPECTED_REFOCUS_QUERY
        and REFOCUS_TURN.lower() != refocus_query.lower()
        and refocus_search_result_event_count >= 1
        and refocus_hidden_summary_present
    )
    return ContextualRefocusReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        first_query_seen_by_search=first_query,
        refocus_query_seen_by_search=refocus_query,
        refocus_search_result_event_count=refocus_search_result_event_count,
        refocus_hidden_summary_present=refocus_hidden_summary_present,
        initial_timed_out=initial_timed_out,
        refocus_timed_out=refocus_timed_out,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify contextual search refocuses do not reach search as raw chat.")
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
        print(f"FIRST_QUERY: {report.first_query_seen_by_search}")
        print(f"REFOCUS_QUERY: {report.refocus_query_seen_by_search}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
