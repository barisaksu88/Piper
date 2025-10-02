from __future__ import annotations
import os
import dearpygui.dearpygui as dpg

def ensure_pane_theme() -> str:
    """Ensure minimal pane theme exists and return its tag."""
    try:
        if dpg.does_item_exist("pane_theme"):
            return "pane_theme"

        # Mirror light-blue child/window colors when requested so per-item binds don't
        # override the global theme back to defaults.
        import os
        val = os.environ.get("PIPER_UI_THEME", "").replace("_", "").strip().lower()
        lightblue = val in ("lightblue", "blue")

        with dpg.theme(tag="pane_theme"):
            if lightblue:
                # Child windows (chat/logs panes)
                with dpg.theme_component(dpg.mvChildWindow):
                    dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (25, 35, 46, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_Text,    (255, 255, 255, 255))
                # Root window safety (in case bound to a Window later)
                with dpg.theme_component(dpg.mvWindowAppItem):
                    dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (13, 27, 42, 255))
            else:
                # Leave empty for default/clean themes
                with dpg.theme_component(dpg.mvAll):
                    pass
    except Exception:
        return "pane_theme"
    return "pane_theme"

def _norm_theme():
    return os.environ.get("PIPER_UI_THEME", "").replace("_", "").strip().lower()

def apply_theme_if_enabled() -> None:
    val = _norm_theme()
    if val == "clean":
        apply_clean_theme()
    elif val in ("lightblue", "blue"):
        apply_lightblue_theme()

def apply_lightblue_theme() -> None:
    try:
        if dpg.does_item_exist("lightblue_theme"):
            dpg.bind_theme("lightblue_theme")
            return
    except Exception:
        pass

    with dpg.theme(tag="lightblue_theme"):
        with dpg.theme_component(dpg.mvAll):
            # window + child backgrounds to pale blue; keep readable text
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (200, 220, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,  (200, 220, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,     (10, 10, 10, 255))
            # mild rounding/spacing (match cleans ergonomics)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,  8, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,    6, 6, category=dpg.mvThemeCat_Core)

    dpg.bind_theme("lightblue_theme")