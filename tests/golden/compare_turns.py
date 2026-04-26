"""tests/golden/compare_turns.py

Golden Harness Comparator — Phase 0 of the LangGraph migration.

Compares two golden turn JSON files (or two corpora) using the semantic
comparison rules from the migration spec.  Byte-level equality is NOT used;
instead each field has its own comparison rule.

DISCIPLINE RULES:
1. ONLY implement comparison logic. Do NOT touch orchestrator_phases.py.
2. Normalization rules must match spec Amendment 1 exactly.
3. The comparison report is MANDATORY.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Normalization (must stay in sync with record_piper_turns.py)
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_TEMP_PATH_RE = re.compile(r"[\\/]temp[\\/][^\\/]+|[\\/]tmp[\\/][^\\/]+", re.IGNORECASE)


def looks_like_random_id(value: str) -> bool:
    return bool(_UUID_RE.match(str(value or "")))


def normalize_temp_path(path: str) -> str:
    return _TEMP_PATH_RE.sub("/tmp/<TEMP>", str(path or ""))


def normalize_tool_result(result: Any) -> Any:
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return [normalize_tool_result(item) for item in result]
    if not isinstance(result, dict):
        return result
    result = copy.deepcopy(result)
    for key in ["timestamp", "created_at", "modified_at", "accessed_at", "ts", "time"]:
        if key in result:
            result[key] = "<TIMESTAMP>"
    if "id" in result and looks_like_random_id(str(result["id"])):
        result["id"] = "<UUID>"
    if "path" in result and isinstance(result["path"], str):
        result["path"] = normalize_temp_path(result["path"])
    if "paths" in result and isinstance(result["paths"], list):
        result["paths"] = [normalize_temp_path(str(p)) for p in result["paths"]]
    for key in ["result", "data", "payload", "detail"]:
        if key in result:
            result[key] = normalize_tool_result(result[key])
    return result


def _normalize_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip arg variance from tool calls for stable comparison."""
    out: list[dict[str, Any]] = []
    for call in calls:
        tool = str(call.get("tool") or "")
        # Normalize whitespace in the tool tag
        tool = re.sub(r"\s+", " ", tool).strip()
        out.append({"tool": tool})
    return out


def _normalize_stage_transitions(stages: list[str]) -> list[str]:
    return [str(s).strip().upper() for s in stages]


# ---------------------------------------------------------------------------
# Field-level comparators (spec Amendment 1)
# ---------------------------------------------------------------------------

def _compare_exact(a: Any, b: Any, path: str) -> list[str]:
    if a != b:
        return [f"{path}: expected {a!r}, got {b!r}"]
    return []


def _compare_set_equality(a: list[str], b: list[str], path: str) -> list[str]:
    sa = set(str(x) for x in a)
    sb = set(str(x) for x in b)
    if sa != sb:
        missing = sa - sb
        extra = sb - sa
        msgs = []
        if missing:
            msgs.append(f"{path}: missing {missing}")
        if extra:
            msgs.append(f"{path}: extra {extra}")
        return msgs
    return []


def _compare_tool_calls(a: list[dict], b: list[dict], path: str) -> list[str]:
    na = _normalize_tool_calls(a)
    nb = _normalize_tool_calls(b)
    if len(na) != len(nb):
        return [f"{path}: count mismatch {len(na)} vs {len(nb)}"]
    errs: list[str] = []
    for i, (ca, cb) in enumerate(zip(na, nb)):
        errs.extend(_compare_exact(ca, cb, f"{path}[{i}]"))
    return errs


def _compare_tool_results(a: list[Any], b: list[Any], path: str) -> list[str]:
    na = [normalize_tool_result(x) for x in a]
    nb = [normalize_tool_result(x) for x in b]
    if len(na) != len(nb):
        return [f"{path}: count mismatch {len(na)} vs {len(nb)}"]
    errs: list[str] = []
    for i, (ra, rb) in enumerate(zip(na, nb)):
        if ra != rb:
            errs.append(f"{path}[{i}]: {ra!r} != {rb!r}")
    return errs


def _compare_pre_persona_output(a: str, b: str, path: str) -> list[str]:
    # Exact match on the structured content before persona wrapping
    sa = str(a or "").strip()
    sb = str(b or "").strip()
    if sa != sb:
        return [f"{path}: mismatch\n--- expected ---\n{sa}\n--- got ---\n{sb}"]
    return []


def _compare_persona_output(a: str, b: str, path: str) -> list[str]:
    # Skip comparison — persona output is non-deterministic
    # (spec Amendment 2)
    return []


def _compare_checkpoint_id(a: str, b: str, path: str) -> list[str]:
    # Presence check only
    ha = bool(str(a or "").strip())
    hb = bool(str(b or "").strip())
    if ha != hb:
        return [f"{path}: presence mismatch {ha} vs {hb}"]
    return []


