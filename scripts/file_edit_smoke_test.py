from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness

EDIT_TURNS = (
    ("remove_item", "Remove 'eggs' from the grocery list file."),
    ("remove_item_again", "Remove 'Eggs' from the grocery list file again."),
    ("read_updated_file", "Read grocery_list.txt and tell me its exact contents only."),
)

INITIAL_ITEMS = ("Apples", "Bananas", "Carrots", "Dairy milk", "Eggs")
UPDATED_ITEMS = ("Apples", "Bananas", "Carrots", "Dairy milk")


@dataclass(frozen=True)
class EditTurnReport:
    name: str
    timed_out: bool
    duration_s: float
    assistant_text: str
    status_history: list[str]
    file_content: str
    passed: bool


@dataclass(frozen=True)
class EditSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[EditTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _seed_grocery_fixture(workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    grocery_path = workspace / "grocery_list.txt"
    grocery_path.write_text("\n".join(INITIAL_ITEMS) + "\n", encoding="utf-8")
    return grocery_path


def _normalize_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _turn_passed(name: str, assistant_text: str, file_content: str, timed_out: bool) -> bool:
    if timed_out:
        return False
    lines = _normalize_lines(file_content)
    text_l = (assistant_text or "").lower()
    if name == "remove_item":
        return (
            bool((assistant_text or "").strip())
            and
            lines == list(UPDATED_ITEMS)
            and "eggs" not in file_content.lower()
            and "bread" not in text_l
            and "error" not in text_l
            and "failed" not in text_l
        )
    if name == "remove_item_again":
        return (
            bool((assistant_text or "").strip())
            and
            lines == list(UPDATED_ITEMS)
            and "eggs remain" not in text_l
            and "failed" not in text_l
            and "error" not in text_l
            and "bread" not in text_l
        )
    if name == "read_updated_file":
        return lines == list(UPDATED_ITEMS) and all(item.lower() in text_l for item in ("apples", "bananas", "carrots", "dairy milk")) and "eggs" not in text_l
    return False


def run_smoke(*, timeout: float, keep_data_copy: bool) -> EditSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    grocery_path = _seed_grocery_fixture(harness.data_dir / "workspace")
    boot = harness.start()
    turns: list[EditTurnReport] = []
    try:
        for name, text in EDIT_TURNS:
            result = harness.send_text(text, timeout_s=timeout)
            file_content = grocery_path.read_text(encoding="utf-8") if grocery_path.exists() else ""
            turns.append(
                EditTurnReport(
                    name=name,
                    timed_out=result.timed_out,
                    duration_s=result.duration_s,
                    assistant_text=result.assistant_text,
                    status_history=list(result.status_history),
                    file_content=file_content,
                    passed=_turn_passed(name, result.assistant_text, file_content, result.timed_out),
                )
            )
    finally:
        harness.close()
    return EditSmokeReport(
        ready=bool(boot.ready),
        success=bool(boot.ready) and all(turn.passed for turn in turns),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic FILE_WORK content-edit smoke test through the isolated Piper harness.")
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
            print(f"  file_content={turn.file_content!r}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
