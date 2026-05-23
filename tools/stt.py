"""core/stt.py

Speech-to-Text using Faster-Whisper.
"""

from __future__ import annotations

import logging
import os
import numpy as np

_sd = None
_sounddevice_error: Exception | None = None
_WhisperModel = None
_whisper_import_error: Exception | None = None
_LOG = logging.getLogger(__name__)


def _log_voice_debug(message: str) -> None:
    try:
        from config import CFG

        CFG.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CFG.DEBUG_DIR / "voice_identity_debug.txt", "a", encoding="utf-8") as f:
            f.write(str(message).rstrip() + "\n")
    except Exception:
        pass


def _voice_decision_debug_line(*, mode: str, active_user: str, decision) -> str:
    best_user = str(getattr(decision, "best_user", "") or "none")
    best_score = float(getattr(decision, "best_score", 0.0) or 0.0)
    second_score = float(getattr(decision, "second_score", 0.0) or 0.0)
    margin = float(getattr(decision, "margin", 0.0) or 0.0)
    best_is_admin = bool(getattr(decision, "best_is_admin", False))
    threshold = float(getattr(decision, "threshold", 0.0) or 0.0)
    margin_threshold = float(getattr(decision, "margin_threshold", 0.0) or 0.0)
    final_decision = str(getattr(decision, "decision", "") or "unknown")
    reason = str(getattr(decision, "reason", "") or "none")
    final_user = str(getattr(decision, "final_user", "") or "none")
    return (
        "match "
        f"mode={mode} active={active_user or 'unknown'} "
        f"best_user={best_user} best_score={best_score:.3f} "
        f"second_score={second_score:.3f} margin={margin:.3f} "
        f"best_is_admin={str(best_is_admin).lower()} "
        f"threshold={threshold:.3f} margin_threshold={margin_threshold:.3f} "
        f"final_decision={final_decision} final_user={final_user} reason={reason}"
    )


def _load_sounddevice():
    global _sd, _sounddevice_error
    if _sd is not None:
        return _sd
    if _sounddevice_error is not None:
        raise STTError("sounddevice not installed.") from _sounddevice_error
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        _sounddevice_error = exc
        raise STTError("sounddevice not installed.") from exc
    _sd = sd
    return sd


def _load_whisper_model_class():
    global _WhisperModel, _whisper_import_error
    if _WhisperModel is not None:
        return _WhisperModel
    if _whisper_import_error is not None:
        raise STTError("faster-whisper not installed.") from _whisper_import_error
    try:
        from faster_whisper import WhisperModel as model_cls  # type: ignore
    except Exception as exc:
        _whisper_import_error = exc
        raise STTError("faster-whisper not installed.") from exc
    _WhisperModel = model_cls
    return model_cls


class STTError(RuntimeError):
    pass


def _concatenate_audio_chunks(chunks: list[np.ndarray]) -> np.ndarray:
    """Concatenate a list of audio chunk arrays."""
    if not chunks:
        return np.array([], dtype=np.int16)
    return np.concatenate(chunks, axis=0)


def _rms_gate(audio_data: np.ndarray, min_rms: float) -> bool:
    """Return True if audio passes the RMS no-speech gate."""
    if audio_data.size == 0:
        return False
    rms = float(np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)))
    return rms >= min_rms


def _normalize_and_resample(audio_data: np.ndarray, source_sr: int) -> np.ndarray:
    """Convert int16 audio to float32 mono and resample to 16 kHz if needed."""
    audio_float = audio_data.astype(np.float32) / 32768.0
    audio_mono = audio_float.squeeze()
    if source_sr != 16000:
        source_idx = np.arange(audio_mono.shape[0], dtype=np.float32)
        target_len = max(1, int(round(audio_mono.shape[0] * 16000 / source_sr)))
        target_idx = np.linspace(0, audio_mono.shape[0] - 1, num=target_len, dtype=np.float32)
        audio_downsampled = np.interp(target_idx, source_idx, audio_mono).astype(np.float32)
    else:
        audio_downsampled = audio_mono.astype(np.float32)
    return audio_downsampled


def _transcribe_audio_array(model, audio_float32_16k: np.ndarray) -> str:
    """Run Faster-Whisper transcription on a normalized audio array."""
    segments, _info = model.transcribe(
        audio_float32_16k,
        beam_size=5,
        language="en",
        condition_on_previous_text=False,
    )
    return "".join([seg.text for seg in segments]).strip()


