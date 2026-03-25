from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import dearpygui.dearpygui as dpg

from config import CFG
from core.codex_bridge import CodexRepairCoordinator
from core.code_session import EmbeddedCodeSession
from core.engines.proactive_monitor import ProactiveMonitor
from core.engines.stats_collector import StatsCollector
from core.engineering_support import build_manual_codex_snapshot
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
from memory.vision_session import VisionSessionMemory


RESTART_EXIT_CODE = 85


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
        boot_mgr,
        img_gen,
        live_screen,
        vision_session_memory: VisionSessionMemory,
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
        self.boot_mgr = boot_mgr
        self.img_gen = img_gen
        self.live_screen = live_screen
        self.vision_session_memory = vision_session_memory
        self.code_session = EmbeddedCodeSession(
            self.agent_brain.workspace,
            lambda kind, payload: self.ui_queue.put((kind, payload)),
        )
        self.codex_repair = CodexRepairCoordinator(
            repo_root=CFG.ROOT_DIR,
            data_dir=CFG.DATA_DIR,
            auto_enabled=CFG.CODEX_AUTO_REPAIR_ENABLED,
            poll_interval_s=CFG.CODEX_REPAIR_POLL_INTERVAL_S,
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
        self.stage_meta = ""
        self.code_session_meta = ""
        self.style_meta = "Style: DEFAULT"
        self.screen_meta = "Screen: OFF"
        self.live_screen_pending = False
        self.thinking_placeholder = "Thinking..."
        self.code_session_active = False
        self.document_ingest_active = False
        self.event_speech_mode = normalize_event_speech_mode(EVENT_SPEECH_OFF)
        self.latest_codex_brief_path = ""
        self.latest_codex_summary = ""
        self.latest_codex_escalation = None
        self.pending_codex_recovery = self.codex_repair.peek_recovery()
        self._last_codex_status_line = ""
        self._chat_rendered_messages: List[Tuple[str, str]] = []
        self._chat_rendered_tags: List[int | str] = []
        self._chat_render_wrap_columns: int | None = None
        self._last_tts_busy = False
        self._event_speech_recent: Dict[str, float] = {}
        self._vision_note_lock = threading.Lock()
        self._vision_note_active = False
        self._last_vision_note_signature = ""

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
        report = self.stats_collector.build_readonly_report()
        dpg.set_value(self.tags.stats_view_text, report)
        self.refresh_text_view_height(self.tags.stats_view_text)
        self.request_autoscroll(self.tags.stats_view_child)

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
            style_state = self.style_mgr.load(0.7, "af_heart", 0.9)
            return style_state.tts_voice, style_state.tts_speed
        except Exception:
            return None, None

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
                style_state = self.style_mgr.load(0.7, "af_heart", 0.9)
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
        if self._try_append_chat_ui(renderable_chat_messages(self.chat_state.get_messages_snapshot())):
            return
        self._refresh_chat_ui()

    def chat_upsert_streaming_assistant(self, text: str) -> None:
        self.chat_state.upsert_streaming_assistant(text)
        if self._try_update_streaming_chat_ui(renderable_chat_messages(self.chat_state.get_messages_snapshot())):
            return
        print(f"[STREAM DEBUG] _try_update_streaming_chat_ui FAILED — falling back to full refresh (text len={len(text)})")
        self._refresh_chat_ui()

    def persist_turn(self, role: str, content: str) -> None:
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
        can_interact = self.boot_ready and not self.has_active_operations()
        code_active = self.boot_ready and self.has_active_code_session()
        tts_active = self.boot_ready and self.is_tts_active()
        self._last_tts_busy = bool(tts_active)
        stop_enabled = self.boot_ready and (self.has_active_operations() or code_active or tts_active)
        for tag in (
            self.tags.input_box,
            self.tags.send_button,
            self.tags.clear_session_button,
            self.tags.mic_button,
            self.tags.restart_button,
        ):
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
            dpg.configure_item(
                self.tags.input_box,
                hint="Send input to running code session..." if code_active else "Type here... (Enter to send)",
            )
        if dpg.does_item_exist(self.tags.send_button):
            dpg.set_item_label(self.tags.send_button, "Send to Code" if code_active else "Send")

    def set_boot_ready(self, value: bool) -> None:
        self.boot_ready = bool(value)
        self.refresh_interaction_state()
        if self.boot_ready:
            self.resume_codex_recovery_if_needed()

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

    def export_codex_support_snapshot(self, note: str = "") -> None:
        messages_snapshot = self.chat_state.get_messages_snapshot()
        user_msg = ""
        for message in reversed(messages_snapshot):
            if str(message.get("role") or "") == "user":
                user_msg = str(message.get("content") or "").strip()
                break
        monitor_text = ""
        dashboard_text = ""
        status_snapshot = self.runtime_mode
        if dpg.does_item_exist(self.tags.agent_log_text):
            monitor_text = str(dpg.get_value(self.tags.agent_log_text) or "")
        if dpg.does_item_exist(self.tags.dashboard_activity_text):
            dashboard_text = str(dpg.get_value(self.tags.dashboard_activity_text) or "")
        decision = build_manual_codex_snapshot(
            log_path=CFG.CODEX_ESCALATION_LOG_PATH,
            note=note,
            user_msg=user_msg,
            history_tail=messages_snapshot[-8:],
            monitor_text=monitor_text,
            dashboard_text=dashboard_text,
            status_snapshot=status_snapshot,
            source="ui_command",
        )
        self.latest_codex_escalation = decision
        self.ui_queue.put(("codex_escalation", decision))

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

    def queue_codex_repair(self, escalation: dict[str, object]) -> None:
        result = self.codex_repair.request_repair(escalation)
        message = str(result.get("message") or "").strip()
        if message and message != self._last_codex_status_line:
            self._last_codex_status_line = message
            self.log_agent_monitor(f"[ENGINEERING SUPPORT] {message}")
            self.ui_queue.put(("status_widget_dashboard_activity", message))

    def poll_codex_repair(self) -> None:
        status = self.codex_repair.poll_status()
        if not status:
            return
        message = str(status.get("message") or "").strip()
        if message and message != self._last_codex_status_line:
            self._last_codex_status_line = message
            self.log_agent_monitor(f"[ENGINEERING SUPPORT] {message}")
            self.ui_queue.put(("status_widget_dashboard_activity", message))
        state = str(status.get("state") or "").strip().lower()
        startup_recovery_pending = bool(self.pending_codex_recovery)
        if state == "restart_requested" and startup_recovery_pending:
            return
        if state == "restart_requested" and not self.restart_requested:
            self.chat_append("system", "[Self-Heal] Engineering repair verified. Restarting Piper to resume the interrupted request.")
            self.on_restart()

    def resume_codex_recovery_if_needed(self) -> None:
        if not self.boot_ready or self.has_active_operations():
            return
        recovery = self.pending_codex_recovery or self.codex_repair.peek_recovery()
        if not recovery:
            return
        self.pending_codex_recovery = {}
        summary = str(recovery.get("summary") or "").strip()
        retry_user_message = str(recovery.get("retry_user_message") or "").strip()
        if summary:
            self.chat_append("system", f"[Self-Heal] {summary}")
        self.chat_append("system", "[Self-Heal] Retrying the interrupted request.")
        self.codex_repair.consume_recovery()
        if retry_user_message:
            self.ui_queue.put(("status_widget_dashboard_activity", "Retrying interrupted request after repair."))
            self.submit_user_text(retry_user_message)

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

        style_state = self.style_mgr.load(0.7, "af_heart", 0.9)
        if dpg.does_item_exist(self.tags.mode_indicator):
            mode_name = style_state.name.upper() if style_state.name.lower() != "default" else ""
            self.set_mode_indicator(f"MODE: {mode_name}" if mode_name else "")
        refresh_live_screen_ui_action(self)
        self._refresh_top_bar()
        self.refresh_documents_view()
        self.refresh_interaction_state()
        self.proactive_monitor.start()

        boot_thread = threading.Thread(target=self.boot_mgr.run_sequence, daemon=True)
        boot_thread.start()

        try:
            while dpg.is_dearpygui_running():
                self.pump_ui_queue()
                self.poll_codex_repair()
                tts_busy = self.is_tts_active()
                if tts_busy != self._last_tts_busy:
                    self.refresh_interaction_state()
                dpg.render_dearpygui_frame()
                self._flush_autoscrolls()
        finally:
            self.proactive_monitor.stop()
            dpg.destroy_context()
            self.code_session.shutdown()
        return RESTART_EXIT_CODE if self.restart_requested else 0
