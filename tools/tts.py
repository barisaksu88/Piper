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

import base64
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import queue
import time
import types
import re
import traceback
import wave
import struct
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Union

try:
    import numpy as np
except ImportError:
    np = None

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
    backend: str = "auto"
    lang: str = "en-us"
    voice: str = "af_heart"
    speed: float = 0.85
    sample_rate: int = 24000


class TTSError(RuntimeError):
    pass


_TTS_LOG_LOCK = threading.Lock()


def log_tts_error(msg: str) -> None:
    try:
        CFG.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        with _TTS_LOG_LOCK:
            with open(str(CFG.TTS_DEBUG_PATH), "a", encoding="utf-8") as f:
                f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def _load_kokoro_torch_model_class():
    existing = sys.modules.get("kokoro.model")
    if existing is not None and hasattr(existing, "KModel"):
        return existing.KModel

    package_root = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages" / "kokoro"
    if not package_root.exists():
        raise TTSError(f"Pure Kokoro package not found: {package_root}")

    package = sys.modules.get("kokoro")
    if package is None:
        package = types.ModuleType("kokoro")
        package.__path__ = [str(package_root)]  # type: ignore[attr-defined]
        package.__package__ = "kokoro"
        sys.modules["kokoro"] = package

    def _load(name: str, rel_path: str):
        existing_mod = sys.modules.get(name)
        if existing_mod is not None:
            return existing_mod
        path = package_root / rel_path
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:
            raise TTSError(f"Could not load Kokoro module spec: {name}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("kokoro.custom_stft", "custom_stft.py")
    _load("kokoro.istftnet", "istftnet.py")
    _load("kokoro.modules", "modules.py")
    model_mod = _load("kokoro.model", "model.py")
    return model_mod.KModel


def _patch_platform_for_windows_torch_import() -> None:
    """Avoid Python's WMI-backed platform probes during torch import on Windows.

    On this machine, torch import can hang inside platform.{system,machine}(),
    which route through stdlib WMI queries. The Kokoro torch worker only needs
    a stable Windows/x64 identity, so we provide that directly before import.
    """

    if os.name != "nt":
        return
    try:
        import platform
        from collections import namedtuple

        uname_result = namedtuple(
            "uname_result",
            "system node release version machine processor",
        )
        machine = str(
            os.environ.get("PROCESSOR_ARCHITECTURE")
            or os.environ.get("PROCESSOR_ARCHITEW6432")
            or "AMD64"
        ).strip() or "AMD64"
        processor = str(os.environ.get("PROCESSOR_IDENTIFIER") or machine).strip() or machine
        node = str(os.environ.get("COMPUTERNAME") or "").strip()
        fake = uname_result("Windows", node, "", "", machine, processor)
        platform.uname = lambda: fake
        platform.system = lambda: fake.system
        platform.machine = lambda: fake.machine
        platform.processor = lambda: fake.processor
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
        self._disabled_reason: str = ""
        self._voice_fallback_engine = _KokoroTorchEngine(cfg) if os.name == "nt" else None
        self._fallback_engine = _WindowsSystemSpeechEngine(cfg) if _WindowsSystemSpeechEngine.is_available() else None
        self._espeak_cli_path: str | None = None

    def _warm_fallbacks(self) -> None:
        if self._voice_fallback_engine is not None:
            try:
                completed, _ = self._run_with_timeout(
                    self._voice_fallback_engine.warm_up,
                    self._windows_voice_fallback_timeout_s(background=True),
                )
                if completed:
                    return
                log_tts_error("KOKORO TORCH WARMUP TIMEOUT")
            except Exception as exc:
                log_tts_error(f"KOKORO TORCH WARMUP ERROR: {exc}")
        if self._fallback_engine is not None:
            self._fallback_engine.warm_up()

    def _speak_via_fallbacks(self, text: str, *, voice: Optional[str], speed: Optional[float]) -> None:
        if self._voice_fallback_engine is not None:
            try:
                completed, _ = self._run_with_timeout(
                    lambda: self._voice_fallback_engine.speak_text_blocking(text, voice=voice, speed=speed),
                    self._windows_voice_fallback_timeout_s(background=False),
                )
                if completed:
                    return
                log_tts_error("KOKORO TORCH FALLBACK TIMEOUT")
            except Exception as exc:
                log_tts_error(f"KOKORO TORCH FALLBACK ERROR: {exc}")
        if self._fallback_engine is not None:
            self._fallback_engine.speak_text_blocking(text, voice=voice, speed=speed)
            return
        raise TTSError(self._disabled_reason or "No TTS fallback engine available.")

    def _load(self) -> None:
        if self._disabled_reason:
            raise TTSError(self._disabled_reason)
        if self._loaded:
            return
        with self._load_lock:
            if self._disabled_reason:
                raise TTSError(self._disabled_reason)
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

            if os.name == "nt":
                os.environ.setdefault("ONNX_PROVIDER", "CPUExecutionProvider")

            try:
                from kokoro_onnx import Kokoro  # type: ignore
            except Exception as e:
                raise TTSError("Failed to import kokoro_onnx.") from e

            if os.name == "nt":
                try:
                    import onnxruntime as rt  # type: ignore

                    session_options = rt.SessionOptions()
                    try:
                        session_options.intra_op_num_threads = 1
                        session_options.inter_op_num_threads = 1
                        session_options.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
                        session_options.enable_cpu_mem_arena = False
                    except Exception:
                        pass
                    session = rt.InferenceSession(
                        str(self.cfg.model_path),
                        sess_options=session_options,
                        providers=["CPUExecutionProvider"],
                    )
                    if hasattr(Kokoro, "from_session"):
                        self._kokoro = Kokoro.from_session(session, str(self.cfg.voices_path))
                    else:
                        self._kokoro = Kokoro(str(self.cfg.model_path), str(self.cfg.voices_path))
                except Exception:
                    self._kokoro = Kokoro(str(self.cfg.model_path), str(self.cfg.voices_path))
            else:
                self._kokoro = Kokoro(str(self.cfg.model_path), str(self.cfg.voices_path))
            self._loaded = True

    def _resolve_espeak_cli(self) -> str | None:
        if self._espeak_cli_path is not None:
            return self._espeak_cli_path or None
        candidates = [
            os.environ.get("PIPER_ESPEAK_EXE", "").strip(),
            os.environ.get("ESPEAK_NG_EXE", "").strip(),
            shutil.which("espeak-ng") or "",
            r"C:\Program Files\eSpeak NG\espeak-ng.exe",
            r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe",
        ]
        for raw in candidates:
            candidate = str(raw or "").strip()
            if not candidate:
                continue
            try:
                if Path(candidate).exists():
                    self._espeak_cli_path = candidate
                    return candidate
            except Exception:
                continue
        self._espeak_cli_path = ""
        return None

    def _phonemize_windows_cli(self, text: str, lang: str) -> str:
        exe = self._resolve_espeak_cli()
        if not exe:
            raise TTSError("espeak-ng.exe not found for Windows Kokoro phonemization.")
        resolved_text = str(text or "").strip()
        if not resolved_text:
            return ""
        cmd = [exe, "-q", "--ipa=3", "-v", str(lang or "en-us"), resolved_text]
        try:
            out = subprocess.check_output(
                cmd,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=max(2.0, self._windows_kokoro_timeout_s()),
            )
        except Exception as exc:
            raise TTSError(f"Windows espeak-ng phonemization failed: {exc}") from exc
        # Remove zero-width joiners and collapse whitespace to keep Kokoro tokenization stable.
        cleaned = str(out or "").replace("\u200d", "").replace("\ufeff", "")
        return " ".join(cleaned.split()).strip()

    def stop(self) -> None:
        if self._voice_fallback_engine is not None:
            try:
                self._voice_fallback_engine.stop()
            except Exception:
                pass
        if self._fallback_engine is not None:
            try:
                self._fallback_engine.stop()
            except Exception:
                pass
        if self._sd is None:
            return
        try:
            self._sd.stop()
        except Exception:
            pass

    @staticmethod
    def _run_with_timeout(fn, timeout_s: float):
        done = threading.Event()
        box: dict[str, object] = {}

        def _worker() -> None:
            try:
                box["result"] = fn()
            except Exception as exc:
                box["error"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        done.wait(max(0.1, float(timeout_s)))
        if not done.is_set():
            return False, None
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return True, box.get("result")

    def _windows_kokoro_timeout_s(self) -> float:
        try:
            return float(getattr(CFG, "TTS_KOKORO_TIMEOUT_S", 8.0))
        except Exception:
            return 8.0

    def _windows_voice_fallback_timeout_s(self, *, background: bool) -> float:
        base = self._windows_kokoro_timeout_s()
        if background:
            return max(30.0, base * 4.0)
        return max(25.0, base * 3.0)

    def _disable_with_reason(self, reason: str) -> None:
        self._disabled_reason = str(reason or "Kokoro disabled").strip() or "Kokoro disabled"
        log_tts_error(f"KOKORO DISABLED: {self._disabled_reason}")

    def choose_reply_backend(self, *, voice: Optional[str], speed: Optional[float]) -> str:
        if os.name != "nt":
            return "onnx"
        if not self._disabled_reason:
            log_tts_error("KOKORO REPLY BACKEND: onnx")
            return "onnx"

        voice_fallback = self._voice_fallback_engine
        if voice_fallback is not None:
            try:
                voice_fallback.warm_up()
                wait_s = min(2.0, voice_fallback._dynamic_foreground_ready_wait_s())
                if voice_fallback._wait_until_ready(wait_s):
                    log_tts_error("KOKORO REPLY BACKEND: torch")
                    return "torch"
            except Exception as exc:
                log_tts_error(f"KOKORO REPLY BACKEND CHECK ERROR: {exc}")

        if self._fallback_engine is not None:
            log_tts_error("KOKORO REPLY BACKEND: system")
            return "system"
        return "default"

    def warm_up(self) -> None:
        if self._disabled_reason:
            self._warm_fallbacks()
            return
        try:
            self._load()
        except Exception as exc:
            self._disable_with_reason(f"Kokoro warm-up failed: {exc}")
            self._warm_fallbacks()

    def speak_text_blocking(self, text: str, *, voice: Optional[str], speed: Optional[float]) -> None:
        resolved_text = str(text or "").strip()
        if not resolved_text:
            return
        if self._disabled_reason:
            self._speak_via_fallbacks(resolved_text, voice=voice, speed=speed)
            return
        if os.name != "nt":
            samples, sr = self.synthesize(resolved_text, voice=voice, speed=speed)
            self.play(samples, sr)
            return

        def _synth():
            return self.synthesize(resolved_text, voice=voice, speed=speed)

        try:
            completed, result = self._run_with_timeout(_synth, self._windows_kokoro_timeout_s())
        except Exception as exc:
            log_tts_error(f"KOKORO SYNTH ERROR: {exc}")
            self._disable_with_reason(f"Kokoro synth error: {exc}")
            self._speak_via_fallbacks(resolved_text, voice=voice, speed=speed)
            return

        if not completed:
            self._disable_with_reason("Kokoro synth timed out on Windows")
            self._speak_via_fallbacks(resolved_text, voice=voice, speed=speed)
            return

        samples, sr = result
        self.play(samples, sr)

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
            try:
                samples, sr = self._kokoro.create(text, voice=v, speed=spd, lang=self.cfg.lang)
                return samples, int(sr)
            except Exception as exc:
                log_tts_error(f"KOKORO TEXT CREATE ERROR: {exc}")
                if os.name == "nt":
                    try:
                        phonemes = self._phonemize_windows_cli(text, self.cfg.lang)
                        if phonemes:
                            samples, sr = self._kokoro.create(
                                phonemes,
                                voice=v,
                                speed=spd,
                                lang=self.cfg.lang,
                                is_phonemes=True,
                            )
                            return samples, int(sr)
                    except Exception as fallback_exc:
                        log_tts_error(f"KOKORO CLI PHONEME FALLBACK ERROR: {fallback_exc}")

        if hasattr(self._kokoro, "tts"):
            out = self._kokoro.tts(text, voice=v, speed=spd)
            if isinstance(out, tuple) and len(out) == 2:
                return out[0], int(out[1])
            return out, int(self.cfg.sample_rate)

        raise TTSError("Unsupported kokoro-onnx API.")

    def play(self, samples, sr: int) -> None:
        if os.name == "nt":
            if np is None:
                raise TTSError("Missing audio dependency numpy.")
            try:
                import winsound  # type: ignore
            except Exception as exc:
                raise TTSError("winsound unavailable for Kokoro playback.") from exc

            arr = np.asarray(samples, dtype=np.float32)
            if arr.ndim > 1:
                arr = arr.mean(axis=1)
            arr = np.clip(arr, -1.0, 1.0)
            pcm = (arr * 32767.0).astype(np.int16)
            temp_path = Path(tempfile.gettempdir()) / f"piper_kokoro_{int(time.time() * 1000)}.wav"
            try:
                with wave.open(str(temp_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(int(sr))
                    wf.writeframes(pcm.tobytes())
                winsound.PlaySound(str(temp_path), winsound.SND_FILENAME)
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return

        self._load()

        if self._sd is None:
            try:
                import sounddevice as sd  # type: ignore
            except Exception as e:
                raise TTSError("Missing audio dep sounddevice.") from e
            self._sd = sd

        assert self._sd is not None
        try:
            self._sd.stop()
        except Exception:
            pass

        self._sd.play(samples, sr)
        self._sd.wait()


class _KokoroTorchEngine:
    """Windows-safe Kokoro fallback that uses PyTorch + HF weights instead of ONNX."""

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self._torch = None
        self._model = None
        self._hf_hub_download = None
        self._loaded = False
        self._load_lock = threading.Lock()
        self._async_lock = threading.Lock()
        self._load_done = threading.Event()
        self._load_error: Exception | None = None
        self._load_thread: threading.Thread | None = None
        self._load_started_at: float = 0.0
        self._last_still_loading_log_s: float = 0.0
        self._last_stack_dump_log_s: float = 0.0
        self._voices: dict[str, object] = {}
        self._espeak_cli_path: str | None = None
        self._worker_proc: subprocess.Popen[str] | None = None
        self._worker_stdout_thread: threading.Thread | None = None
        self._worker_stderr_thread: threading.Thread | None = None
        self._worker_events: "queue.Queue[dict[str, object]]" = queue.Queue()
        self._worker_io_lock = threading.Lock()
        self._worker_request_counter = 0
        self._worker_ready = False

    def _worker_script_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "scripts" / "kokoro_torch_worker.py"

    def _drain_worker_stdout(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = str(raw or "").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                self._worker_events.put(event)
            except Exception:
                log_tts_error(f"KOKORO TORCH WORKER STDOUT: {line}")

    def _drain_worker_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = str(raw or "").rstrip()
            if line:
                log_tts_error(f"KOKORO TORCH WORKER STDERR: {line}")

    def _start_worker_process(self) -> None:
        if os.name != "nt":
            return
        with self._async_lock:
            proc = self._worker_proc
            if proc is not None and proc.poll() is None:
                return
            script_path = self._worker_script_path()
            if not script_path.exists():
                raise TTSError(f"Kokoro worker script missing: {script_path}")
            self._worker_ready = False
            while not self._worker_events.empty():
                try:
                    self._worker_events.get_nowait()
                except queue.Empty:
                    break
            proc = subprocess.Popen(
                [str(Path(sys.executable)), "-u", str(script_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self._worker_proc = proc
            self._worker_stdout_thread = threading.Thread(target=self._drain_worker_stdout, args=(proc,), daemon=True)
            self._worker_stderr_thread = threading.Thread(target=self._drain_worker_stderr, args=(proc,), daemon=True)
            self._worker_stdout_thread.start()
            self._worker_stderr_thread.start()
            self._load_started_at = time.time()
            self._last_still_loading_log_s = 0.0
            self._last_stack_dump_log_s = 0.0
            log_tts_error("KOKORO TORCH WORKER PROCESS START")

    def _next_worker_id(self) -> str:
        self._worker_request_counter += 1
        return str(self._worker_request_counter)

    def _repo_id(self) -> str:
        return str(getattr(CFG, "TTS_KOKORO_HF_REPO_ID", "hexgrad/Kokoro-82M") or "hexgrad/Kokoro-82M").strip()

    def _local_torch_dir(self) -> Path:
        base = getattr(CFG, "KOKORO_DIR", Path(__file__).resolve().parents[1] / "models" / "kokoro")
        subdir = str(getattr(CFG, "KOKORO_TORCH_SUBDIR", "torch") or "torch").strip() or "torch"
        return Path(base) / subdir

    def _local_torch_model_path(self) -> Path:
        return self._local_torch_dir() / str(getattr(CFG, "KOKORO_TORCH_MODEL", "kokoro-v1_0.pth"))

    def _local_torch_config_path(self) -> Path:
        return self._local_torch_dir() / str(getattr(CFG, "KOKORO_TORCH_CONFIG", "config.json"))

    def _local_voice_path(self, voice: str) -> Path:
        return self._local_torch_dir() / "voices" / f"{voice}.pt"

    def _resolve_espeak_cli(self) -> str | None:
        if self._espeak_cli_path is not None:
            return self._espeak_cli_path or None
        candidates = [
            os.environ.get("PIPER_ESPEAK_EXE", "").strip(),
            os.environ.get("ESPEAK_NG_EXE", "").strip(),
            shutil.which("espeak-ng") or "",
            r"C:\Program Files\eSpeak NG\espeak-ng.exe",
            r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe",
        ]
        for raw in candidates:
            candidate = str(raw or "").strip()
            if not candidate:
                continue
            try:
                if Path(candidate).exists():
                    self._espeak_cli_path = candidate
                    return candidate
            except Exception:
                continue
        self._espeak_cli_path = ""
        return None

    def _phonemize_windows_cli(self, text: str, lang: str) -> str:
        exe = self._resolve_espeak_cli()
        if not exe:
            raise TTSError("espeak-ng.exe not found for Kokoro phonemization.")
        resolved_text = str(text or "").strip()
        if not resolved_text:
            return ""
        cmd = [exe, "-q", "--ipa=3", "-v", str(lang or "en-us"), resolved_text]
        try:
            out = subprocess.check_output(
                cmd,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=30,
            )
        except Exception as exc:
            raise TTSError(f"Windows espeak-ng phonemization failed: {exc}") from exc
        cleaned = str(out or "").replace("\u200d", "").replace("\ufeff", "")
        return " ".join(cleaned.split()).strip()

    def _load(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            try:
                _patch_platform_for_windows_torch_import()
                log_tts_error("KOKORO TORCH STEP: import torch")
                import torch  # type: ignore
                log_tts_error("KOKORO TORCH STEP: import huggingface_hub")
                from huggingface_hub import hf_hub_download  # type: ignore
            except Exception as exc:
                raise TTSError(f"Pure Kokoro dependencies unavailable: {exc}") from exc
            log_tts_error("KOKORO TORCH STEP: load KModel class")
            KModel = _load_kokoro_torch_model_class()
            try:
                torch.set_num_threads(1)
                torch.set_num_interop_threads(1)
            except Exception:
                pass
            self._torch = torch
            self._hf_hub_download = hf_hub_download
            local_config = self._local_torch_config_path()
            local_model = self._local_torch_model_path()
            log_tts_error(
                f"KOKORO TORCH STEP: model paths config_exists={local_config.exists()} model_exists={local_model.exists()}"
            )
            if local_config.exists() and local_model.exists():
                log_tts_error("KOKORO TORCH STEP: init KModel from local files")
                self._model = KModel(
                    repo_id=self._repo_id(),
                    config=str(local_config),
                    model=str(local_model),
                ).to("cpu").eval()
            else:
                log_tts_error("KOKORO TORCH STEP: init KModel from HF")
                self._model = KModel(repo_id=self._repo_id()).to("cpu").eval()
            log_tts_error("KOKORO TORCH STEP: model ready")
            self._loaded = True

    def _background_load_worker(self) -> None:
        try:
            log_tts_error("KOKORO TORCH WORKER START")
            if os.name == "nt":
                self._start_worker_process()
                deadline = time.time() + self._voice_fallback_timeout_s(background=True)
                while time.time() < deadline:
                    if self._wait_until_ready(1.0):
                        break
                if not self._worker_ready:
                    raise TTSError("Pure Kokoro worker did not reach ready state.")
            else:
                self._load()
            self._load_error = None
            log_tts_error("KOKORO TORCH READY")
        except Exception as exc:
            self._load_error = exc
            log_tts_error(f"KOKORO TORCH LOAD ERROR: {exc}")
        finally:
            self._load_done.set()

    def _start_background_load(self) -> None:
        if self._loaded or self._worker_ready:
            return
        with self._async_lock:
            thread = self._load_thread
            if thread is not None and thread.is_alive():
                return
            if self._loaded or self._worker_ready:
                return
            self._load_done.clear()
            self._load_error = None
            self._load_started_at = time.time()
            self._last_still_loading_log_s = 0.0
            self._last_stack_dump_log_s = 0.0
            self._load_thread = threading.Thread(target=self._background_load_worker, daemon=True)
            self._load_thread.start()
            log_tts_error("KOKORO TORCH LOAD START")

    def _foreground_ready_wait_s(self) -> float:
        try:
            return float(getattr(CFG, "TTS_KOKORO_TORCH_READY_WAIT_S", 2.0))
        except Exception:
            return 2.0

    def _voice_fallback_timeout_s(self, *, background: bool) -> float:
        try:
            base = float(getattr(CFG, "TTS_KOKORO_TIMEOUT_S", 8.0))
        except Exception:
            base = 8.0
        if background:
            return max(60.0, base * 4.0)
        return max(25.0, base * 3.0)

    def _dynamic_foreground_ready_wait_s(self) -> float:
        base = max(0.1, self._foreground_ready_wait_s())
        if not self._load_started_at:
            return base
        age = max(0.0, time.time() - self._load_started_at)
        if age < 10.0:
            return base
        return min(8.0, max(base, 4.0))

    def _wait_until_ready(self, timeout_s: float) -> bool:
        if self._loaded or self._worker_ready:
            return True
        if self._load_error is not None:
            raise TTSError(f"Pure Kokoro load failed: {self._load_error}")
        if os.name == "nt":
            self._start_worker_process()
        else:
            self._start_background_load()
        if timeout_s <= 0:
            return self._loaded or self._worker_ready
        if os.name == "nt":
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                proc = self._worker_proc
                if proc is None:
                    break
                if proc.poll() is not None:
                    raise TTSError(f"Pure Kokoro worker exited with code {proc.returncode}")
                remaining = max(0.05, deadline - time.time())
                try:
                    event = self._worker_events.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    pass
                else:
                    event_type = str(event.get("type") or "")
                    if event_type == "ready":
                        self._worker_ready = True
                        self._loaded = True
                        return True
                    if event_type == "error":
                        error_text = str(event.get("error") or "Pure Kokoro worker failed.")
                        self._load_error = TTSError(error_text)
                        raise TTSError(error_text)
            done = False
        else:
            done = self._load_done.wait(timeout_s)
        if self._loaded or self._worker_ready:
            return True
        if done and self._load_error is not None:
            raise TTSError(f"Pure Kokoro load failed: {self._load_error}")
        if self._load_started_at:
            age = max(0.0, time.time() - self._load_started_at)
            if age >= 15.0 and age - self._last_still_loading_log_s >= 10.0:
                self._last_still_loading_log_s = age
                log_tts_error(f"KOKORO TORCH STILL LOADING: {age:.1f}s")
            if age >= 20.0 and age - self._last_stack_dump_log_s >= 30.0:
                self._last_stack_dump_log_s = age
                thread = self._load_thread
                ident = None if thread is None else thread.ident
                frame = None if ident is None else sys._current_frames().get(ident)
                if frame is not None:
                    log_tts_error("KOKORO TORCH STACK DUMP START")
                    for line in traceback.format_stack(frame)[-20:]:
                        for part in line.rstrip().splitlines():
                            log_tts_error(f"  {part}")
                    log_tts_error("KOKORO TORCH STACK DUMP END")
        return self._loaded

    def warm_up(self) -> None:
        if os.name == "nt":
            self._start_background_load()
            return
        self._load()

    def stop(self) -> None:
        proc = self._worker_proc
        self._worker_proc = None
        self._worker_ready = False
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return

    def _load_voice_pack(self, voice: str):
        if os.name == "nt":
            if not self._wait_until_ready(self._dynamic_foreground_ready_wait_s()):
                raise TTSError("Pure Kokoro warm-up is still in progress.")
        else:
            self._load()
        assert self._torch is not None
        assert self._hf_hub_download is not None
        cache_key = str(voice or self.cfg.voice).strip() or self.cfg.voice
        if cache_key in self._voices:
            return self._voices[cache_key]
        if cache_key.endswith(".pt"):
            voice_path = cache_key
        elif self._local_voice_path(cache_key).exists():
            voice_path = str(self._local_voice_path(cache_key))
        else:
            voice_path = self._hf_hub_download(repo_id=self._repo_id(), filename=f"voices/{cache_key}.pt")
        pack = self._torch.load(voice_path, map_location="cpu", weights_only=True)
        self._voices[cache_key] = pack
        return pack

    def _request_worker_wav(
        self,
        text: str,
        *,
        voice: Optional[str],
        speed: Optional[float],
        timeout_s: Optional[float] = None,
    ) -> Path:
        if not self._wait_until_ready(self._dynamic_foreground_ready_wait_s()):
            raise TTSError("Pure Kokoro warm-up is still in progress.")
        proc = self._worker_proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise TTSError("Pure Kokoro worker is not available.")
        req_id = self._next_worker_id()
        temp_path = Path(tempfile.gettempdir()) / f"piper_kokoro_worker_{req_id}.wav"
        payload = {
            "id": req_id,
            "text": str(text or ""),
            "voice": str(voice or self.cfg.voice or "").strip() or self.cfg.voice,
            "speed": float(speed if speed is not None else self.cfg.speed),
            "wav_path": str(temp_path),
        }
        with self._worker_io_lock:
            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
            except Exception as exc:
                raise TTSError(f"Pure Kokoro worker request failed: {exc}") from exc

            deadline = time.time() + float(timeout_s or self._voice_fallback_timeout_s(background=False))
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise TTSError(f"Pure Kokoro worker exited with code {proc.returncode}")
                remaining = max(0.05, deadline - time.time())
                try:
                    event = self._worker_events.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    continue
                event_type = str(event.get("type") or "")
                if event_type == "ready":
                    self._worker_ready = True
                    self._loaded = True
                    continue
                if event_type != "result":
                    continue
                if str(event.get("id") or "") != req_id:
                    continue
                if not bool(event.get("ok")):
                    raise TTSError(str(event.get("error") or "Pure Kokoro worker synthesis failed."))
                wav_path = Path(str(event.get("wav_path") or temp_path))
                if not wav_path.exists():
                    raise TTSError(f"Pure Kokoro worker output missing: {wav_path}")
                return wav_path
        raise TTSError("Pure Kokoro worker synthesis timed out.")

    @staticmethod
    def _read_wav_float32(path: Path):
        if np is None:
            raise TTSError("Missing audio dependency numpy.")
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            raw_data = wf.readframes(wf.getnframes())
        if sampwidth == 2:
            audio = np.frombuffer(raw_data, dtype=np.int16)
            samples = audio.astype(np.float32) / 32767.0
        elif sampwidth == 4:
            samples = np.frombuffer(raw_data, dtype=np.float32)
        else:
            raise TTSError(f"Unsupported WAV sample width: {sampwidth}")
        if n_channels == 2:
            samples = samples.reshape(-1, 2).mean(axis=1)
        return samples, int(sr)

    def list_voices(self) -> List[str]:
        return sorted(self._voices.keys())

    def synthesize(self, text: str, voice: Optional[str], speed: Optional[float]):
        if os.name == "nt":
            wav_path = self._request_worker_wav(text, voice=voice, speed=speed)
            try:
                return self._read_wav_float32(wav_path)
            finally:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            self._load()
        assert self._model is not None
        phonemes = self._phonemize_windows_cli(text, self.cfg.lang)
        if not phonemes:
            raise TTSError("Kokoro phonemizer returned no phonemes.")
        resolved_voice = (voice or self.cfg.voice).strip() if (voice or self.cfg.voice) else self.cfg.voice
        pack = self._load_voice_pack(resolved_voice)
        spd = float(speed if speed is not None else self.cfg.speed)
        index = max(0, min(len(pack) - 1, len(phonemes) - 1))
        audio = self._model(phonemes, pack[index], spd)
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        return audio, int(self.cfg.sample_rate)

    def speak_text_blocking(self, text: str, *, voice: Optional[str], speed: Optional[float]) -> None:
        if os.name == "nt":
            samples, sr = self.synthesize(text, voice=voice, speed=speed)
            self.play(samples, sr)
            return
        samples, sr = self.synthesize(text, voice=voice, speed=speed)
        self.play(samples, sr)

    def play_wav_path(self, path: Path) -> None:
        if os.name == "nt":
            try:
                import winsound  # type: ignore
            except Exception as exc:
                raise TTSError("winsound unavailable for Kokoro playback.") from exc
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return
        try:
            import soundfile as sf  # type: ignore
            import sounddevice as sd  # type: ignore
        except Exception as exc:
            raise TTSError(f"Audio playback dependencies unavailable: {exc}") from exc
        samples, sr = sf.read(str(path), dtype="float32")
        sd.play(samples, sr)
        sd.wait()

    def play(self, samples, sr: int) -> None:
        if np is None:
            raise TTSError("Missing audio dependency numpy.")
        if os.name == "nt":
            try:
                import winsound  # type: ignore
            except Exception as exc:
                raise TTSError("winsound unavailable for Kokoro playback.") from exc
            arr = np.asarray(samples, dtype=np.float32)
            if arr.ndim > 1:
                arr = arr.mean(axis=1)
            arr = np.clip(arr, -1.0, 1.0)
            pcm = (arr * 32767.0).astype(np.int16)
            temp_path = Path(tempfile.gettempdir()) / f"piper_kokoro_torch_{int(time.time() * 1000)}.wav"
            try:
                with wave.open(str(temp_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(int(sr))
                    wf.writeframes(pcm.tobytes())
                winsound.PlaySound(str(temp_path), winsound.SND_FILENAME)
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return

        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:
            raise TTSError("Missing audio dep sounddevice.") from exc
        try:
            sd.stop()
        except Exception:
            pass
        sd.play(samples, sr)
        sd.wait()


class _WindowsSystemSpeechEngine:
    _FEMALE_HINTS = (
        "zira",
        "jenny",
        "aria",
        "hazel",
        "susan",
        "samantha",
        "catherine",
        "eva",
        "mia",
        "helena",
        "sonia",
        "elsa",
        "katja",
        "laura",
        "paulina",
    )
    _MALE_HINTS = (
        "david",
        "mark",
        "guy",
        "george",
        "james",
        "richard",
        "daniel",
        "stefan",
        "pablo",
        "sean",
        "ryan",
    )

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._winsound = None
        self._voices_cache: List[str] | None = None

    @staticmethod
    def is_available() -> bool:
        return os.name == "nt"

    @staticmethod
    def _resolve_powershell_exe() -> str:
        candidates = [
            Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
            Path(r"C:\Windows\System32\powershell.exe"),
        ]
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
                    return str(candidate)
            except Exception:
                continue
        return "powershell.exe"

    def _voice_name(self, voice: Optional[str], default_voice: str) -> str:
        candidate = str(voice or default_voice or "").strip()
        if not candidate:
            return ""
        lowered = candidate.lower()
        if not lowered.startswith(("af_", "bf_")):
            return candidate

        voices = self.list_voices()
        if not voices:
            return ""

        preferred_hints = self._FEMALE_HINTS if lowered.startswith("af_") else self._MALE_HINTS
        normalized = [(name, name.lower()) for name in voices]
        for hint in preferred_hints:
            for original, lowered_name in normalized:
                if hint in lowered_name:
                    return original
        return voices[0]

    @staticmethod
    def _rate_from_speed(speed: Optional[float], default_speed: float) -> int:
        try:
            resolved = float(speed if speed is not None else default_speed)
        except Exception:
            resolved = float(default_speed)
        # System.Speech uses [-10, 10]. Keep the mapping conservative.
        return max(-4, min(4, int(round((resolved - 1.0) * 10.0))))

    @staticmethod
    def _powershell_command(script: str) -> list[str]:
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return [
            _WindowsSystemSpeechEngine._resolve_powershell_exe(),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ]

    def _run_powershell(
        self,
        script: str,
        *,
        env: dict[str, str] | None = None,
        wait: bool = True,
    ) -> subprocess.CompletedProcess[str] | subprocess.Popen[str]:
        cmd = self._powershell_command(script)
        merged_env = {**os.environ, **(env or {})}
        if wait:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=merged_env,
                timeout=60,
            )
        with self._proc_lock:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                env=merged_env,
            )
            return self._proc

    def warm_up(self) -> None:
        # Keep boot-time warm-up lightweight: just prove SAPI is reachable.
        self.list_voices()

    def stop(self) -> None:
        with self._proc_lock:
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            import winsound  # type: ignore
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def list_voices(self) -> List[str]:
        if self._voices_cache is not None:
            return list(self._voices_cache)
        script = """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
}
finally {
    $s.Dispose()
}
""".strip()
        proc = self._run_powershell(script, wait=True)
        if not isinstance(proc, subprocess.CompletedProcess):
            return []
        if proc.returncode != 0:
            return []
        self._voices_cache = [line.strip() for line in str(proc.stdout or "").splitlines() if line.strip()]
        return list(self._voices_cache)

    def speak_text_blocking(self, text: str, *, voice: Optional[str], speed: Optional[float]) -> None:
        resolved_text = str(text or "").strip()
        if not resolved_text:
            return
        resolved_voice = self._voice_name(voice, self.cfg.voice)
        resolved_rate = self._rate_from_speed(speed, self.cfg.speed)
        script = """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if (-not [string]::IsNullOrWhiteSpace($env:PIPER_TTS_VOICE)) {
        $voices = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
        if ($voices -contains $env:PIPER_TTS_VOICE) {
            $s.SelectVoice($env:PIPER_TTS_VOICE)
        }
    }
    $s.Rate = [int]$env:PIPER_TTS_RATE
    $s.Speak($env:PIPER_TTS_TEXT)
}
finally {
    $s.Dispose()
}
""".strip()
        result = self._run_powershell(
            script,
            env={
                "PIPER_TTS_TEXT": resolved_text,
                "PIPER_TTS_VOICE": resolved_voice,
                "PIPER_TTS_RATE": str(resolved_rate),
            },
            wait=True,
        )
        if not isinstance(result, subprocess.CompletedProcess):
            return
        if result.returncode != 0:
            stderr = str(result.stderr or result.stdout or "").strip()
            raise TTSError(stderr or "Windows speech synthesis failed.")

    def play(self, samples, sr: int) -> None:
        if np is None:
            raise TTSError("Missing audio dependency numpy.")
        try:
            import winsound  # type: ignore
        except Exception as exc:
            raise TTSError("winsound unavailable for Windows TTS playback.") from exc

        arr = np.asarray(samples, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        arr = np.clip(arr, -1.0, 1.0)
        pcm = (arr * 32767.0).astype(np.int16)
        temp_path = Path(tempfile.gettempdir()) / f"piper_tts_{int(time.time() * 1000)}.wav"
        try:
            with wave.open(str(temp_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(int(sr))
                wf.writeframes(pcm.tobytes())
            winsound.PlaySound(str(temp_path), winsound.SND_FILENAME)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


class _StreamChunker:
    """Implements 3-phase streaming chunking:
    - Phase 0 (first chunk): Fast start, complete short sentences, force split at ~80.
    - Phase 1 (second chunk): Medium-fast refill, force split at ~210.
    - Phase 2+ (later chunks): Quality mode, longer emotional phrasing, force split at ~320.
    - Treats newlines as sentence endings (pauses).
    """

    # Added \n to detect line breaks as pauses
    _SENT_END_RE = re.compile(r"(?:(?<!\d)[.!?]|\n)")

    def __init__(
        self,
        first_complete_min_chars: int = 8,
        first_force_chars: int = 80,
        second_min_chars: int = 100,
        second_force_chars: int = 150,
        later_min_chars: int = 280,
        max_chars: int = 320,
    ):
        self.first_complete_min_chars = int(first_complete_min_chars)
        self.first_force_chars = int(first_force_chars)
        self.second_min_chars = int(second_min_chars)
        self.second_force_chars = int(second_force_chars)
        self.later_min_chars = int(later_min_chars)
        self.max_chars = int(max_chars)

        self.buf = ""
        self.emitted = 0
        self._chunks_sent = 0

    def reset(self) -> None:
        self.buf = ""
        self.emitted = 0
        self._chunks_sent = 0

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

    def _phase_settings(self) -> tuple[int, int]:
        if self._chunks_sent == 0:
            return self.first_complete_min_chars, self.first_force_chars
        if self._chunks_sent == 1:
            return self.second_min_chars, self.second_force_chars
        return self.later_min_chars, self.max_chars

    def _find_safe_split(self, limit: int) -> int:
        """Return absolute index of a safe split point at or before `limit` chars
        from current emitted position. Prefers spaces, then commas, then the limit.
        """
        end = self.emitted + limit
        # Prefer space
        space_pos = self.buf.rfind(" ", self.emitted, end)
        if space_pos != -1 and space_pos > self.emitted:
            return space_pos
        # Fallback: comma
        comma_pos = self.buf.rfind(",", self.emitted, end)
        if comma_pos != -1 and comma_pos > self.emitted:
            return comma_pos
        return end

    def _emit_ready(self, *, intermediate: bool) -> List[str]:
        out: List[str] = []

        while True:
            remaining = self.buf[self.emitted :]

            min_chars, force_chars = self._phase_settings()

            # Newline handling: treat as hard stop if enough chars
            if "\n" in remaining and len(remaining) >= self.first_complete_min_chars:
                nl_pos = remaining.find("\n")
                chunk = remaining[:nl_pos].strip()
                if chunk:
                    out.append(chunk)
                    self.emitted += nl_pos + 1
                    self._chunks_sent += 1
                    continue

            # Normal logic
            if len(remaining) < min_chars:
                break

            search_from = self.emitted
            m = self._SENT_END_RE.search(self.buf, pos=search_from)

            if m:
                cut = m.end()
            else:
                if intermediate and len(remaining) < force_chars:
                    break
                if intermediate:
                    cut = self._find_safe_split(force_chars)
                else:
                    cut = len(self.buf)

            chunk = self.buf[self.emitted : cut].strip()
            self.emitted = cut
            if chunk:
                out.append(chunk)
                self._chunks_sent += 1

        return out
    
class TTS:
    """Background-threaded TTS service with overlapped synth/play."""

    _SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

    def __init__(self, cfg: Optional[TTSConfig] = None):
        self.cfg = cfg or TTSConfig()
        self.engine = self._select_engine(self.cfg)

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
        self._utterance_lock = threading.Lock()
        self._utterance_serial = 0
        self._utterance_backends: dict[int, str] = {}

        self._synth_thread = threading.Thread(target=self._synth_loop, daemon=True)
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)

        # Streaming state
        self._stream_lock = threading.Lock()
        self._stream_epoch: Optional[int] = None
        self._stream_utterance: Optional[int] = None
        self._stream_voice: Optional[str] = None
        self._stream_speed: Optional[float] = None
        self._stream_chunker = _StreamChunker(
            first_complete_min_chars=8,
            first_force_chars=80,
            second_min_chars=100,
            second_force_chars=150,
            later_min_chars=280,
            max_chars=320,
        )
        self._warm_lock = threading.Lock()
        self._warmed = False

    @staticmethod
    def _select_engine(cfg: TTSConfig):
        backend = str(getattr(cfg, "backend", "auto") or "auto").strip().lower()
        if backend in {"system", "sapi", "windows"}:
            return _WindowsSystemSpeechEngine(cfg)
        if backend == "kokoro":
            return _KokoroEngine(cfg)
        if backend == "auto":
            model_path = getattr(cfg, "model_path", None)
            voices_path = getattr(cfg, "voices_path", None)
            if model_path is not None and voices_path is not None:
                try:
                    if Path(model_path).exists() and Path(voices_path).exists():
                        return _KokoroEngine(cfg)
                except Exception:
                    pass
            if _WindowsSystemSpeechEngine.is_available():
                return _WindowsSystemSpeechEngine(cfg)
        return _KokoroEngine(cfg)

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
            if hasattr(self.engine, "warm_up"):
                self.engine.warm_up()
            else:
                self.engine.synthesize(sample_text, voice=voice, speed=speed)
            self._warmed = True

    def _bump_epoch(self) -> int:
        with self._epoch_lock:
            self._epoch += 1
            return self._epoch

    def _get_epoch(self) -> int:
        with self._epoch_lock:
            return self._epoch

    def _next_utterance_id(self) -> int:
        with self._utterance_lock:
            self._utterance_serial += 1
            utterance_id = self._utterance_serial
            # Keep only a small recent cache of locked backend choices.
            threshold = utterance_id - 8
            stale = [key for key in self._utterance_backends if key < threshold]
            for key in stale:
                self._utterance_backends.pop(key, None)
            return utterance_id

    def _choose_backend_for_utterance(
        self,
        utterance_id: int,
        voice: Optional[str],
        speed: Optional[float],
    ) -> str:
        with self._utterance_lock:
            cached = self._utterance_backends.get(utterance_id)
            if cached:
                return cached

        backend = "default"
        if os.name == "nt" and isinstance(self.engine, _KokoroEngine):
            backend = self.engine.choose_reply_backend(voice=voice, speed=speed)

        with self._utterance_lock:
            self._utterance_backends[utterance_id] = backend
        return backend

    def _segment_text_for_backend(self, text: str, backend: str) -> List[str]:
        return [text]

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
            self._stream_utterance = None
            self._stream_voice = None
            self._stream_speed = None
            self._stream_chunker.reset()
        with self._utterance_lock:
            self._utterance_backends.clear()
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
        utterance_id = self._next_utterance_id()
        for ch in chunks:
            if ch:
                self._queue_text_job(epoch, utterance_id, ch, voice, speed)

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
            self._stream_utterance = self._next_utterance_id()
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
            utterance_id = self._stream_utterance
            voice = self._stream_voice
            speed = self._stream_speed

            if epoch is None or epoch != self._get_epoch():
                return

            ready = self._stream_chunker.push(delta)

        if epoch is None or utterance_id is None:
            return
        for ch in ready:
            if ch and epoch == self._get_epoch():
                self._queue_text_job(epoch, utterance_id, ch, voice, speed)

    def stream_flush(self) -> None:
        """Force the buffer to emit text immediately. Used before SFX."""
        if not self.cfg.enabled:
            return
        
        with self._stream_lock:
            epoch = self._stream_epoch
            utterance_id = self._stream_utterance
            voice = self._stream_voice
            speed = self._stream_speed
            if epoch is None or utterance_id is None:
                return
            
            chunks = self._stream_chunker.flush()
            
        for ch in chunks:
            if ch and epoch == self._get_epoch():
                self._queue_text_job(epoch, utterance_id, ch, voice, speed)

    def stream_end(self) -> None:
        if not self.cfg.enabled:
            return

        with self._stream_lock:
            epoch = self._stream_epoch
            utterance_id = self._stream_utterance
            voice = self._stream_voice
            speed = self._stream_speed
            self._stream_epoch = None
            self._stream_utterance = None
            self._stream_voice = None
            self._stream_speed = None

            if epoch is None or epoch != self._get_epoch():
                self._stream_chunker.reset()
                return

            chunks = self._stream_chunker.end()

        for ch in chunks:
            if ch and epoch == self._get_epoch() and utterance_id is not None:
                self._queue_text_job(epoch, utterance_id, ch, voice, speed)

    def _queue_text_job(
        self,
        epoch: int,
        utterance_id: int,
        text: str,
        voice: Optional[str],
        speed: Optional[float],
    ) -> None:
        clean_text = self._clean_tts_text(text).strip()
        if not clean_text:
            return
        backend = self._choose_backend_for_utterance(utterance_id, voice, speed)
        for segment in self._segment_text_for_backend(clean_text, backend):
            segment = str(segment or "").strip()
            if not segment:
                continue
            self._job_q.put((epoch, "text", (segment, voice, speed, backend)))

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
                    text, voice, speed, backend = payload
                    try:
                        if os.name == "nt" and isinstance(self.engine, _KokoroEngine):
                            if backend == "torch" and self.engine._voice_fallback_engine is not None:
                                samples, sr = self.engine._voice_fallback_engine.synthesize(text, voice=voice, speed=speed)
                            elif backend == "system" and self.engine._fallback_engine is not None:
                                self.engine._fallback_engine.speak_text_blocking(text, voice=voice, speed=speed)
                                continue
                            else:
                                samples, sr = self.engine.synthesize(text, voice=voice, speed=speed)
                        elif hasattr(self.engine, "synthesize"):
                            samples, sr = self.engine.synthesize(text, voice=voice, speed=speed)
                        elif hasattr(self.engine, "speak_text_blocking"):
                            self.engine.speak_text_blocking(text, voice=voice, speed=speed)
                            continue
                        else:
                            raise TTSError("TTS engine cannot synthesize or speak text.")
                    except Exception as e:
                        log_tts_error(f"TTS SYNTH ERROR: {e}")
                        if os.name == "nt" and isinstance(self.engine, _KokoroEngine):
                            fallback_backend = backend
                            try:
                                self.engine._disable_with_reason(f"Kokoro synth error: {e}")
                                if backend not in {"torch", "system"}:
                                    fallback_backend = self.engine.choose_reply_backend(voice=voice, speed=speed)
                                if fallback_backend == "torch" and self.engine._voice_fallback_engine is not None:
                                    samples, sr = self.engine._voice_fallback_engine.synthesize(text, voice=voice, speed=speed)
                                elif fallback_backend == "system" and self.engine._fallback_engine is not None:
                                    self.engine._fallback_engine.speak_text_blocking(text, voice=voice, speed=speed)
                                    continue
                                else:
                                    continue
                            except Exception as fallback_exc:
                                log_tts_error(f"TTS STREAM FALLBACK ERROR: {fallback_exc}")
                                continue
                        else:
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
                        if np is None:
                            raise TTSError("Missing audio dependency numpy.")
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
