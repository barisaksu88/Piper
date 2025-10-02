from __future__ import annotations
import dearpygui.dearpygui as dpg
from ui.layout_constants import L

def run(*, drive_event_loop: bool = False) -> None:   # ★ added gate (default off)
    """Behavior-preserving wrapper (gate lets us opt-in later)."""
    if drive_event_loop:
        dpg.create_viewport(
            title="Piper GUI",
            width=L.WINDOW.WIDTH,
            height=L.WINDOW.HEIGHT,
        )
        try:
            dpg.configure_viewport(0, decorated=False)
        except Exception:
            try: dpg.set_viewport_decorated(False)
            except Exception: pass
        try:
            dpg.setup_dearpygui()
            dpg.show_viewport()
        except Exception: pass
        dpg.start_dearpygui()
        try:
            dpg.destroy_context()
        except Exception:
            pass

