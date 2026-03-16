from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness

BROKEN_SCRIPT = """PLAYER_SPEED = 5
SCREEN_WIDTH = 20


def handle_key(key: str, velocity: int) -> int:
    if key in ("left", "a"):
        return -PLAYER_SPEED
    if key == "space":
        return 0
    return velocity


def clamp_position(x: int) -> int:
    if x < 0:
        return 0
    if x > SCREEN_WIDT:
        return SCREEN_WIDT
    return x


if __name__ == "__main__":
    left = handle_key("left", 0)
    right = handle_key("right", 0)
    print(f"left={left}, right={right}, clamp={clamp_position(30)}")
"""

DIAGNOSIS_TURN = "Inspect control_demo.py and identify why the left and right controls do not both work. Diagnose only; do not edit the file."
FIX_TURN = "Fix control_demo.py so both left and right controls work correctly and the boundary clamp uses the proper width constant."
RUN_TURN = "Run control_demo.py."


@dataclass(frozen=True)
class FlowTurnReport:
    name: str
    timed_out: bool
    duration_s: float
    assistant_text: str
    status_history: list[str]
    ui_event_count: int
    passed: bool


@dataclass(frozen=True)
class CodeRepairFlowReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[FlowTurnReport]
    final_file_content: str


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _seed_fixture(workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    script_path = workspace / "control_demo.py"
    script_path.write_text(BROKEN_SCRIPT, encoding="utf-8")
    return script_path


def _diagnosis_passed(result, file_content: str) -> bool:
    if result.timed_out:
        return False
    assistant_text = (result.assistant_text or "").strip()
    text_l = assistant_text.lower()
    mentions_control_issue = any(token in text_l for token in ("right", "missing", "handle_key"))
    return (
        assistant_text
        and assistant_text.strip() != file_content.strip()
        and ("control_demo.py" in assistant_text or "right" in text_l)
        and mentions_control_issue
        and "failed" not in text_l
        and "engineering support" not in text_l
        and file_content == BROKEN_SCRIPT
    )


def _fix_passed(result, file_content: str) -> bool:
    if result.timed_out:
        return False
    text_l = (result.assistant_text or "").lower()
    typo_still_present = re.search(r"(?<![A-Za-z0-9_])SCREEN_WIDT(?![A-Za-z0-9_])", file_content) is not None
    width_constant_present = re.search(r"(?<![A-Za-z0-9_])SCREEN_WIDTH(?![A-Za-z0-9_])", file_content) is not None
    right_branch = re.search(r'key\s+in\s+\("right",\s*"d"\)|key\s*==\s*"right"|key\s+in\s+\("left",\s*"a",\s*"right",\s*"d"\)', file_content)
    return (
        bool((result.assistant_text or "").strip())
        and not typo_still_present
        and width_constant_present
        and right_branch is not None
        and "failed" not in text_l
        and "error" not in text_l
    )


def _run_passed(result) -> bool:
    if result.timed_out:
        return False
    ui_events = result.ui_events
    launch_seen = any(str(event.get("kind") or "") == "code_session_launch" for event in ui_events)
    output_payloads = [
        str(event.get("payload") or "")
        for event in ui_events
        if str(event.get("kind") or "") in {"code_session_output", "status_widget_dashboard_activity", "status"}
    ]
    output_blob = "\n".join(output_payloads).lower()
    assistant_l = (result.assistant_text or "").lower()
    captured_output = launch_seen and "right=5" in output_blob and "clamp=20" in output_blob
    launched_in_code_tab = launch_seen and (
        "embedded code tab" in assistant_l
        or "queued for execution" in assistant_l
        or "queued for the embedded code tab" in assistant_l
    )
    return captured_output or launched_in_code_tab


def run_smoke(*, timeout: float, keep_data_copy: bool) -> CodeRepairFlowReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    script_path = harness.data_dir / "workspace" / "control_demo.py"
    report = CodeRepairFlowReport(
        ready=False,
        success=False,
        data_dir=str(harness.data_dir),
        kept_data_dir=None,
        turns=[],
        final_file_content="",
    )
    try:
        _clear_isolated_chat_memory(harness.data_dir)
        _seed_fixture(harness.data_dir / "workspace")
        boot = harness.start()

        turns: list[FlowTurnReport] = []

        diagnosis_result = harness.send_text(DIAGNOSIS_TURN, timeout_s=timeout)
        diagnosis_content = script_path.read_text(encoding="utf-8")
        turns.append(
            FlowTurnReport(
                name="diagnosis",
                timed_out=diagnosis_result.timed_out,
                duration_s=diagnosis_result.duration_s,
                assistant_text=diagnosis_result.assistant_text,
                status_history=list(diagnosis_result.status_history),
                ui_event_count=len(diagnosis_result.ui_events),
                passed=_diagnosis_passed(diagnosis_result, diagnosis_content),
            )
        )

        fix_result = harness.send_text(FIX_TURN, timeout_s=timeout)
        fixed_content = script_path.read_text(encoding="utf-8")
        turns.append(
            FlowTurnReport(
                name="fix",
                timed_out=fix_result.timed_out,
                duration_s=fix_result.duration_s,
                assistant_text=fix_result.assistant_text,
                status_history=list(fix_result.status_history),
                ui_event_count=len(fix_result.ui_events),
                passed=_fix_passed(fix_result, fixed_content),
            )
        )

        run_result = harness.send_text(RUN_TURN, timeout_s=timeout, idle_grace_s=1.25)
        final_content = script_path.read_text(encoding="utf-8")
        turns.append(
            FlowTurnReport(
                name="run",
                timed_out=run_result.timed_out,
                duration_s=run_result.duration_s,
                assistant_text=run_result.assistant_text,
                status_history=list(run_result.status_history),
                ui_event_count=len(run_result.ui_events),
                passed=_run_passed(run_result),
            )
        )

        report = CodeRepairFlowReport(
            ready=bool(boot.ready),
            success=bool(boot.ready) and all(turn.passed for turn in turns),
            data_dir=str(harness.data_dir),
            kept_data_dir=None,
            turns=turns,
            final_file_content=final_content,
        )
    finally:
        harness.close()

    return CodeRepairFlowReport(
        ready=report.ready,
        success=report.success,
        data_dir=report.data_dir,
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=report.turns,
        final_file_content=report.final_file_content,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a generic code diagnosis-fix-run smoke through the isolated Piper harness.")
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
        print(f"DATA_DIR: {report.data_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        for turn in report.turns:
            print(f"{turn.name}: passed={turn.passed} timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
            print(f"  ui_event_count={turn.ui_event_count}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
