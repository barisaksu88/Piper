from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.routing.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class EmptyDirCleanupRouteNormalizerReport:
    success: bool
    checked_phrases: list[str]
    failed_phrases: list[str]


def _bad_file_delete_seed() -> dict:
    return {
        "decision": "TASK",
        "card": {
            "goal": 'Locate the workspace file that best matches "empty folders" and delete it.',
            "context": [
                "The workspace root is '.'.",
                'The requested file reference is "empty folders".',
            ],
            "stages": [
                {
                    "stage_goal": 'Find the workspace file that best matches "empty folders" and delete it if found.',
                    "stage_type": "FILE_WORK",
                    "success_condition": "A matching file is deleted, or the absence of any plausible file match is confirmed.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _bad_memory_delete_seed() -> dict:
    return {
        "decision": "TASK",
        "card": {
            "goal": "Remove the user fact 'all empty folders' from memory",
            "context": [
                "The user explicitly asked to remove a durable fact from memory.",
                "Use durable knowledge memory, not tasks or events.",
            ],
            "stages": [
                {
                    "stage_goal": "Remove the durable user fact 'all empty folders' from memory",
                    "stage_type": "MEMORY_WORK",
                    "success_condition": "Knowledge store no longer contains the fact all empty folders",
                    "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
                }
            ],
        },
    }


def _routes_to_empty_dir_cleanup(phrase: str, seed: dict) -> tuple[bool, str]:
    normalized = normalize_route_decision(seed, phrase, [])
    card = dict(normalized.get("card") or {})
    stage = dict(((card.get("stages") or [{}])[0]) or {})

    decision = str(normalized.get("decision") or "")
    goal = str(card.get("goal") or "")
    stage_goal = str(stage.get("stage_goal") or "")
    success_condition = str(stage.get("success_condition") or "")
    file_match_fallback_avoided = "best matches" not in stage_goal.lower() and "plausible file match" not in success_condition.lower()

    success = bool(
        decision == "TASK"
        and "empty folders" in goal.lower()
        and "currently empty" in stage_goal.lower()
        and "no empty folders remain" in success_condition.lower()
        and file_match_fallback_avoided
    )
    reason = f"decision={decision!r} goal={goal!r} stage_goal={stage_goal!r}"
    return success, reason


def run_smoke() -> EmptyDirCleanupRouteNormalizerReport:
    cases = [
        ("delete the empty folders", _bad_file_delete_seed()),
        ("delete all empty folders", _bad_file_delete_seed()),
        ("delete all empty folders", _bad_memory_delete_seed()),
    ]
    checked_phrases: list[str] = []
    failed_phrases: list[str] = []

    for phrase, seed in cases:
        checked_phrases.append(phrase)
        success, reason = _routes_to_empty_dir_cleanup(phrase, seed)
        if not success:
            failed_phrases.append(f"{phrase}: {reason}")

    return EmptyDirCleanupRouteNormalizerReport(
        success=not failed_phrases,
        checked_phrases=checked_phrases,
        failed_phrases=failed_phrases,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
