from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from config import data_state_path
from scripts.computer_use_fixture_server import running_fixture_server


@dataclass(frozen=True)
class PlaywrightLocalhostDownloadFollowupHarnessReport:
    ready: bool
    success: bool
    first_timed_out: bool
    second_timed_out: bool
    first_assistant_text: str
    second_assistant_text: str
    second_outcome: str
    second_stage_type: str
    second_stage_goal: str
    downloaded_rel: str


def _stats_lines(data_dir) -> list[dict]:
    stats_path = data_dir / "stats.jsonl"
    if not stats_path.exists():
        return []
    rows: list[dict] = []
    for line in stats_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def run_smoke(*, timeout: float, keep_data_copy: bool) -> PlaywrightLocalhostDownloadFollowupHarnessReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    first_assistant_text = ""
    second_assistant_text = ""
    first_timed_out = True
    second_timed_out = True
    second_outcome = ""
    second_stage_type = ""
    second_stage_goal = ""
    downloaded_rel = ""
    try:
        if boot.ready:
            with running_fixture_server() as base_url:
                first = harness.send_text(
                    f"Open {base_url}/download_hub.html in the browser and tell me the page title.",
                    timeout_s=timeout,
                )
                second = harness.send_text(
                    "download the quarterly report into browser_downloads_followup",
                    timeout_s=timeout,
                )
                first_assistant_text = first.assistant_text
                second_assistant_text = second.assistant_text
                first_timed_out = first.timed_out
                second_timed_out = second.timed_out
                maybe_download = harness.data_dir / "workspace" / "browser_downloads_followup" / "quarterly-report.pdf"
                if maybe_download.exists():
                    downloaded_rel = "browser_downloads_followup/quarterly-report.pdf"

            stats = _stats_lines(harness.data_dir)
            if len(stats) >= 2:
                second_stats = stats[-1]
                second_outcome = str(second_stats.get("outcome") or "")
                stages = second_stats.get("stages") or []
                stage = stages[0] if stages and isinstance(stages[0], dict) else {}
                second_stage_type = str(stage.get("stage_type") or "")
                second_stage_goal = str(stage.get("stage_goal") or "")
    finally:
        harness.close()

    first_lower = first_assistant_text.lower()
    second_lower = second_assistant_text.lower()
    success = (
        bool(boot.ready)
        and not first_timed_out
        and not second_timed_out
        and "download hub fixture" in first_lower
        and second_outcome == "VERIFIED"
        and second_stage_type == "COMPUTER_USE"
        and "quarterly report" in second_stage_goal.lower()
        and "browser_downloads_followup" in second_stage_goal
        and "browser_downloads_followup/quarterly-report.pdf" in second_lower
        and "quarterly report" in second_lower
        and "release notes" not in second_lower
        and "sha256" not in second_lower
        and downloaded_rel == "browser_downloads_followup/quarterly-report.pdf"
    )
    return PlaywrightLocalhostDownloadFollowupHarnessReport(
        ready=bool(boot.ready),
        success=bool(success),
        first_timed_out=bool(first_timed_out),
        second_timed_out=bool(second_timed_out),
        first_assistant_text=first_assistant_text,
        second_assistant_text=second_assistant_text,
        second_outcome=second_outcome,
        second_stage_type=second_stage_type,
        second_stage_goal=second_stage_goal,
        downloaded_rel=downloaded_rel,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an active browser page can handle a follow-up request to download a specific artifact."
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"FIRST_TIMED_OUT: {report.first_timed_out}")
        print(f"SECOND_TIMED_OUT: {report.second_timed_out}")
        print(f"SECOND_OUTCOME: {report.second_outcome}")
        print(f"SECOND_STAGE_TYPE: {report.second_stage_type}")
        print(f"SECOND_STAGE_GOAL: {report.second_stage_goal}")
        print(f"DOWNLOADED_REL: {report.downloaded_rel}")
        print(f"FIRST_ASSISTANT: {report.first_assistant_text}")
        print(f"SECOND_ASSISTANT: {report.second_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
