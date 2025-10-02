"""Chat column builder (layout + local echo + CLI bridge send).
Keeps logic out of ui/_panes_impl.py.
"""
from __future__ import annotations
import dearpygui.dearpygui as dpg
from ui.layout_constants import L
from services.input_bridge_cli import InputBridgeCLI, BridgeConfig
from ui.hooks.llm_chat import reply_for_user_text as _llm_reply_for_user_text
from ui.helpers.scroll_utils import apply_autoscroll_and_breathing, set_bottom_padding_next_frame, scroll_to_bottom_next_frame

# --- local state ---
_bridge: InputBridgeCLI | None = None

def _bridge_mode() -> str:
    try:
        return getattr(getattr(_bridge, "config", None), "mode", "attach")
    except Exception:
        return "attach"

# --- IB03 (event-driven): inline banner + retry (no pollers) ---

def _cli_error_handler(exc: BaseException) -> None:
    """Show inline banner on real bridge error."""
    try:
        if _bridge_mode() != "attach" and dpg.does_item_exist("cli_banner_inline"):
            dpg.configure_item("cli_banner_inline", show=True)
    except Exception:
        pass


def _restart_bridge() -> None:
    global _bridge
    try:
        if _bridge:
            try:
                _bridge.stop()
            except Exception:
                pass
        _bridge = None
        _ensure_bridge()
        if dpg.does_item_exist("cli_banner_inline"):
            dpg.configure_item("cli_banner_inline", show=False)
    except Exception:
        pass


def _cli_retry(sender=None, app_data=None, user_data=None):
    _restart_bridge()


def _append_chat_line(text: str) -> None:
    try:
        cur = dpg.get_value("chat_text") if dpg.does_item_exist("chat_text") else ""
        cur = (cur + ("\n" if cur else "") + text)
        if dpg.does_item_exist("chat_text"):
            dpg.set_value("chat_text", cur)
            # always autoscroll + keep breathing room when a new line is appended
            scroll_to_bottom_next_frame("chat_scroll")
            apply_autoscroll_and_breathing(
                "chat_scroll",
                "chat_pad",
                ((L.INPUT.HEIGHT + (L.INPUT.PAD_Y * 2)) * 2) + L.SPACE.SMALL,
            )
    except Exception:
        pass


def _ensure_bridge() -> InputBridgeCLI | None:
    global _bridge
    try:
        if _bridge is None:
            _bridge = InputBridgeCLI(on_line=lambda s: _append_chat_line(f"CLI: {s}"), on_error=_cli_error_handler, config=BridgeConfig())
            _bridge.start()
        return _bridge
    except Exception:
        return None


def _refocus_input() -> None:
    """Robustly return typing focus to the chat input after submit.
    Tries immediate focus calls and then schedules a next-frame fallback.
    """
    try:
        try:
            dpg.set_item_keyboard_focus("chat_input")
            return
        except Exception:
            pass
        try:
            dpg.focus_item("chat_input")
            return
        except Exception:
            pass
        try:
            dpg.set_keyboard_focus("chat_input")  # older fallback in some builds
            return
        except Exception:
            pass
        # next-frame fallback (more reliable when callback steals focus)
        try:
            dpg.set_frame_callback(dpg.get_frame_count() + 1, lambda s=None: dpg.focus_item("chat_input"))
        except Exception:
            pass
    except Exception:
        pass


def _chat_submit(sender=None, app_data=None, user_data=None):
    try:
        if not dpg.does_item_exist("chat_input"):
            return
        msg = (dpg.get_value("chat_input") or "").strip()
        if not msg:
            return
        _append_chat_line(f"You: {msg}")
        dpg.set_value("chat_input", "")
        # keep focus for continuous typing
        _refocus_input()
        # LLM01: direct stub reply (independent of CLI bridge)
        try:
            for _extra in _llm_reply_for_user_text(msg, persona=None):
                _append_chat_line(_extra)
        except Exception:
            pass
        # bridge forward in attach mode
        b = _ensure_bridge()
        try:
            if b and getattr(b, "config", None) and getattr(b.config, "mode", "attach") == "attach":
                b.send(msg)
                if dpg.does_item_exist("cli_banner_inline"):
                    dpg.configure_item("cli_banner_inline", show=False)
        except Exception:
            pass
    except Exception:
        pass


def _ensure_input_theme():
    if not dpg.does_item_exist("__chat_input_theme"):
        with dpg.theme(tag="__chat_input_theme"):
            with dpg.theme_component(dpg.mvInputText):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, L.COLOR.BG)
                dpg.add_theme_color(dpg.mvThemeCol_Text, L.COLOR.FG)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, float(L.SPACE.ROUNDING))
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8.0, 6.0)


