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
class FileRenameMoveCorrectionTurnReport:
    name: str
    assistant_text: str
    timed_out: bool
    duration_s: float
    alpha_exists: bool
    beta_exists: bool
    archive_beta_exists: bool
    archive_old_beta_exists: bool
    archive_old_beta_content: str | None


@dataclass(frozen=True)
class FileRenameMoveCorrectionSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[FileRenameMoveCorrectionTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _snapshot(workspace: Path) -> tuple[bool, bool, bool, bool, str | None]:
    alpha = workspace / "alpha.txt"
    beta = workspace / "beta.txt"
    archive_beta = workspace / "archive" / "beta.txt"
    archive_old_beta = workspace / "archive" / "old" / "beta.txt"
    return (
        alpha.exists(),
        beta.exists(),
        archive_beta.exists(),
        archive_old_beta.exists(),
        archive_old_beta.read_text(encoding="utf-8") if archive_old_beta.exists() else None,
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileRenameMoveCorrectionSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for rel in ("alpha.txt", "beta.txt", "archive/beta.txt", "archive/old/beta.txt"):
        path = workspace / rel
        if path.exists():
            path.unlink()
    for rel in ("archive/old", "archive"):
        directory = workspace / rel
        if directory.exists():
            for child in sorted(directory.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            if directory.exists():
                try:
                    directory.rmdir()
                except OSError:
                    pass
    (workspace / "alpha.txt").write_text("alpha-body", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    turns: list[FileRenameMoveCorrectionTurnReport] = []

    for name, text in (
        ("rename_then_move", "Rename file alpha.txt to beta.txt. Then move the new beta.txt into a folder called archive."),
        ("correction", "Actually put it in archive/old instead."),
    ):
        result = harness.send_text(text, timeout_s=timeout)
        alpha_exists, beta_exists, archive_beta_exists, archive_old_beta_exists, archive_old_beta_content = _snapshot(workspace)
        turns.append(
            FileRenameMoveCorrectionTurnReport(
                name=name,
                assistant_text=result.assistant_text,
                timed_out=result.timed_out,
                duration_s=result.duration_s,
                alpha_exists=alpha_exists,
                beta_exists=beta_exists,
                archive_beta_exists=archive_beta_exists,
                archive_old_beta_exists=archive_old_beta_exists,
                archive_old_beta_content=archive_old_beta_content,
            )
        )

    harness.close()

    final = turns[-1]
    final_reply = final.assistant_text.lower()
    success = (
        bool(boot.ready)
        and all(not turn.timed_out for turn in turns)
        and not final.alpha_exists
        and not final.beta_exists
        and not final.archive_beta_exists
        and final.archive_old_beta_exists
        and final.archive_old_beta_content == "alpha-body"
        and ("archive/old" in final_reply or "archive\\\\old" in final_reply)
        and "couldn't" not in final_reply
    )
    return FileRenameMoveCorrectionSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a rename-then-move chain can be corrected to a new destination without losing content."
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
        for turn in report.turns:
            print(f"{turn.name}: timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
            print(
                "  "
                f"alpha_exists={turn.alpha_exists} beta_exists={turn.beta_exists} "
                f"archive_beta_exists={turn.archive_beta_exists} "
                f"archive_old_beta_exists={turn.archive_old_beta_exists} "
                f"archive_old_beta_content={turn.archive_old_beta_content!r}"
            )
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
