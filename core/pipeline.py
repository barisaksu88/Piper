"""core/chat_pipeline.py

Chat pipeline:
- Handles streaming
- Cleans Tool Tags for TTS/Display (UI Safety)
- Handles Stage Directions
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from config import CFG
from tools.registry import get_registered_tool_names
from tools.tts import log_tts_error


# -----------------
# Stage-direction processing
# -----------------

_VOCAL_SFX = {
    "sigh": "sigh.wav", "sighs": "sigh.wav",
    "laugh": "laugh.wav", "laughs": "laugh.wav", "haha": "laugh.wav",
    "giggle": "giggle.wav", "giggles": "giggle.wav",
    "chuckle": "chuckle.wav", "chuckles": "chuckle.wav",
    "gasp": "gasp.wav", "gasps": "gasp.wav",
    "groan": "groan.wav", "groans": "groan.wav",
    "cough": "cough.wav", "coughs": "cough.wav",
    "sniff": "sniff.wav", "sniffs": "sniff.wav",
    "yawn": "yawn.wav", "yawns": "yawn.wav",
}

_SEMANTIC_TAGS = [
    ("reminds you", "Reminder: "), ("reminding you", "Reminder: "),
    ("sternly", "Listen. "), ("harshly", "Listen. "), ("firmly", "Listen. "),
    ("softly", "Quietly. "), ("quietly", "Quietly. "), ("whispers", "Quietly. "), ("whispering", "Quietly. "),
    ("deadpan", "Deadpan. "), ("serious", "Seriously. "),
]

_GESTURE_HINTS = [
    "smirks", "smirk", "rolls eyes", "nods", "shrugs", "leans in", "leans closer", 
    "tilts head", "raises an eyebrow", "arches an eyebrow",
]

@dataclass
class StageAction:
    sfx: Optional[str] = None
    say: str = ""

@dataclass
class TTSOutput:
    text: str = ""
    sfx_file: Optional[str] = None

def _default_sfx_dir() -> Path:
    return Path(getattr(CFG, "DATA_DIR", Path.cwd() / "data")) / "sfx"

def _stage_to_actions(stage_text: str) -> List[StageAction]:
    raw = (stage_text or "").strip()
    if not raw: return []
    low = raw.lower()
    actions: List[StageAction] = []
    for k, wav in _VOCAL_SFX.items():
        if k in low: actions.append(StageAction(sfx=wav))
    for needle, tag in _SEMANTIC_TAGS:
        if needle in low: actions.append(StageAction(say=tag))
    if any(h in low for h in _GESTURE_HINTS): actions.append(StageAction(say="… "))
    if not actions: actions.append(StageAction(say="… "))
    return actions

class StageDirectionProcessor:
    def __init__(self, sfx_dir: Optional[Path] = None):
        self.sfx_dir = Path(sfx_dir) if sfx_dir else _default_sfx_dir()
        self._in_stage = False
        self._stage_buf = ""

    def reset(self) -> None:
        self._in_stage = False
        self._stage_buf = ""

    def process_delta(self, delta: str) -> List[TTSOutput]:
        if not delta: return []
        outputs: List[TTSOutput] = []
        text_buf = ""
        for ch in delta:
            if ch != "*":
                if self._in_stage: self._stage_buf += ch
                else: text_buf += ch
                continue
            if not self._in_stage:
                self._in_stage = True
                self._stage_buf = ""
                if text_buf:
                    outputs.append(TTSOutput(text=text_buf))
                    text_buf = ""
            else:
                self._in_stage = False
                inner = self._stage_buf
                self._stage_buf = ""
                actions = _stage_to_actions(inner)
                for act in actions:
                    if act.sfx: outputs.append(TTSOutput(sfx_file=act.sfx))
                    if act.say: text_buf += act.say
        if text_buf: outputs.append(TTSOutput(text=text_buf))
        return outputs


# -----------------
# TAG SCRUBBER (UI Safety)
# -----------------

class TagScrubber:
    """Filters all Tool Tags from TTS/Display."""

    def __init__(self):
        self._buf = ""
        self._known_tags = tuple(get_registered_tool_names(include_legacy=True))

    def reset(self):
        self._buf = ""

    def flush(self) -> str:
        ret = self._buf
        self._buf = ""
        return self._scrub(ret)

    def process_delta(self, delta: str) -> str:
        self._buf += delta
        out = ""
        
        i = 0
        while i < len(self._buf):
            if self._buf[i] == '[':
                # Check for block tags (RUN_CODE)
                is_block = False
                # We only filter RUN_CODE now
                if self._buf[i:].upper().startswith("[RUN_CODE]"):
                    close_tag = "[/RUN_CODE]"
                    end_idx = self._buf.upper().find(close_tag, i)
                    if end_idx != -1:
                        i = end_idx + len(close_tag)
                        is_block = True
                    else:
                        self._buf = self._buf[i:]
                        return out
                
                if is_block: continue

                close_idx = self._buf.find(']', i)
                
                if close_idx != -1:
                    potential_tag = self._buf[i : close_idx+1]
                    if self._is_command(potential_tag):
                        i = close_idx + 1
                        continue
                    else:
                        out += '['
                        i += 1
                        continue
                else:
                    self._buf = self._buf[i:]
                    return out
            
            out += self._buf[i]
            i += 1

        self._buf = ""
        return out

    def _is_command(self, text: str) -> bool:
        text_u = text.upper().replace("/", "")
        for tag in self._known_tags:
            if text_u.startswith(f"[{tag}"):
                return True
        return False
    
    def _scrub(self, text: str) -> str:
        # Only scrub RUN_CODE now
        text = re.sub(r'\[RUN_CODE\].*?\[\/RUN_CODE\]', '', text, flags=re.DOTALL|re.IGNORECASE)
        return text.strip()


# -----------------
# Chat pipeline
# -----------------

class ChatPipeline:
    def __init__(
        self,
        *,
        tts,
        chat_append_fn: Callable[[str, str], None],
        chat_upsert_fn: Callable[[str, str], None],
        persist_turn_fn: Callable[[str, str], None],
        set_status_fn: Callable[[str], None],
        finalize_stream_fn: Optional[Callable[[], None]] = None,
    ):
        self.tts = tts
        self.chat_append = chat_append_fn
        self.chat_upsert = chat_upsert_fn
        self.persist_turn = persist_turn_fn
        self.set_status = set_status_fn
        self.finalize_stream = finalize_stream_fn

        self._stream_buffer: str = ""
        self._clean_stream_buffer: str = ""
        self._stream_active: bool = False
        self._tts_started: bool = False
        self._tts_voice: Optional[str] = None
        self._tts_speed: Optional[float] = None
        self._stage = StageDirectionProcessor()
        self._tag_scrubber = TagScrubber()
        self._stream_started_at: float | None = None
        self._tts_started_at: float | None = None
        self._completed_stream_metrics: List[dict[str, float | str]] = []

    def consume_completed_stream_metrics(self) -> List[dict[str, float | str]]:
        metrics = list(self._completed_stream_metrics)
        self._completed_stream_metrics.clear()
        return metrics

    def _finalize_stream_metrics(self, ended_kind: str) -> None:
        if self._stream_started_at is None:
            self._tts_started_at = None
            return
        ended_at = time.perf_counter()
        stream_ms = round(max(0.0, ended_at - self._stream_started_at) * 1000.0, 3)
        tts_ms = 0.0
        if self._tts_started_at is not None:
            tts_ms = round(max(0.0, ended_at - self._tts_started_at) * 1000.0, 3)
        self._completed_stream_metrics.append(
            {
                "ended_kind": str(ended_kind or ""),
                "stream_ms": stream_ms,
                "tts_ms": tts_ms,
            }
        )
        self._stream_started_at = None
        self._tts_started_at = None

    @staticmethod
    def _clean_numbers_for_tts(text: str) -> str:
        """Prepares numbers for TTS: removes commas, fixes decimals."""
        # 1. Remove ALL commas between digits (Fixes 12,000,000)
        text = re.sub(r"(?<=\d),(?=\d)", "", text)
        
        # 2. Replace decimal points with " point " (Fixes 7.6)
        text = re.sub(r"(?<=\d)\.(?=\d)", " point ", text)
        
        return text

    def handle_event(
        self,
        kind: str,
        payload: str,
        *,
        tts_voice: Optional[str] = None,
        tts_speed: Optional[float] = None,
    ) -> None:
        def _log_stream_tts_error(action: str, exc: Exception) -> None:
            log_tts_error(f"PIPELINE {action} ERROR: {exc}")

        if kind == "start":
            self._stream_buffer = ""
            self._clean_stream_buffer = ""
            self._stream_active = True
            self._tts_started = False
            self._tts_voice = tts_voice
            self._tts_speed = tts_speed
            self._stage.reset()
            self._tag_scrubber.reset()
            self._stream_started_at = time.perf_counter()
            self._tts_started_at = None
            self.set_status("Generating…")
            # tts.stream_start() is deferred to the first delta so a long
            # <think>…</think> preamble does not leave the TTS connection stale.
            return

        if kind == "delta":
            if not self._stream_active:
                if CFG.DEBUG_STREAMING_PIPELINE:
                    print(f"[STREAM] delta DROPPED (_stream_active=False): {payload!r:.40}", flush=True)
                return

            # Lazy TTS start: open the TTS stream on the first real delta so
            # the connection is fresh even after a long thinking phase.
            if not self._tts_started:
                self._tts_started = True
                self._tts_started_at = time.perf_counter()
                try:
                    self.tts.stream_start(voice=self._tts_voice, speed=self._tts_speed)
                except Exception as exc:
                    _log_stream_tts_error("stream_start", exc)

            self._stream_buffer += payload
            clean_payload = self._tag_scrubber.process_delta(payload)
            self._clean_stream_buffer += clean_payload
            self.chat_upsert(self._clean_stream_buffer)

            outputs = self._stage.process_delta(clean_payload)
            for out in outputs:
                if out.sfx_file:
                    try:
                        self.tts.stream_flush()
                    except Exception as exc:
                        _log_stream_tts_error("stream_flush", exc)
                    wav_path = self._stage.sfx_dir / out.sfx_file
                    self.tts.play_wav(wav_path)
                if out.text:
                    try:
                        self.tts.stream_push(out.text)
                    except Exception as exc:
                        _log_stream_tts_error("stream_push", exc)
            return

        if kind == "end":
            if not self._stream_active: return
            self._stream_active = False

            leftover = self._tag_scrubber.flush()
            if leftover:
                self._clean_stream_buffer += leftover
                if self._tts_started:
                    try:
                        self.tts.stream_push(leftover)
                    except Exception as exc:
                        _log_stream_tts_error("stream_push_tail", exc)

            final_text = self._clean_stream_buffer.strip()
            if self._tts_started:
                try:
                    self.tts.stream_end()
                except Exception as exc:
                    _log_stream_tts_error("stream_end", exc)
            self._tts_started = False
            self._finalize_stream_metrics("end")

            if final_text:
                self.chat_upsert(final_text)
                try: self.persist_turn("assistant", final_text)
                except Exception: pass
            if self.finalize_stream is not None:
                try:
                    self.finalize_stream()
                except Exception:
                    pass

            self.set_status("Ready")
            return

        if kind == "error":
            self._stream_active = False
            self.set_status("Error")
            self.chat_append("system", payload)
            if self._tts_started:
                try:
                    self.tts.stream_end()
                except Exception as exc:
                    _log_stream_tts_error("stream_end_error", exc)
            self._tts_started = False
            self._finalize_stream_metrics("error")
            return

        if kind == "cancel":
            if not self._stream_active:
                self.set_status(str(payload or "Canceled"))
                return

            self._stream_active = False
            self._stream_buffer = ""
            self._tag_scrubber.reset()
            self._stage.reset()
            if self._tts_started:
                try:
                    self.tts.stop()
                except Exception as exc:
                    _log_stream_tts_error("stop", exc)
            self._tts_started = False
            self._finalize_stream_metrics("cancel")

            partial_text = self._clean_stream_buffer.strip()
            if partial_text:
                self.chat_upsert(partial_text)

            self.set_status(str(payload or "Canceled"))
            return
