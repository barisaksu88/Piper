from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.followup_resolution import FollowupResolutionEngine  # noqa: E402
from core.engines.state_mutation import StateMutationEngine  # noqa: E402
from core.operational_state_service import OperationalStateService  # noqa: E402
from memory.state_owner import SharedStateOwner  # noqa: E402


class _DummyKnowledge:
    def list_for_display(self) -> str:
        return "[WORLD STATE]\n- works on: Catch the Stars"


class _DummyLLM:
    def generate(self, messages, temperature: float = 0.0, cancel_token=None):
        payload = json.loads(messages[-1]["content"])
        latest = str(payload.get("latest_user_input") or "").strip().lower()
        if latest == "well, remove it.":
            return json.dumps(
                {
                    "decision": "delete_task",
                    "target": "buy milk",
                    "confidence": "high",
                    "reason": "latest active task is buy milk and the user asked to remove it",
                }
            )
        if latest == "i've already done it, you may remove it.":
            return json.dumps(
                {
                    "decision": "complete_task",
                    "target": "buy milk",
                    "confidence": "high",
                    "reason": "the user says the active task is already done",
                }
            )
        if latest == "any tasks left?":
            return json.dumps(
                {
                    "decision": "query_tasks",
                    "query": "What tasks do I have right now?",
                    "confidence": "high",
                    "reason": "the user is asking for current task state",
                }
            )
        if latest == "great.":
            return json.dumps(
                {
                    "decision": "chat",
                    "confidence": "high",
                    "reason": "plain acknowledgement",
                }
            )
        if latest == "just remember that fact.":
            return json.dumps(
                {
                    "decision": "store_knowledge",
                    "target": "favorite drink",
                    "value": "coffee",
                    "confidence": "high",
                    "reason": "previous user statement was a stable personal fact",
                }
            )
        if latest == "remove it from your memory.":
            return json.dumps(
                {
                    "decision": "remove_knowledge",
                    "target": "works on: Catch the Stars",
                    "confidence": "high",
                    "reason": "explicit memory scope with known project fact",
                }
            )
        return json.dumps({"decision": "keep_route", "confidence": "low", "reason": "fallback"})


class _FallbackOnlyLLM:
    def generate(self, messages, temperature: float = 0.0, cancel_token=None):
        payload = json.loads(messages[-1]["content"])
        latest = str(payload.get("latest_user_input") or "").strip().lower()
        if latest == "remove it.":
            return json.dumps({"decision": "keep_route", "confidence": "low", "reason": "fallback"})
        return json.dumps({"decision": "keep_route", "confidence": "low", "reason": "fallback"})


@dataclass(frozen=True)
class FollowupResolutionEngineReport:
    success: bool
    delete_task_route: dict
    fallback_delete_task_route: dict
    complete_task_route: dict
    query_tasks_route: dict
    chat_route: dict
    store_memory_route: dict
    remove_memory_route: dict


