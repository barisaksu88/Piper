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
class ExtensionReorgScopeRouteNormalizerReport:
    success: bool
    workspace_goal: str
    workspace_context: list[str]
    workspace_stage_goals: list[str]
    workspace_active_targets: list[list[str]]
    clarified_goal: str
    clarified_context: list[str]
    clarified_stage_goals: list[str]
    clarified_active_targets: list[list[str]]
    direct_goal: str
    direct_context: list[str]
    direct_stage_goals: list[str]
    direct_active_targets: list[list[str]]


def _seed_route() -> dict:
    return {
        "decision": "TASK",
        "card": {
            "goal": "Consolidate workspace files so each extension ends up in one relevant folder.",
            "context": ["The workspace root is '.'."],
            "stages": [
                {
                    "stage_goal": "Inspect the workspace and build an extension inventory with a destination folder chosen for each extension.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "An extension inventory exists and a destination folder is identified for each relevant extension.",
                    "allowed_tools": ["FILE_OP"],
                },
                {
                    "stage_goal": "Consolidate files so each extension lives in one chosen destination folder without creating duplicates.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "For every relevant extension, files are consolidated into a single destination folder and duplicate identical files are not kept twice.",
                    "allowed_tools": ["FILE_OP"],
                },
            ],
        },
    }


def run_smoke() -> ExtensionReorgScopeRouteNormalizerReport:
    workspace_wide = normalize_route_decision(
        _seed_route(),
        "Organize the workspace. Put files with the same extension into relevant folders, avoid duplicates, and delete empty folders that emerge.",
        [],
    )
    workspace_card = dict(workspace_wide.get("card") or {})
    workspace_stages = [dict(stage) for stage in (workspace_card.get("stages") or []) if isinstance(stage, dict)]

    clarified = normalize_route_decision(
        _seed_route(),
        "not root the test folder ./test",
        [
            {"role": "user", "content": "consolidate my test folder by file type"},
            {
                "role": "assistant",
                "content": "Right, so \"test folder\" means the one at `./test` or your root `.`?",
            },
        ],
    )
    clarified_card = dict(clarified.get("card") or {})
    clarified_stages = [dict(stage) for stage in (clarified_card.get("stages") or []) if isinstance(stage, dict)]

    direct = normalize_route_decision(
        _seed_route(),
        "organize ./test by file type",
        [],
    )
    direct_card = dict(direct.get("card") or {})
    direct_stages = [dict(stage) for stage in (direct_card.get("stages") or []) if isinstance(stage, dict)]

    workspace_goal = str(workspace_card.get("goal") or "")
    workspace_context = [str(item) for item in (workspace_card.get("context") or [])]
    workspace_stage_goals = [str(stage.get("stage_goal") or "") for stage in workspace_stages]
    workspace_active_targets = [list(stage.get("active_targets") or []) for stage in workspace_stages]

    clarified_goal = str(clarified_card.get("goal") or "")
    clarified_context = [str(item) for item in (clarified_card.get("context") or [])]
    clarified_stage_goals = [str(stage.get("stage_goal") or "") for stage in clarified_stages]
    clarified_active_targets = [list(stage.get("active_targets") or []) for stage in clarified_stages]

    direct_goal = str(direct_card.get("goal") or "")
    direct_context = [str(item) for item in (direct_card.get("context") or [])]
    direct_stage_goals = [str(stage.get("stage_goal") or "") for stage in direct_stages]
    direct_active_targets = [list(stage.get("active_targets") or []) for stage in direct_stages]

    workspace_ok = (
        str(workspace_wide.get("decision") or "") == "TASK"
        and "under '.'" in workspace_goal
        and any("workspace root is '.'" in item.lower() for item in workspace_context)
        and workspace_stage_goals
        and all("'" not in item or "'./same'" not in item for item in workspace_stage_goals)
        and workspace_active_targets
        and all(targets == ["."] for targets in workspace_active_targets)
    )
    clarified_ok = (
        str(clarified.get("decision") or "") == "TASK"
        and "./test" in clarified_goal
        and any("./test" in item for item in clarified_context)
        and any("do not sweep the whole workspace root" in item.lower() for item in clarified_context)
        and clarified_stage_goals
        and all("./test" in item for item in clarified_stage_goals)
        and clarified_active_targets
        and all(targets == ["test"] for targets in clarified_active_targets)
    )
    direct_ok = (
        str(direct.get("decision") or "") == "TASK"
        and "./test" in direct_goal
        and any("./test" in item for item in direct_context)
        and direct_stage_goals
        and all("./test" in item for item in direct_stage_goals)
        and direct_active_targets
        and all(targets == ["test"] for targets in direct_active_targets)
    )
    return ExtensionReorgScopeRouteNormalizerReport(
        success=bool(workspace_ok and clarified_ok and direct_ok),
        workspace_goal=workspace_goal,
        workspace_context=workspace_context,
        workspace_stage_goals=workspace_stage_goals,
        workspace_active_targets=workspace_active_targets,
        clarified_goal=clarified_goal,
        clarified_context=clarified_context,
        clarified_stage_goals=clarified_stage_goals,
        clarified_active_targets=clarified_active_targets,
        direct_goal=direct_goal,
        direct_context=direct_context,
        direct_stage_goals=direct_stage_goals,
        direct_active_targets=direct_active_targets,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
