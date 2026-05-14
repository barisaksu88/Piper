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
import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from config import Config, CFG



def _get_free_port() -> int:
    """Return an ephemeral localhost port that is free at call time."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

        port = _get_free_port()

        def _runner() -> None:
            result.append(run_web_bound(host="127.0.0.1", port=port, ws_path="/ws"))

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()

        # Give run_web time to start the bridge.
        time.sleep(0.3)

        # Verify bridge is running by connecting.
        import asyncio
        import websockets

        async def _poke() -> None:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
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

    def test_run_web_calls_pump_ui_queue_web_with_forward_queue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_web must call pump_ui_queue_web with a forward_queue so state updates
        happen and BridgeServer can broadcast events to WebSocket clients."""
        from ui.controller import PiperController
        import ui.controller_queue

        pump_calls: list[dict[str, Any]] = []
        original_pump = ui.controller_queue.pump_ui_queue_web

        def _tracking_pump(controller: Any, *, forward_queue: Any = None) -> None:
            pump_calls.append({"forward_queue": forward_queue})
            original_pump(controller, forward_queue=forward_queue)

        monkeypatch.setattr(ui.controller_queue, "pump_ui_queue_web", _tracking_pump)

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

        port = _get_free_port()

        def _runner() -> None:
            run_web_bound(host="127.0.0.1", port=port, ws_path="/ws")

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        time.sleep(0.3)

        # Trigger exit via restart action.
        import asyncio
        import websockets

        async def _poke() -> None:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                await ws.send(
                    json.dumps(
                        {"frame": "action", "action": "restart_piper", "payload": {}}
                    )
                )

        asyncio.run(_poke())
        thread.join(timeout=5.0)

        assert pump_calls, "pump_ui_queue_web was never called during run_web"
        assert any(
            call.get("forward_queue") is not None for call in pump_calls
        ), "pump_ui_queue_web was called but never with a forward_queue"


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


# ---------------------------------------------------------------------------
# Phase 6 — Regression lock tests (live smoke fixes)
# ---------------------------------------------------------------------------


class TestBootReadyWebState:
    def test_boot_ready_sets_controller_state_and_forwards(self) -> None:
        """pump_ui_queue_web must set controller.boot_ready=True and forward
        the event to bridge_queue when _boot_ui_min_visible_until has passed."""
        from ui.controller_queue import pump_ui_queue_web

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.boot_ready = False
        ctrl._boot_ui_min_visible_until = time.perf_counter() - 0.1
        ctrl._pending_boot_ready = False
        ctrl._pending_boot_ready_payload = ""

        bridge_q: queue.Queue = queue.Queue()
        ctrl.ui_queue.put(("boot_ready", "System Ready"))
        pump_ui_queue_web(ctrl, forward_queue=bridge_q)

        assert ctrl.boot_ready is True
        assert bridge_q.qsize() == 1
        kind, payload = bridge_q.get_nowait()
        assert kind == "boot_ready"
        assert payload == "System Ready"

    def test_boot_ready_deferred_until_min_visible(self) -> None:
        """If _boot_ui_min_visible_until is in the future, boot_ready must be
        deferred and the event still forwarded."""
        from ui.controller_queue import pump_ui_queue_web

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.boot_ready = False
        ctrl._boot_ui_min_visible_until = time.perf_counter() + 10.0
        ctrl._pending_boot_ready = False
        ctrl._pending_boot_ready_payload = ""

        bridge_q: queue.Queue = queue.Queue()
        ctrl.ui_queue.put(("boot_ready", "System Ready"))
        pump_ui_queue_web(ctrl, forward_queue=bridge_q)

        assert ctrl.boot_ready is False
        assert ctrl._pending_boot_ready is True
        assert ctrl._pending_boot_ready_payload == "System Ready"
        assert bridge_q.qsize() == 1


class TestStateSyncedDuplicatePrevention:
    def test_state_synced_chat_append_not_re_appended(self) -> None:
        """_state_synced chat_append must be forwarded but NOT duplicate chat_state."""
        from ui.controller_queue import pump_ui_queue_web

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.chat_state = MagicMock()
        ctrl.chat_state.get_messages_snapshot.return_value = [
            {"role": "user", "content": "hello"}
        ]

        bridge_q: queue.Queue = queue.Queue()
        ctrl.ui_queue.put(("chat_append", {"role": "user", "content": "hello", "_state_synced": True}))
        pump_ui_queue_web(ctrl, forward_queue=bridge_q)

        ctrl.chat_state.append.assert_not_called()
        assert bridge_q.qsize() == 1
        kind, payload = bridge_q.get_nowait()
        assert kind == "chat_append"
        assert payload["_state_synced"] is True


class TestNonSyncedChatAppendWebState:
    def test_non_synced_chat_append_appends_to_state(self) -> None:
        """Non-synced chat_append must be appended to chat_state exactly once
        and forwarded to bridge_queue."""
        from ui.controller_queue import pump_ui_queue_web

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.chat_state = MagicMock()

        bridge_q: queue.Queue = queue.Queue()
        ctrl.ui_queue.put(("chat_append", {"role": "system", "content": "[UI] test"}))
        pump_ui_queue_web(ctrl, forward_queue=bridge_q)

        ctrl.chat_state.append.assert_called_once_with("system", "[UI] test")
        assert bridge_q.qsize() == 1
        kind, payload = bridge_q.get_nowait()
        assert kind == "chat_append"
        assert payload["role"] == "system"


class TestDpgHardExitGuardLifecycle:
    def test_run_web_replaces_and_restores_dpg_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_web must replace dpg.does_item_exist during the loop and restore
        it on exit, even if an exception occurs."""
        from ui.controller import PiperController
        import dearpygui.dearpygui as dpg

        original = dpg.does_item_exist

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

        port = _get_free_port()

        def _runner() -> None:
            run_web_bound(host="127.0.0.1", port=port, ws_path="/ws")

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        time.sleep(0.3)

        # During the loop, dpg.does_item_exist should be run_web's lambda (returns False)
        assert dpg.does_item_exist is not original, "dpg.does_item_exist was not replaced"
        assert dpg.does_item_exist("any_tag") is False, "run_web replacement did not return False"

        # Trigger exit via restart action.
        import asyncio
        import websockets

        async def _poke() -> None:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                await ws.send(
                    json.dumps(
                        {"frame": "action", "action": "restart_piper", "payload": {}}
                    )
                )

        asyncio.run(_poke())
        thread.join(timeout=5.0)

        # After exit, the original must be restored.
        assert dpg.does_item_exist is original, "dpg.does_item_exist was not restored after run_web"


class TestBridgeQueueSeparation:
    def test_bridge_server_uses_bridge_queue_not_ui_queue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In Web mode BridgeServer must consume from bridge_queue, not
        controller.ui_queue, so pump_ui_queue_web and BridgeServer do not
        race on the same queue."""
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
        ctrl._dispatch_web_action = PiperController._dispatch_web_action.__get__(ctrl, MagicMock)  # type: ignore[method-assign]

        # Track what queue BridgeServer receives via the real import path
        server_init_calls: list[dict[str, Any]] = []

        def _capture_server(*, ui_queue: queue.Queue, **kwargs: Any) -> MagicMock:
            server_init_calls.append({"ui_queue": ui_queue, "kwargs": kwargs})
            mock = MagicMock()
            mock.start = MagicMock()
            mock.stop = MagicMock()
            return mock

        monkeypatch.setattr("web_ui.bridge.server.BridgeServer", _capture_server)

        run_web_bound = PiperController.run_web.__get__(ctrl, MagicMock)  # type: ignore[var-annotated]

        # Put an event on ui_queue before starting
        ctrl.ui_queue.put(("chat_append", {"role": "user", "content": "pre"}))

        port = _get_free_port()

        def _runner() -> None:
            run_web_bound(host="127.0.0.1", port=port, ws_path="/ws")

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        time.sleep(0.4)

        # Trigger exit
        ctrl.restart_requested = True
        thread.join(timeout=3.0)

        assert len(server_init_calls) == 1
        bridge_queue = server_init_calls[0]["ui_queue"]
        # BridgeServer must NOT receive controller.ui_queue directly
        assert bridge_queue is not ctrl.ui_queue, "BridgeServer was wired directly to controller.ui_queue"

        # The pre-queued event must have been consumed from ui_queue by pump_ui_queue_web,
        # not left for BridgeServer (which would race).
        assert ctrl.ui_queue.empty(), "controller.ui_queue was not drained by pump_ui_queue_web"


