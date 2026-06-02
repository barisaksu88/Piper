from __future__ import annotations

import logging
import queue
import time

import dearpygui.dearpygui as dpg

_LOG = logging.getLogger(__name__)

from config import CFG
from ui.controller_actions import (
    _refresh_active_user_style,
    handle_search_result,
    handle_show_image,
    refresh_live_screen_ui,
)
from ui.controller_render import append_bounded_line_block
from ui.controller_status import classify_runtime_mode

# Minimum interval between streaming delta UI updates (seconds).
# Ensures deltas are visually distinguishable even when vsync is off
# or the render loop runs very fast.
_MIN_DELTA_INTERVAL = 0.016  # ~60 fps cap for streaming
_last_delta_time: float = 0.0


def _activate_boot_ready_ui(controller, payload: object) -> None:
    if dpg.does_item_exist("boot_group"):
        dpg.configure_item("boot_group", show=False)
    if dpg.does_item_exist("status_group"):
        dpg.configure_item("status_group", show=True)
    controller.set_boot_ready(True)
    controller._refresh_chat_ui()
    controller.refresh_stats_view()
    controller.maybe_speak_ui_event("boot_ready", payload)


def pump_ui_queue(controller, forward_queue: queue.Queue | None = None) -> None:
    global _last_delta_time
    while True:
        try:
            kind, payload = controller.ui_queue.get_nowait()
        except queue.Empty:
            break

        if forward_queue is not None:
            forward_queue.put((kind, payload))

        # Stream deltas must be processed one per frame for smooth streaming.
        # After each delta, return control to the render loop immediately.
        if kind == "assistant_stream_delta":
            text = str(payload.get("text") or "") if isinstance(payload, dict) else str(payload or "")
            now = time.perf_counter()
            elapsed = now - _last_delta_time
            if CFG.DEBUG_STREAMING_PIPELINE:
                print(f"[STREAM] delta ({elapsed*1000:.0f}ms gap) text={text!r:.60}")
            # Throttle: if we just processed a delta, wait until the next
            # frame boundary so the render loop can display it.
            if elapsed < _MIN_DELTA_INTERVAL:
                time.sleep(max(0.0, _MIN_DELTA_INTERVAL - elapsed))
            controller.pipeline.handle_event("delta", text)
            _last_delta_time = time.perf_counter()
            break  # Return to render loop so this chunk is displayed

        if kind == "boot_log":
            if dpg.does_item_exist(controller.tags.boot_log_text):
                current = dpg.get_value(controller.tags.boot_log_text)
                dpg.set_value(controller.tags.boot_log_text, current + str(payload) + "\n")
                controller.refresh_text_view_height(controller.tags.boot_log_text)
                controller.request_autoscroll(controller.tags.boot_log_child)
            controller.maybe_speak_ui_event(kind, payload)
            continue
        if kind == "boot_ready":
            if time.perf_counter() < float(getattr(controller, "_boot_ui_min_visible_until", 0.0)):
                controller._pending_boot_ready = True
                controller._pending_boot_ready_payload = payload
            else:
                _activate_boot_ready_ui(controller, payload)
            continue

        if kind == "ui_controls_refresh":
            controller.refresh_interaction_state()
            continue
        if kind == "active_user_changed":
            preserve_transcript = bool(isinstance(payload, dict) and payload.get("preserve_transcript"))
            captured_messages: list[dict[str, str]] = []
            if preserve_transcript:
                try:
                    captured_messages = [
                        dict(message)
                        for message in controller.chat_state.get_messages_snapshot()
                        if str(message.get("role") or "").lower() in ("user", "assistant")
                        and str(message.get("content") or "").strip() not in {"Thinking...", "Thinking…"}
                    ]
                except Exception:
                    captured_messages = []
            try:
                controller.chat_state.bind_memory_path(controller.user_runtime.current_memory_path())
            except Exception:
                pass
            if preserve_transcript and captured_messages:
                try:
                    for message in captured_messages:
                        controller.chat_state.persist_turn(message["role"], message["content"])
                except Exception:
                    pass
                controller.session_meta = "Session: active"
            controller.refresh_active_user_meta()
            try:
                _refresh_active_user_style(controller)
            except Exception:
                pass
            controller._refresh_chat_ui()
            controller.refresh_interaction_state()
            continue
        if kind == "clear_thinking":
            controller.clear_thinking_placeholder()
            continue
        if kind == "chat_append":
            if isinstance(payload, dict):
                if not payload.get("_state_synced"):
                    role = str(payload.get("role") or "system")
                    content = str(payload.get("content") or "")
                    if content:
                        controller.chat_append(role, content)
                else:
                    # Already in chat_state; just ensure DPG reflects the latest state.
                    controller._refresh_chat_ui()
            continue
        if kind == "document_ingest_active":
            controller.document_ingest_active = bool(payload)
            controller.refresh_interaction_state()
            continue
        if kind == "live_screen_refresh":
            if isinstance(payload, dict) and "pending" in payload:
                controller.live_screen_pending = bool(payload.get("pending"))
            refresh_live_screen_ui(controller)
            controller.refresh_interaction_state()
            continue

        if kind == "agent_log":
            controller.log_agent_monitor(str(payload))
            controller.maybe_speak_ui_event(kind, payload)
            continue
        if kind == "status":
            controller.set_status(str(payload))
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "status_widget_mode":
            controller.runtime_mode = classify_runtime_mode(controller._clean_ui_text(payload))
            if controller.runtime_mode == "IDLE":
                controller.stage_meta = ""
            controller._refresh_top_bar()
            continue
        if kind == "status_widget_step":
            controller._set_stage_meta(str(payload))
            continue
        if kind == "status_widget_dashboard_activity":
            if dpg.does_item_exist(controller.tags.dashboard_activity_text):
                current = dpg.get_value(controller.tags.dashboard_activity_text)
                dpg.set_value(
                    controller.tags.dashboard_activity_text,
                    append_bounded_line_block(current, str(payload), max_lines=50),
                )
                controller.refresh_text_view_height(controller.tags.dashboard_activity_text)
                controller.request_autoscroll(controller.tags.dashboard_activity_child)
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "assistant_stream_start":
            tts_voice = None
            tts_speed = None
            if isinstance(payload, dict):
                tts_voice = payload.get("tts_voice")
                tts_speed = payload.get("tts_speed")
            if CFG.DEBUG_STREAMING_PIPELINE:
                print(f"[STREAM] START (voice={tts_voice})")
            controller.pipeline.handle_event("start", "", tts_voice=tts_voice, tts_speed=tts_speed)
            continue
        if kind == "assistant_stream_end":
            if CFG.DEBUG_STREAMING_PIPELINE:
                print("[STREAM] END")
            controller.pipeline.handle_event("end", "", tts_voice=None, tts_speed=None)
            continue
        if kind == "error":
            controller.pipeline.handle_event("error", str(payload), tts_voice=None, tts_speed=None)
            controller.maybe_speak_ui_event(kind, payload)
            continue
        if kind == "show_image":
            handle_show_image(controller, payload)
            continue
        if kind == "code_session_launch":
            try:
                controller.activate_code_tab()
                controller.set_code_status("Launching embedded process...")
                path = str((payload or {}).get("path") or "").strip()
                if path:
                    controller.start_code_session(path)
            except Exception as exc:
                controller.set_code_session_active(False)
                controller.set_code_status(f"Launch failed: {exc}")
                controller.append_code_output(f"\n[Embedded code launch failed: {exc}]\n")
                controller.refresh_interaction_state()
            controller.maybe_speak_ui_event(kind, payload)
            continue
        if kind == "code_session_reset":
            controller.clear_code_output()
            continue
        if kind == "code_session_output":
            controller.append_code_output(str(payload))
            continue
        if kind == "code_session_status":
            controller.set_code_status(str(payload))
            controller.maybe_speak_ui_event(kind, payload)
            continue
        if kind == "code_session_active":
            controller.set_code_session_active(bool(payload))
            if not controller.code_session_active and not controller.has_active_operations():
                controller.set_status("IDLE")
            continue
        if kind == "code_session_focus":
            controller.activate_code_tab()
            controller.focus_code_input()
            continue
        if kind == "code_view":
            if not controller.has_active_code_session() and dpg.does_item_exist(controller.tags.code_view_text):
                controller.replace_code_output(str(payload))
                controller.refresh_interaction_state()
            continue
        if kind == "documents_view":
            if dpg.does_item_exist(controller.tags.documents_view_text):
                dpg.set_value(controller.tags.documents_view_text, str(payload))
                controller.refresh_text_view_height(controller.tags.documents_view_text)
                controller.request_autoscroll(controller.tags.documents_view_child)
            continue
        if kind == "stats_view_refresh":
            controller.refresh_stats_view()
            continue
        if kind == "search_result":
            controller.maybe_speak_ui_event(kind, payload)
            handle_search_result(controller, payload)
            continue
        if kind == "vision_snapshot_note":
            note_text = ""
            speak = False
            if isinstance(payload, dict):
                note_text = str(payload.get("text") or "").strip()
                speak = bool(payload.get("speak"))
            else:
                note_text = str(payload or "").strip()
            if dpg.does_item_exist(controller.tags.dashboard_activity_text):
                current = dpg.get_value(controller.tags.dashboard_activity_text)
                line = f"Vision note: {note_text}"
                dpg.set_value(
                    controller.tags.dashboard_activity_text,
                    append_bounded_line_block(current, str(line), max_lines=50),
                )
                controller.refresh_text_view_height(controller.tags.dashboard_activity_text)
                controller.request_autoscroll(controller.tags.dashboard_activity_child)
            if speak:
                controller.maybe_speak_ui_event(kind, note_text)
            continue

    if (
        getattr(controller, "_pending_boot_ready", False)
        and not getattr(controller, "boot_ready", False)
        and time.perf_counter() >= float(getattr(controller, "_boot_ui_min_visible_until", 0.0))
    ):
        payload = getattr(controller, "_pending_boot_ready_payload", "")
        controller._pending_boot_ready = False
        controller._pending_boot_ready_payload = ""
        _activate_boot_ready_ui(controller, payload)


