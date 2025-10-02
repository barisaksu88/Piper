"""
B04.3 Tag utilities â€” extracted from ui/panes.py
No new literals; pure helpers.
"""
import dearpygui.dearpygui as dpg

def first_existing_tag(candidates):
    """
    Return the first DearPyGui tag that exists from a list of candidates.
    """
    try:
        for t in candidates or []:
            try:
                if dpg.does_item_exist(t):
                    return t
            except Exception:
                pass
    except Exception:
        pass
    return None

def shorten_path(p: str, max_chars: int = 72) -> str:
    """
    Compact a long path/string into head...tail with a max length.
    Safe and defensive; returns best-effort string on errors.
    """
    try:
        if p is None:
            return ""
        s = str(p)
        if len(s) <= max_chars:
            return s
        # leave room for the ellipsis
        keep = max(0, max_chars - 3)
        head = max(10, keep // 2)
        tail = max(0, keep - head)
        return s[:head] + "..." + (s[-tail:] if tail > 0 else "")
    except Exception:
        # last-resort truncation
        s = str(p)
        return s[:max_chars]

