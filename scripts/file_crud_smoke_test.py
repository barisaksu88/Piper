from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness

CRUD_TASKS = (
    ("create_file", "In the workspace, create the text file text_files/harness_alpha.txt with the exact contents: alpha beta gamma"),
    ("copy_file", "In the workspace, create the folder text_files/harness_box if needed and copy text_files/harness_alpha.txt to text_files/harness_box/harness_alpha_copy.txt"),
    ("move_file", "In the workspace, move text_files/harness_alpha.txt to text_files/harness_box/harness_alpha_moved.txt"),
    ("read_file", "Read the file text_files/harness_box/harness_alpha_moved.txt and tell me its exact contents only."),
    ("delete_file", "Delete the file text_files/harness_box/harness_alpha_copy.txt from the workspace."),
    ("delete_absent_again", "Delete the file text_files/harness_box/harness_alpha_copy.txt from the workspace."),
)


@dataclass(frozen=True)
class CrudTurnReport:
    name: str
    timed_out: bool
    duration_s: float
    assistant_text: str
    status_history: list[str]
    alpha_exists: bool
    copy_exists: bool
    moved_exists: bool
    moved_content: str | None
    passed: bool


@dataclass(frozen=True)
class CrudSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[CrudTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _reset_workspace_fixture(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    alpha_path = workspace / "text_files" / "harness_alpha.txt"
    harness_box = workspace / "text_files" / "harness_box"
    if alpha_path.exists():
        alpha_path.unlink()
    shutil.rmtree(harness_box, ignore_errors=True)


def _workspace_state(workspace: Path) -> dict[str, object]:
    moved_path = workspace / "text_files" / "harness_box" / "harness_alpha_moved.txt"
    return {
        "alpha_exists": (workspace / "text_files" / "harness_alpha.txt").exists(),
        "copy_exists": (workspace / "text_files" / "harness_box" / "harness_alpha_copy.txt").exists(),
        "moved_exists": moved_path.exists(),
        "moved_content": moved_path.read_text(encoding="utf-8") if moved_path.exists() else None,
    }


def _turn_passed(name: str, state: dict[str, object], assistant_text: str, timed_out: bool) -> bool:
    if timed_out:
        return False
    if name == "create_file":
        return bool(state["alpha_exists"]) and not bool(state["copy_exists"]) and not bool(state["moved_exists"])
    if name == "copy_file":
        return bool(state["alpha_exists"]) and bool(state["copy_exists"]) and not bool(state["moved_exists"])
    if name == "move_file":
        return (not bool(state["alpha_exists"])) and bool(state["copy_exists"]) and bool(state["moved_exists"]) and state["moved_content"] == "alpha beta gamma"
    if name == "read_file":
        return state["moved_content"] == "alpha beta gamma" and "alpha beta gamma" in assistant_text
    if name == "delete_file":
        return (not bool(state["copy_exists"])) and bool(state["moved_exists"])
    if name == "delete_absent_again":
        return (not bool(state["copy_exists"])) and bool(state["moved_exists"])
    return False


def run_smoke(*, timeout: float, keep_data_copy: bool) -> CrudSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    workspace = harness.data_dir / "workspace"
    _reset_workspace_fixture(workspace)
    boot = harness.start()
    turns: list[CrudTurnReport] = []
    try:
        for name, text in CRUD_TASKS:
            result = harness.send_text(text, timeout_s=timeout)
            state = _workspace_state(workspace)
            turns.append(
                CrudTurnReport(
                    name=name,
                    timed_out=result.timed_out,
                    duration_s=result.duration_s,
                    assistant_text=result.assistant_text,
                    status_history=list(result.status_history),
                    alpha_exists=bool(state["alpha_exists"]),
                    copy_exists=bool(state["copy_exists"]),
                    moved_exists=bool(state["moved_exists"]),
                    moved_content=state["moved_content"] if isinstance(state["moved_content"], str) else None,
                    passed=_turn_passed(name, state, result.assistant_text, result.timed_out),
                )
            )
    finally:
        harness.close()
    return CrudSmokeReport(
        ready=bool(boot.ready),
        success=bool(boot.ready) and all(turn.passed for turn in turns),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic small FILE_WORK CRUD smoke test through the isolated Piper harness.")
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
            print(
                "  state="
                f"alpha={turn.alpha_exists} copy={turn.copy_exists} moved={turn.moved_exists} "
                f"moved_content={turn.moved_content!r}"
            )
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
