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
class FileRenameThenMoveTurnReport:
    name: str
    assistant_text: str
    timed_out: bool
    alpha_exists: bool
    beta_exists: bool
    archive_beta_exists: bool
    archive_beta_content: str | None


@dataclass(frozen=True)
class FileRenameThenMoveSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[FileRenameThenMoveTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _snapshot(workspace: Path) -> tuple[bool, bool, bool, str | None]:
    alpha = workspace / "alpha.txt"
    beta = workspace / "beta.txt"
    archive_beta = workspace / "archive" / "beta.txt"
    return (
        alpha.exists(),
        beta.exists(),
        archive_beta.exists(),
        archive_beta.read_text(encoding="utf-8") if archive_beta.exists() else None,
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileRenameThenMoveSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for rel in ("alpha.txt", "beta.txt", "archive/beta.txt"):
        path = workspace / rel
        if path.exists():
            path.unlink()
    archive_dir = workspace / "archive"
    if archive_dir.exists() and not any(archive_dir.iterdir()):
        archive_dir.rmdir()
    (workspace / "alpha.txt").write_text("alpha-body", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    turns: list[FileRenameThenMoveTurnReport] = []

    for name, text in (
        ("rename_then_move", "Rename file alpha.txt to beta.txt. Then move the new beta.txt into a folder called archive."),
        ("content_followup", "Did you keep the original content?"),
    ):
        result = harness.send_text(text, timeout_s=timeout)
        alpha_exists, beta_exists, archive_beta_exists, archive_beta_content = _snapshot(workspace)
        turns.append(
            FileRenameThenMoveTurnReport(
                name=name,
                assistant_text=result.assistant_text,
                timed_out=result.timed_out,
                alpha_exists=alpha_exists,
                beta_exists=beta_exists,
                archive_beta_exists=archive_beta_exists,
                archive_beta_content=archive_beta_content,
            )
        )

    harness.close()

    first, second = turns
    success = (
        bool(boot.ready)
        and not first.timed_out
        and not second.timed_out
        and not first.alpha_exists
        and not first.beta_exists
        and first.archive_beta_exists
        and first.archive_beta_content == "alpha-body"
        and "incomplete" not in first.assistant_text.lower()
        and not second.alpha_exists
        and not second.beta_exists
        and second.archive_beta_exists
        and second.archive_beta_content == "alpha-body"
        and bool(second.assistant_text.strip())
    )
    return FileRenameThenMoveSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify rename-then-move routes to one verifiable final state and follow-up content questions do not mutate it."
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
        for turn in report.turns:
            print(f"{turn.name}: timed_out={turn.timed_out}")
            print(f"  assistant={turn.assistant_text}")
            print(f"  alpha_exists={turn.alpha_exists} beta_exists={turn.beta_exists}")
            print(f"  archive_beta_exists={turn.archive_beta_exists} archive_beta_content={turn.archive_beta_content!r}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
