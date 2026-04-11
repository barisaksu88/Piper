from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict

import dearpygui.dearpygui as dpg

from config import CFG
from core.commands import handle_command
from core.engines.proactive_monitor import (
    build_proactive_consumed_message,
    build_proactive_trigger_message,
)
from core.orchestrator import run_agent_loop
from core.runtime_control import CancellationToken, OperationCancelled
from tools.screen_capture import ScreenCaptureError
from tools.vision import VisionError, VisionRequest, analyze_image, resolve_vision_request
from ui.event_speech import normalize_event_speech_mode


LIVE_SCREEN_MODE_LABELS = {
    "display": "Display",
    "window": "Window",
    "pointer": "Pointer",
}
LIVE_SCREEN_INTERVAL_OPTIONS = (2.0, 5.0, 10.0, 15.0)


def _clear_conversation_summary_file() -> None:
    _clear_conversation_summary_file_at()


def _clear_conversation_summary_file_at(path: Path | None = None) -> None:
    target = Path(path) if path is not None else CFG.CONVERSATION_SUMMARY_PATH
    try:
        target.unlink(missing_ok=True)
    except Exception:
        pass


def _refresh_active_user_style(controller) -> None:
    try:
        style_filename = str(controller.user_runtime.current_style_filename() or "").strip()
    except Exception:
        style_filename = ""
    if style_filename:
        controller.style_mgr.active_filename = style_filename
    style_state = controller.load_style_state()
    mode = style_state.name.upper() if style_state.name.lower() != "default" else ""
    controller.set_mode_indicator(f"MODE: {mode}" if mode else "")


def _render_user_list_message(controller) -> str:
    active_id = ""
    active_profile = None
    try:
        active_profile = controller.user_runtime.active_profile()
        active_id = active_profile.user_id
    except Exception:
        active_id = ""
    lines = ["[UI] Users:"]
    if getattr(active_profile, "is_unknown", False):
        lines.append("* Current speaker: Unknown [unknown; unknown] (not a saved profile)")
    try:
        profiles = controller.user_runtime.list_profiles()
    except Exception:
        profiles = []
    for profile in profiles:
        marker = "*" if profile.user_id == active_id else "-"
        role = str(controller.user_runtime.profile_role_label(profile) or "user")
        lines.append(f"{marker} {profile.name} [{profile.user_id}; {role}]")
    if len(lines) == 1:
        lines.append("- (none)")
    return "\n".join(lines)


def _render_active_user_message(controller) -> str:
    try:
        profile = controller.user_runtime.active_profile()
    except Exception:
        return "[UI] Active user unavailable."
    role = str(controller.user_runtime.profile_role_label(profile) or "user")
    return f"[UI] Active user: {profile.name} [{profile.user_id}; {role}]"


def _apply_active_user_switch(controller) -> None:
    try:
        controller.tts.stop()
    except Exception:
        pass
    try:
        controller.agent_brain.suspend_runtime_sessions()
    except Exception:
        pass
    controller.chat_state.bind_memory_path(controller.user_runtime.current_memory_path())
    controller.chat_state.begin_fresh_session(wipe_persistent=False)
    controller.session_meta = "Session: fresh"
    controller.stage_meta = ""
    controller.runtime_mode = "IDLE"
    controller.refresh_active_user_meta()
    _refresh_active_user_style(controller)
    controller.refresh_documents_view()
    controller._refresh_chat_ui()
    controller.refresh_interaction_state()


def _switch_active_user(controller, target: str) -> str:
    result = controller.user_runtime.request_typed_user_switch(target)
    if getattr(result, "switched", False):
        _apply_active_user_switch(controller)
    return str(getattr(result, "message", "") or "[UI] User switch failed.")


def _submit_admin_password(controller, raw_text: str) -> str:
    text = str(raw_text or "")
    lowered = text.strip().lower()
    if lowered in {"/cancel", "cancel"}:
        return controller.user_runtime.cancel_pending_admin_password()
    result = controller.user_runtime.submit_admin_password(text)
    if getattr(result, "switched", False):
        _apply_active_user_switch(controller)
    return str(getattr(result, "message", "") or "[UI] Admin sign-in failed.")


