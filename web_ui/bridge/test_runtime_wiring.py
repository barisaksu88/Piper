"""web_ui.bridge.test_runtime_wiring

Deterministic pytest suite for Phase 3 runtime wiring:
- config defaults and env overrides
- app.py branch logic
- controller.run_web lifecycle
- web action dispatch
- DPG-safety audit for Web mode dispatch
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from config import Config, CFG


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_web_ui_enabled_defaults_false(self) -> None:
        cfg = Config()
        assert cfg.WEB_UI_ENABLED is False

    def test_web_ui_host_defaults_loopback(self) -> None:
        cfg = Config()
        assert cfg.WEB_UI_HOST == "127.0.0.1"

    def test_web_ui_port_defaults_8787(self) -> None:
        cfg = Config()
        assert cfg.WEB_UI_PORT == 8787

    def test_web_ui_ws_path_defaults_ws(self) -> None:
        cfg = Config()
        assert cfg.WEB_UI_WS_PATH == "/ws"


class TestConfigEnvOverrides:
    def test_web_ui_enabled_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPER_WEB_UI_ENABLED", "true")
        cfg = Config()
        assert cfg.WEB_UI_ENABLED is True

    def test_web_ui_port_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPER_WEB_UI_PORT", "8788")
        cfg = Config()
        assert cfg.WEB_UI_PORT == 8788

    def test_web_ui_host_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPER_WEB_UI_HOST", "localhost")
        cfg = Config()
        assert cfg.WEB_UI_HOST == "localhost"

    def test_web_ui_ws_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPER_WEB_UI_WS_PATH", "/bridge")
        cfg = Config()
        assert cfg.WEB_UI_WS_PATH == "/bridge"


# ---------------------------------------------------------------------------
# app.py branch tests
# ---------------------------------------------------------------------------


class TestAppBranch:
    def test_app_uses_run_when_web_ui_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app as app_module

        calls: list[tuple[str, dict[str, Any]]] = []

        class FakeController:
            def run(self) -> int:
                calls.append(("run", {}))
                return 0

            def run_web(self, **kwargs: Any) -> int:
                calls.append(("run_web", kwargs))
                return 0

        monkeypatch.setattr(app_module, "build_controller", lambda: FakeController())
        monkeypatch.setattr(app_module.CFG, "WEB_UI_ENABLED", False)

        result = app_module.main()
        assert result == 0
        assert calls == [("run", {})]

    def test_app_uses_run_web_when_web_ui_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app as app_module

        calls: list[tuple[str, dict[str, Any]]] = []

        class FakeController:
            def run(self) -> int:
                calls.append(("run", {}))
                return 0

            def run_web(self, **kwargs: Any) -> int:
                calls.append(("run_web", kwargs))
                return 0

        monkeypatch.setattr(app_module, "build_controller", lambda: FakeController())
        monkeypatch.setattr(app_module.CFG, "WEB_UI_ENABLED", True)

        result = app_module.main()
        assert result == 0
        assert len(calls) == 1
        assert calls[0][0] == "run_web"
        assert calls[0][1]["host"] == "127.0.0.1"
        assert calls[0][1]["port"] == 8787
        assert calls[0][1]["ws_path"] == "/ws"


# ---------------------------------------------------------------------------
# Controller dispatch tests (MagicMock-based)
# ---------------------------------------------------------------------------


def _make_mock_controller() -> MagicMock:
    """Return a MagicMock wired with the real _dispatch_web_action method."""
    from ui.controller import PiperController

    ctrl = MagicMock()
    ctrl.ui_queue = queue.Queue()
    ctrl.restart_requested = False
    ctrl.boot_ready = True
    ctrl.live_screen = MagicMock()
    ctrl.boot_mgr = MagicMock()
    ctrl.has_active_code_session = MagicMock(return_value=True)
    ctrl.has_active_operations = MagicMock(return_value=False)
    ctrl.code_session_active = False
    ctrl._pending_input_modality = "typed"

    # Bind the real dispatch method so we test the real wiring.
    ctrl._dispatch_web_action = PiperController._dispatch_web_action.__get__(  # type: ignore[method-assign]
        ctrl, MagicMock
    )
    return ctrl


class TestWebActionDispatch:
    def test_send_message_calls_submit_user_text(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("send_message", {"text": "hello world"})
        ctrl.submit_user_text.assert_called_once_with("hello world")

    def test_stop_calls_on_stop(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("stop", {})
        ctrl.on_stop.assert_called_once_with()

    def test_new_session_calls_on_new_session(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("new_session", {})
        ctrl.on_new_session.assert_called_once_with()

    def test_clear_chat_calls_on_clear(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("clear_chat", {})
        ctrl.on_clear.assert_called_once_with()

    def test_snapshot_toggle_calls_on_snapshot(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("snapshot_toggle", {})
        ctrl.on_snapshot.assert_called_once_with()

    def test_event_speech_mode_calls_set_event_speech_mode(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("event_speech_mode", {"mode": "noisy"})
        ctrl.set_event_speech_mode.assert_called_once_with("noisy", announce=True)

    def test_restart_piper_sets_restart_requested(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("restart_piper", {})
        assert ctrl.restart_requested is True
        ctrl.boot_mgr.shutdown.assert_called_once_with()
        ctrl.set_status.assert_called_once_with("Restarting...")

    def test_open_document_picker_deferred(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("open_document_picker", {})
        kind, payload = ctrl.ui_queue.get_nowait()
        assert kind == "chat_append"
        assert "frontend-owned" in payload["content"]

    def test_document_picker_cancel_no_op(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("document_picker_cancel", {})
        assert ctrl.ui_queue.empty()

    def test_code_send_with_active_session(self) -> None:
        ctrl = _make_mock_controller()
        ctrl.has_active_code_session.return_value = True
        ctrl._dispatch_web_action("code_send", {"text": "print(1)"})
        ctrl.send_code_session_input.assert_called_once_with("print(1)")

    def test_code_send_without_active_session_does_nothing(self) -> None:
        ctrl = _make_mock_controller()
        ctrl.has_active_code_session.return_value = False
        ctrl._dispatch_web_action("code_send", {"text": "print(1)"})
        ctrl.send_code_session_input.assert_not_called()

    def test_code_run_with_path(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("code_run", {"path": "test.py"})
        ctrl.start_code_session.assert_called_once_with("test.py")

    def test_code_run_without_path_warns(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("code_run", {})
        kind, payload = ctrl.ui_queue.get_nowait()
        assert kind == "chat_append"
        assert "requires a path" in payload["content"]

    def test_code_clear_calls_on_code_clear(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("code_clear", {})
        ctrl.on_code_clear.assert_called_once_with()

    def test_live_screen_mode_sets_mode(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("live_screen_mode", {"mode": "pointer"})
        ctrl.live_screen.set_mode.assert_called_once_with("pointer")

    def test_live_screen_interval_sets_interval(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("live_screen_interval", {"interval_s": 5.0})
        ctrl.live_screen.set_interval.assert_called_once_with(5.0)

    def test_mic_toggle_deferred(self) -> None:
        ctrl = _make_mock_controller()
        ctrl._dispatch_web_action("mic_toggle", {})
        kind, payload = ctrl.ui_queue.get_nowait()
        assert kind == "chat_append"
        assert "not available" in payload["content"]


# ---------------------------------------------------------------------------
# run_web lifecycle tests
# ---------------------------------------------------------------------------


class TestRunWebLifecycle:
    def test_run_web_starts_and_stops_bridge(self) -> None:
        """run_web starts BridgeServer, consumes actions, and shuts down cleanly."""
        from ui.controller import PiperController

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.restart_requested = False
        ctrl.boot_mgr = MagicMock()
        ctrl.boot_mgr.run_sequence = MagicMock()
        ctrl.proactive_monitor = MagicMock()
        ctrl.agent_brain = MagicMock()
        ctrl.code_session = MagicMock()
        ctrl.searxng_service = None
        ctrl.load_memory_into_chat = MagicMock()
        ctrl.knowledge_mgr = MagicMock()
        ctrl._dispatch_web_action = PiperController._dispatch_web_action.__get__(  # type: ignore[method-assign]
            ctrl, MagicMock
        )

        # Bind the real run_web method.
        run_web_bound = PiperController.run_web.__get__(ctrl, MagicMock)  # type: ignore[var-annotated]

        # Start run_web in a background thread; stop it shortly after.
        result: list[int] = []

        def _runner() -> None:
            result.append(run_web_bound(host="127.0.0.1", port=8787, ws_path="/ws"))

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()

        # Give run_web time to start the bridge.
        time.sleep(0.3)

        # Verify bridge is running by connecting.
        import asyncio
        import websockets

        async def _poke() -> None:
            async with websockets.connect("ws://127.0.0.1:8787/ws") as ws:
                await ws.send(
                    json.dumps(
                        {"frame": "action", "action": "restart_piper", "payload": {}}
                    )
                )

        asyncio.run(_poke())

        # Wait for run_web to exit.
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "run_web did not exit after restart action"
        assert result == [85]  # RESTART_EXIT_CODE

        # Verify cleanup happened.
        ctrl.proactive_monitor.stop.assert_called_once()
        ctrl.agent_brain.shutdown.assert_called_once()
        ctrl.code_session.shutdown.assert_called_once()

    def test_run_web_does_not_call_pump_ui_queue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_web must never call pump_ui_queue (BridgeServer consumes ui_queue)."""
        from ui.controller import PiperController

        pump_calls: list[Any] = []
        original_pump = PiperController.pump_ui_queue

        def _tracking_pump(self: Any) -> None:
            pump_calls.append(True)
            original_pump(self)

        monkeypatch.setattr(PiperController, "pump_ui_queue", _tracking_pump)

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.restart_requested = False
        ctrl.boot_mgr = MagicMock()
        ctrl.boot_mgr.run_sequence = MagicMock()
        ctrl.proactive_monitor = MagicMock()
        ctrl.agent_brain = MagicMock()
        ctrl.code_session = MagicMock()
        ctrl.searxng_service = None
        ctrl.load_memory_into_chat = MagicMock()
        ctrl.knowledge_mgr = MagicMock()
        ctrl._dispatch_web_action = PiperController._dispatch_web_action.__get__(ctrl, MagicMock)  # type: ignore[method-assign]

        run_web_bound = PiperController.run_web.__get__(ctrl, MagicMock)  # type: ignore[var-annotated]

        def _runner() -> None:
            run_web_bound(host="127.0.0.1", port=8787, ws_path="/ws")

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        time.sleep(0.3)

        # Trigger exit via restart action.
        import asyncio
        import websockets

        async def _poke() -> None:
            async with websockets.connect("ws://127.0.0.1:8787/ws") as ws:
                await ws.send(
                    json.dumps(
                        {"frame": "action", "action": "restart_piper", "payload": {}}
                    )
                )

        asyncio.run(_poke())
        thread.join(timeout=5.0)

        assert not pump_calls, "pump_ui_queue was called during run_web"


