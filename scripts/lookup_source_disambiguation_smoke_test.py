from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness


@dataclass(frozen=True)
class LookupSourceDisambiguationReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    clarification_text: str
    resolution_text: str
    clarification_status_history: list[str]
    resolution_status_history: list[str]
    clarification_timed_out: bool
    resolution_timed_out: bool


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
    (workspace / "grocery_list.txt").write_text("Apples\nBananas\nCarrots\n", encoding="utf-8")


def run_smoke(*, timeout: float, keep_data_copy: bool) -> LookupSourceDisambiguationReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    _seed_grocery_fixture(harness.data_dir / "workspace")
    boot = harness.start()
    clarification_text = ""
    resolution_text = ""
    clarification_status_history: list[str] = []
    resolution_status_history: list[str] = []
    clarification_timed_out = False
    resolution_timed_out = False
    try:
        clarification = harness.send_text("Search for grocery.", timeout_s=timeout)
        clarification_text = str(clarification.assistant_text or "")
        clarification_status_history = list(clarification.status_history)
        clarification_timed_out = bool(clarification.timed_out)

        resolution = harness.send_text("workspace files", timeout_s=timeout)
        resolution_text = str(resolution.assistant_text or "")
        resolution_status_history = list(resolution.status_history)
        resolution_timed_out = bool(resolution.timed_out)
    finally:
        harness.close()

    clarification_lower = clarification_text.lower()
    resolution_lower = resolution_text.lower()
    asked_source_question = (
        "?" in clarification_text
        and "web" in clarification_lower
        and "workspace" in clarification_lower
    )
    resolved_to_workspace_lookup = (
        "grocery_list.txt" in resolution_lower
        and "web" not in resolution_lower
        and "search the web" not in resolution_lower
    )
    success = bool(boot.ready) and not clarification_timed_out and not resolution_timed_out and asked_source_question and resolved_to_workspace_lookup
    return LookupSourceDisambiguationReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        clarification_text=clarification_text,
        resolution_text=resolution_text,
        clarification_status_history=clarification_status_history,
        resolution_status_history=resolution_status_history,
        clarification_timed_out=clarification_timed_out,
        resolution_timed_out=resolution_timed_out,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that ambiguous lookup requests trigger a web-vs-workspace clarification and resolve cleanly on the next turn.")
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
        print(f"CLARIFICATION_TIMED_OUT: {report.clarification_timed_out}")
        print(f"RESOLUTION_TIMED_OUT: {report.resolution_timed_out}")
        print(f"CLARIFICATION: {report.clarification_text}")
        print(f"RESOLUTION: {report.resolution_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
