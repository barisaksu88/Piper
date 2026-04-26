from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import ui.controller as controller_module  # noqa: E402
import ui.controller_actions as controller_actions  # noqa: E402
from ui.controller import PiperController  # noqa: E402


class FakeDpg:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.config: dict[str, dict[str, object]] = {}
        self.labels: dict[str, str] = {}
        self.focused: list[str] = []

    def does_item_exist(self, tag) -> bool:  # noqa: ANN001
        del tag
        return True

    def configure_item(self, tag, **kwargs) -> None:  # noqa: ANN001
        self.config.setdefault(str(tag), {}).update(kwargs)

    def set_item_label(self, tag, label: str) -> None:  # noqa: ANN001
        self.labels[str(tag)] = str(label)

    def get_value(self, tag):  # noqa: ANN001
        return self.values.get(str(tag), "")

    def set_value(self, tag, value) -> None:  # noqa: ANN001
        self.values[str(tag)] = str(value or "")

    def focus_item(self, tag) -> None:  # noqa: ANN001
        self.focused.append(str(tag))


class FakeQueue:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def put(self, item) -> None:  # noqa: ANN001
        self.events.append(item)


class FakePipeline:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def handle_event(self, kind: str, text: str, **kwargs) -> None:
        del kwargs
        self.events.append((kind, text))


class FakeTts:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _tags() -> SimpleNamespace:
    return SimpleNamespace(
        input_box="input_box",
        send_button="send_button",
        clear_session_button="clear_session_button",
        mic_button="mic_button",
        restart_button="restart_button",
        snapshot_button="snapshot_button",
        live_screen_mode_combo="live_screen_mode_combo",
        live_screen_interval_combo="live_screen_interval_combo",
        event_speech_combo="event_speech_combo",
        ingest_button="ingest_button",
        stop_button="stop_button",
        code_input_box="code_input_box",
        code_send_button="code_send_button",
        code_run_button="code_run_button",
        code_stop_button="code_stop_button",
        code_clear_button="code_clear_button",
    )


class RefreshController:
    def __init__(self) -> None:
        self.tags = _tags()
        self.boot_ready = True
        self.live_screen_pending = False
        self.document_ingest_active = False
        self._last_tts_busy = False

    def has_active_operations(self) -> bool:
        return True

    def has_active_code_session(self) -> bool:
        return False

    def is_tts_active(self) -> bool:
        return False

    def current_code_preview_runnable_path(self) -> str:
        return ""


class SendController:
    def __init__(self) -> None:
        self.tags = _tags()
        self.boot_ready = True
        self.ui_queue = FakeQueue()
        self.pipeline = FakePipeline()
        self.tts = FakeTts()
        self.canceled_calls = 0
        self.statuses: list[str] = []
        self.chat_messages: list[tuple[str, str]] = []

    def has_active_operations(self) -> bool:
        return True

    def cancel_active_operations(self, reason: str = "Stopped by user.") -> bool:
        del reason
        self.canceled_calls += 1
        return True

    def stop_code_session(self) -> bool:
        return False

    def is_tts_active(self) -> bool:
        return False

    def set_status(self, status: str) -> None:
        self.statuses.append(str(status))

    def chat_append(self, role: str, content: str) -> None:
        self.chat_messages.append((str(role), str(content)))


@dataclass(frozen=True)
class UiActiveCancelSmokeReport:
    success: bool
    input_enabled_while_active: bool
    send_enabled_while_active: bool
    send_label: str
    input_hint: str
    clear_disabled_while_active: bool
    stop_button_enabled: bool
    typed_cancel_stopped: bool
    busy_text_did_not_start_new_turn: bool


def main() -> int:
    fake_dpg = FakeDpg()
    original_controller_dpg = controller_module.dpg
    original_actions_dpg = controller_actions.dpg
    try:
        controller_module.dpg = fake_dpg
        controller_actions.dpg = fake_dpg

        refresh_controller = RefreshController()
        PiperController.refresh_interaction_state(refresh_controller)

        fake_dpg.values["input_box"] = "please stop"
        send_controller = SendController()
        controller_actions.on_send(send_controller)

        fake_dpg.values["input_box"] = "new request while busy"
        busy_controller = SendController()
        controller_actions.on_send(busy_controller)

        input_config = fake_dpg.config.get("input_box", {})
        send_config = fake_dpg.config.get("send_button", {})
        clear_config = fake_dpg.config.get("clear_session_button", {})
        stop_config = fake_dpg.config.get("stop_button", {})
        report = UiActiveCancelSmokeReport(
            success=False,
            input_enabled_while_active=input_config.get("enabled") is True,
            send_enabled_while_active=send_config.get("enabled") is True,
            send_label=fake_dpg.labels.get("send_button", ""),
            input_hint=str(input_config.get("hint") or ""),
            clear_disabled_while_active=clear_config.get("enabled") is False,
            stop_button_enabled=stop_config.get("enabled") is True,
            typed_cancel_stopped=(
                send_controller.canceled_calls == 1
                and ("status_widget_dashboard_activity", "Stop requested.") in send_controller.ui_queue.events
                and ("cancel", "Canceled") in send_controller.pipeline.events
                and send_controller.statuses[-1:] == ["Stopping..."]
            ),
            busy_text_did_not_start_new_turn=(
                busy_controller.canceled_calls == 0
                and busy_controller.chat_messages
                and busy_controller.chat_messages[-1][0] == "system"
                and "Piper is busy" in busy_controller.chat_messages[-1][1]
            ),
        )
        success = (
            report.input_enabled_while_active
            and report.send_enabled_while_active
            and report.send_label == "Stop"
            and "stop/cancel" in report.input_hint
            and report.clear_disabled_while_active
            and report.stop_button_enabled
            and report.typed_cancel_stopped
            and report.busy_text_did_not_start_new_turn
        )
        report = UiActiveCancelSmokeReport(**{**asdict(report), "success": bool(success)})
    finally:
        controller_module.dpg = original_controller_dpg
        controller_actions.dpg = original_actions_dpg

    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
