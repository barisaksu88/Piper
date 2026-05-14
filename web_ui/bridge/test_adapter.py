"""web_ui.bridge.test_adapter

Deterministic tests for the pure bridge adapter.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from web_ui.bridge import adapter, message_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_frame(json_str: str) -> dict[str, Any]:
    return json.loads(json_str)


# ---------------------------------------------------------------------------
# Event kind coverage — one test per mapped kind from CONTRACT.md
# ---------------------------------------------------------------------------


class TestStreamingEvents:
    def test_stream_start(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("assistant_stream_start", {"tts_voice": "af_heart", "tts_speed": 0.85}))
        assert frame["frame"] == "event"
        assert frame["kind"] == "stream.start"
        assert frame["payload"]["tts_voice"] == "af_heart"
        assert frame["payload"]["tts_speed"] == 0.85

    def test_stream_start_empty(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("assistant_stream_start", ""))
        assert frame["kind"] == "stream.start"
        assert frame["payload"] == {}

    def test_stream_delta(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("assistant_stream_delta", {"text": "Hello"}))
        assert frame["kind"] == "stream.delta"
        assert frame["payload"]["text"] == "Hello"

    def test_stream_delta_raw_string(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("assistant_stream_delta", "Hello"))
        assert frame["payload"]["text"] == "Hello"

    def test_stream_end(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("assistant_stream_end", {}))
        assert frame["kind"] == "stream.end"
        assert "timestamp" in frame


class TestStatusEvents:
    def test_status_set(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("status", "THINKING"))
        assert frame["kind"] == "status.set"
        assert frame["payload"]["text"] == "THINKING"

    def test_status_widget_mode(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("status_widget_mode", "ROUTING"))
        assert frame["kind"] == "status.mode"
        assert frame["payload"]["text"] == "ROUTING"

    def test_status_widget_step(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("status_widget_step", "Stage 1/2"))
        assert frame["kind"] == "status.step"
        assert frame["payload"]["text"] == "Stage 1/2"

    def test_status_widget_dashboard_activity(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("status_widget_dashboard_activity", "Ingesting document: foo.pdf"))
        assert frame["kind"] == "activity.append"
        assert frame["payload"]["text"] == "Ingesting document: foo.pdf"

    def test_ui_controls_refresh(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("ui_controls_refresh", ""))
        assert frame["kind"] == "controls.refresh"
        assert frame["payload"] == {}


class TestBootEvents:
    def test_boot_log(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("boot_log", "Warming TTS engine..."))
        assert frame["kind"] == "boot.log"
        assert frame["payload"]["text"] == "Warming TTS engine..."

    def test_boot_ready(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("boot_ready", ""))
        assert frame["kind"] == "boot.ready"
        assert "timestamp" in frame


class TestChatEvents:
    def test_chat_append(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "system", "content": "[UI] Live screen enabled"}))
        assert frame["kind"] == "chat.append"
        assert frame["payload"]["role"] == "system"
        assert frame["payload"]["content"] == "[UI] Live screen enabled"

    def test_chat_sync(self):
        frame = _decode_frame(
            adapter.ui_tuple_to_ws_frame(
                "chat_sync",
                [
                    ("user", "hello"),
                    ("assistant", "hi there"),
                    {"role": "system", "content": "[UI] test"},
                ],
            )
        )
        assert frame["kind"] == "chat.sync"
        assert frame["sourceKind"] == "chat_sync"
        messages = frame["payload"]["messages"]
        assert len(messages) == 3
        assert messages[0] == {"role": "user", "content": "hello"}
        assert messages[1] == {"role": "assistant", "content": "hi there"}
        assert messages[2] == {"role": "system", "content": "[UI] test"}

    def test_chat_sync_empty(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_sync", []))
        assert frame["payload"]["messages"] == []

    def test_clear_thinking(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("clear_thinking", ""))
        assert frame["kind"] == "chat.clear_thinking"
        assert frame["payload"] == {}


class TestSearchEvents:
    def test_search_result(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("search_result", {"query": "Python 3.13", "data": "<html>results</html>"}))
        assert frame["kind"] == "search.result"
        assert frame["payload"]["query"] == "Python 3.13"
        assert frame["payload"]["data"] == "<html>results</html>"
        assert frame["payload"]["failed"] is False

    def test_search_result_failed(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("search_result", {"query": "x", "data": "", "error": "timeout"}))
        assert frame["payload"]["failed"] is True


class TestImageVisionEvents:
    def test_show_image(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("show_image", "Image saved to: workspace/out.png"))
        assert frame["kind"] == "image.show"
        assert frame["payload"]["path"] == "workspace/out.png"
        assert frame["payload"]["url"] == "/workspace/out.png"
        assert "caption" in frame["payload"]

    def test_show_image_raw_path(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("show_image", "out.png"))
        assert frame["payload"]["path"] == "out.png"
        assert frame["payload"]["url"] == "/workspace/out.png"

    def test_show_image_subdir(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("show_image", "images/sub/test.png"))
        assert frame["payload"]["path"] == "images/sub/test.png"
        assert frame["payload"]["url"] == "/workspace/images/sub/test.png"

    def test_show_image_windows_absolute_workspace(self):
        frame = _decode_frame(
            adapter.ui_tuple_to_ws_frame("show_image", "Image saved to: C:\\Projects\\Piper\\data\\workspace\\foo.png")
        )
        assert frame["payload"]["path"] == "C:\\Projects\\Piper\\data\\workspace\\foo.png"
        assert frame["payload"]["url"] == "/workspace/foo.png"

    def test_show_image_windows_absolute_workspace_subdir(self):
        frame = _decode_frame(
            adapter.ui_tuple_to_ws_frame("show_image", "C:\\Projects\\Piper\\data\\workspace\\images\\bar.jpg")
        )
        assert frame["payload"]["path"] == "C:\\Projects\\Piper\\data\\workspace\\images\\bar.jpg"
        assert frame["payload"]["url"] == "/workspace/images/bar.jpg"

    def test_show_image_unsafe_extension_no_url(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("show_image", "evil.exe"))
        assert frame["payload"]["path"] == "evil.exe"
        assert "url" not in frame["payload"]

    def test_show_image_traversal_no_url(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("show_image", "../secret.png"))
        assert frame["payload"]["path"] == "../secret.png"
        assert "url" not in frame["payload"]

    def test_show_image_absolute_no_url(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("show_image", "/etc/passwd"))
        assert frame["payload"]["path"] == "/etc/passwd"
        assert "url" not in frame["payload"]

    def test_vision_snapshot_note_dict(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("vision_snapshot_note", {"text": "Screen shows IDE", "speak": True}))
        assert frame["kind"] == "vision.note"
        assert frame["payload"]["text"] == "Screen shows IDE"
        assert frame["payload"]["speak"] is True

    def test_vision_snapshot_note_string(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("vision_snapshot_note", "Screen shows browser"))
        assert frame["payload"]["text"] == "Screen shows browser"
        assert frame["payload"]["speak"] is False


class TestCodeSessionEvents:
    def test_code_session_launch(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_session_launch", {"path": "script.py"}))
        assert frame["kind"] == "code.launch"
        assert frame["payload"]["path"] == "script.py"

    def test_code_session_launch_none(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_session_launch", None))
        assert frame["payload"] == {}

    def test_code_session_reset(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_session_reset", ""))
        assert frame["kind"] == "code.reset"
        assert frame["payload"] == {}

    def test_code_session_output(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_session_output", "Hello from script\n"))
        assert frame["kind"] == "code.output"
        assert frame["payload"]["text"] == "Hello from script\n"

    def test_code_session_status(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_session_status", "Exited with code 0"))
        assert frame["kind"] == "code.status"
        assert frame["payload"]["text"] == "Exited with code 0"

    def test_code_session_active(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_session_active", True))
        assert frame["kind"] == "code.active"
        assert frame["payload"]["active"] is True

    def test_code_session_focus(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_session_focus", ""))
        assert frame["kind"] == "code.focus"
        assert frame["payload"] == {}

    def test_code_view(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("code_view", "print('hello')"))
        assert frame["kind"] == "code.preview"
        assert frame["payload"]["text"] == "print('hello')"


class TestDocumentEvents:
    def test_documents_view(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("documents_view", "No documents ingested."))
        assert frame["kind"] == "document.view"
        assert frame["payload"]["text"] == "No documents ingested."

    def test_document_ingest_active(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("document_ingest_active", True))
        assert frame["kind"] == "document.ingest_active"
        assert frame["payload"]["active"] is True


class TestUserIdentityEvents:
    def test_active_user_changed(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("active_user_changed", {"preserve_transcript": True}))
        assert frame["kind"] == "user.changed"
        assert frame["payload"]["preserve_transcript"] is True

    def test_active_user_changed_empty(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("active_user_changed", None))
        assert frame["payload"]["preserve_transcript"] is False


class TestStatsEvents:
    def test_stats_view_refresh(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("stats_view_refresh", ""))
        assert frame["kind"] == "stats.refresh"
        assert frame["payload"] == {}


class TestErrorEvents:
    def test_error(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("error", "Orchestrator Error: timeout"))
        assert frame["kind"] == "error"
        assert frame["payload"]["message"] == "Orchestrator Error: timeout"


class TestAgentLogEvents:
    def test_agent_log(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("agent_log", "Planner chose FILE_OP"))
        assert frame["kind"] == "log.agent"
        assert frame["payload"]["text"] == "Planner chose FILE_OP"


class TestLiveScreenEvents:
    def test_live_screen_refresh(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("live_screen_refresh", {"pending": True}))
        assert frame["kind"] == "screen.refresh"
        assert frame["payload"]["pending"] is True


class TestConfigEvents:
    def test_config_reloaded(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("config_reloaded", ["LOG_LEVEL", "TEMPERATURE"]))
        assert frame["kind"] == "config.reloaded"
        assert frame["payload"]["changed_keys"] == ["LOG_LEVEL", "TEMPERATURE"]


# ---------------------------------------------------------------------------
# Strict unknown event handling
# ---------------------------------------------------------------------------


class TestUnknownEventStrictness:
    def test_unknown_event_raises(self):
        with pytest.raises(ValueError, match="Unknown event kind"):
            adapter.ui_tuple_to_ws_frame("phantom_event", "data")

    def test_is_known_event_kind_true(self):
        assert adapter.is_known_event_kind("status") is True

    def test_is_known_event_kind_false(self):
        assert adapter.is_known_event_kind("phantom_event") is False

    def test_get_event_schema_known(self):
        schema = adapter.get_event_schema("status")
        assert "payload_fields" in schema
        assert "visibility" in schema

    def test_get_event_schema_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown event kind"):
            adapter.get_event_schema("phantom_event")


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------


class TestSourceKindPresence:
    def test_stream_delta_has_source_kind(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("assistant_stream_delta", {"text": "hi"}))
        assert frame["sourceKind"] == "assistant_stream_delta"
        assert frame["kind"] == "stream.delta"

    def test_status_has_source_kind(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("status", "IDLE"))
        assert frame["sourceKind"] == "status"
        assert frame["kind"] == "status.set"

    def test_chat_append_has_source_kind(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "user", "content": "hello"}))
        assert frame["sourceKind"] == "chat_append"
        assert frame["kind"] == "chat.append"

    def test_error_has_source_kind(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("error", "oops"))
        assert frame["sourceKind"] == "error"
        assert frame["kind"] == "error"


class TestActionParsing:
    def test_send_message(self):
        raw = json.dumps({"frame": "action", "requestId": "r1", "action": "send_message", "payload": {"text": "hello"}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "send_message"
        assert payload["text"] == "hello"

    def test_stop(self):
        raw = json.dumps({"frame": "action", "requestId": "r2", "action": "stop", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "stop"
        assert payload == {}

    def test_new_session(self):
        raw = json.dumps({"frame": "action", "requestId": "r3", "action": "new_session", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "new_session"

    def test_clear_chat(self):
        raw = json.dumps({"frame": "action", "requestId": "r3b", "action": "clear_chat", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "clear_chat"

    def test_mic_toggle(self):
        raw = json.dumps({"frame": "action", "requestId": "r4", "action": "mic_toggle", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "mic_toggle"

    def test_snapshot_toggle(self):
        raw = json.dumps({"frame": "action", "requestId": "r4b", "action": "snapshot_toggle", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "snapshot_toggle"

    def test_live_screen_mode(self):
        raw = json.dumps({"frame": "action", "requestId": "r5a", "action": "live_screen_mode", "payload": {"mode": "display"}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "live_screen_mode"
        assert payload["mode"] == "display"

    def test_live_screen_interval(self):
        raw = json.dumps({"frame": "action", "requestId": "r5b", "action": "live_screen_interval", "payload": {"interval_s": 5}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "live_screen_interval"
        assert payload["interval_s"] == 5

    def test_event_speech_mode(self):
        raw = json.dumps({"frame": "action", "requestId": "r5c", "action": "event_speech_mode", "payload": {"mode": "important"}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "event_speech_mode"
        assert payload["mode"] == "important"

    def test_restart_piper(self):
        raw = json.dumps({"frame": "action", "requestId": "r5d", "action": "restart_piper", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "restart_piper"

    def test_open_document_picker(self):
        raw = json.dumps({"frame": "action", "requestId": "r5e", "action": "open_document_picker", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "open_document_picker"

    def test_document_picker_selected(self):
        raw = json.dumps({"frame": "action", "requestId": "r5f", "action": "document_picker_selected", "payload": {"paths": ["C:/tmp/a.pdf"]}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "document_picker_selected"
        assert payload["paths"] == ["C:/tmp/a.pdf"]

    def test_document_picker_cancel(self):
        raw = json.dumps({"frame": "action", "requestId": "r5g", "action": "document_picker_cancel", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "document_picker_cancel"

    def test_code_send(self):
        raw = json.dumps({"frame": "action", "requestId": "r6", "action": "code_send", "payload": {"text": "input"}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "code_send"
        assert payload["text"] == "input"

    def test_code_run(self):
        raw = json.dumps({"frame": "action", "requestId": "r7", "action": "code_run", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "code_run"

    def test_code_clear(self):
        raw = json.dumps({"frame": "action", "requestId": "r8", "action": "code_clear", "payload": {}})
        name, payload = adapter.parse_action_frame(raw)
        assert name == "code_clear"


# ---------------------------------------------------------------------------
# Invalid action handling
# ---------------------------------------------------------------------------


class TestInvalidActionHandling:
    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid action JSON"):
            adapter.parse_action_frame("not-json-at-all")

    def test_unknown_action_raises(self):
        raw = json.dumps({"frame": "action", "action": "launch_missiles", "payload": {}})
        with pytest.raises(ValueError, match="Unknown action name"):
            adapter.parse_action_frame(raw)

    def test_missing_action_field_raises(self):
        raw = json.dumps({"frame": "action", "payload": {}})
        with pytest.raises(ValueError, match="missing 'action' field"):
            adapter.parse_action_frame(raw)

    def test_wrong_frame_kind_raises(self):
        raw = json.dumps({"frame": "event", "action": "send_message", "payload": {}})
        with pytest.raises(ValueError, match="frame='action'"):
            adapter.parse_action_frame(raw)

    def test_non_object_raises(self):
        raw = json.dumps(["action", "send_message"])
        with pytest.raises(ValueError, match="must be a JSON object"):
            adapter.parse_action_frame(raw)


# ---------------------------------------------------------------------------
# Leakage prevention
# ---------------------------------------------------------------------------


class TestLeakagePrevention:
    def test_voice_identity_clarification_suppressed(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "system", "content": "[VOICE IDENTITY CLARIFICATION] Who is speaking?"}))
        assert frame["payload"]["_suppressed"] is True
        assert frame["payload"]["content"] == ""

    def test_voice_identity_event_suppressed(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "system", "content": "[VOICE IDENTITY EVENT]\nSwitched to Baris."}))
        assert frame["payload"]["_suppressed"] is True

    def test_ui_password_required_visible(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "system", "content": "[UI] Password required."}))
        assert frame["payload"].get("_suppressed") is None
        assert frame["payload"]["content"] == "[UI] Password required."

    def test_ui_identity_disambiguation_suppressed(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "system", "content": "[UI] I need one more detail to identify who is speaking."}))
        assert frame["payload"]["_suppressed"] is True

    def test_normal_ui_message_allowed(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "system", "content": "[UI] Live screen mode enabled."}))
        assert frame["payload"].get("_suppressed") is None
        assert frame["payload"]["content"] == "[UI] Live screen mode enabled."

    def test_user_role_never_suppressed(self):
        frame = _decode_frame(adapter.ui_tuple_to_ws_frame("chat_append", {"role": "user", "content": "[VOICE IDENTITY CLARIFICATION] test"}))
        assert frame["payload"].get("_suppressed") is None


# ---------------------------------------------------------------------------
# message_schema unit tests
# ---------------------------------------------------------------------------


class TestMessageSchema:
    def test_is_known_event_kind(self):
        assert message_schema.is_known_event_kind("status") is True
        assert message_schema.is_known_event_kind("nope") is False

    def test_get_frontend_event_name(self):
        assert message_schema.get_frontend_event_name("status") == "status.set"

    def test_get_frontend_event_name_unknown(self):
        with pytest.raises(ValueError):
            message_schema.get_frontend_event_name("nope")

    def test_is_known_action_name(self):
        assert message_schema.is_known_action_name("send_message") is True
        assert message_schema.is_known_action_name("nope") is False

    def test_event_frame_to_dict(self):
        frame = message_schema.EventFrame(timestamp="2026-01-01T00:00:00Z", kind="test", payload={"a": 1})
        d = frame.to_dict()
        assert d["frame"] == "event"
        assert d["kind"] == "test"
        assert d["payload"]["a"] == 1

    def test_action_frame_from_dict(self):
        frame = message_schema.ActionFrame.from_dict({"timestamp": "2026-01-01T00:00:00Z", "action": "send_message", "payload": {"text": "hi"}})
        assert frame.action == "send_message"
        assert frame.payload["text"] == "hi"

    def test_error_frame_to_dict(self):
        frame = message_schema.ErrorFrame(timestamp="2026-01-01T00:00:00Z", kind="validation_error", message="bad")
        d = frame.to_dict()
        assert d["frame"] == "error"
        assert d["message"] == "bad"
