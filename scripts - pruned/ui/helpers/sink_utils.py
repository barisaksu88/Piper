# scripts/ui/helpers/sink_utils.py
from __future__ import annotations
import dearpygui.dearpygui as dpg
from typing import Iterable, Optional

def first_existing_tag(candidates: Iterable[str]) -> Optional[str]:
    for t in candidates:
        try:
            if dpg.does_item_exist(t):
                return t
        except Exception:
            pass
    return None

def ensure_sink(text_tag: str, scroll_tag: str) -> str:
    """
    Ensure a text item exists. If text_tag is missing but scroll exists,
    create the text under the scroll. Return the resolved text tag to use.
    """
    try:
        if dpg.does_item_exist(text_tag):
            return text_tag
        if dpg.does_item_exist(scroll_tag):
            dpg.add_text("", tag=text_tag, parent=scroll_tag)
            return text_tag
    except Exception:
        pass
    # last resort: return original name even if it doesn't exist (callers are defensive)
    return text_tag

