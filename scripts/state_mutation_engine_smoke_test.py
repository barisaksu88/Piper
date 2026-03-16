from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.state_mutation import StateMutationEngine  # noqa: E402
from core.scratchpad_formatter import ScratchpadFormatter  # noqa: E402


class _FakeKnowledgeManager:
    def __init__(self, payload):
        self._payload = payload

    def load(self):
        return dict(self._payload)


class _FakeOperationalStateService:
    def build_readonly_answer(self, query: str) -> str:
        if "events" in str(query).lower():
            return "Upcoming events: dentist appointment on 2026-03-24."
        return ""


@dataclass(frozen=True)
class StateMutationEngineSmokeReport:
    success: bool
    correction_decision: str
    task_completion_decision: str
    knowledge_query_decision: str
    knowledge_store_decision: str
    knowledge_remove_decision: str
    project_remove_decision: str
    project_remove_subject: str
    false_success_status: str
    false_success_effective_success: bool
    empty_list_mutation_status: str
    empty_list_mutation_effective_success: bool
    empty_list_mutation_auto_reroute: bool
    formatter_status: str
    knowledge_status: str
    knowledge_state_owner: str
    knowledge_failure_effective_success: bool
    memory_absent_target: str
    memory_present_target: str
    readonly_knowledge_answer: str
    readonly_knowledge_owner: str
    readonly_event_answer: str
    readonly_event_owner: str
    readonly_state_assertion_answer: str
    readonly_state_assertion_owner: str
    normalized_task_delete_goal: str
    normalized_task_delete_action: str
    normalized_contextual_remember_stage: str
    normalized_reminder_goal: str
    normalized_reminder_action: str
    normalized_plural_delete_stage_count: int
    natural_event_completion_goal: str
    natural_event_completion_action: str
    natural_event_completion_tools: list[str]


