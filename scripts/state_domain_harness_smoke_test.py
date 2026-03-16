from __future__ import annotations

import argparse
import json
import re
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


def _collect_errors(turns: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for turn in turns:
        errors.extend(str(item) for item in turn.get("errors", []))
    return errors


def _turn_record(result, data_dir: Path) -> dict[str, Any]:
    return {
        "user_text": result.user_text,
        "assistant_text": result.assistant_text,
        "timed_out": result.timed_out,
        "errors": [event["payload"] for event in result.ui_events if event.get("kind") == "error"],
        "state": {
            "tasks": _read_json(data_state_path(data_dir, "tasks.json")) or {},
            "events": _read_json(data_state_path(data_dir, "events.json")) or {},
            "knowledge": _read_json(data_state_path(data_dir, "knowledge.json")) or {},
            "world_model": _read_json(data_state_path(data_dir, "world_model.json")) or {},
            "situational_state": _read_json(data_state_path(data_dir, "situational_state.json")) or {},
            "intent_state": _read_json(data_state_path(data_dir, "intent_state.json")) or {},
        },
    }


@dataclass(frozen=True)
class ScenarioReport:
    name: str
    success: bool
    signals: dict[str, Any]
    kept_data_dir: str


def _task_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = _collect_errors(turns)
    replies = [str(turn.get("assistant_text") or "") for turn in turns]
    return {
        "pass": (
            "Pending tasks: buy milk." in replies[1]
            and "No pending tasks." in replies[3]
            and not errors
        ),
        "signals": {
            "reply_2": replies[1],
            "reply_4": replies[3],
            "errors": errors,
            "final_tasks": turns[-1]["state"].get("tasks"),
        },
    }


def _task_vague_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = _collect_errors(turns)
    replies = [str(turn.get("assistant_text") or "") for turn in turns]
    return {
        "pass": (
            "Pending tasks: buy bread." in replies[1]
            and "No pending tasks." in replies[3]
            and not errors
        ),
        "signals": {
            "reply_2": replies[1],
            "reply_4": replies[3],
            "errors": errors,
            "final_tasks": turns[-1]["state"].get("tasks"),
        },
    }


def _event_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = _collect_errors(turns)
    events_mid = turns[1]["state"].get("events") or {}
    events_final = turns[-1]["state"].get("events") or {}
    target = "smoke alpha appointment"
    added = any(target in str(name).lower() for name in events_mid.keys())
    removed = not any(target in str(name).lower() for name in events_final.keys())
    return {
        "pass": added and removed and not errors,
        "signals": {
            "events_mid": events_mid,
            "events_final": events_final,
            "reply_2": turns[1].get("assistant_text"),
            "reply_4": turns[-1].get("assistant_text"),
            "errors": errors,
        },
    }


def _event_vague_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = _collect_errors(turns)
    events_mid = turns[1]["state"].get("events") or {}
    events_final = turns[-1]["state"].get("events") or {}
    target = "smoke beta appointment"
    added = any(target in str(name).lower() for name in events_mid.keys())
    removed = not any(target in str(name).lower() for name in events_final.keys())
    first_reply = str(turns[0].get("assistant_text") or "")
    return {
        "pass": added and removed and bool(first_reply.strip()) and not errors,
        "signals": {
            "events_mid": events_mid,
            "events_final": events_final,
            "reply_1": first_reply,
            "reply_2": turns[1].get("assistant_text"),
            "reply_4": turns[-1].get("assistant_text"),
            "errors": errors,
        },
    }


def _reminder_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = _collect_errors(turns)
    events = turns[-1]["state"].get("events") or {}
    target_entry = next(
        (
            (str(name), str(date))
            for name, date in events.items()
            if "get a new yearly insurance" in str(name).lower()
        ),
        ("", ""),
    )
    target_name, target_date = target_entry
    return {
        "pass": bool(target_name) and target_date == "2026-03-25" and not errors,
        "signals": {
            "events": events,
            "reply_2": turns[-1].get("assistant_text"),
            "target_name": target_name,
            "target_date": target_date,
            "errors": errors,
        },
    }


def _knowledge_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = _collect_errors(turns)
    final_reply = str(turns[-1].get("assistant_text") or "").lower()
    query_reply = str(turns[1].get("assistant_text") or "").lower()
    knowledge = turns[-1]["state"].get("knowledge") or {}
    world_model = turns[-1]["state"].get("world_model") or {}
    knowledge_blob = json.dumps(knowledge, ensure_ascii=False).lower()
    world_blob = json.dumps(world_model, ensure_ascii=False).lower()
    favorite_removed = ("favorite drink" not in knowledge_blob) and ("favorite_drink" not in world_blob)
    return {
        "pass": (
            "your favorite drink is coffee." in query_reply
            and favorite_removed
            and ("coffee" not in final_reply)
            and not errors
        ),
        "signals": {
            "reply_2": turns[1].get("assistant_text"),
            "reply_4": turns[-1].get("assistant_text"),
            "knowledge_blob": knowledge_blob[:500],
            "world_blob": world_blob[:500],
            "errors": errors,
        },
    }


def _knowledge_vague_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    errors = _collect_errors(turns)
    second_reply = str(turns[1].get("assistant_text") or "").lower()
    fourth_reply = str(turns[3].get("assistant_text") or "").lower()
    knowledge = turns[-1]["state"].get("knowledge") or {}
    world_model = turns[-1]["state"].get("world_model") or {}
    knowledge_blob = json.dumps(knowledge, ensure_ascii=False).lower()
    world_blob = json.dumps(world_model, ensure_ascii=False).lower()
    favorite_removed = ("favorite drink" not in knowledge_blob) and ("favorite_drink" not in world_blob)
    return {
        "pass": (
            "your favorite drink is coffee." in second_reply
            and fourth_reply == "i do not have a stored favorite drink."
            and favorite_removed
            and not errors
        ),
        "signals": {
            "reply_2": turns[1].get("assistant_text"),
            "reply_4": turns[3].get("assistant_text"),
            "knowledge_blob": knowledge_blob[:500],
            "world_blob": world_blob[:500],
            "errors": errors,
        },
    }


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "task_flow_direct",
        "turns": [
            "Add a task to buy milk.",
            "What tasks do I have right now?",
            "I bought the milk.",
            "What tasks do I have now?",
        ],
        "evaluator": _task_flow_eval,
    },
    {
        "name": "task_flow_vague_completion",
        "turns": [
            "Add a task to buy bread.",
            "What tasks do I have right now?",
            "I did it.",
            "What tasks do I have now?",
        ],
        "evaluator": _task_vague_flow_eval,
    },
    {
        "name": "event_flow_direct",
        "turns": [
            "Add an event smoke alpha appointment on today.",
            "What events do I have scheduled?",
            "I went to the smoke alpha appointment.",
            "What events do I have now?",
        ],
        "evaluator": _event_flow_eval,
    },
    {
        "name": "event_flow_vague_completion",
        "turns": [
            "Add an event smoke beta appointment on today.",
            "What events do I have scheduled?",
            "I went to it.",
            "What events do I have now?",
        ],
        "evaluator": _event_vague_flow_eval,
    },
    {
        "name": "event_flow_vague_reminder",
        "turns": [
            "My insurance company told me that my cars insurance will end on the 25th, so remind me to get a new yearly insurance for that.",
            "What events do I have scheduled?",
        ],
        "evaluator": _reminder_flow_eval,
    },
    {
        "name": "knowledge_flow_direct",
        "turns": [
            "Remember that my favorite drink is coffee.",
            "What do you know about my favorite drink?",
            "Forget that my favorite drink is coffee.",
            "What do you know about my favorite drink now?",
        ],
        "evaluator": _knowledge_flow_eval,
    },
    {
        "name": "knowledge_flow_vague_query",
        "turns": [
            "Remember that my favorite drink is coffee.",
            "Do you remember my favorite drink?",
            "Forget that my favorite drink is coffee.",
            "Do you remember my favorite drink now?",
        ],
        "evaluator": _knowledge_vague_flow_eval,
    },
]


def _run_scenario(turns: list[str]) -> tuple[list[dict[str, Any]], str]:
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
            result = harness.send_text(text, timeout_s=180.0)
            records.append(_turn_record(result, harness.data_dir))
    finally:
        harness.close()
        kept = str(harness.kept_data_dir or "")
    return records, kept


def run_smoke() -> list[ScenarioReport]:
    reports: list[ScenarioReport] = []
    for scenario in SCENARIOS:
        turns, kept = _run_scenario(list(scenario["turns"]))
        evaluation = scenario["evaluator"](turns)
        reports.append(
            ScenarioReport(
                name=str(scenario["name"]),
                success=bool(evaluation.get("pass")),
                signals=dict(evaluation.get("signals") or {}),
                kept_data_dir=kept,
            )
        )
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description="State-domain harness smoke for task/event/knowledge flows.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    reports = run_smoke()
    success = all(report.success for report in reports)
    payload = {
        "success": bool(success),
        "reports": [asdict(report) for report in reports],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