def _observe_typed_speaker_identity(controller, raw_text: str) -> tuple[bool, str]:
    result = controller.user_runtime.observe_typed_identity_hint(raw_text)
    if result is None:
        return False, ""
    if getattr(result, "requires_password", False):
        return True, str(getattr(result, "message", "") or "[UI] Password required.")
    if getattr(result, "requires_identity_clarification", False):
        return True, str(getattr(result, "message", "") or "[UI] I need one more detail to identify who is speaking.")
    if getattr(result, "switched", False):
        _apply_active_user_switch(controller)
    return False, ""


def reset_mic_ui(controller) -> None:
    controller.mic_state = "idle"
    if dpg.does_item_exist(controller.tags.mic_button):
        dpg.set_item_label(controller.tags.mic_button, "MIC")
        dpg.bind_item_theme(controller.tags.mic_button, 0)


def on_mic_toggle(controller) -> None:
    from tools.stt import get_stt_engine

    if not controller.boot_ready and controller.mic_state == "idle":
        return
    if controller.mic_state == "idle":
        controller.mic_state = "recording"
        if dpg.does_item_exist(controller.tags.mic_button):
            dpg.set_item_label(controller.tags.mic_button, "STOP")
            with dpg.theme() as temp_theme:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (200, 50, 50, 255))
                dpg.bind_item_theme(controller.tags.mic_button, temp_theme)
        controller.set_status("Listening...")
        try:
            engine = get_stt_engine()
            engine.start_recording()
        except Exception as exc:
            reset_mic_ui(controller)
            controller.set_status("Mic Error")
            controller.chat_append("system", f"[Mic Error: {exc}]")
        return

    reset_mic_ui(controller)
    controller.set_status("Transcribing...")
    try:
        engine = get_stt_engine()
        text = engine.stop_recording()
        controller.set_status("IDLE")
        if text:
            if dpg.does_item_exist(controller.tags.input_box):
                dpg.set_value(controller.tags.input_box, text)
            controller.on_send()
        else:
            controller.chat_append("system", "[No speech detected]")
    except Exception as exc:
        controller.set_status("Error")
        controller.chat_append("system", f"[STT Error: {exc}]")


def _finalize_operation(controller, token: CancellationToken) -> None:
    controller.release_cancel_token(token)
    if controller.has_active_operations():
        return
    controller.ui_queue.put(("status", "Canceled" if token.is_cancelled else "IDLE"))


def do_generate_stream(controller, cancel_token: CancellationToken | None = None) -> None:
    if not controller.gen_lock.acquire(blocking=False):
        return
    token = cancel_token or controller.create_cancel_token()
    controller.retain_cancel_token(token)
    try:
        token.raise_if_cancelled()
        run_agent_loop(controller.build_orchestrator_config(cancel_token=token))
    except OperationCancelled:
        controller.ui_queue.put(("status_widget_dashboard_activity", "Stop completed."))
    except Exception as exc:
        import traceback

        traceback.print_exc()
        controller.ui_queue.put(("error", f"Orchestrator Error: {exc}"))
    finally:
        controller.agent_brain.suspend_runtime_sessions()
        controller.ui_queue.put(("clear_thinking", ""))
        _finalize_operation(controller, token)
        controller.gen_lock.release()


