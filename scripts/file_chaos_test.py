from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness

DEFAULT_PROMPT = (
    "Organize the workspace. Put files with the same extension into relevant folders, "
    "avoid duplicates, and delete empty folders that emerge."
)

EXPECTED_BUCKETS = {
    ".png": "images",
    ".txt": "text_files",
    ".py": "python_scripts",
    ".json": ".json",
}

EXPECTED_FILES = {
    ".json/misc_data.json",
    "images/PiperGen_90001_.png",
    "images/sine_wave_chart.png",
    "images/reference_image.png",
    "python_scripts/existing_script.py",
    "text_files/trip_checklist.txt",
    "text_files/qwen_flow_chart.txt",
    "text_files/old_note.txt",
    "text_files/reference_notes.txt",
    "python_scripts/cleanup_plan.py",
    "python_scripts/report_helper.py",
    ".json/file_operations.json",
    ".json/reference_state.json",
}

EXPECTED_REMOVED_DIRS = {
    "archives",
    "drafts",
    "misc",
    "scripts",
    "sorted",
    "text",
}


@dataclass(frozen=True)
class ChaosReport:
    ready: bool
    prompt: str
    assistant_text: str
    timed_out: bool
    duration_s: float
    success: bool
    workspace_dir: str
    kept_data_dir: str | None
    root_dirs: list[str]
    root_files: list[str]
    misplaced_files: list[str]
    empty_dirs: list[str]
    missing_expected_files: list[str]
    unexpected_remaining_dirs: list[str]
    status_history: list[str]
    system_messages: list[str]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def seed_workspace_fixture(workspace: Path) -> None:
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    png_bytes = b"\x89PNG\r\nchaos-test\n"

    _write_bytes(workspace / "images" / "reference_image.png", png_bytes)
    _write_text(workspace / "text_files" / "reference_notes.txt", "reference note\n")
    _write_text(workspace / "python_scripts" / "existing_script.py", "print('existing')\n")
    _write_json(workspace / ".json" / "reference_state.json", {"state": "ok"})

    _write_bytes(workspace / "PiperGen_90001_.png", png_bytes)
    _write_bytes(workspace / "sine_wave_chart.png", b"\x89PNG\r\nsine-wave\n")
    _write_text(workspace / "trip_checklist.txt", "passport\ncharger\n")
    _write_text(workspace / "qwen_flow_chart.txt", "route -> plan -> act\n")
    _write_text(workspace / "cleanup_plan.py", "print('cleanup')\n")
    _write_json(workspace / "file_operations.json", {"ops": ["list_tree", "move_many"]})

    _write_text(workspace / "reference_notes.txt", "reference note\n")
    _write_json(workspace / "reference_state.json", {"state": "ok"})
    _write_bytes(workspace / "reference_image.png", png_bytes)

    _write_text(workspace / "archives" / "old_note.txt", "archived note\n")
    _write_text(workspace / "drafts" / "report_helper.py", "def report():\n    return 'ok'\n")
    _write_json(workspace / "misc" / "misc_data.json", {"kind": "misc"})

    for rel_dir in ("scripts", "sorted", "text", "logs/old"):
        (workspace / rel_dir).mkdir(parents=True, exist_ok=True)


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _find_empty_dirs(root: Path) -> list[str]:
    empty: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not any(path.iterdir()):
            empty.append(path.relative_to(root).as_posix())
    return empty


def _find_misplaced_files(root: Path) -> list[str]:
    misplaced: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = Path(rel).parts
        if not parts:
            continue
        top_level = parts[0]
        expected_top = EXPECTED_BUCKETS.get(path.suffix.lower())
        if expected_top and top_level != expected_top:
            misplaced.append(rel)
    return misplaced


