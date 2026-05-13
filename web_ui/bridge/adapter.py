"""web_ui.bridge.adapter

Pure translation layer between Piper's ui_queue tuples and WebSocket JSON frames.

Constraints:
- No I/O, no sockets, no threads.
- No imports from ui/, core/, memory/, tools/, or app.py.
- Unknown events/actions raise ValueError (strict mode).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from web_ui.bridge.message_schema import (
    ActionFrame,
    ErrorFrame,
    EventFrame,
    get_frontend_event_name,
    is_known_action_name,
    is_known_event_kind as _is_known_event_kind,
)

# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Payload normalizers
# ---------------------------------------------------------------------------


def _normalize_stream_delta_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        text = str(payload.get("text") or "")
    else:
        text = str(payload or "")
    return {"text": text}


def _normalize_stream_start_payload(payload: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(payload, dict):
        if payload.get("tts_voice"):
            result["tts_voice"] = str(payload["tts_voice"])
        if payload.get("tts_speed") is not None:
            result["tts_speed"] = float(payload["tts_speed"])
    return result


def _normalize_chat_append_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"role": "system", "content": str(payload or "")}
    role = str(payload.get("role") or "system")
    content = str(payload.get("content") or "")
    # Do not leak raw [UI] clarification / identity-selection text into chat.
    if _is_leaky_system_text(role, content):
        return {"role": "system", "content": "", "_suppressed": True}
    return {"role": role, "content": content}


def _normalize_chat_sync_payload(payload: object) -> dict[str, Any]:
    """Normalize a chat_sync payload into {"messages": [...]}.

    Payload is expected to be a list of (role, content) tuples or dicts.
    """
    messages: list[dict[str, str]] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                messages.append({
                    "role": str(item.get("role") or "system"),
                    "content": str(item.get("content") or ""),
                })
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                messages.append({"role": str(item[0]), "content": str(item[1])})
    return {"messages": messages}


def _normalize_show_image_payload(payload: object) -> dict[str, Any]:
    text = str(payload or "")
    # Extract path from "Image saved to: path" message.
    path = text
    if "Image saved to:" in text:
        path = text.split("Image saved to:")[-1].strip()
    return {"caption": text, "path": path}


def _normalize_vision_note_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {
            "text": str(payload.get("text") or ""),
            "speak": bool(payload.get("speak")),
        }
    return {"text": str(payload or ""), "speak": False}


def _normalize_code_session_launch_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        path = str(payload.get("path") or "").strip()
        return {"path": path} if path else {}
    return {}


def _normalize_search_result_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"query": "", "data": "", "failed": False}
    query = str(payload.get("query") or "")
    data = str(payload.get("data") or "")
    failed = bool(payload.get("error")) or not data
    # CancellationToken is not serializable; strip it.
    return {"query": query, "data": data, "failed": failed}


def _normalize_active_user_changed_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {"preserve_transcript": bool(payload.get("preserve_transcript"))}
    return {"preserve_transcript": False}


def _normalize_live_screen_refresh_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {"pending": bool(payload.get("pending"))}
    return {}


def _normalize_document_ingest_payload(payload: object) -> dict[str, Any]:
    return {"active": bool(payload)}


def _normalize_config_reloaded_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, (list, tuple)):
        return {"changed_keys": list(payload)}
    return {"changed_keys": []}


def _normalize_code_session_active_payload(payload: object) -> dict[str, Any]:
    return {"active": bool(payload)}


def _normalize_code_output_payload(payload: object) -> dict[str, Any]:
    return {"text": str(payload or "")}


def _normalize_code_status_payload(payload: object) -> dict[str, Any]:
    return {"text": str(payload or "")}


def _normalize_code_preview_payload(payload: object) -> dict[str, Any]:
    return {"text": str(payload or "")}


def _normalize_generic_string_payload(payload: object) -> dict[str, Any]:
    return {"text": str(payload or "")}


def _normalize_error_payload(payload: object) -> dict[str, Any]:
    return {"message": str(payload or "")}


def _normalize_empty_payload(_payload: object) -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Leakage guard
# ---------------------------------------------------------------------------

_SUPPRESSED_SYSTEM_PREFIXES: tuple[str, ...] = (
    "[VOICE IDENTITY CLARIFICATION]",
    "[VOICE IDENTITY EVENT]",
    "[UI] I need one more detail to identify who is speaking.",
)


def _is_leaky_system_text(role: str, content: str) -> bool:
    if role != "system":
        return False
    stripped = content.strip()
    return stripped.startswith(_SUPPRESSED_SYSTEM_PREFIXES)


# ---------------------------------------------------------------------------
# Normalizer router
# ---------------------------------------------------------------------------

_EVENT_NORMALIZERS: dict[str, callable] = {
    "assistant_stream_delta": _normalize_stream_delta_payload,
    "assistant_stream_start": _normalize_stream_start_payload,
    "assistant_stream_end": _normalize_stream_start_payload,
    "chat_append": _normalize_chat_append_payload,
    "chat_sync": _normalize_chat_sync_payload,
    "show_image": _normalize_show_image_payload,
    "vision_snapshot_note": _normalize_vision_note_payload,
    "code_session_launch": _normalize_code_session_launch_payload,
    "code_session_reset": _normalize_empty_payload,
    "code_session_output": _normalize_code_output_payload,
    "code_session_status": _normalize_code_status_payload,
    "code_session_active": _normalize_code_session_active_payload,
    "code_session_focus": _normalize_empty_payload,
    "code_view": _normalize_code_preview_payload,
    "search_result": _normalize_search_result_payload,
    "active_user_changed": _normalize_active_user_changed_payload,
    "live_screen_refresh": _normalize_live_screen_refresh_payload,
    "document_ingest_active": _normalize_document_ingest_payload,
    "config_reloaded": _normalize_config_reloaded_payload,
    "error": _normalize_error_payload,
    # All remaining events default to generic string payload
    "status": _normalize_generic_string_payload,
    "status_widget_mode": _normalize_generic_string_payload,
    "status_widget_step": _normalize_generic_string_payload,
    "status_widget_dashboard_activity": _normalize_generic_string_payload,
    "ui_controls_refresh": _normalize_empty_payload,
    "boot_log": _normalize_generic_string_payload,
    "boot_ready": _normalize_generic_string_payload,
    "clear_thinking": _normalize_empty_payload,
    "documents_view": _normalize_generic_string_payload,
    "stats_view_refresh": _normalize_empty_payload,
    "agent_log": _normalize_generic_string_payload,
}


def _normalize_payload(kind: str, payload: object) -> dict[str, Any]:
    normalizer = _EVENT_NORMALIZERS.get(kind, _normalize_generic_string_payload)
    return normalizer(payload)


# ---------------------------------------------------------------------------
# Event schema descriptions
# ---------------------------------------------------------------------------

_EVENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "assistant_stream_start": {
        "payload_fields": {"tts_voice": "str | None", "tts_speed": "float | None"},
        "visibility": ["chat", "status"],
    },
    "assistant_stream_delta": {
        "payload_fields": {"text": "str"},
        "visibility": ["chat"],
    },
    "assistant_stream_end": {
        "payload_fields": {"tts_voice": "str | None", "tts_speed": "float | None"},
        "visibility": ["chat", "status"],
    },
    "status": {
        "payload_fields": {"text": "str"},
        "visibility": ["status"],
    },
    "status_widget_mode": {
        "payload_fields": {"text": "str"},
        "visibility": ["status"],
    },
    "status_widget_step": {
        "payload_fields": {"text": "str"},
        "visibility": ["status"],
    },
    "status_widget_dashboard_activity": {
        "payload_fields": {"text": "str"},
        "visibility": ["log", "status"],
    },
    "ui_controls_refresh": {
        "payload_fields": {},
        "visibility": ["control"],
    },
    "boot_log": {
        "payload_fields": {"text": "str"},
        "visibility": ["log"],
    },
    "boot_ready": {
        "payload_fields": {},
        "visibility": ["status", "control"],
    },
    "chat_append": {
        "payload_fields": {"role": "str", "content": "str"},
        "visibility": ["chat"],
    },
    "clear_thinking": {
        "payload_fields": {},
        "visibility": ["chat"],
    },
    "search_result": {
        "payload_fields": {"query": "str", "data": "str", "failed": "bool"},
        "visibility": ["internal"],
    },
    "show_image": {
        "payload_fields": {"caption": "str", "path": "str"},
        "visibility": ["image"],
    },
    "vision_snapshot_note": {
        "payload_fields": {"text": "str", "speak": "bool"},
        "visibility": ["log", "status"],
    },
    "code_session_launch": {
        "payload_fields": {"path": "str"},
        "visibility": ["code", "status"],
    },
    "code_session_reset": {
        "payload_fields": {},
        "visibility": ["code"],
    },
    "code_session_output": {
        "payload_fields": {"text": "str"},
        "visibility": ["code"],
    },
    "code_session_status": {
        "payload_fields": {"text": "str"},
        "visibility": ["code", "status"],
    },
    "code_session_active": {
        "payload_fields": {"active": "bool"},
        "visibility": ["code", "control"],
    },
    "code_session_focus": {
        "payload_fields": {},
        "visibility": ["control"],
    },
    "code_view": {
        "payload_fields": {"text": "str"},
        "visibility": ["code"],
    },
    "documents_view": {
        "payload_fields": {"text": "str"},
        "visibility": ["document"],
    },
    "document_ingest_active": {
        "payload_fields": {"active": "bool"},
        "visibility": ["control"],
    },
    "active_user_changed": {
        "payload_fields": {"preserve_transcript": "bool"},
        "visibility": ["chat", "status"],
    },
    "stats_view_refresh": {
        "payload_fields": {},
        "visibility": ["status"],
    },
    "error": {
        "payload_fields": {"message": "str"},
        "visibility": ["chat", "status"],
    },
    "agent_log": {
        "payload_fields": {"text": "str"},
        "visibility": ["log"],
    },
    "live_screen_refresh": {
        "payload_fields": {"pending": "bool"},
        "visibility": ["status", "control"],
    },
    "config_reloaded": {
        "payload_fields": {"changed_keys": "list[str]"},
        "visibility": ["internal"],
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_known_event_kind(kind: str) -> bool:
    """Return True if *kind* is a registered ui_queue event kind."""
    return _is_known_event_kind(kind)


def get_event_schema(kind: str) -> dict[str, Any]:
    """Return the schema description for a known event kind.

    Raises ValueError for unknown kinds.
    """
    if kind not in _EVENT_SCHEMAS:
        raise ValueError(f"Unknown event kind: {kind!r}")
    return dict(_EVENT_SCHEMAS[kind])


def ui_tuple_to_ws_frame(kind: str, payload: object) -> str:
    """Convert a ui_queue (kind, payload) tuple into a JSON WebSocket frame.

    Raises ValueError for unknown event kinds.
    """
    if not _is_known_event_kind(kind):
        raise ValueError(f"Unknown event kind: {kind!r}")

    frontend_kind = get_frontend_event_name(kind)
    normalized_payload = _normalize_payload(kind, payload)

    frame = EventFrame(
        timestamp=_utc_timestamp(),
        kind=frontend_kind,
        source_kind=kind,
        payload=normalized_payload,
    )
    return json.dumps(frame.to_dict(), separators=(",", ":"))


def parse_action_frame(raw_json: str) -> tuple[str, dict[str, Any]]:
    """Parse an incoming JSON action frame and return (action_name, payload).

    Raises ValueError for invalid JSON, missing fields, or unknown action names.
    """
    try:
        data: dict[str, Any] = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid action JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Action frame must be a JSON object")

    if data.get("frame") != "action":
        raise ValueError("Action frame must have frame='action'")

    action_name = str(data.get("action") or "").strip()
    if not action_name:
        raise ValueError("Action frame missing 'action' field")

    if not is_known_action_name(action_name):
        raise ValueError(f"Unknown action name: {action_name!r}")

    payload = dict(data.get("payload") or {})
    return action_name, payload