def _run_voice_identity_hook(
    engine: "STTEngine",
    audio_samples: np.ndarray,
) -> None:
    """Run voice recognition / enrollment on the given audio samples.

    Sets ``engine._last_voice_match`` and ``engine._last_audio_samples``.
    """
    try:
        from core.voice_recognition import get_voice_engine
        from config import CFG

        engine._last_audio_samples = audio_samples

        if not CFG.VOICE_RECOGNITION_ENABLED:
            return

        voice_engine = get_voice_engine()
        if not voice_engine.available():
            return

        embedding = voice_engine.extract_embedding(audio_samples)
        if embedding is None:
            return

        current_user_id = str(getattr(engine, "_active_voice_user_id", "") or "").strip()
        current_is_unknown = bool(getattr(engine, "_active_voice_user_unknown", True))

        if current_user_id and not current_is_unknown and voice_engine.is_enrolling(current_user_id):
            completed = voice_engine.add_enrollment_sample(current_user_id, embedding)
            _log_voice_debug(f"enrollment_sample user={current_user_id} completed={completed}")
        else:
            if hasattr(voice_engine, "evaluate_match"):
                try:
                    decision = voice_engine.evaluate_match(embedding, first_turn=current_is_unknown)
                except TypeError:
                    decision = voice_engine.evaluate_match(embedding)
                mode = "unknown_eval" if current_is_unknown else "strict_eval"
                _log_voice_debug(
                    _voice_decision_debug_line(
                        mode=mode,
                        active_user=current_user_id or "unknown",
                        decision=decision,
                    )
                )
                engine._last_voice_match = (
                    getattr(decision, "final_user", "") or None,
                    float(getattr(decision, "best_score", 0.0) or 0.0),
                    {
                        "best_user": str(getattr(decision, "best_user", "") or ""),
                        "best_score": float(getattr(decision, "best_score", 0.0) or 0.0),
                        "second_score": float(getattr(decision, "second_score", 0.0) or 0.0),
                        "margin": float(getattr(decision, "margin", 0.0) or 0.0),
                        "best_is_admin": bool(getattr(decision, "best_is_admin", False)),
                        "threshold": float(getattr(decision, "threshold", 0.0) or 0.0),
                        "margin_threshold": float(getattr(decision, "margin_threshold", 0.0) or 0.0),
                        "final_decision": str(getattr(decision, "decision", "") or ""),
                        "reason": str(getattr(decision, "reason", "") or ""),
                    },
                )
            else:
                matched_user, similarity = voice_engine.match(embedding)
                _log_voice_debug(
                    "match "
                    f"mode=legacy active={current_user_id or 'unknown'} "
                    f"best_user={matched_user or 'none'} best_score={float(similarity or 0.0):.3f} "
                    "second_score=0.000 margin=0.000 best_is_admin=false "
                    "threshold=0.000 margin_threshold=0.000 "
                    f"final_decision={'accepted_legacy' if matched_user else 'unknown'} "
                    f"final_user={matched_user or 'none'} reason=legacy_engine"
                )
                engine._last_voice_match = (matched_user, similarity)
    except Exception as exc:
        _log_voice_debug(f"error {type(exc).__name__}: {exc}")


