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
from config import data_state_path


@dataclass(frozen=True)
class FileMultiCreateCountSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    assistant_text: str
    timed_out: bool
    files_created: list[str]
    file_contents: dict[str, str]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileMultiCreateCountSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    target_dir = workspace / "temp_data"
    if target_dir.exists():
        for path in sorted(target_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        target_dir.rmdir()

    boot = harness.start()
    harness.chat_state.clear()
    result = harness.send_text(
        'Create a folder called "temp_data" and add 3 dummy files inside it.',
        timeout_s=timeout,
    )

    files_created = sorted(
        str(path.relative_to(workspace)).replace("\\", "/")
        for path in target_dir.rglob("*")
        if path.is_file()
    )
    file_contents = {
        rel: (workspace / rel).read_text(encoding="utf-8")
        for rel in files_created
    }
    harness.close()

    success = (
        bool(boot.ready)
        and not result.timed_out
        and len(files_created) == 3
        and all(file_contents.get(path, "").strip() for path in files_created)
    )
    return FileMultiCreateCountSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        assistant_text=result.assistant_text,
        timed_out=result.timed_out,
        files_created=files_created,
        file_contents=file_contents,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a multi-file create stage does not report success after only one dummy file is created."
    )
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
        print(f"TIMED_OUT: {report.timed_out}")
        print(f"ASSISTANT: {report.assistant_text}")
        print(f"FILES_CREATED: {report.files_created}")
        print(f"FILE_CONTENTS: {json.dumps(report.file_contents, ensure_ascii=False)}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
