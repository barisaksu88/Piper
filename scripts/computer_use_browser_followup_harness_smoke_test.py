from __future__ import annotations

import argparse
import json
import sys
import types
from dataclasses import asdict, dataclass

# Pre-stub heavy ML libs that hang at import on Windows.
class _StubModule(types.ModuleType):
    def __getattr__(self, name: str):
        raise ImportError(f"{self.__name__} is stubbed")

for _mod_name in ("resemblyzer", "sentence_transformers"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _StubModule(_mod_name)

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from config import data_state_path


@dataclass(frozen=True)
class BrowserFollowupHarnessReport:
    ready: bool
    success: bool
    first_timed_out: bool
    second_timed_out: bool
    first_assistant_text: str
    second_assistant_text: str
    second_outcome: str
    second_stage_type: str
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


def run_smoke(*, timeout: float, keep_data_copy: bool) -> BrowserFollowupHarnessReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    first_assistant_text = ""
    second_assistant_text = ""
    first_timed_out = True
    second_timed_out = True
    second_outcome = ""
    second_stage_type = ""
    second_stage_goal = ""
    try:
        if boot.ready:
            first = harness.send_text(
                "Open iana.org/domains/reserved in the browser and tell me the main heading.",
                timeout_s=timeout,
            )
            second = harness.send_text(
                "what else is there",
                timeout_s=timeout,
            )
            first_assistant_text = first.assistant_text
            second_assistant_text = second.assistant_text
            first_timed_out = first.timed_out
            second_timed_out = second.timed_out

            stats = _stats_lines(harness.data_dir)
            if len(stats) >= 2:
                second_stats = stats[-1]
                second_outcome = str(second_stats.get("outcome") or "")
                stages = second_stats.get("stages") or []
                first_stage = stages[0] if stages and isinstance(stages[0], dict) else {}
                second_stage_type = str(first_stage.get("stage_type") or "")
                second_stage_goal = str(first_stage.get("stage_goal") or "")
    finally:
        harness.close()

    second_lower = second_assistant_text.lower()
    success = (
        bool(boot.ready)
        and not first_timed_out
        and not second_timed_out
        and second_outcome == "VERIFIED"
        and second_stage_type == "COMPUTER_USE"
        and "general info" in second_stage_goal.lower()
        and "certain domains are set aside" in second_lower
        and "section about 'general info'" not in second_lower
        and ("other visible sections include" in second_lower or "example domains" in second_lower)
        and "broad inquiry" not in second_lower
        and "are you referring" not in second_lower
    )
    return BrowserFollowupHarnessReport(
        ready=bool(boot.ready),
        success=bool(success),
        first_timed_out=bool(first_timed_out),
        second_timed_out=bool(second_timed_out),
        first_assistant_text=first_assistant_text,
        second_assistant_text=second_assistant_text,
        second_outcome=second_outcome,
        second_stage_type=second_stage_type,
        second_stage_goal=second_stage_goal,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify short browser follow-ups stay grounded in the active page instead of falling back to generic chat."
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
        print(f"SECOND_OUTCOME: {report.second_outcome}")
        print(f"SECOND_STAGE_TYPE: {report.second_stage_type}")
        print(f"SECOND_STAGE_GOAL: {report.second_stage_goal}")
        print(f"FIRST_ASSISTANT: {report.first_assistant_text}")
        print(f"SECOND_ASSISTANT: {report.second_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
