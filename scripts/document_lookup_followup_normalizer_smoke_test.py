from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.routing.route_normalizer import normalize_route_decision  # noqa: E402
from core.skills import apply_route_skill_layer  # noqa: E402


@dataclass(frozen=True)
class DocumentLookupFollowupReport:
    success: bool
    normalized_goal: str
    normalized_stage_goal: str
    normalized_context: list[str]
    skilled_stage_goal: str
    skilled_planner_hint: str
    read_it_back_goal: str
    read_it_back_stage_goal: str


def run_smoke() -> DocumentLookupFollowupReport:
    prior_exact_read = {
        "decision": "TASK",
        "card": {
            "goal": "Read the exact contents of 'grocery_list.txt'.",
            "context": [
                "The workspace root is '.'.",
                "The target file path is 'grocery_list.txt'.",
                "Return the file contents exactly once when read succeeds.",
            ],
            "stages": [
                {
                    "stage_goal": "Read the exact contents of the file 'grocery_list.txt'.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The exact contents of 'grocery_list.txt' are read once.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }
    history = [
        {"role": "user", "content": "Can you tell me what it says in the grocery list?"},
        {"role": "assistant", "content": "Apples\nBananas\nCarrots\nDairy milk\nEggs"},
        {
            "role": "system",
            "content": (
                "[LATEST_RUNTIME_CONTEXT]\n"
                "Previous route: TASK\n"
                "Previous user request: Yes, but what's in the file?\n"
                "Task goal: Read the exact contents of 'grocery_list.txt'.\n"
                "Execution status: FILE OPERATION SUCCESS\n"
                "Runtime note: Last direct file read targeted 'grocery_list.txt'.\n"
                "Relevant paths: grocery_list.txt\n"
            ),
            "hidden": True,
        },
        {"role": "user", "content": "Yes, but what's in the file?"},
        {"role": "assistant", "content": "Apples\nBananas\nCarrots\nDairy milk\nEggs"},
        {"role": "user", "content": "I think we already have a document, the naming might not be matching, please check again."},
    ]

    normalized = normalize_route_decision(
        prior_exact_read,
        "I think we already have a document, the naming might not be matching, please check again.",
        history,
    )
    skilled = apply_route_skill_layer(
        normalized,
        "I think we already have a document, the naming might not be matching, please check again.",
        history,
        enabled=True,
    )

    normalized_card = dict(normalized.get("card") or {})
    normalized_stage = dict((normalized_card.get("stages") or [{}])[0] or {})
    skilled_card = dict(skilled.get("card") or {})
    skilled_stage = dict((skilled_card.get("stages") or [{}])[0] or {})
    skill = dict(skilled_stage.get("skill") or {})

    normalized_goal = str(normalized_card.get("goal") or "")
    normalized_stage_goal = str(normalized_stage.get("stage_goal") or "")
    normalized_context = [str(item) for item in normalized_card.get("context") or []]
    skilled_stage_goal = str(skilled_stage.get("stage_goal") or "")
    skilled_planner_hint = str(skill.get("planner_hint") or "")

    normalized_is_search = (
        "grocery_list.txt" in normalized_goal
        and "match" in normalized_goal.lower()
        and "search workspace filenames" in normalized_stage_goal.lower()
        and "exact contents" not in normalized_stage_goal.lower()
    )
    skilled_is_lookup_only = (
        "search workspace filenames for files matching 'grocery_list.txt'" in skilled_stage_goal.lower()
        and "find_paths match satisfies the stage" in skilled_planner_hint.lower()
        and "do not call read_text or read_many" in skilled_planner_hint.lower()
    )
    implicit_history = [
        {"role": "user", "content": "Can you tell me what it says in the grocery list?"},
        {"role": "assistant", "content": "Apples\nBananas\nCarrots\nDairy milk\nEggs"},
    ]
    implicit_read = normalize_route_decision(
        {
            "decision": "TASK",
            "card": {
                "goal": "Read the grocery list.",
                "stages": [
                    {
                        "stage_goal": "Read the grocery list.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "The grocery list is read.",
                    }
                ],
            },
        },
        "Read it back.",
        implicit_history,
    )
    implicit_card = dict(implicit_read.get("card") or {})
    implicit_stage = dict((implicit_card.get("stages") or [{}])[0] or {})
    read_it_back_goal = str(implicit_card.get("goal") or "")
    read_it_back_stage_goal = str(implicit_stage.get("stage_goal") or "")
    read_it_back_uses_prior_subject = (
        "grocery list" in read_it_back_goal.lower()
        and "it back" not in read_it_back_goal.lower()
        and "grocery list" in read_it_back_stage_goal.lower()
    )

    return DocumentLookupFollowupReport(
        success=bool(normalized_is_search and skilled_is_lookup_only and read_it_back_uses_prior_subject),
        normalized_goal=normalized_goal,
        normalized_stage_goal=normalized_stage_goal,
        normalized_context=normalized_context,
        skilled_stage_goal=skilled_stage_goal,
        skilled_planner_hint=skilled_planner_hint,
        read_it_back_goal=read_it_back_goal,
        read_it_back_stage_goal=read_it_back_stage_goal,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify that naming-mismatch follow-ups stay lookup-only even when hidden runtime context includes a prior exact read."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
