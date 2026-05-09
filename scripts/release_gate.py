#!/usr/bin/env python3
"""Release gate — deterministic local reviewability check for Piper branches.

Safe to run anytime. Does not mutate files.
Exit codes:
    0 = SHIP
    1 = NEEDS_EVIDENCE
    2 = BLOCKED
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Risky / private / runtime file patterns
# ---------------------------------------------------------------------------
RISKY_PATH_PREFIXES = (
    "data/debug/",
    "data/voice_embeddings/",
    "data/voice_calibration/",
    "data/runtime/",
    "data/state/",
    "data/users/",
)

RISKY_NAME_PREFIXES = (
    ".codex",
    ".claude",
)

RISKY_NAME_SUFFIXES = (
    ".gguf",
    ".bin",
    ".pt",
    ".pth",
    ".onnx",
    ".safetensors",
)

RISKY_NAME_PATTERNS = (
    "tmp_*.txt",
    "tmp_*.py",
    "out.txt",
    "out2.txt",
    "testfile.txt",
)

RISKY_SCRIPT_PATTERNS = (
    "scripts/debug_*.py",
    "scripts/check_checkpoint*.py",
    "scripts/phase8_debug_*.py",
)

# ---------------------------------------------------------------------------
# Domain mapping: path glob / prefix / file name -> domain label
# ---------------------------------------------------------------------------
DOMAIN_RULES: list[tuple[tuple[str, ...], str]] = [
    # voice identity
    (("core/voice_recognition.py", "tools/stt.py", "memory/user_runtime.py",
      "data/prompts/instructions.txt", "scripts/voice_", "scripts/enroll_voice.py",
      "data/voice_embeddings/", "data/voice_calibration/"), "voice identity"),
    # UI / controller
    (("ui/",), "UI/controller"),
    # routing / orchestrator / executor
    (("core/orchestrator.py", "core/orchestrator_phases.py", "core/orchestrator_graph",
      "core/graph_nodes.py", "core/executor.py", "core/routing/",
      "core/planner_boundary.py"), "routing/orchestrator/executor"),
    # tools / scripts
    (("tools/", "scripts/"), "tools/scripts"),
    # memory / privacy / user runtime
    (("memory/", "core/prompt_context.py"), "memory/privacy/user runtime"),
    # docs
    (("docs/",), "docs"),
]

# ---------------------------------------------------------------------------
# Smoke-test recommendations per domain
# ---------------------------------------------------------------------------
SMOKE_MAP: dict[str, list[str]] = {
    "voice identity": [
        "scripts/voice_identity_drift_smoke_test.py --json",
        "scripts/voice_identity_inference_smoke_test.py --json",
    ],
    "UI/controller": [
        "scripts/user_runtime_smoke_test.py --json",
        "scripts/code_session_smoke_test.py --json",
    ],
    "routing/orchestrator/executor": [
        "scripts/piper_graph_smoke_test.py --json",
        "scripts/orchestrator_graph_smoke_test.py --json",
        "scripts/code_session_smoke_test.py --json",
    ],
    "tools/scripts": [
        "scripts/file_edit_smoke_test.py --json",
        "scripts/file_lookup_smoke_test.py --json",
        "scripts/file_crud_smoke_test.py --json",
        "scripts/file_chaos_test.py --json",
    ],
    "memory/privacy/user runtime": [
        "scripts/user_runtime_smoke_test.py --json",
        "scripts/adversarial_safety_check.py --json",
    ],
    "docs": [
        "python -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts",
    ],
}

HIGH_RISK_DOMAINS = {
    "voice identity",
    "UI/controller",
    "routing/orchestrator/executor",
    "memory/privacy/user runtime",
}


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT_DIR), *args],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_ok(*args: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT_DIR), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout.strip()


def current_branch() -> str:
    return _git("branch", "--show-current")


def is_main(branch: str) -> bool:
    return branch in ("main", "master")


def git_status_porcelain() -> list[str]:
    out = _git("status", "--porcelain")
    return [line for line in out.splitlines() if line.strip()]


def changed_files_against_main() -> list[str]:
    """Return files changed on current branch vs main, if main exists."""
    ok, out = _git_ok("diff", "--name-only", "main...HEAD")
    if ok:
        return [line for line in out.splitlines() if line.strip()]
    # fallback: try origin/main
    ok, out = _git_ok("diff", "--name-only", "origin/main...HEAD")
    if ok:
        return [line for line in out.splitlines() if line.strip()]
    return []


def parse_status(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (staged, unstaged, untracked) file paths."""
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in lines:
        if len(line) < 3:
            continue
        xy = line[:2]
        path = line[3:]
        # handle rename format "R   old -> new"
        if " -> " in path:
            path = path.split(" -> ")[-1]
        if xy == "??":
            untracked.append(path)
        elif xy[0] != " ":
            staged.append(path)
        elif xy[1] != " ":
            unstaged.append(path)
    return staged, unstaged, untracked


# ---------------------------------------------------------------------------
# Risk & domain detection
# ---------------------------------------------------------------------------

def _matches_any(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(p) for p in prefixes)


def _matches_suffixes(path: str, suffixes: tuple[str, ...]) -> bool:
    return any(path.endswith(s) for s in suffixes)


def _matches_name_patterns(path: str, patterns: tuple[str, ...]) -> bool:
    from fnmatch import fnmatch
    name = Path(path).name
    return any(fnmatch(name, pat.split("/")[-1]) for pat in patterns)


