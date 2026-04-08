from __future__ import annotations

import queue
import time

import dearpygui.dearpygui as dpg

from config import CFG
from ui.controller_actions import handle_search_result, handle_show_image, refresh_live_screen_ui
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


def pump_ui_queue(controller) -> None:
    global _last_delta_time
    while True:
        try:
            kind, payload = controller.ui_queue.get_nowait()
        except queue.Empty:
            break

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
        if kind == "clear_thinking":
            controller.clear_thinking_placeholder()
            continue
        if kind == "chat_append":
            if isinstance(payload, dict):
                role = str(payload.get("role") or "system")
                content = str(payload.get("content") or "")
                if content:
                    controller.chat_append(role, content)
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
        if kind == "codex_escalation":
            if isinstance(payload, dict):
                controller.latest_codex_escalation = payload
                controller.latest_codex_brief_path = str(payload.get("brief_path") or "").strip()
                controller.latest_codex_summary = str(payload.get("summary") or "").strip()
                summary = controller.latest_codex_summary or "Codex support brief prepared."
                location = controller.latest_codex_brief_path
                line = f"[ENGINEERING SUPPORT] {summary}"
                if location:
                    line += f" ({location})"
                controller.log_agent_monitor(line)
                if bool(payload.get("manual")):
                    controller.chat_append("system", f"[UI] Codex support brief prepared: {location or 'data/debug/codex_escalations.jsonl'}")
                controller.queue_codex_repair(payload)
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
            handle_show_image(controller, str(payload))
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