def run_smoke() -> StateMutationEngineSmokeReport:
    engine = StateMutationEngine()

    prior_card = {
        "goal": "Complete the event 'dentist appointment'",
        "context": [
            "The user has a scheduled dentist appointment.",
            "The assistant previously acted as if the appointment had not been booked yet.",
        ],
    }
    prior_stages = [
        {
            "stage_goal": "Mark the event 'dentist appointment' as completed and archive it",
            "stage_type": "TASK_EVENT_WORK",
            "success_condition": "Active event is removed from the list and the completion is archived as memory",
            "allowed_tools": ["COMPLETE_EVENT"],
        }
    ]

    correction_intent = engine.classify_task_event_followup(
        card=prior_card,
        stages=prior_stages,
        user_msg="Fix your calendar, I already got an appointment.",
    )

    task_intent = engine.classify_task_event_followup(
        card={
            "goal": "Complete the task 'buy milk'",
            "context": ["The task is still pending in the list."],
        },
        stages=[
            {
                "stage_goal": "Mark the task 'buy milk' as completed and archive it",
                "stage_type": "TASK_EVENT_WORK",
                "success_condition": "Active task is removed from the list and the completion is archived as memory",
                "allowed_tools": ["COMPLETE_TASK"],
            }
        ],
        user_msg="I bought milk.",
    )

    knowledge_query_intent = engine.classify_knowledge_intent(
        user_msg="Do you remember my favorite drink?",
    )
    knowledge_store_intent = engine.classify_knowledge_intent(
        user_msg="Remember that my favorite drink is coffee.",
    )
    knowledge_remove_intent = engine.classify_knowledge_intent(
        user_msg="Forget that my favorite drink is coffee.",
    )
    project_remove_intent = engine.classify_knowledge_intent(
        user_msg="I'm not really working on that project to catch the stars, please remove it.",
    )

    false_success_pack = engine.build_outcome_pack(
        success=True,
        stage_type="TASK_EVENT_WORK",
        fallback_observation="OBSERVATION_TEXT: Event not found: dentist appointment",
        stage_entries=[
            "STEP 1\nTHOUGHT: Try completion\nACTION: [COMPLETE_EVENT: dentist appointment]\nOBSERVATION_KIND: error\nOBSERVATION_TEXT: Event not found: dentist appointment",
        ],
    )
    empty_list_mutation_pack = engine.build_outcome_pack(
        success=True,
        stage_type="TASK_EVENT_WORK",
        fallback_observation="OBSERVATION_TEXT: No pending tasks.",
        stage_entries=[
            "=== STAGE 1 START ===\nSTAGE_GOAL: Delete the 'Catch the Stars' project entry from the user's task or project list.\nSTAGE_TYPE: TASK_EVENT_WORK\nSUCCESS_CONDITION: The 'Catch the Stars' project is no longer present in the user's active tasks or project list.",
            "STEP 1\nTHOUGHT: I need to list the current tasks to find the target before I can delete it.\nACTION: [LIST_TASKS]\nOBSERVATION_KIND: info\nOBSERVATION_TEXT: No pending tasks.",
        ],
    )
    formatter_pack = ScratchpadFormatter.build_outcome_pack(
        success=True,
        stage_type="TASK_EVENT_WORK",
        last_observation="STEP 1\nTHOUGHT: Try completion\nACTION: [COMPLETE_EVENT: dentist appointment]\nOBSERVATION_KIND: error\nOBSERVATION_TEXT: Event not found: dentist appointment",
        stage_entries=[
            "STEP 1\nTHOUGHT: Try completion\nACTION: [COMPLETE_EVENT: dentist appointment]\nOBSERVATION_KIND: error\nOBSERVATION_TEXT: Event not found: dentist appointment",
        ],
    )

    knowledge_pack = engine.build_outcome_pack(
        success=True,
        stage_type="MEMORY_WORK",
        fallback_observation="OBSERVATION_TEXT: System confirmation: Knowledge base updated successfully.",
        stage_entries=[
            "STEP 1\nTHOUGHT: Store fact\nACTION: [UPDATE_KNOWLEDGE: favorite_drink = coffee]\nOBSERVATION_KIND: success\nOBSERVATION_TEXT: System confirmation: Knowledge base updated successfully.",
        ],
    )

    knowledge_failure_pack = engine.build_outcome_pack(
        success=True,
        stage_type="MEMORY_WORK",
        fallback_observation="OBSERVATION_TEXT: Key not found: works on: Catch the Stars",
        stage_entries=[
            "STEP 1\nTHOUGHT: Remove fact\nACTION: [REMOVE_KNOWLEDGE: works on: Catch the Stars]\nOBSERVATION_KIND: error\nOBSERVATION_TEXT: Key not found: works on: Catch the Stars",
        ],
    )
    memory_stage = {
        "stage_goal": "Remove the durable user fact 'works on: Catch the Stars' from memory",
        "stage_type": "MEMORY_WORK",
        "success_condition": "Knowledge store no longer contains the fact works on: Catch the Stars",
        "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
    }
    memory_absent_target = engine.memory_remove_listing_confirms_absent(
        stage=memory_stage,
        list_result_text="[WORLD STATE]\n- occupation: pilot for turkish airlines",
    )
    memory_present_target = engine.memory_remove_listing_confirms_absent(
        stage=memory_stage,
        list_result_text="[WORLD STATE]\n- works on: Catch the Stars\nEntity: Catch the Stars (project)",
    )

    success = (
        correction_intent.decision == "chat_correction"
        and task_intent.decision == "complete_task"
        and knowledge_query_intent.decision == "query_knowledge"
        and knowledge_store_intent.decision == "store_knowledge"
        and knowledge_store_intent.subject == "favorite drink"
        and knowledge_store_intent.value == "coffee"
        and knowledge_remove_intent.decision == "remove_knowledge"
        and project_remove_intent.decision == "remove_knowledge"
        and project_remove_intent.subject.lower() == "works on: catch the stars"
        and false_success_pack.effective_success is False
        and false_success_pack.status == "FAILED / INCOMPLETE"
        and empty_list_mutation_pack.effective_success is False
        and empty_list_mutation_pack.status == "FAILED / INCOMPLETE"
        and empty_list_mutation_pack.auto_reroute is True
        and formatter_pack.effective_success is False
        and knowledge_pack.status == "KNOWLEDGE UPDATED"
        and knowledge_pack.state_owner == "world_model"
        and knowledge_failure_pack.effective_success is False
        and memory_absent_target == "works on: Catch the Stars"
        and memory_present_target == ""
    )

    readonly_knowledge_pack = engine.build_readonly_answer(
        query="What do you know about my favorite drink?",
        knowledge_mgr=_FakeKnowledgeManager({"favorite_drink": {"value": "coffee"}}),
        operational_state_service=_FakeOperationalStateService(),
    )
    readonly_event_pack = engine.build_readonly_answer(
        query="What events do I have scheduled?",
        knowledge_mgr=_FakeKnowledgeManager({}),
        operational_state_service=_FakeOperationalStateService(),
    )
    readonly_state_assertion_pack = engine.build_readonly_answer(
        query="There should be events now.",
        knowledge_mgr=_FakeKnowledgeManager({}),
        operational_state_service=_FakeOperationalStateService(),
    )

    task_delete_route = engine.normalize_route_decision(
        decision={"decision": "CHAT"},
        user_msg="Please remove that from the tasks.",
        recent_history=[
            {"role": "assistant", "content": "Pending tasks: buy milk."},
        ],
    )
    task_delete_stage = dict((((task_delete_route or {}).get("card") or {}).get("stages") or [{}])[0])
    contextual_remember_route = engine.normalize_route_decision(
        decision={"decision": "CHAT"},
        user_msg="Just remember that fact.",
        recent_history=[
            {"role": "user", "content": "My favorite drink is coffee."},
            {"role": "assistant", "content": "Thinking..."},
        ],
    )
    reminder_route = engine.normalize_route_decision(
        decision={
            "decision": "TASK",
            "card": {
                "goal": "Handle the reminder request.",
                "context": [],
                "stages": [
                    {
                        "stage_goal": "Handle the reminder request.",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": "The reminder is added correctly.",
                        "allowed_tools": ["ADD_TASK"],
                    }
                ],
            },
        },
        user_msg="My insurance company told me my car insurance will end on the 25th, so remind me to get a new yearly insurance for that.",
        recent_history=[],
    )
    reminder_stage = dict((((reminder_route or {}).get("card") or {}).get("stages") or [{}])[0])
    plural_delete_route = engine.normalize_route_decision(
        decision={"decision": "CHAT"},
        user_msg="Done the shopping, remove them all.",
        recent_history=[
            {"role": "assistant", "content": "Pending tasks: buy milk; buy bread."},
        ],
    )
    natural_event_completion_route = engine.normalize_route_decision(
        decision={
            "decision": "TASK",
            "card": {
                "goal": "Complete the task 'Cool, I forgot about those, thank you, but I washed my car already'",
                "context": [
                    "The user indicated they completed the task.",
                    "Use the latest runtime context as the authoritative source for the active target.",
                ],
                "stages": [
                    {
                        "stage_goal": "Mark the task 'Cool, I forgot about those, thank you, but I washed my car already' as completed and archive it",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": "Active task is removed from the list and the completion is archived as memory",
                        "allowed_tools": ["COMPLETE_TASK"],
                    }
                ],
            },
        },
        user_msg="Cool, I forgot about those, thank you, but I washed my car already.",
        recent_history=[
            {
                "role": "system",
                "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK\nPrevious user request: Add an event to wash my car tomorrow.\nTask goal: Add an event for to wash my car on 2026-03-15\nExecution status: EVENT SCHEDULED\nRuntime note: Event scheduled: to wash my car on 2026-03-15\nUse this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict.",
                "hidden": True,
            },
            {"role": "assistant", "content": "Upcoming events: to wash my car on 2026-03-15; dentist appointment on 2026-03-24."},
        ],
    )
    natural_event_completion_stage = dict(
        ((natural_event_completion_route or {}).get("card") or {}).get("stages", [{}])[0]
    )
    success = (
        success
        and readonly_knowledge_pack.answer == "Your favorite drink is coffee."
        and readonly_knowledge_pack.state_owner == "world_model"
        and readonly_event_pack.answer == "Upcoming events: dentist appointment on 2026-03-24."
        and readonly_event_pack.state_owner == "task_event"
        and readonly_state_assertion_pack.answer == "Upcoming events: dentist appointment on 2026-03-24."
        and readonly_state_assertion_pack.state_owner == "task_event"
        and str(((task_delete_route or {}).get("card") or {}).get("goal") or "") == "Delete the task 'buy milk' from the active task list"
        and str((task_delete_stage.get("mutation") or {}).get("action") or "") == "delete"
        and str((((contextual_remember_route or {}).get("card") or {}).get("stages") or [{}])[0].get("stage_type") or "") == "MEMORY_WORK"
        and str(((reminder_route or {}).get("card") or {}).get("goal") or "").endswith("on 2026-03-25")
        and str((reminder_stage.get("mutation") or {}).get("action") or "") == "schedule"
        and len((((plural_delete_route or {}).get("card") or {}).get("stages") or [])) == 2
        and "wash my car" in str(((natural_event_completion_route or {}).get("card") or {}).get("goal") or "").lower()
        and "cool, i forgot about those" not in str(((natural_event_completion_route or {}).get("card") or {}).get("goal") or "").lower()
        and str((natural_event_completion_stage.get("mutation") or {}).get("action") or "") == "complete"
        and list(natural_event_completion_stage.get("allowed_tools") or []) == ["COMPLETE_EVENT", "LIST_EVENTS"]
    )

    return StateMutationEngineSmokeReport(
        success=bool(success),
        correction_decision=correction_intent.decision,
        task_completion_decision=task_intent.decision,
        knowledge_query_decision=knowledge_query_intent.decision,
        knowledge_store_decision=knowledge_store_intent.decision,
        knowledge_remove_decision=knowledge_remove_intent.decision,
        project_remove_decision=project_remove_intent.decision,
        project_remove_subject=project_remove_intent.subject,
        false_success_status=false_success_pack.status,
        false_success_effective_success=bool(false_success_pack.effective_success),
        empty_list_mutation_status=empty_list_mutation_pack.status,
        empty_list_mutation_effective_success=bool(empty_list_mutation_pack.effective_success),
        empty_list_mutation_auto_reroute=bool(empty_list_mutation_pack.auto_reroute),
        formatter_status=formatter_pack.status,
        knowledge_status=knowledge_pack.status,
        knowledge_state_owner=knowledge_pack.state_owner,
        knowledge_failure_effective_success=bool(knowledge_failure_pack.effective_success),
        memory_absent_target=memory_absent_target,
        memory_present_target=memory_present_target,
        readonly_knowledge_answer=readonly_knowledge_pack.answer,
        readonly_knowledge_owner=readonly_knowledge_pack.state_owner,
        readonly_event_answer=readonly_event_pack.answer,
        readonly_event_owner=readonly_event_pack.state_owner,
        readonly_state_assertion_answer=readonly_state_assertion_pack.answer,
        readonly_state_assertion_owner=readonly_state_assertion_pack.state_owner,
        normalized_task_delete_goal=str(((task_delete_route or {}).get("card") or {}).get("goal") or ""),
        normalized_task_delete_action=str((task_delete_stage.get("mutation") or {}).get("action") or ""),
        normalized_contextual_remember_stage=str((((contextual_remember_route or {}).get("card") or {}).get("stages") or [{}])[0].get("stage_type") or ""),
        normalized_reminder_goal=str(((reminder_route or {}).get("card") or {}).get("goal") or ""),
        normalized_reminder_action=str((reminder_stage.get("mutation") or {}).get("action") or ""),
        normalized_plural_delete_stage_count=len((((plural_delete_route or {}).get("card") or {}).get("stages") or [])),
        natural_event_completion_goal=str(((natural_event_completion_route or {}).get("card") or {}).get("goal") or ""),
        natural_event_completion_action=str((natural_event_completion_stage.get("mutation") or {}).get("action") or ""),
        natural_event_completion_tools=list(natural_event_completion_stage.get("allowed_tools") or []),
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
