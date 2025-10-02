from __future__ import annotations
"""Slim GUI entry (R2): coordinator only.
- Boot viewport + init_ui
- Start/stop tailer
- Heartbeat tick + header publish
- Buffer -> refresh_ui
No parsing/state/LLM logic here.
"""
import os
from pathlib import Path
from collections import deque
from datetime import datetime
import dearpygui.dearpygui as dpg

# UI-only imports
from ui.panes import init_ui, refresh_ui
from ui.tailer import Tailer
from ui.helpers.header_utils import publish_header
from ui.helpers.refresh_chat import load_state_and_build_lines
# Config (UI-only)
LOG_PATH = os.environ.get("PIPER_CORE_LOG", r"C:\\Piper\\run\\core.log")
TAIL_FROM_START = os.environ.get("PIPER_UI_TAIL_FROM_START", "0") == "1"
POLL_INTERVAL_SEC = float(os.environ.get("PIPER_UI_POLL_SEC", "0.25"))

# Buffers (UI-only)
LOG_MAX_LINES = 1200
CHAT_MAX_LINES = 600
log_buffer: deque[str] = deque(maxlen=LOG_MAX_LINES)
chat_buffer: deque[str] = deque(maxlen=CHAT_MAX_LINES)
last_update_ts: datetime | None = None

def _on_line(line: str) -> None:
    """Append raw lines to log; avoid parsing/classification per R2."""
    global last_update_ts
    s = (line or "").rstrip("\r\n")
    if not s:
        return
    log_buffer.append(s)
    last_update_ts = datetime.now()


def _hb_text() -> str:
    if last_update_ts is None:
        return "last change = 0 seconds ago"
    try:
        secs = max(0, int((datetime.now() - last_update_ts).total_seconds()))
        return f"last change = {secs} seconds ago"
    except Exception:
        return "last change = 0 seconds ago"

def _schedule_ticks() -> None:
    frames_per_tick = max(1, int(POLL_INTERVAL_SEC * 60))

    def _tick():
        try:
            try:
                publish_header(status=_hb_text(), heartbeat=True)
            except Exception:
                pass
            try:
                lines = load_state_and_build_lines()
                chat_text = "\n".join(lines)
                log_text = "\n".join(log_buffer)
                refresh_ui("", _hb_text(), chat_text, log_text, True, True)
            except Exception:
                pass
        finally:
            try:
                dpg.set_frame_callback(dpg.get_frame_count() + frames_per_tick, _tick)
            except Exception:
                pass

    try:
        dpg.set_frame_callback(dpg.get_frame_count() + 1, _tick)
    except Exception:
        pass

def run() -> None:
    print("[GUI] Starting Piper GUI (LL-R03).")
    p = Path(LOG_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("[GUI] Tailing created. Start CLI to feed logs.\n", encoding="utf-8")

    dpg.create_context()
    try:
        try:
            from ui.theme_utils import apply_theme_if_enabled as _apply_theme
            _apply_theme()
        except Exception:
            pass

        init_ui(str(p))
        # Initial repaint from canonical state (no need to wait for first tick)
        try:
            _chat = "\n".join(load_state_and_build_lines())
            _log = "\n".join(log_buffer)
            refresh_ui("", _hb_text(), _chat, _log, True, True)
        except Exception:
            pass
        tailer = Tailer(Path(LOG_PATH), from_start=TAIL_FROM_START, poll_interval=POLL_INTERVAL_SEC)
        tailer.start_in_thread(on_line=_on_line)

        _schedule_ticks()

        from ui.dpg_app import run as dpg_run
        dpg_run(drive_event_loop=True)
    finally:
        pass

def main():
    run()

if __name__ == "__main__":
    main()