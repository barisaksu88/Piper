from __future__ import annotations

import ctypes
import os
import time

import dearpygui.dearpygui as dpg


_DWMWA_USE_IMMERSIVE_DARK_MODE = (20, 19)
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_WINDOW_BG_RGB = (18, 25, 40)
_WINDOW_TEXT_RGB = (220, 225, 235)


def _rgb_to_colorref(rgb: tuple[int, int, int]) -> int:
    red, green, blue = rgb
    return red | (green << 8) | (blue << 16)


def _find_viewport_handle(title: str) -> int:
    user32 = ctypes.windll.user32
    for _ in range(20):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return int(hwnd)
        time.sleep(0.05)
    return 0


def _set_window_attribute(hwnd: int, attribute: int, value: int) -> bool:
    typed_value = ctypes.c_int(value)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd,
        attribute,
        ctypes.byref(typed_value),
        ctypes.sizeof(typed_value),
    )
    return result == 0


def apply_windows_viewport_theme(title: str) -> bool:
    if os.name != "nt":
        return False
    if dpg.get_platform() != dpg.mvPlatform_Windows:
        return False

    try:
        hwnd = _find_viewport_handle(title)
        if not hwnd:
            return False

        dark_mode_applied = False
        for attribute in _DWMWA_USE_IMMERSIVE_DARK_MODE:
            if _set_window_attribute(hwnd, attribute, 1):
                dark_mode_applied = True
                break

        _set_window_attribute(hwnd, _DWMWA_CAPTION_COLOR, _rgb_to_colorref(_WINDOW_BG_RGB))
        _set_window_attribute(hwnd, _DWMWA_BORDER_COLOR, _rgb_to_colorref(_WINDOW_BG_RGB))
        _set_window_attribute(hwnd, _DWMWA_TEXT_COLOR, _rgb_to_colorref(_WINDOW_TEXT_RGB))
        return dark_mode_applied
    except Exception:
        return False
