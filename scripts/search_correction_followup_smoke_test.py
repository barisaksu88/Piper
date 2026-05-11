from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
import tools.search as search_module


FIRST_TURN = "Research on the"
SECOND_TURN = "It got cut off, I meant research on the latest AI news."
AI_DATA = """VERDICT: verified
ANSWER:
Recent AI news coverage focuses on model releases, benchmark updates, and major product launches.

Supporting evidence:
1. Prompt throughput and decode benchmarks remain a major comparison point.
   — AI News Roundup
"""
GRAMMAR_DATA = """VERDICT: verified
ANSWER:
Both 'research on' and 'research in' are correct depending on context.

Supporting evidence:
1. 'Research on' is commonly used when the study is about a topic.
   — Grammar Guide
"""


@dataclass(frozen=True)
class SearchCorrectionFollowupReport:
    ready: bool
    success: bool
    timed_out: bool
    duration_s: float
    queries_seen: list[str]
    second_turn_final_text: str
    second_turn_search_result_events: int


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run_smoke(*, timeout: float) -> SearchCorrectionFollowupReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=False)
    boot = harness.start()
    original = search_module.perform_search
    queries_seen: list[str] = []
    first = None
    second = None

    def _fake_search(query: str, data_dir, log_callback=None, cancel_token=None):
        del data_dir, cancel_token
        clean = str(query or "").strip()
        queries_seen.append(clean)
        if log_callback:
            log_callback(f"[fake-search] {clean}")
        time.sleep(0.2)
        if "latest ai news" in clean.lower():
            return AI_DATA
        return GRAMMAR_DATA

    search_module.perform_search = _fake_search
    try:
        first = harness.send_text(FIRST_TURN, timeout_s=timeout)
        second = harness.send_text(SECOND_TURN, timeout_s=timeout)
    finally:
        search_module.perform_search = original
        harness.close()

    second_assistant_messages = [
        str(message.get("content") or "").strip()
        for message in list(second.messages if second is not None else [])
        if str(message.get("role") or "").strip() == "assistant"
    ]
    second_final = second_assistant_messages[-1] if second_assistant_messages else ""
    second_events = sum(
        1 for event in list(second.ui_events if second is not None else [])
        if str(event.get("kind") or "").strip() == "search_result"
    )
    duration_s = float((first.duration_s if first is not None else 0.0) + (second.duration_s if second is not None else 0.0))
    timed_out = bool((first.timed_out if first is not None else True) or (second.timed_out if second is not None else True))
    success = (
        bool(boot.ready)
        and not timed_out
        and len(queries_seen) >= 2
        and queries_seen[0].lower() == "research on the"
        and queries_seen[1].lower() == "research on the latest ai news"
        and second_events >= 1
        and "latest ai news" in queries_seen[1].lower()
        and "model releases" in second_final.lower()
        and "research on" not in second_final.lower()
    )
    return SearchCorrectionFollowupReport(
        ready=bool(boot.ready),
        success=bool(success),
        timed_out=timed_out,
        duration_s=duration_s,
        queries_seen=queries_seen,
        second_turn_final_text=second_final,
        second_turn_search_result_events=second_events,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that 'I meant ...' after a completed search launches a fresh corrected search.")
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
        print(f"QUERIES_SEEN: {report.queries_seen}")
        print(f"SECOND_TURN_FINAL_TEXT: {report.second_turn_final_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
