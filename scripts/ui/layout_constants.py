# CONTRACT - Single Source of Truth (UI Metrics)
# ui/layout_constants.py is the single source of truth for geometry, spacing,
# ratios, and theme numbers. All UI code must derive sizes from here; no
# hard-coded magic numbers in panes/components.
from __future__ import annotations
"""Central catalog for Piper UI layout constants.
Prep-only: importing this file must not change behavior anywhere.
Usage: `from ui.layout_constants import L` (L is a simple namespace instance)."""
from dataclasses import dataclass

# ---- Basic scalar types -------------------------------------------------
@dataclass(frozen=True)
class Window:
    WIDTH: int = 1095 
    HEIGHT: int = 780  
    MIN_WIDTH: int = 980
    MIN_HEIGHT: int = 640
    OFFSET_X: int = 0   # used by _panes_impl.add_window(...)
    OFFSET_Y: int = 0

@dataclass(frozen=True)
class Pane:
    LEFT_WIDTH: int = 780   # int(1345 * 0.58)
    RIGHT_WIDTH: int = 315  # 1345 - 780 - 250
    HEADER_HEIGHT: int = 48
    FOOTER_HEIGHT: int = 36
    SPLIT_RATIO: float = 0.58      # LEFT_W = int(WINDOW.WIDTH * SPLIT_RATIO)
    RIGHT_GUTTER: int = 250        # RIGHT_W = WIDTH - LEFT_W - RIGHT_GUTTER
    BODY_VPAD: int = 80            # ROW_H = WINDOW.HEIGHT - BODY_VPAD

@dataclass(frozen=True)
class Spacing:
    PAD: int = 12
    GAP: int = 4
    SECTION_GAP: int = 4
    BORDER: int = 1
    ROUNDING: int = 12
    SMALL: int = 8
    MID: int = 16
    
@dataclass(frozen=True)
class Fonts:
    UI: float = 16.0
    MONO: float = 14.0
    HEADER: float = 18.0
    TINY: float = 12.0

@dataclass(frozen=True)
class Colors:
    # Placeholder RGBA tuples (Dear PyGui style later if needed)
    BG: tuple[int, int, int, int] = (18, 18, 18, 255)
    FG: tuple[int, int, int, int] = (230, 230, 230, 255)
    ACCENT: tuple[int, int, int, int] = (0, 170, 180, 255)
    MUTED: tuple[int, int, int, int] = (120, 120, 120, 255)
    OK: tuple[int, int, int, int] = (60, 200, 120, 255)
    WARN: tuple[int, int, int, int] = (240, 180, 40, 255)
    ERR: tuple[int, int, int, int] = (220, 60, 60, 255)

@dataclass(frozen=True)
class Heartbeat:
    # Poll/refresh cadence; aligns with existing POLL_INTERVAL_SEC
    POLL_SEC: float = 0.35

# ---- Aggregate/semantic namespaces -------------------------------------
@dataclass(frozen=True)
class AVATAR:
    PANEL_H: int = 450     # avatar panel height (right/bottom)
    MIN_PANEL_H: int = 180 # minimal initial avatar panel height
    FIT_MARGIN: float = 0.96

@dataclass(frozen=True)
class RIGHTCOL:
    # Space reserved below logs for avatar panel + vertical gap
    RESERVED_BELOW: int | None = None  # computed at call sites if needed

@dataclass(frozen=True)
class HEADER:
    SEP_VISIBLE: bool = False
    SEP_THICKNESS: int = 1
    BOTTOM_GAP: int = 6
    DOT_SIZE: int = 14

@dataclass(frozen=True)
class CHAT:
    INSET_LEFT: int = -8
    INSET_TOP: int = 4

# ----- Chat Input Bar geometry (CI01) -----
@dataclass(frozen=True)
class INPUT:
    HEIGHT: int = 36          # height of input area (line), px
    PAD_Y: int = 6            # vertical padding inside the input bar, px
    SEND_BTN_WIDTH: int = 88  # reserved width for future Send button, px
    MAXLEN: int = 2048        # max characters when wired (CI02)

# ---- Export namespace `L` ----------------------------------------------
class _LNamespace:
    WINDOW: Window = Window()
    PANE: Pane = Pane()
    SPACE: Spacing = Spacing()
    FONT: Fonts = Fonts()
    COLOR: Colors = Colors()
    HB: Heartbeat = Heartbeat()
    AVATAR: AVATAR = AVATAR()
    RIGHTCOL: RIGHTCOL = RIGHTCOL()
    HEADER: HEADER = HEADER()
    CHAT: CHAT = CHAT()
    INPUT: INPUT = INPUT()

L = _LNamespace()
