"""Unit tests for core.engines.state_mutation.StateMutationEngine.

These tests require no LLM, no web search, no threading, and no external services.
They validate the deterministic public API and selected private helpers.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.engines.state_mutation import StateMutationEngine
from core.contracts import (
    KnowledgeMutationIntent,
    StateMutationIntent,
    StageOutcomePack,
    StateReadonlyPack,
)


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def engine() -> StateMutationEngine:
    return StateMutationEngine()


class _FakeKnowledgeManager:
    def __init__(self, payload: dict[str, Any] | None = None):
        self._payload = payload or {}

    def load(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeOperationalStateService:
    def __init__(self, answer: str = ""):
        self._answer = answer

    def build_readonly_answer(self, query: str) -> str:
        return self._answer


# ── 1. classify_knowledge_intent ─────────────────────────────────────

class TestClassifyKnowledgeIntent:
    def test_query(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_knowledge_intent(user_msg="What is my favorite drink?")
        assert intent.decision == "query_knowledge"
        assert intent.subject == "favorite drink"

    def test_store(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_knowledge_intent(
            user_msg="Remember that my favorite drink is coffee."
        )
        assert intent.decision == "store_knowledge"
        assert intent.subject == "favorite drink"
        assert intent.value == "coffee"

    def test_remove(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_knowledge_intent(
            user_msg="Forget that my favorite drink is coffee."
        )
        assert intent.decision == "remove_knowledge"
        assert intent.subject == "favorite drink"

    def test_none_unrelated(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_knowledge_intent(user_msg="What is the weather today?")
        assert intent.decision == "none"

    def test_empty(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_knowledge_intent(user_msg="")
        assert intent.decision == "none"

    def test_transient_remember_request_is_none(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_knowledge_intent(user_msg="Remember that I am hungry.")
        assert intent.decision == "none"


# ── 2. classify_task_event_followup ──────────────────────────────────

class TestClassifyTaskEventFollowup:
    def test_complete_task(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_task_event_followup(
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
        assert intent.decision == "complete_task"
        assert intent.subject == "milk"

    def test_chat_correction_without_record_context(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_task_event_followup(
            card={
                "goal": "Complete the event 'dentist appointment'",
                "context": ["The user has a scheduled dentist appointment."],
            },
            stages=[
                {
                    "stage_goal": "Mark the event 'dentist appointment' as completed and archive it",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Active event is removed from the list and the completion is archived as memory",
                    "allowed_tools": ["COMPLETE_EVENT"],
                }
            ],
            user_msg="Fix your calendar, I already got an appointment.",
        )
        assert intent.decision == "chat_correction"

    def test_inspect_event(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_task_event_followup(
            card={
                "goal": "Complete the event 'dentist appointment'",
                "context": [],
            },
            stages=[
                {
                    "stage_goal": "Mark the event 'dentist appointment' as completed and archive it",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Active event is removed from the list and the completion is archived as memory",
                    "allowed_tools": ["COMPLETE_EVENT"],
                }
            ],
            user_msg="Check my calendar for dentist",
        )
        assert intent.decision == "inspect_event"
        assert "dentist" in intent.subject

    def test_none_unrelated(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_task_event_followup(
            card={"goal": "Do something", "context": []},
            stages=[],
            user_msg="What is the capital of France?",
        )
        assert intent.decision == "none"

    def test_file_work_request_is_none(self, engine: StateMutationEngine) -> None:
        intent = engine.classify_task_event_followup(
            card={"goal": "Do something", "context": []},
            stages=[{"stage_type": "FILE_WORK"}],
            user_msg="Create a file called notes.txt",
        )
        assert intent.decision == "none"


# ── 3. memory_remove_listing_confirms_absent ─────────────────────────

class TestMemoryRemoveListingConfirmsAbsent:
    def test_absent_confirmed(self, engine: StateMutationEngine) -> None:
        stage = {
            "stage_goal": "Remove the durable user fact 'works on: Catch the Stars' from memory",
            "stage_type": "MEMORY_WORK",
            "success_condition": "Knowledge store no longer contains the fact works on: Catch the Stars",
            "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
        }
        result = engine.memory_remove_listing_confirms_absent(
            stage=stage,
            list_result_text="[WORLD STATE]\n- occupation: pilot for turkish airlines",
        )
        assert result == "works on: Catch the Stars"

    def test_present_not_confirmed(self, engine: StateMutationEngine) -> None:
        stage = {
            "stage_goal": "Remove the durable user fact 'works on: Catch the Stars' from memory",
            "stage_type": "MEMORY_WORK",
            "success_condition": "Knowledge store no longer contains the fact works on: Catch the Stars",
            "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
        }
        result = engine.memory_remove_listing_confirms_absent(
            stage=stage,
            list_result_text="[WORLD STATE]\n- works on: Catch the Stars\nEntity: Catch the Stars (project)",
        )
        assert result == ""

    def test_ambiguous_listing_not_confirmed(self, engine: StateMutationEngine) -> None:
        stage = {
            "stage_goal": "Remove the durable user fact 'works on: Catch the Stars' from memory",
            "stage_type": "MEMORY_WORK",
            "success_condition": "Knowledge store no longer contains the fact works on: Catch the Stars",
            "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
        }
        # Significant word "Catch" appears in listing but target not exactly present
        result = engine.memory_remove_listing_confirms_absent(
            stage=stage,
            list_result_text="[WORLD STATE]\n- works on: Catch the Moon",
        )
        assert result == ""

    def test_non_memory_stage_returns_empty(self, engine: StateMutationEngine) -> None:
        stage = {
            "stage_goal": "Do something",
            "stage_type": "FILE_WORK",
        }
        result = engine.memory_remove_listing_confirms_absent(
            stage=stage,
            list_result_text="No knowledge stored.",
        )
        assert result == ""


# ── 4. build_readonly_answer ─────────────────────────────────────────

class TestBuildReadonlyAnswer:
    def test_knowledge_query_answer(self, engine: StateMutationEngine) -> None:
        pack = engine.build_readonly_answer(
            query="What do you know about my favorite drink?",
            knowledge_mgr=_FakeKnowledgeManager({"favorite_drink": {"value": "coffee"}}),
            operational_state_service=_FakeOperationalStateService(),
        )
        assert pack.answer == "Your favorite drink is coffee."
        assert pack.state_owner == "world_model"
        assert pack.query_kind == "knowledge"

    def test_knowledge_not_found(self, engine: StateMutationEngine) -> None:
        pack = engine.build_readonly_answer(
            query="What is my favorite color?",
            knowledge_mgr=_FakeKnowledgeManager({}),
            operational_state_service=_FakeOperationalStateService(),
        )
        assert pack.answer == "I do not have a stored favorite color."
        assert pack.state_owner == "world_model"

    def test_task_event_readonly_answer(self, engine: StateMutationEngine) -> None:
        pack = engine.build_readonly_answer(
            query="What events do I have scheduled?",
            knowledge_mgr=_FakeKnowledgeManager({}),
            operational_state_service=_FakeOperationalStateService(
                answer="Upcoming events: dentist appointment on 2027-06-15."
            ),
        )
        assert pack.answer == "Upcoming events: dentist appointment on 2027-06-15."
        assert pack.state_owner == "task_event"
        assert pack.query_kind == "operational"

    def test_profile_summary_answer(self, engine: StateMutationEngine) -> None:
        class _KMWithDisplay:
            def render_prompt_state(self, prefix: str, max_entities: int = 8) -> str:
                return "[WORLD STATE]\n- occupation: pilot"

        pack = engine.build_readonly_answer(
            query="Tell me everything you know about me",
            knowledge_mgr=_KMWithDisplay(),
            operational_state_service=_FakeOperationalStateService(
                answer="Upcoming events: dentist appointment on 2027-06-15."
            ),
        )
        assert "pilot" in pack.answer
        assert "dentist appointment" in pack.answer
        assert pack.state_owner == "world_model"

    def test_empty_query(self, engine: StateMutationEngine) -> None:
        pack = engine.build_readonly_answer(
            query="",
            knowledge_mgr=_FakeKnowledgeManager({}),
            operational_state_service=_FakeOperationalStateService(),
        )
        assert pack.answer == ""


# ── 5. build_outcome_pack ────────────────────────────────────────────

class TestBuildOutcomePack:
    def test_task_event_work_success(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="TASK_EVENT_WORK",
            fallback_observation="OBSERVATION_TEXT: Task added: buy milk",
            stage_entries=[
                "STEP 1\nTHOUGHT: Add task\nACTION: [ADD_TASK: buy milk]\nOBSERVATION_KIND: success\nOBSERVATION_TEXT: Task added: buy milk"
            ],
        )
        assert pack.status == "TASK ADDED"
        assert pack.effective_success is True
        assert pack.state_owner == "task"
        assert pack.mutation_kind == "add"

    def test_task_event_work_failure(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="TASK_EVENT_WORK",
            fallback_observation="OBSERVATION_TEXT: Event not found: dentist appointment",
            stage_entries=[
                "STEP 1\nTHOUGHT: Try completion\nACTION: [COMPLETE_EVENT: dentist appointment]\nOBSERVATION_KIND: error\nOBSERVATION_TEXT: Event not found: dentist appointment"
            ],
        )
        assert pack.status == "FAILED / INCOMPLETE"
        assert pack.effective_success is False
        assert pack.state_owner == "task_event"

    def test_memory_work_success(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="MEMORY_WORK",
            fallback_observation="OBSERVATION_TEXT: System confirmation: Knowledge base updated successfully.",
            stage_entries=[
                "STEP 1\nTHOUGHT: Store fact\nACTION: [UPDATE_KNOWLEDGE: favorite_drink = coffee]\nOBSERVATION_KIND: success\nOBSERVATION_TEXT: System confirmation: Knowledge base updated successfully."
            ],
        )
        assert pack.status == "KNOWLEDGE UPDATED"
        assert pack.effective_success is True
        assert pack.state_owner == "world_model"

    def test_memory_work_failure(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="MEMORY_WORK",
            fallback_observation="OBSERVATION_TEXT: Key not found: works on: Catch the Stars",
            stage_entries=[
                "STEP 1\nTHOUGHT: Remove fact\nACTION: [REMOVE_KNOWLEDGE: works on: Catch the Stars]\nOBSERVATION_KIND: error\nOBSERVATION_TEXT: Key not found: works on: Catch the Stars"
            ],
        )
        assert pack.status == "FAILED / INCOMPLETE"
        assert pack.effective_success is False
        assert pack.state_owner == "memory"

    def test_proposal_only_outcome(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="TASK_EVENT_WORK",
            fallback_observation="PROPOSAL: I cannot restore the deleted file with the current tool set.",
            stage_entries=[
                "STEP 1\nTHOUGHT: I cannot restore the deleted file with the current tool set.\nACTION: [NO_TOOL_PROPOSAL]\nPROPOSAL: I cannot restore the deleted file with the current tool set."
            ],
        )
        assert pack.status == "FAILED / INCOMPLETE"
        assert pack.effective_success is False
        assert pack.state_owner == "task_event"

    def test_empty_list_mutation_auto_reroute(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="TASK_EVENT_WORK",
            fallback_observation="OBSERVATION_TEXT: No pending tasks.",
            stage_entries=[
                "=== STAGE 1 START ===\nSTAGE_GOAL: Delete the 'Catch the Stars' project entry from the user's task or project list.\nSTAGE_TYPE: TASK_EVENT_WORK\nSUCCESS_CONDITION: The 'Catch the Stars' project is no longer present in the user's active tasks or project list.",
                "STEP 1\nTHOUGHT: I need to list the current tasks to find the target before I can delete it.\nACTION: [LIST_TASKS]\nOBSERVATION_KIND: info\nOBSERVATION_TEXT: No pending tasks.",
            ],
        )
        assert pack.status == "FAILED / INCOMPLETE"
        assert pack.effective_success is False
        assert pack.auto_reroute is True
        assert "state-owner mismatch" in pack.reroute_reason

    def test_image_work_success(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="IMAGE_WORK",
            fallback_observation="",
        )
        assert pack.status == "IMAGE GENERATED"
        assert pack.effective_success is True

    def test_file_work_success(self, engine: StateMutationEngine) -> None:
        pack = engine.build_outcome_pack(
            success=True,
            stage_type="FILE_WORK",
            fallback_observation="",
        )
        assert pack.status == "FILE OPERATION SUCCESS"
        assert pack.effective_success is True


# ── 6. normalize_route_decision ──────────────────────────────────────

class TestNormalizeRouteDecision:
    def test_task_add_remains_task(self, engine: StateMutationEngine) -> None:
        # Single-stage task adds do not trigger normalization changes;
        # normalize_route_decision returns None when no modification is needed.
        decision = engine.normalize_route_decision(
            decision={
                "decision": "TASK",
                "card": {
                    "goal": "Add a task to buy milk",
                    "stages": [
                        {
                            "stage_goal": "Create a task to buy milk",
                            "stage_type": "TASK_EVENT_WORK",
                            "success_condition": "Task is created once with the requested details",
                            "allowed_tools": ["ADD_TASK"],
                        }
                    ],
                },
            },
            user_msg="Add a task to buy milk",
            recent_history=[],
        )
        assert decision is None

    def test_event_schedule_normalized_correctly(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={
                "decision": "TASK",
                "card": {
                    "goal": "Schedule an event",
                    "context": ["The user wants to schedule something."],
                    "stages": [
                        {
                            "stage_goal": "Create a task to schedule dentist appointment",
                            "stage_type": "TASK_EVENT_WORK",
                            "success_condition": "Task is created once with the requested details",
                            "allowed_tools": ["ADD_TASK"],
                        },
                        {
                            "stage_goal": "Schedule the event for 2026-06-15",
                            "stage_type": "TASK_EVENT_WORK",
                            "success_condition": "Event is created once with the requested date",
                            "allowed_tools": ["ADD_EVENT"],
                        },
                    ],
                },
            },
            user_msg="Schedule a dentist appointment on June 15th",
            recent_history=[],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        stages = decision.get("card", {}).get("stages", [])
        assert len(stages) == 1
        assert "event" in stages[0].get("stage_goal", "").lower()
        assert stages[0].get("mutation", {}).get("action") == "schedule"

    def test_knowledge_store_normalized(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Remember that my favorite drink is coffee.",
            recent_history=[],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        stages = decision.get("card", {}).get("stages", [])
        assert len(stages) == 1
        assert stages[0].get("stage_type") == "MEMORY_WORK"
        assert stages[0].get("mutation", {}).get("action") == "store"

    def test_knowledge_remove_normalized(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Forget that my favorite drink is coffee.",
            recent_history=[],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        stages = decision.get("card", {}).get("stages", [])
        assert len(stages) == 1
        assert stages[0].get("stage_type") == "MEMORY_WORK"
        assert stages[0].get("mutation", {}).get("action") == "remove"

    def test_reminder_route_normalized(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
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
            user_msg="My insurance company told me my car insurance will end on 2027-04-15, so remind me to get a new yearly insurance for that.",
            recent_history=[],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        goal = decision.get("card", {}).get("goal", "")
        assert "2027-04-15" in goal
        stages = decision.get("card", {}).get("stages", [])
        assert stages[0].get("mutation", {}).get("action") == "schedule"

    def test_delete_followup_normalized(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Please remove that from the tasks.",
            recent_history=[
                {"role": "assistant", "content": "Pending tasks: buy milk."},
            ],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        goal = decision.get("card", {}).get("goal", "")
        assert "Delete" in goal
        stages = decision.get("card", {}).get("stages", [])
        assert stages[0].get("mutation", {}).get("action") == "delete"

    def test_completion_followup_normalized(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Done with buy milk.",
            recent_history=[
                {
                    "role": "system",
                    "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK\nPrevious user request: Add a task to buy milk.\nTask goal: Add a task to buy milk\nExecution status: TASK ADDED\nRuntime note: Task added: buy milk\nUse this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict.",
                    "hidden": True,
                },
                {"role": "assistant", "content": "Pending tasks: buy milk."},
            ],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        goal = decision.get("card", {}).get("goal", "")
        assert "Complete" in goal
        stages = decision.get("card", {}).get("stages", [])
        assert stages[0].get("mutation", {}).get("action") == "complete"

    def test_plural_task_followup_normalized(self, engine: StateMutationEngine) -> None:
        # "Done" triggers completion mode, so plural follow-up resolves to complete actions.
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Done the shopping, remove them all.",
            recent_history=[
                {"role": "assistant", "content": "Pending tasks: buy milk; buy bread."},
            ],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        stages = decision.get("card", {}).get("stages", [])
        assert len(stages) == 2
        for stage in stages:
            assert stage.get("stage_type") == "TASK_EVENT_WORK"
            assert stage.get("mutation", {}).get("action") == "complete"

    def test_contextual_remember_followup(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Just remember that fact.",
            recent_history=[
                {"role": "user", "content": "My favorite drink is coffee."},
                {"role": "assistant", "content": "Thinking..."},
            ],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        stages = decision.get("card", {}).get("stages", [])
        assert stages[0].get("stage_type") == "MEMORY_WORK"
        assert stages[0].get("mutation", {}).get("action") == "store"

    def test_chat_stays_chat_for_unrelated(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="What is the weather today?",
            recent_history=[],
        )
        assert decision is None or decision["decision"] == "CHAT"


# ── 7. Regression tests for undertested paths ────────────────────────

class TestNormalizeChatTaskEventFollowup:
    def test_chat_to_task_completion(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Done with buy milk.",
            recent_history=[
                {
                    "role": "system",
                    "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK\nPrevious user request: Add a task to buy milk.\nTask goal: Add a task to buy milk\nExecution status: TASK ADDED\nRuntime note: Task added: buy milk\nUse this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict.",
                    "hidden": True,
                },
                {"role": "assistant", "content": "Task added: buy milk."},
            ],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        goal = decision.get("card", {}).get("goal", "")
        assert "Complete" in goal

    def test_chat_stays_chat_without_completion_hint(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Tell me more about that.",
            recent_history=[
                {"role": "assistant", "content": "Task added: buy milk."},
            ],
        )
        # Should stay CHAT or return None
        assert decision is None or decision["decision"] == "CHAT"


class TestBindCompletionTargetFromRecentContext:
    def test_binds_from_runtime_context(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={
                "decision": "TASK",
                "card": {
                    "goal": "Complete the task 'Cool, I forgot about those, thank you, but I washed my car already'",
                    "context": ["The user indicated they completed the task."],
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
        assert decision is not None
        assert decision["decision"] == "TASK"
        goal = decision.get("card", {}).get("goal", "").lower()
        assert "wash my car" in goal
        assert "cool, i forgot about those" not in goal
        stages = decision.get("card", {}).get("stages", [])
        assert stages[0].get("mutation", {}).get("action") == "complete"
        assert "COMPLETE_EVENT" in stages[0].get("allowed_tools", [])


class TestNormalizeScheduleCorrectionToChat:
    def test_schedule_correction_stays_chat(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={
                "decision": "TASK",
                "card": {
                    "goal": "Schedule dentist appointment",
                    "context": [],
                    "stages": [
                        {
                            "stage_goal": "Schedule the event 'dentist appointment' for 2026-06-15",
                            "stage_type": "TASK_EVENT_WORK",
                            "success_condition": "Event is created once with the requested date",
                            "allowed_tools": ["ADD_EVENT"],
                        }
                    ],
                },
            },
            user_msg="Actually, change the appointment to next week.",
            recent_history=[],
        )
        assert decision is not None
        assert decision["decision"] == "CHAT"


class TestNormalizeEventFollowupInspection:
    def test_event_inspection_followup(self, engine: StateMutationEngine) -> None:
        # _normalize_event_followup_inspection is difficult to trigger through
        # normalize_route_decision without very specific card/stage alignment.
        # Test the helper directly with a user_msg that satisfies its guards.
        result = engine._normalize_event_followup_inspection(
            decision={"decision": "TASK"},
            card={
                "goal": "Schedule dentist appointment",
                "context": [],
            },
            stages=[
                {
                    "stage_goal": "Schedule the event 'dentist appointment' for 2026-06-15",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Event is created once with the requested date",
                    "allowed_tools": ["ADD_EVENT"],
                }
            ],
            user_msg="Check my calendar for dentist",
        )
        assert result is not None
        assert result["decision"] == "TASK"
        goal = result.get("card", {}).get("goal", "")
        assert "Check" in goal
        stages = result.get("card", {}).get("stages", [])
        assert stages[0].get("allowed_tools") == ["LIST_EVENTS"]


class TestNormalizeRetryFromLatestRuntimeContext:
    def test_retry_from_runtime_context(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Try again.",
            recent_history=[
                {
                    "role": "system",
                    "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK\nPrevious user request: Remember that my favorite drink is coffee.",
                    "hidden": True,
                },
            ],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        goal = decision.get("card", {}).get("goal", "")
        assert "favorite drink" in goal

    def test_retry_without_task_history_returns_none(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Try again.",
            recent_history=[
                {
                    "role": "system",
                    "content": "[LATEST_RUNTIME_CONTEXT]\nPrevious route: CHAT\nPrevious user request: Hello.",
                    "hidden": True,
                },
            ],
        )
        assert decision is None or decision["decision"] == "CHAT"


class TestNormalizeReminderTaskOverrideFollowup:
    def test_reminder_task_override(self, engine: StateMutationEngine) -> None:
        decision = engine.normalize_route_decision(
            decision={"decision": "CHAT"},
            user_msg="Keep it as a task.",
            recent_history=[
                {"role": "user", "content": "Remind me to buy milk."},
            ],
        )
        assert decision is not None
        assert decision["decision"] == "TASK"
        goal = decision.get("card", {}).get("goal", "")
        assert "buy milk" in goal
        stages = decision.get("card", {}).get("stages", [])
        assert stages[0].get("mutation", {}).get("action") == "add"


class TestNormalizeCasualCompletionToChat:
    def test_casual_completion_without_record_context_returns_none(self, engine: StateMutationEngine) -> None:
        # _normalize_casual_completion_to_chat returns None when conditions are not met,
        # leaving the original route unchanged upstream.
        decision = engine.normalize_route_decision(
            decision={
                "decision": "TASK",
                "card": {
                    "goal": "Do something unrelated",
                    "context": [],
                    "stages": [
                        {
                            "stage_goal": "Do something",
                            "stage_type": "TASK_EVENT_WORK",
                            "success_condition": "Done",
                            "allowed_tools": ["ADD_TASK"],
                        }
                    ],
                },
            },
            user_msg="Okay, sounds good.",
            recent_history=[],
        )
        assert decision is None


class TestExtractContextualMemoryRemoveSubject:
    def test_extracts_from_recent_assistant_content(self, engine: StateMutationEngine) -> None:
        subject = engine._extract_contextual_memory_remove_subject(
            user_msg="I am not working on that project anymore, remove it.",
            recent_history=[
                {
                    "role": "assistant",
                    "content": "[WORLD STATE]\n- works on: Catch the Stars",
                },
            ],
        )
        assert subject == "works on: Catch the Stars"

    def test_no_match_returns_empty(self, engine: StateMutationEngine) -> None:
        subject = engine._extract_contextual_memory_remove_subject(
            user_msg="What is the weather?",
            recent_history=[],
        )
        assert subject == ""


class TestExtractWorkStateRemoveSubject:
    def test_extracts_project_name(self, engine: StateMutationEngine) -> None:
        subject = engine._extract_work_state_remove_subject(
            "I'm not really working on that project to catch the stars, please remove it."
        )
        assert "catch the stars" in subject.lower()

    def test_no_marker_returns_empty(self, engine: StateMutationEngine) -> None:
        subject = engine._extract_work_state_remove_subject(
            "I am working on a new project."
        )
        assert subject == ""


class TestLooksLikeFileWorkRequest:
    def test_direct_file_create(self, engine: StateMutationEngine) -> None:
        assert engine._looks_like_file_work_request(user_msg="Create a file called notes.txt") is True

    def test_direct_file_edit(self, engine: StateMutationEngine) -> None:
        assert engine._looks_like_file_work_request(user_msg="Edit readme.md") is True

    def test_unrelated_text(self, engine: StateMutationEngine) -> None:
        assert engine._looks_like_file_work_request(user_msg="What is the weather?") is False

    def test_empty(self, engine: StateMutationEngine) -> None:
        assert engine._looks_like_file_work_request(user_msg="") is False

    def test_file_work_stage_overrides(self, engine: StateMutationEngine) -> None:
        assert engine._looks_like_file_work_request(
            user_msg="What is the weather?",
            stages=[{"stage_type": "FILE_WORK"}],
        ) is True
