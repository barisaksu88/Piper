from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness  # noqa: E402


@dataclass(frozen=True)
class ClarificationHarnessReport:
    success: bool
    assistant_text: str
    agent_log_tail: list[str]
    error_events: list[str]
    status_tail: list[str]
    kept_data_dir: str


def run_smoke() -> ClarificationHarnessReport:
    harness = PiperHarness(
        persist_turns=False,
        enable_memory_learning=True,
        isolated_data=True,
        keep_data_copy=True,
    )
    boot = harness.start()
    if not boot.ready:
        return ClarificationHarnessReport(
            success=False,
            assistant_text="",
            agent_log_tail=[],
            error_events=[f"boot_not_ready: {boot}"],
            status_tail=[],
            kept_data_dir="",
        )

    try:
        harness.send_text("Alright, again, let's start everything you know about me.", timeout_s=180.0)
        harness.send_text("That's not the hall of it.", timeout_s=180.0)
        result = harness.send_text("A temporary tree.", timeout_s=180.0)

        agent_logs = [
            str(event.get("payload") or "")
            for event in result.ui_events
            if str(event.get("kind") or "") == "agent_log"
        ]
        error_events = [
            str(event.get("payload") or "")
            for event in result.ui_events
            if str(event.get("kind") or "") == "error"
        ]
        assistant_text = str(result.assistant_text or "")
        assistant_lower = assistant_text.lower()
        log_tail = agent_logs[-8:]
        status_tail = list(result.status_history[-6:])

        asked_for_clarification = "?" in assistant_text and any(
            phrase in assistant_lower
            for phrase in (
                "what did you mean",
                "what exactly did you want",
                "what did you want me to",
                "clarify",
            )
        )
        no_old_failure_narration = not any(
            phrase in assistant_lower
            for phrase in (
                "file operation",
                "search for a file named",
                "could not be compiled",
                "profile has failed",
            )
        )
        saw_route_pause_log = any("clarification pause" in item.lower() for item in agent_logs)
        saw_safe_chat_route = any("-> route: chat" in item.lower() for item in agent_logs)

        success = bool(
            asked_for_clarification
            and no_old_failure_narration
            and (saw_route_pause_log or saw_safe_chat_route)
            and not error_events
            and not result.timed_out
        )
        kept_data_dir = ""
    finally:
        harness.close()
        kept_data_dir = str(harness.kept_data_dir or kept_data_dir)

    return ClarificationHarnessReport(
        success=success,
        assistant_text=assistant_text,
        agent_log_tail=log_tail,
        error_events=error_events,
        status_tail=status_tail,
        kept_data_dir=kept_data_dir,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
