"""ui/layout.py"""

from __future__ import annotations
from typing import Any, Callable, Dict
import dearpygui.dearpygui as dpg

from ui.event_speech import EVENT_SPEECH_OFF, event_speech_mode_label, event_speech_mode_options
from ui.windowing import apply_windows_viewport_theme


RIGHT_PANE_WIDTH = 460

def _apply_dark_blue_theme():
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0.0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 6.0)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6.0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 8)

            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (18, 25, 40, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (22, 30, 48, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (12, 18, 30, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (25, 35, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 225, 235, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (100, 110, 130, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (40, 60, 90, 100))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (40, 60, 90, 150))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (45, 90, 150, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 120, 190, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (80, 150, 220, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (15, 22, 35, 180))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (40, 70, 110, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (60, 100, 150, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ModalWindowDimBg, (0, 0, 0, 150))
        dpg.bind_theme(global_theme)


def _estimate_text_view_height(text: str, *, min_height: int) -> int:
    lines = max(str(text or "").count("\n") + 1, 1)
    return max(min_height, min(60000, 4 + (lines * 15)))

def build_ui(*, callbacks: Dict[str, Callable[..., Any]], tags: Dict[str, Any], app_title: str, w: int, h: int) -> None:
    TAG_MAIN_WINDOW = tags["TAG_MAIN_WINDOW"]
    TAG_MAIN_TAB_BAR = tags["TAG_MAIN_TAB_BAR"]
    TAG_CHAT_CHILD = tags["TAG_CHAT_CHILD"]
    TAG_CHAT_TEXT = tags["TAG_CHAT_TEXT"]
    TAG_INPUT = tags["TAG_INPUT"]
    TAG_SEND_BUTTON = tags["TAG_SEND_BUTTON"]
    TAG_STOP_BUTTON = tags["TAG_STOP_BUTTON"]
    TAG_CLEAR_SESSION_BUTTON = tags["TAG_CLEAR_SESSION_BUTTON"]
    TAG_STATUS = tags["TAG_STATUS"]
    TAG_MODE_INDICATOR = tags["TAG_MODE_INDICATOR"]
    TAG_BOOT_LOG_CHILD = tags["TAG_BOOT_LOG_CHILD"]
    TAG_BOOT_LOG = tags["TAG_BOOT_LOG_TEXT"]
    TAG_DASHBOARD_ACTIVITY_CHILD = tags["TAG_DASHBOARD_ACTIVITY_CHILD"]
    TAG_DASHBOARD_ACTIVITY_TEXT = tags["TAG_DASHBOARD_ACTIVITY_TEXT"]
    TAG_AGENT_LOG = tags["TAG_AGENT_LOG_TEXT"]
    TAG_AGENT_LOG_CHILD = tags["TAG_AGENT_LOG_CHILD"]
    TAG_MIC_BUTTON = tags["TAG_MIC_BUTTON"]
    TAG_SNAPSHOT_BUTTON = tags["TAG_SNAPSHOT_BUTTON"]
    TAG_LIVE_SCREEN_MODE_COMBO = tags["TAG_LIVE_SCREEN_MODE_COMBO"]
    TAG_LIVE_SCREEN_INTERVAL_COMBO = tags["TAG_LIVE_SCREEN_INTERVAL_COMBO"]
    TAG_EVENT_SPEECH_COMBO = tags["TAG_EVENT_SPEECH_COMBO"]
    TAG_RESTART_BUTTON = tags["TAG_RESTART_BUTTON"]
    TAG_INGEST_BUTTON = tags["TAG_INGEST_BUTTON"]
    TAG_CODE_TAB = tags["TAG_CODE_TAB"]
    TAG_STATS_TAB = tags["TAG_STATS_TAB"]
    TAG_CODE_VIEW_CHILD = tags["TAG_CODE_VIEW_CHILD"]
    TAG_CODE_VIEW_TEXT = tags["TAG_CODE_VIEW_TEXT"]
    TAG_CODE_STATUS_TEXT = tags["TAG_CODE_STATUS_TEXT"]
    TAG_CODE_INPUT = tags["TAG_CODE_INPUT"]
    TAG_CODE_SEND_BUTTON = tags["TAG_CODE_SEND_BUTTON"]
    TAG_CODE_RUN_BUTTON = tags["TAG_CODE_RUN_BUTTON"]
    TAG_CODE_CLEAR_BUTTON = tags["TAG_CODE_CLEAR_BUTTON"]
    TAG_CODE_STOP_BUTTON = tags["TAG_CODE_STOP_BUTTON"]
    TAG_DOCUMENTS_VIEW_CHILD = tags["TAG_DOCUMENTS_VIEW_CHILD"]
    TAG_DOCUMENTS_VIEW_TEXT = tags["TAG_DOCUMENTS_VIEW_TEXT"]
    TAG_STATS_VIEW_CHILD = tags["TAG_STATS_VIEW_CHILD"]
    TAG_STATS_VIEW_TEXT = tags["TAG_STATS_VIEW_TEXT"]
    chat_wrap = max(620, w - RIGHT_PANE_WIDTH - 180)

    on_send = callbacks["on_send"]
    on_stop = callbacks["on_stop"]
    on_new_session = callbacks["on_new_session"]
    on_mic_toggle = callbacks["on_mic_toggle"]
    on_snapshot = callbacks["on_snapshot"]
    on_live_screen_mode_changed = callbacks["on_live_screen_mode_changed"]
    on_live_screen_interval_changed = callbacks["on_live_screen_interval_changed"]
    on_event_speech_mode_changed = callbacks["on_event_speech_mode_changed"]
    on_restart = callbacks["on_restart"]
    on_open_document_picker = callbacks["on_open_document_picker"]
    on_document_picker_selected = callbacks["on_document_picker_selected"]
    on_document_picker_cancel = callbacks["on_document_picker_cancel"]
    on_code_send = callbacks["on_code_send"]
    on_code_run = callbacks["on_code_run"]
    on_code_clear = callbacks["on_code_clear"]

    dpg.create_context()
    _apply_dark_blue_theme()

    with dpg.theme() as text_pane_theme:
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (12, 18, 30, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (40, 60, 90, 140))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (15, 22, 35, 180))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (40, 70, 110, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (60, 100, 150, 255))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 8, 8)

    with dpg.theme(tag="selectable_text_theme") as selectable_text_theme:
        with dpg.theme_component(dpg.mvInputText):
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (12, 18, 30, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (18, 25, 40, 0))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 225, 235, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TextSelectedBg, (60, 100, 160, 140))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 3, 1)

    with dpg.theme(tag="chat_message_list_theme") as chat_message_list_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 2)
    
    dpg.create_viewport(title=app_title, width=w, height=h, clear_color=(18, 25, 40, 255), decorated=True)
    
    # --- Main Window ---
    with dpg.window(tag=TAG_MAIN_WINDOW, label=app_title, no_title_bar=True, no_resize=True, no_move=True, no_collapse=True, no_scrollbar=True):
        
        # --- TOP BAR ---
        with dpg.group(horizontal=True):
            dpg.add_text("Piper Core", color=(80, 160, 240, 255))
            dpg.add_spacer(width=20)
            dpg.add_text("IDLE", tag=TAG_STATUS, color=(120, 180, 140, 255))
            dpg.add_spacer(width=20)
            dpg.add_text("Session: active | Style: DEFAULT", tag=TAG_MODE_INDICATOR, color=(120, 130, 150, 255))
        
        dpg.add_separator()

        # --- MAIN TAB BAR (Chat | Visual Cortex) ---
        with dpg.tab_bar(tag=TAG_MAIN_TAB_BAR):
            
            # === TAB 1: CHAT ===
            with dpg.tab(label="Chat"):
                
                # Horizontal Split: Left (Chat) | Right (Status)
                with dpg.group(horizontal=True):
                    
                    # --- LEFT COLUMN (Chat Interface) ---
                    with dpg.child_window(width=-RIGHT_PANE_WIDTH, border=False, no_scrollbar=True):
                        
                        # 1. Chat History (Darker Background)
                        with dpg.theme() as chat_bg_theme:
                            with dpg.theme_component(dpg.mvAll):
                                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (12, 18, 30, 255))
                        
                        with dpg.child_window(tag=TAG_CHAT_CHILD, autosize_x=True, autosize_y=False, height=-85, border=True):
                            dpg.bind_item_theme(dpg.last_item(), chat_bg_theme)
                            with dpg.group(tag=TAG_CHAT_TEXT):
                                pass
                            dpg.bind_item_theme(TAG_CHAT_TEXT, chat_message_list_theme)

                        # 2. Input Area
                        with dpg.group(horizontal=True):
                            dpg.add_input_text(tag=TAG_INPUT, multiline=False, width=-100, height=45, hint="Type here... (Enter to send)")
                            dpg.add_button(tag=TAG_SEND_BUTTON, label="Send", width=80, height=30, callback=on_send)

                        # 3. Button Bar
                        with dpg.group(horizontal=True):
                            dpg.add_button(tag=TAG_STOP_BUTTON, label="Stop", width=100, height=35, callback=lambda: on_stop())
                            dpg.add_button(tag=TAG_CLEAR_SESSION_BUTTON, label="Clear Session", width=140, height=35, callback=lambda: dpg.configure_item("modal_clear_session", show=True))
                            dpg.add_spacer(width=10)
                            dpg.add_button(label="MIC", width=80, height=35, tag=TAG_MIC_BUTTON, callback=on_mic_toggle)
                            dpg.add_button(label="VISION", width=80, height=35, tag=TAG_SNAPSHOT_BUTTON, callback=on_snapshot)
                            dpg.add_combo(
                                ("Display", "Window", "Pointer"),
                                default_value="Display",
                                width=110,
                                tag=TAG_LIVE_SCREEN_MODE_COMBO,
                                callback=on_live_screen_mode_changed,
                            )
                            dpg.add_combo(
                                ("2s", "5s", "10s", "15s"),
                                default_value="10s",
                                width=80,
                                tag=TAG_LIVE_SCREEN_INTERVAL_COMBO,
                                callback=on_live_screen_interval_changed,
                            )
                            dpg.add_combo(
                                event_speech_mode_options(),
                                default_value=event_speech_mode_label(EVENT_SPEECH_OFF),
                                width=150,
                                tag=TAG_EVENT_SPEECH_COMBO,
                                callback=on_event_speech_mode_changed,
                            )

                    # --- RIGHT COLUMN (Status & Monitor) ---
                    with dpg.child_window(width=RIGHT_PANE_WIDTH, border=True, autosize_y=False, no_scrollbar=True):
                        
                        with dpg.tab_bar(tag="right_tab_bar"):
                            
                            # === TAB 1: STATUS ===
                            with dpg.tab(label="Status"):
                                dpg.add_spacer(height=10)
                                
                                # 1. BOOT SEQUENCE (Initially Visible)
                                with dpg.group(tag="boot_group"):
                                    dpg.add_text("System Boot", color=(255, 255, 255, 255))
                                    dpg.add_separator()
                                    with dpg.child_window(
                                        tag=TAG_BOOT_LOG_CHILD,
                                        width=-1,
                                        height=150,
                                        border=True,
                                        horizontal_scrollbar=True,
                                    ):
                                        dpg.bind_item_theme(dpg.last_item(), text_pane_theme)
                                        dpg.add_input_text(
                                            tag=TAG_BOOT_LOG,
                                            multiline=True,
                                            readonly=True,
                                            tab_input=False,
                                            width=-1,
                                            height=_estimate_text_view_height("", min_height=96),
                                            default_value="",
                                        )
                                        dpg.bind_item_theme(TAG_BOOT_LOG, selectable_text_theme)
                                    dpg.add_spacer(height=10)
                                    dpg.add_text("Initializing...", tag="boot_status_label", color=(200, 200, 200, 255))
                                
                                # 2. ACTIVITY LOG (Hidden until Boot Ready)
                                with dpg.group(tag="status_group", show=False):
                                    with dpg.child_window(
                                        tag=TAG_DASHBOARD_ACTIVITY_CHILD,
                                        width=-1,
                                        height=-1,
                                        border=True,
                                        horizontal_scrollbar=True,
                                    ):
                                        dpg.bind_item_theme(dpg.last_item(), text_pane_theme)
                                        dpg.add_input_text(
                                            tag=TAG_DASHBOARD_ACTIVITY_TEXT,
                                            multiline=True,
                                            readonly=True,
                                            tab_input=False,
                                            width=-1,
                                            height=_estimate_text_view_height("Systems Online", min_height=140),
                                            default_value="Systems Online",
                                        )
                                        dpg.bind_item_theme(TAG_DASHBOARD_ACTIVITY_TEXT, selectable_text_theme)

                            # === TAB 2: DOCUMENTS ===
                            with dpg.tab(label="Documents"):
                                dpg.add_text("Ingested Documents", color=(175, 215, 255, 255))
                                dpg.add_separator()
                                with dpg.group(horizontal=True):
                                    dpg.add_button(tag=TAG_INGEST_BUTTON, label="Ingest Document", callback=on_open_document_picker)
                                with dpg.child_window(
                                    tag=TAG_DOCUMENTS_VIEW_CHILD,
                                    width=-1,
                                    height=-1,
                                    border=True,
                                    horizontal_scrollbar=True,
                                ):
                                    dpg.bind_item_theme(dpg.last_item(), text_pane_theme)
                                    dpg.add_input_text(
                                        tag=TAG_DOCUMENTS_VIEW_TEXT,
                                        multiline=True,
                                        readonly=True,
                                        tab_input=False,
                                        width=-1,
                                        height=_estimate_text_view_height("No documents ingested.", min_height=140),
                                        default_value="No documents ingested.",
                                    )
                                    dpg.bind_item_theme(TAG_DOCUMENTS_VIEW_TEXT, selectable_text_theme)

                            # === TAB 3: MONITOR ===
                            with dpg.tab(label="Monitor"):
                                dpg.add_text("Raw Logs", color=(255, 200, 100, 255))
                                dpg.add_separator()
                                with dpg.child_window(
                                    tag=TAG_AGENT_LOG_CHILD,
                                    width=-1,
                                    height=-50,
                                    border=True,
                                    horizontal_scrollbar=True,
                                ):
                                    dpg.bind_item_theme(dpg.last_item(), text_pane_theme)
                                    dpg.add_input_text(
                                        tag=TAG_AGENT_LOG,
                                        multiline=True,
                                        readonly=True,
                                        tab_input=False,
                                        width=-1,
                                        height=_estimate_text_view_height("Waiting for activity...", min_height=120),
                                        default_value="Waiting for activity...",
                                    )
                                    dpg.bind_item_theme(TAG_AGENT_LOG, selectable_text_theme)
                                
                                # Buttons
                                with dpg.group(horizontal=True):
                                    dpg.add_button(label="Clear Logs", callback=lambda: dpg.set_value(TAG_AGENT_LOG, ""))
                                    dpg.add_button(tag=TAG_RESTART_BUTTON, label="Restart Piper", callback=on_restart)

            # === TAB 2: VISUAL CORTEX ===
            with dpg.tab(label="Visual Cortex"):
                width, height = 512, 512
                data = [0.2] * (width * height * 4)
                
                with dpg.theme() as cortex_theme:
                    with dpg.theme_component(dpg.mvAll):
                        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (10, 10, 15, 255))

                with dpg.child_window(width=-1, height=-1):
                    dpg.bind_item_theme(dpg.last_item(), cortex_theme)
                    
                    if not dpg.does_item_exist("image_texture_registry"):
                        with dpg.texture_registry(tag="image_texture_registry"):
                            dpg.add_static_texture(width=width, height=height, default_value=data, tag="generated_image_texture")
                    
                    dpg.add_image("generated_image_texture", tag="image_pane")

            # === TAB 3: CODE ===
            with dpg.tab(label="Code", tag=TAG_CODE_TAB):
                dpg.add_text("Code Console", color=(175, 215, 255, 255))
                dpg.add_separator()
                dpg.add_text("No active process.", tag=TAG_CODE_STATUS_TEXT, color=(160, 170, 190, 255))
                dpg.add_spacer(height=6)
                with dpg.child_window(
                    tag=TAG_CODE_VIEW_CHILD,
                    width=-1,
                    height=-76,
                    border=True,
                    horizontal_scrollbar=True,
                ):
                    dpg.bind_item_theme(dpg.last_item(), text_pane_theme)
                    dpg.add_input_text(
                        tag=TAG_CODE_VIEW_TEXT,
                        multiline=True,
                        readonly=True,
                        tab_input=False,
                        width=-1,
                        height=_estimate_text_view_height("No code artefact or process output yet.", min_height=120),
                        default_value="No code artefact or process output yet.",
                    )
                    dpg.bind_item_theme(TAG_CODE_VIEW_TEXT, selectable_text_theme)
                with dpg.group(horizontal=True):
                    dpg.add_input_text(
                        tag=TAG_CODE_INPUT,
                        multiline=False,
                        width=-330,
                        hint="Send input to running process...",
                        on_enter=True,
                        callback=lambda sender=None, app_data=None, user_data=None: on_code_send(),
                    )
                    dpg.add_button(tag=TAG_CODE_SEND_BUTTON, label="Send Input", width=100, callback=on_code_send)
                    dpg.add_button(tag=TAG_CODE_RUN_BUTTON, label="Run File", width=100, callback=on_code_run)
                    dpg.add_button(tag=TAG_CODE_STOP_BUTTON, label="Stop Process", width=100, callback=lambda sender=None, app_data=None, user_data=None: on_stop())
                with dpg.group(horizontal=True):
                    dpg.add_button(tag=TAG_CODE_CLEAR_BUTTON, label="Clear Console", width=120, callback=on_code_clear)

            # === TAB 4: STATS ===
            with dpg.tab(label="Stats", tag=TAG_STATS_TAB):
                dpg.add_text("Turn Statistics", color=(175, 215, 255, 255))
                dpg.add_separator()
                with dpg.child_window(
                    tag=TAG_STATS_VIEW_CHILD,
                    width=-1,
                    height=-1,
                    border=True,
                    horizontal_scrollbar=True,
                ):
                    dpg.bind_item_theme(dpg.last_item(), text_pane_theme)
                    dpg.add_input_text(
                        tag=TAG_STATS_VIEW_TEXT,
                        multiline=True,
                        readonly=True,
                        tab_input=False,
                        width=-1,
                        height=_estimate_text_view_height("No stats recorded yet.", min_height=160),
                        default_value="No stats recorded yet.",
                    )
                    dpg.bind_item_theme(TAG_STATS_VIEW_TEXT, selectable_text_theme)

        # Key Handlers
        enter_armed = {"armed": True}
        with dpg.handler_registry():
            def _key_send():
                if not dpg.is_item_focused(TAG_INPUT) or not enter_armed["armed"]: return
                enter_armed["armed"] = False
                on_send()
            def _key_release(): enter_armed["armed"] = True

            dpg.add_key_down_handler(key=dpg.mvKey_Return, callback=_key_send)
            if hasattr(dpg, "add_key_release_handler"):
                dpg.add_key_release_handler(key=dpg.mvKey_Return, callback=_key_release)

        dpg.configure_item(TAG_INPUT, enabled=False)
        dpg.configure_item(TAG_SEND_BUTTON, enabled=False)
        dpg.configure_item(TAG_STOP_BUTTON, enabled=False)
        dpg.configure_item(TAG_CLEAR_SESSION_BUTTON, enabled=False)
        dpg.configure_item(TAG_MIC_BUTTON, enabled=False)
        dpg.configure_item(TAG_SNAPSHOT_BUTTON, enabled=False)
        dpg.configure_item(TAG_EVENT_SPEECH_COMBO, enabled=False)
        dpg.configure_item(TAG_RESTART_BUTTON, enabled=False)
        dpg.configure_item(TAG_INGEST_BUTTON, enabled=False)
        dpg.configure_item(TAG_CODE_INPUT, enabled=False)
        dpg.configure_item(TAG_CODE_SEND_BUTTON, enabled=False)
        dpg.configure_item(TAG_CODE_RUN_BUTTON, enabled=False)
        dpg.configure_item(TAG_CODE_STOP_BUTTON, enabled=False)
        dpg.configure_item(TAG_CODE_CLEAR_BUTTON, enabled=False)

        with dpg.file_dialog(
            directory_selector=False,
            show=False,
            callback=on_document_picker_selected,
            cancel_callback=on_document_picker_cancel,
            file_count=10,
            tag="document_ingest_dialog",
            width=760,
            height=460,
            modal=True,
        ):
            dpg.add_file_extension(".*")
            dpg.add_file_extension("Documents (*.pdf *.docx *.txt *.md *.json *.py *.csv){.pdf,.docx,.txt,.md,.json,.py,.csv}")
            dpg.add_file_extension(".pdf", color=(255, 205, 120, 255))
            dpg.add_file_extension(".docx", color=(120, 180, 255, 255))
            dpg.add_file_extension(".txt", color=(160, 255, 160, 255))
            dpg.add_file_extension(".md", color=(160, 255, 160, 255))
        
    # Modal for clearing session
    with dpg.window(label="Confirm", modal=True, show=False, tag="modal_clear_session", no_resize=True, no_move=True):
        dpg.add_text("Clear current session memory?\nThis cannot be undone.")
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Clear Session", width=120, callback=lambda: (on_new_session(), dpg.configure_item("modal_clear_session", show=False)))
            dpg.add_button(label="Cancel", width=120, callback=lambda: dpg.configure_item("modal_clear_session", show=False))

    dpg.setup_dearpygui()
    dpg.show_viewport()
    apply_windows_viewport_theme(app_title)
    dpg.set_primary_window(TAG_MAIN_WINDOW, True)
