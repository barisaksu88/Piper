"""core/tts.py

Kokoro ONNX TTS wrapper.

Design goals:
- Offline, local TTS
- Lazy-load model on first use
- Non-blocking speak()
- stop() support
- Overlap synthesis and playback (pipeline)
- Sequential SFX playback (Strict Ordering)
"""

from __future__ import annotations

import threading
import queue
import time
import re
import wave
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Union

import numpy as np

from config import CFG

# =============================================================
# TWEAK THIS: How loud should SFX be compared to normal?
# 1.0 = Original Volume
# 2.0 = Double Volume
# 3.0 = Triple Volume
# =============================================================
SFX_VOLUME_BOOST = 3.5


@dataclass
class TTSConfig:
    enabled: bool = True
    model_path: Optional[Path] = None
    voices_path: Optional[Path] = None
    lang: str = "en-us"
    voice: str = "af_sarah"
    speed: float = 1.0
    sample_rate: int = 24000


class TTSError(RuntimeError):
    pass


def log_tts_error(msg: str) -> None:
    try:
        CFG.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        with open(str(CFG.TTS_DEBUG_PATH), "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


class _KokoroEngine:
    """Thin wrapper over kokoro-onnx with lazy imports."""

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self._kokoro = None
        self._sd = None
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return

            if not self.cfg.model_path or not self.cfg.voices_path:
                raise TTSError(
                    "TTS model_path/voices_path not configured."
                )

            if not self.cfg.model_path.exists():
                raise TTSError(f"Kokoro model not found: {self.cfg.model_path}")
            if not self.cfg.voices_path.exists():
                raise TTSError(f"Kokoro voices not found: {self.cfg.voices_path}")

            try:
                from kokoro_onnx import Kokoro  # type: ignore
            except Exception as e:
                raise TTSError("Failed to import kokoro_onnx.") from e

            try:
                import sounddevice as sd  # type: ignore
            except Exception as e:
                raise TTSError("Missing audio dep sounddevice.") from e

            self._sd = sd
            self._kokoro = Kokoro(str(self.cfg.model_path), str(self.cfg.voices_path))
            self._loaded = True

    def stop(self) -> None:
        if self._sd is None:
            return
        try:
            self._sd.stop()
        except Exception:
            pass

    def list_voices(self) -> List[str]:
        self._load()
        for attr in ("get_voices", "voices", "voice_names", "available_voices"):
            v = getattr(self._kokoro, attr, None)
            try:
                if callable(v):
                    out = v()
                    if isinstance(out, (list, tuple)):
                        return list(out)
                if isinstance(v, (list, tuple)):
                    return list(v)
            except Exception:
                continue
        return []

    def synthesize(self, text: str, voice: Optional[str], speed: Optional[float]):
        self._load()
        assert self._kokoro is not None

        v = (voice or self.cfg.voice).strip() if (voice or self.cfg.voice) else self.cfg.voice
        spd = float(speed if speed is not None else self.cfg.speed)

        if hasattr(self._kokoro, "create"):
            samples, sr = self._kokoro.create(text, voice=v, speed=spd, lang=self.cfg.lang)
            return samples, int(sr)

        if hasattr(self._kokoro, "tts"):
            out = self._kokoro.tts(text, voice=v, speed=spd)
            if isinstance(out, tuple) and len(out) == 2:
                return out[0], int(out[1])
            return out, int(self.cfg.sample_rate)

        raise TTSError("Unsupported kokoro-onnx API.")

    def play(self, samples, sr: int) -> None:
        self._load()
        assert self._sd is not None

        try:
            self._sd.stop()
        except Exception:
            pass

        self._sd.play(samples, sr)
        self._sd.wait()


class _StreamChunker:
    """Implements: 
    - First chunk: Fast start (~20 chars).
    - Remaining chunks: Normal flow (~100 chars) to preserve emotion.
    - Treats newlines as sentence endings (pauses).
    """

    # Added \n to detect line breaks as pauses
    _SENT_END_RE = re.compile(r"(?:(?<!\d)[.!?]|\n)")

    def __init__(self, first_min_chars: int = 20, next_min_chars: int = 100, max_chars: int = 300):
        self.first_min_chars = int(first_min_chars)
        self.next_min_chars = int(next_min_chars)
        self.max_chars = int(max_chars)
        
        self.buf = ""
        self.emitted = 0
        self._first_chunk_sent = False

    def reset(self) -> None:
        self.buf = ""
        self.emitted = 0
        self._first_chunk_sent = False

    def push(self, delta: str) -> List[str]:
        if not delta:
            return []
        self.buf += delta
        return self._emit_ready(intermediate=True)

    def flush(self) -> List[str]:
        """Force emit everything remaining immediately."""
        out = []
        rem = self.buf[self.emitted :].strip()
        if rem:
            out.append(rem)
        self.reset()
        return out

    def end(self) -> List[str]:
        out = self._emit_ready(intermediate=False)
        rem = self.buf[self.emitted :].strip()
        if rem:
            out.append(rem)
        self.reset()
        return out

    def _emit_ready(self, *, intermediate: bool) -> List[str]:
        out: List[str] = []

        while True:
            remaining = self.buf[self.emitted :]
            
            # SAFETY VALVE: Force split if huge
            if len(remaining) >= self.max_chars:
                cut_pos = remaining.rfind(' ', 0, self.max_chars)
                if cut_pos == -1:
                    cut_pos = self.max_chars
                
                chunk = self.buf[self.emitted : self.emitted + cut_pos].strip()
                self.emitted += cut_pos
                if chunk:
                    out.append(chunk)
                    self._first_chunk_sent = True # Ensure we switch mode if we force split
                continue

            # Determine threshold
            current_min_chars = self.first_min_chars
            if self._first_chunk_sent:
                current_min_chars = self.next_min_chars

            # --- NEW: NEWLINE HANDLING ---
            # If we have a minimum amount of text AND a newline appears, treat it as a hard stop.
            # This fixes reading lists: "1. Apple\n" will trigger immediately.
            if '\n' in remaining and len(remaining) >= self.first_min_chars:
                # Find the first newline in the current remaining buffer
                nl_pos = remaining.find('\n')
                # Cut the chunk there
                chunk = remaining[:nl_pos].strip()
                if chunk:
                    out.append(chunk)
                    self.emitted += nl_pos + 1 # +1 to skip the newline itself
                    self._first_chunk_sent = True
                    continue # Loop again to process the next part
            # ------------------------------

            # Normal logic
            if len(remaining) < current_min_chars:
                break

            search_from = self.emitted + current_min_chars
            m = self._SENT_END_RE.search(self.buf, pos=search_from)
            
            if not m:
                if intermediate:
                    break
                cut = len(self.buf)
            else:
                cut = m.end()

            chunk = self.buf[self.emitted : cut].strip()
            self.emitted = cut
            if chunk:
                out.append(chunk)
                self._first_chunk_sent = True # Switch to relaxed mode

        return out
    
class TTS:
    """Background-threaded TTS service with overlapped synth/play."""

    _SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

    def __init__(self, cfg: Optional[TTSConfig] = None):
        self.cfg = cfg or TTSConfig()
        self.engine = _KokoroEngine(self.cfg)

        # The Job Queue: handles both text synthesis and SFX loading in order
        # Item: (epoch, type, payload)
        # type: "text" -> payload: (text, voice, speed)
        # type: "sfx"  -> payload: path_str
        self._job_q: "queue.Queue[Tuple[int, str, object]]" = queue.Queue()
        self._audio_q: "queue.Queue[Tuple[int, object, int]]" = queue.Queue()

        self._stop_evt = threading.Event()
        self._started = False
        self._state_lock = threading.Lock()
        self._synth_active = False
        self._play_active = False

        self._epoch_lock = threading.Lock()
        self._epoch = 0

        self._synth_thread = threading.Thread(target=self._synth_loop, daemon=True)
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)

        # Streaming state
        self._stream_lock = threading.Lock()
        self._stream_epoch: Optional[int] = None
        self._stream_voice: Optional[str] = None
        self._stream_speed: Optional[float] = None
        self._stream_chunker = _StreamChunker(first_min_chars=20, next_min_chars=300)
        self._warm_lock = threading.Lock()
        self._warmed = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._synth_thread.start()
        self._play_thread.start()

    def shutdown(self) -> None:
        self._stop_evt.set()
        self.stop()

    def warm_up(
        self,
        *,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        sample_text: str = "Piper warm up.",
    ) -> None:
        if not self.cfg.enabled:
            return
        if self._warmed:
            return
        with self._warm_lock:
            if self._warmed:
                return
            if not self._started:
                self.start()
            self.engine.synthesize(sample_text, voice=voice, speed=speed)
            self._warmed = True

    def _bump_epoch(self) -> int:
        with self._epoch_lock:
            self._epoch += 1
            return self._epoch

    def _get_epoch(self) -> int:
        with self._epoch_lock:
            return self._epoch

    def _set_synth_active(self, active: bool) -> None:
        with self._state_lock:
            self._synth_active = bool(active)

    def _set_play_active(self, active: bool) -> None:
        with self._state_lock:
            self._play_active = bool(active)

    def is_busy(self) -> bool:
        with self._stream_lock:
            stream_active = self._stream_epoch is not None
        with self._state_lock:
            synth_active = self._synth_active
            play_active = self._play_active
        return (
            stream_active
            or synth_active
            or play_active
            or not self._job_q.empty()
            or not self._audio_q.empty()
        )

    def stop(self) -> None:
        self._bump_epoch()

        with self._stream_lock:
            self._stream_epoch = None
            self._stream_voice = None
            self._stream_speed = None
            self._stream_chunker.reset()
        self._set_synth_active(False)
        self._set_play_active(False)

        try:
            while True:
                self._job_q.get_nowait()
        except queue.Empty:
            pass

        try:
            while True:
                self._audio_q.get_nowait()
        except queue.Empty:
            pass

        try:
            self.engine.stop()
        except Exception:
            pass

    def list_voices(self) -> List[str]:
        return self.engine.list_voices()

    def speak(self, text: str, *, voice: Optional[str] = None, speed: Optional[float] = None) -> None:
        """Non-streaming: enqueue a small+medium+rest split for overlap."""
        if not self.cfg.enabled:
            return
        if not text or not text.strip():
            return

        if not self._started:
            self.start()

        text = text.strip()
        if len(text) > 12000:
            text = text[:12000] + "…"

        chunks = self._split_3stage(text, first_target_chars=100, second_target_chars=500)
        epoch = self._get_epoch()
        for ch in chunks:
            if ch:
                self._queue_text_job(epoch, ch, voice, speed)

    # -----------------
    # Streaming interface
    # -----------------

    def stream_start(self, *, voice: Optional[str] = None, speed: Optional[float] = None) -> None:
        if not self.cfg.enabled:
            return
        if not self._started:
            self.start()

        with self._stream_lock:
            self._stream_chunker.reset()
            self._stream_epoch = self._get_epoch()
            self._stream_voice = voice
            self._stream_speed = speed

    def stream_push(self, delta: str) -> None:
        if not self.cfg.enabled:
            return
        if not delta:
            return

        with self._stream_lock:
            if self._stream_epoch is None:
                self.stream_start()

            epoch = self._stream_epoch
            voice = self._stream_voice
            speed = self._stream_speed

            if epoch is None or epoch != self._get_epoch():
                return

            ready = self._stream_chunker.push(delta)

        if epoch is None:
            return
        for ch in ready:
            if ch and epoch == self._get_epoch():
                self._queue_text_job(epoch, ch, voice, speed)

    def stream_flush(self) -> None:
        """Force the buffer to emit text immediately. Used before SFX."""
        if not self.cfg.enabled:
            return
        
        with self._stream_lock:
            epoch = self._stream_epoch
            voice = self._stream_voice
            speed = self._stream_speed
            if epoch is None: return
            
            chunks = self._stream_chunker.flush()
            
        for ch in chunks:
            if ch and epoch == self._get_epoch():
                self._queue_text_job(epoch, ch, voice, speed)

    def stream_end(self) -> None:
        if not self.cfg.enabled:
            return

        with self._stream_lock:
            epoch = self._stream_epoch
            voice = self._stream_voice
            speed = self._stream_speed
            self._stream_epoch = None
            self._stream_voice = None
            self._stream_speed = None

            if epoch is None or epoch != self._get_epoch():
                self._stream_chunker.reset()
                return

            chunks = self._stream_chunker.end()

        for ch in chunks:
            if ch and epoch == self._get_epoch():
                self._queue_text_job(epoch, ch, voice, speed)

    def _queue_text_job(
        self,
        epoch: int,
        text: str,
        voice: Optional[str],
        speed: Optional[float],
    ) -> None:
        clean_text = self._clean_tts_text(text).strip()
        if not clean_text:
            return
        self._job_q.put((epoch, "text", (clean_text, voice, speed)))

    # -----------------
    # NEW: Sequential SFX support
    # -----------------

    def play_wav(self, path: Path) -> None:
        """Queue a WAV file in the SAME queue as text to ensure order."""
        if not self.cfg.enabled:
            return
        
        if not path.exists():
            return

        epoch = self._get_epoch()
        # Put in job queue so it waits for previous text to finish synth
        self._job_q.put((epoch, "sfx", str(path)))

    # -----------------

    def _synth_loop(self) -> None:
        """Processes jobs in order: either synthesizing text or loading WAVs."""
        while not self._stop_evt.is_set():
            try:
                epoch, job_type, payload = self._job_q.get(timeout=0.1)
            except queue.Empty:
                continue

            if epoch != self._get_epoch():
                continue

            self._set_synth_active(True)
            try:
                # Handle Text Job
                if job_type == "text":
                    text, voice, speed = payload
                    try:
                        samples, sr = self.engine.synthesize(text, voice=voice, speed=speed)
                    except Exception as e:
                        log_tts_error(f"TTS SYNTH ERROR: {e}")
                        continue

                    if epoch != self._get_epoch():
                        continue

                    # Put audio in play queue
                    while not self._stop_evt.is_set():
                        if epoch != self._get_epoch():
                            break
                        try:
                            self._audio_q.put((epoch, samples, sr), timeout=0.1)
                            break
                        except queue.Full:
                            continue

                # Handle SFX Job
                elif job_type == "sfx":
                    path_str = payload
                    try:
                        with wave.open(path_str, 'rb') as wf:
                            sr = wf.getframerate()
                            n_channels = wf.getnchannels()
                            raw_data = wf.readframes(wf.getnframes())

                            if wf.getsampwidth() == 2:
                                audio = np.frombuffer(raw_data, dtype=np.int16)
                                samples = audio.astype(np.float32) / 32767.0
                            elif wf.getsampwidth() == 4:
                                samples = np.frombuffer(raw_data, dtype=np.float32)
                            else:
                                continue

                            if n_channels == 2:
                                samples = samples.reshape(-1, 2).mean(axis=1)

                            # APPLY GAIN
                            samples = samples * SFX_VOLUME_BOOST

                            # CLIP (Prevent distortion if too loud)
                            samples = np.clip(samples, -1.0, 1.0)

                            # Put audio in play queue
                            if epoch == self._get_epoch():
                                self._audio_q.put((epoch, samples, sr))

                    except Exception as e:
                        log_tts_error(f"WAV LOAD ERROR: {path_str} - {e}")
            finally:
                self._set_synth_active(False)

    def _play_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                epoch, samples, sr = self._audio_q.get(timeout=0.1)
            except queue.Empty:
                continue

            if epoch != self._get_epoch():
                continue

            self._set_play_active(True)
            try:
                self.engine.play(samples, sr)
            except Exception as e:
                log_tts_error(f"TTS PLAY ERROR: {e}")
                time.sleep(0.05)
            finally:
                self._set_play_active(False)

    @staticmethod
    def _clean_tts_text(text: str) -> str:
        """Final polish before synthesis: Fix numbers and units."""
        
        # 1. Fix Decimals: 7.6 -> 7 point 6
        text = re.sub(r"(\d)\.(\d)", r"\1 point \2", text)
        
        # 2. Remove Commas: 12,000 -> 12000
        text = re.sub(r"(?<=\d),(?=\d)", "", text)
        
        # 3. Fix Temperature Units
        # Handle specific C/F cases first for better flow
        text = re.sub(r"(\d)\s*°C\b", r"\1 degrees Celsius", text, flags=re.IGNORECASE)
        text = re.sub(r"(\d)\s*°F\b", r"\1 degrees Fahrenheit", text, flags=re.IGNORECASE)
        
        # Any remaining degree symbols become "degrees"
        text = text.replace("°", " degrees ")
        
        return text

    def _split_sentences(self, text: str, max_chunk_chars: int = 260) -> List[str]:
        parts = self._SPLIT_RE.split(text)
        out: List[str] = []
        buf = ""

        for p in parts:
            p = p.strip()
            if not p:
                continue

            if not buf:
                buf = p
                continue

            if len(buf) + 1 + len(p) <= max_chunk_chars:
                buf = f"{buf} {p}"
            else:
                out.append(buf)
                buf = p

        if buf:
            out.append(buf)

        return out

    def _split_3stage(
        self,
        text: str,
        first_target_chars: int = 20,
        second_target_chars: int = 100,
        max_sentence_chars: int = 260,
    ) -> List[str]:
        sents = self._split_sentences(text, max_chunk_chars=max_sentence_chars)
        if not sents:
            return []

        def pack_until(min_chars: int, start_idx: int) -> Tuple[str, int]:
            buf: List[str] = []
            total = 0
            i = start_idx
            while i < len(sents):
                s = sents[i].strip()
                i += 1
                if not s:
                    continue
                add = len(s) + (1 if buf else 0)
                buf.append(s)
                total += add
                if total >= min_chars:
                    break
            return (" ".join(buf).strip(), i)

        c1, i = pack_until(first_target_chars, 0)
        if i >= len(sents):
            return [c1]

        c2, j = pack_until(second_target_chars, i)
        if j >= len(sents):
            return [c1, c2]

        c3 = " ".join(sents[j:]).strip()
        if not c3:
            return [c1, c2]

        return [c1, c2, c3]


_tts_singleton: Optional[TTS] = None


def get_tts(cfg: Optional[TTSConfig] = None) -> TTS:
    global _tts_singleton
    if _tts_singleton is None:
        _tts_singleton = TTS(cfg)
    return _tts_singleton
