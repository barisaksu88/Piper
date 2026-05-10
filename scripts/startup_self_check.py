#!/usr/bin/env python3
"""Piper startup self-check — diagnostic tool for local runtime health.

Safe to run anytime. Does not launch servers, models, or UI.
May create missing standard runtime directories (data/, data/state/, data/debug/).

Exit codes:
    0 = OK or WARN
    2 = BLOCKED (critical failure)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo root
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CheckItem:
    name: str
    status: str  # "OK", "WARN", "BLOCKED"
    message: str


@dataclass
class SelfCheckReport:
    verdict: str = "OK"
    summary: str = ""
    checks: list[CheckItem] = field(default_factory=list)
    python_version: str = ""
    python_executable: str = ""
    repo_root: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add(report: SelfCheckReport, name: str, status: str, message: str) -> None:
    report.checks.append(CheckItem(name=name, status=status, message=message))


def _ensure_dir(path: Path) -> tuple[bool, str]:
    """Create directory if missing. Return (success, message)."""
    try:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            return True, f"created {path}"
        if not path.is_dir():
            return False, f"exists but is not a directory: {path}"
        return True, f"exists: {path}"
    except OSError as exc:
        return False, f"cannot create {path}: {exc}"


def _git_ok(*args: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT_DIR), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout.strip()


def _tracked_files_with_prefix(prefix: str) -> list[str]:
    ok, out = _git_ok("ls-files")
    if not ok:
        return []
    return [line for line in out.splitlines() if line.strip().startswith(prefix)]


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------

def run_self_check() -> SelfCheckReport:
    report = SelfCheckReport()
    report.python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    report.python_executable = sys.executable
    report.repo_root = str(ROOT_DIR)

    # 1. Python version
    py_ok = sys.version_info >= (3, 10)
    _add(
        report,
        "python_version",
        "OK" if py_ok else "BLOCKED",
        f"{report.python_version} ({'supported' if py_ok else 'requires 3.10+'})"
    )

    # 2. Repo root detection
    repo_ok = (ROOT_DIR / "app.py").exists() and (ROOT_DIR / "config.py").exists()
    _add(
        report,
        "repo_root",
        "OK" if repo_ok else "BLOCKED",
        f"{ROOT_DIR} ({'app.py + config.py found' if repo_ok else 'missing app.py or config.py'})"
    )

    # 3. Required directories
    required_dirs = [
        ROOT_DIR / "data",
        ROOT_DIR / "data" / "state",
        ROOT_DIR / "data" / "debug",
        ROOT_DIR / "models",
    ]
    for d in required_dirs:
        ok, msg = _ensure_dir(d)
        _add(report, f"dir_{d.name}", "OK" if ok else "BLOCKED", msg)

    # 4. Config import
    cfg: Any = None
    try:
        sys.path.insert(0, str(ROOT_DIR))
        from config import CFG, Config  # noqa: E402
        cfg = CFG
        _add(report, "config_import", "OK", "config.py imported successfully")
    except Exception as exc:
        _add(report, "config_import", "BLOCKED", f"failed to import config: {exc}")
        # Cannot continue config-dependent checks
        report.verdict = "BLOCKED"
        report.summary = "Config import failed — runtime cannot boot."
        return report

    # 5. Important config paths resolve
    path_checks = [
        ("DATA_DIR", getattr(cfg, "DATA_DIR", None)),
        ("MEMORY_PATH", getattr(cfg, "MEMORY_PATH", None)),
        ("LLAMA_SERVER_EXE", getattr(cfg, "LLAMA_SERVER_EXE", None)),
        ("LLAMA_SERVER_URL", getattr(cfg, "LLAMA_SERVER_URL", None)),
        ("MODEL_PATH", getattr(cfg, "MODEL_PATH", None)),
    ]
    if hasattr(cfg, "KOKORO_DIR"):
        path_checks.append(("KOKORO_DIR", cfg.KOKORO_DIR))

    for name, val in path_checks:
        if val is None:
            _add(report, f"config_{name}", "WARN", f"{name} is None or missing")
        elif isinstance(val, Path):
            _add(report, f"config_{name}", "OK", f"{name} = {val}")
        else:
            _add(report, f"config_{name}", "OK", f"{name} = {val}")

    # 6. Llama server executable exists
    llama_exe = getattr(cfg, "LLAMA_SERVER_EXE", None)
    if llama_exe and isinstance(llama_exe, Path):
        if llama_exe.exists():
            _add(report, "llama_server_exe", "OK", f"found: {llama_exe}")
        else:
            _add(report, "llama_server_exe", "WARN", f"missing: {llama_exe}")
    else:
        _add(report, "llama_server_exe", "WARN", "LLAMA_SERVER_EXE not configured")

    # 7. Model path exists and is .gguf
    model_path = getattr(cfg, "MODEL_PATH", None)
    if model_path and isinstance(model_path, Path):
        if model_path.exists() and model_path.suffix.lower() == ".gguf":
            size_mb = round(model_path.stat().st_size / (1024 * 1024), 1)
            _add(report, "model_path", "OK", f"{model_path.name} ({size_mb} MB)")
        elif model_path.exists():
            _add(report, "model_path", "WARN", f"exists but not .gguf: {model_path}")
        else:
            _add(report, "model_path", "WARN", f"missing: {model_path}")
    else:
        _add(report, "model_path", "WARN", "MODEL_PATH not configured")

    # 8. LangGraph checkpoint parent exists or can be created
    checkpoint_path = getattr(cfg, "LANGGRAPH_CHECKPOINT_PATH", None)
    if checkpoint_path and isinstance(checkpoint_path, Path):
        parent = checkpoint_path.parent
        ok, msg = _ensure_dir(parent)
        _add(report, "langgraph_checkpoint_dir", "OK" if ok else "WARN", msg)
    else:
        _add(report, "langgraph_checkpoint_dir", "WARN", "LANGGRAPH_CHECKPOINT_PATH not configured")

    # 9. Runtime/private files are not tracked by Git
    risky_prefixes = [
        "data/debug/",
        "data/runtime/",
        "data/state/",
        "data/users/",
        "data/voice_embeddings/",
        "data/voice_calibration/",
        "data/vector_store/",
        ".claude/",
        ".codex/",
    ]
    tracked_risky: list[str] = []
    for prefix in risky_prefixes:
        tracked_risky.extend(_tracked_files_with_prefix(prefix))
    if tracked_risky:
        _add(report, "git_tracked_risky", "BLOCKED", f"{len(tracked_risky)} tracked risky files (e.g. {tracked_risky[0]})")
    else:
        _add(report, "git_tracked_risky", "OK", "no tracked risky/private/runtime files")

    # 10. Warn if risky runtime leftovers exist
    debug_dir = ROOT_DIR / "data" / "debug"
    leftover_warns: list[str] = []
    if debug_dir.exists():
        try:
            entries = list(debug_dir.iterdir())
            if len(entries) > 20:
                leftover_warns.append(f"data/debug has {len(entries)} files")
            for f in entries:
                if f.is_file() and f.stat().st_size > 50 * 1024 * 1024:
                    leftover_warns.append(f"large debug file: {f.name} ({round(f.stat().st_size/1024/1024,1)} MB)")
        except OSError:
            pass

    benchmark_results = ROOT_DIR / "data" / "benchmarks" / "results"
    if benchmark_results.exists():
        try:
            count = len(list(benchmark_results.iterdir()))
            if count > 50:
                leftover_warns.append(f"data/benchmarks/results has {count} files")
        except OSError:
            pass

    if leftover_warns:
        _add(report, "runtime_leftovers", "WARN", "; ".join(leftover_warns))
    else:
        _add(report, "runtime_leftovers", "OK", "no suspicious runtime leftovers")

    # ---------- Verdict ----------
    has_blocked = any(c.status == "BLOCKED" for c in report.checks)
    has_warn = any(c.status == "WARN" for c in report.checks)

    if has_blocked:
        report.verdict = "BLOCKED"
        blocked_names = [c.name for c in report.checks if c.status == "BLOCKED"]
        report.summary = f"Blocked by: {', '.join(blocked_names)}"
    elif has_warn:
        report.verdict = "WARN"
        warn_names = [c.name for c in report.checks if c.status == "WARN"]
        report.summary = f"Warnings: {', '.join(warn_names)}"
    else:
        report.verdict = "OK"
        report.summary = "All checks passed."

    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_human(report: SelfCheckReport) -> None:
    print(f"Python       : {report.python_version}")
    print(f"Executable   : {report.python_executable}")
    print(f"Repo root    : {report.repo_root}")
    print()
    for check in report.checks:
        status_icon = {"OK": "[OK]", "WARN": "[WARN]", "BLOCKED": "[BLOCKED]"}.get(check.status, "[?]")
        print(f"{status_icon} {check.name}: {check.status} - {check.message}")
    print()
    print(f"Verdict : {report.verdict}")
    print(f"Summary : {report.summary}")


def print_json(report: SelfCheckReport) -> None:
    payload = asdict(report)
    print(json.dumps(payload, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Piper startup self-check")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    report = run_self_check()
    if args.json:
        print_json(report)
    else:
        print_human(report)

    return 2 if report.verdict == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
