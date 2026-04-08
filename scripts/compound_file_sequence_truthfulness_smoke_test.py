from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness


@dataclass(frozen=True)
class CompoundSequenceTurnReport:
    name: str
    timed_out: bool
    duration_s: float
    assistant_text: str
    bob_exists: bool
    b_exists: bool
    b_content: str | None
    passed: bool


@dataclass(frozen=True)
class CompoundSequenceTruthfulnessReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[CompoundSequenceTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _workspace_state(workspace: Path) -> tuple[bool, bool, str | None]:
    bob_path = workspace / "bob"
    b_path = workspace / "b.txt"
    bob_exists = bob_path.exists()
    b_exists = b_path.exists()
    b_content = b_path.read_text(encoding="utf-8") if b_exists else None
    return bob_exists, b_exists, b_content


def _turn_passed(
    name: str,
    assistant_text: str,
    *,
    bob_exists: bool,
    b_exists: bool,
    b_content: str | None,
    timed_out: bool,
) -> bool:
    if timed_out:
        return False
    lowered = assistant_text.lower()
    if name == "clarify":
        return "which filename" in lowered and "exact content" in lowered
    if name == "sequence":
        return (
            (not bob_exists)
            and b_exists
            and b_content == "hello"
            and "final state" in lowered
            and "does not exist" in lowered
        )
    if name == "final_state_correction":
        return (
            (not bob_exists)
            and b_exists
            and b_content == "hello"
            and "did not exist" in lowered
        )
    return False


def run_smoke(*, timeout: float, keep_data_copy: bool) -> CompoundSequenceTruthfulnessReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    workspace = harness.data_dir / "workspace"
    (workspace / "b.txt").write_text("hello", encoding="utf-8")
    boot = harness.start()
    turns: list[CompoundSequenceTurnReport] = []
    try:
        for name, text in (
            ("clarify", "create a file and then delete it and then undo it and then redo it"),
            ("sequence", "doesnt matter, name: bob, content: flame"),
            ("final_state_correction", "its final state should be non-existing i think"),
        ):
            result = harness.send_text(text, timeout_s=timeout)
            bob_exists, b_exists, b_content = _workspace_state(workspace)
            turns.append(
                CompoundSequenceTurnReport(
                    name=name,
                    timed_out=result.timed_out,
                    duration_s=result.duration_s,
                    assistant_text=result.assistant_text,
                    bob_exists=bob_exists,
                    b_exists=b_exists,
                    b_content=b_content,
                    passed=_turn_passed(
                        name,
                        result.assistant_text,
                        bob_exists=bob_exists,
                        b_exists=b_exists,
                        b_content=b_content,
                        timed_out=result.timed_out,
                    ),
                )
            )
    finally:
        harness.close()
    return CompoundSequenceTruthfulnessReport(
        ready=bool(boot.ready),
        success=bool(boot.ready) and all(turn.passed for turn in turns),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify compound create/delete/undo/redo file flow reports the real final state and treats state-correction follow-ups as non-mutating.")
    parser.add_argument("--timeout", type=float, default=240.0, help="Per-turn timeout in seconds.")
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
            print(f"  bob_exists={turn.bob_exists} b_exists={turn.b_exists} b_content={turn.b_content!r}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
