"""tools/interpreter.py

Sandboxed Python Code Execution.
"""

from dataclasses import dataclass
import subprocess
import sys
import os
import re
import time
from pathlib import Path

from core.runtime_control import CancellationToken, OperationCancelled

# Safety Timeout in seconds
EXEC_TIMEOUT = 30


@dataclass(frozen=True)
class ExecutionReport:
    status: str
    summary: str
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None

class Interpreter:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run_report(self, code: str, *, cancel_token: CancellationToken | None = None) -> ExecutionReport:
        """Executes Python code and returns a structured report."""
        if not code or not code.strip():
            return ExecutionReport(status="failed", summary="Error: No code provided.")

        # --- SECURITY JAIL V3 (SMARTER) ---
        BLOCKED_IMPORTS = [
            "shutil", "subprocess", "multiprocessing",
            "socket", "ctypes", "importlib", "__import__", "sys"
        ]
        for mod in BLOCKED_IMPORTS:
            if f"import {mod}" in code or f"from {mod}" in code:
                return ExecutionReport(
                    status="blocked",
                    summary=f"SECURITY VIOLATION: Importing '{mod}' is blocked.",
                )

        if re.search(r'["\']([A-Za-z]:|/|\.\.)', code):
            return ExecutionReport(
                status="blocked",
                summary="SECURITY VIOLATION: Absolute paths (C:\\) or parent folders (../) are blocked. Stay in the current folder.",
            )

        BLOCKED_CALLS = ["os.system", "os.popen", "subprocess", "exec(", "eval("]
        for call in BLOCKED_CALLS:
            if call in code:
                return ExecutionReport(
                    status="blocked",
                    summary=f"SECURITY VIOLATION: Usage of '{call}' is blocked for safety.",
                )
        # --- END SECURITY ---

        temp_path = self.workspace / "temp_exec.py"
        try:
            temp_path.write_text(code, encoding="utf-8")

            cmd = [sys.executable, str(temp_path)]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self.workspace),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            start = time.monotonic()
            while True:
                if cancel_token is not None and cancel_token.is_cancelled:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise OperationCancelled(cancel_token.reason)
                if process.poll() is not None:
                    break
                if time.monotonic() - start > EXEC_TIMEOUT:
                    process.kill()
                    process.communicate()
                    return ExecutionReport(
                        status="failed",
                        summary=f"Error: Execution timed out after {EXEC_TIMEOUT} seconds.",
                    )
                time.sleep(0.1)

            stdout, stderr = process.communicate()

            stdout = (stdout or "").strip()
            stderr = (stderr or "").strip()

            if process.returncode != 0:
                return ExecutionReport(
                    status="failed",
                    summary=f"Execution Error (Code {process.returncode})",
                    stdout=stdout,
                    stderr=stderr,
                    return_code=process.returncode,
                )

            if stdout:
                summary = "Execution succeeded with output."
            else:
                summary = "Execution successful (No output)."
            return ExecutionReport(
                status="executed",
                summary=summary,
                stdout=stdout,
                stderr=stderr,
                return_code=process.returncode,
            )

        except OperationCancelled:
            raise
        except Exception as e:
            return ExecutionReport(
                status="failed",
                summary=f"System Error: {e}",
            )
        finally:
            try:
                os.remove(temp_path)
            except Exception:
                pass

    def run(self, code: str) -> str:
        """Executes python code in a separate process with a Directory Jail."""
        report = self.run_report(code)
        if report.status == "executed":
            if report.stdout:
                return f"Output:\n{report.stdout}"
            return report.summary
        if report.stderr:
            return f"{report.summary}:\n{report.stderr}"
        return report.summary
