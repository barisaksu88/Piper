from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import CFG  # noqa: E402
from tools.tts import _load_kokoro_torch_model_class, _patch_platform_for_windows_torch_import  # noqa: E402


def _configure_worker_logging() -> None:
    try:
        from loguru import logger  # noqa: PLC0415

        logger.remove()
        logger.add(sys.stderr, level="WARNING")
    except Exception:
        pass


def _kokoro_lang_code(lang: str) -> str:
    lowered = str(lang or "").strip().lower()
    if lowered.startswith("en-gb") or lowered.startswith("en_uk"):
        return "b"
    return "a"


def _resolve_espeak_cli() -> str:
    candidates = [
        str(Path.cwd() / "espeak-ng.exe"),
        str(Path(r"C:\Program Files\eSpeak NG\espeak-ng.exe")),
        str(Path(r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe")),
        shutil.which("espeak-ng") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("espeak-ng.exe not found")


def _local_torch_dir() -> Path:
    base = getattr(CFG, "KOKORO_DIR", ROOT / "models" / "kokoro")
    subdir = str(getattr(CFG, "KOKORO_TORCH_SUBDIR", "torch") or "torch").strip() or "torch"
    return Path(base) / subdir


def _load_model():
    _patch_platform_for_windows_torch_import()
    import torch  # noqa: PLC0415
    from kokoro import KPipeline  # noqa: PLC0415

    KModel = _load_kokoro_torch_model_class()
    try:
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except Exception:
        pass
    torch_dir = _local_torch_dir()
    config_path = torch_dir / str(getattr(CFG, "KOKORO_TORCH_CONFIG", "config.json"))
    model_path = torch_dir / str(getattr(CFG, "KOKORO_TORCH_MODEL", "kokoro-v1_0.pth"))
    if not config_path.exists() or not model_path.exists():
        raise RuntimeError(f"Local Kokoro torch assets missing: {config_path} / {model_path}")
    model = KModel(
        repo_id=str(getattr(CFG, "TTS_KOKORO_HF_REPO_ID", "hexgrad/Kokoro-82M")),
        config=str(config_path),
        model=str(model_path),
    ).to("cpu").eval()
    pipeline = KPipeline(
        lang_code=_kokoro_lang_code(str(getattr(CFG, "TTS_LANG", "en-us"))),
        repo_id=str(getattr(CFG, "TTS_KOKORO_HF_REPO_ID", "hexgrad/Kokoro-82M")),
        model=model,
    )
    return torch, model, pipeline, torch_dir


def _write_wav(path: Path, samples, sample_rate: int) -> None:
    import numpy as np  # noqa: PLC0415

    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())


def _pause_samples_for_text(text: str, sample_rate: int) -> int:
    raw = str(text or "")
    stripped = raw.rstrip()
    if not stripped:
        return 0
    if "\n\n" in raw:
        seconds = 0.38
    elif "\n" in raw:
        seconds = 0.28
    elif stripped.endswith(("?", "!")):
        seconds = 0.22
    elif stripped.endswith("."):
        seconds = 0.18
    elif stripped.endswith((";", ":")):
        seconds = 0.14
    elif stripped.endswith((",", "—")):
        seconds = 0.1
    else:
        seconds = 0.0
    return max(0, int(round(float(sample_rate) * seconds)))


def main() -> int:
    _configure_worker_logging()
    try:
        espeak_dir = str(Path(_resolve_espeak_cli()).parent)
        os.environ.setdefault("PATH", espeak_dir + os.pathsep + os.environ.get("PATH", ""))
    except Exception:
        pass
    try:
        torch, model, pipeline, torch_dir = _load_model()
    except Exception as exc:
        print(json.dumps({"type": "error", "error": f"load_failed: {exc}"}), flush=True)
        traceback.print_exc(file=sys.stderr)
        return 1

    voices: dict[str, object] = {}
    print(json.dumps({"type": "ready"}), flush=True)

    for raw in sys.stdin:
        raw = str(raw or "").strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            req_id = str(req.get("id") or "")
            text = str(req.get("text") or "").strip()
            voice = str(req.get("voice") or getattr(CFG, "TTS_VOICE", "af_heart")).strip() or "af_heart"
            speed = float(req.get("speed") or getattr(CFG, "TTS_SPEED", 0.85))
            wav_path = Path(str(req.get("wav_path") or (Path(tempfile.gettempdir()) / f"piper_kokoro_worker_{req_id}.wav")))
            if not text:
                raise RuntimeError("empty_text")
            pack = voices.get(voice)
            if pack is None:
                voice_path = torch_dir / "voices" / f"{voice}.pt"
                if not voice_path.exists():
                    raise RuntimeError(f"voice_missing: {voice_path}")
                pack = torch.load(str(voice_path), map_location="cpu", weights_only=True)
                voices[voice] = pack
            outputs = list(pipeline(text, voice=pack, speed=speed))
            audios = []
            sample_rate = int(getattr(CFG, "sample_rate", 24000) or 24000)
            for idx, result in enumerate(outputs):
                output = getattr(result, "output", None)
                audio = None if output is None else getattr(output, "audio", None)
                if audio is None:
                    continue
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                audios.append(audio)
                if idx < len(outputs) - 1:
                    import numpy as np  # noqa: PLC0415

                    gap = _pause_samples_for_text(getattr(result, "graphemes", ""), sample_rate)
                    if gap > 0:
                        audios.append(np.zeros(gap, dtype=np.float32))
            if not audios:
                raise RuntimeError("empty_audio")
            if len(audios) == 1:
                audio = audios[0]
            else:
                import numpy as np  # noqa: PLC0415

                audio = np.concatenate(audios)
            wav_path.parent.mkdir(parents=True, exist_ok=True)
            _write_wav(wav_path, audio, sample_rate)
            print(
                json.dumps(
                    {
                        "type": "result",
                        "id": req_id,
                        "ok": True,
                        "wav_path": str(wav_path),
                        "sample_rate": 24000,
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "type": "result",
                        "id": str(req.get("id") or "") if "req" in locals() else "",
                        "ok": False,
                        "error": str(exc),
                    }
                ),
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
