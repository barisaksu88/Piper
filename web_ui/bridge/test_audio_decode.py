"""web_ui.bridge.test_audio_decode

Deterministic tests for the Web UI audio decoder.

Mocks ffmpeg subprocess to avoid requiring a real ffmpeg installation.
Uses stdlib wave to produce deterministic test WAV data.
"""

from __future__ import annotations

import base64
import io
import struct
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tools.audio_decode import AudioDecodeError, decode_web_audio


def _make_test_wav_bytes(sample_rate: int = 16000, duration_s: float = 0.1) -> bytes:
    """Produce a minimal mono 16-bit WAV file in memory."""
    n_samples = int(sample_rate * duration_s)
    # Simple sine wave at 440 Hz.
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    audio = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


class TestDecodeWebAudio:
    @patch("tools.audio_decode._find_ffmpeg")
    @patch("tools.audio_decode.subprocess.run")
    def test_decode_webm_produces_float32_mono(self, mock_run, mock_find_ffmpeg):
        mock_find_ffmpeg.return_value = "ffmpeg"
        # ffmpeg writes a WAV file; simulate by making the mock write one.
        wav_bytes = _make_test_wav_bytes()

        def _fake_run(cmd, **kwargs):
            # cmd is: ffmpeg -y -i input.webm -ar 16000 -ac 1 -f wav output.wav
            output_path = cmd[-1]
            with open(output_path, "wb") as f:
                f.write(wav_bytes)
            return MagicMock(returncode=0, stderr=b"")

        mock_run.side_effect = _fake_run

        audio = decode_web_audio(base64.b64encode(b"dummy_webm").decode(), "webm")
        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32
        assert audio.ndim == 1
        assert audio.size > 0

    @patch("tools.audio_decode._find_ffmpeg")
    @patch("tools.audio_decode.subprocess.run")
    def test_decode_wav_produces_float32_mono(self, mock_run, mock_find_ffmpeg):
        mock_find_ffmpeg.return_value = "ffmpeg"
        wav_bytes = _make_test_wav_bytes()

        def _fake_run(cmd, **kwargs):
            output_path = cmd[-1]
            with open(output_path, "wb") as f:
                f.write(wav_bytes)
            return MagicMock(returncode=0, stderr=b"")

        mock_run.side_effect = _fake_run

        audio = decode_web_audio(base64.b64encode(b"dummy_wav").decode(), "wav")
        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32
        assert audio.ndim == 1

    def test_empty_audio_raises(self):
        with pytest.raises(ValueError, match="Audio payload is missing"):
            decode_web_audio("", "wav")

    def test_unsupported_format_raises(self):
        with pytest.raises(ValueError, match="Unsupported audio format"):
            decode_web_audio(base64.b64encode(b"x").decode(), "mp3")

    def test_invalid_base64_raises(self):
        with pytest.raises(AudioDecodeError, match="Invalid base64"):
            decode_web_audio("!!!not-valid-base64!!!", "wav")

    def test_oversized_payload_raises(self):
        big = base64.b64encode(b"x" * 1024 * 1024).decode()
        with pytest.raises(AudioDecodeError, match="exceeds limit"):
            decode_web_audio(big, "wav", max_decoded_bytes=100)

    @patch("tools.audio_decode._find_ffmpeg")
    def test_missing_ffmpeg_raises(self, mock_find_ffmpeg):
        mock_find_ffmpeg.return_value = None
        with pytest.raises(AudioDecodeError, match="ffmpeg not found"):
            decode_web_audio(base64.b64encode(b"x").decode(), "wav")

    @patch("tools.audio_decode._find_ffmpeg")
    @patch("tools.audio_decode.subprocess.run")
    def test_ffmpeg_failure_raises(self, mock_run, mock_find_ffmpeg):
        mock_find_ffmpeg.return_value = "ffmpeg"
        mock_run.return_value = MagicMock(returncode=1, stderr=b"codec not found")
        with pytest.raises(AudioDecodeError, match="ffmpeg failed"):
            decode_web_audio(base64.b64encode(b"x").decode(), "webm")
