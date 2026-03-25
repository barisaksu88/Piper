from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness

LOOKUP_TURNS = (
    ("read_named_list", "Can you tell me what it says in the grocery list?"),
    ("short_pronoun_followup", "What's in it?"),
    ("read_file_followup", "Yes, but what's in the file?"),
    ("read_it_back", "Read it back."),
    ("recheck_name_mismatch", "I think we already have a document, the naming might not be matching, please check again."),
    ("search_fragment", "Maybe just search for grocery?"),
    ("pronoun_followup", "Yes, can you read what's in it?"),
)

EXPECTED_ITEMS = ("Apples", "Bananas", "Carrots", "Dairy milk", "Eggs")


@dataclass(frozen=True)
class LookupTurnReport:
    name: str
    timed_out: bool
    duration_s: float
    assistant_text: str
    status_history: list[str]
    passed: bool


@dataclass(frozen=True)
class LookupSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[LookupTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _seed_grocery_fixture(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    grocery_path = workspace / "grocery_list.txt"
    grocery_path.write_text("\n".join(EXPECTED_ITEMS) + "\n", encoding="utf-8")


def _contains_expected_items(text: str) -> bool:
    return all(item.lower() in (text or "").lower() for item in EXPECTED_ITEMS)


def _turn_passed(name: str, assistant_text: str, timed_out: bool) -> bool:
    if timed_out:
        return False
    text = assistant_text or ""
    text_l = text.lower()
    if name in {"read_named_list", "short_pronoun_followup", "read_file_followup", "read_it_back"}:
        return _contains_expected_items(text) and "no file named" not in text_l
    if name == "pronoun_followup":
        return _contains_expected_items(text) and "zero matches" not in text_l and "what's in it" not in text_l
    if name in {"recheck_name_mismatch", "search_fragment"}:
        return "grocery_list.txt" in text_l and "no results" not in text_l
    return False


def run_smoke(*, timeout: float, keep_data_copy: bool) -> LookupSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    _seed_grocery_fixture(harness.data_dir / "workspace")
    boot = harness.start()
    turns: list[LookupTurnReport] = []
    try:
        for name, text in LOOKUP_TURNS:
            result = harness.send_text(text, timeout_s=timeout)
            turns.append(
                LookupTurnReport(
                    name=name,
                    timed_out=result.timed_out,
                    duration_s=result.duration_s,
                    assistant_text=result.assistant_text,
                    status_history=list(result.status_history),
                    passed=_turn_passed(name, result.assistant_text, result.timed_out),
                )
            )
    finally:
        harness.close()
    return LookupSmokeReport(
        ready=bool(boot.ready),
        success=bool(boot.ready) and all(turn.passed for turn in turns),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a grocery-list filename lookup/read smoke test through the isolated Piper harness.")
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
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