def do_vision_query(
    controller,
    *,
    image_path: str,
    question: str,
    cancel_token: CancellationToken | None = None,
) -> None:
    if not controller.gen_lock.acquire(blocking=False):
        controller.clear_thinking_placeholder()
        return
    token = cancel_token or controller.create_cancel_token()
    controller.retain_cancel_token(token)
    style_state = controller.load_style_state()
    try:
        token.raise_if_cancelled()
        resolved = resolve_vision_request(
            VisionRequest(
                image_path=image_path,
                question=question,
            )
        )
        controller.ui_queue.put(
            (
                "status_widget_dashboard_activity",
                f"Analyzing image: {resolved.image_path.name}",
            )
        )
        controller.pipeline.handle_event(
            "start",
            "",
            tts_voice=style_state.tts_voice,
            tts_speed=style_state.tts_speed,
        )
        answer = analyze_image(
            controller.llm,
            request=resolved,
            style_overlay=style_state.overlay or "",
            temperature=0.2,
            max_tokens=400,
            cancel_token=token,
        )
        controller.pipeline.handle_event(
            "delta",
            answer,
            tts_voice=style_state.tts_voice,
            tts_speed=style_state.tts_speed,
        )
        controller.pipeline.handle_event(
            "end",
            "",
            tts_voice=style_state.tts_voice,
            tts_speed=style_state.tts_speed,
        )
    except OperationCancelled:
        controller.pipeline.handle_event(
            "cancel",
            "Canceled",
            tts_voice=style_state.tts_voice,
            tts_speed=style_state.tts_speed,
        )
        controller.ui_queue.put(("status_widget_dashboard_activity", "Vision query canceled."))
    except VisionError as exc:
        controller.clear_thinking_placeholder()
        controller.pipeline.handle_event("error", f"[UI] {exc}", tts_voice=None, tts_speed=None)
    except Exception as exc:
        controller.clear_thinking_placeholder()
        controller.pipeline.handle_event("error", f"Vision Error: {exc}", tts_voice=None, tts_speed=None)
    finally:
        _finalize_operation(controller, token)
        controller.gen_lock.release()


def _workspace_relative_image_path(path: Path) -> str:
    workspace_root = CFG.DATA_DIR / "workspace"
    try:
        return path.relative_to(workspace_root).as_posix()
    except Exception:
        return path.name


def _resolve_ui_image_path(raw_value: str) -> Path | None:
    text = str(raw_value or "").strip()
    if not text:
        return None

    raw_path = Path(text)
    workspace_root = CFG.DATA_DIR / "workspace"
    candidates = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    candidates.append(workspace_root / raw_path)
    candidates.append(workspace_root / raw_path.name)
    candidates.append(CFG.COMFY_OUTPUT_DIR / raw_path)
    candidates.append(CFG.COMFY_OUTPUT_DIR / raw_path.name)

    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate
    return None


def _format_live_screen_mode_label(mode: str) -> str:
    return LIVE_SCREEN_MODE_LABELS.get(str(mode or "").strip().lower(), "Display")


def _parse_live_screen_mode_label(label: object) -> str:
    text = str(label or "").strip().lower()
    for mode, mode_label in LIVE_SCREEN_MODE_LABELS.items():
        if text == mode or text == mode_label.lower():
            return mode
    return "display"


def _format_live_screen_interval_label(interval_s: float) -> str:
    rounded = int(round(float(interval_s)))
    return f"{rounded}s"


def _parse_live_screen_interval_label(label: object) -> float:
    text = str(label or "").strip().lower().removesuffix("s")
    try:
        value = float(text)
    except Exception:
        return 10.0
    if value <= 0:
        return 10.0
    return value