# ---------------------------------------------------------------------------
# Turn comparison engine
# ---------------------------------------------------------------------------

@dataclass
class TurnComparison:
    turn_id: str
    case_name: str
    mismatches: list[str] = field(default_factory=list)
    passed: bool = False


def compare_turns(expected: dict[str, Any], actual: dict[str, Any]) -> TurnComparison:
    """Compare two golden turn dicts using semantic rules."""
    case_name = str(expected.get("case_name", "unknown"))
    turn_id = str(expected.get("turn_id", "unknown"))
    errs: list[str] = []

    # route_decision — exact match
    errs.extend(_compare_exact(expected.get("route_decision"), actual.get("route_decision"), "route_decision"))

    # stage_transitions — exact ordered match
    errs.extend(
        _compare_exact(
            _normalize_stage_transitions(expected.get("stage_transitions") or []),
            _normalize_stage_transitions(actual.get("stage_transitions") or []),
            "stage_transitions",
        )
    )

    # tool_calls — exact match (args normalized)
    errs.extend(
        _compare_tool_calls(
            expected.get("tool_calls") or [],
            actual.get("tool_calls") or [],
            "tool_calls",
        )
    )

    # tool_results — normalize then compare
    errs.extend(
        _compare_tool_results(
            expected.get("tool_results") or [],
            actual.get("tool_results") or [],
            "tool_results",
        )
    )

    # pre_persona_output — exact match
    errs.extend(
        _compare_pre_persona_output(
            expected.get("pre_persona_output") or "",
            actual.get("pre_persona_output") or "",
            "pre_persona_output",
        )
    )

    # persona_output — skip comparison (reference only)
    errs.extend(
        _compare_persona_output(
            expected.get("persona_output") or "",
            actual.get("persona_output") or "",
            "persona_output",
        )
    )

    # workspace_state — set equality
    errs.extend(
        _compare_set_equality(
            expected.get("workspace_state") or [],
            actual.get("workspace_state") or [],
            "workspace_state",
        )
    )

    # verification_passed — exact match
    errs.extend(
        _compare_exact(
            bool(expected.get("verification_passed")),
            bool(actual.get("verification_passed")),
            "verification_passed",
        )
    )

    # checkpoint_id — presence check only
    errs.extend(
        _compare_checkpoint_id(
            expected.get("checkpoint_id") or "",
            actual.get("checkpoint_id") or "",
            "checkpoint_id",
        )
    )

    return TurnComparison(
        turn_id=turn_id,
        case_name=case_name,
        mismatches=errs,
        passed=len(errs) == 0,
    )


# ---------------------------------------------------------------------------
# Corpus comparison
# ---------------------------------------------------------------------------

@dataclass
class CorpusComparison:
    total: int
    passed: int
    failed: int
    missing: list[str]
    extra: list[str]
    turn_results: list[TurnComparison]


def load_corpus(corpus_dir: Path) -> dict[str, dict[str, Any]]:
    turns: dict[str, dict[str, Any]] = {}
    for path in sorted(corpus_dir.glob("turn_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            case_name = str(data.get("case_name", path.stem))
            turns[case_name] = data
        except Exception as exc:
            print(f"[WARN] Failed to load {path}: {exc}")
    return turns


def compare_corpora(expected_dir: Path, actual_dir: Path) -> CorpusComparison:
    expected = load_corpus(expected_dir)
    actual = load_corpus(actual_dir)

    results: list[TurnComparison] = []
    missing: list[str] = []
    extra: list[str] = []

    for case_name in sorted(expected):
        if case_name not in actual:
            missing.append(case_name)
            continue
        result = compare_turns(expected[case_name], actual[case_name])
        results.append(result)

    for case_name in sorted(actual):
        if case_name not in expected:
            extra.append(case_name)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed) + len(missing)
    total = len(results) + len(missing)

    return CorpusComparison(
        total=total,
        passed=passed,
        failed=failed,
        missing=missing,
        extra=extra,
        turn_results=results,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Compare golden corpus turns.")
    parser.add_argument("expected", type=Path, help="Expected corpus directory")
    parser.add_argument("actual", type=Path, help="Actual corpus directory")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    report = compare_corpora(args.expected, args.actual)

    if args.json:
        payload = {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "missing": report.missing,
            "extra": report.extra,
            "turns": [
                {
                    "turn_id": r.turn_id,
                    "case_name": r.case_name,
                    "passed": r.passed,
                    "mismatches": r.mismatches,
                }
                for r in report.turn_results
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Total:   {report.total}")
        print(f"Passed:  {report.passed}")
        print(f"Failed:  {report.failed}")
        if report.missing:
            print(f"Missing: {', '.join(report.missing)}")
        if report.extra:
            print(f"Extra:   {', '.join(report.extra)}")
        for r in report.turn_results:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.case_name} ({r.turn_id})")
            for m in r.mismatches:
                print(f"         -> {m}")

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
