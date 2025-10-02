"""
B04.8 Header bridge â€” tiny wrapper around header bar refresh.
Keeps panes.py slim. No new literals.
"""
from __future__ import annotations

def apply_header_updates(hb_module, state_text: str, heartbeat_text: str,
                         tone_text: str, sarcasm_text: str, tailing_text: str) -> None:
    """Call header bar's refresh() if present, guarded."""
    try:
        if hb_module is not None and hasattr(hb_module, "refresh"):
            hb_module.refresh(
                state_text=state_text,
                heartbeat_text=heartbeat_text,
                tone_text=tone_text,
                sarcasm_text=sarcasm_text,
                tailing_text=tailing_text,
            )
    except Exception:
        # Keep UI resilient
        pass
    
def set_hb_text(text: str) -> None:
    """
    Update heartbeat label via header module if present, else fallback to legacy label.
    Safe no-op if neither path exists.
    """
    try:
        # Prefer header moduleâ€™s own method if available
        # (Some builds mount a global _hb in panes; refresh_core still uses globals())
        hb = globals().get("_hb")
        if hb is not None and hasattr(hb, "set_heartbeat"):
            hb.set_heartbeat(text)
            return
    except Exception:
        pass
    # Fallback to legacy in-place update
    try:
        import dearpygui.dearpygui as dpg
        if dpg.does_item_exist("hb_label"):
            dpg.set_value("hb_label", text)
    except Exception:
        pass

