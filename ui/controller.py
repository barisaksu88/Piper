from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import dearpygui.dearpygui as dpg

from config import CFG
from core.code_session import EmbeddedCodeSession
from core.engines.proactive_monitor import ProactiveMonitor
from core.engines.stats_collector import StatsCollector
from core.pipeline import ChatPipeline
from core.runtime_control import CancellationToken
from ui.layout import build_ui
from ui.event_speech import (
    EVENT_SPEECH_OFF,
    event_speech_message,
    event_speech_mode_label,
    normalize_event_speech_mode,
)
from ui.vision_commentary import (
    VISION_COMMENT_SKIP_TOKEN,
    build_vision_comment_prompt,
    recent_user_vision_context,
)
from ui.controller_actions import (
    do_generate_stream as do_generate_stream_action,
    on_clear as on_clear_action,
    on_code_clear as on_code_clear_action,
    on_code_run as on_code_run_action,
    on_code_send as on_code_send_action,
    on_document_picker_cancel as on_document_picker_cancel_action,
    on_document_picker_selected as on_document_picker_selected_action,
    on_open_document_picker as on_open_document_picker_action,
    on_mic_toggle as on_mic_toggle_action,
    on_new_session as on_new_session_action,
    on_restart as on_restart_action,
    on_event_speech_mode_changed as on_event_speech_mode_changed_action,
    on_live_screen_interval_changed as on_live_screen_interval_changed_action,
    on_live_screen_mode_changed as on_live_screen_mode_changed_action,
    on_snapshot as on_snapshot_action,
    on_send as on_send_action,
    on_stop as on_stop_action,
    refresh_live_screen_ui as refresh_live_screen_ui_action,
    reset_mic_ui as reset_mic_ui_action,
    submit_text_input as submit_text_input_action,
    trigger_proactive_reminder as trigger_proactive_reminder_action,
)
from ui.controller_queue import pump_ui_queue as pump_ui_queue_action
from ui.controller_render import format_chat_message_block, renderable_chat_messages
from ui.controller_status import (
    clean_ui_text,
    classify_runtime_mode,
    log_agent_monitor as log_agent_monitor_action,
    refresh_top_bar,
    set_mode_indicator as set_mode_indicator_action,
    set_stage_meta as set_stage_meta_action,
    set_status as set_status_action,
)
from tools.vision import VisionResolvedRequest, analyze_image
from core.search.searxng_service import SearXNGService
from memory.vision_session import VisionSessionMemory


RESTART_EXIT_CODE = 85
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class UiTags:
    main_window: str = "main_window"
    chat_child: str = "chat_child"
    chat_text: str = "chat_text"
    input_box: str = "input_box"
    send_button: str = "send_button"
    stop_button: str = "stop_button"
    clear_session_button: str = "clear_session_button"
    status_text: str = "status_text"
    mode_indicator: str = "mode_indicator"
    boot_log_child: str = "boot_log_child"
    boot_log_text: str = "boot_log_text"
    dashboard_activity_child: str = "dashboard_activity_child"
    dashboard_activity_text: str = "dashboard_activity"
    agent_log_text: str = "agent_log_text"
    agent_log_child: str = "agent_log_child"
    mic_button: str = "mic_button"
    snapshot_button: str = "snapshot_button"
    live_screen_mode_combo: str = "live_screen_mode_combo"
    live_screen_interval_combo: str = "live_screen_interval_combo"
    event_speech_combo: str = "event_speech_combo"
    restart_button: str = "restart_button"
    ingest_button: str = "ingest_button"
    main_tab_bar: str = "main_tab_bar"
    code_tab: str = "code_tab"
    stats_tab: str = "stats_tab"
    code_view_child: str = "code_view_child"
    code_view_text: str = "code_view_text"
    code_status_text: str = "code_status_text"
    code_input_box: str = "code_input_box"
    code_send_button: str = "code_send_button"
    code_run_button: str = "code_run_button"
    code_clear_button: str = "code_clear_button"
    code_stop_button: str = "code_stop_button"
    documents_view_child: str = "documents_view_child"
    documents_view_text: str = "documents_view_text"
    stats_view_child: str = "stats_view_child"
    stats_view_text: str = "stats_view_text"
    stats_latency_plot: str = "stats_latency_plot"
    stats_latency_x_axis: str = "stats_latency_x_axis"
    stats_latency_y_axis: str = "stats_latency_y_axis"
    stats_total_series: str = "stats_total_series"
    stats_total_upper_series: str = "stats_total_upper_series"
    stats_total_outlier_series: str = "stats_total_outlier_series"
    stats_phase_plot: str = "stats_phase_plot"
    stats_phase_x_axis: str = "stats_phase_x_axis"
    stats_phase_y_axis: str = "stats_phase_y_axis"
    stats_route_series: str = "stats_route_series"
    stats_manager_series: str = "stats_manager_series"
    stats_reporter_series: str = "stats_reporter_series"
    stats_persona_series: str = "stats_persona_series"
    stats_tts_series: str = "stats_tts_series"
    stats_workload_group: str = "stats_workload_group"
    stats_workload_plot: str = "stats_workload_plot"
    stats_workload_x_axis: str = "stats_workload_x_axis"
    stats_workload_y_axis: str = "stats_workload_y_axis"
    stats_planner_series: str = "stats_planner_series"
    stats_executor_series: str = "stats_executor_series"

    def for_layout(self) -> Dict[str, str]:
        return {
            "TAG_MAIN_WINDOW": self.main_window,
            "TAG_CHAT_CHILD": self.chat_child,
            "TAG_CHAT_TEXT": self.chat_text,
            "TAG_INPUT": self.input_box,
            "TAG_SEND_BUTTON": self.send_button,
            "TAG_STOP_BUTTON": self.stop_button,
            "TAG_CLEAR_SESSION_BUTTON": self.clear_session_button,
            "TAG_STATUS": self.status_text,
            "TAG_MODE_INDICATOR": self.mode_indicator,
            "TAG_BOOT_LOG_CHILD": self.boot_log_child,
            "TAG_BOOT_LOG_TEXT": self.boot_log_text,
            "TAG_DASHBOARD_ACTIVITY_CHILD": self.dashboard_activity_child,
            "TAG_DASHBOARD_ACTIVITY_TEXT": self.dashboard_activity_text,
            "TAG_AGENT_LOG_TEXT": self.agent_log_text,
            "TAG_AGENT_LOG_CHILD": self.agent_log_child,
            "TAG_MIC_BUTTON": self.mic_button,
            "TAG_SNAPSHOT_BUTTON": self.snapshot_button,
            "TAG_LIVE_SCREEN_MODE_COMBO": self.live_screen_mode_combo,
            "TAG_LIVE_SCREEN_INTERVAL_COMBO": self.live_screen_interval_combo,
            "TAG_EVENT_SPEECH_COMBO": self.event_speech_combo,
            "TAG_RESTART_BUTTON": self.restart_button,
            "TAG_INGEST_BUTTON": self.ingest_button,
            "TAG_MAIN_TAB_BAR": self.main_tab_bar,
            "TAG_CODE_TAB": self.code_tab,
            "TAG_STATS_TAB": self.stats_tab,
            "TAG_CODE_VIEW_CHILD": self.code_view_child,
            "TAG_CODE_VIEW_TEXT": self.code_view_text,
            "TAG_CODE_STATUS_TEXT": self.code_status_text,
            "TAG_CODE_INPUT": self.code_input_box,
            "TAG_CODE_SEND_BUTTON": self.code_send_button,
            "TAG_CODE_RUN_BUTTON": self.code_run_button,
            "TAG_CODE_CLEAR_BUTTON": self.code_clear_button,
            "TAG_CODE_STOP_BUTTON": self.code_stop_button,
            "TAG_DOCUMENTS_VIEW_CHILD": self.documents_view_child,
            "TAG_DOCUMENTS_VIEW_TEXT": self.documents_view_text,
            "TAG_STATS_VIEW_CHILD": self.stats_view_child,
            "TAG_STATS_VIEW_TEXT": self.stats_view_text,
            "TAG_STATS_LATENCY_PLOT": self.stats_latency_plot,
            "TAG_STATS_LATENCY_X_AXIS": self.stats_latency_x_axis,
            "TAG_STATS_LATENCY_Y_AXIS": self.stats_latency_y_axis,
            "TAG_STATS_TOTAL_SERIES": self.stats_total_series,
            "TAG_STATS_TOTAL_UPPER_SERIES": self.stats_total_upper_series,
            "TAG_STATS_TOTAL_OUTLIER_SERIES": self.stats_total_outlier_series,
            "TAG_STATS_PHASE_PLOT": self.stats_phase_plot,
            "TAG_STATS_PHASE_X_AXIS": self.stats_phase_x_axis,
            "TAG_STATS_PHASE_Y_AXIS": self.stats_phase_y_axis,
            "TAG_STATS_ROUTE_SERIES": self.stats_route_series,
            "TAG_STATS_MANAGER_SERIES": self.stats_manager_series,
            "TAG_STATS_REPORTER_SERIES": self.stats_reporter_series,
            "TAG_STATS_PERSONA_SERIES": self.stats_persona_series,
            "TAG_STATS_TTS_SERIES": self.stats_tts_series,
            "TAG_STATS_WORKLOAD_GROUP": self.stats_workload_group,
            "TAG_STATS_WORKLOAD_PLOT": self.stats_workload_plot,
            "TAG_STATS_WORKLOAD_X_AXIS": self.stats_workload_x_axis,
            "TAG_STATS_WORKLOAD_Y_AXIS": self.stats_workload_y_axis,
            "TAG_STATS_PLANNER_SERIES": self.stats_planner_series,
            "TAG_STATS_EXECUTOR_SERIES": self.stats_executor_series,
        }


