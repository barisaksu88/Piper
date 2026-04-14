from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from config import data_state_path


@dataclass(frozen=True)
class PlaywrightExampleTwoTurnHarnessReport:
    ready: bool
    success: bool
    first_timed_out: bool
    second_timed_out: bool
    first_assistant_text: str
    second_assistant_text: str
    first_duration_s: float
    second_duration_s: float
    first_outcome: str
    second_outcome: str
    second_stage_goal: str
    thread_error_seen: bool


def _stats_lines(data_dir) -> list[dict]:
    stats_path = data_dir / "stats.jsonl"
    if not stats_path.exists():
        return []
    rows: list[dict] = []
    for line in stats_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def run_smoke(*, timeout: float, keep_data_copy: bool) -> PlaywrightExampleTwoTurnHarnessReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    first_assistant_text = ""
    second_assistant_text = ""
    first_timed_out = True
    second_timed_out = True
    first_duration_s = 0.0
    second_duration_s = 0.0
    first_outcome = ""
    second_outcome = ""
    second_stage_goal = ""
    thread_error_seen = False
    try:
        if boot.ready:
            first = harness.send_text(
                "Open example.com in the browser and tell me the page title.",
                timeout_s=timeout,
            )
            second = harness.send_text(
                "What's the main heading?",
                timeout_s=timeout,
            )
            first_assistant_text = first.assistant_text
            second_assistant_text = second.assistant_text
            first_timed_out = first.timed_out
            second_timed_out = second.timed_out
            first_duration_s = first.duration_s
            second_duration_s = second.duration_s

            stats = _stats_lines(harness.data_dir)
            if len(stats) >= 1:
                first_outcome = str(stats[-2].get("outcome") or "") if len(stats) >= 2 else str(stats[-1].get("outcome") or "")
            if len(stats) >= 2:
                second_stats = stats[-1]
                second_outcome = str(second_stats.get("outcome") or "")
                stages = second_stats.get("stages") or []
                first_stage = stages[0] if stages and isinstance(stages[0], dict) else {}
                second_stage_goal = str(first_stage.get("stage_goal") or "")

            combined_text = " ".join((first_assistant_text, second_assistant_text)).lower()
            thread_error_seen = "cannot switch to a different thread" in combined_text
            if not thread_error_seen:
                for debug_name in ("persona_debug.txt", "planner_debug.txt", "router_debug.txt"):
                    debug_path = harness.data_dir / "debug" / debug_name
                    if not debug_path.exists():
                        continue
                    debug_text = debug_path.read_text(encoding="utf-8", errors="ignore").lower()
                    if "cannot switch to a different thread" in debug_text:
                        thread_error_seen = True
                        break
    finally:
        harness.close()

    second_lower = second_assistant_text.lower()
    success = (
        bool(boot.ready)
        and not first_timed_out
        and not second_timed_out
        and first_outcome == "VERIFIED"
        and second_outcome == "VERIFIED"
        and "page heading" in second_stage_goal.lower()
        and "example domain" in second_lower
        and "main heading" in second_lower
        and "requested text" not in second_lower
        and "systems indicate" not in first_assistant_text.lower()
        and "systems indicate" not in second_lower
        and not thread_error_seen
    )
    return PlaywrightExampleTwoTurnHarnessReport(
        ready=bool(boot.ready),
        success=bool(success),
        first_timed_out=bool(first_timed_out),
        second_timed_out=bool(second_timed_out),
        first_assistant_text=first_assistant_text,
        second_assistant_text=second_assistant_text,
        first_duration_s=first_duration_s,
        second_duration_s=second_duration_s,
        first_outcome=first_outcome,
        second_outcome=second_outcome,
        second_stage_goal=second_stage_goal,
        thread_error_seen=bool(thread_error_seen),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify two browser turns in one Piper session do not reuse Playwright across threads unsafely."
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"FIRST_TIMED_OUT: {report.first_timed_out}")
        print(f"SECOND_TIMED_OUT: {report.second_timed_out}")
        print(f"FIRST_OUTCOME: {report.first_outcome}")
        print(f"SECOND_OUTCOME: {report.second_outcome}")
        print(f"SECOND_STAGE_GOAL: {report.second_stage_goal}")
        print(f"THREAD_ERROR_SEEN: {report.thread_error_seen}")
        print(f"FIRST_DURATION_S: {report.first_duration_s}")
        print(f"SECOND_DURATION_S: {report.second_duration_s}")
        print(f"FIRST_ASSISTANT: {report.first_assistant_text}")
        print(f"SECOND_ASSISTANT: {report.second_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
