from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TTSProbeEvent:
    kind: str
    text: str = ""
    voice: Optional[str] = None
    speed: Optional[float] = None
    path: Optional[str] = None
    ts: float = 0.0


@dataclass(frozen=True)
class TTSUtterance:
    text: str
    voice: Optional[str]
    speed: Optional[float]
    sfx: List[str]


class RecordingTTS:
    """A no-audio TTS implementation for harness runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_voice: Optional[str] = None
        self._current_speed: Optional[float] = None
        self._chunks: List[str] = []
        self._sfx: List[str] = []
        self.events: List[TTSProbeEvent] = []
        self.utterances: List[TTSUtterance] = []

    def start(self) -> None:
        return

    def shutdown(self) -> None:
        self.stop()

    def stop(self) -> None:
        with self._lock:
            self._current_voice = None
            self._current_speed = None
            self._chunks = []
            self._sfx = []
            self.events.append(TTSProbeEvent(kind="stop", ts=time.time()))

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._current_voice or self._current_speed or self._chunks or self._sfx)

    def stream_start(self, *, voice: Optional[str] = None, speed: Optional[float] = None) -> None:
        with self._lock:
            self._current_voice = voice
            self._current_speed = speed
            self._chunks = []
            self._sfx = []
            self.events.append(
                TTSProbeEvent(kind="stream_start", voice=voice, speed=speed, ts=time.time())
            )

    def stream_push(self, delta: str) -> None:
        if not delta:
            return
        with self._lock:
            self._chunks.append(delta)
            self.events.append(
                TTSProbeEvent(
                    kind="stream_push",
                    text=delta,
                    voice=self._current_voice,
                    speed=self._current_speed,
                    ts=time.time(),
                )
            )

    def stream_flush(self) -> None:
        with self._lock:
            self.events.append(
                TTSProbeEvent(
                    kind="stream_flush",
                    voice=self._current_voice,
                    speed=self._current_speed,
                    ts=time.time(),
                )
            )

    def stream_end(self) -> None:
        with self._lock:
            text = "".join(self._chunks).strip()
            self.utterances.append(
                TTSUtterance(
                    text=text,
                    voice=self._current_voice,
                    speed=self._current_speed,
                    sfx=list(self._sfx),
                )
            )
            self.events.append(
                TTSProbeEvent(
                    kind="stream_end",
                    text=text,
                    voice=self._current_voice,
                    speed=self._current_speed,
                    ts=time.time(),
                )
            )
            self._current_voice = None
            self._current_speed = None
            self._chunks = []
            self._sfx = []

    def play_wav(self, path: Path) -> None:
        with self._lock:
            path_str = str(path)
            self._sfx.append(path_str)
            self.events.append(TTSProbeEvent(kind="play_wav", path=path_str, ts=time.time()))

    def list_voices(self) -> List[str]:
        return []

    def snapshot_events(self, start_index: int = 0) -> List[Dict[str, object]]:
        with self._lock:
            return [asdict(event) for event in self.events[start_index:]]

    def snapshot_utterances(self, start_index: int = 0) -> List[Dict[str, object]]:
        with self._lock:
            return [
                {
                    "text": utterance.text,
                    "voice": utterance.voice,
                    "speed": utterance.speed,
                    "sfx": list(utterance.sfx),
                }
                for utterance in self.utterances[start_index:]
            ]
