# C:\Piper\scripts\ui\theme.py
# UI Theme adapter (idempotent)

import os
import dearpygui.dearpygui as dpg

def apply_theme_if_enabled() -> None:
    """
    Apply the 'clean' theme if PIPER_UI_THEME=clean.
    Safe to call multiple times; binds once.
    """
    if os.environ.get("PIPER_UI_THEME", "").lower() != "clean":
        return
    apply_clean_theme()

def apply_clean_theme() -> None:
    """
    Idempotent Dear PyGui theme creation/binding.
    """
    try:
        if dpg.does_item_exist("clean_theme"):
            dpg.bind_theme("clean_theme")
            return
    except Exception:
        # If does_item_exist not available or throws, continue to create
        pass

    with dpg.theme(tag="clean_theme"):
        with dpg.theme_component(dpg.mvAll):
            # Backgrounds & text
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (24,24,28,255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,  (24,24,28,255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,     (230,230,235,255))
            # Frames/Buttons
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,          (34,34,38,255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,   (54,54,60,255))
            dpg.add_theme_color(dpg.mvThemeCol_Button,           (34,34,38,255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,    (54,54,60,255))
            # Spacing/Rounding
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 6, category=dpg.mvThemeCat_Core)

    dpg.bind_theme("clean_theme")