# ---------------------------------------------------------------------------
# Hardening audit tests
# ---------------------------------------------------------------------------


def _make_realish_controller() -> MagicMock:
    """Return a MagicMock with real PiperController methods bound for DPG audit."""
    from ui.controller import PiperController

    ctrl = MagicMock()
    ctrl.ui_queue = queue.Queue()
    ctrl.restart_requested = False
    ctrl.boot_ready = True
    ctrl.tags = MagicMock()
    ctrl.tags.input_box = "input_box"
    ctrl.tags.event_speech_combo = "event_speech_combo"
    ctrl.tags.chat_text = "chat_text"
    ctrl.tags.code_view_text = "code_view_text"
    ctrl.gen_lock = threading.Lock()
    ctrl.chat_state = MagicMock()
    ctrl.chat_state.get_messages_snapshot.return_value = []
    ctrl.live_screen = MagicMock()
    ctrl.live_screen.is_enabled.return_value = False
    ctrl.boot_mgr = MagicMock()
    ctrl.pipeline = MagicMock()
    ctrl.tts = MagicMock()
    ctrl._chat_rendered_messages = []
    ctrl._chat_rendered_tags = []
    ctrl._chat_render_wrap_columns = None
    ctrl.event_speech_mode = "off"
    ctrl._event_speech_recent = {}
    ctrl.thinking_placeholder = "Thinking..."
    ctrl.session_meta = ""
    ctrl.stage_meta = ""
    ctrl.runtime_mode = "IDLE"
    ctrl.style_meta = ""
    ctrl.screen_meta = ""
    ctrl.code_session_meta = ""
    ctrl.user_meta = ""
    ctrl.document_ingest_active = False
    ctrl.live_screen_pending = False
    ctrl._pending_boot_ready = False
    ctrl._pending_boot_ready_payload = ""
    ctrl.width = 1450
    ctrl.height = 860

    # Bind real methods that web dispatch exercises.
    ctrl._dispatch_web_action = PiperController._dispatch_web_action.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.submit_user_text = PiperController.submit_user_text.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.set_event_speech_mode = PiperController.set_event_speech_mode.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl._refresh_top_bar = PiperController._refresh_top_bar.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl._refresh_chat_ui = PiperController._refresh_chat_ui.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.show_thinking_placeholder = PiperController.show_thinking_placeholder.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.refresh_text_view_height = PiperController.refresh_text_view_height.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.request_autoscroll = PiperController.request_autoscroll.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl._reset_chat_render_cache = PiperController._reset_chat_render_cache.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl._speak_event_notification = PiperController._speak_event_notification.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl._event_tts_profile = PiperController._event_tts_profile.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.refresh_interaction_state = PiperController.refresh_interaction_state.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.clear_code_output = PiperController.clear_code_output.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl._flush_autoscrolls = PiperController._flush_autoscrolls.__get__(ctrl, MagicMock)  # type: ignore[method-assign]
    ctrl.load_style_state = MagicMock()
    ctrl.do_generate_stream = lambda: None

    return ctrl


