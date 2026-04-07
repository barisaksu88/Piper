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
class ComputerUseHarnessSmokeReport:
    ready: bool
    success: bool
    timed_out: bool
    assistant_text: str
    duration_s: float
    stats_decision: str
    stage_type: str
    stage_goal: str


def _latest_stats_line(data_dir) -> dict:
    stats_path = data_dir / "stats.jsonl"
    if not stats_path.exists():
        return {}
    lines = [line.strip() for line in stats_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        payload = json.loads(lines[-1])
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def run_smoke(*, timeout: float, keep_data_copy: bool) -> ComputerUseHarnessSmokeReport:
    fixture = (ROOT_DIR / "scripts" / "fixtures" / "computer_use" / "index.html").resolve().as_uri()
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    assistant_text = ""
    timed_out = True
    duration_s = 0.0
    stats = {}
    try:
        if boot.ready:
            result = harness.send_text(
                f"Open {fixture} in the browser and tell me the page title.",
                timeout_s=timeout,
            )
            assistant_text = result.assistant_text
            timed_out = result.timed_out
            duration_s = result.duration_s
            stats = _latest_stats_line(harness.data_dir)
    finally:
        harness.close()

    stages = stats.get("stages") or []
    first_stage = stages[0] if stages and isinstance(stages[0], dict) else {}
    stats_decision = str(stats.get("decision") or "")
    stage_type = str(first_stage.get("stage_type") or "")
    stage_goal = str(first_stage.get("stage_goal") or "")
    lowered_reply = assistant_text.lower()
    success = (
        bool(boot.ready)
        and not timed_out
        and stats_decision == "TASK"
        and stage_type == "COMPUTER_USE"
        and "browser fixture home" in lowered_reply
    )
    return ComputerUseHarnessSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        timed_out=bool(timed_out),
        assistant_text=assistant_text,
        duration_s=duration_s,
        stats_decision=stats_decision,
        stage_type=stage_type,
        stage_goal=stage_goal,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify explicit browser requests route into COMPUTER_USE and return the fixture page title.")
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
        print(f"TIMED_OUT: {report.timed_out}")
        print(f"STATS_DECISION: {report.stats_decision}")
        print(f"STAGE_TYPE: {report.stage_type}")
        print(f"STAGE_GOAL: {report.stage_goal}")
        print(f"DURATION_S: {report.duration_s}")
        print(f"ASSISTANT: {report.assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
