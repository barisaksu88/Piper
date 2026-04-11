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
class PythonDocsFollowupHarnessReport:
    ready: bool
    success: bool
    replies: list[str]
    timed_out_flags: list[bool]
    outcomes: list[str]
    stage_types: list[str]
    stage_goals: list[str]


_PROMPTS = (
    "Open docs.python.org/3/license.html in the browser and tell me the page title.",
    "what else is there",
    "general info",
    "anything else?",
)


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


def run_smoke(*, timeout: float, keep_data_copy: bool) -> PythonDocsFollowupHarnessReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    replies: list[str] = []
    timed_out_flags: list[bool] = []
    outcomes: list[str] = []
    stage_types: list[str] = []
    stage_goals: list[str] = []
    try:
        if boot.ready:
            for prompt in _PROMPTS:
                result = harness.send_text(prompt, timeout_s=timeout)
                replies.append(result.assistant_text)
                timed_out_flags.append(bool(result.timed_out))

            stats = _stats_lines(harness.data_dir)
            relevant = stats[-len(_PROMPTS) :] if len(stats) >= len(_PROMPTS) else stats
            for stat in relevant:
                outcomes.append(str(stat.get("outcome") or ""))
                stages = stat.get("stages") or []
                stage = stages[0] if stages and isinstance(stages[0], dict) else {}
                stage_types.append(str(stage.get("stage_type") or ""))
                stage_goals.append(str(stage.get("stage_goal") or ""))
    finally:
        harness.close()

    lower_replies = [str(item or "").lower() for item in replies]
    success = (
        bool(boot.ready)
        and len(replies) == len(_PROMPTS)
        and not any(timed_out_flags)
        and outcomes[:4] == ["VERIFIED", "VERIFIED", "VERIFIED", "VERIFIED"]
        and stage_types[:4] == ["COMPUTER_USE", "COMPUTER_USE", "COMPUTER_USE", "COMPUTER_USE"]
        and len(set(lower_replies[1:])) >= 2
        and "section about 'general info'" not in lower_replies[1]
        and "section about 'general info'" not in lower_replies[2]
        and "section about 'general info'" not in lower_replies[3]
        and "which specific piece of information" not in lower_replies[1]
        and "clarify" not in stage_goals[1].lower()
        and "python.org/about/license" not in lower_replies[2]
        and "welcome to python.org" not in lower_replies[2]
        and "docs.python.org/3/license.html" in stage_goals[2].lower()
        and "clarify" not in stage_goals[3].lower()
        and "which specific piece of information" not in lower_replies[3]
    )
    return PythonDocsFollowupHarnessReport(
        ready=bool(boot.ready),
        success=bool(success),
        replies=replies,
        timed_out_flags=timed_out_flags,
        outcomes=outcomes,
        stage_types=stage_types,
        stage_goals=stage_goals,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Python docs browser follow-ups stay in COMPUTER_USE and remain pinned to the current page."
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=True))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"TIMED_OUT_FLAGS: {report.timed_out_flags}")
        print(f"OUTCOMES: {report.outcomes}")
        print(f"STAGE_TYPES: {report.stage_types}")
        print(f"STAGE_GOALS: {report.stage_goals}")
        print(f"REPLIES: {report.replies}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