class STTEngine:
    def __init__(self):
        self.model = None
        self._recording = False
        self._audio_data = []
        self._stream = None
        self._min_rms = float(os.environ.get("PIPER_STT_MIN_RMS", "50"))
        self._sample_rate = 16000
        self._last_voice_match = None
        self._active_voice_user_id = ""
        self._active_voice_user_unknown = True

    def _load_model(self):
        if self.model:
            return
        whisper_model_cls = _load_whisper_model_class()

        _LOG.info("[STT] Loading Model...")
        self.model = whisper_model_cls("base", device="cpu", compute_type="float32")

    def start_recording(self):
        sd = _load_sounddevice()

        try:
            devices = sd.query_devices()
        except Exception as e:
            raise STTError(f"Unable to query audio devices: {e}") from e

        input_devices = [
            (i, dev) for i, dev in enumerate(devices)
            if dev['max_input_channels'] > 0
        ]
        if not input_devices:
            raise STTError("No input audio devices available.")

        input_device_index = None
        selected_device = None

        for i, dev in input_devices:
            name_lower = dev['name'].lower()
            if 'mikrofon' in name_lower and 'stereo' not in name_lower and 'kar' not in name_lower:
                input_device_index = i
                selected_device = dev
                break

        if input_device_index is None:
            try:
                default_input = sd.default.device[0]
            except Exception:
                default_input = None

            if isinstance(default_input, int) and default_input >= 0:
                dev = devices[default_input]
                if dev['max_input_channels'] > 0:
                    input_device_index = default_input
                    selected_device = dev

        if input_device_index is None:
            input_device_index, selected_device = input_devices[0]

        self._audio_data = []
        self._recording = True
        self._last_voice_match = None

        def _callback(indata, frames, time, status):
            if self._recording:
                self._audio_data.append(indata.copy())

        target_sr = int(selected_device.get('default_samplerate') or 16000)

        try:
            self._stream = sd.InputStream(
                samplerate=target_sr,
                device=input_device_index,
                channels=1,
                dtype='int16',
                callback=_callback
            )
            self._stream.start()
            self._sample_rate = target_sr
            _LOG.info(
                "[STT] Recording from device %s: %s",
                input_device_index,
                selected_device["name"],
            )
        except Exception as e:
            self._recording = False
            self._stream = None
            raise STTError(f"Unable to start microphone stream: {e}") from e

    def stop_recording(self) -> str:
        self._recording = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._audio_data:
            return ""

        audio_data = _concatenate_audio_chunks(self._audio_data)
        if not _rms_gate(audio_data, self._min_rms):
            return ""

        try:
            self._load_model()
            audio_downsampled = _normalize_and_resample(audio_data, int(self._sample_rate or 16000))
            text = _transcribe_audio_array(self.model, audio_downsampled)
            _run_voice_identity_hook(self, audio_downsampled)
            return text
        except Exception as e:
            _LOG.warning("[STT] Error: %s", e)
            return ""

    def transcribe_buffer(self, audio_data: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe a pre-recorded audio buffer from the Web UI / WebView mic path.

        Args:
            audio_data: Audio samples as a numpy array (any numeric dtype).
            sample_rate: Sample rate of the input audio (default 16000).

        Returns:
            Transcribed text, or empty string if no speech detected or an error occurs.

        Side effects:
            Sets ``self._last_voice_match`` and ``self._last_audio_samples``
            for downstream voice identity consumption.
        """
        if audio_data is None or audio_data.size == 0:
            return ""

        # Convert to float32 mono at the target sample rate.
        audio_float = np.asarray(audio_data, dtype=np.float32)
        audio_mono = audio_float.squeeze()
        if audio_mono.ndim > 1:
            audio_mono = audio_mono.mean(axis=1)

        source_sr = int(sample_rate or 16000)
        if source_sr != 16000:
            source_idx = np.arange(audio_mono.shape[0], dtype=np.float32)
            target_len = max(1, int(round(audio_mono.shape[0] * 16000 / source_sr)))
            target_idx = np.linspace(0, audio_mono.shape[0] - 1, num=target_len, dtype=np.float32)
            audio_downsampled = np.interp(target_idx, source_idx, audio_mono).astype(np.float32)
        else:
            audio_downsampled = audio_mono.astype(np.float32)

        # RMS gate using the same threshold as native stop_recording.
        # Compute RMS on the normalized float32 array (scale back to int16-equivalent for comparison).
        int16_equiv = (audio_downsampled * 32768.0).astype(np.float32)
        rms = float(np.sqrt(np.mean(int16_equiv ** 2)))
        if rms < self._min_rms:
            return ""

        try:
            self._load_model()
            text = _transcribe_audio_array(self.model, audio_downsampled)
            _run_voice_identity_hook(self, audio_downsampled)
            return text
        except Exception as e:
            _LOG.warning("[STT] transcribe_buffer error: %s", e)
            return ""

    def set_active_voice_profile(self, user_id: str, *, is_unknown: bool = False) -> None:
        self._active_voice_user_id = str(user_id or "").strip()
        self._active_voice_user_unknown = bool(is_unknown)

    def consume_last_voice_match(self):
        result = self._last_voice_match
        self._last_voice_match = None
        return result

_engine = None

def get_stt_engine() -> STTEngine:
    global _engine
    if _engine is None:
        _engine = STTEngine()
    return _engine
