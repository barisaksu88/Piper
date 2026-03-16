from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from tools.file_ops import FileOpError, resolve_workspace_path


EventCallback = Callable[[str, object], None]


class EmbeddedCodeSession:
    def __init__(self, workspace: Path, emit: EventCallback) -> None:
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.emit = emit
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._script_path = ""
        self._suppressed_processes: set[int] = set()

    def is_active(self) -> bool:
        with self._lock:
            process = self._process
        return process is not None and process.poll() is None

    def active_script(self) -> str:
        with self._lock:
            return self._script_path

    def start_script(self, raw_path: str) -> str:
        full_path, rel_path = resolve_workspace_path(self.workspace, raw_path)
        if full_path.suffix.lower() != ".py":
            raise FileOpError("Embedded code sessions only support relative .py files inside the workspace.")
        if not full_path.is_file():
            raise FileOpError(f"Workspace script not found: {rel_path}")

        self.stop(silent=True)

        process = subprocess.Popen(
            [sys.executable, "-u", rel_path],
            cwd=str(self.workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=0,
        )
        with self._lock:
            self._process = process
            self._script_path = rel_path
        self.emit("code_session_reset", "")
        self.emit("code_session_status", f"Running: {rel_path}")
        self.emit("code_session_output", f"$ python {rel_path}\n\n")
        self.emit("code_session_active", True)
        self.emit("code_session_focus", "")
        self.emit("ui_controls_refresh", "")

        reader = threading.Thread(
            target=self._pump_stdout,
            args=(process, rel_path),
            daemon=True,
            name="embedded-code-session",
        )
        self._reader_thread = reader
        reader.start()
        return rel_path

    def send_input(self, text: str) -> bool:
        payload = str(text or "")
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            return False
        process.stdin.write(payload + "\n")
        process.stdin.flush()
        self.emit("code_session_output", payload + "\n")
        self.emit("code_session_focus", "")
        return True

    def stop(self, *, silent: bool = False) -> bool:
        with self._lock:
            process = self._process
            script_path = self._script_path
            if process is None:
                return False
            self._process = None
            self._script_path = ""
            self._suppressed_processes.add(id(process))
        if process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if not silent:
            self.emit("code_session_status", f"Stopped: {script_path or 'session'}")
            self.emit("code_session_output", "\n[Process stopped]\n")
        self.emit("code_session_active", False)
        self.emit("ui_controls_refresh", "")
        return True

    def shutdown(self) -> None:
        self.stop(silent=True)

    def _pump_stdout(self, process: subprocess.Popen[str], rel_path: str) -> None:
        try:
            stream = process.stdout
            if stream is None:
                return
            while True:
                chunk = stream.read(1)
                if chunk == "":
                    break
                self.emit("code_session_output", chunk)
        except Exception as exc:
            self.emit("code_session_output", f"\n[Code session read error: {exc}]\n")
        finally:
            try:
                return_code = process.wait(timeout=1)
            except Exception:
                return_code = process.poll()
            with self._lock:
                suppressed = id(process) in self._suppressed_processes
                self._suppressed_processes.discard(id(process))
                is_current_process = self._process is process
                if is_current_process:
                    self._process = None
                    self._script_path = ""
            if suppressed:
                return
            if return_code == 0:
                self.emit("code_session_status", f"Finished: {rel_path}")
            elif return_code is None:
                self.emit("code_session_status", f"Ended: {rel_path}")
            else:
                self.emit("code_session_status", f"Exited ({return_code}): {rel_path}")
            self.emit("code_session_output", f"\n[Process exited with code {return_code}]\n")
            self.emit("code_session_active", False)
            self.emit("ui_controls_refresh", "")
