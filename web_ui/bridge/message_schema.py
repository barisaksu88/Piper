"""web_ui.bridge.message_schema

TypedDict-style event/action contracts for the Piper Web UI bridge.

No runtime imports from ui/, core/, memory/, tools/, or app.py.
This module is pure schema/contract code only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Event kind registry — mapping from ui_queue kind strings to frontend
# WebSocket event names.
# ---------------------------------------------------------------------------

KNOWN_EVENT_KINDS: dict[str, str] = {
    # Streaming
    "assistant_stream_start": "stream.start",
    "assistant_stream_delta": "stream.delta",
    "assistant_stream_end": "stream.end",
    # Status / mode
    "status": "status.set",
    "status_widget_mode": "status.mode",
    "status_widget_step": "status.step",
    "status_widget_dashboard_activity": "activity.append",
    "ui_controls_refresh": "controls.refresh",
    # Boot / lifecycle
    "boot_log": "boot.log",
    "boot_ready": "boot.ready",
    # Chat / message
    "chat_append": "chat.append",
    "chat_sync": "chat.sync",
    "clear_thinking": "chat.clear_thinking",
    # Search
    "search_result": "search.result",
    # Image / vision
    "show_image": "image.show",
    "vision_snapshot_note": "vision.note",
    # Code session
    "code_session_launch": "code.launch",
    "code_session_reset": "code.reset",
    "code_session_output": "code.output",
    "code_session_status": "code.status",
    "code_session_active": "code.active",
    "code_session_focus": "code.focus",
    "code_view": "code.preview",
    # Document
    "documents_view": "document.view",
    "document_ingest_active": "document.ingest_active",
    # User / identity
    "active_user_changed": "user.changed",
    # Stats
    "stats_view_refresh": "stats.refresh",
    # Error
    "error": "error",
    # Agent / monitor
    "agent_log": "log.agent",
    # Live screen
    "live_screen_refresh": "screen.refresh",
    # Config
    "config_reloaded": "config.reloaded",
    # Mic / STT
    "mic_status": "mic.status",
    # TTS / playback
    "tts_status": "tts.status",
    # Style / persona
    "style_status": "style.status",
    # Auth
    "auth_status": "auth.status",
    # Workspace
    "workspace_files": "workspace.files",
    "file_contents": "file.contents",
    # Stop acknowledgement
    "stop_ack": "stop.ack",
}

# Set for O(1) membership checks.
_EVENT_KIND_SET: set[str] = set(KNOWN_EVENT_KINDS.keys())

# ---------------------------------------------------------------------------
# Action registry — mapping from frontend action names to internal action
# identifiers.
# ---------------------------------------------------------------------------

KNOWN_ACTION_NAMES: set[str] = {
    "send_message",
    "stop",
    "new_session",
    "clear_chat",
    "mic_toggle",
    "mic_start",
    "mic_stop",
    "snapshot_toggle",
    "live_screen_mode",
    "live_screen_interval",
    "event_speech_mode",
    "restart_piper",
    "open_document_picker",
    "document_picker_selected",
    "document_picker_cancel",
    "stats_refresh",
    "code_send",
    "code_run",
    "code_clear",
    "list_workspace_files",
    "read_workspace_file",
    "save_workspace_file",
    "mic_audio_submit",
    "screen_analyze",
}

# ---------------------------------------------------------------------------
# Frame kinds
# ---------------------------------------------------------------------------

FrameKind = Literal["event", "action", "error"]

# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def is_known_event_kind(kind: str) -> bool:
    """Return True if *kind* is a registered ui_queue event kind."""
    return kind in _EVENT_KIND_SET


def get_frontend_event_name(kind: str) -> str:
    """Map a ui_queue kind to its frontend WebSocket event name.

    Raises ValueError for unknown kinds.
    """
    if kind not in _EVENT_KIND_SET:
        raise ValueError(f"Unknown event kind: {kind!r}")
    return KNOWN_EVENT_KINDS[kind]


def is_known_action_name(action: str) -> bool:
    """Return True if *action* is a registered frontend action name."""
    return action in KNOWN_ACTION_NAMES


# ---------------------------------------------------------------------------
# Dataclasses for structured frames
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventFrame:
    """Outgoing backend -> frontend event frame."""

    frame: Literal["event"] = "event"
    timestamp: str = ""
    request_id: str = ""
    kind: str = ""
    source_kind: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "timestamp": self.timestamp,
            "requestId": self.request_id,
            "kind": self.kind,
            "sourceKind": self.source_kind,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class ActionFrame:
    """Incoming frontend -> backend action frame."""

    frame: Literal["action"] = "action"
    timestamp: str = ""
    request_id: str = ""
    action: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionFrame":
        return cls(
            frame="action",
            timestamp=str(data.get("timestamp") or ""),
            request_id=str(data.get("requestId") or ""),
            action=str(data.get("action") or ""),
            payload=dict(data.get("payload") or {}),
        )


@dataclass(frozen=True, slots=True)
class ErrorFrame:
    """Outgoing backend -> frontend error frame."""

    frame: Literal["error"] = "error"
    timestamp: str = ""
    request_id: str = ""
    kind: str = ""
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "timestamp": self.timestamp,
            "requestId": self.request_id,
            "kind": self.kind,
            "message": self.message,
            "payload": self.payload,
        }