class PiperController:
    def __init__(
        self,
        *,
        app_title: str,
        width: int,
        height: int,
        ui_queue: "queue.Queue[tuple[str, object]]",
        chat_state,
        style_mgr,
        tts,
        llm,
        knowledge_mgr,
        document_mgr,
        agent_brain,
        prompt_context_service,
        user_runtime,
        boot_mgr,
        img_gen,
        live_screen,
        vision_session_memory: VisionSessionMemory,
        searxng_service: SearXNGService | None = None,
        tags: UiTags | None = None,
    ) -> None:
        self.app_title = app_title
        self.width = width
        self.height = height
        self.tags = tags or UiTags()

        self.ui_queue = ui_queue
        self.chat_state = chat_state
        self.style_mgr = style_mgr
        self.tts = tts
        self.llm = llm
        self.knowledge_mgr = knowledge_mgr
        self.document_mgr = document_mgr
        self.agent_brain = agent_brain
        self.prompt_context_service = prompt_context_service
        self.user_runtime = user_runtime
        self.boot_mgr = boot_mgr
        self.img_gen = img_gen
        self.live_screen = live_screen
        self.vision_session_memory = vision_session_memory
        self.searxng_service = searxng_service
        self.code_session = EmbeddedCodeSession(
            self.agent_brain.workspace,
            lambda kind, payload: self.ui_queue.put((kind, payload)),
        )
        self.stats_collector = StatsCollector(CFG.STATS_PATH, CFG.STATS_ALERTS_PATH)

        self.gen_lock = threading.Lock()
        self.cancel_lock = threading.Lock()
        self.cancel_tokens: dict[CancellationToken, int] = {}
        self._search_in_flight_count = 0
        self._active_search_query = ""
        self._proactive_reminder_inflight_ids: set[str] = set()
        self.pending_autoscrolls: dict[str, int] = {}
        self.mic_state = "idle"
        self.restart_requested = False
        self.boot_ready = False
        self.runtime_mode = "IDLE"
        self.session_meta = "Session: active"
        self.user_meta = ""
        self.stage_meta = ""
        self.code_session_meta = ""
        self.style_meta = "Style: DEFAULT"
        self.screen_meta = "Screen: OFF"
        self.live_screen_pending = False
        self.thinking_placeholder = "Thinking..."
        self.code_session_active = False
        self._conversation_summary_override: str | None = None
        self.document_ingest_active = False
        self.event_speech_mode = normalize_event_speech_mode(EVENT_SPEECH_OFF)
        self._chat_rendered_messages: List[Tuple[str, str]] = []
        self._chat_rendered_tags: List[int | str] = []
        self._chat_render_wrap_columns: int | None = None
        self._last_tts_busy = False
        self._pending_input_modality = "typed"
        self._event_speech_recent: Dict[str, float] = {}
        self._vision_note_lock = threading.Lock()
        self._vision_note_active = False
        self._last_vision_note_signature = ""
        self._boot_ui_min_visible_until = 0.0
        self._pending_boot_ready = False
        self._pending_boot_ready_payload: object = ""
        self.refresh_active_user_meta(update_ui=False)

        self.pipeline = ChatPipeline(
            tts=self.tts,
            chat_append_fn=self.chat_append,
            chat_upsert_fn=self.chat_upsert_streaming_assistant,
            persist_turn_fn=self.persist_turn,
            set_status_fn=self.set_status,
            finalize_stream_fn=self.chat_state.finalize_streaming_assistant,
        )
        self.proactive_monitor = ProactiveMonitor(
            CFG.REMINDERS_PATH,
            can_dispatch=self.can_dispatch_proactive_reminder,
            is_inflight=self.is_proactive_reminder_inflight,
            dispatch_callback=lambda reminder: trigger_proactive_reminder_action(self, reminder),
            log_callback=self.safe_log,
        )
        CFG.on_change(self._on_config_changed)

    def _text_view_min_height(self, text_tag: str) -> int:
        if text_tag == self.tags.boot_log_text:
            return 56
        if text_tag == self.tags.dashboard_activity_text:
            return 72
        if text_tag == self.tags.documents_view_text:
            return 72
        if text_tag == self.tags.stats_view_text:
            return 96
        if text_tag == self.tags.agent_log_text:
            return 64
        if text_tag == self.tags.code_view_text:
            return 64
        return 48

    def refresh_text_view_height(self, text_tag: str) -> None:
        if not dpg.does_item_exist(text_tag):
            return
        if text_tag == self.tags.chat_text:
            return
        text = str(dpg.get_value(text_tag) or "")
        lines = max(text.count("\n") + 1, 1)
        height = max(self._text_view_min_height(text_tag), min(60000, 4 + (lines * 15)))
        try:
            dpg.configure_item(text_tag, height=height)
        except Exception:
            pass

    def refresh_stats_view(self) -> None:
        if not dpg.does_item_exist(self.tags.stats_view_text):
            return
        snapshot = self.stats_collector.build_dashboard_snapshot()
        dpg.set_value(self.tags.stats_view_text, str(snapshot.get("summary_text") or "No stats recorded yet."))
        self.refresh_text_view_height(self.tags.stats_view_text)
        x_values = [float(value) for value in snapshot.get("turn_numbers") or []]
        self._set_stats_series(
            self.tags.stats_total_series,
            x_values,
            snapshot.get("total_ms") or [],
            show=bool(x_values),
        )
        self._set_stats_series(
            self.tags.stats_total_upper_series,
            x_values,
            snapshot.get("total_upper_ms") or [],
            show=bool(x_values),
        )
        self._set_stats_series(
            self.tags.stats_total_outlier_series,
            snapshot.get("total_outlier_x") or [],
            snapshot.get("total_outlier_y") or [],
            show=bool(snapshot.get("total_outlier_x") or []),
        )

        self._set_stats_series(
            self.tags.stats_route_series,
            x_values,
            snapshot.get("route_ms") or [],
            show=self._has_nonzero_values(snapshot.get("route_ms") or []),
        )
        self._set_stats_series(
            self.tags.stats_manager_series,
            x_values,
            snapshot.get("manager_ms") or [],
            show=self._has_nonzero_values(snapshot.get("manager_ms") or []),
        )
        self._set_stats_series(
            self.tags.stats_reporter_series,
            x_values,
            snapshot.get("reporter_ms") or [],
            show=self._has_nonzero_values(snapshot.get("reporter_ms") or []),
        )
        self._set_stats_series(
            self.tags.stats_persona_series,
            x_values,
            snapshot.get("persona_ms") or [],
            show=self._has_nonzero_values(snapshot.get("persona_ms") or []),
        )
        self._set_stats_series(
            self.tags.stats_tts_series,
            x_values,
            snapshot.get("tts_ms") or [],
            show=self._has_nonzero_values(snapshot.get("tts_ms") or []),
        )

        planner_values = snapshot.get("planner_total_ms") or []
        executor_values = snapshot.get("executor_total_ms") or []
        has_workload = self._has_nonzero_values(planner_values) or self._has_nonzero_values(executor_values)
        if dpg.does_item_exist(self.tags.stats_workload_group):
            dpg.configure_item(self.tags.stats_workload_group, show=has_workload)
        self._set_stats_series(
            self.tags.stats_planner_series,
            x_values,
            planner_values,
            show=self._has_nonzero_values(planner_values),
        )
        self._set_stats_series(
            self.tags.stats_executor_series,
            x_values,
            executor_values,
            show=self._has_nonzero_values(executor_values),
        )

        for axis_tag in (
            self.tags.stats_latency_x_axis,
            self.tags.stats_latency_y_axis,
            self.tags.stats_phase_x_axis,
            self.tags.stats_phase_y_axis,
            self.tags.stats_workload_x_axis,
            self.tags.stats_workload_y_axis,
        ):
            if not dpg.does_item_exist(axis_tag):
                continue
            try:
                dpg.fit_axis_data(axis_tag)
            except Exception:
                continue

    @staticmethod
    def _has_nonzero_values(values: List[float] | Tuple[float, ...]) -> bool:
        return any(float(value or 0.0) > 0.0 for value in values)

    def _set_stats_series(
        self,
        series_tag: str,
        x_values: List[float] | Tuple[float, ...],
        y_values: List[float] | Tuple[float, ...],
        *,
        show: bool,
    ) -> None:
        if not dpg.does_item_exist(series_tag):
            return
        xs = [float(value or 0.0) for value in x_values]
        ys = [float(value or 0.0) for value in y_values]
        try:
            dpg.set_value(series_tag, [xs, ys])
            dpg.configure_item(series_tag, show=bool(show))
        except Exception:
            return

    @staticmethod
    def _chat_message_height(text: str) -> int:
        lines = max(str(text or "").count("\n") + 1, 1)
        return max(22, min(60000, 2 + (lines * 13)))

    def _chat_wrap_columns(self) -> int:
        width_px = 0
        if dpg.does_item_exist(self.tags.chat_child):
            try:
                width_px = int((dpg.get_item_rect_size(self.tags.chat_child) or [0, 0])[0] or 0)
            except Exception:
                width_px = 0
        if width_px <= 0:
            width_px = max(360, self.width - 560)
        usable_width = max(width_px - 28, 240)
        return max(40, int(usable_width / 7))

    def is_tts_active(self) -> bool:
        try:
            return bool(getattr(self.tts, "is_busy", lambda: False)())
        except Exception:
            return False

    def _event_tts_profile(self) -> tuple[str | None, float | None]:
        try:
            style_state = self.load_style_state()
            return style_state.tts_voice, style_state.tts_speed
        except Exception:
            return None, None

    def load_style_state(self):
        return self.style_mgr.load(
            float(getattr(CFG, "TEMPERATURE", 0.7)),
            str(getattr(CFG, "TTS_VOICE", "af_heart")),
            float(getattr(CFG, "TTS_SPEED", 0.85)),
        )

    def web_active_user_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"preserve_transcript": False}
        try:
            profile = self.user_runtime.active_profile()
            payload["user_name"] = str(getattr(profile, "name", "") or "")
            payload["user_id"] = str(getattr(profile, "user_id", "") or "")
            payload["role"] = str(getattr(profile, "role", "") or "")
        except Exception:
            pass
        return payload

    def web_style_status_payload(self) -> dict[str, str]:
        try:
            style_state = self.load_style_state()
        except Exception:
            return {"name": "default", "label": "Default", "filename": ""}
        name = str(getattr(style_state, "name", "") or "default").strip() or "default"
        try:
            filename = str(getattr(self.style_mgr, "active_filename", "") or "").strip()
        except Exception:
            filename = ""
        return {
            "name": name,
            "label": name.upper() if name.lower() != "default" else "Default",
            "filename": filename,
        }

    def web_tts_status_payload(self) -> dict[str, str]:
        try:
            tts = self.tts
            play_active = bool(getattr(tts, "_play_active", False))
            synth_active = bool(getattr(tts, "_synth_active", False))
            stream_active = False
            stream_lock = getattr(tts, "_stream_lock", None)
            if stream_lock is not None:
                try:
                    with stream_lock:
                        stream_active = getattr(tts, "_stream_epoch", None) is not None
                except Exception:
                    stream_active = False
            queued = False
            for attr in ("_job_q", "_audio_q"):
                q = getattr(tts, attr, None)
                if q is not None and not q.empty():
                    queued = True
                    break
            if play_active:
                state = "playing"
            elif synth_active or stream_active or queued:
                state = "synthesizing"
            else:
                state = "idle"
            return {"state": state, "error": ""}
        except Exception as exc:
            return {"state": "error", "error": str(exc)}

    def _speak_event_notification(self, text: str, *, key: str = "", force: bool = False) -> None:
        if not force and self.event_speech_mode == EVENT_SPEECH_OFF:
            return
        cleaned = clean_ui_text(text).strip()
        if not cleaned:
            return
        now = time.monotonic()
        dedupe_window = 0.3 if self.event_speech_mode == "noisy" else 0.9 if self.event_speech_mode == "all" else 2.0
        dedupe_key = (key or cleaned).strip().lower()
        if not force and dedupe_key:
            last = self._event_speech_recent.get(dedupe_key, 0.0)
            if now - last < dedupe_window:
                return
        if dedupe_key:
            self._event_speech_recent[dedupe_key] = now
        try:
            voice, speed = self._event_tts_profile()
            self.tts.speak(cleaned, voice=voice, speed=speed)
        except Exception:
            return

    def maybe_speak_ui_event(self, kind: str, payload: object) -> None:
        if not self.boot_ready and str(kind or "").strip().lower() != "boot_ready":
            return
        message = event_speech_message(kind, payload, mode=self.event_speech_mode)
        if not message:
            return
        self._speak_event_notification(message.get("text", ""), key=str(message.get("key") or ""))

    def set_event_speech_mode(self, value: object, *, announce: bool = True) -> None:
        mode = normalize_event_speech_mode(value)
        self.event_speech_mode = mode
        label = event_speech_mode_label(mode)
        if dpg.does_item_exist(self.tags.event_speech_combo):
            dpg.set_value(self.tags.event_speech_combo, label)
        self.ui_queue.put(("status_widget_dashboard_activity", f"Event speech mode: {label}"))
        if announce and mode != EVENT_SPEECH_OFF:
            self._speak_event_notification(f"{label}.", key="event_speech_mode", force=True)

    def set_vision_session_active(self, active: bool) -> None:
        self.vision_session_memory.set_active(active)

    def queue_visual_note(self, image_path: Path | str) -> None:
        if not self.boot_ready:
            return
        if getattr(self.live_screen, "is_enabled", lambda: False)() is False:
            return
        path = Path(image_path).resolve()
        if not path.exists() or not path.is_file():
            return
        try:
            signature = f"{path}:{path.stat().st_mtime_ns}"
        except Exception:
            signature = str(path)
        with self._vision_note_lock:
            if self._vision_note_active or signature == self._last_vision_note_signature:
                return
            self._vision_note_active = True
            self._last_vision_note_signature = signature

        def worker() -> None:
            try:
                deadline = time.monotonic() + 5.0
                while self.has_active_operations() and time.monotonic() < deadline:
                    time.sleep(0.15)
                style_state = self.load_style_state()
                recent_notes = self.vision_session_memory.recent_notes(limit=4)
                recent_user_messages = recent_user_vision_context(
                    self.chat_state.get_messages_snapshot(),
                    limit=3,
                )
                answer = analyze_image(
                    self.llm,
                    request=VisionResolvedRequest(
                        image_path=path,
                        question=build_vision_comment_prompt(
                            recent_notes=recent_notes,
                            recent_user_messages=recent_user_messages,
                        ),
                    ),
                    style_overlay=style_state.overlay or "",
                    temperature=0.1,
                    max_tokens=80,
                )
                cleaned = clean_ui_text(answer).strip()
                if cleaned.upper() == VISION_COMMENT_SKIP_TOKEN:
                    return
                if cleaned and self.vision_session_memory.note_is_session_safe(cleaned):
                    spoken_event = event_speech_message(
                        "vision_snapshot_note",
                        {"text": cleaned},
                        mode=self.event_speech_mode,
                    )
                    should_speak = bool(spoken_event) and self.vision_session_memory.should_speak(cleaned)
                    if should_speak:
                        self.vision_session_memory.add_note(cleaned)
                    self.ui_queue.put(("vision_snapshot_note", {"text": cleaned, "speak": bool(should_speak)}))
            except Exception:
                return
            finally:
                with self._vision_note_lock:
                    self._vision_note_active = False

        threading.Thread(target=worker, daemon=True).start()

    def _reset_chat_render_cache(self) -> None:
        self._chat_rendered_messages = []
        self._chat_rendered_tags = []
        self._chat_render_wrap_columns = None

    def _append_chat_message_widget(self, role: str, content: str, *, wrap_columns: int) -> None:
        if not dpg.does_item_exist(self.tags.chat_text):
            return
        message_text = format_chat_message_block(role, content, wrap_columns=wrap_columns)
        if self._chat_rendered_tags:
            dpg.add_spacer(parent=self.tags.chat_text, height=2)
        message_tag = dpg.add_input_text(
            parent=self.tags.chat_text,
            multiline=True,
            readonly=True,
            tab_input=False,
            width=-1,
            height=self._chat_message_height(message_text),
            default_value=message_text,
        )
        dpg.bind_item_theme(message_tag, "selectable_text_theme")
        self._chat_rendered_messages.append((role, content))
        self._chat_rendered_tags.append(message_tag)
        self._chat_render_wrap_columns = wrap_columns

    def _update_last_chat_message_widget(self, role: str, content: str, *, wrap_columns: int) -> bool:
        if (
            not self._chat_rendered_tags
            or not self._chat_rendered_messages
            or self._chat_render_wrap_columns != wrap_columns
        ):
            return False
        if self._chat_rendered_messages[-1][0] != role:
            return False
        message_tag = self._chat_rendered_tags[-1]
        if not dpg.does_item_exist(message_tag):
            return False
        message_text = format_chat_message_block(role, content, wrap_columns=wrap_columns)
        dpg.set_value(message_tag, message_text)
        dpg.configure_item(message_tag, height=self._chat_message_height(message_text))
        self._chat_rendered_messages[-1] = (role, content)
        return True

    def _try_append_chat_ui(self, messages: List[Tuple[str, str]]) -> bool:
        if not dpg.does_item_exist(self.tags.chat_text):
            return False
        wrap_columns = self._chat_wrap_columns()
        if (
            self._chat_render_wrap_columns is not None
            and self._chat_render_wrap_columns != wrap_columns
        ):
            return False
        if len(messages) != len(self._chat_rendered_messages) + 1:
            return False
        if messages[:-1] != self._chat_rendered_messages:
            return False
        role, content = messages[-1]
        self._append_chat_message_widget(role, content, wrap_columns=wrap_columns)
        self.request_autoscroll(self.tags.chat_child)
        return True

    def _try_update_streaming_chat_ui(self, messages: List[Tuple[str, str]]) -> bool:
        if not dpg.does_item_exist(self.tags.chat_text):
            return False
        wrap_columns = self._chat_wrap_columns()
        if (
            self._chat_render_wrap_columns is not None
            and self._chat_render_wrap_columns != wrap_columns
        ):
            return False
        if len(messages) == len(self._chat_rendered_messages) + 1:
            if messages[:-1] != self._chat_rendered_messages:
                return False
            role, content = messages[-1]
            self._append_chat_message_widget(role, content, wrap_columns=wrap_columns)
            self.request_autoscroll(self.tags.chat_child)
            return True
        if len(messages) != len(self._chat_rendered_messages) or not messages:
            return False
        if messages[:-1] != self._chat_rendered_messages[:-1]:
            return False
        role, content = messages[-1]
        if not self._update_last_chat_message_widget(role, content, wrap_columns=wrap_columns):
            return False
        self.request_autoscroll(self.tags.chat_child, frames=2)
        return True

    def set_status(self, text: str) -> None:
        set_status_action(self, text)

    def set_mode_indicator(self, text: str) -> None:
        set_mode_indicator_action(self, text)

    @staticmethod
    def _clean_ui_text(text: object) -> str:
        return clean_ui_text(text)

    @staticmethod
    def _classify_runtime_mode(text: str) -> str:
        return classify_runtime_mode(text)

    def _refresh_top_bar(self) -> None:
        refresh_top_bar(self)

    def _set_stage_meta(self, text: str) -> None:
        set_stage_meta_action(self, text)

    def safe_log(self, text: str) -> None:
        print(text)
        self.ui_queue.put(("agent_log", text))

    def log_agent_monitor(self, text: str) -> None:
        log_agent_monitor_action(self, text)

    def request_autoscroll(self, tag: str, *, frames: int = 3) -> None:
        if not tag or not dpg.does_item_exist(tag):
            return
        remaining = self.pending_autoscrolls.get(tag, 0)
        self.pending_autoscrolls[tag] = max(remaining, frames)

    def _flush_autoscrolls(self) -> None:
        if not self.pending_autoscrolls:
            return
        for tag, frames in list(self.pending_autoscrolls.items()):
            if frames <= 0 or not dpg.does_item_exist(tag):
                self.pending_autoscrolls.pop(tag, None)
                continue
            try:
                dpg.set_y_scroll(tag, dpg.get_y_scroll_max(tag))
            except Exception:
                self.pending_autoscrolls.pop(tag, None)
                continue
            if frames <= 1:
                self.pending_autoscrolls.pop(tag, None)
            else:
                self.pending_autoscrolls[tag] = frames - 1

    def _refresh_chat_ui(self) -> None:
        if not dpg.does_item_exist(self.tags.chat_text):
            return

        messages_snapshot = self.chat_state.get_messages_snapshot()
        dpg.delete_item(self.tags.chat_text, children_only=True)
        self._reset_chat_render_cache()
        messages = renderable_chat_messages(messages_snapshot)
        wrap_columns = self._chat_wrap_columns()
        for role, content in messages:
            self._append_chat_message_widget(role, content, wrap_columns=wrap_columns)
        self.request_autoscroll(self.tags.chat_child)

    def chat_append(self, role: str, content: str) -> None:
        self.chat_state.append(role, content)
        self.ui_queue.put(("chat_append", {"role": role, "content": content, "_state_synced": True}))
        if self._try_append_chat_ui(renderable_chat_messages(self.chat_state.get_messages_snapshot())):
            return
        self._refresh_chat_ui()

    def chat_upsert_streaming_assistant(self, text: str) -> None:
        self.chat_state.upsert_streaming_assistant(text)
        if self._try_update_streaming_chat_ui(renderable_chat_messages(self.chat_state.get_messages_snapshot())):
            return
        _LOG.debug(
            "_try_update_streaming_chat_ui failed — falling back to full refresh (text len=%d)",
            len(text),
        )
        self._refresh_chat_ui()

    def _should_persist_turn(self) -> bool:
        """Return False when persistence must be skipped per the privacy model.

        - Unknown users: session-only, no persistent history.
        - Incognito mode (knowledge=false style): no persistent history.
        """
        try:
            profile = self.user_runtime.active_profile()
        except Exception:
            return True
        if getattr(profile, "is_unknown", False):
            return False
        try:
            style_state = self.load_style_state()
            if not getattr(style_state, "knowledge", True):
                return False
        except Exception:
            pass
        return True

    def persist_turn(self, role: str, content: str) -> None:
        if not self._should_persist_turn():
            return
        self.chat_state.persist_turn(role, content)

    def show_thinking_placeholder(self) -> None:
        messages = self.chat_state.get_messages_snapshot()
        if messages:
            last = messages[-1]
            if last.get("role") == "assistant" and last.get("content") == self.thinking_placeholder:
                return
        self.chat_append("assistant", self.thinking_placeholder)

    def clear_thinking_placeholder(self) -> None:
        if self.chat_state.remove_last_assistant_if_exact(self.thinking_placeholder):
            self._refresh_chat_ui()

    def _messages_for_model(self) -> List[Dict[str, str]]:
        return self.chat_state.for_model()

    def build_orchestrator_config(
        self,
        *,
        cancel_token: CancellationToken | None = None,
        langgraph_resume_thread_id: str = "",
        langgraph_resume_checkpoint_id: str = "",
        langgraph_resume_value: object | None = None,
    ) -> "OrchestratorConfig":
        from core.orchestrator import OrchestratorConfig

        input_modality = str(getattr(self, "_pending_input_modality", "typed") or "typed")
        self._pending_input_modality = "typed"
        voice_identity_notice = str(getattr(self, "_pending_voice_identity_notice", "") or "").strip()
        self._pending_voice_identity_notice = ""
        voice_identity_state = self._build_voice_identity_state()
        # Consume one-shot conversation summary override (set by on_new_session / on_clear).
        summary_override = getattr(self, "_conversation_summary_override", None)
        if summary_override is not None:
            self._conversation_summary_override = None
        return OrchestratorConfig(
            llm=self.llm,
            brain=self.agent_brain,
            knowledge=self.knowledge_mgr,
            prompt_context=self.prompt_context_service,
            chat=self.chat_state,
            styles=self.style_mgr,
            pipeline=self.pipeline,
            ui=self.ui_queue,
            get_context=self._messages_for_model,
            boot=self.boot_mgr,
            img_gen=self.img_gen,
            live_screen=self.live_screen,
            cancel_token=cancel_token,
            retain_cancel_token=self.retain_cancel_token,
            release_cancel_token=self.release_cancel_token,
            is_search_in_flight=self.is_search_in_flight,
            retain_search_in_flight=self.retain_search_in_flight,
            release_search_in_flight=self.release_search_in_flight,
            current_search_query=self.current_search_query,
            conversation_summary_path=Path(self.user_runtime.current_conversation_summary_path()),
            conversation_summary=summary_override,
            user_runtime=self.user_runtime,
            input_modality=input_modality,
            voice_identity_notice=voice_identity_notice,
            voice_identity_state=voice_identity_state,
            langgraph_resume_thread_id=str(langgraph_resume_thread_id or ""),
            langgraph_resume_checkpoint_id=str(langgraph_resume_checkpoint_id or ""),
            langgraph_resume_value=langgraph_resume_value,
        )

    def _build_voice_identity_state(self) -> dict[str, str]:
        try:
            profile = self.user_runtime.active_profile()
        except Exception:
            return {"current_speaker": "unknown", "recognition_status": "unknown", "access_tier": "unknown"}
        is_unknown = bool(getattr(profile, "is_unknown", False))
        current_speaker = "unknown" if is_unknown else str(getattr(profile, "name", "") or getattr(profile, "user_id", "") or "unknown")
        tracker = getattr(self, "_voice_drift_tracker", None)
        drift_pending = bool(
            isinstance(tracker, dict)
            and (
                int(tracker.get("candidate_count") or 0) > 0
                or int(tracker.get("unknown_count") or 0) > 0
            )
        )
        recognition_status = "unknown" if is_unknown else "tentative" if drift_pending else "confirmed"
        try:
            access_tier = "admin" if self.user_runtime.is_admin_unlocked() else "unknown" if is_unknown else "public"
        except Exception:
            access_tier = "unknown" if is_unknown else "public"
        return {
            "current_speaker": current_speaker,
            "recognition_status": recognition_status,
            "access_tier": access_tier,
        }

    def _on_config_changed(self, changed_keys: list[str]) -> None:
        """Handle live config updates."""
        llm_reconnect_fields = {
            "LLAMA_SERVER_URL",
            "LLAMA_SERVER_MODEL",
            "LLAMA_SERVER_TIMEOUT_S",
            "LLAMA_SERVER_STREAM_READ_TIMEOUT_S",
            "MODEL_PATH",
            "MMPROJ_PATH",
        }
        if llm_reconnect_fields.intersection(changed_keys):
            from llm.llm_server_client import LlamaServerConfig

            new_llm_cfg = LlamaServerConfig(
                base_url=str(getattr(CFG, "LLAMA_SERVER_URL", "http://127.0.0.1:8080")),
                model=str(getattr(CFG, "LLAMA_SERVER_MODEL", "qwen")),
                temperature=float(getattr(CFG, "TEMPERATURE", 0.7)),
                max_tokens=int(getattr(CFG, "MAX_TOKENS", 2048)),
                timeout_s=float(getattr(CFG, "LLAMA_SERVER_TIMEOUT_S", 300.0)),
                stream_read_timeout_s=float(getattr(CFG, "LLAMA_SERVER_STREAM_READ_TIMEOUT_S", 30.0)),
                debug_path=CFG.LLM_HTTP_PAYLOAD_DEBUG_PATH if getattr(CFG, "DEBUG_LLM_HTTP_PAYLOADS", False) else None,
            )
            self.llm.reconnect(new_llm_cfg)
            _LOG.info("LLM client reconnected with updated config")

        if "LOG_LEVEL" in changed_keys:
            logging.getLogger().setLevel(getattr(logging, getattr(CFG, "LOG_LEVEL", "INFO"), logging.INFO))
            _LOG.info("Log level changed to %s", getattr(CFG, "LOG_LEVEL", "INFO"))

        self.ui_queue.put(("config_reloaded", changed_keys))

    def create_cancel_token(self) -> CancellationToken:
        return CancellationToken()

    def retain_cancel_token(self, token: CancellationToken | None) -> None:
        if token is None:
            return
        with self.cancel_lock:
            self.cancel_tokens[token] = self.cancel_tokens.get(token, 0) + 1
        self.ui_queue.put(("ui_controls_refresh", ""))

    def release_cancel_token(self, token: CancellationToken | None) -> None:
        if token is None:
            return
        with self.cancel_lock:
            current = self.cancel_tokens.get(token, 0)
            if current <= 1:
                self.cancel_tokens.pop(token, None)
            else:
                self.cancel_tokens[token] = current - 1
        self.ui_queue.put(("ui_controls_refresh", ""))

    def retain_search_in_flight(self, query: str = "") -> None:
        with self.cancel_lock:
            self._search_in_flight_count += 1
            clean_query = str(query or "").strip()
            if clean_query:
                self._active_search_query = clean_query
        self.ui_queue.put(("ui_controls_refresh", ""))

    def release_search_in_flight(self) -> None:
        with self.cancel_lock:
            if self._search_in_flight_count > 0:
                self._search_in_flight_count -= 1
            if self._search_in_flight_count <= 0:
                self._search_in_flight_count = 0
                self._active_search_query = ""
        self.ui_queue.put(("ui_controls_refresh", ""))

    def is_search_in_flight(self) -> bool:
        with self.cancel_lock:
            return self._search_in_flight_count > 0

    def current_search_query(self) -> str:
        with self.cancel_lock:
            if self._search_in_flight_count <= 0:
                return ""
            return self._active_search_query

    def retain_proactive_reminder_inflight(self, reminder_id: str) -> None:
        clean_id = str(reminder_id or "").strip()
        if not clean_id:
            return
        with self.cancel_lock:
            self._proactive_reminder_inflight_ids.add(clean_id)
        self.ui_queue.put(("ui_controls_refresh", ""))

    def release_proactive_reminder_inflight(self, reminder_id: str) -> None:
        clean_id = str(reminder_id or "").strip()
        if not clean_id:
            return
        with self.cancel_lock:
            self._proactive_reminder_inflight_ids.discard(clean_id)
        self.ui_queue.put(("ui_controls_refresh", ""))

    def is_proactive_reminder_inflight(self, reminder_id: str) -> bool:
        clean_id = str(reminder_id or "").strip()
        if not clean_id:
            return False
        with self.cancel_lock:
            return clean_id in self._proactive_reminder_inflight_ids

    def can_dispatch_proactive_reminder(self) -> bool:
        if not self.boot_ready:
            return False
        if self.has_active_operations() or self.has_active_code_session():
            return False
        if self.document_ingest_active or self.live_screen_pending:
            return False
        if self.is_tts_active():
            return False
        return True

    def has_active_operations(self) -> bool:
        with self.cancel_lock:
            return bool(self.cancel_tokens) or self._search_in_flight_count > 0

    def has_active_code_session(self) -> bool:
        return self.code_session_active or self.code_session.is_active()

    def current_code_preview_runnable_path(self) -> str:
        if not dpg.does_item_exist(self.tags.code_view_text):
            return ""
        text = str(dpg.get_value(self.tags.code_view_text) or "")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("Path: "):
                continue
            path = stripped[len("Path: ") :].strip()
            if path.lower().endswith(".py"):
                return path
        return ""

    def cancel_active_operations(self, reason: str = "Stopped by user.") -> bool:
        with self.cancel_lock:
            tokens = list(self.cancel_tokens)
        for token in tokens:
            token.cancel(reason)
        return bool(tokens)

    def refresh_interaction_state(self) -> None:
        active_operations = self.has_active_operations()
        can_interact = self.boot_ready and not active_operations
        code_active = self.boot_ready and self.has_active_code_session()
        tts_active = self.boot_ready and self.is_tts_active()
        self._last_tts_busy = bool(tts_active)
        stop_enabled = self.boot_ready and (active_operations or code_active or tts_active)
        input_enabled = self.boot_ready and (can_interact or code_active or active_operations)
        for tag in (self.tags.input_box, self.tags.send_button):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, enabled=input_enabled)
        for tag in (self.tags.clear_session_button, self.tags.mic_button, self.tags.restart_button):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, enabled=can_interact)
        if dpg.does_item_exist(self.tags.snapshot_button):
            dpg.configure_item(
                self.tags.snapshot_button,
                enabled=self.boot_ready and not self.live_screen_pending,
            )
        for tag in (
            self.tags.live_screen_mode_combo,
            self.tags.live_screen_interval_combo,
            self.tags.event_speech_combo,
        ):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, enabled=self.boot_ready and not self.live_screen_pending)
        if dpg.does_item_exist(self.tags.ingest_button):
            dpg.configure_item(
                self.tags.ingest_button,
                enabled=can_interact and not self.document_ingest_active,
            )
        if dpg.does_item_exist(self.tags.stop_button):
            dpg.configure_item(self.tags.stop_button, enabled=stop_enabled)
        if dpg.does_item_exist(self.tags.code_input_box):
            dpg.configure_item(self.tags.code_input_box, enabled=code_active)
        if dpg.does_item_exist(self.tags.code_send_button):
            dpg.configure_item(self.tags.code_send_button, enabled=code_active)
        if dpg.does_item_exist(self.tags.code_run_button):
            dpg.configure_item(
                self.tags.code_run_button,
                enabled=self.boot_ready and not self.has_active_operations() and not code_active and bool(self.current_code_preview_runnable_path()),
            )
        if dpg.does_item_exist(self.tags.code_stop_button):
            dpg.configure_item(self.tags.code_stop_button, enabled=code_active)
        if dpg.does_item_exist(self.tags.code_clear_button):
            dpg.configure_item(self.tags.code_clear_button, enabled=self.boot_ready)
        if dpg.does_item_exist(self.tags.input_box):
            hint = "Type here... (Enter to send)"
            if active_operations:
                hint = "Type stop/cancel or press Stop..."
            elif code_active:
                hint = "Send input to running code session..."
            dpg.configure_item(
                self.tags.input_box,
                hint=hint,
            )
        if dpg.does_item_exist(self.tags.send_button):
            label = "Stop" if active_operations else ("Send to Code" if code_active else "Send")
            dpg.set_item_label(self.tags.send_button, label)

    def set_boot_ready(self, value: bool) -> None:
        self.boot_ready = bool(value)
        self.refresh_interaction_state()

    def refresh_documents_view(self) -> None:
        try:
            payload = self.document_mgr.render_ui_summary()
        except Exception as exc:
            payload = f"Document view unavailable: {exc}"
        self.ui_queue.put(("documents_view", payload))

    def set_code_status(self, text: str) -> None:
        if dpg.does_item_exist(self.tags.code_status_text):
            dpg.set_value(self.tags.code_status_text, str(text))

    def clear_code_output(self) -> None:
        if dpg.does_item_exist(self.tags.code_view_text):
            dpg.set_value(self.tags.code_view_text, "")
            self.refresh_text_view_height(self.tags.code_view_text)

    def replace_code_output(self, text: str) -> None:
        if dpg.does_item_exist(self.tags.code_view_text):
            dpg.set_value(self.tags.code_view_text, str(text))
            self.refresh_text_view_height(self.tags.code_view_text)
            self.request_autoscroll(self.tags.code_view_child)

    def append_code_output(self, text: str) -> None:
        if not dpg.does_item_exist(self.tags.code_view_text):
            return
        current = dpg.get_value(self.tags.code_view_text) or ""
        updated = current + str(text)
        if len(updated) > 40000:
            updated = updated[-40000:]
        dpg.set_value(self.tags.code_view_text, updated)
        self.refresh_text_view_height(self.tags.code_view_text)
        self.request_autoscroll(self.tags.code_view_child)

    def activate_code_tab(self) -> None:
        if dpg.does_item_exist(self.tags.main_tab_bar) and dpg.does_item_exist(self.tags.code_tab):
            try:
                dpg.set_value(self.tags.main_tab_bar, self.tags.code_tab)
            except Exception:
                pass

    def focus_code_input(self) -> None:
        if dpg.does_item_exist(self.tags.code_input_box):
            try:
                dpg.focus_item(self.tags.code_input_box)
            except Exception:
                pass

    def start_code_session(self, rel_path: str) -> None:
        started_path = self.code_session.start_script(rel_path)
        self.code_session_meta = f"Code: {started_path}"
        self.stage_meta = ""
        self.runtime_mode = "CODE SESSION"
        self._refresh_top_bar()

    def send_code_session_input(self, text: str) -> bool:
        return self.code_session.send_input(text)

    def stop_code_session(self) -> bool:
        return self.code_session.stop()

    def set_code_session_active(self, active: bool) -> None:
        self.code_session_active = bool(active)
        if self.code_session_active:
            script_path = self.code_session.active_script().strip()
            self.code_session_meta = f"Code: {script_path}" if script_path else "Code: interactive"
            self.stage_meta = ""
            if self.runtime_mode in {"IDLE", "CANCELED", "CODE SESSION"}:
                self.runtime_mode = "CODE SESSION"
        else:
            self.code_session_meta = ""
            if self.runtime_mode == "CODE SESSION":
                self.runtime_mode = "IDLE"
        self._refresh_top_bar()
        self.refresh_interaction_state()

    def submit_user_text(self, user_text: str) -> None:
        text = str(user_text or "").strip()
        if not text or not self.boot_ready or self.has_active_operations() or self.has_active_code_session():
            return
        self.chat_append("user", text)
        self.persist_turn("user", text)
        self.session_meta = "Session: active"
        self._refresh_top_bar()
        if dpg.does_item_exist(self.tags.input_box):
            dpg.set_value(self.tags.input_box, "")
            dpg.focus_item(self.tags.input_box)
        self.show_thinking_placeholder()
        threading.Thread(target=self.do_generate_stream, daemon=True).start()

    def _reset_mic_ui(self) -> None:
        reset_mic_ui_action(self)

    def on_mic_toggle(self) -> None:
        on_mic_toggle_action(self)

    def on_snapshot(self) -> None:
        on_snapshot_action(self)

    def on_live_screen_mode_changed(self, sender=None, app_data=None, user_data=None) -> None:
        on_live_screen_mode_changed_action(self, sender=sender, app_data=app_data, user_data=user_data)

    def on_live_screen_interval_changed(self, sender=None, app_data=None, user_data=None) -> None:
        on_live_screen_interval_changed_action(self, sender=sender, app_data=app_data, user_data=user_data)

    def do_generate_stream(self) -> None:
        do_generate_stream_action(self)

    def on_new_session(self) -> None:
        on_new_session_action(self)

    def on_clear(self) -> None:
        on_clear_action(self)

    def on_send(self) -> None:
        on_send_action(self)

    def on_code_send(self) -> None:
        on_code_send_action(self)

    def on_code_run(self) -> None:
        on_code_run_action(self)

    def on_code_clear(self) -> None:
        on_code_clear_action(self)

    def on_stop(self) -> None:
        on_stop_action(self)

    def on_restart(self) -> None:
        on_restart_action(self)

    def on_event_speech_mode_changed(self, sender=None, app_data=None, user_data=None) -> None:
        on_event_speech_mode_changed_action(self, sender=sender, app_data=app_data, user_data=user_data)

    def on_open_document_picker(self) -> None:
        on_open_document_picker_action(self)

    def on_document_picker_selected(self, sender=None, app_data=None, user_data=None) -> None:
        on_document_picker_selected_action(self, sender=sender, app_data=app_data, user_data=user_data)

    def on_document_picker_cancel(self, sender=None, app_data=None, user_data=None) -> None:
        on_document_picker_cancel_action(self, sender=sender, app_data=app_data, user_data=user_data)

    def pump_ui_queue(self) -> None:
        pump_ui_queue_action(self)

    def load_memory_into_chat(self) -> None:
        self.chat_state.load_recent_memory(limit=50)

    def refresh_active_user_meta(self, *, update_ui: bool = True) -> None:
        label = ""
        try:
            label = str(self.user_runtime.active_user_label() or "").strip()
        except Exception:
            label = ""
        self.user_meta = f"User: {label}" if label else ""
        if update_ui:
            self._refresh_top_bar()

    def _handle_web_mic_audio_submit(self, payload: dict) -> None:
        """Handle mic audio submission from Web UI / WebView.

        Runs decode + STT + voice identity in a worker thread so the Web
        dispatch loop is not blocked.
        """
        from tools.audio_decode import AudioDecodeError, decode_web_audio
        from tools.stt import get_stt_engine
        from ui.controller_actions import _apply_voice_identity_match

        audio_b64 = str(payload.get("audio") or "").strip()
        fmt = str(payload.get("format") or "").strip().lower()

        # Payload validation
        if not audio_b64:
            self.ui_queue.put(("mic_status", {"state": "error", "error": "Empty audio payload"}))
            return
        if fmt not in ("webm", "wav"):
            self.ui_queue.put(("mic_status", {"state": "error", "error": f"Unsupported format: {fmt}"}))
            return

        # Busy guard
        if self.has_active_operations() or self.has_active_code_session() or self.document_ingest_active or self.live_screen_pending:
            self.ui_queue.put(("mic_status", {"state": "error", "error": "Piper is busy"}))
            return

        def _worker() -> None:
            try:
                _LOG.info("Web mic: received audio payload format=%s len=%d", fmt, len(audio_b64))

                self.ui_queue.put(("mic_status", {"state": "transcribing", "stage": "decoding", "message": "Decoding audio...", "error": ""}))
                decode_start = time.perf_counter()
                audio_np = decode_web_audio(
                    audio_b64,
                    fmt,  # type: ignore[arg-type]
                    max_decoded_bytes=CFG.WEB_MIC_MAX_DECODED_BYTES,
                    ffmpeg_timeout_s=float(getattr(CFG, "WEB_MIC_FFMPEG_TIMEOUT_S", 30)),
                )
                decode_elapsed = time.perf_counter() - decode_start
                _LOG.info("Web mic: decode complete samples=%d elapsed=%.3fs", len(audio_np), decode_elapsed)

                duration_s = float(len(audio_np)) / 16000.0
                _LOG.info("Web mic: audio duration=%.3fs", duration_s)
                if duration_s > CFG.WEB_MIC_MAX_SECONDS:
                    self.ui_queue.put(("mic_status", {"state": "error", "error": "Audio duration exceeds limit"}))
                    return

                engine = get_stt_engine()
                try:
                    profile = self.user_runtime.active_profile()
                    if hasattr(engine, "set_active_voice_profile"):
                        engine.set_active_voice_profile(
                            profile.user_id,
                            is_unknown=getattr(profile, "is_unknown", False),
                        )
                except Exception:
                    pass

                self.ui_queue.put(("mic_status", {"state": "transcribing", "stage": "stt", "message": "Running local STT...", "error": ""}))
                stt_start = time.perf_counter()
                transcript = engine.transcribe_buffer(audio_np, sample_rate=16000)
                stt_elapsed = time.perf_counter() - stt_start
                _LOG.info("Web mic: STT complete elapsed=%.3fs empty=%s", stt_elapsed, not transcript)

                self.ui_queue.put(("mic_status", {"state": "transcribing", "stage": "identity", "message": "Checking voice identity...", "error": ""}))
                identity_start = time.perf_counter()
                _apply_voice_identity_match(self, engine)
                identity_elapsed = time.perf_counter() - identity_start
                _LOG.info("Web mic: identity complete elapsed=%.3fs", identity_elapsed)

                self.ui_queue.put(("mic_status", {"state": "transcribing", "stage": "submitting", "message": "Submitting transcript...", "error": ""}))
                if transcript:
                    self._pending_input_modality = "voice"
                    self.submit_user_text(transcript)
                else:
                    self.chat_append("system", "[No speech detected]")

                self.ui_queue.put(("mic_status", {"state": "idle", "error": ""}))
            except AudioDecodeError as exc:
                _LOG.warning("Web mic audio decode failed: %s", exc)
                self.ui_queue.put(("mic_status", {"state": "error", "error": str(exc)}))
            except Exception as exc:
                _LOG.exception("Web mic audio submission failed")
                self.ui_queue.put(("mic_status", {"state": "error", "error": f"STT error: {exc}"}))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_web_mic_start(self) -> None:
        """Start native mic recording from Web UI / WebView."""
        from tools.stt import get_stt_engine

        if self.mic_state != "idle":
            return
        if not self.boot_ready:
            self.ui_queue.put(("mic_status", {"state": "error", "error": "Piper is not ready"}))
            return
        try:
            engine = get_stt_engine()
            try:
                profile = self.user_runtime.active_profile()
                if hasattr(engine, "set_active_voice_profile"):
                    engine.set_active_voice_profile(profile.user_id, is_unknown=getattr(profile, "is_unknown", False))
            except Exception:
                pass
            engine.start_recording()
            self.mic_state = "recording"
            self.ui_queue.put(("mic_status", {"state": "listening", "message": "Listening..."}))
        except Exception as exc:
            self.mic_state = "idle"
            _LOG.warning("Web mic start failed: %s", exc)
            self.ui_queue.put(("mic_status", {"state": "error", "error": f"Mic error: {exc}"}))

    def _handle_web_mic_stop(self) -> None:
        """Stop native mic recording from Web UI / WebView and run STT in a worker."""
        from tools.stt import get_stt_engine
        from ui.controller_actions import _apply_voice_identity_match

        if self.mic_state != "recording":
            return
        self.mic_state = "idle"
        self.ui_queue.put(("mic_status", {"state": "transcribing", "message": "Running local STT..."}))

        def _worker() -> None:
            try:
                engine = get_stt_engine()
                text = engine.stop_recording()
                if text:
                    _apply_voice_identity_match(self, engine)
                    self._pending_input_modality = "voice"
                    self.submit_user_text(text)
                else:
                    _apply_voice_identity_match(self, engine)
                    self.chat_append("system", "[No speech detected]")
                self.ui_queue.put(("mic_status", {"state": "idle", "error": ""}))
            except Exception as exc:
                _LOG.exception("Web mic stop/STT failed")
                self.ui_queue.put(("mic_status", {"state": "error", "error": f"STT error: {exc}"}))

        threading.Thread(target=_worker, daemon=True).start()

    def _dispatch_web_action(self, action_name: str, payload: dict) -> None:
        """Dispatch a Web UI action without touching DearPyGui widgets."""
        if action_name == "send_message":
            text = str(payload.get("text", ""))
            submit_text_input_action(self, text, emit_bridge_events=True)
        elif action_name == "stop":
            self.on_stop()
        elif action_name == "new_session":
            self.on_new_session()
        elif action_name == "clear_chat":
            self.on_clear()
        elif action_name == "mic_toggle":
            if self.mic_state == "idle":
                self._handle_web_mic_start()
            else:
                self._handle_web_mic_stop()
        elif action_name == "mic_start":
            self._handle_web_mic_start()
        elif action_name == "mic_stop":
            self._handle_web_mic_stop()
        elif action_name == "mic_audio_submit":
            _LOG.info("Web mic: action received")
            self._handle_web_mic_audio_submit(payload)
        elif action_name == "snapshot_toggle":
            self.on_snapshot()
        elif action_name == "live_screen_mode":
            mode = str(payload.get("mode", "display")).strip().lower()
            self.live_screen.set_mode(mode)
            enabled = False
            try:
                enabled = self.live_screen.is_enabled()
            except Exception:
                pass
            self.screen_meta = f"Screen: {'LIVE' if enabled else 'OFF'} {mode}"
            self.set_vision_session_active(enabled)
            self.ui_queue.put(
                ("status_widget_dashboard_activity", f"Live screen source set to {mode}.")
            )
        elif action_name == "live_screen_interval":
            interval_s = float(payload.get("interval_s", 10.0))
            self.live_screen.set_interval(interval_s)
            enabled = False
            try:
                enabled = self.live_screen.is_enabled()
            except Exception:
                pass
            self.screen_meta = f"Screen: {'LIVE' if enabled else 'OFF'} {interval_s}s"
            self.ui_queue.put(
                (
                    "status_widget_dashboard_activity",
                    f"Live screen interval set to {interval_s}s.",
                )
            )
        elif action_name == "event_speech_mode":
            mode = payload.get("mode", "")
            self.set_event_speech_mode(mode, announce=True)
        elif action_name == "restart_piper":
            self.restart_requested = True
            self.set_status("Restarting...")

            def _shutdown_worker() -> None:
                try:
                    self.boot_mgr.shutdown()
                except Exception as exc:
                    _LOG.exception("Web restart shutdown failed")
                self.ui_queue.put(("status_widget_dashboard_activity", "Restart requested."))

            threading.Thread(target=_shutdown_worker, daemon=True).start()
        elif action_name == "open_document_picker":
            self.ui_queue.put(
                (
                    "chat_append",
                    {
                        "role": "system",
                        "content": "[UI] Document picker is frontend-owned in Web UI mode.",
                    },
                )
            )
        elif action_name == "document_picker_selected":
            paths = payload.get("paths", [])
            if paths:
                from ui.controller_actions import _start_document_ingest
                _start_document_ingest(self, [str(p) for p in paths])
            else:
                self.ui_queue.put(
                    (
                        "chat_append",
                        {"role": "system", "content": "[UI] No document selected."},
                    )
                )
        elif action_name == "document_picker_cancel":
            pass
        elif action_name == "code_send":
            text = str(payload.get("text", "")).strip()
            if text and self.has_active_code_session():
                self.send_code_session_input(text)
        elif action_name == "code_run":
            path = str(payload.get("path", "")).strip()
            content = str(payload.get("content", ""))
            if content and path:
                from pathlib import Path
                script_path = CFG.DATA_DIR / "workspace" / path
                script_path.parent.mkdir(parents=True, exist_ok=True)
                script_path.write_text(content, encoding="utf-8")
                self.safe_log(f"[Code] Saved {path} ({len(content)} chars)")
            if path:
                self.start_code_session(path)
                self.set_code_status(f"Launching: {path}")
                self.set_status("CODE SESSION")
            else:
                self.ui_queue.put(
                    (
                        "chat_append",
                        {
                            "role": "system",
                            "content": "[UI] Code run requires a path in Web UI mode.",
                        },
                    )
                )
            self.refresh_interaction_state()
        elif action_name == "code_clear":
            self.on_code_clear()
        elif action_name == "list_workspace_files":
            from pathlib import Path
            workspace_dir = CFG.DATA_DIR / "workspace"
            files = []
            if workspace_dir.exists():
                for f in sorted(workspace_dir.iterdir()):
                    if f.is_file() and f.suffix.lower() in (".py", ".txt", ".md", ".jpg", ".jpeg", ".png", ".webp"):
                        files.append({
                            "name": f.name,
                            "path": str(f),
                            "size": f.stat().st_size,
                        })
            self.ui_queue.put(("workspace_files", {"files": files, "path": str(workspace_dir)}))
        else:
            self.ui_queue.put(("error", f"Unhandled web action: {action_name}"))

    def run_web(
        self,
        host: str = "127.0.0.1",
        port: int = 8787,
        ws_path: str = "/ws",
        *,
        use_window: bool | None = None,
    ) -> int:
        """Run Piper in Web UI bridge mode (no DearPyGui)."""
        CFG.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.agent_brain.cleanup_old_events()
        self.load_memory_into_chat()
        self.knowledge_mgr.set_logger(self.safe_log)

        self.proactive_monitor.start()
        self._boot_ui_min_visible_until = time.perf_counter() + float(
            getattr(CFG, "BOOT_SCREEN_MIN_VISIBLE_S", 0.75)
        )
        self._pending_boot_ready = False
        self._pending_boot_ready_payload = ""
        boot_thread = threading.Thread(target=self.boot_mgr.run_sequence, daemon=True)
        boot_thread.start()

        action_queue: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        bridge_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        from web_ui.bridge.server import BridgeServer
        from web_ui.bridge.adapter import ui_tuple_to_ws_frame
        from ui.controller_queue import pump_ui_queue_web
        from ui.controller_render import renderable_chat_messages

        def _web_payload(method_name: str, fallback: dict[str, object]) -> dict[str, object]:
            method = getattr(self, method_name, None)
            if callable(method):
                try:
                    payload = method()
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    pass
            return dict(fallback)

        def _on_client_connect() -> list[str]:
            """Return initial frames for a newly connected WebSocket client."""
            try:
                messages = renderable_chat_messages(self.chat_state.get_messages_snapshot())
                return [
                    ui_tuple_to_ws_frame("chat_sync", messages),
                    ui_tuple_to_ws_frame(
                        "active_user_changed",
                        _web_payload("web_active_user_payload", {"preserve_transcript": False}),
                    ),
                    ui_tuple_to_ws_frame(
                        "style_status",
                        _web_payload("web_style_status_payload", {"name": "default", "label": "Default", "filename": ""}),
                    ),
                    ui_tuple_to_ws_frame(
                        "auth_status",
                        {"waiting": bool(self.user_runtime.is_waiting_for_admin_password())},
                    ),
                    ui_tuple_to_ws_frame(
                        "tts_status",
                        _web_payload("web_tts_status_payload", {"state": "idle", "error": ""}),
                    ),
                ]
            except Exception:
                return []

        bridge = BridgeServer(
            ui_queue=bridge_queue,
            action_queue=action_queue,
            host=host,
            port=port,
            ws_path=ws_path,
            static_dir=str(CFG.WORKSPACE_DIR),
            frontend_dist_dir=str(CFG.WEB_UI_FRONTEND_DIST_DIR),
            on_client_connect=_on_client_connect,
            max_message_size=int(getattr(CFG, "WEB_UI_MAX_WS_MESSAGE_BYTES", 20 * 1024 * 1024)),
        )
        bridge.start()

        # Guard DearPyGui calls: without a DPG context dpg.does_item_exist causes a
        # native hard exit on Windows.  Every DPG mutation in the codebase is already
        # guarded with ``if dpg.does_item_exist(tag):``; returning False safely skips
        # all of them in Web mode.
        _orig_dpg_exists = dpg.does_item_exist
        dpg.does_item_exist = lambda _tag: False

        if use_window is None:
            use_window = getattr(CFG, "WEB_UI_WINDOW", False)
        stop_event = threading.Event()
        previous_tts_status: tuple[str, str] | None = None

        def _pump_loop() -> None:
            """Web UI action/pump loop.

            Runs on the main thread in browser mode, or in a background
            thread when a desktop window is active.
            """
            nonlocal previous_tts_status
            try:
                while not self.restart_requested and not stop_event.is_set():
                    try:
                        action_name, payload = action_queue.get(timeout=0.05)
                    except queue.Empty:
                        pass
                    else:
                        try:
                            self._dispatch_web_action(action_name, payload)
                        except Exception as exc:
                            _LOG.exception("Web action dispatch failed: %s", action_name)
                            self.ui_queue.put(("error", f"Web action error: {action_name}: {exc}"))

                    pump_ui_queue_web(self, forward_queue=bridge_queue)

                    # Auth state tracking: emit when password-waiting state changes.
                    try:
                        auth_waiting = self.user_runtime.is_waiting_for_admin_password()
                    except Exception:
                        auth_waiting = False
                    if getattr(self, "_web_ui_prev_auth_waiting", None) != auth_waiting:
                        self._web_ui_prev_auth_waiting = auth_waiting
                        bridge_queue.put(("auth_status", {"waiting": auth_waiting}))
                    tts_payload = _web_payload("web_tts_status_payload", {"state": "idle", "error": ""})
                    tts_key = (
                        str(tts_payload.get("state") or "idle"),
                        str(tts_payload.get("error") or ""),
                    )
                    if previous_tts_status != tts_key:
                        previous_tts_status = tts_key
                        bridge_queue.put(("tts_status", tts_payload))
            except KeyboardInterrupt:
                pass

        try:
            if use_window:
                pump_thread = threading.Thread(
                    target=_pump_loop, daemon=True, name="piper-web-pump"
                )
                pump_thread.start()

                from web_ui.window import open_piper_window

                open_piper_window(f"http://{host}:{port}")

                stop_event.set()
                pump_thread.join(timeout=3.0)
            else:
                _pump_loop()
        except KeyboardInterrupt:
            pass
        finally:
            dpg.does_item_exist = _orig_dpg_exists
            bridge.stop()
            self.proactive_monitor.stop()
            self.agent_brain.shutdown()
            self.code_session.shutdown()
            self.boot_mgr.shutdown()
            if getattr(self, "searxng_service", None):
                self.searxng_service.shutdown()

        return RESTART_EXIT_CODE if self.restart_requested else 0

    def run(self) -> int:
        CFG.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.agent_brain.cleanup_old_events()

        self.load_memory_into_chat()
        self.knowledge_mgr.set_logger(self.safe_log)

        build_ui(
            callbacks={
                "on_send": self.on_send,
                "on_stop": self.on_stop,
                "on_new_session": self.on_new_session,
                "on_mic_toggle": self.on_mic_toggle,
                "on_snapshot": self.on_snapshot,
                "on_live_screen_mode_changed": self.on_live_screen_mode_changed,
                "on_live_screen_interval_changed": self.on_live_screen_interval_changed,
                "on_event_speech_mode_changed": self.on_event_speech_mode_changed,
                "on_restart": self.on_restart,
                "on_open_document_picker": self.on_open_document_picker,
                "on_document_picker_selected": self.on_document_picker_selected,
                "on_document_picker_cancel": self.on_document_picker_cancel,
                "on_code_send": self.on_code_send,
                "on_code_run": self.on_code_run,
                "on_code_clear": self.on_code_clear,
            },
            tags=self.tags.for_layout(),
            app_title=self.app_title,
            w=self.width,
            h=self.height,
        )

        self.refresh_active_user_meta()
        style_state = self.load_style_state()
        if dpg.does_item_exist(self.tags.mode_indicator):
            mode_name = style_state.name.upper() if style_state.name.lower() != "default" else ""
            self.set_mode_indicator(f"MODE: {mode_name}" if mode_name else "")
        refresh_live_screen_ui_action(self)
        self._refresh_top_bar()
        self.refresh_documents_view()
        self.refresh_interaction_state()
        self.proactive_monitor.start()
        self._boot_ui_min_visible_until = time.perf_counter() + float(
            getattr(CFG, "BOOT_SCREEN_MIN_VISIBLE_S", 0.75)
        )
        self._pending_boot_ready = False
        self._pending_boot_ready_payload = ""

        boot_thread = threading.Thread(target=self.boot_mgr.run_sequence, daemon=True)
        boot_thread.start()

        try:
            while dpg.is_dearpygui_running():
                self.pump_ui_queue()
                tts_busy = self.is_tts_active()
                if tts_busy != self._last_tts_busy:
                    self.refresh_interaction_state()
                dpg.render_dearpygui_frame()
                self._flush_autoscrolls()
        finally:
            self.proactive_monitor.stop()
            dpg.destroy_context()
            self.agent_brain.shutdown()
            self.code_session.shutdown()
            if getattr(self, "searxng_service", None):
                self.searxng_service.shutdown()
        return RESTART_EXIT_CODE if self.restart_requested else 0
