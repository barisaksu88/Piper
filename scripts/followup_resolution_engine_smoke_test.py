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
    def generate(self, messages, temperature: float = 0.0, max_tokens: int | None = None, cancel_token=None):
        del max_tokens
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
    def generate(self, messages, temperature: float = 0.0, max_tokens: int | None = None, cancel_token=None):
        del max_tokens
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
    browser_followup_route: dict
    browser_title_followup_route: dict
    browser_topic_reply_route: dict
    browser_anything_else_route: dict
    browser_retrieve_details_route: dict
    browser_go_back_followup_route: dict
    browser_download_followup_route: dict


def run_smoke() -> FollowupResolutionEngineReport:
    with tempfile.TemporaryDirectory(prefix="piper-followup-resolution-") as tmp:
        data_dir = Path(tmp)
        owner = SharedStateOwner.for_data_dir(data_dir)
        owner.task_store.add("buy milk", "pending")
        owner.event_store.add("dentist appointment", "2027-06-15")
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
        browser_followup_history = [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: Open iana.org/domains/reserved in the browser and tell me the main heading.\n"
                    "Task goal: Use the browser to complete the requested interaction at 'https://iana.org/domains/reserved'.\n"
                    "Execution status: SUCCESS\n"
                    "Runtime note: The main heading at https://www.iana.org/domains/reserved is \"IANA-managed Reserved Domains\".\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                ),
            },
            {"role": "assistant", "content": "The main heading at https://www.iana.org/domains/reserved is \"IANA-managed Reserved Domains\"."},
            {"role": "user", "content": "what else is there"},
        ]
        browser_followup_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision={"decision": "CHAT"},
            user_msg="what else is there",
            recent_history=browser_followup_history,
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        browser_title_followup_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision={"decision": "CHAT"},
            user_msg="What's the title?",
            recent_history=browser_followup_history[:-1] + [{"role": "user", "content": "What's the title?"}],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        browser_topic_history = [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: what else is there\n"
                    "Task goal: Clarify the user's ambiguous request: what else is there\n"
                    "Execution status: PAUSED / AWAITING USER INPUT\n"
                    "Runtime note: PROPOSAL: Which specific piece of information from the Python license page would you like me to extract next?\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                ),
            },
            {
                "role": "assistant",
                "content": 'The page title at https://docs.python.org/3/license.html is "History and License — Python 3.14.3 documentation".',
            },
            {
                "role": "assistant",
                "content": "Which specific piece of information from the Python license page would you like me to extract next?",
            },
            {"role": "user", "content": "general info"},
        ]
        browser_topic_reply_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision={"decision": "TASK", "card": {"goal": "Clarify browser request", "stages": [{"stage_type": "CHAT"}]}},
            user_msg="general info",
            recent_history=browser_topic_history,
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        browser_anything_else_history = [
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    "Previous user request: general info\n"
                    "Task goal: Extract the 'general info' section from the Python license page\n"
                    "Execution status: SUCCESS\n"
                    "Runtime note: The page title at https://docs.python.org/3/license.html is \"History and License — Python 3.14.3 documentation\".\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                ),
            },
            {
                "role": "assistant",
                "content": 'The page title at https://docs.python.org/3/license.html is "History and License — Python 3.14.3 documentation".',
            },
            {"role": "user", "content": "anything else?"},
        ]
        browser_anything_else_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision={"decision": "CHAT"},
            user_msg="anything else?",
            recent_history=browser_anything_else_history,
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        browser_retrieve_details_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision={"decision": "CHAT"},
            user_msg="retrieve those details for me",
            recent_history=browser_anything_else_history[:-1]
            + [
                {
                    "role": "assistant",
                    "content": "If you wish to explore specific clauses, simply let me know and I shall retrieve those details for you.",
                },
                {"role": "user", "content": "retrieve those details for me"},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        browser_go_back_followup_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision={"decision": "CHAT"},
            user_msg="go back",
            recent_history=[
                {
                    "role": "system",
                    "content": (
                        "[LATEST_RUNTIME_CONTEXT]\n"
                        "Previous route: TASK\n"
                        "Previous user request: Open file:///fixture/index.html in the browser, click the next link, and tell me the page title.\n"
                        "Task goal: Use the browser to complete the requested interaction at 'file:///fixture/next.html'.\n"
                        "Execution status: SUCCESS\n"
                        "Runtime note: Arrived on the next page at file:///fixture/next.html.\n"
                        "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                    ),
                },
                {"role": "assistant", "content": "Arrived on the next page at file:///fixture/next.html."},
                {"role": "user", "content": "go back"},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )
        browser_download_followup_route = engine.refine_with_llm(
            llm=_FallbackOnlyLLM(),
            decision={"decision": "CHAT"},
            user_msg="download the quarterly report into browser_downloads",
            recent_history=[
                {
                    "role": "system",
                    "content": (
                        "[LATEST_RUNTIME_CONTEXT]\n"
                        "Previous route: TASK\n"
                        "Previous user request: Open http://127.0.0.1:9000/download_hub.html in the browser and tell me the page title.\n"
                        "Task goal: Use the browser to inspect the current page.\n"
                        "Execution status: SUCCESS\n"
                        "Runtime note: The page title at http://127.0.0.1:9000/download_hub.html is \"Download Hub Fixture\".\n"
                        "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                    ),
                },
                {"role": "assistant", "content": 'The page title at http://127.0.0.1:9000/download_hub.html is "Download Hub Fixture".'},
                {"role": "user", "content": "download the quarterly report into browser_downloads"},
            ],
            operational_state_service=ops,
            knowledge_mgr=knowledge,
        )

    delete_stage = dict((((delete_task_route or {}).get("card") or {}).get("stages") or [{}])[0])
    fallback_delete_stage = dict((((fallback_delete_task_route or {}).get("card") or {}).get("stages") or [{}])[0])
    complete_stage = dict((((complete_task_route or {}).get("card") or {}).get("stages") or [{}])[0])
    store_stage = dict((((store_memory_route or {}).get("card") or {}).get("stages") or [{}])[0])
    remove_stage = dict((((remove_memory_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_stage = dict((((browser_followup_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_title_stage = dict((((browser_title_followup_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_topic_stage = dict((((browser_topic_reply_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_anything_stage = dict((((browser_anything_else_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_details_stage = dict((((browser_retrieve_details_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_go_back_stage = dict((((browser_go_back_followup_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_meta = dict(browser_stage.get("computer_use") or {})
    browser_title_meta = dict(browser_title_stage.get("computer_use") or {})
    browser_topic_meta = dict(browser_topic_stage.get("computer_use") or {})
    browser_anything_meta = dict(browser_anything_stage.get("computer_use") or {})
    browser_details_meta = dict(browser_details_stage.get("computer_use") or {})
    browser_go_back_meta = dict(browser_go_back_stage.get("computer_use") or {})
    browser_download_stage = dict((((browser_download_followup_route or {}).get("card") or {}).get("stages") or [{}])[0])
    browser_download_meta = dict(browser_download_stage.get("computer_use") or {})

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
        and (browser_followup_route or {}).get("decision") == "TASK"
        and str(browser_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(browser_meta.get("start_url") or "") == "https://iana.org/domains/reserved"
        and str(browser_meta.get("selector_hint") or "") == "body"
        and "information about 'general info'" in str(browser_stage.get("stage_goal") or "").lower()
        and (browser_title_followup_route or {}).get("decision") == "TASK"
        and str(browser_title_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(browser_title_meta.get("start_url") or "") == "https://iana.org/domains/reserved"
        and bool(browser_title_meta.get("report_title"))
        and (browser_topic_reply_route or {}).get("decision") == "TASK"
        and str(browser_topic_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(browser_topic_meta.get("start_url") or "") == "https://docs.python.org/3/license.html"
        and str(browser_topic_meta.get("requested_topic") or "") == "general info"
        and str(browser_topic_meta.get("selector_hint") or "") == "body"
        and (browser_anything_else_route or {}).get("decision") == "TASK"
        and str(browser_anything_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(browser_anything_meta.get("start_url") or "") == "https://docs.python.org/3/license.html"
        and str(browser_anything_meta.get("selector_hint") or "") == "body"
        and (browser_retrieve_details_route or {}).get("decision") == "TASK"
        and str(browser_details_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(browser_details_meta.get("start_url") or "") == "https://docs.python.org/3/license.html"
        and str(browser_details_meta.get("selector_hint") or "") == "body"
        and (browser_go_back_followup_route or {}).get("decision") == "TASK"
        and str(browser_go_back_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(browser_go_back_meta.get("start_url") or "") == "file:///fixture/next.html"
        and str(browser_go_back_meta.get("history_navigation") or "") == "back"
        and (browser_download_followup_route or {}).get("decision") == "TASK"
        and str(browser_download_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(browser_download_meta.get("start_url") or "") == "http://127.0.0.1:9000/download_hub.html"
        and str(browser_download_meta.get("download_dir") or "") == "browser_downloads"
        and str(browser_download_meta.get("download_hint") or "") == "quarterly report"
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
        browser_followup_route=browser_followup_route or {},
        browser_title_followup_route=browser_title_followup_route or {},
        browser_topic_reply_route=browser_topic_reply_route or {},
        browser_anything_else_route=browser_anything_else_route or {},
        browser_retrieve_details_route=browser_retrieve_details_route or {},
        browser_go_back_followup_route=browser_go_back_followup_route or {},
        browser_download_followup_route=browser_download_followup_route or {},
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
