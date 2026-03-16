from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.prompt_builder import PromptBuilder  # noqa: E402
from core.skills import apply_route_skill_layer  # noqa: E402


@dataclass(frozen=True)
class SkillLayerSmokeReport:
    success: bool
    cleanup_skill: str
    code_fix_skill: str
    search_skill: str
    file_edit_skill: str
    lookup_variant_stops_at_paths: bool
    path_copy_has_skill: bool
    disabled_has_skill: bool
    planner_includes_active_skill: bool


def run_smoke() -> SkillLayerSmokeReport:
    cleanup_route = {
        "decision": "TASK",
        "card": {
            "goal": "Organize the workspace by extension.",
            "context": ["The workspace root is '.'."],
            "stages": [
                {
                    "stage_goal": "Consolidate files so each extension lives in one chosen destination folder without creating duplicates.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "For every relevant extension, files are consolidated into a single destination folder and duplicate identical files are not kept twice.",
                    "allowed_tools": ["FILE_OP"],
                },
                {
                    "stage_goal": "Delete empty folders that remain after consolidation.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "No empty folders remain under the workspace root.",
                    "allowed_tools": ["FILE_OP"],
                },
            ],
        },
    }
    code_fix_route = {
        "decision": "TASK",
        "card": {
            "goal": "Fix the keyboard controls in catch_the_stars.py.",
            "stages": [
                {
                    "stage_goal": "Inspect catch_the_stars.py and identify why left/right controls do not work.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The concrete code issue is identified.",
                    "allowed_tools": ["FILE_OP", "RUN_CODE"],
                    "context": ["The relevant file is 'catch_the_stars.py'."],
                },
                {
                    "stage_goal": "Edit catch_the_stars.py so left and right controls work correctly.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The updated artifact satisfies the control fix request.",
                    "allowed_tools": ["FILE_OP", "RUN_CODE"],
                    "context": ["The relevant file is 'catch_the_stars.py'."],
                },
            ],
        },
    }
    search_route = {
        "decision": "SEARCH",
        "card": {
            "query": "latest llama.cpp qwen benchmarks",
        },
    }
    file_edit_route = {
        "decision": "TASK",
        "card": {
            "goal": 'Remove "eggs" from the workspace file matching "grocery list".',
            "context": [
                "The workspace root is '.'.",
                'The requested document reference is "grocery list".',
                'Keep all other file content unchanged unless the requested text removal requires a local formatting cleanup.',
            ],
            "stages": [
                {
                    "stage_goal": 'Locate the workspace file that best matches "grocery list", remove the exact text "eggs" from its contents, and save the updated file.',
                    "stage_type": "FILE_WORK",
                    'success_condition': 'A matching file is identified and no longer contains "eggs".',
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }
    path_copy_route = {
        "decision": "TASK",
        "card": {
            "goal": "Copy 'text_files/harness_alpha.txt' to 'text_files/harness_beta.txt'.",
            "context": ["The workspace root is '.'."],
            "stages": [
                {
                    "stage_goal": "Copy the file 'text_files/harness_alpha.txt' to 'text_files/harness_beta.txt'.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "Both 'text_files/harness_alpha.txt' and 'text_files/harness_beta.txt' exist and the destination contents match the source.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }
    lookup_route = {
        "decision": "TASK",
        "card": {
            "goal": "Find the matching workspace filename.",
            "stages": [
                {
                    "stage_goal": "Search workspace filenames for files that plausibly match \"grocery list\".",
                    "stage_type": "FILE_WORK",
                    "success_condition": "Matching file paths are identified, or the absence of any plausible filename match is confirmed.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }

    cleanup_skilled = apply_route_skill_layer(cleanup_route, "Organize the workspace by extension.", enabled=True)
    code_fix_skilled = apply_route_skill_layer(code_fix_route, "Fix the controls in catch_the_stars.py.", enabled=True)
    search_skilled = apply_route_skill_layer(search_route, "Search the web for latest llama.cpp qwen benchmarks.", enabled=True)
    file_edit_skilled = apply_route_skill_layer(file_edit_route, "Remove 'eggs' from the grocery list file.", enabled=True)
    path_copy_skilled = apply_route_skill_layer(path_copy_route, "Copy text_files/harness_alpha.txt to text_files/harness_beta.txt.", enabled=True)
    lookup_skilled = apply_route_skill_layer(lookup_route, "The naming might not be matching, please check again.", enabled=True)
    cleanup_disabled = apply_route_skill_layer(cleanup_route, "Organize the workspace by extension.", enabled=False)

    planner_prompt = PromptBuilder.build_planner_prompt(
        base_template="[STAGE_CARD]\n\n[SCRATCHPAD]\n\n[TOOL_GUIDE]",
        stage=dict((cleanup_skilled.get("card") or {}).get("stages", [])[0]),
        scratchpad_text="=== STAGE 1 START ===",
        step_count=1,
    )

    cleanup_skill = str((cleanup_skilled.get("skill") or {}).get("name") or "").strip()
    code_fix_skill = str((code_fix_skilled.get("skill") or {}).get("name") or "").strip()
    search_skill = str((search_skilled.get("skill") or {}).get("name") or "").strip()
    file_edit_skill = str((file_edit_skilled.get("skill") or {}).get("name") or "").strip()
    lookup_procedure = [str(item).strip().lower() for item in ((lookup_skilled.get("skill") or {}).get("procedure") or []) if str(item).strip()]
    lookup_variant_stops_at_paths = (
        any("path" in item for item in lookup_procedure)
        and any("do not read file contents" in item or "stop once the path match is proven" in item for item in lookup_procedure)
    )
    path_copy_has_skill = bool(path_copy_skilled.get("skill"))
    disabled_has_skill = bool(cleanup_disabled.get("skill"))
    planner_includes_active_skill = "### ACTIVE_SKILL" in planner_prompt and "workspace_cleanup" in planner_prompt

    success = all(
        (
            cleanup_skill == "workspace_cleanup",
            code_fix_skill == "code_fix",
            search_skill == "search_research",
            file_edit_skill == "file_edit",
            lookup_variant_stops_at_paths,
            not path_copy_has_skill,
            not disabled_has_skill,
            planner_includes_active_skill,
        )
    )
    return SkillLayerSmokeReport(
        success=bool(success),
        cleanup_skill=cleanup_skill,
        code_fix_skill=code_fix_skill,
        search_skill=search_skill,
        file_edit_skill=file_edit_skill,
        lookup_variant_stops_at_paths=bool(lookup_variant_stops_at_paths),
        path_copy_has_skill=bool(path_copy_has_skill),
        disabled_has_skill=bool(disabled_has_skill),
        planner_includes_active_skill=bool(planner_includes_active_skill),
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Verify the reversible skill layer selects and injects workflow skills.")


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