def build_report(
    *,
    workspace: Path,
    result_assistant_text: str,
    timed_out: bool,
    duration_s: float,
    status_history: list[str],
    system_messages: list[str],
    prompt: str,
    kept_data_dir: Path | None,
) -> ChaosReport:
    root_dirs = sorted(path.name for path in workspace.iterdir() if path.is_dir())
    root_files = sorted(path.name for path in workspace.iterdir() if path.is_file())
    empty_dirs = _find_empty_dirs(workspace)
    misplaced_files = _find_misplaced_files(workspace)
    missing_expected_files = sorted(rel for rel in EXPECTED_FILES if not (workspace / rel).exists())
    unexpected_remaining_dirs = sorted(rel for rel in EXPECTED_REMOVED_DIRS if (workspace / rel).exists())
    success = not any(
        (
            timed_out,
            root_files,
            empty_dirs,
            misplaced_files,
            missing_expected_files,
            unexpected_remaining_dirs,
        )
    )
    return ChaosReport(
        ready=True,
        prompt=prompt,
        assistant_text=result_assistant_text,
        timed_out=timed_out,
        duration_s=duration_s,
        success=success,
        workspace_dir=str(workspace),
        kept_data_dir=str(kept_data_dir) if kept_data_dir else None,
        root_dirs=root_dirs,
        root_files=root_files,
        misplaced_files=misplaced_files,
        empty_dirs=empty_dirs,
        missing_expected_files=missing_expected_files,
        unexpected_remaining_dirs=unexpected_remaining_dirs,
        status_history=status_history,
        system_messages=system_messages,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recreate and verify the File Chaos Test against an isolated Piper harness workspace.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to send to Piper.")
    parser.add_argument("--timeout", type=float, default=240.0, help="Harness timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()

    harness = PiperHarness(isolated_data=True, keep_data_copy=args.keep_data_copy)
    workspace = harness.data_dir / "workspace"
    _clear_isolated_chat_memory(harness.data_dir)
    seed_workspace_fixture(workspace)

    try:
        boot = harness.start()
        if not boot.ready:
            payload = {"ready": False, "boot": asdict(boot)}
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 1

        result = harness.send_text(args.prompt, timeout_s=args.timeout)
        report = build_report(
            workspace=workspace,
            result_assistant_text=result.assistant_text,
            timed_out=result.timed_out,
            duration_s=result.duration_s,
            status_history=result.status_history,
            system_messages=result.system_messages,
            prompt=args.prompt,
            kept_data_dir=None,
        )
    finally:
        harness.close()

    report = ChaosReport(
        **{
            **asdict(report),
            "kept_data_dir": str(harness.kept_data_dir) if harness.kept_data_dir else None,
        }
    )

    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print("FILE CHAOS TEST")
        print(f"PROMPT: {report.prompt}")
        print(f"ASSISTANT: {report.assistant_text or '(no assistant reply)'}")
        print(f"TIMED_OUT: {report.timed_out}")
        print(f"DURATION_S: {report.duration_s}")
        print(f"SUCCESS: {report.success}")
        print(f"WORKSPACE_DIR: {report.workspace_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        print(f"ROOT_DIRS: {', '.join(report.root_dirs) if report.root_dirs else '(none)'}")
        print(f"ROOT_FILES: {', '.join(report.root_files) if report.root_files else '(none)'}")
        if report.empty_dirs:
            print("EMPTY_DIRS:")
            for item in report.empty_dirs:
                print(f"  - {item}")
        if report.misplaced_files:
            print("MISPLACED_FILES:")
            for item in report.misplaced_files:
                print(f"  - {item}")
        if report.missing_expected_files:
            print("MISSING_EXPECTED_FILES:")
            for item in report.missing_expected_files:
                print(f"  - {item}")
        if report.unexpected_remaining_dirs:
            print("UNEXPECTED_REMAINING_DIRS:")
            for item in report.unexpected_remaining_dirs:
                print(f"  - {item}")
        if report.system_messages:
            print("SYSTEM_MESSAGES:")
            for item in report.system_messages:
                print(f"  - {item}")
        print(f"STATUS_HISTORY: {' | '.join(report.status_history) if report.status_history else '(none)'}")

    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
