"""core/boot.py

Handles initialization of the LLM server.
"""

import logging
import os
import re
import subprocess
import time
import threading
from collections.abc import Callable, Sequence
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse
from config import CFG, data_debug_path

try:
    import psutil
except ImportError:
    psutil = None


PostBootTask = tuple[str, Callable[[], object]]
_LOG = logging.getLogger(__name__)

class BootManager:
    def __init__(
        self,
        ui_queue,
        post_boot_tasks: Sequence[PostBootTask] | None = None,
        background_boot_tasks: Sequence[PostBootTask] | None = None,
    ):
        self.process = None
        self.ready = False
        self.server_ready = False
        self.brain_ready = False
        self.ui_queue = ui_queue
        self.post_boot_tasks = list(post_boot_tasks or [])
        self.background_boot_tasks = list(background_boot_tasks or [])
        self._server_log_handle = None

    def pause_server(self):
        """Stops the LLM server to free VRAM."""
        if self.process and self.process.poll() is None:
            self.log("[Boot] Pausing LLM Server...")
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
                self.log("[Boot] Server Paused.")
            except Exception as e:
                self.log(f"[Boot] Error pausing: {e}")
            finally:
                self._close_server_log_handle()

    def resume_server(self):
        """Restarts the server."""
        if not (self.process and self.process.poll() is None):
            self.log("[Boot] Resuming LLM Server...")
            self.run_sequence(run_post_boot_tasks=False) # Restarts logic
            
    def log(self, message: str):
        _LOG.info("%s", message)
        if self.ui_queue:
            self.ui_queue.put(("boot_log", message))

    @staticmethod
    def _normalize_pathish(value: object) -> str:
        return str(value or "").strip().replace("\\", "/").lower()

    def _target_server_port(self) -> str:
        parsed = urlparse(str(getattr(CFG, "LLAMA_SERVER_URL", "http://127.0.0.1:8080")))
        return str(parsed.port or 8080)

    @staticmethod
    def _runtime_path_arg(path_value: object, *, executable: object) -> str:
        raw = str(path_value or "").strip()
        if not raw:
            return ""
        exe_text = str(executable or "").strip().lower()
        if os.name != "nt" and exe_text.endswith(".exe"):
            match = re.match(r"^/mnt/([a-z])/(.*)$", raw)
            if match:
                drive = match.group(1).upper()
                suffix = match.group(2).replace("/", "\\")
                return f"{drive}:\\{suffix}"
        return raw

    @staticmethod
    def _cmdline_value(cmdline: list[str], flag: str) -> str:
        lowered = [str(part or "").strip().lower() for part in cmdline]
        try:
            idx = lowered.index(flag.lower())
        except ValueError:
            return ""
        if idx + 1 >= len(cmdline):
            return ""
        return str(cmdline[idx + 1] or "").strip()

    def _is_managed_llama_server(self, proc_info: dict) -> bool:
        proc_name = str(proc_info.get("name") or "").strip().lower()
        cmdline = [str(part or "").strip() for part in (proc_info.get("cmdline") or []) if str(part or "").strip()]
        if "llama-server" not in proc_name and not any("llama-server" in part.lower() for part in cmdline):
            return False

        port = self._cmdline_value(cmdline, "--port")
        if port != self._target_server_port():
            return False

        model_arg = self._normalize_pathish(self._cmdline_value(cmdline, "-m"))
        target_model = self._normalize_pathish(getattr(CFG, "MODEL_PATH", ""))
        target_model_name = Path(str(getattr(CFG, "MODEL_PATH", ""))).name.lower()
        if not model_arg or not target_model_name:
            return False
        if model_arg != target_model and not model_arg.endswith("/" + target_model_name):
            return False

        exe_name = Path(str(getattr(CFG, "LLAMA_SERVER_EXE", ""))).name.lower()
        if exe_name and cmdline:
            first = self._normalize_pathish(cmdline[0])
            if not first.endswith("/" + exe_name) and Path(cmdline[0]).name.lower() != exe_name:
                return False
        return True

    def _kill_orphans(self):
        self.log("[Boot] Checking for orphan server processes...")
        if psutil is None:
            self.log("[Boot] psutil missing; skipping orphan process scan.")
            return
        killed_any = False
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if not self._is_managed_llama_server(proc.info):
                    continue
                self.log(f"[Boot] Killing orphan server PID {proc.info['pid']}")
                proc.kill()
                killed_any = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, TypeError, ValueError):
                continue
        if killed_any:
            time.sleep(1)

    def _wait_for_server(self):
        self.log("Starting LLM Server...")
        # Kill any managed orphans from prior sessions BEFORE trusting an
        # existing /health response.  A zombie server may still answer 200
        # while being unable to serve real requests.
        self._kill_orphans()

        try:
            req = urllib.request.Request(f"{CFG.LLAMA_SERVER_URL}/health", method='GET')
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    self.log("Using existing LLM server.")
                    self.server_ready = True
                    return True
        except Exception:
            pass

        if not hasattr(CFG, 'LLAMA_SERVER_EXE') or not CFG.LLAMA_SERVER_EXE.exists():
            self.log(f"FATAL: Server binary not found at {CFG.LLAMA_SERVER_EXE}")
            self.server_ready = False
            return False

        if not hasattr(CFG, 'MODEL_PATH') or not CFG.MODEL_PATH.exists():
            self.log(f"FATAL: Model file not found at {CFG.MODEL_PATH}")
            self.server_ready = False
            return False

        server_exe = str(CFG.LLAMA_SERVER_EXE)
        try:
            _ver = subprocess.check_output([server_exe, "--version"], stderr=subprocess.STDOUT, timeout=5).decode(errors="replace")
            _ver_line = next((l for l in _ver.splitlines() if l.startswith("version:")), None)
            if _ver_line:
                self.log(f"llama.cpp {_ver_line.strip()}")
        except Exception:
            pass
        model_arg = self._runtime_path_arg(CFG.MODEL_PATH, executable=server_exe)
        cmd = [
            server_exe,
            "-m", model_arg,
            "--port", "8080",
            "--ctx-size", str(CFG.CONTEXT_SIZE),
            "-ngl", str(getattr(CFG, "LLAMA_SERVER_GPU_LAYERS", 99)),
            "--host", str(getattr(CFG, "LLAMA_SERVER_BIND_HOST", "127.0.0.1")),
            # Use 'auto' when mmproj is present — llama.cpp will skip FA for vision ops
            # that don't support it, avoiding the CPU fallback issue on Qwen3.5 + CUDA.
            "--flash-attn", "auto" if (getattr(CFG, "MMPROJ_PATH", None) and Path(getattr(CFG, "MMPROJ_PATH", None)).exists()) else "on",
        ]
        reasoning_budget = getattr(CFG, "LLAMA_SERVER_REASONING_BUDGET", -1)
        if reasoning_budget is not None:
            cmd.extend(["--reasoning-budget", str(reasoning_budget)])
        mmproj_path = getattr(CFG, "MMPROJ_PATH", None)
        if mmproj_path and Path(mmproj_path).exists():
            mmproj_arg = self._runtime_path_arg(mmproj_path, executable=server_exe)
            cmd.extend(["--mmproj", mmproj_arg])
            self.log(f"Using multimodal projector: {mmproj_path}")

        try:
            log_path = data_debug_path(CFG.DATA_DIR, "llama_server.log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._server_log_handle = open(log_path, "a", encoding="utf-8", errors="replace")
            popen_kwargs = {
                "stdout": self._server_log_handle,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(cmd, **popen_kwargs)
            self.log(f"Server PID {self.process.pid} launched.")
        except Exception as e:
            self._close_server_log_handle()
            self.log(f"FATAL: Server failed to start: {e}")
            self.server_ready = False
            return False
        
        self.log("Waiting for server health check...")
        start_time = time.time()
        timeout_s = float(getattr(CFG, "LLAMA_SERVER_HEALTH_TIMEOUT_S", 120.0))
        last_progress_log = 0.0
        while time.time() - start_time < timeout_s:
            if self.process.poll() is not None:
                self._close_server_log_handle()
                self.log(f"FATAL: Server crashed with code {self.process.returncode}")
                return False
            try:
                req = urllib.request.Request(f"{CFG.LLAMA_SERVER_URL}/health", method='GET')
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        self.log("Server Health Check: OK")
                        self.server_ready = True
                        return True
                    if resp.status == 503 and (time.time() - last_progress_log) >= 10:
                        self.log("Server is still loading the model...")
                        last_progress_log = time.time()
            except urllib.error.HTTPError as exc:
                if exc.code == 503 and (time.time() - last_progress_log) >= 10:
                    self.log("Server is up but still loading the model...")
                    last_progress_log = time.time()
                time.sleep(0.5)
            except Exception:
                if (time.time() - last_progress_log) >= 10:
                    self.log("Waiting for model load...")
                    last_progress_log = time.time()
                time.sleep(0.5)
        self.log(f"FATAL: Server health check timed out after {int(timeout_s)}s")
        self.server_ready = False
        return False

    def _close_server_log_handle(self) -> None:
        if self._server_log_handle is not None:
            try:
                self._server_log_handle.close()
            except Exception:
                pass
            self._server_log_handle = None

    def _init_brain(self):
        self.log("Initializing Vector Brain...")
        try:
            from memory.brain import get_brain
            brain = get_brain(CFG.DATA_DIR)
            if getattr(brain, "vector_ready", False):
                self.log("Brain Model Loaded.")
            elif getattr(brain, "vector_warmup_pending", False):
                self.log("Brain Ready (fallback active; vector warm-up continues).")
            else:
                self.log("Brain Ready.")
            self.brain_ready = True
            return True
        except Exception as e:
            self.log(f"FATAL: Brain failed: {e}")
            self.brain_ready = False
            return False

    def _run_post_boot_tasks(self) -> None:
        for label, callback in self.post_boot_tasks:
            self._run_named_task(label, callback)

    def _run_named_task(self, label: str, callback: Callable[[], object]) -> None:
        self.log(label)
        try:
            result = callback()
            if isinstance(result, str) and result.strip():
                self.log(result.strip())
            else:
                self.log(f"{label} OK")
        except Exception as exc:
            self.log(f"{label} FAILED: {exc}")

    def _start_background_boot_tasks(self) -> None:
        for label, callback in self.background_boot_tasks:
            threading.Thread(
                target=self._run_named_task,
                args=(label, callback),
                daemon=True,
            ).start()

    def run_sequence(self, *, run_post_boot_tasks: bool = True):
        self.ready = False
        self.server_ready = False
        self.brain_ready = False
        
        server_thread = threading.Thread(target=self._wait_for_server)
        brain_thread = threading.Thread(target=self._init_brain)
        post_boot_thread = None
        
        server_thread.start()
        if run_post_boot_tasks and self.post_boot_tasks:
            post_boot_thread = threading.Thread(target=self._run_post_boot_tasks)
            post_boot_thread.start()
        if run_post_boot_tasks and self.background_boot_tasks:
            self._start_background_boot_tasks()
        brain_thread.start()
        
        server_thread.join()
        brain_thread.join()
        if post_boot_thread is not None:
            post_boot_thread.join()
        
        process_running = self.process and self.process.poll() is None
        if self.server_ready and self.brain_ready and (process_running or self.process is None):
            self.log("System Ready.")
            self.ready = True
            if self.ui_queue:
                self.ui_queue.put(("boot_ready", ""))
        else:
            self.log("System Failed.")

    def shutdown(self):
        killed = False
        if self.process and self.process.poll() is None:
            self.log("[System] Terminating LLM Server...")
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try: self.process.kill()
                except Exception:
                    pass
            finally:
                self._close_server_log_handle()
            killed = True

        # If we didn't manage to kill our own process (e.g. the server was
        # already running when the harness started), sweep for managed orphans
        # so test harnesses don't leak llama-server instances.
        if not killed:
            self._kill_orphans()
