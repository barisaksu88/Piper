from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.route_clarity import RouteClarifier  # noqa: E402


class _DummyLLM:
    def generate(self, messages, temperature: float = 0.0, cancel_token=None):
        payload = json.loads(messages[-1]["content"])
        latest = str(payload.get("latest_user_input") or "").strip().lower()
        if latest == "a temporary tree.":
            return json.dumps(
                {
                    "decision": "clarify_chat",
                    "question": "What did you want me to change or remember by 'a temporary tree'?",
                    "reason": "fragmentary correction without a concrete action",
                }
            )
        return json.dumps({"decision": "keep_task", "question": "", "reason": "concrete enough"})


@dataclass(frozen=True)
class RouteClarifierReport:
    success: bool
    ambiguous_route: dict
    explicit_result_is_none: bool
    proposal_confirmation_route: dict


def run_smoke() -> RouteClarifierReport:
    clarifier = RouteClarifier()
    llm = _DummyLLM()

    bad_file_route = {
        "decision": "TASK",
        "card": {
            "goal": "Create a new temporary profile entry or update the existing profile to include the 'tree' information.",
            "context": [
                "The user previously indicated the profile was incomplete.",
                "The user just issued the command 'A temporary tree.'",
            ],
            "stages": [
                {
                    "stage_goal": "Create a new temporary profile entry or update the existing profile to include the 'tree' information.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The user's profile file is updated to reflect the new 'tree' entry.",
                    "allowed_tools": ["FILE_OP", "RUN_CODE"],
                }
            ],
        },
    }

    ambiguous_route = clarifier.refine_with_llm(
        llm=llm,
        decision=bad_file_route,
        user_msg="A temporary tree.",
        recent_history=[
            {"role": "assistant", "content": "The profile summary was shown."},
            {"role": "user", "content": "That's not the whole of it."},
            {"role": "assistant", "content": "What is missing?"},
            {"role": "user", "content": "A temporary tree."},
            {"role": "assistant", "content": "Thinking..."},
        ],
    )

    explicit_result = clarifier.refine_with_llm(
        llm=llm,
        decision=bad_file_route,
        user_msg="Create a temporary tree file for me.",
        recent_history=[
            {"role": "user", "content": "Create a temporary tree file for me."},
            {"role": "assistant", "content": "Thinking..."},
        ],
    )

    proposal_confirmation_route = clarifier.refine_with_llm(
        llm=llm,
        decision=bad_file_route,
        user_msg="Yes, please.",
        recent_history=[
            {
                "role": "system",
                "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK\nPrevious user request: Not bike loot, ride my bike.\nTask goal: Update the scheduled event description from 'I need to bike loot' to 'ride my bike' for 2026-03-15\nExecution status: EVENT REMOVED\nRuntime note: Event removed: I need to bike loot\nUse this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict.",
                "hidden": True,
            },
            {"role": "user", "content": "Not bike loot, ride my bike."},
            {
                "role": "assistant",
                "content": "Systems indicate the event \"I need to bike loot\" has been removed from your schedule.\n\nI have no record of an event titled \"ride my bike\" in your current log. Shall I schedule this activity for tomorrow instead?",
            },
            {"role": "user", "content": "Yes, please."},
            {"role": "assistant", "content": "Thinking..."},
        ],
    )

    stage = dict((ambiguous_route or {}).get("card", {}).get("stages", [{}])[0])
    confirmation_stage = dict((proposal_confirmation_route or {}).get("card", {}).get("stages", [{}])[0])
    success = (
        (ambiguous_route or {}).get("decision") == "TASK"
        and str(stage.get("stage_type") or "") == "CHAT"
        and "clarify" in str((ambiguous_route or {}).get("card", {}).get("goal") or "").lower()
        and explicit_result is None
        and (proposal_confirmation_route or {}).get("decision") == "TASK"
        and "ride my bike" in str((proposal_confirmation_route or {}).get("card", {}).get("goal") or "").lower()
        and str(confirmation_stage.get("stage_type") or "") == "TASK_EVENT_WORK"
        and list(confirmation_stage.get("allowed_tools") or []) == ["ADD_EVENT"]
    )
    return RouteClarifierReport(
        success=bool(success),
        ambiguous_route=ambiguous_route or {},
        explicit_result_is_none=explicit_result is None,
        proposal_confirmation_route=proposal_confirmation_route or {},
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