def build(*, parent: str, pane_theme) -> None:
    """Build the left chat column under the given parent tag."""
    LEFT_W = L.PANE.LEFT_WIDTH
    PAD = L.SPACE.PAD
    ROW_H = L.WINDOW.HEIGHT - L.PANE.BODY_VPAD

    # container
    if not dpg.does_item_exist("chat_container"):
        with dpg.group(parent=parent, tag="chat_container"):
            with dpg.group(horizontal=True, tag="chat_header_row"):
                dpg.add_text("Chat", tag="chat_hdr")
                dpg.add_spacer(width=L.SPACE.GAP)
                dpg.add_button(label="Copy Chat", tag="copy_chat_btn",
                               callback=lambda: dpg.set_clipboard_text(dpg.get_value("chat_text") or ""))
                dpg.add_spacer(width=L.SPACE.GAP)
                # IB03 inline banner (hidden by default; event-driven)
                with dpg.group(tag="cli_banner_inline", horizontal=True, show=False):
                    dpg.add_text("CLI bridge stopped —")
                    dpg.add_spacer(width=6)
                    dpg.add_button(label="Retry", tag="cli_retry_btn", callback=_cli_retry)
                dpg.add_spacer(width=L.SPACE.GAP)
            dpg.add_spacer(height=L.CHAT.INSET_TOP)
            dpg.add_group(tag="chat_column", horizontal=False)

        # scroll
        INPUT_H = (L.INPUT.HEIGHT + (L.INPUT.PAD_Y * 2))
        with dpg.child_window(
            parent="chat_column",
            tag="chat_scroll",
            width=(LEFT_W - PAD),
            height=(ROW_H - INPUT_H - L.SPACE.SECTION_GAP),
            autosize_x=False,
            autosize_y=False,
            no_scrollbar=False,
        ):
            dpg.add_text("", tag="chat_text", wrap=(LEFT_W - PAD - L.SPACE.PAD - L.SPACE.GAP))
            dpg.add_spacer(tag="chat_pad", height=((INPUT_H * 2) + L.SPACE.SMALL))
        try:
            dpg.bind_item_theme("chat_scroll", pane_theme)
        except Exception:
            pass

        # ensure bridge exists at startup (so test flag / real errors surface)
        try:
            _ensure_bridge()
        except Exception:
            pass

        # input bar
        with dpg.child_window(
            parent="chat_column",
            tag="chat_input_bar",
            width=(LEFT_W - PAD),
            height=INPUT_H,
            autosize_x=False,
            autosize_y=False,
            no_scrollbar=True,
            border=True,
        ):
            dpg.add_spacer(height=max(0, L.INPUT.PAD_Y // 10))
            dpg.add_input_text(tag="chat_input", hint="", width=(LEFT_W - PAD - L.SPACE.PAD - L.SPACE.GAP), on_enter=True, callback=_chat_submit)
            _ensure_input_theme()
            try:
                dpg.bind_item_theme("chat_input", "__chat_input_theme")
            except Exception:
                pass

    # ensure path for existing layouts
    if dpg.does_item_exist("chat_container"):
        # ensure bridge exists on ensure path too
        try:
            _ensure_bridge()
        except Exception:
            pass
        if not dpg.does_item_exist("chat_column"):
            dpg.add_group(tag="chat_column", parent="chat_container", horizontal=False)
        # ensure inline banner exists even on ensure path
        if dpg.does_item_exist("chat_header_row") and not dpg.does_item_exist("cli_banner_inline"):
            with dpg.group(tag="cli_banner_inline", parent="chat_header_row", horizontal=True, show=False):
                dpg.add_text("CLI bridge stopped —")
                dpg.add_spacer(width=6)
                dpg.add_button(label="Retry", tag="cli_retry_btn", callback=_cli_retry)
        INPUT_H = (L.INPUT.HEIGHT + (L.INPUT.PAD_Y * 2))
        if dpg.does_item_exist("chat_scroll"):
            try:
                dpg.configure_item("chat_scroll", width=(LEFT_W - PAD), height=max(100, ROW_H - INPUT_H - L.SPACE.SECTION_GAP))
                if dpg.does_item_exist("chat_text"):
                    dpg.configure_item("chat_text", wrap=(LEFT_W - PAD - L.SPACE.PAD - L.SPACE.GAP))
                if dpg.does_item_exist("chat_pad"):
                    set_bottom_padding_next_frame("chat_pad", ((INPUT_H * 2) + L.SPACE.SMALL))
            except Exception:
                pass
        if not dpg.does_item_exist("chat_input_bar"):
            with dpg.child_window(parent="chat_column", tag="chat_input_bar",
                                  width=(LEFT_W - PAD), height=INPUT_H,
                                  autosize_x=False, autosize_y=False,
                                  no_scrollbar=True, border=True):
                dpg.add_spacer(height=max(0, L.INPUT.PAD_Y // 10))
                dpg.add_input_text(tag="chat_input", hint="", width=(LEFT_W - PAD - L.SPACE.PAD - L.SPACE.GAP), on_enter=True, callback=_chat_submit)
                _ensure_input_theme()
                try:
                    dpg.bind_item_theme("chat_input", "__chat_input_theme")
                except Exception:
                    pass
        else:
            if not dpg.does_item_exist("chat_input"):
                dpg.add_input_text(tag="chat_input", hint="", width=(LEFT_W - PAD), on_enter=True, callback=_chat_submit, parent="chat_input_bar")
                _ensure_input_theme()
                try:
                    dpg.bind_item_theme("chat_input", "__chat_input_theme")
                except Exception:
                    pass
            else:
                try:
                    dpg.configure_item("chat_input", width=(LEFT_W - PAD - L.SPACE.PAD - L.SPACE.GAP))
                    _ensure_input_theme()
                    dpg.bind_item_theme("chat_input", "__chat_input_theme")
                except Exception:
                    pass
