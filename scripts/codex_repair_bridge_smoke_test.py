from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.codex_bridge import CodexRepairCoordinator  # noqa: E402


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="piper-codex-repair-"))
    data_dir = temp_root / "data"
    (data_dir / "state").mkdir(parents=True, exist_ok=True)
    (data_dir / "debug").mkdir(parents=True, exist_ok=True)
    (data_dir / "reference").mkdir(parents=True, exist_ok=True)

    escalation_log_path = data_dir / "debug" / "codex_escalations.jsonl"
    escalation_payload = {
        "timestamp_utc": "2026-03-11T00:00:00+00:00",
        "manual": False,
        "source": "runtime",
        "reason": "verification_block",
        "summary": "FILE_WORK verification is blocked repeatedly.",
        "note": "",
        "status_snapshot": "FAILED",
        "user_msg": "Please retry the grocery list edit.",
        "route_decision": {"decision": "TASK"},
        "context_card": {"goal": "Fix the grocery list file flow."},
        "history_tail": [{"role": "user", "content": "Please retry the grocery list edit."}],
        "recent_signals": [{"kind": "verification_block", "severity": "warning"}],
        "trigger_signal": {"kind": "verification_block", "severity": "warning"},
        "scratchpad_tail": ["FILE_CHECKER_VERDICT: FAILED"],
        "monitor_tail": ["[ENGINEERING SIGNAL] blocked"],
        "dashboard_tail": ["Codex support brief prepared."],
    }
    escalation_log_path.write_text(json.dumps(escalation_payload, ensure_ascii=False) + "\n", encoding="utf-8")

    previous_simulate = os.environ.get("PIPER_CODEX_REPAIR_SIMULATE")
    os.environ["PIPER_CODEX_REPAIR_SIMULATE"] = "fixed"
    try:
        coordinator = CodexRepairCoordinator(
            repo_root=ROOT_DIR,
            data_dir=data_dir,
            auto_enabled=True,
            poll_interval_s=0.05,
        )
        start_result = coordinator.request_repair(
            {
                "decision": "ask_codex",
                "reason": "verification_block",
                "summary": "FILE_WORK verification is blocked repeatedly.",
                "brief_path": str(escalation_log_path),
                "manual": False,
                "signal_count": 2,
                "trigger_kind": "verification_block",
            }
        )
        if not bool(start_result.get("accepted")):
            raise AssertionError(f"Repair request was not accepted: {start_result}")

        deadline = time.time() + 20.0
        final_status: dict[str, object] = {}
        while time.time() < deadline:
            final_status = coordinator.current_status()
            if str(final_status.get("state") or "") == "restart_requested":
                break
            time.sleep(0.1)
        if str(final_status.get("state") or "") != "restart_requested":
            raise AssertionError(f"Repair worker did not reach restart_requested: {final_status}")

        recovery = coordinator.peek_recovery()
        if str(recovery.get("retry_user_message") or "") != "Please retry the grocery list edit.":
            raise AssertionError(f"Unexpected recovery payload: {recovery}")

        consumed = coordinator.consume_recovery()
        if str(consumed.get("request_id") or "") != str(recovery.get("request_id") or ""):
            raise AssertionError("Recovery consume did not return the pending payload.")

        resumed_status = coordinator.current_status()
        if str(resumed_status.get("state") or "") != "resumed":
            raise AssertionError(f"Expected resumed status after consume, got: {resumed_status}")

        print(
            json.dumps(
                {
                    "success": True,
                    "accepted": start_result,
                    "final_status": final_status,
                    "recovery": recovery,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        if previous_simulate is None:
            os.environ.pop("PIPER_CODEX_REPAIR_SIMULATE", None)
        else:
            os.environ["PIPER_CODEX_REPAIR_SIMULATE"] = previous_simulate
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