class TestChatAppendBroadcastContract:
    def test_chat_append_emits_state_synced_ui_queue_event(self) -> None:
        """controller.chat_append must append to chat_state AND emit a
        ui_queue chat_append event with _state_synced=True."""
        from ui.controller import PiperController

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.chat_state = MagicMock()
        ctrl._chat_rendered_messages = []
        ctrl._chat_rendered_tags = []
        ctrl._chat_render_wrap_columns = None
        ctrl._try_append_chat_ui = MagicMock(return_value=False)
        ctrl._refresh_chat_ui = MagicMock()

        PiperController.chat_append(ctrl, "user", "hello")

        ctrl.chat_state.append.assert_called_once_with("user", "hello")
        assert ctrl.ui_queue.qsize() == 1
        kind, payload = ctrl.ui_queue.get_nowait()
        assert kind == "chat_append"
        assert payload["role"] == "user"
        assert payload["content"] == "hello"
        assert payload.get("_state_synced") is True


class TestDpgPumpCompatibility:
    def test_pump_ui_queue_accepts_no_forward_queue(self) -> None:
        """pump_ui_queue must still work when called without forward_queue."""
        from ui.controller_queue import pump_ui_queue

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.chat_state = MagicMock()
        # No exception expected
        pump_ui_queue(ctrl)

    def test_state_synced_chat_append_does_not_re_append_in_dpg_pump(self) -> None:
        """In DPG mode, _state_synced chat_append must NOT call chat_append again
        (which would add a duplicate to chat_state and re-emit to ui_queue)."""
        from ui.controller_queue import pump_ui_queue

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.chat_state = MagicMock()
        ctrl.tags = MagicMock()

        ctrl.ui_queue.put(("chat_append", {"role": "user", "content": "hello", "_state_synced": True}))
        pump_ui_queue(ctrl)

        ctrl.chat_append.assert_not_called()
        ctrl._refresh_chat_ui.assert_called_once()

    def test_non_synced_chat_append_still_calls_chat_append_in_dpg_pump(self) -> None:
        """In DPG mode, non-synced chat_append must still call controller.chat_append
        so backend-generated messages are added to chat_state."""
        from ui.controller_queue import pump_ui_queue

        ctrl = MagicMock()
        ctrl.ui_queue = queue.Queue()
        ctrl.chat_state = MagicMock()
        ctrl.tags = MagicMock()

        ctrl.ui_queue.put(("chat_append", {"role": "system", "content": "[UI] test"}))
        pump_ui_queue(ctrl)

        ctrl.chat_append.assert_called_once_with("system", "[UI] test")


class TestChatSyncUsesRenderableMessages:
    def test_chat_sync_excludes_hidden_and_system_noise(self) -> None:
        """The chat.sync callback must use renderable_chat_messages logic so
        hidden messages and system noise are excluded."""
        from ui.controller import PiperController
        from ui.controller_render import renderable_chat_messages
        from web_ui.bridge.adapter import ui_tuple_to_ws_frame

        ctrl = MagicMock()
        ctrl.chat_state.get_messages_snapshot.return_value = [
            {"role": "user", "content": "visible user"},
            {"role": "assistant", "content": "visible assistant"},
            {"role": "system", "content": "[Saved to file: secret.txt]", "hidden": True},
            {"role": "system", "content": "System retrieved file foo.py"},
            {"role": "system", "content": "Tool Response: bar"},
            {"role": "assistant", "content": ""},
            {"role": "system", "content": "[UI] Live screen enabled"},
        ]

        # Replicate the callback logic from run_web
        messages = renderable_chat_messages(ctrl.chat_state.get_messages_snapshot())
        sync_frame = ui_tuple_to_ws_frame("chat_sync", messages)
        import json

        frame = json.loads(sync_frame)
        assert frame["kind"] == "chat.sync"
        assert frame["sourceKind"] == "chat_sync"
        msgs = frame["payload"]["messages"]

        # Only visible messages should appear
        roles = [m["role"] for m in msgs]
        contents = [m["content"] for m in msgs]
        assert roles == ["user", "assistant", "system"]
        assert "visible user" in contents
        assert "visible assistant" in contents
        assert "[UI] Live screen enabled" in contents
        # Hidden / noise must NOT appear
        assert "[Saved to file: secret.txt]" not in contents
        assert "System retrieved file foo.py" not in contents
        assert "Tool Response" not in contents
        assert "" not in contents

    def test_chat_sync_payload_shape(self) -> None:
        """chat.sync payload must be a list of {role, content} objects."""
        from web_ui.bridge.adapter import ui_tuple_to_ws_frame
        import json

        sync_frame = ui_tuple_to_ws_frame("chat_sync", [("user", "hi")])
        frame = json.loads(sync_frame)
        assert frame["payload"]["messages"] == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Conversation summary reset on new_session / clear_chat
# ---------------------------------------------------------------------------


