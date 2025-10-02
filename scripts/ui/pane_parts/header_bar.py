from __future__ import annotations
"""Header Bar pane (authoritative)."""
import os
import sys
from pathlib import Path
from typing import Optional
import dearpygui.dearpygui as dpg
from ui.layout_constants import L

# Tags
HEADER_GROUP = "header_group"
STATE_DOT_DRAW = "state_dot_draw"
STATE_DOT_CIRCLE = "state_dot_circle"
STATE_LABEL = "state_label"
HB_LABEL = "hb_label"
TONE_LABEL = "tone_label"
SARCASM_LABEL = "sarcasm_label"
TAILING_LABEL = "tailing_label"

HDR_ITEM_HANDLERS   = "hdr_item_handlers"    # item-scoped registry (attached to header)
HDR_GLOBAL_HANDLERS = "hdr_global_handlers"  # global registry (drag/release)

# Fallback state (used if OS-native drag isn't available)
_dragging = False
_offset_screen = (0, 0)   # cursor_screen - viewport_screen at drag start

def _get_cursor_screen():
    """Return OS cursor position in screen pixels on Windows; else None."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (int(pt.x), int(pt.y))
    except Exception:
        return None

def _start_native_drag_if_possible() -> bool:
    """Issue OS move on Windows; returns True if the OS took over."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        hwnd = dpg.get_viewport_platform_handle()
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        user32.ReleaseCapture()
        WM_NCLBUTTONDOWN = 0x00A1
        HTCAPTION = 2
        user32.SendMessageW(int(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)
        return True
    except Exception:
        return False

#  Item-scoped START (fire exactly when you click the header) 
def _hdr_on_item_clicked():
    """Begin drag when user clicks the header group. We only *start* here; movement is handled by global drag handler."""
    global _dragging, _offset_screen
    # Prefer OS-native drag on Windows
    if _start_native_drag_if_possible():
        _dragging = False  # OS owns move; global handlers harmless
        return

    # Fallback: compute screen-pixel offset at drag start
    cur = _get_cursor_screen()
    if cur is not None:
        vx, vy = dpg.get_viewport_pos()  # screen px
        _offset_screen = (cur[0] - vx, cur[1] - vy)
        _dragging = True
        return

    # Non-Windows ultimate fallback: use DPG logical coords (rare)
    mx, my = dpg.get_mouse_pos()
    vx, vy = dpg.get_viewport_pos()
    _offset_screen = (mx - vx, my - vy)
    _dragging = True

#  Global MOVE/END (runs while button held down) 
def _global_on_mouse_drag():
    """Move viewport during drag (fallback paths only). NOTE: When OS-native drag is active, this simply won't run."""
    global _dragging, _offset_screen
    if not _dragging:
        return

    cur = _get_cursor_screen()
    if cur is not None:
        vx = cur[0] - _offset_screen[0]
        vy = cur[1] - _offset_screen[1]
        dpg.set_viewport_pos((int(vx), int(vy)))
    else:
        mx, my = dpg.get_mouse_pos()
        vx = int(mx - _offset_screen[0])
        vy = int(my - _offset_screen[1])
        dpg.set_viewport_pos((vx, vy))

def _global_on_mouse_release():
    """Stop fallback drag (OS-native drag ignores this)."""
    global _dragging
    _dragging = False

def mount_header(header_root: str) -> None:
    """Bind handlers such that:
      • Clicking the header (item-scoped) *starts* drag immediately.
      • Global drag/release keep things moving until mouseup.
    Idempotent (safe to call multiple times)."""
    if not dpg.does_item_exist(header_root):
        return

    # 1) Item-scoped: start on click of the header itself
    if not dpg.does_item_exist(HDR_ITEM_HANDLERS):
        with dpg.item_handler_registry(tag=HDR_ITEM_HANDLERS):
            # DearPyGui provides "item clicked" handler in item registries
            dpg.add_item_clicked_handler(callback=_hdr_on_item_clicked)
    dpg.bind_item_handler_registry(header_root, HDR_ITEM_HANDLERS)

    # 2) Global: move while dragging & stop on release
    if not dpg.does_item_exist(HDR_GLOBAL_HANDLERS):
        with dpg.handler_registry(tag=HDR_GLOBAL_HANDLERS):
            dpg.add_mouse_drag_handler(button=0, threshold=0, callback=_global_on_mouse_drag)
            dpg.add_mouse_release_handler(button=0, callback=_global_on_mouse_release)
            
# Build + Refresh
def build(*, parent: str | int, log_path: Optional[str] = None) -> str:
    """Build the header inside the given parent container. Returns the tag of the header group ('header_group')."""
    if not parent:
        raise ValueError("header_bar.build() requires an explicit parent")

    # Root group for the header row
    if not dpg.does_item_exist(HEADER_GROUP):
        dpg.add_group(tag=HEADER_GROUP, horizontal=True, parent=parent)

    # Left cluster
    if not dpg.does_item_exist("hdr_title"):
        dpg.add_text("Piper GUI", tag="hdr_title", parent=HEADER_GROUP)
    dpg.add_spacer(width=L.SPACE.PAD, parent=HEADER_GROUP)

    if not dpg.does_item_exist("font_registry"):
        with dpg.font_registry(tag="font_registry"):
            pass

    # Ensure a header font sized from L.FONT.HEADER exists, then bind it to the title tag
    try:
        _hdr_size = int(getattr(getattr(L, "FONT", object()), "HEADER", 18))
    except Exception:
        _hdr_size = 18

    if not dpg.does_item_exist("header_font"):
        # Try common Windows fonts; silently no-op if none found
        for _fp in (
            r"C:\\Windows\\Fonts\\segoeui.ttf",
            r"C:\\Windows\\Fonts\\arial.ttf",
            r"C:\\Windows\\Fonts\\calibri.ttf",
        ):
            try:
                if os.path.exists(_fp):
                    dpg.add_font(_fp, _hdr_size, tag="header_font")
                    break
            except Exception:
                pass

    if dpg.does_item_exist("header_font") and dpg.does_item_exist("hdr_title"):
        try:
            dpg.bind_item_font("hdr_title", "header_font")
        except Exception:
            pass

    if not dpg.does_item_exist(STATE_DOT_DRAW):
        dpg.add_drawlist(width=L.HEADER.DOT_SIZE, height=L.HEADER.DOT_SIZE, parent=HEADER_GROUP, tag=STATE_DOT_DRAW)
    if not dpg.does_item_exist(STATE_DOT_CIRCLE):
        dpg.draw_circle(
            center=(7, 7),
            radius=6,
            color=(0, 0, 0, 0),
            fill=(120, 120, 120, 255),
            tag=STATE_DOT_CIRCLE,
            parent=STATE_DOT_DRAW,
        )

    dpg.add_spacer(width=L.SPACE.SMALL, parent=HEADER_GROUP)
    if not dpg.does_item_exist("hdr_state_static"):
        dpg.add_text("State:", tag="hdr_state_static", parent=HEADER_GROUP)
    if not dpg.does_item_exist(STATE_LABEL):
        dpg.add_text("-", tag=STATE_LABEL, parent=HEADER_GROUP)

    # helper to add tiny separators safely
    def _sep(tag: str) -> None:
        if not dpg.does_item_exist(tag):
            dpg.add_spacer(width=L.SPACE.PAD, parent=HEADER_GROUP)
            dpg.add_text("~", tag=tag, parent=HEADER_GROUP)
            dpg.add_spacer(width=L.SPACE.SMALL, parent=HEADER_GROUP)

    _sep("sep_state_hb")
    if not dpg.does_item_exist(HB_LABEL):
        dpg.add_text("", tag=HB_LABEL, parent=HEADER_GROUP)

    _sep("sep_hb_tone")
    if not dpg.does_item_exist(TONE_LABEL):
        dpg.add_text("Tone: neutral", tag=TONE_LABEL, parent=HEADER_GROUP)

    _sep("sep_tone_sarc")
    if not dpg.does_item_exist(SARCASM_LABEL):
        dpg.add_text("Sarcasm: off", tag=SARCASM_LABEL, parent=HEADER_GROUP)

    _sep("sep_sarc_tail")
    tail = ""
    if log_path:
        try:
            tail = f"Tailing: {Path(log_path).name or log_path}"
        except Exception:
            tail = f"Tailing: {log_path}"

    if not dpg.does_item_exist(TAILING_LABEL):
        dpg.add_text(tail, tag=TAILING_LABEL, parent=HEADER_GROUP)
    else:
        dpg.set_value(TAILING_LABEL, tail)

    # Register frameless drag handlers (hover-gated)
    mount_header(HEADER_GROUP)

    return HEADER_GROUP

def refresh(state_text: str, heartbeat_text: str, tone_text: str, sarcasm_text: str,
    tailing_text: str, state_name: Optional[str] = None,) -> None:
    """Update header labels. Keeps only RAW state name in STATE_LABEL (static 'State:' prefix is separate)."""
    if dpg.does_item_exist(STATE_LABEL):
        _raw = (state_text or "").strip()
        if _raw.lower().startswith("state:"):
            _raw = _raw.split(":", 1)[1].strip()
        dpg.set_value(STATE_LABEL, _raw or "-")
    if dpg.does_item_exist(HB_LABEL):
        dpg.set_value(HB_LABEL, heartbeat_text or "last change = n/a")
    if dpg.does_item_exist(TONE_LABEL):
        dpg.set_value(TONE_LABEL, tone_text)
    if dpg.does_item_exist(SARCASM_LABEL):
        dpg.set_value(SARCASM_LABEL, sarcasm_text)
    if dpg.does_item_exist(TAILING_LABEL):
        dpg.set_value(TAILING_LABEL, tailing_text)

    if state_name is not None:
        set_state_dot(state_name)

# State Dot (single authority)
_DOT_LAST: Optional[str] = None

def _find_dot_ids() -> list[str]:
    return [STATE_DOT_CIRCLE]

def set_state_dot(state_name: str) -> None:
    """Update the colored dot to reflect the current state. Robust across aliases; safe if the draw item doesn't exist yet."""
    global _DOT_LAST

    if not isinstance(state_name, str):
        name = ""
    else:
        name = state_name.strip().upper()

    if _DOT_LAST == name:
        return

    # Allow an external palette to be injected via module globals
    colors = globals().get(
        "STATE_COLORS",
        {"SLEEPING": (96, 96, 96, 255),
            "WAKING": (255, 190, 60, 255),
            "LISTENING": (90, 160, 255, 255),
            "THINKING": (160, 120, 255, 255),
            "SPEAKING": (80, 220, 120, 255),},)
    color = colors.get(name, colors.get("SLEEPING", (96, 96, 96, 255)))

    # Update "State: …" text to match the resolved name (if present)
    try:
        if dpg.does_item_exist(STATE_LABEL):
            dpg.set_value(STATE_LABEL, name or "-")
    except Exception:
        pass

    # Paint existing circle aliases (both fill and outline for DPG quirks)
    for dot_tag in _find_dot_ids():
        try:
            dpg.configure_item(dot_tag, fill=color)
        except Exception:
            pass
        try:
            dpg.configure_item(dot_tag, color=color)
        except Exception:
            pass

    _DOT_LAST = name

# Public API expected by helpers/header_utils.py

def set_heartbeat(text: str) -> None:
    if dpg.does_item_exist(HB_LABEL):
        dpg.set_value(HB_LABEL, text)

def set_state(text: str) -> None:
    raw = (text or "").strip()
    if raw.lower().startswith("state:"):
        raw = raw.split(":", 1)[1].strip()
    if dpg.does_item_exist(STATE_LABEL):
        dpg.set_value(STATE_LABEL, raw or "-")
    set_state_dot(raw)

def set_tone(text: str) -> None:
    raw = (text or "").strip()
    if raw.lower().startswith("tone:"):
        raw = raw.split(":", 1)[1].strip()
    val = f"Tone: {raw}" if raw else "Tone: -"
    if dpg.does_item_exist(TONE_LABEL):
        dpg.set_value(TONE_LABEL, val)

def set_sarcasm(on: bool) -> None:
    if dpg.does_item_exist(SARCASM_LABEL):
        dpg.set_value(SARCASM_LABEL, "Sarcasm: on" if on else "Sarcasm: off")

# NOTE: Keep a scoped per-frame update for HB_LABEL in the entry_impl tick loop, guarded by value-change checks. 
# Do NOT consolidate hb_text usage across refresh + per-frame paths; subtle timing deps can break labels.