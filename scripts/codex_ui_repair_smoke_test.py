from __future__ import annotations

import json
import os
import queue
import shutil
import tempfile
import time
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.codex_bridge import CodexRepairCoordinator  # noqa: E402
from ui.controller import PiperController  # noqa: E402


class _DummyController:
    def __init__(self, *, data_dir: Path) -> None:
        self.codex_repair = CodexRepairCoordinator(
            repo_root=ROOT_DIR,
            data_dir=data_dir,
            auto_enabled=True,
            poll_interval_s=0.05,
        )
        self._last_codex_status_line = ""
        self.ui_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.restart_requested = False
        self.boot_ready = True
        self.pending_codex_recovery = self.codex_repair.peek_recovery()
        self.monitor_lines: list[str] = []
        self.chat_events: list[tuple[str, str]] = []
        self.submitted_user_texts: list[str] = []

    def log_agent_monitor(self, text: str) -> None:
        self.monitor_lines.append(str(text))

    def chat_append(self, role: str, text: str) -> None:
        self.chat_events.append((str(role), str(text)))

    def on_restart(self) -> None:
        self.restart_requested = True

    def has_active_operations(self) -> bool:
        return False

    def submit_user_text(self, user_text: str) -> None:
        self.submitted_user_texts.append(str(user_text))


def _drain_queue(items: queue.Queue[tuple[str, str]]) -> list[tuple[str, str]]:
    drained: list[tuple[str, str]] = []
    while True:
        try:
            drained.append(items.get_nowait())
        except queue.Empty:
            return drained


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="piper-codex-ui-repair-"))
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
        "status_snapshot": "FAILED",
        "user_msg": "Please retry the grocery list edit.",
        "route_decision": {"decision": "TASK"},
        "context_card": {"goal": "Fix the grocery list file flow."},
        "recent_signals": [{"kind": "verification_block", "severity": "warning"}],
        "trigger_signal": {"kind": "verification_block", "severity": "warning"},
        "scratchpad_tail": ["FILE_CHECKER_VERDICT: FAILED"],
    }
    escalation_log_path.write_text(json.dumps(escalation_payload, ensure_ascii=False) + "\n", encoding="utf-8")

    previous_simulate = os.environ.get("PIPER_CODEX_REPAIR_SIMULATE")
    os.environ["PIPER_CODEX_REPAIR_SIMULATE"] = "fixed"
    try:
        controller = _DummyController(data_dir=data_dir)
        escalation = {
            "decision": "ask_codex",
            "reason": "verification_block",
            "summary": "FILE_WORK verification is blocked repeatedly.",
            "brief_path": str(escalation_log_path),
            "manual": False,
            "signal_count": 2,
            "trigger_kind": "verification_block",
        }

        PiperController.queue_codex_repair(controller, escalation)

        deadline = time.time() + 20.0
        while time.time() < deadline and not controller.restart_requested:
            PiperController.poll_codex_repair(controller)
            time.sleep(0.1)
        if not controller.restart_requested:
            raise AssertionError("Controller poll path never requested a restart.")

        if not any("Engineering repair verified. Restarting Piper" in text for _, text in controller.chat_events):
            raise AssertionError(f"Restart chat event missing: {controller.chat_events}")

        recovery = controller.codex_repair.peek_recovery()
        if str(recovery.get("retry_user_message") or "") != "Please retry the grocery list edit.":
            raise AssertionError(f"Unexpected recovery payload: {recovery}")
        boot_recovery_payload = dict(recovery)

        controller.pending_codex_recovery = recovery
        PiperController.resume_codex_recovery_if_needed(controller)

        if controller.submitted_user_texts != ["Please retry the grocery list edit."]:
            raise AssertionError(f"Retry submission mismatch: {controller.submitted_user_texts}")

        final_status = controller.codex_repair.current_status()
        if str(final_status.get("state") or "") != "resumed":
            raise AssertionError(f"Expected resumed status after recovery, got: {final_status}")

        drained = _drain_queue(controller.ui_queue)
        if not any(kind == "status_widget_dashboard_activity" for kind, _ in drained):
            raise AssertionError(f"Expected dashboard activity updates, got: {drained}")

        # Fresh-boot recovery path must not request another restart when a
        # verified recovery payload is already waiting on disk.
        controller.codex_repair.store.save_recovery(boot_recovery_payload)
        controller.codex_repair.store.save_status(
            {
                "request_id": str(boot_recovery_payload.get("request_id") or ""),
                "state": "restart_requested",
                "message": "Repair verified. Restarting Piper to resume the interrupted request.",
                "updated_at_utc": "2026-03-13T20:28:48+00:00",
                "restart_requested": True,
            }
        )
        rebooted_controller = _DummyController(data_dir=data_dir)
        rebooted_controller.boot_ready = False
        PiperController.poll_codex_repair(rebooted_controller)
        if rebooted_controller.restart_requested:
            raise AssertionError("Boot replay should not trigger a second restart when recovery is pending.")
        rebooted_controller.boot_ready = True
        PiperController.resume_codex_recovery_if_needed(rebooted_controller)
        if rebooted_controller.submitted_user_texts != ["Please retry the grocery list edit."]:
            raise AssertionError(
                f"Boot recovery did not hand the retry back correctly: {rebooted_controller.submitted_user_texts}"
            )

        print(
            json.dumps(
                {
                    "success": True,
                    "restart_requested": controller.restart_requested,
                    "boot_replay_restart_requested": rebooted_controller.restart_requested,
                    "monitor_lines": controller.monitor_lines[-4:],
                    "chat_events": controller.chat_events[-3:],
                    "submitted_user_texts": controller.submitted_user_texts,
                    "final_status": final_status,
                    "ui_events": drained[-4:],
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