class TestConversationSummaryReset:
    """New Session and Clear Chat must reset both persisted file and in-memory
    conversation_summary so the next orchestrator turn starts fresh."""

    def test_new_session_deletes_persisted_summary(self, tmp_path: pytest.TestPath, monkeypatch: pytest.MonkeyPatch) -> None:
        from ui.controller_actions import on_new_session

        summary_path = tmp_path / "conversation_summary.json"
        summary_path.write_text('{"summary": "old summary"}', encoding="utf-8")

        ctrl = MagicMock()
        ctrl.user_runtime.current_conversation_summary_path.return_value = summary_path
        ctrl.chat_state.new_session = MagicMock()
        ctrl.tts.stop = MagicMock()

        on_new_session(ctrl)

        assert not summary_path.exists()

    def test_new_session_sets_in_memory_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ui.controller_actions import on_new_session

        ctrl = MagicMock()
        ctrl.user_runtime.current_conversation_summary_path.return_value = __import__("pathlib").Path("/dev/null")
        ctrl.chat_state.new_session = MagicMock()
        ctrl.tts.stop = MagicMock()

        on_new_session(ctrl)

        assert ctrl._conversation_summary_override == ""

    def test_clear_chat_sets_in_memory_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ui.controller_actions import on_clear
        import dearpygui.dearpygui as dpg

        monkeypatch.setattr(dpg, "does_item_exist", lambda _tag: False)
        monkeypatch.setattr(dpg, "delete_item", lambda _tag, **kwargs: None)
        monkeypatch.setattr(dpg, "set_value", lambda _tag, _val: None)

        ctrl = MagicMock()
        ctrl.user_runtime.current_conversation_summary_path.return_value = __import__("pathlib").Path("/dev/null")
        ctrl.chat_state.clear = MagicMock()

        on_clear(ctrl)

        assert ctrl._conversation_summary_override == ""

    def test_build_orchestrator_config_consumes_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_orchestrator_config must pass the override to OrchestratorConfig
        and then clear it so subsequent turns load from file normally."""
        from ui.controller import PiperController
        from core.orchestrator import OrchestratorConfig

        ctrl = MagicMock()
        ctrl._conversation_summary_override = ""
        ctrl.llm = MagicMock()
        ctrl.agent_brain = MagicMock()
        ctrl.knowledge_mgr = MagicMock()
        ctrl.prompt_context_service = MagicMock()
        ctrl.chat_state = MagicMock()
        ctrl.style_mgr = MagicMock()
        ctrl.pipeline = MagicMock()
        ctrl.ui_queue = MagicMock()
        ctrl.boot_mgr = MagicMock()
        ctrl.img_gen = MagicMock()
        ctrl.live_screen = MagicMock()
        ctrl.user_runtime = MagicMock()
        ctrl.user_runtime.current_conversation_summary_path.return_value = __import__("pathlib").Path("/tmp/summary.json")
        ctrl._pending_input_modality = "typed"
        ctrl._pending_voice_identity_notice = ""

        # Bind the real method
        bound = PiperController.build_orchestrator_config.__get__(ctrl, MagicMock)
        cfg: OrchestratorConfig = bound()

        assert cfg.conversation_summary == ""
        assert ctrl._conversation_summary_override is None

    def test_build_orchestrator_config_passes_none_when_no_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ui.controller import PiperController
        from core.orchestrator import OrchestratorConfig

        ctrl = MagicMock()
        ctrl._conversation_summary_override = None
        ctrl.llm = MagicMock()
        ctrl.agent_brain = MagicMock()
        ctrl.knowledge_mgr = MagicMock()
        ctrl.prompt_context_service = MagicMock()
        ctrl.chat_state = MagicMock()
        ctrl.style_mgr = MagicMock()
        ctrl.pipeline = MagicMock()
        ctrl.ui_queue = MagicMock()
        ctrl.boot_mgr = MagicMock()
        ctrl.img_gen = MagicMock()
        ctrl.live_screen = MagicMock()
        ctrl.user_runtime = MagicMock()
        ctrl.user_runtime.current_conversation_summary_path.return_value = __import__("pathlib").Path("/tmp/summary.json")
        ctrl._pending_input_modality = "typed"
        ctrl._pending_voice_identity_notice = ""

        bound = PiperController.build_orchestrator_config.__get__(ctrl, MagicMock)
        cfg: OrchestratorConfig = bound()

        assert cfg.conversation_summary is None

    def test_orchestrator_uses_override_when_provided(self, tmp_path: pytest.TestPath) -> None:
        """If OrchestratorConfig.conversation_summary is set, the orchestrator must
        use it instead of loading from the file."""
        from core.orchestrator import Orchestrator, OrchestratorConfig

        summary_path = tmp_path / "conversation_summary.json"
        summary_path.write_text('{"summary": "stale summary"}', encoding="utf-8")

        cfg = OrchestratorConfig(
            llm=MagicMock(),
            brain=MagicMock(),
            knowledge=MagicMock(),
            prompt_context=MagicMock(),
            chat=MagicMock(),
            styles=MagicMock(),
            pipeline=MagicMock(),
            ui=MagicMock(),
            get_context=MagicMock(),
            boot=MagicMock(),
            img_gen=MagicMock(),
            conversation_summary_path=summary_path,
            conversation_summary="",
        )

        orc = Orchestrator(cfg)
        assert orc.conversation_summary == ""

    def test_orchestrator_loads_from_file_when_no_override(self, tmp_path: pytest.TestPath) -> None:
        """If OrchestratorConfig.conversation_summary is None, the orchestrator must
        load from the file as before."""
        from core.orchestrator import Orchestrator, OrchestratorConfig

        summary_path = tmp_path / "conversation_summary.json"
        summary_path.write_text('{"summary": "file summary"}', encoding="utf-8")

        cfg = OrchestratorConfig(
            llm=MagicMock(),
            brain=MagicMock(),
            knowledge=MagicMock(),
            prompt_context=MagicMock(),
            chat=MagicMock(),
            styles=MagicMock(),
            pipeline=MagicMock(),
            ui=MagicMock(),
            get_context=MagicMock(),
            boot=MagicMock(),
            img_gen=MagicMock(),
            conversation_summary_path=summary_path,
        )

        orc = Orchestrator(cfg)
        assert orc.conversation_summary == "file summary"


# ---------------------------------------------------------------------------
# TagScrubber — internal markers must not reach display text
# ---------------------------------------------------------------------------


class TestTagScrubberInternalMarkers:
    def test_scrubs_router(self) -> None:
        from core.pipeline import TagScrubber

        scrubber = TagScrubber()
        assert scrubber.process_delta("Hello [ROUTER] world") == "Hello  world"

    def test_scrubs_recall(self) -> None:
        from core.pipeline import TagScrubber

        scrubber = TagScrubber()
        assert scrubber.process_delta("See [RECALL: foo] bar") == "See  bar"

    def test_scrubs_recall_multiline(self) -> None:
        from core.pipeline import TagScrubber

        scrubber = TagScrubber()
        assert scrubber.process_delta("A [RECALL: line1\nline2] B") == "A  B"

    def test_scrubs_run_code_block(self) -> None:
        from core.pipeline import TagScrubber

        scrubber = TagScrubber()
        assert scrubber.process_delta("Run [RUN_CODE]x=1[/RUN_CODE] now") == "Run  now"

    def test_normal_text_untouched(self) -> None:
        from core.pipeline import TagScrubber

        scrubber = TagScrubber()
        assert scrubber.process_delta("Normal reply.") == "Normal reply."

    def test_process_delta_scrubs_router_and_recall_across_calls(self) -> None:
        from core.pipeline import TagScrubber

        scrubber = TagScrubber()
        out1 = scrubber.process_delta("Hello [ROUTER]")
        out2 = scrubber.process_delta(" world [RECALL: x] end")
        assert out1 == "Hello "
        assert out2 == " world  end"


# ---------------------------------------------------------------------------
# ChatPipeline.clean_stream_buffer — exposes scrubbed text for Web pump
# ---------------------------------------------------------------------------


class TestChatPipelineCleanStreamBuffer:
    def test_clean_buffer_empty_before_start(self) -> None:
        from core.pipeline import ChatPipeline

        pipeline = ChatPipeline(
            tts=MagicMock(),
            chat_append_fn=MagicMock(),
            chat_upsert_fn=MagicMock(),
            persist_turn_fn=MagicMock(),
            set_status_fn=MagicMock(),
        )
        assert pipeline.clean_stream_buffer == ""

    def test_clean_buffer_accumulates_scrubbed_text(self) -> None:
        from core.pipeline import ChatPipeline

        pipeline = ChatPipeline(
            tts=MagicMock(),
            chat_append_fn=MagicMock(),
            chat_upsert_fn=MagicMock(),
            persist_turn_fn=MagicMock(),
            set_status_fn=MagicMock(),
        )
        pipeline.handle_event("start", "")
        pipeline.handle_event("delta", "Hello [ROUTER] world")
        assert pipeline.clean_stream_buffer == "Hello  world"

    def test_clean_buffer_resets_on_start(self) -> None:
        from core.pipeline import ChatPipeline

        pipeline = ChatPipeline(
            tts=MagicMock(),
            chat_append_fn=MagicMock(),
            chat_upsert_fn=MagicMock(),
            persist_turn_fn=MagicMock(),
            set_status_fn=MagicMock(),
        )
        pipeline.handle_event("start", "")
        pipeline.handle_event("delta", "first")
        pipeline.handle_event("start", "")
        assert pipeline.clean_stream_buffer == ""


# ---------------------------------------------------------------------------
# pump_ui_queue_web forwards clean deltas, not raw text
# ---------------------------------------------------------------------------


class TestPumpWebForwardsCleanDeltas:
    def test_stream_delta_forwards_scrubbed_text(self) -> None:
        from ui.controller_queue import pump_ui_queue_web
        from core.pipeline import ChatPipeline

        ui_q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        fwd_q: "queue.Queue[tuple[str, object]]" = queue.Queue()

        ctrl = MagicMock()
        ctrl.ui_queue = ui_q
        pipeline = ChatPipeline(
            tts=MagicMock(),
            chat_append_fn=MagicMock(),
            chat_upsert_fn=MagicMock(),
            persist_turn_fn=MagicMock(),
            set_status_fn=MagicMock(),
        )
        ctrl.pipeline = pipeline

        ui_q.put(("assistant_stream_start", ""))
        ui_q.put(("assistant_stream_delta", {"text": "Hello [ROUTER]"}))
        ui_q.put(("assistant_stream_delta", {"text": " world"}))
        ui_q.put(("assistant_stream_end", ""))

        pump_ui_queue_web(ctrl, forward_queue=fwd_q)

        # start, delta, delta, end
        assert fwd_q.qsize() == 4
        kinds = [fwd_q.get()[0] for _ in range(4)]
        assert kinds == [
            "assistant_stream_start",
            "assistant_stream_delta",
            "assistant_stream_delta",
            "assistant_stream_end",
        ]

    def test_clean_delta_excludes_internal_markers(self) -> None:
        from ui.controller_queue import pump_ui_queue_web
        from core.pipeline import ChatPipeline

        ui_q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        fwd_q: "queue.Queue[tuple[str, object]]" = queue.Queue()

        ctrl = MagicMock()
        ctrl.ui_queue = ui_q
        pipeline = ChatPipeline(
            tts=MagicMock(),
            chat_append_fn=MagicMock(),
            chat_upsert_fn=MagicMock(),
            persist_turn_fn=MagicMock(),
            set_status_fn=MagicMock(),
        )
        ctrl.pipeline = pipeline

        ui_q.put(("assistant_stream_start", ""))
        ui_q.put(("assistant_stream_delta", {"text": "A [ROUTER] B"}))
        ui_q.put(("assistant_stream_end", ""))

        pump_ui_queue_web(ctrl, forward_queue=fwd_q)

        # Skip start
        fwd_q.get()
        delta_kind, delta_payload = fwd_q.get()
        assert delta_kind == "assistant_stream_delta"
        assert delta_payload["text"] == "A  B"


# ---------------------------------------------------------------------------
# renderable_chat_messages — defensive exclusion of internal markers
# ---------------------------------------------------------------------------


class TestRenderableChatMessagesInternalMarkers:
    def test_excludes_router_text(self) -> None:
        from ui.controller_render import renderable_chat_messages

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "[ROUTER] rerouting..."},
        ]
        rendered = renderable_chat_messages(messages)
        assert not any("[ROUTER]" in content for _role, content in rendered)
        assert rendered == [("user", "hi")]

    def test_includes_normal_assistant_text(self) -> None:
        from ui.controller_render import renderable_chat_messages

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        rendered = renderable_chat_messages(messages)
        assert rendered == [("user", "hi"), ("assistant", "Hello!")]

    def test_excludes_hidden_messages(self) -> None:
        from ui.controller_render import renderable_chat_messages

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "secret", "hidden": True},
        ]
        rendered = renderable_chat_messages(messages)
        assert rendered == [("user", "hi")]

    def test_excludes_empty_assistant(self) -> None:
        from ui.controller_render import renderable_chat_messages

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "   "},
        ]
        rendered = renderable_chat_messages(messages)
        assert rendered == [("user", "hi")]
