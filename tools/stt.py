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

class STTEngine:
    def __init__(self):
        self.model = None
        self._recording = False
        self._audio_data = []
        self._stream = None
        self._min_rms = float(os.environ.get("PIPER_STT_MIN_RMS", "50"))
        self._sample_rate = 16000
        
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

        audio_data = np.concatenate(self._audio_data, axis=0)
        rms = float(np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)))
        if rms < self._min_rms:
            return ""
            
        try:
            self._load_model()

            audio_float = audio_data.astype(np.float32) / 32768.0
            audio_mono = audio_float.squeeze()
            source_sr = int(self._sample_rate or 16000)
            if source_sr != 16000:
                source_idx = np.arange(audio_mono.shape[0], dtype=np.float32)
                target_len = max(1, int(round(audio_mono.shape[0] * 16000 / source_sr)))
                target_idx = np.linspace(0, audio_mono.shape[0] - 1, num=target_len, dtype=np.float32)
                audio_downsampled = np.interp(target_idx, source_idx, audio_mono).astype(np.float32)
            else:
                audio_downsampled = audio_mono.astype(np.float32)
            
            segments, info = self.model.transcribe(
                audio_downsampled, 
                beam_size=5,
                language="en",
                condition_on_previous_text=False
            )
            
            return "".join([seg.text for seg in segments]).strip()
            
        except Exception as e:
            _LOG.warning("[STT] Error: %s", e)
            return ""

_engine = None

def get_stt_engine() -> STTEngine:
    global _engine
    if _engine is None:
        _engine = STTEngine()
    return _engine
