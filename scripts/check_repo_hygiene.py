#!/usr/bin/env python3
"""Repo hygiene checker — deterministic local check for junk/private/runtime files.

Safe to run anytime. Does not mutate files.
Exit codes:
    0 = SHIP
    1 = WARN
    2 = BLOCKED
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WARN_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB
BLOCK_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

RISKY_PATH_PREFIXES = (
    "data/debug/",
    "data/runtime/",
    "data/state/",
    "data/users/",
    "data/voice_embeddings/",
    "data/voice_calibration/",
    "data/vector_store/",
)

RISKY_NAME_PREFIXES = (
    ".codex",
    ".claude",
)

BINARY_ARTIFACT_SUFFIXES = (
    ".gguf",
    ".safetensors",
    ".pt",
    ".pth",
    ".onnx",
    ".bin",
)

SCRATCH_PATTERNS = (
    "start_comfy.ps1",
    "tmp_*.py",
    "tmp_*.txt",
    "out.txt",
    "out2.txt",
    "testfile.txt",
    "scripts/debug_*.py",
    "scripts/check_checkpoint*.py",
    "scripts/phase8_debug_*.py",
    "scripts/phase8_approval_only.py",
)

# Paths that are allowed to contain large files
LARGE_FILE_ALLOWLIST_PREFIXES = (
    ".venv/",
    "models/",
    "runtime/",
    "data/workspace/",
    "data/vector_store/",
    "data/debug/",
)

# Known top-level directories — used for typo detection
KNOWN_TOP_LEVEL_DIRS = {
    "AGENTS",
    "core",
    "data",
    "docs",
    "llm",
    "memory",
    "models",
    "notes",
    "runtime",
    "scripts",
    "tests",
    "tools",
    "ui",
}

# Text file extensions to scan for absolute paths
TEXT_EXTENSIONS = (
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".bat",
    ".ps1",
    ".sh",
)

ABSOLUTE_PATH_PATTERNS = (
    r"C:\\Projects\\Piper",
    r"C:\\Users\\Hawk Gaming",
    r"/mnt/c/Projects/Piper",
    r"/mnt/c/Users/Hawk Gaming",
    r"/home/[^/]+/Piper",
)


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


def tracked_files() -> list[str]:
    out = _git("ls-files")
    return [line for line in out.splitlines() if line.strip()]


def git_status_porcelain() -> list[str]:
    out = _git("status", "--porcelain")
    return [line for line in out.splitlines() if line.strip()]


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
# Pattern matching
# ---------------------------------------------------------------------------

def is_risky_path(path: str) -> bool:
    return any(path.startswith(p) for p in RISKY_PATH_PREFIXES)


def is_risky_name_prefix(path: str) -> bool:
    return any(path.startswith(p) for p in RISKY_NAME_PREFIXES)


def is_binary_artifact(path: str) -> bool:
    return any(path.endswith(s) for s in BINARY_ARTIFACT_SUFFIXES)


def is_scratch_file(path: str) -> bool:
    name = Path(path).name
    return any(
        fnmatch(path, pat) or fnmatch(name, pat.split("/")[-1])
        for pat in SCRATCH_PATTERNS
    )


def is_large_file_allowed(path: str) -> bool:
    return any(path.startswith(p) for p in LARGE_FILE_ALLOWLIST_PREFIXES)


def file_size(path: str) -> int:
    try:
        return os.path.getsize(ROOT_DIR / path)
    except OSError:
        return 0


def is_text_file(path: str) -> bool:
    return any(path.endswith(ext) for ext in TEXT_EXTENSIONS)


def find_absolute_paths(path: str) -> list[str]:
    """Scan a text file for local absolute paths. Returns matched patterns."""
    try:
        content = (ROOT_DIR / path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    found: list[str] = []
    for pat in ABSOLUTE_PATH_PATTERNS:
        if pat in content:
            found.append(pat)
    return found


# ---------------------------------------------------------------------------
# .gitignore parsing for "ignored but tracked" detection
# ---------------------------------------------------------------------------

def load_gitignore_patterns() -> list[str]:
    gitignore = ROOT_DIR / ".gitignore"
    if not gitignore.exists():
        return []
    patterns: list[str] = []
    for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def is_ignored_by_gitignore(path: str, patterns: list[str]) -> bool:
    """Rough check: does path match any gitignore pattern?"""
    for pat in patterns:
        # Simple pattern handling
        if pat.endswith("/"):
            # Directory pattern
            if path.startswith(pat.rstrip("/") + "/") or path == pat.rstrip("/"):
                return True
        elif "/" in pat:
            # Path-specific pattern
            if fnmatch(path, pat) or path.startswith(pat.rstrip("*")):
                return True
        else:
            # Generic name pattern
            if fnmatch(Path(path).name, pat):
                return True
            # Also check parent dir + name for generic patterns
            if fnmatch(path, "*/" + pat):
                return True
    return False


# ---------------------------------------------------------------------------
# Suspicious typo detection
# ---------------------------------------------------------------------------

def find_suspicious_typos(paths: Iterable[str]) -> list[tuple[str, str]]:
    """Find paths that look like typos of known top-level dirs."""
    results: list[tuple[str, str]] = []
    for path in paths:
        parts = path.split("/")
        if not parts:
            continue
        first = parts[0]
        # Skip exact matches and empty strings
        if first in KNOWN_TOP_LEVEL_DIRS or not first:
            continue
        # Check for single-character deletion of a known dir
        for known in KNOWN_TOP_LEVEL_DIRS:
            if len(known) > 1 and len(first) == len(known) - 1:
                # Check if first is known with one char removed
                for i in range(len(known)):
                    if known[:i] + known[i + 1 :] == first:
                        results.append((path, f"'{first}' looks like '{known}' with one letter missing"))
                        break
            elif len(known) > 2 and len(first) == len(known):
                # Check for single-character substitution
                diff = sum(a != b for a, b in zip(known, first))
                if diff == 1:
                    results.append((path, f"'{first}' looks like a typo of '{known}'"))
                    break
    return results


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class HygieneResult:
    tracked_risky_files: list[str] = field(default_factory=list)
    tracked_binary_artifacts: list[str] = field(default_factory=list)
    tracked_large_files: list[tuple[str, float]] = field(default_factory=list)
    ignored_but_tracked: list[str] = field(default_factory=list)
    untracked_risky_files: list[str] = field(default_factory=list)
    untracked_scratch_files: list[str] = field(default_factory=list)
    staged_risky_files: list[str] = field(default_factory=list)
    suspicious_typos: list[tuple[str, str]] = field(default_factory=list)
    absolute_paths_in_text: list[tuple[str, list[str]]] = field(default_factory=list)
    verdict: str = "SHIP"
    reason: str = ""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def run_hygiene() -> HygieneResult:
    result = HygieneResult()

    all_tracked = tracked_files()
    status_lines = git_status_porcelain()
    staged, unstaged, untracked = parse_status(status_lines)
    all_working_tree = set(staged) | set(unstaged) | set(untracked)
    gitignore_patterns = load_gitignore_patterns()

    # 1. Tracked risky/private/runtime files
    for path in all_tracked:
        if is_risky_path(path) or is_risky_name_prefix(path):
            result.tracked_risky_files.append(path)
        if is_binary_artifact(path):
            result.tracked_binary_artifacts.append(path)

    # 2. Large files among tracked files
    for path in all_tracked:
        if is_large_file_allowed(path):
            continue
        size = file_size(path)
        if size > BLOCK_SIZE_BYTES:
            result.tracked_large_files.append((path, round(size / (1024 * 1024), 2)))

    # 3. Ignored but tracked
    for path in all_tracked:
        if is_ignored_by_gitignore(path, gitignore_patterns):
            result.ignored_but_tracked.append(path)

    # 4. Untracked risky files
    for path in untracked:
        if is_risky_path(path) or is_risky_name_prefix(path) or is_binary_artifact(path):
            result.untracked_risky_files.append(path)

    # 5. Untracked scratch files
    for path in all_working_tree:
        if is_scratch_file(path):
            result.untracked_scratch_files.append(path)

    # 6. Staged risky files
    for path in staged:
        if is_risky_path(path) or is_risky_name_prefix(path) or is_binary_artifact(path):
            result.staged_risky_files.append(path)

    # 7. Suspicious typos
    result.suspicious_typos = find_suspicious_typos(all_tracked)

    # 8. Absolute paths in text files
    for path in all_tracked:
        if is_text_file(path):
            found = find_absolute_paths(path)
            if found:
                result.absolute_paths_in_text.append((path, found))

    # ---------- Verdict ----------
    if result.tracked_risky_files or result.tracked_binary_artifacts or result.staged_risky_files:
        result.verdict = "BLOCKED"
        reasons: list[str] = []
        if result.tracked_risky_files:
            reasons.append(f"tracked risky files: {len(result.tracked_risky_files)}")
        if result.tracked_binary_artifacts:
            reasons.append(f"tracked binary artifacts: {len(result.tracked_binary_artifacts)}")
        if result.staged_risky_files:
            reasons.append(f"staged risky files: {len(result.staged_risky_files)}")
        if result.tracked_large_files:
            reasons.append(f"tracked large files: {len(result.tracked_large_files)}")
        result.reason = "; ".join(reasons)
    elif (
        result.untracked_risky_files
        or result.untracked_scratch_files
        or result.ignored_but_tracked
        or result.suspicious_typos
        or result.absolute_paths_in_text
        or result.tracked_large_files
    ):
        result.verdict = "WARN"
        reasons = []
        if result.untracked_risky_files:
            reasons.append(f"untracked risky files: {len(result.untracked_risky_files)}")
        if result.untracked_scratch_files:
            reasons.append(f"untracked scratch files: {len(result.untracked_scratch_files)}")
        if result.ignored_but_tracked:
            reasons.append(f"ignored but tracked: {len(result.ignored_but_tracked)}")
        if result.suspicious_typos:
            reasons.append(f"suspicious typos: {len(result.suspicious_typos)}")
        if result.absolute_paths_in_text:
            reasons.append(f"absolute paths in text: {len(result.absolute_paths_in_text)}")
        if result.tracked_large_files:
            reasons.append(f"large files: {len(result.tracked_large_files)}")
        result.reason = "; ".join(reasons)
    else:
        result.verdict = "SHIP"
        result.reason = "No hygiene issues found."

    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

EXIT_CODES = {
    "SHIP": 0,
    "WARN": 1,
    "BLOCKED": 2,
}


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "  (none)"
    return "\n".join(f"  - {i}" for i in items)


def _fmt_size_list(items: list[tuple[str, float]]) -> str:
    if not items:
        return "  (none)"
    return "\n".join(f"  - {path} ({size_mb} MB)" for path, size_mb in items)


def _fmt_tuple_list(items: list[tuple[str, str]]) -> str:
    if not items:
        return "  (none)"
    return "\n".join(f"  - {path} ({reason})" for path, reason in items)


def _fmt_abs_path_list(items: list[tuple[str, list[str]]]) -> str:
    if not items:
        return "  (none)"
    lines: list[str] = []
    for path, patterns in items:
        for pat in patterns:
            lines.append(f"  - {path}: contains '{pat}'")
    return "\n".join(lines)


def print_human(result: HygieneResult) -> None:
    print("=== Tracked risky/private/runtime files ===")
    print(_fmt_list(result.tracked_risky_files))
    print()
    print("=== Tracked binary artifacts ===")
    print(_fmt_list(result.tracked_binary_artifacts))
    print()
    print("=== Tracked large files (>10 MB) ===")
    print(_fmt_size_list(result.tracked_large_files))
    print()
    print("=== Ignored but tracked ===")
    print(_fmt_list(result.ignored_but_tracked))
    print()
    print("=== Untracked risky files ===")
    print(_fmt_list(result.untracked_risky_files))
    print()
    print("=== Untracked scratch files ===")
    print(_fmt_list(result.untracked_scratch_files))
    print()
    print("=== Staged risky files ===")
    print(_fmt_list(result.staged_risky_files))
    print()
    print("=== Suspicious typos ===")
    print(_fmt_tuple_list(result.suspicious_typos))
    print()
    print("=== Absolute paths in text files ===")
    print(_fmt_abs_path_list(result.absolute_paths_in_text))
    print()
    print(f"Verdict : {result.verdict}")
    print(f"Reason  : {result.reason}")


def print_json(result: HygieneResult) -> None:
    payload = asdict(result)
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Piper repo hygiene checker")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    result = run_hygiene()
    if args.json:
        print_json(result)
    else:
        print_human(result)

    return EXIT_CODES.get(result.verdict, 1)


if __name__ == "__main__":
    raise SystemExit(main())