def _matches_path_patterns(path: str, patterns: tuple[str, ...]) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(path, pat) for pat in patterns)


def is_risky_file(path: str) -> bool:
    return (
        _matches_any(path, RISKY_PATH_PREFIXES)
        or _matches_any(path, RISKY_NAME_PREFIXES)
        or _matches_suffixes(path, RISKY_NAME_SUFFIXES)
        or _matches_name_patterns(path, RISKY_NAME_PATTERNS)
        or _matches_path_patterns(path, RISKY_SCRIPT_PATTERNS)
    )


def detect_domains(paths: Iterable[str]) -> set[str]:
    domains: set[str] = set()
    for path in paths:
        for prefixes, domain in DOMAIN_RULES:
            if any(path.startswith(p) for p in prefixes):
                domains.add(domain)
                break
    return domains


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    branch: str = ""
    is_main: bool = False
    dirty: bool = False
    staged_files: list[str] = field(default_factory=list)
    unstaged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    changed_vs_main: list[str] = field(default_factory=list)
    risky_staged: list[str] = field(default_factory=list)
    risky_untracked: list[str] = field(default_factory=list)
    risky_vs_main: list[str] = field(default_factory=list)
    touched_domains: list[str] = field(default_factory=list)
    high_risk_domains: list[str] = field(default_factory=list)
    recommended_smokes: list[str] = field(default_factory=list)
    verdict: str = "SHIP"
    reason: str = ""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def run_gate() -> GateResult:
    result = GateResult()
    result.branch = current_branch()
    result.is_main = is_main(result.branch)

    status_lines = git_status_porcelain()
    result.dirty = bool(status_lines)
    staged, unstaged, untracked = parse_status(status_lines)
    result.staged_files = staged
    result.unstaged_files = unstaged
    result.untracked_files = untracked

    result.changed_vs_main = changed_files_against_main()

    # Risky file detection across staged, untracked, and branch diff
    result.risky_staged = [p for p in staged if is_risky_file(p)]
    result.risky_untracked = [p for p in untracked if is_risky_file(p)]
    result.risky_vs_main = [p for p in result.changed_vs_main if is_risky_file(p)]

    # Domain detection from branch diff + working tree changes
    all_touched = set(result.changed_vs_main) | set(staged) | set(unstaged)
    domains = detect_domains(all_touched)
    result.touched_domains = sorted(domains)
    result.high_risk_domains = sorted(domains & HIGH_RISK_DOMAINS)

    # Smoke recommendations
    smokes: set[str] = set()
    for d in domains:
        smokes.update(SMOKE_MAP.get(d, []))
    result.recommended_smokes = sorted(smokes)

    # ---------- Verdict ----------
    # BLOCKED: risky files staged, or high-risk runtime work on main
    if result.risky_staged:
        result.verdict = "BLOCKED"
        result.reason = f"Risky/private/runtime files staged: {result.risky_staged}"
    elif result.is_main and result.high_risk_domains:
        result.verdict = "BLOCKED"
        result.reason = (
            f"High-risk runtime work on main (domains: {result.high_risk_domains}). "
            "Use a feature branch."
        )
    elif result.high_risk_domains:
        result.verdict = "NEEDS_EVIDENCE"
        result.reason = (
            f"High-risk domains changed ({result.high_risk_domains}). "
            "Run recommended smoke tests and capture evidence before review."
        )
    elif result.dirty:
        result.verdict = "NEEDS_EVIDENCE"
        result.reason = "Working tree is dirty (unstaged changes or untracked files)."
    else:
        result.verdict = "SHIP"
        result.reason = "No blockers, no required evidence missing."

    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

EXIT_CODES = {
    "SHIP": 0,
    "NEEDS_EVIDENCE": 1,
    "BLOCKED": 2,
}


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "  (none)"
    return "\n".join(f"  - {i}" for i in items)


def print_human(result: GateResult) -> None:
    print(f"Branch           : {result.branch}")
    print(f"Is main          : {result.is_main}")
    print(f"Dirty            : {result.dirty}")
    print(f"Staged files     : {len(result.staged_files)}")
    print(_fmt_list(result.staged_files))
    print(f"Unstaged files   : {len(result.unstaged_files)}")
    print(_fmt_list(result.unstaged_files))
    print(f"Untracked files  : {len(result.untracked_files)}")
    print(_fmt_list(result.untracked_files))
    print(f"Changed vs main  : {len(result.changed_vs_main)}")
    print(_fmt_list(result.changed_vs_main))
    print()
    print("Risky files staged    :", len(result.risky_staged))
    print(_fmt_list(result.risky_staged))
    print("Risky files untracked :", len(result.risky_untracked))
    print(_fmt_list(result.risky_untracked))
    print("Risky files vs main   :", len(result.risky_vs_main))
    print(_fmt_list(result.risky_vs_main))
    print()
    print("Touched domains       :", result.touched_domains or ["(none)"])
    print("High-risk domains     :", result.high_risk_domains or ["(none)"])
    print()
    print("Recommended smoke tests:")
    print(_fmt_list(result.recommended_smokes))
    print()
    print(f"Verdict : {result.verdict}")
    print(f"Reason  : {result.reason}")


def print_json(result: GateResult) -> None:
    # Convert to plain dict for JSON serialization
    payload = asdict(result)
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Piper release gate")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    result = run_gate()
    if args.json:
        print_json(result)
    else:
        print_human(result)

    return EXIT_CODES.get(result.verdict, 1)


if __name__ == "__main__":
    raise SystemExit(main())