def run_smoke() -> FollowupResolutionEngineReport:
    with tempfile.TemporaryDirectory(prefix="piper-followup-resolution-") as tmp:
        data_dir = Path(tmp)
        owner = SharedStateOwner.for_data_dir(data_dir)
        owner.task_store.add("buy milk", "pending")
        owner.event_store.add("dentist appointment", "2026-03-24")
        ops = OperationalStateService(owner)
        knowledge = _DummyKnowledge()
        llm = _DummyLLM()
        engine = FollowupResolutionEngine(state_mutation_engine=StateMutationEngine())

        bad_memory_route = {
            "decision": "TASK",
            "card": {
                "goal": "Remove the user fact 'it' from memory",
                "context": [],
                "stages": [
                    {
                        "stage_goal": "Remove the durable user fact 'it' from memory",
                        "stage_type": "MEMORY_WORK",
                        "success_condition": "Knowledge store no longer contains the fact it",
                        "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
                    }
                ],
            },
        }

        delete_task_route = engine.refine_with_llm(
            llm=llm,
            decision=bad_memory_route,
            user_msg="Well, remove it.",
            recent_history=[
                {"role": "assistant", "content": "Pending tasks: buy milk."},
                {"role": "user", "content": "Well, remove it."},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        fallback_delete_task_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision=bad_memory_route,
            user_msg="Remove it.",
            recent_history=[
                {"role": "assistant", "content": "Pending tasks: buy milk."},
                {"role": "user", "content": "Remove it."},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        complete_task_route = engine.refine_with_llm(
            llm=llm,
            decision=bad_memory_route,
            user_msg="I've already done it, you may remove it.",
            recent_history=[
                {
                    "role": "system",
                    "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK\nPrevious user request: Add a task to buy milk.\nTask goal: Add a new task to buy milk\nExecution status: TASK ADDED\nRuntime note: Task added: buy milk\nUse this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict.",
                },
                {"role": "assistant", "content": "Pending tasks: buy milk."},
                {"role": "user", "content": "I've already done it, you may remove it."},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        query_tasks_route = engine.refine_with_llm(
            llm=llm,
            decision={"decision": "CHAT"},
            user_msg="Any tasks left?",
            recent_history=[
                {"role": "assistant", "content": "The task to buy milk has been removed from your list, Sir."},
                {"role": "user", "content": "Any tasks left?"},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        chat_route = engine.refine_with_llm(
            llm=llm,
            decision=bad_memory_route,
            user_msg="Great.",
            recent_history=[
                {
                    "role": "system",
                    "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK\nPrevious user request: Any tasks left?\nTask goal: Check current task state\nExecution status: TASKS LISTED\nRuntime note: Pending tasks: buy milk.\nUse this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict.",
                },
                {"role": "assistant", "content": "Pending tasks: buy milk."},
                {"role": "user", "content": "Great."},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        store_memory_route = engine.refine_with_llm(
            llm=llm,
            decision={"decision": "CHAT"},
            user_msg="Just remember that fact.",
            recent_history=[
                {"role": "user", "content": "My favorite drink is coffee."},
                {"role": "assistant", "content": "Noted."},
                {"role": "user", "content": "Just remember that fact."},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        remove_memory_route = engine.refine_with_llm(
            llm=llm,
            decision={"decision": "CHAT"},
            user_msg="Remove it from your memory.",
            recent_history=[
                {"role": "assistant", "content": "[WORLD STATE]\n- works on: Catch the Stars"},
                {"role": "user", "content": "Remove it from your memory."},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )

    delete_stage = dict((((delete_task_route or {}).get("card") or {}).get("stages") or [{}])[0])
    fallback_delete_stage = dict((((fallback_delete_task_route or {}).get("card") or {}).get("stages") or [{}])[0])
    complete_stage = dict((((complete_task_route or {}).get("card") or {}).get("stages") or [{}])[0])
    store_stage = dict((((store_memory_route or {}).get("card") or {}).get("stages") or [{}])[0])
    remove_stage = dict((((remove_memory_route or {}).get("card") or {}).get("stages") or [{}])[0])

    success = (
        (delete_task_route or {}).get("decision") == "TASK"
        and "buy milk" in str(((delete_task_route or {}).get("card") or {}).get("goal") or "").lower()
        and list(delete_stage.get("allowed_tools") or []) == ["DELETE_TASK"]
        and str((delete_stage.get("mutation") or {}).get("action") or "") == "delete"
        and (fallback_delete_task_route or {}).get("decision") == "TASK"
        and "buy milk" in str(((fallback_delete_task_route or {}).get("card") or {}).get("goal") or "").lower()
        and list(fallback_delete_stage.get("allowed_tools") or []) == ["DELETE_TASK"]
        and str((fallback_delete_stage.get("mutation") or {}).get("action") or "") == "delete"
        and (complete_task_route or {}).get("decision") == "TASK"
        and "buy milk" in str(((complete_task_route or {}).get("card") or {}).get("goal") or "").lower()
        and list(complete_stage.get("allowed_tools") or []) == ["COMPLETE_TASK", "LIST_TASKS"]
        and str((complete_stage.get("mutation") or {}).get("action") or "") == "complete"
        and (query_tasks_route or {}).get("decision") == "CHAT"
        and str(((query_tasks_route or {}).get("card") or {}).get("query") or "") == "What tasks do I have right now?"
        and chat_route == {"decision": "CHAT"}
        and (store_memory_route or {}).get("decision") == "TASK"
        and str(store_stage.get("stage_type") or "") == "MEMORY_WORK"
        and "favorite drink" in str(store_stage.get("stage_goal") or "").lower()
        and str((store_stage.get("mutation") or {}).get("action") or "") == "store"
        and (remove_memory_route or {}).get("decision") == "TASK"
        and str(remove_stage.get("stage_type") or "") == "MEMORY_WORK"
        and "catch the stars" in str(remove_stage.get("stage_goal") or "").lower()
        and str((remove_stage.get("mutation") or {}).get("action") or "") == "remove"
    )

    return FollowupResolutionEngineReport(
        success=bool(success),
        delete_task_route=delete_task_route or {},
        fallback_delete_task_route=fallback_delete_task_route or {},
        complete_task_route=complete_task_route or {},
        query_tasks_route=query_tasks_route or {},
        chat_route=chat_route or {},
        store_memory_route=store_memory_route or {},
        remove_memory_route=remove_memory_route or {},
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
