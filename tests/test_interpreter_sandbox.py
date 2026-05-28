"""tests.test_interpreter_sandbox

Hardening tests for the interpreter sandbox.
These verify that common escape vectors are blocked.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from tools.interpreter import Interpreter


@pytest.fixture
def interpreter(tmp_path: Path) -> Interpreter:
    return Interpreter(workspace=tmp_path)


class TestBuiltinsSubscript:
    def test_builtins_subscript_import_is_blocked(self, interpreter: Interpreter) -> None:
        code = '__builtins__["__import__"]("os").system("echo pwned")'
        report = interpreter.run_report(code)
        assert report.status == "blocked"
        assert "__builtins__" in report.summary

    def test_builtins_subscript_eval_is_blocked(self, interpreter: Interpreter) -> None:
        code = '__builtins__["eval"]("1+1")'
        report = interpreter.run_report(code)
        assert report.status == "blocked"
        assert "__builtins__" in report.summary


class TestPathlibBlocked:
    def test_pathlib_import_blocked(self, interpreter: Interpreter) -> None:
        code = 'import pathlib\nprint("ok")'
        report = interpreter.run_report(code)
        assert report.status == "blocked"
        assert "pathlib" in report.summary

    def test_pathlib_import_from_blocked(self, interpreter: Interpreter) -> None:
        code = 'from pathlib import Path\nprint("ok")'
        report = interpreter.run_report(code)
        assert report.status == "blocked"
        assert "pathlib" in report.summary

    def test_pathlib_absolute_path_string_blocked(self, interpreter: Interpreter) -> None:
        code = 'import pathlib\np = pathlib.Path("/etc/passwd")'
        report = interpreter.run_report(code)
        # Import is blocked before the path string is reached
        assert report.status == "blocked"


class TestSafeCodeStillRuns:
    def test_simple_print_works(self, interpreter: Interpreter) -> None:
        code = 'print("hello")'
        report = interpreter.run_report(code)
        assert report.status == "executed"
        assert "hello" in report.stdout
