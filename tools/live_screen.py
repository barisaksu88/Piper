from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from config import CFG
from tools.screen_capture import (
    CAPTURE_MODE_DISPLAY,
    CAPTURE_MODE_POINTER,
    ScreenCaptureError,
    capture_screen_view_to_path,
)


CaptureCallback = Callable[[Path], None]
ErrorCallback = Callable[[str], None]


@dataclass(frozen=True)
class LiveScreenState:
    enabled: bool
    image_path: Path
    focus_image_path: Path
    mode: str
    interval_s: float
    last_capture_ts: float = 0.0
    last_error: str = ""


class LiveScreenSession:
    def __init__(
        self,
        data_dir: Path,
        *,
        interval_s: float | None = None,
        max_stale_s: float | None = None,
        filename: str | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self._interval_s = float(interval_s if interval_s is not None else getattr(CFG, "LIVE_SCREEN_INTERVAL_S", 10.0))
        self._max_stale_override = max_stale_s
        self.image_path = self.data_dir / "workspace" / "images" / str(
            filename or getattr(CFG, "LIVE_SCREEN_FILENAME", "live_screen.jpg")
        )
        self.focus_image_path = self.data_dir / "workspace" / "images" / str(
            getattr(CFG, "LIVE_SCREEN_FOCUS_FILENAME", "live_focus.jpg")
        )
        self._mode = str(getattr(CFG, "LIVE_SCREEN_SOURCE_MODE", CAPTURE_MODE_DISPLAY) or CAPTURE_MODE_DISPLAY).strip().lower()
        self._lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self._enabled = False
        self._last_capture_ts = 0.0
        self._last_error = ""
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._on_capture: Optional[CaptureCallback] = None
        self._on_error: Optional[ErrorCallback] = None

    def state(self) -> LiveScreenState:
        with self._lock:
            return LiveScreenState(
                enabled=self._enabled,
                image_path=self.image_path,
                focus_image_path=self.focus_image_path,
                mode=self._mode,
                interval_s=self._interval_s,
                last_capture_ts=self._last_capture_ts,
                last_error=self._last_error,
            )

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def mode(self) -> str:
        with self._lock:
            return self._mode

    def interval_s(self) -> float:
        with self._lock:
            return self._interval_s

    def max_stale_s(self) -> float:
        with self._lock:
            return self._effective_max_stale(self._interval_s)

    def _effective_max_stale(self, interval_s: float) -> float:
        if self._max_stale_override is not None:
            return float(self._max_stale_override)
        return float(getattr(CFG, "LIVE_SCREEN_MAX_STALE_S", max(interval_s * 3.0, 20.0)))

    def set_interval(self, interval_s: float) -> None:
        with self._lock:
            self._interval_s = max(float(interval_s), 0.5)

    def set_mode(self, mode: str) -> None:
        normalized = str(mode or CAPTURE_MODE_DISPLAY).strip().lower() or CAPTURE_MODE_DISPLAY
        with self._lock:
            self._mode = normalized

    def start(
        self,
        *,
        on_capture: CaptureCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> Path:
        with self._lock:
            self._enabled = True
            self._last_error = ""
            self._on_capture = on_capture
            self._on_error = on_error
            self._stop_evt = threading.Event()
        path = self.capture_once()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()
        return path

    def stop(self) -> None:
        thread: Optional[threading.Thread] = None
        with self._lock:
            self._enabled = False
            self._stop_evt.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)

    def capture_once(self) -> Path:
        with self._lock:
            mode = self._mode
        return self._capture_to_path(self.image_path, mode=mode, notify=True)

    def capture_focus_image(self) -> Path:
        return self._capture_to_path(self.focus_image_path, mode=CAPTURE_MODE_POINTER, notify=False)

    def _capture_to_path(self, output_path: Path, *, mode: str, notify: bool) -> Path:
        with self._capture_lock:
            path = capture_screen_view_to_path(output_path, mode=mode)
        callback: Optional[CaptureCallback] = None
        with self._lock:
            if notify:
                self._last_capture_ts = time.time()
                self._last_error = ""
                callback = self._on_capture
        if notify and callback is not None:
            try:
                callback(path)
            except Exception:
                pass
        return path

    def current_image_path(self, *, require_fresh: bool = True) -> Optional[Path]:
        with self._lock:
            enabled = self._enabled
            last_capture_ts = self._last_capture_ts
            max_stale_s = self._effective_max_stale(self._interval_s)
        if require_fresh:
            if not enabled:
                return None
            if not last_capture_ts or (time.time() - last_capture_ts) > max_stale_s:
                return None
        if self.image_path.exists() and self.image_path.is_file():
            return self.image_path
        return None

    def _loop(self) -> None:
        while True:
            with self._lock:
                stop_evt = self._stop_evt
                enabled = self._enabled
                error_callback = self._on_error
                interval_s = self._interval_s
            if not enabled or stop_evt.is_set():
                return
            if stop_evt.wait(interval_s):
                return
            try:
                self.capture_once()
            except ScreenCaptureError as exc:
                with self._lock:
                    self._last_error = str(exc)
                if error_callback is not None:
                    try:
                        error_callback(str(exc))
                    except Exception:
                        pass
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                if error_callback is not None:
                    try:
                        error_callback(str(exc))
                    except Exception:
                        pass
