#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent


@dataclass(frozen=True)
class SmokeTest:
    path: Path
    category: str

    @property
    def filename(self) -> str:
        return self.path.name


def _categorize(filename: str) -> str:
    stem = filename.lower()
    if stem.startswith("file_"):
        return "FILE_WORK"
    if stem.startswith("computer_use_"):
        return "COMPUTER_USE"
    if stem.startswith("code_"):
        return "CODE"
    if stem.startswith("search_"):
        return "SEARCH"
    if stem.startswith("memory_"):
        return "MEMORY"
    if stem.startswith(("route_", "lookup_source_", "followup_")):
        return "ROUTING"
    if stem.startswith("persona_"):
        return "PERSONA"
    if stem.startswith("undo_"):
        return "UNDO"
    if stem.startswith("codex_"):
        return "CODEX"
    if stem.startswith("knowledge_"):
        return "KNOWLEDGE"
    if stem.startswith("vision_"):
        return "VISION"
    if stem.startswith("stream") or stem.startswith("tts_"):
        return "STREAMING"
    if stem.startswith("stats_"):
        return "STATS"
    if stem.startswith("conversation_"):
        return "CONVERSATION"
    if stem.startswith("context_pack_"):
        return "CONTEXT"
    if stem.startswith("change_journal_"):
        return "JOURNAL"
    if stem.startswith("executor_budget_"):
        return "EXECUTOR"
    if stem.startswith("proactive_"):
        return "PROACTIVE"
    if stem.startswith("reminder_"):
        return "REMINDERS"
    if stem.startswith("turn_explanation_"):
        return "EXPLANATION"
    if stem.startswith("state_"):
        return "STATE"
    if stem.startswith("skill_"):
        return "SKILLS"
    if stem.startswith("world_model_"):
        return "WORLD_MODEL"
    if stem.startswith("document_") or "doc_focus" in stem:
        return "DOCUMENT"
    return "GENERAL"


def discover_tests() -> list[SmokeTest]:
    tests = [
        SmokeTest(path=path, category=_categorize(path.name))
        for path in sorted(SCRIPTS_DIR.glob("*_smoke_test.py"))
    ]
    return tests


def filter_tests(
    tests: Iterable[SmokeTest],
    *,
    category: str | None,
    patterns: list[str],
    skip_harness: bool,
) -> list[SmokeTest]:
    selected = list(tests)
    if skip_harness:
        selected = [test for test in selected if "harness" not in test.filename.lower()]
    if category:
        category_upper = category.upper()
        selected = [test for test in selected if test.category == category_upper]
    if patterns:
        selected = [
            test
            for test in selected
            if any(fnmatch.fnmatch(test.filename, pattern) for pattern in patterns)
        ]
    return selected


def _run_verbose(command: list[str], timeout_s: float) -> tuple[int | None, float, str, bool]:
    start = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    output_lines: list[str] = []

    def _reader() -> None:
        for line in process.stdout:
            output_lines.append(line)
            print(line, end="")

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        return_code = None
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass
        reader.join(timeout=1.0)

    elapsed = time.perf_counter() - start
    return return_code, elapsed, "".join(output_lines), timed_out


def _run_quiet(command: list[str], timeout_s: float) -> tuple[int | None, float, str, bool]:
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        elapsed = time.perf_counter() - start
        return completed.returncode, elapsed, completed.stdout + completed.stderr, False
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - start
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return None, elapsed, stdout + stderr, True


def run_test(
    test: SmokeTest,
    *,
    timeout_s: float,
    verbose: bool,
) -> tuple[str, float, int | None]:
    command = [sys.executable, str(test.path)]
    if verbose:
        return_code, elapsed, _output, timed_out = _run_verbose(command, timeout_s)
    else:
        return_code, elapsed, _output, timed_out = _run_quiet(command, timeout_s)

    if timed_out:
        return "TIMEOUT", elapsed, None
    if return_code == 0:
        return "PASS", elapsed, 0
    return "FAIL", elapsed, return_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and run Piper smoke tests from scripts/.",
    )
    parser.add_argument("patterns", nargs="*", help="fnmatch patterns against smoke test filenames")
    parser.add_argument("--category", help="Run only tests in this category")
    parser.add_argument("--list", action="store_true", dest="list_only", help="List discovered tests and categories, then exit")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failure or timeout")
    parser.add_argument("--skip-harness", action="store_true", help="Exclude tests whose filename contains 'harness'")
    parser.add_argument("--verbose", "-v", action="store_true", help="Stream per-test stdout/stderr")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-test timeout in seconds (default: 60)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    tests = filter_tests(
        discover_tests(),
        category=args.category,
        patterns=args.patterns,
        skip_harness=bool(args.skip_harness),
    )

    if args.list_only:
        for test in tests:
            print(f"{test.category:<14} {test.filename}", flush=True)
        return 0

    if not tests:
        print("No smoke tests matched the requested filters.", flush=True)
        return 1

    passed = 0
    failed = 0
    timed_out = 0

    for test in tests:
        if args.verbose:
            print(f"==> Running {test.filename} [{test.category}]", flush=True)
        status, elapsed, return_code = run_test(test, timeout_s=args.timeout, verbose=args.verbose)
        if status == "PASS":
            passed += 1
            print(f"[PASS] {test.filename} ({elapsed:.1f}s)", flush=True)
        elif status == "TIMEOUT":
            timed_out += 1
            print(f"[TIMEOUT] {test.filename} ({elapsed:.1f}s)", flush=True)
        else:
            failed += 1
            exit_code = "unknown" if return_code is None else str(return_code)
            print(f"[FAIL] {test.filename} ({elapsed:.1f}s) — exit code {exit_code}", flush=True)

        if args.fail_fast and status != "PASS":
            break

    print(f"Summary: {passed} passed, {failed} failed, {timed_out} timed out", flush=True)
    return 0 if failed == 0 and timed_out == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
