"""tools/interpreter.py

Sandboxed Python Code Execution.
"""

from dataclasses import dataclass
import subprocess
import sys
import os
import re
import time
import ast
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

        # --- SECURITY JAIL V4 (AST-BASED) ---
        BLOCKED_IMPORTS = {
            "shutil", "subprocess", "multiprocessing",
            "socket", "ctypes", "importlib", "os", "sys"
        }
        BLOCKED_CALLS = {
            "__import__", "eval", "exec", "compile", "open", "getattr"
        }
        BLOCKED_ATTR_ROOTS = {
            "__builtins__", "os", "subprocess", "shutil",
            "multiprocessing", "socket", "ctypes", "importlib", "sys"
        }

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return ExecutionReport(status="failed", summary=f"Syntax Error: {e}")

        for node in ast.walk(tree):
            # Block imports of forbidden modules
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in BLOCKED_IMPORTS:
                        return ExecutionReport(
                            status="blocked",
                            summary=f"SECURITY VIOLATION: Importing '{alias.name}' is blocked.",
                        )
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in BLOCKED_IMPORTS:
                    return ExecutionReport(
                        status="blocked",
                        summary=f"SECURITY VIOLATION: Importing from '{node.module}' is blocked.",
                    )
            # Block calls to dangerous functions
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in BLOCKED_CALLS:
                        return ExecutionReport(
                            status="blocked",
                            summary=f"SECURITY VIOLATION: Call to '{node.func.id}' is blocked.",
                        )
                elif isinstance(node.func, ast.Attribute):
                    # Block attribute access on dangerous modules (e.g., os.system)
                    parts = []
                    attr_node = node.func
                    while isinstance(attr_node, ast.Attribute):
                        parts.append(attr_node.attr)
                        attr_node = attr_node.value
                    if isinstance(attr_node, ast.Name):
                        parts.append(attr_node.id)
                        root = parts[-1]
                        if root in BLOCKED_ATTR_ROOTS:
                            return ExecutionReport(
                                status="blocked",
                                summary=f"SECURITY VIOLATION: Attribute access on '{root}' is blocked.",
                            )
                        # Block __builtins__.* access
                        full = ".".join(reversed(parts))
                        if full.startswith("__builtins__"):
                            return ExecutionReport(
                                status="blocked",
                                summary=f"SECURITY VIOLATION: __builtins__ access is blocked.",
                            )
            # Block direct attribute access on forbidden roots (e.g., os.system without call)
            elif isinstance(node, ast.Attribute):
                root = None
                attr_node = node
                while isinstance(attr_node, ast.Attribute):
                    attr_node = attr_node.value
                if isinstance(attr_node, ast.Name):
                    root = attr_node.id
                if root in BLOCKED_ATTR_ROOTS:
                    return ExecutionReport(
                        status="blocked",
                        summary=f"SECURITY VIOLATION: Attribute access on '{root}' is blocked.",
                    )
            # Block direct use of forbidden names as expressions (e.g., eval)
            elif isinstance(node, ast.Name):
                if node.id in BLOCKED_CALLS:
                    return ExecutionReport(
                        status="blocked",
                        summary=f"SECURITY VIOLATION: Usage of '{node.id}' is blocked.",
                    )

        if re.search(r'["\']([A-Za-z]:|/|\.\.)', code):
            return ExecutionReport(
                status="blocked",
                summary="SECURITY VIOLATION: Absolute paths (C:\\) or parent folders (../) are blocked. Stay in the current folder.",
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
