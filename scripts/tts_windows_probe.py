from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import CFG  # noqa: E402
from tools.tts import TTSConfig, TTSError, _KokoroEngine, _KokoroTorchEngine  # noqa: E402
from tools.tts import _load_kokoro_torch_model_class, _patch_platform_for_windows_torch_import  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="Short Quinn probe for Piper.")
    parser.add_argument("--voice", default="af_bella")
    parser.add_argument("--speed", type=float, default=1.05)
    parser.add_argument("--engine", choices=["onnx", "torch"], default="torch")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = TTSConfig(
        enabled=True,
        backend="kokoro",
        model_path=ROOT / "models" / "kokoro" / "kokoro-v1.0.onnx",
        voices_path=ROOT / "models" / "kokoro" / "voices-v1.0.bin",
        voice=args.voice,
        speed=args.speed,
    )
    engine = _KokoroTorchEngine(cfg) if args.engine == "torch" else _KokoroEngine(cfg)
    report: dict[str, object] = {
        "backend": args.engine,
        "voice": args.voice,
        "speed": args.speed,
        "model_path": str(cfg.model_path),
        "voices_path": str(cfg.voices_path),
    }

    try:
        print("PROBE_START", flush=True)
        print("IMPORT_NUMPY_START", flush=True)
        t_numpy = time.perf_counter()
        import numpy as np  # noqa: PLC0415
        report["numpy_import_s"] = round(time.perf_counter() - t_numpy, 3)
        print(f"IMPORT_NUMPY_DONE {report['numpy_import_s']}", flush=True)

        if args.engine == "onnx":
            print("IMPORT_ORT_START", flush=True)
            t_ort = time.perf_counter()
            os.environ.setdefault("ONNX_PROVIDER", "CPUExecutionProvider")
            import onnxruntime as rt  # noqa: PLC0415
            report["ort_import_s"] = round(time.perf_counter() - t_ort, 3)
            print(f"IMPORT_ORT_DONE {report['ort_import_s']}", flush=True)

            print("IMPORT_KOKORO_START", flush=True)
            t_kokoro = time.perf_counter()
            from kokoro_onnx import Kokoro  # noqa: PLC0415
            report["kokoro_import_s"] = round(time.perf_counter() - t_kokoro, 3)
            report["onnxruntime_version"] = getattr(rt, "__version__", "")
            report["kokoro_class"] = getattr(Kokoro, "__name__", "")
            print(f"IMPORT_KOKORO_DONE {report['kokoro_import_s']}", flush=True)

            print("SESSION_START", flush=True)
            t_session = time.perf_counter()
            session = rt.InferenceSession(
                str(cfg.model_path),
                providers=["CPUExecutionProvider"],
            )
            report["session_s"] = round(time.perf_counter() - t_session, 3)
            report["providers"] = list(session.get_providers())
            print(f"SESSION_DONE {report['session_s']}", flush=True)

            print("VOICES_START", flush=True)
            t_voices = time.perf_counter()
            voices = np.load(str(cfg.voices_path))
            report["voices_s"] = round(time.perf_counter() - t_voices, 3)
            report["voice_count"] = len(list(voices.keys()))
            print(f"VOICES_DONE {report['voices_s']}", flush=True)

            print("FROM_SESSION_START", flush=True)
            t_from_session = time.perf_counter()
            Kokoro.from_session(session, str(cfg.voices_path))
            report["from_session_s"] = round(time.perf_counter() - t_from_session, 3)
            print(f"FROM_SESSION_DONE {report['from_session_s']}", flush=True)
        else:
            print("IMPORT_TORCH_START", flush=True)
            t_torch = time.perf_counter()
            _patch_platform_for_windows_torch_import()
            import torch  # noqa: PLC0415
            report["torch_import_s"] = round(time.perf_counter() - t_torch, 3)
            report["torch_version"] = getattr(torch, "__version__", "")
            print(f"IMPORT_TORCH_DONE {report['torch_import_s']}", flush=True)

            print("IMPORT_HF_START", flush=True)
            t_hf = time.perf_counter()
            from huggingface_hub import hf_hub_download  # noqa: PLC0415
            report["hf_import_s"] = round(time.perf_counter() - t_hf, 3)
            report["hf_repo_id"] = str(getattr(CFG, "TTS_KOKORO_HF_REPO_ID", "hexgrad/Kokoro-82M"))
            print(f"IMPORT_HF_DONE {report['hf_import_s']}", flush=True)

            print("LOAD_KMODEL_CLASS_START", flush=True)
            t_class = time.perf_counter()
            KModel = _load_kokoro_torch_model_class()
            report["kmodel_class_s"] = round(time.perf_counter() - t_class, 3)
            report["kmodel_class"] = getattr(KModel, "__name__", "")
            print(f"LOAD_KMODEL_CLASS_DONE {report['kmodel_class_s']}", flush=True)

        print("ENGINE_LOAD_START", flush=True)
        t0 = time.perf_counter()
        engine._load()
        report["load_s"] = round(time.perf_counter() - t0, 3)
        report["disabled_reason_after_load"] = getattr(engine, "_disabled_reason", "")
        print(f"LOAD_DONE {report['load_s']}", flush=True)

        if args.engine == "onnx":
            kokoro = engine._kokoro
            sess = getattr(kokoro, "sess", None)
            if sess is not None:
                try:
                    report["providers"] = list(sess.get_providers())
                except Exception as exc:  # pragma: no cover - probe only
                    report["providers_error"] = repr(exc)
                try:
                    report["inputs"] = [
                        {"name": i.name, "type": i.type, "shape": list(i.shape)}
                        for i in sess.get_inputs()
                    ]
                    report["outputs"] = [
                        {"name": o.name, "type": o.type, "shape": list(o.shape)}
                        for o in sess.get_outputs()
                    ]
                except Exception as exc:  # pragma: no cover - probe only
                    report["io_error"] = repr(exc)

            t1 = time.perf_counter()
            print("SYNTH_START", flush=True)
            samples, sample_rate = engine.synthesize(args.text, voice=args.voice, speed=args.speed)
            report["synth_s"] = round(time.perf_counter() - t1, 3)
            report["sample_rate"] = int(sample_rate)
            report["sample_count"] = int(len(samples))
            report["ok"] = True
            print(f"SYNTH_DONE {report['synth_s']}", flush=True)
        else:
            t1 = time.perf_counter()
            print("WORKER_READY_START", flush=True)
            ready = engine._wait_until_ready(20.0)
            report["worker_ready_s"] = round(time.perf_counter() - t1, 3)
            report["worker_ready"] = bool(ready)
            if not ready:
                raise TTSError("Pure Kokoro worker did not become ready.")
            report["ok"] = True
            print(f"WORKER_READY_DONE {report['worker_ready_s']}", flush=True)
    except Exception as exc:
        report["ok"] = False
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        if isinstance(exc, TTSError):
            report["tts_error"] = True
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
