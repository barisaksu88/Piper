#!/usr/bin/env python3
"""Aggregate deterministic Search v1 smoke gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SmokeResult:
    script: str
    passed: bool
    returncode: int
    stdout_tail: str
    stderr_tail: str


def _tail(text: str, limit: int = 2000) -> str:
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[-limit:]


def _run_script(script: str) -> SmokeResult:
    proc = subprocess.run(
        [sys.executable, str(ROOT / script), "--json"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    return SmokeResult(
        script=script,
        passed=proc.returncode == 0,
        returncode=proc.returncode,
        stdout_tail=_tail(proc.stdout),
        stderr_tail=_tail(proc.stderr),
    )


def run_smoke() -> dict[str, object]:
    scripts = [
        "scripts/search_topic_resolver_smoke_test.py",
        "scripts/search_topic_route_normalizer_smoke_test.py",
        "scripts/search_prompt_isolation_smoke_test.py",
        "scripts/search_tool_searxng_depth_smoke_test.py",
        "scripts/search_tool_fallback_smoke_test.py",
        "scripts/searxng_backend_smoke_test.py",
    ]
    results = [_run_script(script) for script in scripts]
    return {
        "success": all(result.passed for result in results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "results": [asdict(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Search v1 smoke checks.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    args = parser.parse_args()

    report = run_smoke()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report['success']}")
        for result in report["results"]:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"{status}: {result['script']}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