class TestSubmitUserTextExistence:
    def test_submit_user_text_exists_on_piper_controller(self) -> None:
        from ui.controller import PiperController

        assert hasattr(PiperController, "submit_user_text")
        assert callable(getattr(PiperController, "submit_user_text"))

    def test_web_send_message_calls_real_submit_user_text(self) -> None:
        """If submit_user_text were missing, binding would fail or dispatch would crash."""
        ctrl = _make_realish_controller()
        calls: list[str] = []

        def _tracking_submit(text: str) -> None:
            calls.append(text)

        # Replace the bound real method with a tracker.
        ctrl.submit_user_text = _tracking_submit  # type: ignore[method-assign]
        ctrl._dispatch_web_action("send_message", {"text": "hello"})
        assert calls == ["hello"]


class TestWebDispatchDpgSafety:
    """Monkeypatch DPG mutation functions to raise; verify web dispatch stays safe.

    DPG guards use ``dpg.does_item_exist()`` to skip widget mutations when widgets
    are absent. In Web mode there is no DearPyGui context, so all guards evaluate to
    False and no mutation function should be called. We simulate this by forcing
    ``does_item_exist`` to return False and making every mutation function raise.
    """

    @pytest.fixture
    def _ban_dpg_mutations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import dearpygui.dearpygui as dpg

        # Simulate "no DPG widgets exist" — the guard every mutation path uses.
        monkeypatch.setattr(dpg, "does_item_exist", lambda tag: False)

        # Any call to a DPG mutation function outside the guard should fail the test.
        banned = [
            "set_value",
            "delete_item",
            "set_item_label",
            "bind_item_theme",
            "configure_item",
            "focus_item",
            "stop_dearpygui",
            "set_y_scroll",
            "add_spacer",
            "add_input_text",
            "fit_axis_data",
            "load_image",
            "add_static_texture",
            "add_image",
            "get_value",
            "get_item_rect_size",
            "get_y_scroll_max",
        ]
        for name in banned:
            if hasattr(dpg, name):
                monkeypatch.setattr(
                    dpg,
                    name,
                    lambda *a, _name=name, **k: (_ for _ in ()).throw(
                        AssertionError(
                            f"Banned DPG function '{_name}' called in web mode"
                        )
                    ),
                )

    def test_send_message_no_unsafe_dpg(self, _ban_dpg_mutations: None) -> None:
        ctrl = _make_realish_controller()
        ctrl._dispatch_web_action("send_message", {"text": "safe text"})

    def test_stop_no_unsafe_dpg(self, _ban_dpg_mutations: None) -> None:
        ctrl = _make_realish_controller()
        ctrl._dispatch_web_action("stop", {})

    def test_new_session_no_unsafe_dpg(self, _ban_dpg_mutations: None) -> None:
        ctrl = _make_realish_controller()
        ctrl._dispatch_web_action("new_session", {})

    def test_clear_chat_no_unsafe_dpg(self, _ban_dpg_mutations: None) -> None:
        ctrl = _make_realish_controller()
        ctrl._dispatch_web_action("clear_chat", {})

    def test_code_clear_no_unsafe_dpg(self, _ban_dpg_mutations: None) -> None:
        ctrl = _make_realish_controller()
        ctrl._dispatch_web_action("code_clear", {})

    def test_restart_piper_no_unsafe_dpg(self, _ban_dpg_mutations: None) -> None:
        ctrl = _make_realish_controller()
        ctrl._dispatch_web_action("restart_piper", {})


class TestWebDispatchNeverCallsPumpUiQueue:
    def test_dispatch_actions_do_not_call_pump_ui_queue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_dispatch_web_action must never call pump_ui_queue."""
        from ui.controller import PiperController

        pump_calls: list[Any] = []
        original_pump = PiperController.pump_ui_queue

        def _tracking_pump(self: Any) -> None:
            pump_calls.append(True)
            original_pump(self)

        monkeypatch.setattr(PiperController, "pump_ui_queue", _tracking_pump)

        ctrl = _make_mock_controller()
        for action, payload in [
            ("send_message", {"text": "hi"}),
            ("stop", {}),
            ("new_session", {}),
            ("clear_chat", {}),
            ("snapshot_toggle", {}),
            ("event_speech_mode", {"mode": "all"}),
            ("restart_piper", {}),
            ("code_clear", {}),
            ("live_screen_mode", {"mode": "pointer"}),
            ("live_screen_interval", {"interval_s": 5}),
            ("document_picker_cancel", {}),
            ("open_document_picker", {}),
        ]:
            ctrl._dispatch_web_action(action, payload)

        assert not pump_calls, f"pump_ui_queue called during dispatch of actions"
