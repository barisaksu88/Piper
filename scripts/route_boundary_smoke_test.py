from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator_phases import _refine_ambiguous_task_route_with_llm, _resolve_followup_route_with_llm  # noqa: E402
from core.file_target_confirmation import build_pending_file_target_confirmation_message  # noqa: E402
from core.route_boundary import BoundaryValidationError, RouteClarifierBoundary, FollowupResolutionBoundary, RouterBoundary  # noqa: E402
from core.routing.route_normalizer import detect_route_interceptor, normalize_route_decision  # noqa: E402


class _CaptureUI:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def put(self, event: tuple[str, object]) -> None:
        self.events.append(event)


class _InvalidLLM:
    def generate(self, messages, temperature: float = 0.0, max_tokens=None, cancel_token=None, **kwargs):
        return json.dumps({"decision": "nonsense"})


class _KeepRouteLLM:
    def generate(self, messages, temperature: float = 0.0, max_tokens=None, cancel_token=None, **kwargs):
        return json.dumps({"decision": "keep_route"})


@dataclass(frozen=True)
class RouteBoundaryReport:
    success: bool
    router_valid_task: dict
    router_valid_search_confidence: dict
    router_invalid_fallback: dict
    live_environment_route_result: dict
    explicit_web_search_route_result: dict
    benchmark_topic_search_route_result: dict
    dotted_topic_search_route_result: dict
    ambiguous_lookup_route_result: dict
    high_confidence_web_lookup_route_result: dict
    high_confidence_workspace_lookup_route_result: dict
    benchmark_ambiguous_task_route_result: dict
    dotted_ambiguous_task_route_result: dict
    compound_file_sequence_route_result: dict
    file_state_correction_interceptor_result: dict | None
    file_target_correction_interceptor_result: dict | None
    file_target_confirmation_yes_result: dict | None
    file_target_confirmation_cancel_result: dict | None
    lookup_source_followup_result: dict
    lookup_source_web_followup_result: dict
    web_offer_affirmative_followup_result: dict
    contextual_lookup_route_result: dict
    task_event_verification_followup_result: dict | None
    memory_recall_affirmative_followup_result: dict | None
    memory_recall_correction_followup_result: dict | None
    memory_recall_attempt_followup_result: dict | None
    memory_recall_context_repair_followup_result: dict | None
    appointment_event_detail_followup_result: dict | None
    followup_wrapper_result: dict | None
    clarifier_wrapper_result: dict | None
    followup_validation_logged: bool
    clarifier_validation_logged: bool


def _is_lookup_source_clarification(route: dict, subject: str) -> bool:
    card = dict((route or {}).get("card") or {})
    stages = [dict(stage) for stage in (card.get("stages") or []) if isinstance(stage, dict)]
    stage_goal = str((stages[0] if stages else {}).get("stage_goal") or "").lower()
    goal = str(card.get("goal") or "").lower()
    normalized_subject = str(subject or "").lower()
    return (
        str((route or {}).get("decision") or "").upper() == "TASK"
        and str((stages[0] if stages else {}).get("stage_type") or "") == "CHAT"
        and "clarify lookup source" in goal
        and "web" in stage_goal
        and "workspace" in stage_goal
        and normalized_subject in goal
    )


def _is_task_delete_card(route: dict | None, subject: str) -> bool:
    card = dict((route or {}).get("card") or {})
    stages = [dict(stage) for stage in (card.get("stages") or []) if isinstance(stage, dict)]
    goal = str(card.get("goal") or "").lower()
    stage_type = str((stages[0] if stages else {}).get("stage_type") or "")
    mutation = dict((stages[0] if stages else {}).get("mutation") or {})
    return (
        str((route or {}).get("decision") or "").upper() == "TASK"
        and "delete the task" in goal
        and str(subject or "").lower() in goal
        and stage_type == "TASK_EVENT_WORK"
        and str(mutation.get("action") or "").lower() == "delete"
    )


