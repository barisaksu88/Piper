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
class PlaywrightExampleAltPromptHarnessReport:
    ready: bool
    success: bool
    first_timed_out: bool
    second_timed_out: bool
    first_assistant_text: str
    second_assistant_text: str
    first_decision: str
    second_decision: str
    first_stage_type: str
    second_stage_type: str
    first_stage_goal: str
    second_stage_goal: str


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


def _extract_stage_fields(stats: dict) -> tuple[str, str]:
    stages = stats.get("stages") or []
    first_stage = stages[0] if stages and isinstance(stages[0], dict) else {}
    return str(stats.get("decision") or ""), str(first_stage.get("stage_type") or ""), str(first_stage.get("stage_goal") or "")


def run_smoke(*, timeout: float, keep_data_copy: bool) -> PlaywrightExampleAltPromptHarnessReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    first_assistant_text = ""
    second_assistant_text = ""
    first_timed_out = True
    second_timed_out = True
    first_decision = ""
    second_decision = ""
    first_stage_type = ""
    second_stage_type = ""
    first_stage_goal = ""
    second_stage_goal = ""
    try:
        if boot.ready:
            first = harness.send_text("What's the title of example.com?", timeout_s=timeout)
            second = harness.send_text("What's the main heading on example.com?", timeout_s=timeout)

            first_assistant_text = first.assistant_text
            second_assistant_text = second.assistant_text
            first_timed_out = first.timed_out
            second_timed_out = second.timed_out

            stats = _stats_lines(harness.data_dir)
            if len(stats) >= 2:
                first_decision, first_stage_type, first_stage_goal = _extract_stage_fields(stats[-2])
                second_decision, second_stage_type, second_stage_goal = _extract_stage_fields(stats[-1])
    finally:
        harness.close()

    first_lower = first_assistant_text.lower()
    second_lower = second_assistant_text.lower()
    success = (
        bool(boot.ready)
        and not first_timed_out
        and not second_timed_out
        and first_decision == "TASK"
        and second_decision == "TASK"
        and first_stage_type == "COMPUTER_USE"
        and second_stage_type == "COMPUTER_USE"
        and "page title" in first_stage_goal.lower()
        and "page heading" in second_stage_goal.lower()
        and "example domain" in first_lower
        and "example domain" in second_lower
        and "systems indicate" not in first_lower
        and "systems indicate" not in second_lower
    )
    return PlaywrightExampleAltPromptHarnessReport(
        ready=bool(boot.ready),
        success=bool(success),
        first_timed_out=bool(first_timed_out),
        second_timed_out=bool(second_timed_out),
        first_assistant_text=first_assistant_text,
        second_assistant_text=second_assistant_text,
        first_decision=first_decision,
        second_decision=second_decision,
        first_stage_type=first_stage_type,
        second_stage_type=second_stage_type,
        first_stage_goal=first_stage_goal,
        second_stage_goal=second_stage_goal,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify looser title/heading phrasings on example.com still route into live COMPUTER_USE."
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
        print(f"FIRST_DECISION: {report.first_decision}")
        print(f"SECOND_DECISION: {report.second_decision}")
        print(f"FIRST_STAGE_TYPE: {report.first_stage_type}")
        print(f"SECOND_STAGE_TYPE: {report.second_stage_type}")
        print(f"FIRST_STAGE_GOAL: {report.first_stage_goal}")
        print(f"SECOND_STAGE_GOAL: {report.second_stage_goal}")
        print(f"FIRST_ASSISTANT: {report.first_assistant_text}")
        print(f"SECOND_ASSISTANT: {report.second_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
