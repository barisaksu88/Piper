from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
import tools.search as search_module


INITIAL_TURN = "Search for MLPerf Inference results"
WORKSPACE_TURN = "workspace"
WEB_TURN = "web actually"
EXPECTED_QUERY = "MLPerf Inference results"


@dataclass(frozen=True)
class LookupSourceWorkspaceThenWebFlipReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    clarification_text: str
    workspace_text: str
    web_first_assistant_text: str
    web_final_assistant_text: str
    query_seen_by_search: str
    search_result_event_count: int
    hidden_search_summary_present: bool
    clarification_timed_out: bool
    workspace_timed_out: bool
    web_timed_out: bool


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
    return (
        "SEARCH SNIPPETS:\n"
        f"Title: {clean}\n"
        f"Snippet: Recent benchmark coverage for {clean}.\n\n"
        "--- DEEP DIVE (Full Content) ---\n"
        f"Source: https://example.test/{abs(hash(clean))}\n"
        f"Content: Recent reporting for {clean} includes current benchmark-oriented findings.\n"
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> LookupSourceWorkspaceThenWebFlipReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "mlperf_inference_results.txt").write_text(
        "workspace result marker\n",
        encoding="utf-8",
    )

    boot = harness.start()
    harness.chat_state.clear()
    clarification_text = ""
    workspace_text = ""
    web_first_assistant_text = ""
    web_final_assistant_text = ""
    query_seen_by_search = ""
    search_result_event_count = 0
    hidden_search_summary_present = False
    clarification_timed_out = False
    workspace_timed_out = False
    web_timed_out = False
    original = search_module.perform_search

    def _fake_search(query: str, data_dir, log_callback=None, cancel_token=None):
        del data_dir, cancel_token
        nonlocal query_seen_by_search
        query_seen_by_search = str(query or "").strip()
        if log_callback:
            log_callback(f"[fake-search] {query_seen_by_search}")
        time.sleep(0.2)
        return _build_fake_search_result(query_seen_by_search)

    search_module.perform_search = _fake_search
    try:
        clarification = harness.send_text(INITIAL_TURN, timeout_s=timeout)
        clarification_text = str(clarification.assistant_text or "")
        clarification_timed_out = bool(clarification.timed_out)

        workspace_turn = harness.send_text(WORKSPACE_TURN, timeout_s=timeout)
        workspace_text = str(workspace_turn.assistant_text or "")
        workspace_timed_out = bool(workspace_turn.timed_out)

        web_turn = harness.send_text(WEB_TURN, timeout_s=timeout)
        web_timed_out = bool(web_turn.timed_out)
        assistant_messages = [
            str(message.get("content") or "").strip()
            for message in web_turn.messages
            if str(message.get("role") or "").strip() == "assistant"
        ]
        web_first_assistant_text = assistant_messages[0] if assistant_messages else ""
        web_final_assistant_text = assistant_messages[-1] if assistant_messages else ""
        search_result_event_count = sum(
            1 for event in web_turn.ui_events if str(event.get("kind") or "").strip() == "search_result"
        )
        hidden_search_summary_present = any(
            str(message.get("role") or "").strip() == "system"
            and bool(message.get("hidden"))
            and str(message.get("content") or "").startswith("[SEARCH SUMMARY FOR '")
            for message in web_turn.messages
        )
    finally:
        search_module.perform_search = original
        harness.close()

    clarification_lower = clarification_text.lower()
    workspace_lower = workspace_text.lower()
    web_first_lower = web_first_assistant_text.lower()
    web_final_lower = web_final_assistant_text.lower()
    web_final_references_search_result = (
        "recent" in web_final_lower
        and any(marker in web_final_lower for marker in ("benchmark", "finding", "reporting", "result"))
    )
    success = (
        bool(boot.ready)
        and not clarification_timed_out
        and not workspace_timed_out
        and not web_timed_out
        and "web" in clarification_lower
        and "workspace" in clarification_lower
        and (
            "workspace result marker" in workspace_lower
            or "mlperf_inference_results.txt" in workspace_lower
            or "workspace" in workspace_lower
        )
        and bool(query_seen_by_search)
        and query_seen_by_search.lower() == EXPECTED_QUERY.lower()
        and search_result_event_count >= 1
        and hidden_search_summary_present
        and "ambiguous" not in web_final_lower
        and "workspace" not in web_final_lower
        and "what exactly do you need" not in web_final_lower
        and "probably" not in web_final_lower
        and "usual suspects" not in web_final_lower
        and "[search_first_pass_rule]" not in web_first_lower
        and web_final_references_search_result
    )
    return LookupSourceWorkspaceThenWebFlipReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        clarification_text=clarification_text,
        workspace_text=workspace_text,
        web_first_assistant_text=web_first_assistant_text,
        web_final_assistant_text=web_final_assistant_text,
        query_seen_by_search=query_seen_by_search,
        search_result_event_count=search_result_event_count,
        hidden_search_summary_present=hidden_search_summary_present,
        clarification_timed_out=clarification_timed_out,
        workspace_timed_out=workspace_timed_out,
        web_timed_out=web_timed_out,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an ambiguous workspace choice can later be corrected to web search without losing the original subject."
    )
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
        print(f"QUERY_SEEN_BY_SEARCH: {report.query_seen_by_search}")
        print(f"SEARCH_RESULT_EVENT_COUNT: {report.search_result_event_count}")
        print(f"CLARIFICATION: {report.clarification_text}")
        print(f"WORKSPACE: {report.workspace_text}")
        print(f"WEB_FIRST_ASSISTANT: {report.web_first_assistant_text}")
        print(f"WEB_FINAL_ASSISTANT: {report.web_final_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
