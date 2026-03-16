from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.routing.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class CodeTargetFollowupReport:
    success: bool
    code_followup_goal: str
    code_followup_stage_goals: list[str]
    code_followup_context: list[str]
    non_code_unchanged: bool


def run_smoke() -> CodeTargetFollowupReport:
    vague_code_decision = {
        "decision": "TASK",
        "card": {
            "goal": "Verify and fix the keyboard input handling in the running Pygame script",
            "context": [
                "The user reported that keyboard controls are not working in the game.",
                "The user explicitly requested a self-check of the implementation.",
            ],
            "stages": [
                {
                    "stage_goal": "Locate and analyze the input handler code responsible for left/right ship movement.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The relevant input handler is identified and the root cause of the movement issue is explained.",
                },
                {
                    "stage_goal": "Correct the input handling logic to ensure left and right buttons control the game character.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The code is updated so the left and right controls work correctly.",
                },
            ],
        },
    }
    code_history = [
        {"role": "assistant", "content": "The script `catch_the_stars.py` is currently running in the embedded Code tab."},
        {"role": "user", "content": "Can you self-check the controls?"},
    ]
    normalized_code = normalize_route_decision(
        vague_code_decision,
        "Please inspect and fix the controls in the running game.",
        code_history,
    )
    code_card = dict(normalized_code.get("card") or {})
    code_stage_goals = [str(stage.get("stage_goal") or "") for stage in code_card.get("stages") or [] if isinstance(stage, dict)]
    code_context = [str(item) for item in code_card.get("context") or []]
    code_goal = str(code_card.get("goal") or "")

    normalized_targets_code = (
        "catch_the_stars.py" in code_goal
        and len(code_stage_goals) == 2
        and all("catch_the_stars.py" in goal for goal in code_stage_goals)
        and any("Do not search the workspace" in item for item in code_context)
    )

    non_code_decision = {
        "decision": "TASK",
        "card": {
            "goal": "Inspect the current list items and explain the formatting issue.",
            "context": ["The user is asking about a text document."],
            "stages": [
                {
                    "stage_goal": "Locate and analyze the current list formatting issue.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The formatting issue is explained.",
                }
            ],
        },
    }
    non_code_history = [
        {"role": "assistant", "content": "The matching file is `grocery_list.txt`."},
    ]
    normalized_non_code = normalize_route_decision(
        non_code_decision,
        "Can you inspect the formatting issue?",
        non_code_history,
    )
    non_code_unchanged = normalized_non_code == non_code_decision

    return CodeTargetFollowupReport(
        success=bool(normalized_targets_code and non_code_unchanged),
        code_followup_goal=code_goal,
        code_followup_stage_goals=code_stage_goals,
        code_followup_context=code_context,
        non_code_unchanged=non_code_unchanged,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Verify that vague code follow-up tasks inherit the latest explicit code file target instead of re-searching the workspace.")


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
