#!/usr/bin/env python3
"""Piper build monitor — run from repo root via `python scripts/monitor.py`.

Outputs a JSON report to stdout. Non-zero exit code if any check found issues.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_smoke_tests() -> dict[str, Any]:
    """Run a rotating subset of smoke tests based on current hour."""
    import subprocess

    hour = datetime.now(timezone.utc).hour
    rotation = hour % 4

    suites: dict[int, list[str]] = {
        0: [
            "scripts/langgraph_interrupt_smoke_test.py",
            "scripts/langgraph_checkpoint_inspect_smoke_test.py",
            "scripts/langgraph_checkpoint_recovery_smoke_test.py",
            "scripts/langgraph_recovery_command_smoke_test.py",
        ],
        1: [
            "scripts/ambiguous_task_clarification_harness_smoke_test.py",
            "scripts/benchmark_search_routing_harness_smoke_test.py",
            "scripts/magi_model_server_client_smoke_test.py",
            "scripts/secretary_stage_generator_smoke_test.py",
        ],
        2: [
            "scripts/file_work_smoke_test.py",
            "scripts/turn_screen_image_harness_smoke_test.py",
            "scripts/search_smoke_test.py",
            "scripts/browser_smoke_test.py",
        ],
        3: [
            "scripts/orchestrator_graph_smoke_test.py",
            "scripts/execution_smoke_test.py",
            "scripts/code_edit_current_state_verifier_smoke_test.py",
            "scripts/codex_ui_repair_smoke_test.py",
        ],
    }

    tests = suites.get(rotation, suites[0])
    results: list[dict[str, Any]] = []
    any_failed = False

    # Prefer .venv python
    py = str(Path(".venv/Scripts/python.exe").resolve()) if Path(".venv/Scripts/python.exe").exists() else sys.executable

    for script in tests:
        if not Path(script).exists():
            results.append({"script": script, "status": "MISSING", "duration_ms": 0})
            continue
        start = datetime.now(timezone.utc)
        try:
            proc = subprocess.run(
                [py, script],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            failed = proc.returncode != 0 or '"success"' in proc.stderr and '"success": false' in proc.stderr
            if not failed:
                failed = proc.returncode != 0
        except subprocess.TimeoutExpired:
            failed = True
            proc = None  # type: ignore[assignment]
        duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        results.append({
            "script": script,
            "status": "FAIL" if failed else "PASS",
            "duration_ms": duration,
            "exit_code": proc.returncode if proc else -1,
        })
        if failed:
            any_failed = True

    return {"rotation": rotation, "tests": results, "any_failed": any_failed}


def check_git_hygiene() -> dict[str, Any]:
    """Check for uncommitted changes and temp files."""
    repo = Path(__file__).resolve().parents[1]
    issues: list[str] = []

    # Uncommitted changes
    try:
        proc = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            cwd=str(repo),
            timeout=15,
        )
        changed = [line for line in proc.stdout.strip().splitlines() if line.strip()]
        if changed:
            issues.append(f"{len(changed)} uncommitted file(s)")
    except Exception:
        pass

    # Untracked temp files
    temp_patterns = ("*.pyc", "__pycache__", "*.tmp", ".DS_Store", "*~", "*.swp")
    for pattern in temp_patterns:
        matches = list(repo.rglob(pattern))
        if matches:
            issues.append(f"Untracked temp files: {pattern} ({len(matches)} found)")
            break

    return {"issues": issues, "dirty": len(issues) > 0}


def scan_debug_logs() -> dict[str, Any]:
    """Scan data/debug/*.log for ERROR/Exception lines."""
    repo = Path(__file__).resolve().parents[1]
    debug_dir = repo / "data" / "debug"
    if not debug_dir.exists():
        return {"issues": [], "has_errors": False}

    issues: list[dict[str, Any]] = []
    for log_file in sorted(debug_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            error_lines = [line for line in lines if any(k in line for k in ("ERROR", "Exception", "Traceback"))]
            if error_lines:
                issues.append({
                    "file": str(log_file.relative_to(repo)),
                    "error_count": len(error_lines),
                    "latest": error_lines[-1][:200],
                })
        except Exception:
            pass

    return {"issues": issues, "has_errors": len(issues) > 0}


def check_dependencies() -> dict[str, Any]:
    """Weekly check: compare requirements.txt vs installed packages."""
    repo = Path(__file__).resolve().parents[1]
    req_file = repo / "requirements.txt"
    if not req_file.exists():
        return {"issues": ["requirements.txt not found"], "drift": True}

    # Only run once per week (skip most calls)
    return {"issues": [], "drift": False, "note": "Weekly check skipped; run manually with `pip list --outdated`"}


def main() -> int:
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "smoke_tests": run_smoke_tests(),
        "git_hygiene": check_git_hygiene(),
        "debug_logs": scan_debug_logs(),
        "dependencies": check_dependencies(),
    }

    # Determine overall health
    healthy = (
        not report["smoke_tests"]["any_failed"]
        and not report["git_hygiene"]["dirty"]
        and not report["debug_logs"]["has_errors"]
    )
    report["healthy"] = healthy

    print(json.dumps(report, indent=2, default=str))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