def pump_ui_queue_web(controller, forward_queue: queue.Queue | None = None) -> None:
    """Web-mode variant of pump_ui_queue: updates controller state without touching DPG.

    DearPyGui functions cause a native hard exit when called without an active DPG
    context (see notes/coder-log.md).  This function processes the same ui_queue
    events as pump_ui_queue but skips every DPG-dependent code path, making it safe
    to call from run_web.
    """
    while True:
        try:
            kind, payload = controller.ui_queue.get_nowait()
        except queue.Empty:
            break

        if kind == "assistant_stream_delta":
            text = str(payload.get("text") or "") if isinstance(payload, dict) else str(payload or "")
            prev_clean = controller.pipeline.clean_stream_buffer
            controller.pipeline.handle_event("delta", text)
            new_clean = controller.pipeline.clean_stream_buffer
            clean_delta = new_clean[len(prev_clean):]
            if forward_queue is not None:
                forward_queue.put((kind, {"text": clean_delta}))
            continue

        if kind == "active_user_changed":
            enriched = dict(payload) if isinstance(payload, dict) else {}
            try:
                profile = controller.user_runtime.active_profile()
                enriched["user_name"] = str(getattr(profile, "name", "") or "")
                enriched["user_id"] = str(getattr(profile, "user_id", "") or "")
                enriched["role"] = str(getattr(profile, "role", "") or "")
            except Exception:
                pass
            preserve_transcript = bool(enriched.get("preserve_transcript"))
            captured_messages: list[dict[str, str]] = []
            if preserve_transcript:
                try:
                    captured_messages = [
                        dict(message)
                        for message in controller.chat_state.get_messages_snapshot()
                        if str(message.get("role") or "").lower() in ("user", "assistant")
                        and str(message.get("content") or "").strip() not in {"Thinking...", "Thinking…"}
                    ]
                except Exception:
                    captured_messages = []
            try:
                controller.chat_state.bind_memory_path(controller.user_runtime.current_memory_path())
            except Exception:
                pass
            if preserve_transcript and captured_messages:
                try:
                    for message in captured_messages:
                        controller.chat_state.persist_turn(message["role"], message["content"])
                except Exception:
                    pass
                controller.session_meta = "Session: active"
            label = ""
            try:
                label = str(controller.user_runtime.active_user_label() or "").strip()
            except Exception:
                label = ""
            controller.user_meta = f"User: {label}" if label else ""
            try:
                _refresh_active_user_style(controller)
            except Exception:
                pass
            if forward_queue is not None:
                forward_queue.put((kind, enriched))
                payload_fn = getattr(controller, "web_style_status_payload", None)
                if callable(payload_fn):
                    forward_queue.put(("style_status", payload_fn()))
            continue

        if kind == "search_result":
            _LOG.info("[SEARCH WEB] Handling search_result event.")
            controller.maybe_speak_ui_event(kind, payload)
            handle_search_result(controller, payload)
            if forward_queue is not None:
                forward_queue.put((kind, payload))
            continue

        if kind == "status_widget_mode":
            controller.runtime_mode = classify_runtime_mode(controller._clean_ui_text(payload))
            if controller.runtime_mode == "IDLE":
                controller.stage_meta = ""
            forwarded_payload = "GENERATING" if controller.runtime_mode == "SPEAKING" else payload
            if forward_queue is not None:
                forward_queue.put((kind, forwarded_payload))
            continue

        if kind == "stats_view_refresh":
            enriched_payload = payload if isinstance(payload, dict) else {}
            try:
                snapshot = controller.stats_collector.build_dashboard_snapshot()
                if isinstance(snapshot, dict):
                    enriched_payload = snapshot
            except Exception:
                pass
            if forward_queue is not None:
                forward_queue.put((kind, enriched_payload))
            continue

        if forward_queue is not None:
            forward_queue.put((kind, payload))

        if kind == "boot_log":
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "boot_ready":
            if time.perf_counter() < float(getattr(controller, "_boot_ui_min_visible_until", 0.0)):
                controller._pending_boot_ready = True
                controller._pending_boot_ready_payload = payload
            else:
                controller.boot_ready = True
                controller.maybe_speak_ui_event("boot_ready", payload)
            continue

        if kind == "ui_controls_refresh":
            continue

        if kind == "clear_thinking":
            controller.chat_state.remove_last_assistant_if_exact(controller.thinking_placeholder)
            continue

        if kind == "chat_append":
            if isinstance(payload, dict) and not payload.get("_state_synced"):
                role = str(payload.get("role") or "system")
                content = str(payload.get("content") or "")
                if content:
                    controller.chat_state.append(role, content)
            continue

        if kind == "document_ingest_active":
            controller.document_ingest_active = bool(payload)
            continue

        if kind == "live_screen_refresh":
            if isinstance(payload, dict) and "pending" in payload:
                controller.live_screen_pending = bool(payload.get("pending"))
            continue

        if kind == "agent_log":
            print(f"[MONITOR] {payload}")
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "status":
            from ui.controller_status import clean_ui_text, STAGE_STEP_RE
            clean = clean_ui_text(str(payload))
            if clean:
                stage_match = STAGE_STEP_RE.search(clean)
                if stage_match:
                    stage_num = stage_match.group(1)
                    total_stages = stage_match.group(2)
                    step_num = stage_match.group(3)
                    stage_label = f"Stage {stage_num}/{total_stages}" if total_stages else f"Stage {stage_num}"
                    controller.stage_meta = f"{stage_label} | Step {step_num}"
                    controller.runtime_mode = "THINKING"
                else:
                    controller.stage_meta = ""
                    controller.runtime_mode = classify_runtime_mode(clean)
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "status_widget_step":
            controller.stage_meta = str(payload or "").strip()
            continue

        if kind == "status_widget_dashboard_activity":
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "assistant_stream_start":
            tts_voice = None
            tts_speed = None
            if isinstance(payload, dict):
                tts_voice = payload.get("tts_voice")
                tts_speed = payload.get("tts_speed")
            controller.pipeline.handle_event("start", "", tts_voice=tts_voice, tts_speed=tts_speed)
            continue

        if kind == "assistant_stream_end":
            controller.pipeline.handle_event("end", "", tts_voice=None, tts_speed=None)
            if forward_queue is not None:
                # Intentional extra status events so the frontend settles to idle.
                forward_queue.put(("status_widget_mode", "IDLE"))
                forward_queue.put(("status", "IDLE"))
            continue

        if kind == "error":
            controller.pipeline.handle_event("error", str(payload), tts_voice=None, tts_speed=None)
            if forward_queue is not None:
                # Intentional extra status events so the frontend shows error state.
                forward_queue.put(("status_widget_mode", "ERROR"))
                forward_queue.put(("status", "Error"))
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "code_session_active":
            controller.code_session_active = bool(payload)
            if controller.code_session_active:
                script_path = controller.code_session.active_script().strip()
                controller.code_session_meta = f"Code: {script_path}" if script_path else "Code: interactive"
                controller.stage_meta = ""
                if controller.runtime_mode in {"IDLE", "CANCELED", "CODE SESSION"}:
                    controller.runtime_mode = "CODE SESSION"
            else:
                controller.code_session_meta = ""
                if controller.runtime_mode == "CODE SESSION":
                    controller.runtime_mode = "IDLE"
            continue

        if kind == "code_session_launch":
            try:
                controller.set_code_status("Launching embedded process...")
                path = str((payload or {}).get("path") or "").strip()
                if path:
                    controller.start_code_session(path)
            except Exception as exc:
                controller.set_code_session_active(False)
                controller.set_code_status(f"Launch failed: {exc}")
                controller.append_code_output(f"\n[Embedded code launch failed: {exc}]\n")
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "code_session_output":
            controller.append_code_output(str(payload))
            continue

        if kind == "code_session_status":
            controller.set_code_status(str(payload))
            controller.maybe_speak_ui_event(kind, payload)
            continue

        if kind == "code_session_focus":
            controller.focus_code_input()
            continue

        if kind == "code_view":
            if not controller.has_active_code_session():
                controller.replace_code_output(str(payload))
                controller.refresh_interaction_state()
            continue

        if kind == "config_reloaded":
            continue

        if kind == "vision_snapshot_note":
            note_text = ""
            speak = False
            if isinstance(payload, dict):
                note_text = str(payload.get("text") or "").strip()
                speak = bool(payload.get("speak"))
            else:
                note_text = str(payload or "").strip()
            if speak:
                controller.maybe_speak_ui_event(kind, note_text)
            continue

    if (
        getattr(controller, "_pending_boot_ready", False)
        and not getattr(controller, "boot_ready", False)
        and time.perf_counter() >= float(getattr(controller, "_boot_ui_min_visible_until", 0.0))
    ):
        payload = getattr(controller, "_pending_boot_ready_payload", "")
        controller._pending_boot_ready = False
        controller._pending_boot_ready_payload = ""
        controller.boot_ready = True
        controller.maybe_speak_ui_event("boot_ready", payload)
