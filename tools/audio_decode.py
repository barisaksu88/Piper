"""tools/audio_decode.py

Local audio decoder for Web UI / WebView mic audio submission.

Decodes base64-encoded WebM/Opus or WAV audio into normalized float32
mono numpy arrays at 16 kHz, suitable for the STT pipeline.

Uses ffmpeg subprocess for container/codec support on Windows.
Requires no cloud speech APIs.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from typing import Literal

import numpy as np

_LOG = logging.getLogger(__name__)

# Format whitelist for security.
_SUPPORTED_FORMATS: set[str] = {"webm", "wav"}


def _find_ffmpeg() -> str | None:
    """Locate ffmpeg executable."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    return None


def _ffmpeg_normalize(input_path: str, output_path: str, ffmpeg_exe: str, timeout_s: float = 30.0) -> None:
    """Run ffmpeg to produce a 16 kHz mono WAV."""
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i", input_path,
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioDecodeError(
            "ffmpeg timed out while decoding Web UI mic audio"
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        raise AudioDecodeError(f"ffmpeg failed (code {result.returncode}): {stderr}")


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a WAV file into a float32 numpy array and return (audio, sample_rate)."""
    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 1:
        audio = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        audio = (audio - 128.0) / 128.0
    elif sample_width == 2:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise AudioDecodeError(f"Unsupported WAV sample width: {sample_width}")

    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)

    return audio, sample_rate


class AudioDecodeError(RuntimeError):
    """Raised when audio decoding fails."""
    pass


def decode_web_audio(
    base64_audio: str,
    format: Literal["webm", "wav"],
    *,
    max_decoded_bytes: int = 10 * 1024 * 1024,
    ffmpeg_timeout_s: float = 30.0,
) -> np.ndarray:
    """Decode a base64 audio payload into a float32 mono numpy array at 16 kHz.

    Args:
        base64_audio: Base64-encoded audio bytes (no data URI prefix).
        format: Container format — "webm" or "wav".
        max_decoded_bytes: Reject decoded audio larger than this many bytes.

    Returns:
        Normalized float32 mono audio at 16000 Hz.

    Raises:
        AudioDecodeError: On invalid base64, unsupported format, ffmpeg failure,
            or WAV read errors.
        ValueError: On empty/missing audio or unsupported format.
    """
    if not base64_audio or not isinstance(base64_audio, str):
        raise ValueError("Audio payload is missing or not a string")

    fmt = str(format or "").strip().lower().lstrip(".")
    if fmt not in _SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported audio format: {format!r}")

    try:
        raw_bytes = base64.b64decode(base64_audio, validate=True)
    except Exception as exc:
        raise AudioDecodeError(f"Invalid base64 audio: {exc}") from exc

    if len(raw_bytes) > max_decoded_bytes:
        raise AudioDecodeError(
            f"Decoded audio size {len(raw_bytes)} bytes exceeds limit {max_decoded_bytes}"
        )

    ffmpeg_exe = _find_ffmpeg()
    if ffmpeg_exe is None:
        raise AudioDecodeError("ffmpeg not found; Web UI mic audio cannot be decoded.")

    input_suffix = f".{fmt}"
    output_path = ""
    input_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=input_suffix, delete=False) as f_in:
            f_in.write(raw_bytes)
            input_path = f_in.name

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_out:
            output_path = f_out.name

        _ffmpeg_normalize(input_path, output_path, ffmpeg_exe, timeout_s=ffmpeg_timeout_s)
        audio, _sr = _read_wav(output_path)
        return audio.astype(np.float32)

    finally:
        for p in (input_path, output_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass
