from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence

from core.contracts import FollowupResolution, KnowledgeMutationIntent, RouteDecision
from core.json_utils import parse_json_response
from core.runtime_context import extract_latest_runtime_context_fields, extract_previous_user_message
from core.task_event_context import extract_recent_visible_targets

_AMBIGUOUS_REFERENCE_RE = re.compile(r"(?i)\b(it|that|this|them|those|fact|records?|log|logs|list|left)\b")
_CONTEXTUAL_REMEMBER_RE = re.compile(
    r"(?is)^\s*(?:just\s+)?(?:remember|don't forget|dont forget)"
    r"(?:\s+(?:that(?:\s+fact)?|it|this|the fact))?\s*[.?!]*\s*$"
)
_AMBIGUOUS_MEMORY_FOLLOWUP_RE = re.compile(
    r"(?is)\b(?:remember|forget|remove|delete)\b[^.?!]*\b(?:it|that|this|fact|memory)\b"
    r"|^\s*(?:just\s+)?(?:remember|forget|remove|delete)(?:\s+(?:it|that|this|the fact|that fact))?\s*[.?!]*\s*$"
)
_FOLLOWUP_ACTION_RE = re.compile(
    r"(?i)\b(remove|delete|drop|clear|forget|remember|done|did|completed|complete|finished|went|attended|moved|archive|cancel|great|okay|ok|sure|yes|no|thanks|thank you|left)\b"
)
_READONLY_SHORT_RE = re.compile(
    r"(?i)^\s*(?:any|what(?:'s| is)?|do i have|is it|is that|is this|anything)\b"
)
_TASK_WORD_RE = re.compile(r"(?i)\b(task|tasks|to-?do|to-?do list|pending)\b")
_EVENT_WORD_RE = re.compile(r"(?i)\b(event|events|calendar|schedule|schedules|scheduled)\b")
_MEMORY_WORD_RE = re.compile(r"(?i)\b(memory|knowledge|world state|world model)\b")
_ACK_ONLY_RE = re.compile(
    r"(?is)^\s*(?:great|okay|ok|alright|all right|sure|thanks|thank you|nice|perfect|good)\s*[.!?]*\s*$"
)
_THINKING_RE = re.compile(r"(?is)^\s*thinking\.\.\.\s*$")


class FollowupResolutionEngine:
    def __init__(self, *, state_mutation_engine: Any | None = None) -> None:
        self.state_mutation_engine = state_mutation_engine

    def should_resolve(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Iterable[dict[str, Any]] | None = None,
    ) -> bool:
        text = str(user_msg or "").strip()
        if not text or text.startswith("/"):
            return False

        tokens = re.findall(r"[a-z0-9']+", text.lower())
        runtime = extract_latest_runtime_context_fields(recent_history or [])
        has_runtime_task_context = str(runtime.get("previous_route") or "").strip().upper() == "TASK"
        route_is_task = str((decision or {}).get("decision") or "").strip().upper() == "TASK"
        has_recent_state_context = self._history_has_state_context(recent_history or [])
        previous_user_msg = extract_previous_user_message(recent_history or [], current_text=text)

        if previous_user_msg:
            if self.looks_like_contextual_remember_followup(text):
                return True
            if self.looks_like_ambiguous_memory_followup(text):
                return True

        if _READONLY_SHORT_RE.search(text) and (_TASK_WORD_RE.search(text) or _EVENT_WORD_RE.search(text)):
            return True
        if _ACK_ONLY_RE.match(text) and (has_runtime_task_context or route_is_task):
            return True
        if _AMBIGUOUS_REFERENCE_RE.search(text) and (_FOLLOWUP_ACTION_RE.search(text) or len(tokens) <= 8):
            return has_runtime_task_context or route_is_task or has_recent_state_context
        if len(tokens) <= 8 and (has_runtime_task_context or route_is_task) and _FOLLOWUP_ACTION_RE.search(text):
            return True
        return False

    def refine_with_llm(
        self,
        *,
        llm: Any,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Iterable[dict[str, Any]] | None = None,
        operational_state_service: Any | None = None,
        knowledge_mgr: Any | None = None,
        cancel_token: Any | None = None,
    ) -> RouteDecision | None:
        if not self.should_resolve(decision=decision, user_msg=user_msg, recent_history=recent_history):
            return None

        history_items = list(recent_history or [])
        state_payload = self._build_state_payload(
            operational_state_service=operational_state_service,
            knowledge_mgr=knowledge_mgr,
        )
        deterministic_fallback = self._build_deterministic_fallback_route(
            decision=decision,
            user_msg=user_msg,
            recent_history=history_items,
            state_payload=state_payload,
        )

        messages = self._build_classifier_messages(
            decision=decision,
            user_msg=user_msg,
            recent_history=history_items,
            operational_state_service=operational_state_service,
            knowledge_mgr=knowledge_mgr,
            state_payload=state_payload,
        )
        raw = llm.generate(messages, temperature=0.0, cancel_token=cancel_token)
        payload = parse_json_response(raw)
        resolution = self._parse_resolution_payload(payload)
        route = self._build_route_from_resolution(resolution)
        if self._should_prefer_fallback_route(
            llm_route=route,
            fallback_route=deterministic_fallback,
            user_msg=user_msg,
        ):
            return deterministic_fallback
        return route or deterministic_fallback

    def _build_classifier_messages(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Iterable[dict[str, Any]],
        operational_state_service: Any | None,
        knowledge_mgr: Any | None,
        state_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        history_tail = []
        for item in list(recent_history or [])[-8:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if not role or not content or _THINKING_RE.match(content):
                continue
            history_tail.append({"role": role, "content": content})

        runtime = extract_latest_runtime_context_fields(recent_history or [])
        if state_payload is None:
            state_payload = self._build_state_payload(
                operational_state_service=operational_state_service,
                knowledge_mgr=knowledge_mgr,
            )
        sys_prompt = (
            "Resolve the latest user follow-up for Piper.\n"
            "Return ONLY valid JSON with keys: decision, target, value, query, question, confidence, reason.\n"
            "Allowed decisions: keep_route, chat, clarify, complete_task, delete_task, complete_event, delete_event, "
            "store_knowledge, remove_knowledge, query_tasks, query_events, query_tasks_and_events, query_memory.\n"
            "Rules:\n"
            "- Use current tasks/events/world state and [LATEST_RUNTIME_CONTEXT] as authoritative over assistant narration.\n"
            "- Treat current_route_decision as tentative. Override it when live state and runtime context point elsewhere.\n"
            "- Prefer task/event actions when there is an active matching task/event in current state.\n"
            "- For done/completed/went/handled phrasing about an active record, choose complete_task or complete_event.\n"
            "- For remove/delete/cancel phrasing about an active task/event, choose delete_task or delete_event.\n"
            "- Choose store_knowledge when the user asks to store a durable personal fact, preference, or past event.\n"
            "- 'Just remember that', 'remember that', or 'don't forget that' after a personal statement in recent history means store_knowledge; use the recent personal statement as both target and value.\n"
            "- Personal statements include: things the user did, experienced, owns, prefers, or wants recorded. They do not need to follow 'my X is Y' form.\n"
            "- Only choose remove_knowledge when the user explicitly asks to remove or forget a stored fact.\n"
            "- For remove_knowledge, set target to the exact attribute label from memory_summary (the text before the colon in a '- Label: value' line). Never invent a key; use only labels that appear in memory_summary.\n"
            "- If the user's removal request matches multiple stored facts, choose remove_knowledge for the single most specific match and set target to its exact label.\n"
            "- Words like records, log, or list usually refer to operational records if the active context is task/event.\n"
            "- query_tasks / query_events / query_tasks_and_events are for readonly status checks.\n"
            "- If the turn is just acknowledgement with no requested action, choose chat.\n"
            "- Only choose clarify if you genuinely cannot identify any personal statement in recent history to store; do not clarify when a clear recent statement exists.\n"
        )
        payload = {
            "latest_user_input": str(user_msg or "").strip(),
            "current_route_decision": decision,
            "latest_runtime_context": runtime,
            "recent_history": history_tail,
            "current_state": state_payload,
        }
        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]

    def _build_state_payload(
        self,
        *,
        operational_state_service: Any | None,
        knowledge_mgr: Any | None,
    ) -> dict[str, Any]:
        tasks: list[dict[str, str]] = []
        events: list[dict[str, str]] = []
        if operational_state_service is not None:
            try:
                snapshot = operational_state_service.snapshot(query="", horizon_days=3650)
                tasks = [dict(item) for item in getattr(snapshot, "tasks", [])]
                events = [dict(item) for item in getattr(snapshot, "events", [])]
            except Exception:
                tasks = []
                events = []
        memory_summary = ""
        if knowledge_mgr is not None:
            try:
                memory_summary = str(knowledge_mgr.list_for_display() or "").strip()
            except Exception:
                try:
                    memory_summary = str(knowledge_mgr.render_prompt_state("", max_entities=8) or "").strip()
                except Exception:
                    memory_summary = ""
        return {
            "tasks": tasks,
            "events": events,
            "memory_summary": memory_summary[:1200],
        }

    def _build_deterministic_fallback_route(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
        state_payload: dict[str, Any],
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        lower = text.lower()
        if not text or _MEMORY_WORD_RE.search(lower):
            return None
        if _ACK_ONLY_RE.match(text):
            return {"decision": "CHAT"}
        if _READONLY_SHORT_RE.search(text):
            if _TASK_WORD_RE.search(text) and _EVENT_WORD_RE.search(text):
                return self._build_readonly_chat_route("What tasks and events do I have scheduled?")
            if _TASK_WORD_RE.search(text):
                return self._build_readonly_chat_route("What tasks do I have right now?")
            if _EVENT_WORD_RE.search(text):
                return self._build_readonly_chat_route("What events do I have scheduled?")

        wants_delete = bool(re.search(r"(?i)\b(remove|delete|drop|clear|cancel)\b", text))
        wants_complete = bool(re.search(r"(?i)\b(done|did|completed|complete|finished|went|attended|handled|bought)\b", text))
        if not (wants_delete or wants_complete):
            return None

        recent_tasks, recent_events = extract_recent_visible_targets(recent_history)
        active_tasks = [str(item.get("name") or "").strip() for item in state_payload.get("tasks") or [] if str(item.get("name") or "").strip()]
        active_events = [str(item.get("name") or "").strip() for item in state_payload.get("events") or [] if str(item.get("name") or "").strip()]

        task_targets = recent_tasks or active_tasks
        event_targets = recent_events or active_events

        wants_task = bool(_TASK_WORD_RE.search(text))
        wants_event = bool(_EVENT_WORD_RE.search(text))

        if wants_task and len(task_targets) == 1:
            return self._build_task_event_completion_card(task_targets[0], is_event=False) if wants_complete else self._build_task_event_delete_card(task_targets[0], is_event=False)
        if wants_event and len(event_targets) == 1:
            return self._build_task_event_completion_card(event_targets[0], is_event=True) if wants_complete else self._build_task_event_delete_card(event_targets[0], is_event=True)
        if recent_tasks and not recent_events and len(recent_tasks) == 1:
            return self._build_task_event_completion_card(recent_tasks[0], is_event=False) if wants_complete else self._build_task_event_delete_card(recent_tasks[0], is_event=False)
        if recent_events and not recent_tasks and len(recent_events) == 1:
            return self._build_task_event_completion_card(recent_events[0], is_event=True) if wants_complete else self._build_task_event_delete_card(recent_events[0], is_event=True)
        if len(task_targets) == 1 and len(event_targets) == 0:
            return self._build_task_event_completion_card(task_targets[0], is_event=False) if wants_complete else self._build_task_event_delete_card(task_targets[0], is_event=False)
        if len(event_targets) == 1 and len(task_targets) == 0:
            return self._build_task_event_completion_card(event_targets[0], is_event=True) if wants_complete else self._build_task_event_delete_card(event_targets[0], is_event=True)
        return None

    def _should_prefer_fallback_route(
        self,
        *,
        llm_route: RouteDecision | None,
        fallback_route: RouteDecision | None,
        user_msg: str,
    ) -> bool:
        if fallback_route is None:
            return False
        if llm_route is None:
            return True
        if _MEMORY_WORD_RE.search(str(user_msg or "").lower()):
            return False
        if self._route_is_generic_memory_route(llm_route):
            return True
        if str((llm_route or {}).get("decision") or "").strip().upper() == "CHAT" and str((fallback_route or {}).get("decision") or "").strip().upper() == "TASK":
            return True
        return False

    def _parse_resolution_payload(self, payload: dict[str, Any]) -> FollowupResolution:
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {
            "keep_route",
            "chat",
            "clarify",
            "complete_task",
            "delete_task",
            "complete_event",
            "delete_event",
            "store_knowledge",
            "remove_knowledge",
            "query_tasks",
            "query_events",
            "query_tasks_and_events",
            "query_memory",
        }:
            return FollowupResolution()
        confidence = str(payload.get("confidence") or "").strip().lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        return FollowupResolution(
            decision=decision,
            target=str(payload.get("target") or "").strip(),
            value=str(payload.get("value") or "").strip(),
            query=str(payload.get("query") or "").strip(),
            question=" ".join(str(payload.get("question") or "").split()).strip(),
            confidence=confidence,
            reason=str(payload.get("reason") or "").strip(),
        )

    def _build_route_from_resolution(self, resolution: FollowupResolution) -> RouteDecision | None:
        decision = resolution.decision
        if decision == "keep_route":
            return None
        if decision == "chat":
            return {"decision": "CHAT"}
        if decision == "clarify":
            return self._build_clarification_route(resolution.question)
        if decision == "complete_task" and resolution.target:
            return self._build_task_event_completion_card(resolution.target, is_event=False)
        if decision == "delete_task" and resolution.target:
            return self._build_task_event_delete_card(resolution.target, is_event=False)
        if decision == "complete_event" and resolution.target:
            return self._build_task_event_completion_card(resolution.target, is_event=True)
        if decision == "delete_event" and resolution.target:
            return self._build_task_event_delete_card(resolution.target, is_event=True)
        if decision == "store_knowledge" and resolution.target and resolution.value:
            return self._build_memory_store_card(resolution.target, resolution.value)
        if decision == "remove_knowledge" and resolution.target:
            return self._build_memory_remove_card(resolution.target)
        if decision == "query_tasks":
            return self._build_readonly_chat_route(resolution.query or "What tasks do I have right now?")
        if decision == "query_events":
            return self._build_readonly_chat_route(resolution.query or "What events do I have scheduled?")
        if decision == "query_tasks_and_events":
            return self._build_readonly_chat_route(
                resolution.query or "What tasks and events do I have scheduled?"
            )
        if decision == "query_memory" and resolution.target:
            return self._build_readonly_chat_route(
                resolution.query or f"What do you know about {resolution.target}?"
            )
        if decision == "query_memory":
            return self._build_clarification_route("Which memory did you want me to check?")
        return None

    @staticmethod
    def _route_is_generic_memory_route(route: RouteDecision) -> bool:
        if str((route or {}).get("decision") or "").strip().upper() != "TASK":
            return False
        card = dict((route or {}).get("card") or {})
        stages = [dict(stage) for stage in (card.get("stages") or []) if isinstance(stage, dict)]
        if not stages:
            return False
        if not any(str(stage.get("stage_type") or "").strip().upper() == "MEMORY_WORK" for stage in stages):
            return False
        blob = " ".join(
            [
                str(card.get("goal") or ""),
                *(str(stage.get("stage_goal") or "") for stage in stages),
            ]
        ).lower()
        return bool(re.search(r"\b(?:it|that|this|them|those)\b", blob))

    @staticmethod
    def _build_readonly_chat_route(query: str) -> RouteDecision:
        return {
            "decision": "CHAT",
            "card": {
                "query": str(query or "").strip(),
            },
        }

    @staticmethod
    def _build_clarification_route(question: str) -> RouteDecision:
        question_text = str(question or "").strip() or "What exactly did you want me to do?"
        return {
            "decision": "TASK",
            "card": {
                "goal": "Clarify the user's follow-up request before acting.",
                "context": [
                    "The latest follow-up could not be resolved confidently to one target or domain.",
                    f"Preferred clarification question: {question_text}",
                ],
                "stages": [
                    {
                        "stage_goal": f"Ask the user: {question_text}",
                        "stage_type": "CHAT",
                        "success_condition": "A concise clarification question is ready for the user.",
                        "allowed_tools": [],
                    }
                ],
            },
        }

    def _build_task_event_delete_card(self, subject: str, *, is_event: bool) -> RouteDecision:
        if self.state_mutation_engine is not None:
            return self.state_mutation_engine.build_task_event_delete_route(subject=subject, is_event=is_event)
        tool_name = "REMOVE_EVENT" if is_event else "DELETE_TASK"
        noun = "event" if is_event else "task"
        list_name = "calendar" if is_event else "task list"
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Delete the {noun} '{subject}' from the active {list_name}",
                "context": [
                    f"The user asked to remove an existing {noun} from the active {list_name}.",
                    f"Use {tool_name} for cleanup or cancellation, not completion.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Remove the {noun} '{subject}' from the active {list_name}",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": f"Active {noun} is removed from the {list_name} without treating it as completed",
                        "allowed_tools": [tool_name],
                    }
                ],
            },
        }

    def _build_task_event_completion_card(self, subject: str, *, is_event: bool) -> RouteDecision:
        if self.state_mutation_engine is not None:
            return self.state_mutation_engine.build_task_event_completion_route(subject=subject, is_event=is_event)
        tool_name = "COMPLETE_EVENT" if is_event else "COMPLETE_TASK"
        list_tool_name = "LIST_EVENTS" if is_event else "LIST_TASKS"
        noun = "event" if is_event else "task"
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Complete the {noun} '{subject}'",
                "context": [
                    f"The user indicated they completed the {noun}.",
                    "Use the latest runtime context as the authoritative source for the active target.",
                    f"If direct completion misses the target, inspect the active {noun} list once with {list_tool_name} and retry against the exact live record.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Mark the {noun} '{subject}' as completed and archive it",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": f"Active {noun} is removed from the list and the completion is archived as memory",
                        "allowed_tools": [tool_name, list_tool_name],
                    }
                ],
            },
        }

    def _build_memory_store_card(self, subject: str, value: str) -> RouteDecision:
        if self.state_mutation_engine is not None:
            route = self.state_mutation_engine.build_memory_store_route(subject=subject, value=value)
            if route is not None:
                return route
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Store the user fact {subject} = {value}",
                "context": [
                    "The user explicitly asked to store a durable fact in memory.",
                    "Use durable knowledge memory, not tasks or events.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Store the durable user fact '{subject}' as '{value}'",
                        "stage_type": "MEMORY_WORK",
                        "success_condition": f"Knowledge store contains the fact {subject} = {value}",
                        "allowed_tools": ["UPDATE_KNOWLEDGE"],
                    }
                ],
            },
        }

    def _build_memory_remove_card(self, subject: str) -> RouteDecision:
        if self.state_mutation_engine is not None:
            route = self.state_mutation_engine.build_memory_remove_route(subject=subject)
            if route is not None:
                return route
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Remove the user fact '{subject}' from memory",
                "context": [
                    "The user explicitly asked to remove a durable fact from memory.",
                    "Use durable knowledge memory, not tasks or events.",
                    "If direct removal says the key was not found, inspect memory once with LIST_KNOWLEDGE to find the exact rendered fact or confirm it is already absent.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Remove the durable user fact '{subject}' from memory",
                        "stage_type": "MEMORY_WORK",
                        "success_condition": f"Knowledge store no longer contains the fact {subject}",
                        "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
                    }
                ],
            },
        }

    @staticmethod
    def looks_like_contextual_remember_followup(text: str) -> bool:
        return bool(_CONTEXTUAL_REMEMBER_RE.match(str(text or "").strip()))

    @staticmethod
    def looks_like_ambiguous_memory_followup(text: str) -> bool:
        return bool(_AMBIGUOUS_MEMORY_FOLLOWUP_RE.search(str(text or "").strip()))

    @staticmethod
    def _history_has_state_context(recent_history: Iterable[dict[str, Any]]) -> bool:
        for item in reversed(list(recent_history or [])):
            content = str(item.get("content") or "")
            lower = content.lower()
            if any(
                token in lower
                for token in (
                    "pending tasks:",
                    "upcoming events:",
                    "task added:",
                    "task deleted:",
                    "task completed and archived:",
                    "event scheduled:",
                    "event removed:",
                    "event completed and archived:",
                    "[world state]",
                    "knowledge removed:",
                    "knowledge already absent:",
                )
            ):
                return True
        return False
