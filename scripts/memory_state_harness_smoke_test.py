from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import data_state_path  # noqa: E402
from AGENTS.harness.session import PiperHarness  # noqa: E402


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _state_blob(data_dir: Path) -> str:
    parts = []
    for name in ("knowledge.json", "world_model.json", "situational_state.json", "intent_state.json"):
        payload = _read_json(data_state_path(data_dir, name))
        parts.append(json.dumps(payload, ensure_ascii=False))
    return "\n".join(parts).lower()


def _world_model_has_active_works_on(data_dir: Path, target_text: str) -> bool:
    payload = _read_json(data_state_path(data_dir, "world_model.json")) or {}
    root_id = str(payload.get("root_entity_id") or "person:user")
    nodes = payload.get("nodes") or {}
    edges = payload.get("edges") or []
    target_lower = str(target_text or "").strip().lower()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("source") or "") != root_id:
            continue
        if str(edge.get("relation") or "").strip().lower() != "works_on":
            continue
        target_id = str(edge.get("target") or "").strip()
        node = nodes.get(target_id) or {}
        haystacks = [
            str(target_id),
            str(node.get("label") or ""),
            " ".join(str(alias or "") for alias in (node.get("aliases") or [])),
        ]
        if any(target_lower in hay.lower() for hay in haystacks):
            return True
    return False


def _turn_record(result, data_dir: Path) -> dict[str, Any]:
    return {
        "user_text": result.user_text,
        "assistant_text": result.assistant_text,
        "timed_out": result.timed_out,
        "errors": [event["payload"] for event in result.ui_events if event.get("kind") == "error"],
        "state_blob": _state_blob(data_dir),
        "has_active_works_on_catch": _world_model_has_active_works_on(data_dir, "catch the stars"),
    }


@dataclass(frozen=True)
class MemoryScenarioReport:
    name: str
    success: bool
    kept_data_dir: str
    signals: dict[str, Any]


def _run_turns(turns: list[str], *, timeout_s: float) -> tuple[list[dict[str, Any]], str]:
    harness = PiperHarness(
        persist_turns=False,
        enable_memory_learning=True,
        isolated_data=True,
        keep_data_copy=True,
    )
    boot = harness.start()
    records: list[dict[str, Any]] = []
    if not boot.ready:
        harness.close()
        return ([{"boot_error": True, "boot": boot.__dict__}], "")

    try:
        for text in turns:
            result = harness.send_text(text, timeout_s=timeout_s)
            records.append(_turn_record(result, harness.data_dir))
    finally:
        harness.close()
        kept = str(harness.kept_data_dir or "")
    return records, kept


def _direct_remove_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [err for turn in turns for err in turn.get("errors", [])]
    removal_reply = str(turns[1].get("assistant_text") or "").lower()
    post_reply = str(turns[2].get("assistant_text") or "").lower()
    removed = not bool(turns[-1].get("has_active_works_on_catch"))
    return {
        "pass": (
            not turns[1].get("timed_out")
            and not turns[2].get("timed_out")
            and bool(removal_reply.strip())
            and bool(post_reply.strip())
            and removed
            and "catch the stars" not in post_reply
            and not errors
        ),
        "signals": {
            "removal_reply": turns[1].get("assistant_text"),
            "post_reply": turns[2].get("assistant_text"),
            "errors": errors,
            "final_state_contains_active_target": not removed,
        },
    }


def _repeat_remove_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [err for turn in turns for err in turn.get("errors", [])]
    repeat_reply = str(turns[2].get("assistant_text") or "").lower()
    removed = not bool(turns[-1].get("has_active_works_on_catch"))
    return {
        "pass": (
            not turns[2].get("timed_out")
            and bool(repeat_reply.strip())
            and "failed" not in repeat_reply
            and "error" not in repeat_reply
            and removed
            and not errors
        ),
        "signals": {
            "repeat_reply": turns[2].get("assistant_text"),
            "errors": errors,
            "final_state_contains_active_target": not removed,
        },
    }


def _vague_remove_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [err for turn in turns for err in turn.get("errors", [])]
    removal_reply = str(turns[2].get("assistant_text") or "").lower()
    post_reply = str(turns[3].get("assistant_text") or "").lower()
    removed = not bool(turns[-1].get("has_active_works_on_catch"))
    return {
        "pass": (
            not turns[2].get("timed_out")
            and bool(removal_reply.strip())
            and bool(post_reply.strip())
            and removed
            and "catch the stars" not in post_reply
            and not errors
        ),
        "signals": {
            "removal_reply": turns[2].get("assistant_text"),
            "post_reply": turns[3].get("assistant_text"),
            "errors": errors,
            "final_state_contains_active_target": not removed,
        },
    }


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "memory_remove_direct",
        "turns": [
            "Remember that I work on Catch the Stars.",
            "I'm not really working on Catch the Stars, please remove it from your memory.",
            "Tell me everything you know about me.",
        ],
        "evaluator": _direct_remove_eval,
    },
    {
        "name": "memory_remove_already_absent",
        "turns": [
            "Remember that I work on Catch the Stars.",
            "I'm not really working on Catch the Stars, please remove it from your memory.",
            "I'm not really working on Catch the Stars, please remove it from your memory.",
            "Tell me everything you know about me.",
        ],
        "evaluator": _repeat_remove_eval,
    },
    {
        "name": "memory_remove_vague_followup",
        "turns": [
            "Remember that I work on Catch the Stars.",
            "Tell me everything you know about me.",
            "I'm not really working on that project anymore, remove it.",
            "Tell me everything you know about me.",
        ],
        "evaluator": _vague_remove_eval,
    },
]


def run_smoke(*, timeout_s: float, scenario_filter: set[str] | None = None) -> list[MemoryScenarioReport]:
    reports: list[MemoryScenarioReport] = []
    for scenario in SCENARIOS:
        if scenario_filter and str(scenario["name"]) not in scenario_filter:
            continue
        turns, kept = _run_turns(list(scenario["turns"]), timeout_s=timeout_s)
        evaluation = scenario["evaluator"](turns)
        reports.append(
            MemoryScenarioReport(
                name=str(scenario["name"]),
                success=bool(evaluation.get("pass")),
                kept_data_dir=kept,
                signals=dict(evaluation.get("signals") or {}),
            )
        )
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description="Live harness smoke for direct/vague durable-memory removal flows.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--scenario", action="append", default=[], help="Run only the named scenario. Can be supplied more than once.")
    args = parser.parse_args()

    scenario_filter = {str(name).strip() for name in args.scenario if str(name).strip()} or None
    reports = run_smoke(timeout_s=args.timeout, scenario_filter=scenario_filter)
    payload = {
        "success": all(report.success for report in reports),
        "reports": [asdict(report) for report in reports],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