def run_smoke() -> RouteBoundaryReport:
    router_valid_task = RouterBoundary.validate(
        json.dumps(
            {
                "decision": "TASK",
                "card": {
                    "goal": "Remove bread from grocery_list.txt",
                    "stages": [
                        {
                            "stage_goal": "Remove bread from grocery_list.txt",
                            "stage_type": "FILE_WORK",
                            "success_condition": "bread is removed",
                        }
                    ],
                },
            }
        )
    )

    router_valid_search_confidence = RouterBoundary.validate(
        json.dumps(
            {
                "decision": "SEARCH",
                "card": {"query": "grocery"},
                "source_scope": "web",
                "confidence": "high",
                "question_if_uncertain": "Did you want the web or the workspace?",
            }
        )
    )

    router_invalid_fallback: dict = {}
    try:
        RouterBoundary.validate(json.dumps({"decision": "MAYBE"}))
    except BoundaryValidationError as exc:
        router_invalid_fallback = dict(exc.fallback or {})

    live_environment_route_result = normalize_route_decision(
        {"decision": "SEARCH", "card": {"query": "today's date"}},
        "What's today's date?",
        [],
    )

    explicit_web_search_route_result = normalize_route_decision(
        {
            "decision": "TASK",
            "card": {
                "goal": "Find workspace filenames that match 'llama.cpp'.",
                "stages": [
                    {
                        "stage_goal": "Search workspace filenames for files matching 'llama.cpp'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "Matching file paths are identified.",
                    }
                ],
            },
        },
        "search for the latest news on llama.cpp performance benchmarks",
        [{"role": "system", "content": "=== New session"}],
    )

    benchmark_topic_search_route_result = normalize_route_decision(
        {
            "decision": "SEARCH",
            "card": {"query": "search for MLPerf Inference v5.0 benchmark results"},
            "source_scope": "web",
            "confidence": "high",
        },
        "search for MLPerf Inference v5.0 benchmark results",
        [{"role": "system", "content": "=== New session"}],
    )

    dotted_topic_search_route_result = normalize_route_decision(
        {
            "decision": "SEARCH",
            "card": {"query": "search for llama.cpp benchmark results"},
            "source_scope": "web",
            "confidence": "high",
        },
        "search for llama.cpp benchmark results",
        [{"role": "system", "content": "=== New session"}],
    )

    ambiguous_lookup_route_result = normalize_route_decision(
        {
            "decision": "SEARCH",
            "card": {"query": "grocery"},
            "source_scope": "web",
            "confidence": "low",
            "question_if_uncertain": "Did you want me to search the web, or look in your workspace files?",
        },
        "Search for grocery.",
        [{"role": "system", "content": "=== New session"}],
    )

    high_confidence_web_lookup_route_result = normalize_route_decision(
        {
            "decision": "SEARCH",
            "card": {"query": "grocery"},
            "source_scope": "web",
            "confidence": "high",
        },
        "Search for grocery.",
        [{"role": "system", "content": "=== New session"}],
    )

    high_confidence_workspace_lookup_route_result = normalize_route_decision(
        {
            "decision": "TASK",
            "card": {
                "goal": "Look for a workspace file related to grocery.",
                "stages": [
                    {
                        "stage_goal": "Search workspace filenames for grocery-related files.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "Matching file paths are identified.",
                    }
                ],
            },
            "source_scope": "workspace",
            "confidence": "high",
        },
        "Search for grocery.",
        [{"role": "system", "content": "=== New session"}],
    )

    benchmark_ambiguous_task_route_result = normalize_route_decision(
        {
            "decision": "TASK",
            "card": {
                "goal": "Find workspace filenames that match 'MLPerf Inference v5.0'.",
                "stages": [
                    {
                        "stage_goal": "Search workspace filenames for files matching 'MLPerf Inference v5.0'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "Matching file paths are identified.",
                    }
                ],
            },
        },
        "search for MLPerf Inference v5.0 benchmark results",
        [{"role": "system", "content": "=== New session"}],
    )

    dotted_ambiguous_task_route_result = normalize_route_decision(
        {
            "decision": "TASK",
            "card": {
                "goal": "Find workspace filenames that match 'llama.cpp'.",
                "stages": [
                    {
                        "stage_goal": "Search workspace filenames for files matching 'llama.cpp'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "Matching file paths are identified.",
                    }
                ],
            },
        },
        "search for llama.cpp benchmark results",
        [{"role": "system", "content": "=== New session"}],
    )

    compound_file_sequence_route_result = normalize_route_decision(
        {"decision": "CHAT"},
        "create a file and then delete it and then undo it and then redo it",
        [{"role": "system", "content": "=== New session"}],
    )

    file_state_correction_interceptor_result = detect_route_interceptor(
        "its final state should be non-existing i think",
        [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: doesnt matter, name: bob, content: flame\n"
                    "Task goal: Execute the file lifecycle sequence: create 'bob' with content 'flame', delete it, undo the deletion, and redo the deletion.\n"
                    "Execution status: FILE OPERATION SUCCESS\n"
                    "Runtime note: Deleted bob.. Requested paths were deleted successfully.\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            }
        ],
    )

    file_target_correction_interceptor_result = detect_route_interceptor(
        "it was bob not b",
        [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: its final state should be non-existing i think\n"
                    "Task goal: Ensure the file 'bob' is deleted and remains deleted in the final state.\n"
                    "Execution status: FILE OPERATION SUCCESS\n"
                    "Runtime note: Removed b.txt and verified the file change.\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            }
        ],
    )

    pending_file_confirmation_message = build_pending_file_target_confirmation_message(
        {
            "kind": "missing_file_target_confirmation",
            "exact_target": "bob.txt",
            "candidates": ["b.txt"],
            "question": "I can't find `bob.txt`. Did you mean `b.txt`?",
            "route_decision": {
                "decision": "TASK",
                "card": {
                    "goal": "Delete 'bob.txt'.",
                    "stages": [
                        {
                            "stage_goal": "Delete the file 'bob.txt'.",
                            "stage_type": "FILE_WORK",
                            "success_condition": "'bob.txt' does not exist in the workspace.",
                            "active_targets": ["bob.txt"],
                            "declared_exact_targets": ["bob.txt"],
                        }
                    ],
                },
            },
        }
    )

    file_target_confirmation_yes_result = detect_route_interceptor(
        "sure",
        [{"role": "system", "content": pending_file_confirmation_message, "hidden": True}],
    )

    file_target_confirmation_cancel_result = detect_route_interceptor(
        "never mind",
        [{"role": "system", "content": pending_file_confirmation_message, "hidden": True}],
    )

    lookup_source_followup_result = normalize_route_decision(
        {"decision": "CHAT"},
        "workspace files",
        [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: Search for grocery.\n"
                    "Task goal: Clarify lookup source (web vs workspace) for: grocery\n"
                    "Execution status: PAUSED / WAITING_FOR_USER\n"
                    "Runtime note: Awaiting the user's source choice.\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
                    "Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            },
            {
                "role": "assistant",
                "content": 'Did you want me to search the web for "grocery", or look for it in your workspace files?',
            },
            {"role": "user", "content": "workspace files"},
        ],
    )

    lookup_source_web_followup_result = normalize_route_decision(
        {"decision": "CHAT"},
        "web pls",
        [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: search for MLPerf Inference v5.0 benchmark results\n"
                    "Task goal: Clarify lookup source (web vs workspace) for: MLPerf Inference v5.0 benchmark results\n"
                    "Execution status: PAUSED / WAITING_FOR_USER\n"
                    "Runtime note: Awaiting the user's source choice.\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
                    "Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            },
            {
                "role": "assistant",
                "content": 'Did you want me to search the web for "MLPerf Inference v5.0 benchmark results", or look for it in your workspace files?',
            },
            {"role": "user", "content": "web pls"},
        ],
    )

    web_offer_affirmative_followup_result = normalize_route_decision(
        {"decision": "CHAT"},
        "Sure, go ahead",
        [
            {
                "role": "assistant",
                "content": (
                    "The moon phase I mentioned came from the environment date, not a live web query. "
                    "If you require the precise, real-time ephemeris data for this exact moment, "
                    "I can initiate a search. Shall I check?"
                ),
            },
            {"role": "user", "content": "Sure, go ahead"},
        ],
    )

    contextual_lookup_route_result = normalize_route_decision(
        {"decision": "SEARCH", "card": {"query": "grocery"}},
        "Maybe just search for grocery?",
        [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: Can you tell me what it says in the grocery list?\n"
                    "Task goal: Read the grocery list file.\n"
                    "Execution status: FILE OPERATION SUCCESS\n"
                    "Runtime note: Read grocery_list.txt.\n"
                    "Relevant paths: grocery_list.txt\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
                    "Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            },
            {"role": "assistant", "content": "grocery_list.txt:\nApples\nBananas"},
            {"role": "user", "content": "Maybe just search for grocery?"},
        ],
    )

    followup_orc = SimpleNamespace(
        llm=_KeepRouteLLM(),
        ui=_CaptureUI(),
        user_msg="check to confirm?",
        cancel_token=None,
        prompt_context=SimpleNamespace(
            operational_state_service=None,
            knowledge_mgr=None,
        ),
    )
    task_event_verification_followup_result = _resolve_followup_route_with_llm(
        followup_orc,
        {
            "decision": "TASK",
            "card": {
                "goal": "Clarify lookup source (web vs workspace) for: to confirm",
                "stages": [
                    {
                        "stage_goal": "Ask whether the user wants web or workspace.",
                        "stage_type": "CHAT",
                        "success_condition": "A clarification question is ready.",
                    }
                ],
            },
        },
        [
            {
                "role": "assistant",
                "content": "Upcoming events: dentist appointment on 2026-03-24; Car insurance renewal on 2026-03-25.",
            },
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: remove the insurance one thats sorted\n"
                    "Task goal: Remove the event 'Car insurance renewal' from the active calendar\n"
                    "Execution status: EVENT REMOVED\n"
                    "Runtime note: Event removed: Car insurance renewal\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
                    "Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            },
            {"role": "user", "content": "check to confirm?"},
        ],
    )

    followup_orc.user_msg = "do it to be sure"
    memory_recall_affirmative_followup_result = _resolve_followup_route_with_llm(
        followup_orc,
        {
            "decision": "TASK",
            "card": {
                "goal": "Check durable memory and operational logs for the appointment time.",
                "stages": [
                    {
                        "stage_goal": "Retrieve the exact appointment time from durable memory or operational logs.",
                        "stage_type": "MEMORY_WORK",
                        "success_condition": "The exact appointment time is retrieved.",
                    }
                ],
            },
        },
        [
            {"role": "user", "content": "What time was the appointment?"},
            {
                "role": "assistant",
                "content": "I do not have the specific time at hand, but I can check memory and operational logs if you want me to.",
            },
            {"role": "user", "content": "do it to be sure"},
        ],
    )

    followup_orc.user_msg = "are you sure i said 9:30?"
    memory_recall_correction_followup_result = _resolve_followup_route_with_llm(
        followup_orc,
        {
            "decision": "TASK",
            "card": {
                "goal": "Search memory-related files for the appointment time.",
                "stages": [
                    {
                        "stage_goal": "Search workspace files for memory or operational logs mentioning the appointment time.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "Relevant memory or operational log files are found.",
                    }
                ],
            },
        },
        [
            {"role": "user", "content": "What time was the appointment?"},
            {
                "role": "assistant",
                "content": "I successfully retrieved the specific time of your appointment. It appears you said 09:30.",
            },
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: do it to be sure\n"
                    "Task goal: Retrieve the exact appointment time from durable memory or operational logs.\n"
                    "Execution status: WORLD STATE LISTED\n"
                    "Runtime note: [WORLD STATE]\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
                    "Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            },
            {"role": "user", "content": "are you sure i said 9:30?"},
        ],
    )

    followup_orc.user_msg = "attempt a recall pls"
    memory_recall_attempt_followup_result = _resolve_followup_route_with_llm(
        followup_orc,
        {
            "decision": "TASK",
            "card": {
                "goal": "Check durable memory and operational logs for the appointment time.",
                "stages": [
                    {
                        "stage_goal": "Retrieve the exact appointment time from durable memory or operational logs.",
                        "stage_type": "MEMORY_WORK",
                        "success_condition": "The exact appointment time is retrieved.",
                    }
                ],
            },
        },
        [
            {"role": "user", "content": "What time was the appointment?"},
            {
                "role": "assistant",
                "content": "The specific time has not been retrieved from the current operational logs. Shall I attempt to recall it from memory?",
            },
            {"role": "user", "content": "attempt a recall pls"},
        ],
    )

    followup_orc.user_msg = "i mean for the appointment"
    memory_recall_context_repair_followup_result = _resolve_followup_route_with_llm(
        followup_orc,
        {
            "decision": "TASK",
            "card": {
                "goal": "Find the workspace path that best matches 'e.g'.",
                "stages": [
                    {
                        "stage_goal": "Search workspace filenames for files matching 'e.g'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "Matching file paths are identified, or the absence of any plausible filename match is confirmed.",
                    }
                ],
            },
        },
        [
            {"role": "user", "content": "What time was the appointment?"},
            {
                "role": "assistant",
                "content": "The specific time has not been retrieved from the current operational logs. Shall I attempt to recall it from memory?",
            },
            {"role": "user", "content": "attempt a recall pls"},
            {
                "role": "assistant",
                "content": "No matching files found.",
            },
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: attempt a recall pls\n"
                    "Task goal: Find the workspace path that best matches 'e.g'.\n"
                    "Execution status: FILE OPERATION SUCCESS\n"
                    "Runtime note: FILE_LOOKUP_MATCHES:\n"
                    "Relevant paths: text_files/session_memory.txt\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
                    "Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            },
            {"role": "user", "content": "i mean for the appointment"},
        ],
    )

    followup_orc.user_msg = "when is my appointment tomorrow i mean"
    appointment_event_detail_followup_result = _resolve_followup_route_with_llm(
        followup_orc,
        {
            "decision": "TASK",
            "card": {
                "goal": "Find and retrieve the details of the user's appointment scheduled for tomorrow",
                "stages": [
                    {
                        "stage_goal": "Search the workspace file system for any files containing appointment details for tomorrow",
                        "stage_type": "FILE_WORK",
                        "success_condition": "Relevant appointment details are found in workspace files.",
                    }
                ],
            },
        },
        [
            {
                "role": "assistant",
                "content": "Upcoming events: dentist appointment on 2026-03-24; Car insurance renewal on 2026-03-25.",
            },
            {
                "role": "assistant",
                "content": "No matching files found.",
            },
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: attempt a recall pls\n"
                    "Task goal: Find the workspace path that best matches 'e.g'.\n"
                    "Execution status: FILE OPERATION SUCCESS\n"
                    "Runtime note: FILE_LOOKUP_MATCHES:\n"
                    "Relevant paths: text_files/session_memory.txt\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
                    "Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            },
            {"role": "user", "content": "when is my appointment tomorrow i mean"},
        ],
    )

    try:
        FollowupResolutionBoundary.validate(json.dumps({"decision": "delete_task"}))
    except BoundaryValidationError:
        pass
    else:
        raise AssertionError("expected FollowupResolutionBoundary to reject missing target")

    try:
        RouteClarifierBoundary.validate(json.dumps({"decision": "clarify_chat", "question": ""}))
    except BoundaryValidationError:
        pass
    else:
        raise AssertionError("expected RouteClarifierBoundary to reject empty clarify question")

    ui = _CaptureUI()
    fake_orc = SimpleNamespace(
        llm=_InvalidLLM(),
        ui=ui,
        user_msg="remember that",
        cancel_token=None,
        prompt_context=SimpleNamespace(
            operational_state_service=None,
            knowledge_mgr=None,
        ),
    )
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
    followup_wrapper_result = _resolve_followup_route_with_llm(
        fake_orc,
        bad_memory_route,
        [
            {"role": "user", "content": "I keep my passport in the top drawer."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": "remember that"},
        ],
    )

    fake_orc.user_msg = "temporary tree for profile bio"
    bad_file_route = {
        "decision": "TASK",
        "card": {
            "goal": "Create a temporary tree profile entry",
            "context": [],
            "stages": [
                {
                    "stage_goal": "Create a temporary tree profile entry",
                    "stage_type": "FILE_WORK",
                    "success_condition": "profile is updated",
                    "allowed_tools": ["FILE_OP", "RUN_CODE"],
                }
            ],
        },
    }
    clarifier_wrapper_result = _refine_ambiguous_task_route_with_llm(
        fake_orc,
        bad_file_route,
        [
            {"role": "assistant", "content": "What is missing?"},
            {"role": "user", "content": "temporary tree for profile bio"},
        ],
    )

    followup_validation_logged = any(
        kind == "agent_log" and "Follow-up resolver validation failed" in str(payload)
        for kind, payload in ui.events
    )
    clarifier_validation_logged = any(
        kind == "agent_log" and "Route clarifier validation failed" in str(payload)
        for kind, payload in ui.events
    )

    success = (
        dict(router_valid_task).get("decision") == "TASK"
        and str(((router_valid_task.get("card") or {}).get("goal") or "")).startswith("Remove bread")
        and dict(router_valid_search_confidence).get("source_scope") == "web"
        and dict(router_valid_search_confidence).get("confidence") == "high"
        and router_invalid_fallback == {"decision": "CHAT"}
        and dict(live_environment_route_result).get("decision") == "CHAT"
        and dict(explicit_web_search_route_result).get("decision") == "SEARCH"
        and "llama.cpp performance benchmarks" in str(((explicit_web_search_route_result.get("card") or {}).get("query") or "")).lower()
        and _is_lookup_source_clarification(benchmark_topic_search_route_result, "mlperf inference v5.0 benchmark results")
        and _is_lookup_source_clarification(dotted_topic_search_route_result, "llama.cpp benchmark results")
        and dict(ambiguous_lookup_route_result).get("decision") == "TASK"
        and str(((ambiguous_lookup_route_result.get("card") or {}).get("goal") or "")).lower().startswith("clarify lookup source")
        and str((((ambiguous_lookup_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_type") or "")) == "CHAT"
        and "web" in str((((ambiguous_lookup_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_goal") or "")).lower()
        and "workspace" in str((((ambiguous_lookup_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_goal") or "")).lower()
        and _is_lookup_source_clarification(high_confidence_web_lookup_route_result, "grocery")
        and _is_lookup_source_clarification(high_confidence_workspace_lookup_route_result, "grocery")
        and dict(benchmark_ambiguous_task_route_result).get("decision") == "TASK"
        and str((((benchmark_ambiguous_task_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_type") or "")) == "CHAT"
        and "web" in str((((benchmark_ambiguous_task_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_goal") or "")).lower()
        and "workspace" in str((((benchmark_ambiguous_task_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_goal") or "")).lower()
        and dict(dotted_ambiguous_task_route_result).get("decision") == "TASK"
        and str((((dotted_ambiguous_task_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_type") or "")) == "CHAT"
        and "llama.cpp benchmark results" in str(((dotted_ambiguous_task_route_result.get("card") or {}).get("goal") or "")).lower()
        and dict(compound_file_sequence_route_result).get("decision") == "TASK"
        and str(((compound_file_sequence_route_result.get("card") or {}).get("goal") or "")).lower().startswith("clarify the target details")
        and str((((compound_file_sequence_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_type") or "")) == "CHAT"
        and "do not invent" in " ".join(str(item) for item in ((compound_file_sequence_route_result.get("card") or {}).get("context") or [])).lower()
        and dict((file_state_correction_interceptor_result or {}).get("route_decision") or {}).get("decision") == "CHAT"
        and str((((file_state_correction_interceptor_result or {}).get("route_decision") or {}).get("system_notice") or {}).get("kind") or "") == "file_state_correction_ack"
        and "did not exist" in str((((file_state_correction_interceptor_result or {}).get("route_decision") or {}).get("system_notice") or {}).get("reply") or "").lower()
        and str((file_target_correction_interceptor_result or {}).get("next_stage") or "") == "UNDO"
        and str((((file_target_correction_interceptor_result or {}).get("route_decision") or {}).get("system_notice") or {}).get("correct_target") or "") == "bob"
        and str((((file_target_correction_interceptor_result or {}).get("route_decision") or {}).get("system_notice") or {}).get("wrong_target") or "") == "b.txt"
        and str((file_target_confirmation_yes_result or {}).get("next_stage") or "") == "MANAGER"
        and str((((file_target_confirmation_yes_result or {}).get("route_decision") or {}).get("card") or {}).get("goal") or "").lower() == "delete 'b.txt'."
        and str((((((file_target_confirmation_yes_result or {}).get("route_decision") or {}).get("card") or {}).get("stages") or [{}])[0].get("active_targets") or [""])[0]).lower() == "b.txt"
        and str((((((file_target_confirmation_yes_result or {}).get("route_decision") or {}).get("card") or {}).get("stages") or [{}])[0].get("declared_exact_targets") or [""])[0]).lower() == "b.txt"
        and str((file_target_confirmation_cancel_result or {}).get("next_stage") or "") == "PERSONA"
        and str((((file_target_confirmation_cancel_result or {}).get("route_decision") or {}).get("system_notice") or {}).get("kind") or "") == "file_target_confirmation_cancelled"
        and dict(lookup_source_followup_result).get("decision") == "TASK"
        and "grocery" in str(((lookup_source_followup_result.get("card") or {}).get("goal") or "")).lower()
        and str((((lookup_source_followup_result.get("card") or {}).get("stages") or [{}])[0].get("stage_type") or "")) == "FILE_WORK"
        and dict(lookup_source_web_followup_result).get("decision") == "SEARCH"
        and "mlperf inference v5.0 benchmark results" in str(((lookup_source_web_followup_result.get("card") or {}).get("query") or "")).lower()
        and dict(web_offer_affirmative_followup_result).get("decision") == "SEARCH"
        and "ephemeris" in str(((web_offer_affirmative_followup_result.get("card") or {}).get("query") or "")).lower()
        and dict(contextual_lookup_route_result).get("decision") == "TASK"
        and str((((contextual_lookup_route_result.get("card") or {}).get("stages") or [{}])[0].get("stage_type") or "")) == "FILE_WORK"
        and "clarify lookup source" not in str(((contextual_lookup_route_result.get("card") or {}).get("goal") or "")).lower()
        and dict(task_event_verification_followup_result or {}).get("decision") == "CHAT"
        and "events do i have scheduled" in str(((task_event_verification_followup_result or {}).get("card") or {}).get("query") or "").lower()
        and dict(memory_recall_affirmative_followup_result or {}).get("decision") == "CHAT"
        and str(((memory_recall_affirmative_followup_result or {}).get("card") or {}).get("query") or "").lower() == "what time was the appointment?"
        and dict(memory_recall_correction_followup_result or {}).get("decision") == "CHAT"
        and str(((memory_recall_correction_followup_result or {}).get("card") or {}).get("query") or "").lower() == "what time was the appointment?"
        and dict(memory_recall_attempt_followup_result or {}).get("decision") == "CHAT"
        and str(((memory_recall_attempt_followup_result or {}).get("card") or {}).get("query") or "").lower() == "what time was the appointment?"
        and dict(memory_recall_context_repair_followup_result or {}).get("decision") == "CHAT"
        and str(((memory_recall_context_repair_followup_result or {}).get("card") or {}).get("query") or "").lower() == "what time was the appointment?"
        and dict(appointment_event_detail_followup_result or {}).get("decision") == "CHAT"
        and str(((appointment_event_detail_followup_result or {}).get("card") or {}).get("query") or "").lower() == "what events do i have scheduled tomorrow?"
        and followup_wrapper_result is None
        and clarifier_wrapper_result is None
        and followup_validation_logged
        and clarifier_validation_logged
    )

    return RouteBoundaryReport(
        success=bool(success),
        router_valid_task=dict(router_valid_task),
        router_valid_search_confidence=dict(router_valid_search_confidence),
        router_invalid_fallback=router_invalid_fallback,
        live_environment_route_result=dict(live_environment_route_result),
        explicit_web_search_route_result=dict(explicit_web_search_route_result),
        benchmark_topic_search_route_result=dict(benchmark_topic_search_route_result),
        dotted_topic_search_route_result=dict(dotted_topic_search_route_result),
        ambiguous_lookup_route_result=dict(ambiguous_lookup_route_result),
        high_confidence_web_lookup_route_result=dict(high_confidence_web_lookup_route_result),
        high_confidence_workspace_lookup_route_result=dict(high_confidence_workspace_lookup_route_result),
        benchmark_ambiguous_task_route_result=dict(benchmark_ambiguous_task_route_result),
        dotted_ambiguous_task_route_result=dict(dotted_ambiguous_task_route_result),
        compound_file_sequence_route_result=dict(compound_file_sequence_route_result),
        file_state_correction_interceptor_result=dict(file_state_correction_interceptor_result or {}),
        file_target_correction_interceptor_result=dict(file_target_correction_interceptor_result or {}),
        file_target_confirmation_yes_result=dict(file_target_confirmation_yes_result or {}),
        file_target_confirmation_cancel_result=dict(file_target_confirmation_cancel_result or {}),
        lookup_source_followup_result=dict(lookup_source_followup_result),
        lookup_source_web_followup_result=dict(lookup_source_web_followup_result),
        web_offer_affirmative_followup_result=dict(web_offer_affirmative_followup_result),
        contextual_lookup_route_result=dict(contextual_lookup_route_result),
        task_event_verification_followup_result=dict(task_event_verification_followup_result or {}),
        memory_recall_affirmative_followup_result=dict(memory_recall_affirmative_followup_result or {}),
        memory_recall_correction_followup_result=dict(memory_recall_correction_followup_result or {}),
        memory_recall_attempt_followup_result=dict(memory_recall_attempt_followup_result or {}),
        memory_recall_context_repair_followup_result=dict(memory_recall_context_repair_followup_result or {}),
        appointment_event_detail_followup_result=dict(appointment_event_detail_followup_result or {}),
        followup_wrapper_result=followup_wrapper_result,
        clarifier_wrapper_result=clarifier_wrapper_result,
        followup_validation_logged=followup_validation_logged,
        clarifier_validation_logged=clarifier_validation_logged,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