def _get_live_screen_theme(controller):
    existing = getattr(controller, "_live_screen_theme", None)
    if existing is not None and dpg.does_item_exist(existing):
        return existing

    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (54, 136, 86, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (66, 156, 99, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (42, 116, 73, 255))
    controller._live_screen_theme = theme
    return theme


def refresh_live_screen_ui(controller) -> None:
    enabled = False
    mode = "display"
    interval_s = 10.0
    if getattr(controller, "live_screen", None) is not None:
        try:
            enabled = controller.live_screen.is_enabled()
            mode = controller.live_screen.mode()
            interval_s = controller.live_screen.interval_s()
        except Exception:
            enabled = False
    pending = bool(getattr(controller, "live_screen_pending", False))
    mode_label = _format_live_screen_mode_label(mode)
    interval_label = _format_live_screen_interval_label(interval_s)

    if dpg.does_item_exist(controller.tags.snapshot_button):
        dpg.set_item_label(
            controller.tags.snapshot_button,
            "LIVE" if enabled else ("VISION..." if pending else "VISION"),
        )
        dpg.bind_item_theme(
            controller.tags.snapshot_button,
            _get_live_screen_theme(controller) if enabled else 0,
        )
    if dpg.does_item_exist(controller.tags.live_screen_mode_combo):
        dpg.set_value(controller.tags.live_screen_mode_combo, mode_label)
    if dpg.does_item_exist(controller.tags.live_screen_interval_combo):
        dpg.set_value(controller.tags.live_screen_interval_combo, interval_label)

    if pending and not enabled:
        controller.screen_meta = "Screen: STARTING"
    else:
        controller.screen_meta = f"Screen: {'LIVE' if enabled else 'OFF'} {mode_label} {interval_label}"
    controller.set_vision_session_active(enabled)
    controller._refresh_top_bar()


def _start_live_screen(controller) -> None:
    def on_capture(path: Path) -> None:
        rel_path = _workspace_relative_image_path(path)
        controller.ui_queue.put(("show_image", f"Image saved to: {rel_path}"))

    def on_error(message: str) -> None:
        controller.ui_queue.put(("status_widget_dashboard_activity", f"Live screen capture error: {message}"))

    try:
        path = controller.live_screen.start(on_capture=on_capture, on_error=on_error)
        rel_path = _workspace_relative_image_path(path)
        controller.ui_queue.put(("status_widget_dashboard_activity", f"Live screen mode enabled: {rel_path}"))
        controller.ui_queue.put(
            (
                "chat_append",
                {
                    "role": "system",
                    "content": "[UI] Live screen mode enabled. Piper will use the current screen image on new turns.",
                },
            )
        )
    except ScreenCaptureError as exc:
        try:
            controller.live_screen.stop()
        except Exception:
            pass
        controller.ui_queue.put(("status_widget_dashboard_activity", f"Live screen error: {exc}"))
        controller.ui_queue.put(("chat_append", {"role": "system", "content": f"[UI] Live screen error: {exc}"}))
    except Exception as exc:
        try:
            controller.live_screen.stop()
        except Exception:
            pass
        controller.ui_queue.put(("status_widget_dashboard_activity", f"Live screen error: {exc}"))
        controller.ui_queue.put(("chat_append", {"role": "system", "content": f"[UI] Live screen error: {exc}"}))
    finally:
        controller.ui_queue.put(("live_screen_refresh", {"pending": False}))


def _recapture_live_screen(controller) -> None:
    try:
        path = controller.live_screen.capture_once()
        rel_path = _workspace_relative_image_path(path)
        controller.ui_queue.put(("show_image", f"Image saved to: {rel_path}"))
    except Exception as exc:
        controller.ui_queue.put(("status_widget_dashboard_activity", f"Live screen refresh error: {exc}"))


def on_snapshot(controller) -> None:
    if not controller.boot_ready:
        return
    if getattr(controller, "live_screen_pending", False):
        return
    if controller.live_screen.is_enabled():
        controller.live_screen.stop()
        controller.live_screen_pending = False
        refresh_live_screen_ui(controller)
        controller.refresh_interaction_state()
        controller.ui_queue.put(("status_widget_dashboard_activity", "Live screen mode disabled."))
        controller.chat_append("system", "[UI] Live screen mode disabled.")
        return

    controller.live_screen_pending = True
    refresh_live_screen_ui(controller)
    controller.refresh_interaction_state()
    controller.ui_queue.put(("status_widget_dashboard_activity", "Starting live screen mode..."))
    threading.Thread(target=_start_live_screen, args=(controller,), daemon=True).start()


def on_live_screen_mode_changed(controller, sender=None, app_data=None, user_data=None) -> None:
    if not controller.boot_ready or getattr(controller, "live_screen_pending", False):
        return
    mode = _parse_live_screen_mode_label(app_data)
    controller.live_screen.set_mode(mode)
    refresh_live_screen_ui(controller)
    controller.ui_queue.put(
        ("status_widget_dashboard_activity", f"Live screen source set to {_format_live_screen_mode_label(mode)}.")
    )
    if controller.live_screen.is_enabled():
        threading.Thread(target=_recapture_live_screen, args=(controller,), daemon=True).start()


def on_live_screen_interval_changed(controller, sender=None, app_data=None, user_data=None) -> None:
    if not controller.boot_ready or getattr(controller, "live_screen_pending", False):
        return
    interval_s = _parse_live_screen_interval_label(app_data)
    controller.live_screen.set_interval(interval_s)
    refresh_live_screen_ui(controller)
    controller.ui_queue.put(
        ("status_widget_dashboard_activity", f"Live screen interval set to {_format_live_screen_interval_label(interval_s)}.")
    )


def on_new_session(controller) -> None:
    try:
        controller.tts.stop()
    except Exception:
        pass
    _clear_conversation_summary_file_at(Path(controller.user_runtime.current_conversation_summary_path()))
    controller.chat_state.new_session()
    controller.session_meta = "Session: fresh"
    controller.stage_meta = ""
    controller._refresh_chat_ui()
    style_state = controller.load_style_state()
    mode = style_state.name.upper() if style_state.name.lower() != "default" else ""
    controller.set_mode_indicator(f"MODE: {mode}" if mode else "")
    controller.refresh_interaction_state()


def on_clear(controller) -> None:
    _clear_conversation_summary_file_at(Path(controller.user_runtime.current_conversation_summary_path()))
    controller.chat_state.clear()
    controller.session_meta = "Session: active"
    controller.stage_meta = ""
    controller.runtime_mode = "IDLE"
    controller._refresh_top_bar()
    if dpg.does_item_exist(controller.tags.chat_text):
        dpg.delete_item(controller.tags.chat_text, children_only=True)
    controller._reset_chat_render_cache()
    if dpg.does_item_exist(controller.tags.input_box):
        dpg.set_value(controller.tags.input_box, "")
    controller.refresh_interaction_state()


def on_send(controller) -> None:
    if not controller.boot_ready:
        return
    if controller.has_active_operations():
        return

    raw = dpg.get_value(controller.tags.input_box) or ""
    input_text = str(raw).rstrip("\n")
    if not input_text.strip():
        dpg.set_value(controller.tags.input_box, "")
        return
    if controller.has_active_code_session():
        dpg.set_value(controller.tags.input_box, "")
        controller.activate_code_tab()
        sent = controller.send_code_session_input(input_text)
        if sent:
            controller.focus_code_input()
            controller.set_status("CODE SESSION")
        else:
            controller.set_status("IDLE")
        return

    user_text = input_text.strip()

    if controller.user_runtime.is_waiting_for_admin_password():
        controller.chat_append("system", _submit_admin_password(controller, input_text))
        dpg.set_value(controller.tags.input_box, "")
        return

    res = handle_command(user_text, style_mgr=controller.style_mgr)
    if res.handled:
        if res.action == "clear":
            controller.on_clear()
        elif res.action == "new_session":
            controller.on_new_session()
        elif res.action == "list_users":
            controller.chat_append("system", _render_user_list_message(controller))
        elif res.action == "show_active_user":
            controller.chat_append("system", _render_active_user_message(controller))
        elif res.action == "switch_user" and res.user_query:
            controller.chat_append("system", _switch_active_user(controller, res.user_query))
        elif res.action == "ingest_document" and res.document_path:
            _start_document_ingest(controller, [res.document_path])
        elif res.action == "vision_query" and res.vision_path and res.vision_prompt:
            controller.chat_append("user", user_text)
            controller.persist_turn("user", user_text)
            controller.session_meta = "Session: active"
            controller._refresh_top_bar()
            dpg.set_value(controller.tags.input_box, "")
            dpg.focus_item(controller.tags.input_box)
            controller.show_thinking_placeholder()
            threading.Thread(
                target=do_vision_query,
                kwargs={
                    "controller": controller,
                    "image_path": res.vision_path,
                    "question": res.vision_prompt,
                },
                daemon=True,
            ).start()
            return
        elif res.action == "codex_support":
            controller.export_codex_support_snapshot(res.support_note or "")
        elif res.action == "set_admin_password" and res.password_value is not None:
            outcome = controller.user_runtime.set_admin_password(res.password_value)
            controller.chat_append("system", outcome.message)
            dpg.set_value(controller.tags.input_box, "")
            return
        if res.style_filename:
            try:
                controller.user_runtime.set_active_style_filename(res.style_filename)
            except Exception:
                pass
            style_state = controller.load_style_state()
            mode = style_state.name.upper() if style_state.name.lower() != "default" else ""
            controller.set_mode_indicator(f"MODE: {mode}" if mode else "")
        if res.ui_message:
            controller.chat_append("system", res.ui_message)
        dpg.set_value(controller.tags.input_box, "")
        return

    handled_identity, identity_message = _observe_typed_speaker_identity(controller, user_text)
    if handled_identity:
        controller.chat_append("system", identity_message)
        dpg.set_value(controller.tags.input_box, "")
        return

    controller.submit_user_text(user_text)


def on_code_send(controller) -> None:
    if not controller.boot_ready or not controller.has_active_code_session():
        return
    if not dpg.does_item_exist(controller.tags.code_input_box):
        return
    raw = dpg.get_value(controller.tags.code_input_box) or ""
    text = str(raw).rstrip("\n")
    if not text:
        dpg.set_value(controller.tags.code_input_box, "")
        return
    sent = controller.send_code_session_input(text)
    if sent:
        dpg.set_value(controller.tags.code_input_box, "")
        controller.focus_code_input()


def on_code_run(controller) -> None:
    if not controller.boot_ready or controller.has_active_operations() or controller.has_active_code_session():
        return
    path = controller.current_code_preview_runnable_path()
    if not path:
        controller.set_code_status("No runnable .py file is visible in the current preview.")
        controller.refresh_interaction_state()
        return
    try:
        controller.activate_code_tab()
        controller.set_code_status(f"Launching: {path}")
        controller.start_code_session(path)
        controller.focus_code_input()
        controller.set_status("CODE SESSION")
    except Exception as exc:
        controller.set_code_status(f"Launch failed: {exc}")
        controller.append_code_output(f"\n[Run File failed: {exc}]\n")
    finally:
        controller.refresh_interaction_state()


def on_code_clear(controller) -> None:
    controller.clear_code_output()
    controller.refresh_interaction_state()


def _extract_selected_document_paths(app_data: object) -> list[str]:
    if not isinstance(app_data, dict):
        return []

    def _is_absolute_path(raw: str) -> bool:
        text = str(raw or "").strip()
        if not text:
            return False
        if text.startswith("/mnt/"):
            return True
        if len(text) > 2 and text[1] == ":" and text[2] in {"\\", "/"}:
            return True
        return Path(text).is_absolute()

    def _join_dialog_path(base: str, leaf: str) -> str:
        left = str(base or "").strip()
        right = str(leaf or "").strip()
        if not left:
            return right
        if not right or _is_absolute_path(right):
            return right
        sep = "\\" if "\\" in left or (len(left) > 1 and left[1] == ":") else "/"
        return left.rstrip("/\\") + sep + right.lstrip("/\\")

    current_path = str(app_data.get("current_path") or "").strip()
    selections = app_data.get("selections")
    paths: list[str] = []
    if isinstance(selections, dict) and selections:
        for key, value in selections.items():
            candidate = str(value or "").strip() or str(key or "").strip()
            if not candidate:
                continue
            if current_path and not _is_absolute_path(candidate):
                candidate = _join_dialog_path(current_path, candidate)
            paths.append(candidate)
    if paths:
        deduped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            normalized = str(path).strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped
    file_path_name = str(app_data.get("file_path_name") or "").strip()
    if file_path_name:
        return [file_path_name]
    file_name = str(app_data.get("file_name") or "").strip()
    if current_path and file_name:
        return [_join_dialog_path(current_path, file_name)]
    return []


def on_open_document_picker(controller) -> None:
    if not controller.boot_ready or controller.has_active_operations() or controller.document_ingest_active:
        return
    if dpg.does_item_exist("document_ingest_dialog"):
        dpg.configure_item("document_ingest_dialog", show=True)


def _start_document_ingest(controller, paths: list[str]) -> None:
    cleaned = [str(path).strip() for path in paths if str(path).strip()]
    if not cleaned:
        controller.chat_append("system", "[UI] No document selected.")
        return
    if controller.document_ingest_active:
        controller.chat_append("system", "[UI] Document ingest is already running.")
        return

    controller.document_ingest_active = True
    controller.refresh_interaction_state()

    names = [Path(path).name or str(path) for path in cleaned]
    if len(names) == 1:
        start_message = f"[UI] Ingesting document: {names[0]}"
    else:
        preview = ", ".join(names[:3])
        if len(names) > 3:
            preview += f" (+{len(names) - 3} more)"
        start_message = f"[UI] Ingesting {len(names)} documents: {preview}"
    controller.chat_append("system", start_message)

    def worker() -> None:
        summaries: list[str] = []
        try:
            for path in cleaned:
                name = Path(path).name or str(path)
                controller.ui_queue.put(("status_widget_dashboard_activity", f"Ingesting document: {name}"))
                result = controller.document_mgr.ingest_path(path)
                summaries.append(str(result.get("summary") or f"Document ingest failed: {name}"))
            controller.refresh_documents_view()
            controller.ui_queue.put(("chat_append", {"role": "system", "content": "[UI] " + " | ".join(summaries)}))
        except Exception as exc:
            controller.ui_queue.put(("chat_append", {"role": "system", "content": f"[UI] Document ingest failed: {exc}"}))
        finally:
            controller.ui_queue.put(("document_ingest_active", False))

    threading.Thread(target=worker, daemon=True).start()


def on_document_picker_selected(controller, sender=None, app_data=None, user_data=None) -> None:
    if dpg.does_item_exist("document_ingest_dialog"):
        dpg.configure_item("document_ingest_dialog", show=False)
    paths = _extract_selected_document_paths(app_data)
    if not paths:
        controller.chat_append("system", "[UI] No document selected.")
        return
    _start_document_ingest(controller, paths)


def on_document_picker_cancel(controller, sender=None, app_data=None, user_data=None) -> None:
    if dpg.does_item_exist("document_ingest_dialog"):
        dpg.configure_item("document_ingest_dialog", show=False)


def on_stop(controller) -> None:
    canceled_any = controller.cancel_active_operations()
    stopped_code = controller.stop_code_session()
    tts_was_active = controller.is_tts_active()
    controller.pipeline.handle_event("cancel", "Canceled", tts_voice=None, tts_speed=None)
    try:
        controller.tts.stop()
    except Exception:
        pass
    if canceled_any:
        controller.ui_queue.put(("status_widget_dashboard_activity", "Stop requested."))
        controller.set_status("Stopping...")
    elif stopped_code:
        controller.ui_queue.put(("status_widget_dashboard_activity", "Process stopped."))
        controller.set_status("IDLE")
    elif tts_was_active:
        controller.ui_queue.put(("status_widget_dashboard_activity", "Speech stopped."))
        controller.set_status("IDLE")
    else:
        controller.set_status("IDLE")
    controller.ui_queue.put(("ui_controls_refresh", ""))


def on_restart(controller) -> None:
    try:
        print("[System] Restart requested.")
        controller.restart_requested = True
        controller.set_status("Restarting...")
        controller.boot_mgr.shutdown()
        dpg.stop_dearpygui()
    except Exception as exc:
        print(f"[System] Restart failed: {exc}")


def on_event_speech_mode_changed(controller, sender=None, app_data=None, user_data=None) -> None:
    controller.set_event_speech_mode(normalize_event_speech_mode(app_data), announce=True)


def handle_show_image(controller, payload: str) -> None:
    try:
        fname = payload.split(": ")[-1].strip()
        img_path = _resolve_ui_image_path(fname)

        print(f"[UI] Attempting to load image: {img_path}")
        if img_path is None:
            print("[UI] File not found.")
            return

        workspace_target = CFG.DATA_DIR / "workspace" / img_path.name
        if img_path != workspace_target and img_path.parent == CFG.COMFY_OUTPUT_DIR:
            import shutil

            shutil.copy(img_path, workspace_target)
            img_path = workspace_target

        if img_path.exists():
            width, height, channels, data = dpg.load_image(str(img_path))

            parent_id = controller.tags.main_window
            if dpg.does_item_exist("image_pane"):
                try:
                    parent_id = dpg.get_item_parent("image_pane")
                except Exception:
                    pass

            if dpg.does_item_exist("image_pane"):
                dpg.delete_item("image_pane")
            if dpg.does_item_exist("generated_image_texture"):
                dpg.delete_item("generated_image_texture")
            if not dpg.does_item_exist("image_texture_registry"):
                dpg.add_texture_registry(tag="image_texture_registry")

            dpg.add_static_texture(
                width=width,
                height=height,
                default_value=data,
                tag="generated_image_texture",
                parent="image_texture_registry",
            )
            dpg.add_image("generated_image_texture", tag="image_pane", parent=parent_id)
            print("[UI] Visual Cortex updated.")
            controller.queue_visual_note(img_path)
    except Exception as exc:
        print(f"[UI] Critical Error loading image: {exc}")
        import traceback

        traceback.print_exc()


def handle_search_result(controller, payload: Dict[str, str]) -> None:
    query = payload.get("query", "")
    data = payload.get("data", "")
    cancel_token = payload.get("cancel_token")

    if isinstance(cancel_token, CancellationToken) and cancel_token.is_cancelled:
        return

    controller.ui_queue.put(("status_widget_dashboard_activity", "Summarizing findings..."))
    controller.chat_state.append_message(
        {
            "role": "system",
            "content": f"Background search complete for '{query}'. Data:\n{data[:16000]}",
            "hidden": True,
        }
    )
    controller.chat_state.append_message(
        {
            "role": "system",
            "content": "The web search is complete. Summarize the findings for the user now.",
            "hidden": True,
        }
    )
    controller._refresh_chat_ui()
    if isinstance(cancel_token, CancellationToken):
        controller.retain_cancel_token(cancel_token)

    def report_findings() -> None:
        acquired_lock = False
        try:
            while not controller.gen_lock.acquire(timeout=0.1):
                if isinstance(cancel_token, CancellationToken):
                    cancel_token.raise_if_cancelled()
            acquired_lock = True
            if isinstance(cancel_token, CancellationToken):
                cancel_token.raise_if_cancelled()
            run_agent_loop(controller.build_orchestrator_config(cancel_token=cancel_token))
        except OperationCancelled:
            controller.ui_queue.put(("status_widget_dashboard_activity", "Search summary canceled."))
        except Exception as exc:
            controller.ui_queue.put(("error", f"Async Report Error: {exc}"))
        finally:
            if isinstance(cancel_token, CancellationToken):
                _finalize_operation(controller, cancel_token)
            if acquired_lock:
                controller.gen_lock.release()

    worker = threading.Thread(target=report_findings, daemon=True)
    try:
        worker.start()
    except Exception:
        if isinstance(cancel_token, CancellationToken):
            controller.release_cancel_token(cancel_token)
        raise


def trigger_proactive_reminder(controller, reminder: Dict[str, object]) -> bool:
    reminder_id = str(reminder.get("id") or "").strip()
    if not reminder_id or not controller.can_dispatch_proactive_reminder():
        return False
    raw_message = build_proactive_trigger_message(reminder)
    token = controller.create_cancel_token()
    controller.retain_cancel_token(token)
    controller.retain_proactive_reminder_inflight(reminder_id)
    controller.ui_queue.put(("status_widget_dashboard_activity", "Scheduled reminder firing..."))
    controller.chat_state.append_message(
        {
            "role": "system",
            "content": raw_message,
            "hidden": True,
        }
    )

    def _run_trigger() -> None:
        acquired_lock = False
        completed_normally = False
        try:
            while not controller.gen_lock.acquire(timeout=0.1):
                token.raise_if_cancelled()
            acquired_lock = True
            token.raise_if_cancelled()
            run_agent_loop(controller.build_orchestrator_config(cancel_token=token))
            completed_normally = True
        except OperationCancelled:
            controller.ui_queue.put(("status_widget_dashboard_activity", "Reminder canceled."))
        except Exception as exc:
            controller.ui_queue.put(("error", f"Proactive Reminder Error: {exc}"))
        finally:
            if not completed_normally:
                consumed = {
                    "role": "system",
                    "content": build_proactive_consumed_message(reminder),
                    "hidden": True,
                }
                controller.chat_state.replace_last_system_message(raw_message, consumed)
            controller.release_proactive_reminder_inflight(reminder_id)
            _finalize_operation(controller, token)
            if acquired_lock:
                controller.gen_lock.release()

    worker = threading.Thread(target=_run_trigger, daemon=True)
    try:
        worker.start()
    except Exception:
        controller.release_proactive_reminder_inflight(reminder_id)
        controller.release_cancel_token(token)
        consumed = {
            "role": "system",
            "content": build_proactive_consumed_message(reminder),
            "hidden": True,
        }
        controller.chat_state.replace_last_system_message(raw_message, consumed)
        raise
    return True
