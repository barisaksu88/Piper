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
    decision: str
    goal: str
    stage_goal: str
    success_condition: str
    file_match_fallback_avoided: bool


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


def run_smoke() -> EmptyDirCleanupRouteNormalizerReport:
    normalized = normalize_route_decision(
        _bad_file_delete_seed(),
        "delete the empty folders",
        [],
    )
    card = dict(normalized.get("card") or {})
    stage = dict(((card.get("stages") or [{}])[0]) or {})

    decision = str(normalized.get("decision") or "")
    goal = str(card.get("goal") or "")
    stage_goal = str(stage.get("stage_goal") or "")
    success_condition = str(stage.get("success_condition") or "")
    file_match_fallback_avoided = "best matches" not in stage_goal.lower() and "plausible file match" not in success_condition.lower()

    success = (
        decision == "TASK"
        and "empty folders" in goal.lower()
        and "currently empty" in stage_goal.lower()
        and "no empty folders remain" in success_condition.lower()
        and file_match_fallback_avoided
    )
    return EmptyDirCleanupRouteNormalizerReport(
        success=bool(success),
        decision=decision,
        goal=goal,
        stage_goal=stage_goal,
        success_condition=success_condition,
        file_match_fallback_avoided=bool(file_match_fallback_avoided),
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
