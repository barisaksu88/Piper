from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.commands import handle_command
from core.engineering_support import EngineeringEscalationDetector, build_manual_codex_snapshot
from core.style import StyleManager


@dataclass(frozen=True)
class EscalationSmokeReport:
    success: bool
    auto_triggered: bool
    manual_triggered: bool
    command_parsed: bool
    log_path: str
    entries: int


def run_smoke() -> EscalationSmokeReport:
    root = Path(tempfile.mkdtemp(prefix="piper-codex-escalation-"))
    log_path = root / "codex_escalations.jsonl"
    detector = EngineeringEscalationDetector(log_path)

    auto = detector.record_signal(
        {
            "kind": "verification_block",
            "severity": "warning",
            "source": "executor",
            "summary": "FILE_WORK completion was blocked because verification is still missing.",
            "stage_goal": "Edit grocery_list.txt to remove eggs",
            "stage_type": "FILE_WORK",
        },
        user_msg="Remove eggs from the grocery list file.",
        route_decision={"decision": "TASK"},
        context_card={"goal": "Remove eggs", "stages": [{"stage_goal": "Edit grocery_list.txt to remove eggs"}]},
        scratchpad=["=== STAGE 1 START ===", "SYSTEM ERROR: FILE_WORK cannot complete until FILE_CHECKER_VERDICT is VERIFIED."],
        history_tail=[{"role": "user", "content": "Remove eggs from the grocery list file."}],
    )
    if auto is not None:
        raise AssertionError("Automatic escalation should not trigger on the first verification block.")

    auto = detector.record_signal(
        {
            "kind": "verification_block",
            "severity": "warning",
            "source": "executor",
            "summary": "FILE_WORK completion was blocked because verification is still missing.",
            "stage_goal": "Edit grocery_list.txt to remove eggs",
            "stage_type": "FILE_WORK",
        },
        user_msg="Remove eggs from the grocery list file.",
        route_decision={"decision": "TASK"},
        context_card={"goal": "Remove eggs", "stages": [{"stage_goal": "Edit grocery_list.txt to remove eggs"}]},
        scratchpad=["=== STAGE 1 START ===", "SYSTEM ERROR: FILE_WORK cannot complete until FILE_CHECKER_VERDICT is VERIFIED."],
        history_tail=[{"role": "user", "content": "Remove eggs from the grocery list file."}],
    )

    manual = build_manual_codex_snapshot(
        log_path=log_path,
        note="Please inspect the current runtime state.",
        user_msg="Need engineering support.",
        history_tail=[{"role": "user", "content": "Need engineering support."}],
        monitor_text="[ENGINEERING SIGNAL] FILE_WORK completion was blocked because verification is still missing.",
        dashboard_text="Codex support brief prepared.",
        status_snapshot="ERROR",
        source="smoke_test",
        recent_signals=detector.signals,
    )

    styles_dir = root / "styles"
    styles_dir.mkdir(parents=True, exist_ok=True)
    style_mgr = StyleManager(styles_dir)
    command = handle_command("/codex inspect latest failure", style_mgr=style_mgr)

    entries = []
    if log_path.exists():
        entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    success = bool(auto and manual) and command.handled and command.action == "codex_support" and len(entries) == 2
    return EscalationSmokeReport(
        success=success,
        auto_triggered=bool(auto and auto.get("decision") == "ask_codex"),
        manual_triggered=bool(manual and manual.get("manual")),
        command_parsed=bool(command.handled and command.action == "codex_support" and command.support_note == "inspect latest failure"),
        log_path=str(log_path),
        entries=len(entries),
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Run a smoke test for Codex escalation signals and manual support snapshots.")


def main() -> int:
    args = build_parser().parse_args()
    _ = args
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
