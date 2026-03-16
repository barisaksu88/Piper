from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class KnowledgeRouteNormalizerSmokeReport:
    success: bool
    store_decision: dict
    remove_decision: dict
    query_decision: dict
    transient_remember_decision: dict
    contextual_store_decision: dict
    contextual_transient_decision: dict
    project_remove_decision: dict
    contextual_remove_decision: dict


def run_smoke() -> KnowledgeRouteNormalizerSmokeReport:
    broken_task_decision = {
        "decision": "TASK",
        "card": {
            "goal": "Complete the latest record",
            "context": ["The user is providing a new fact."],
            "stages": [
                {
                    "stage_goal": "Mark the task 'Remember that my favorite drink is coffee' as completed and archive it",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Active task is removed from the list and the completion is archived as memory",
                    "allowed_tools": ["COMPLETE_TASK"],
                }
            ],
        },
    }

    store_decision = normalize_route_decision(
        broken_task_decision,
        "Remember that my favorite drink is coffee.",
    )
    remove_decision = normalize_route_decision(
        broken_task_decision,
        "Forget that my favorite drink is coffee.",
    )
    query_decision = normalize_route_decision(
        broken_task_decision,
        "What do you know about my favorite drink?",
    )
    transient_remember_decision = normalize_route_decision(
        broken_task_decision,
        "Please remember that I'm working on improving piper, which is you.",
    )
    contextual_store_decision = normalize_route_decision(
        broken_task_decision,
        "Just remember that fact.",
        recent_history=[
            {"role": "system", "content": "=== New session"},
            {"role": "user", "content": "My favorite drink is coffee."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": "Just remember that fact."},
            {"role": "assistant", "content": "Thinking..."},
        ],
    )
    contextual_transient_decision = normalize_route_decision(
        broken_task_decision,
        "Just remember that fact.",
        recent_history=[
            {"role": "system", "content": "=== New session"},
            {"role": "user", "content": "My biggest project is currently working on you, Piper."},
            {"role": "assistant", "content": "Understood."},
            {"role": "user", "content": "Just remember that fact."},
            {"role": "assistant", "content": "Thinking..."},
        ],
    )
    project_remove_decision = normalize_route_decision(
        broken_task_decision,
        "I'm not really working on that project to catch the stars, please remove it.",
    )
    contextual_remove_decision = normalize_route_decision(
        broken_task_decision,
        "I'm not really working on that project anymore, please remove it.",
        recent_history=[
            {
                "role": "assistant",
                "content": "[WORLD STATE]\n- works on: Catch the Stars\nEntity: Catch the Stars (project)\n- File Name: catch_the_stars.py",
            }
        ],
    )

    store_stage = dict((store_decision.get("card") or {}).get("stages", [{}])[0])
    remove_stage = dict((remove_decision.get("card") or {}).get("stages", [{}])[0])
    contextual_store_stage = dict((contextual_store_decision.get("card") or {}).get("stages", [{}])[0])
    project_remove_stage = dict((project_remove_decision.get("card") or {}).get("stages", [{}])[0])
    contextual_remove_stage = dict((contextual_remove_decision.get("card") or {}).get("stages", [{}])[0])

    success = (
        store_decision.get("decision") == "TASK"
        and str(store_stage.get("stage_type") or "") == "MEMORY_WORK"
        and list(store_stage.get("allowed_tools") or []) == ["UPDATE_KNOWLEDGE"]
        and "favorite drink" in str(store_stage.get("stage_goal") or "").lower()
        and "coffee" in str(store_stage.get("stage_goal") or "").lower()
        and remove_decision.get("decision") == "TASK"
        and str(remove_stage.get("stage_type") or "") == "MEMORY_WORK"
        and list(remove_stage.get("allowed_tools") or []) == ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"]
        and "favorite drink" in str(remove_stage.get("stage_goal") or "").lower()
        and transient_remember_decision == {"decision": "CHAT"}
        and contextual_store_decision.get("decision") == "TASK"
        and str(contextual_store_stage.get("stage_type") or "") == "MEMORY_WORK"
        and list(contextual_store_stage.get("allowed_tools") or []) == ["UPDATE_KNOWLEDGE"]
        and "favorite drink" in str(contextual_store_stage.get("stage_goal") or "").lower()
        and "coffee" in str(contextual_store_stage.get("stage_goal") or "").lower()
        and contextual_transient_decision == {"decision": "CHAT"}
        and project_remove_decision.get("decision") == "TASK"
        and str(project_remove_stage.get("stage_type") or "") == "MEMORY_WORK"
        and list(project_remove_stage.get("allowed_tools") or []) == ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"]
        and "works on: catch the stars" in str(project_remove_stage.get("stage_goal") or "").lower()
        and contextual_remove_decision.get("decision") == "TASK"
        and str(contextual_remove_stage.get("stage_type") or "") == "MEMORY_WORK"
        and list(contextual_remove_stage.get("allowed_tools") or []) == ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"]
        and "works on: catch the stars" in str(contextual_remove_stage.get("stage_goal") or "").lower()
        and query_decision == {"decision": "CHAT"}
    )
    return KnowledgeRouteNormalizerSmokeReport(
        success=bool(success),
        store_decision=store_decision,
        remove_decision=remove_decision,
        query_decision=query_decision,
        transient_remember_decision=transient_remember_decision,
        contextual_store_decision=contextual_store_decision,
        contextual_transient_decision=contextual_transient_decision,
        project_remove_decision=project_remove_decision,
        contextual_remove_decision=contextual_remove_decision,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
