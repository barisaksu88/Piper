"""B04.16 init core â€” extracted from ui/panes.py (behavior-preserving)."""
from __future__ import annotations

def init_ui(log_path: str) -> None:
    
    """Root fixed & primary. NonÃ¢â‚¬â€˜scrolling headers. Dedicated scroll windows for content."""
    if dpg.does_item_exist("root"):
        dpg.delete_item("root")
    
    global _LOG_PATH
    _LOG_PATH = str(log_path or "")

