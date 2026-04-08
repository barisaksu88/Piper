from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence

from config import CFG
from core.browser_route_utils import build_browser_context_followup_route
from core.contracts import FollowupResolution, KnowledgeMutationIntent, RouteDecision
from core.route_boundary import FollowupResolutionBoundary
from core.runtime_context import extract_latest_runtime_context_fields, extract_previous_user_message
from core.task_event_context import extract_latest_task_event_candidates, extract_recent_visible_targets

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
# Affirmative replies to a system-initiated offer ("Should I…?", "Want me to…?")
_AFFIRMATIVE_CONFIRM_RE = re.compile(
    r"(?is)^\s*(?:yes\s*please|yes|yeah|yep|yup|sure|go ahead|please do|do it|sounds good|absolutely|definitely)\s*[.!?]*\s*$"
)
_SHORT_CONTEXTUAL_FOLLOWUP_MAX_TOKENS = 6
_SHORT_MEMORY_RECALL_FOLLOWUP_MAX_TOKENS = 10
# Heuristic: last assistant message looks like an offer/question to the user
_OFFER_PHRASE_RE = re.compile(
    r"(?i)\b(?:should i|shall i|want me to|would you like|do you want|can i|may i)\b|\?$"
)
_MEMORY_RECALL_OFFER_RE = re.compile(
    r"(?i)\b(?:recall|remember|memory|operational logs?|specific time|details at hand)\b"
)
_MEMORY_RECALL_COMMIT_RE = re.compile(
    r"(?i)\b(?:do|check|look|verify|confirm|double check|be sure|make sure|try|attempt|recall)\b"
)
_MEMORY_RECALL_CONTEXT_WRAPPER_RE = re.compile(r"(?i)^\s*(?:i mean|for\b|about\b|regarding\b|re\b)")
_NEGATIVE_OR_CANCEL_RE = re.compile(
    r"(?is)^\s*(?:no|nope|nah|cancel|stop|leave it|leave that|forget it|never mind|nevermind|no thanks|don't|dont|do not)\b"
)
_QUESTION_START_RE = re.compile(r"(?i)^\s*(?:what|why|how|when|where|who|which|is|are|can|could|would|did|do)\b")
_VERIFICATION_QUESTION_RE = re.compile(
    r"(?is)\?|\b(?:sure|really|right|correct|accurate|verify|confirm)\b"
)
_EVENT_DETAIL_HINT_RE = re.compile(
    r"(?i)\b(?:appointment|appointments|event|events|calendar|schedule|scheduled|deadline|deadlines|reminder|reminders|tomorrow|today|tonight|date|time|when)\b"
)
_THINKING_RE = re.compile(r"(?is)^\s*thinking\.\.\.\s*$")
_EXPLICIT_DEPENDENCY_OVERRIDE_RE = re.compile(
    r"(?is)^\s*(?:override(?:\s+it)?|proceed|continue|do it anyway|force it|ignore (?:the )?(?:lock|dependency)|yes(?:\s*,\s*override(?:\s+it)?)?)\s*[.!?]*\s*$"
)
_ACTIVE_DEPENDENCY_RUNTIME_RE = re.compile(
    r"ACTIVE_(?:TASK|EVENT)_DEPENDENCY:\s*Cannot\s+(?P<verb>delete|move)\s+'(?P<path>[^']+)':\s*"
    r"referenced by active (?P<kind>task|event) '(?P<name>[^']+)'\.",
    re.IGNORECASE,
)
_FILE_READBACK_FOLLOWUP_RE = re.compile(
    r"(?is)^\s*(?:read|show|display|open|print|tell\s+me)\s+(?:it|that|this)"
    r"(?:\s+back)?(?:\s+(?:exactly|verbatim|word\s+for\s+word|as\s+is))?\s*[.!?]*\s*$"
)
_DEPENDENCY_FILE_CLARIFICATION_RE = re.compile(
    r"(?is)^\s*(?:i\s+mean\s+)?(?:the\s+)?(?:file|document|workspace\s+file|path)\s*[.!?]*\s*$"
)


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
        history_list = list(recent_history or [])
        runtime = extract_latest_runtime_context_fields(history_list)
        has_runtime_task_context = str(runtime.get("previous_route") or "").strip().upper() == "TASK"
        route_is_task = str((decision or {}).get("decision") or "").strip().upper() == "TASK"
        has_recent_state_context = self._history_has_state_context(history_list)
        memory_recall_followup = self._should_resolve_memory_recall_followup(
            user_msg=text,
            recent_history=history_list,
        )
        event_detail_followup = self._should_resolve_event_detail_followup(
            user_msg=text,
            recent_history=history_list,
            runtime=runtime,
        )
        file_readback_followup = self._looks_like_file_readback_followup(
            user_msg=text,
            recent_history=history_list,
        )
        dependency_file_clarification = self._looks_like_dependency_file_clarification_followup(
            user_msg=text,
            recent_history=history_list,
        )
        dependency_override_followup = self._looks_like_dependency_override_followup(
            user_msg=text,
            recent_history=history_list,
        )

        # If the secretary already produced a FILE_WORK card, do not intercept it.
        # The followup resolver handles task/event/memory operations only; overriding
        # a correct FILE_WORK route causes mis-routing (e.g. file names read as task names).
        if route_is_task:
            stages = list(((decision or {}).get("card") or {}).get("stages") or [])
            if any(str(s.get("stage_type") or "").upper() == "FILE_WORK" for s in stages) and not (
                memory_recall_followup
                or event_detail_followup
                or file_readback_followup
                or dependency_file_clarification
                or dependency_override_followup
            ):
                return False
        previous_user_msg = extract_previous_user_message(history_list, current_text=text)

        if previous_user_msg:
            if self.looks_like_contextual_remember_followup(text):
                return True
            if self.looks_like_ambiguous_memory_followup(text):
                return True

        # Affirmative reply to a system-initiated offer ("Should I…?", "Go ahead").
        if _AFFIRMATIVE_CONFIRM_RE.match(text) and self._previous_assistant_was_offer(history_list):
            return True

        if _READONLY_SHORT_RE.search(text) and (_TASK_WORD_RE.search(text) or _EVENT_WORD_RE.search(text)):
            return True
        if memory_recall_followup:
            return True
        if event_detail_followup:
            return True
        if file_readback_followup:
            return True
        if dependency_file_clarification:
            return True
        if dependency_override_followup:
            return True
        if self._looks_like_browser_context_followup(
            user_msg=text,
            recent_history=history_list,
        ):
            return True
        if self._should_resolve_runtime_context_followup(
            decision=decision,
            user_msg=text,
            recent_history=history_list,
        ):
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

        # Deterministic path is PRIMARY. The LLM is only called for cases
        # that require understanding natural-language context: knowledge
        # mutations (store/remove fact) and affirmative replies to an offer.
        # Everything else that the deterministic route cannot resolve is
        # returned as None so the original router decision stands unmodified.
        deterministic = self._build_deterministic_fallback_route(
            decision=decision,
            user_msg=user_msg,
            recent_history=history_items,
            state_payload=state_payload,
        )
        if deterministic is not None:
            return deterministic

        text = str(user_msg or "").strip()
        needs_knowledge_llm = (
            self.looks_like_contextual_remember_followup(text)
            or self.looks_like_ambiguous_memory_followup(text)
            or (
                _AFFIRMATIVE_CONFIRM_RE.match(text)
                and self._previous_assistant_was_offer(history_items)
            )
        )
        if not needs_knowledge_llm:
            # No deterministic resolution and no knowledge-mutation signal —
            # pass through. Let the original router decision own this turn.
            return None

        messages = self._build_classifier_messages(
            decision=decision,
            user_msg=user_msg,
            recent_history=history_items,
            operational_state_service=operational_state_service,
            knowledge_mgr=knowledge_mgr,
            state_payload=state_payload,
        )
        raw = llm.generate(
            messages,
            temperature=0.0,
            max_tokens=int(getattr(CFG, "FOLLOWUP_RESOLUTION_MAX_TOKENS", 220)),
            cancel_token=cancel_token,
        )
        resolution = FollowupResolutionBoundary.validate(raw)
        return self._build_route_from_resolution(resolution)

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
        event_detail_route = self._build_event_detail_followup_route(
            user_msg=text,
            recent_history=recent_history,
            state_payload=state_payload,
        )
        if event_detail_route is not None:
            return event_detail_route
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
        memory_recall_route = self._build_memory_recall_followup_route(
            user_msg=text,
            recent_history=recent_history,
        )
        if memory_recall_route is not None:
            return memory_recall_route
        browser_followup_route = self._build_browser_context_followup_route(
            user_msg=text,
            recent_history=recent_history,
        )
        if browser_followup_route is not None:
            return browser_followup_route
        runtime_followup_route = self._build_runtime_context_followup_route(
            decision=decision,
            user_msg=text,
            recent_history=recent_history,
            state_payload=state_payload,
        )
        if runtime_followup_route is not None:
            return runtime_followup_route
        file_readback_route = self._build_file_readback_followup_route(
            user_msg=text,
            recent_history=recent_history,
        )
        if file_readback_route is not None:
            return file_readback_route
        dependency_file_clarification_route = self._build_dependency_file_clarification_route(
            user_msg=text,
            recent_history=recent_history,
        )
        if dependency_file_clarification_route is not None:
            return dependency_file_clarification_route
        dependency_override_route = self._build_dependency_override_followup_route(
            user_msg=text,
            recent_history=recent_history,
        )
        if dependency_override_route is not None:
            return dependency_override_route

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
        fallback_query = str(((fallback_route or {}).get("card") or {}).get("query") or "").strip()
        if (
            fallback_query
            and str((fallback_route or {}).get("decision") or "").strip().upper() == "CHAT"
            and str((llm_route or {}).get("decision") or "").strip().upper() == "TASK"
        ):
            return True
        if str((llm_route or {}).get("decision") or "").strip().upper() == "CHAT" and str((fallback_route or {}).get("decision") or "").strip().upper() == "TASK":
            return True
        if self._should_resolve_runtime_context_followup(
            decision=llm_route or {},
            user_msg=user_msg,
            recent_history=[],
        ) and str((fallback_route or {}).get("decision") or "").strip().upper() == "CHAT" and str((llm_route or {}).get("decision") or "").strip().upper() == "TASK":
            return True
        return False

    def _should_resolve_runtime_context_followup(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> bool:
        text = str(user_msg or "").strip()
        if not text or text.startswith("/"):
            return False
        if _ACK_ONLY_RE.match(text):
            return False
        if _MEMORY_WORD_RE.search(text):
            return False
        lower = text.lower()
        if any(token in lower for token in ("web", "workspace", "file", "files", "folder", "directory", "path", "filename")):
            return False
        tokens = re.findall(r"[a-z0-9']+", lower)
        if not tokens or len(tokens) > _SHORT_CONTEXTUAL_FOLLOWUP_MAX_TOKENS:
            return False
        runtime = extract_latest_runtime_context_fields(recent_history)
        if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
            return False
        return self._looks_like_lookup_source_clarification(decision)

    @staticmethod
    def _looks_like_lookup_source_clarification(decision: RouteDecision) -> bool:
        if str((decision or {}).get("decision") or "").strip().upper() != "TASK":
            return False
        card = dict((decision or {}).get("card") or {})
        goal = str(card.get("goal") or "").strip().lower()
        if goal.startswith("clarify lookup source"):
            return True
        stages = [dict(item) for item in (card.get("stages") or []) if isinstance(item, dict)]
        if not stages:
            return False
        first_stage = stages[0]
        stage_goal = str(first_stage.get("stage_goal") or "").strip().lower()
        return str(first_stage.get("stage_type") or "").strip().upper() == "CHAT" and "web" in stage_goal and "workspace" in stage_goal

    def _build_runtime_context_followup_route(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
        state_payload: dict[str, Any],
    ) -> RouteDecision | None:
        if not self._should_resolve_runtime_context_followup(
            decision=decision,
            user_msg=user_msg,
            recent_history=recent_history,
        ):
            return None

        event_candidates = extract_latest_task_event_candidates(recent_history, is_event=True)
        task_candidates = extract_latest_task_event_candidates(recent_history, is_event=False)
        if event_candidates and task_candidates:
            return self._build_readonly_chat_route("What tasks and events do I have scheduled?")
        if event_candidates:
            return self._build_readonly_chat_route("What events do I have scheduled?")
        if task_candidates:
            return self._build_readonly_chat_route("What tasks do I have right now?")

        recent_tasks, recent_events = extract_recent_visible_targets(recent_history)
        active_tasks = [str(item.get("name") or "").strip() for item in state_payload.get("tasks") or [] if str(item.get("name") or "").strip()]
        active_events = [str(item.get("name") or "").strip() for item in state_payload.get("events") or [] if str(item.get("name") or "").strip()]
        if recent_tasks and recent_events:
            return self._build_readonly_chat_route("What tasks and events do I have scheduled?")
        if recent_events and not recent_tasks:
            return self._build_readonly_chat_route("What events do I have scheduled?")
        if recent_tasks and not recent_events:
            return self._build_readonly_chat_route("What tasks do I have right now?")

        runtime = extract_latest_runtime_context_fields(recent_history)
        runtime_blob = " ".join(
            [
                str(runtime.get("previous_user_request") or ""),
                str(runtime.get("task_goal") or ""),
                str(runtime.get("runtime_note") or ""),
                str(runtime.get("last_log") or ""),
                str(runtime.get("execution_status") or ""),
            ]
        ).lower()
        if _EVENT_WORD_RE.search(runtime_blob):
            return self._build_readonly_chat_route("What events do I have scheduled?")
        if _TASK_WORD_RE.search(runtime_blob):
            return self._build_readonly_chat_route("What tasks do I have right now?")
        if active_tasks and active_events:
            return self._build_readonly_chat_route("What tasks and events do I have scheduled?")
        if active_events and not active_tasks:
            return self._build_readonly_chat_route("What events do I have scheduled?")
        if active_tasks and not active_events:
            return self._build_readonly_chat_route("What tasks do I have right now?")
        return None

    @staticmethod
    def _looks_like_browser_context_followup(
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> bool:
        return build_browser_context_followup_route(user_msg, recent_history) is not None

    @staticmethod
    def _build_browser_context_followup_route(
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        return build_browser_context_followup_route(user_msg, recent_history)

    def _build_dependency_override_followup_route(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        if not self._looks_like_dependency_override_followup(
            user_msg=user_msg,
            recent_history=recent_history,
        ):
            return None
        runtime = extract_latest_runtime_context_fields(recent_history)
        match = self._extract_active_dependency_runtime_match(recent_history)
        if not match:
            return None
        verb = str(match.group("verb") or "").strip().lower()
        path = str(match.group("path") or "").strip()
        kind = str(match.group("kind") or "").strip().lower()
        name = str(match.group("name") or "").strip()
        if not verb or not path:
            return None
        if verb == "delete":
            return self._build_file_dependency_override_delete_card(path=path, kind=kind, name=name)
        return self._build_file_dependency_override_move_card(
            path=path,
            kind=kind,
            name=name,
            runtime=runtime,
        )

    def _should_resolve_memory_recall_followup(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> bool:
        text = str(user_msg or "").strip()
        if not text or text.startswith("/"):
            return False
        lower = text.lower()
        if any(token in lower for token in ("web", "workspace", "file", "files", "folder", "directory", "path", "filename")):
            return False
        tokens = re.findall(r"[a-z0-9']+", lower)
        if not tokens or len(tokens) > _SHORT_MEMORY_RECALL_FOLLOWUP_MAX_TOKENS:
            return False
        previous_memory_offer = self._previous_assistant_implies_memory_recall(recent_history)
        if _AFFIRMATIVE_CONFIRM_RE.match(text) and previous_memory_offer:
            return True
        if (
            len(tokens) <= _SHORT_CONTEXTUAL_FOLLOWUP_MAX_TOKENS
            and previous_memory_offer
            and _MEMORY_RECALL_COMMIT_RE.search(text)
            and not _ACK_ONLY_RE.match(text)
        ):
            return True
        if (
            previous_memory_offer
            and len(tokens) <= _SHORT_CONTEXTUAL_FOLLOWUP_MAX_TOKENS
            and not _ACK_ONLY_RE.match(text)
            and not _NEGATIVE_OR_CANCEL_RE.match(text)
            and not _QUESTION_START_RE.match(text)
        ):
            return True
        runtime = extract_latest_runtime_context_fields(recent_history)
        if not self._runtime_context_implies_memory_lookup(runtime):
            return False
        if _VERIFICATION_QUESTION_RE.search(text):
            return True
        return (
            len(tokens) <= _SHORT_CONTEXTUAL_FOLLOWUP_MAX_TOKENS
            and not _ACK_ONLY_RE.match(text)
            and not _NEGATIVE_OR_CANCEL_RE.match(text)
            and not _QUESTION_START_RE.match(text)
        )

    def _should_resolve_event_detail_followup(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
        runtime: dict[str, str] | None = None,
    ) -> bool:
        text = str(user_msg or "").strip()
        if not text or text.startswith("/"):
            return False
        lower = text.lower()
        if any(token in lower for token in ("web", "workspace", "file", "files", "folder", "directory", "path", "filename")):
            return False
        if _MEMORY_WORD_RE.search(text):
            return False
        tokens = re.findall(r"[a-z0-9']+", lower)
        if not tokens or len(tokens) > 10:
            return False
        if not _EVENT_DETAIL_HINT_RE.search(text):
            return False
        runtime_fields = runtime or extract_latest_runtime_context_fields(recent_history)
        runtime_blob = " ".join(
            [
                str(runtime_fields.get("previous_user_request") or ""),
                str(runtime_fields.get("task_goal") or ""),
                str(runtime_fields.get("runtime_note") or ""),
                str(runtime_fields.get("last_log") or ""),
                str(runtime_fields.get("execution_status") or ""),
            ]
        ).lower()
        recent_tasks, recent_events = extract_recent_visible_targets(recent_history)
        event_candidates = extract_latest_task_event_candidates(recent_history, is_event=True)
        has_event_context = bool(
            recent_events
            or event_candidates
            or re.search(r"\b(?:appointment|event|events|calendar|schedule|scheduled|deadline|reminder)\b", runtime_blob)
        )
        if not has_event_context:
            return False
        if _QUESTION_START_RE.match(text):
            return True
        return bool(
            _MEMORY_RECALL_CONTEXT_WRAPPER_RE.match(text)
            or ("my" in tokens and ("appointment" in tokens or "tomorrow" in tokens))
        )

    def _build_event_detail_followup_route(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
        state_payload: dict[str, Any],
    ) -> RouteDecision | None:
        runtime = extract_latest_runtime_context_fields(recent_history)
        if not self._should_resolve_event_detail_followup(
            user_msg=user_msg,
            recent_history=recent_history,
            runtime=runtime,
        ):
            return None
        lower = str(user_msg or "").lower()
        if "tomorrow" in lower:
            return self._build_readonly_chat_route("What events do I have scheduled tomorrow?")
        if "today" in lower or "tonight" in lower:
            return self._build_readonly_chat_route("What events do I have scheduled today?")
        recent_tasks, recent_events = extract_recent_visible_targets(recent_history)
        active_events = [str(item.get("name") or "").strip() for item in state_payload.get("events") or [] if str(item.get("name") or "").strip()]
        if recent_events or active_events:
            return self._build_readonly_chat_route("What events do I have scheduled?")
        return None

    def _build_memory_recall_followup_route(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        if not self._should_resolve_memory_recall_followup(
            user_msg=user_msg,
            recent_history=recent_history,
        ):
            return None
        query = self._extract_previous_substantive_user_message(
            recent_history,
            current_text=user_msg,
        )
        if not query:
            runtime = extract_latest_runtime_context_fields(recent_history)
            previous_request = str(runtime.get("previous_user_request") or "").strip()
            if previous_request and not _AFFIRMATIVE_CONFIRM_RE.match(previous_request) and not _ACK_ONLY_RE.match(previous_request):
                query = previous_request
        if not query:
            query = self._derive_query_from_runtime_context(recent_history)
        if not query:
            return None
        return self._build_readonly_chat_route(query)

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
    def _previous_assistant_message(recent_history: Iterable[dict[str, Any]]) -> str:
        for item in reversed(list(recent_history or [])):
            if str(item.get("role") or "").strip().lower() != "assistant":
                continue
            content = str(item.get("content") or "").strip()
            if content and not _THINKING_RE.match(content):
                return content
        return ""

    def _previous_assistant_implies_memory_recall(self, recent_history: Iterable[dict[str, Any]]) -> bool:
        content = self._previous_assistant_message(recent_history)
        if not content:
            return False
        return bool(_OFFER_PHRASE_RE.search(content) and _MEMORY_RECALL_OFFER_RE.search(content))

    @staticmethod
    def _runtime_context_implies_memory_lookup(runtime: dict[str, str]) -> bool:
        blob = " ".join(
            [
                str(runtime.get("previous_user_request") or ""),
                str(runtime.get("task_goal") or ""),
                str(runtime.get("runtime_note") or ""),
                str(runtime.get("last_log") or ""),
                str(runtime.get("execution_status") or ""),
            ]
        ).lower()
        if not blob:
            return False
        return bool(
            re.search(r"\b(memory|recall|world state|world model|operational logs?|records?)\b", blob)
            or ("retrieve" in blob and ("time" in blob or "exact" in blob or "specific" in blob))
        )

    def _extract_previous_substantive_user_message(
        self,
        recent_history: Iterable[dict[str, Any]],
        *,
        current_text: str = "",
    ) -> str:
        current_clean = " ".join(str(current_text or "").split()).strip().lower()
        skipped_current = False
        for item in reversed(list(recent_history or [])):
            if str(item.get("role") or "").strip().lower() != "user":
                continue
            content = " ".join(str(item.get("content") or "").split()).strip()
            if not content:
                continue
            normalized = content.lower()
            if current_clean and normalized == current_clean and not skipped_current:
                skipped_current = True
                continue
            if _AFFIRMATIVE_CONFIRM_RE.match(content) or _ACK_ONLY_RE.match(content):
                continue
            if self._looks_like_memory_recall_control_text(content):
                continue
            return content
        return ""

    @staticmethod
    def _looks_like_memory_recall_control_text(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        tokens = re.findall(r"[a-z0-9']+", content.lower())
        if not tokens or len(tokens) > _SHORT_MEMORY_RECALL_FOLLOWUP_MAX_TOKENS:
            return False
        if _AFFIRMATIVE_CONFIRM_RE.match(content):
            return True
        if _MEMORY_RECALL_COMMIT_RE.search(content):
            return True
        return bool(
            _MEMORY_RECALL_CONTEXT_WRAPPER_RE.match(content)
            and not _QUESTION_START_RE.match(content)
        )

    def _derive_query_from_runtime_context(self, recent_history: Sequence[dict[str, Any]]) -> str:
        runtime = extract_latest_runtime_context_fields(recent_history)
        task_goal = str(runtime.get("task_goal") or "").strip()
        if not task_goal:
            return ""
        lowered = task_goal.lower()
        if "time" in lowered and "appointment" in lowered:
            return "What time was the appointment?"
        if "event" in lowered and "time" in lowered:
            return "What time was the event?"
        return ""

    @staticmethod
    def _build_readonly_chat_route(query: str) -> RouteDecision:
        return {
            "decision": "CHAT",
            "card": {
                "query": str(query or "").strip(),
            },
        }

    @staticmethod
    def _build_file_dependency_override_delete_card(*, path: str, kind: str, name: str) -> RouteDecision:
        clean_path = str(path or "").strip().replace("\\", "/")
        clean_kind = kind or "item"
        clean_name = name or "unknown"
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Delete '{clean_path}' with explicit dependency override authorization.",
                "context": [
                    "The workspace root is '.'.",
                    f"The blocked target file path is '{clean_path}'.",
                    f"The user explicitly authorized overriding the active {clean_kind} dependency '{clean_name}'.",
                    "Retry the FILE_WORK operation directly. Do not create TASK_EVENT_WORK stages and do not mutate task/event state as part of this override.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Delete the file '{clean_path}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"'{clean_path}' does not exist in the workspace.",
                        "allowed_tools": ["FILE_OP"],
                        "active_targets": [clean_path],
                        "dependency_override_authorized": True,
                    }
                ],
            },
        }

    @staticmethod
    def _build_file_dependency_retry_delete_card(*, path: str) -> RouteDecision:
        clean_path = str(path or "").strip().replace("\\", "/")
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Delete '{clean_path}'.",
                "context": [
                    "The workspace root is '.'.",
                    f"The blocked target file path is '{clean_path}'.",
                    "The user clarified they mean the file target. This is not permission to mutate task/event state or override the dependency block.",
                    "Retry the FILE_WORK operation directly and keep task/event state unchanged unless the user explicitly asks to modify it too.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Delete the file '{clean_path}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"'{clean_path}' does not exist in the workspace.",
                        "allowed_tools": ["FILE_OP"],
                        "active_targets": [clean_path],
                    }
                ],
            },
        }

    @staticmethod
    def _extract_active_dependency_runtime_match(
        recent_history: Sequence[dict[str, Any]],
    ) -> re.Match[str] | None:
        runtime = extract_latest_runtime_context_fields(recent_history)
        if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
            return None
        runtime_note = str(runtime.get("runtime_note") or "").strip()
        return _ACTIVE_DEPENDENCY_RUNTIME_RE.search(runtime_note)

    def _looks_like_dependency_override_followup(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> bool:
        text = str(user_msg or "").strip()
        if not text or _NEGATIVE_OR_CANCEL_RE.match(text) or _QUESTION_START_RE.match(text):
            return False
        if not (
            _AFFIRMATIVE_CONFIRM_RE.match(text)
            or _EXPLICIT_DEPENDENCY_OVERRIDE_RE.match(text)
        ):
            return False
        return self._extract_active_dependency_runtime_match(recent_history) is not None

    def _build_dependency_file_clarification_route(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        if not self._looks_like_dependency_file_clarification_followup(
            user_msg=user_msg,
            recent_history=recent_history,
        ):
            return None
        runtime = extract_latest_runtime_context_fields(recent_history)
        match = self._extract_active_dependency_runtime_match(recent_history)
        if not match:
            return None
        verb = str(match.group("verb") or "").strip().lower()
        path = str(match.group("path") or "").strip()
        if not path:
            return None
        if verb == "delete":
            return self._build_file_dependency_retry_delete_card(path=path)
        return self._build_file_dependency_retry_move_card(path=path, runtime=runtime)

    def _looks_like_dependency_file_clarification_followup(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> bool:
        text = str(user_msg or "").strip()
        if not _DEPENDENCY_FILE_CLARIFICATION_RE.match(text):
            return False
        return self._extract_active_dependency_runtime_match(recent_history) is not None

    def _build_file_readback_followup_route(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        if not self._looks_like_file_readback_followup(
            user_msg=user_msg,
            recent_history=recent_history,
        ):
            return None
        runtime = extract_latest_runtime_context_fields(recent_history)
        relevant_paths = self._extract_runtime_relevant_paths(runtime)
        if len(relevant_paths) != 1:
            return None
        return self._build_file_readback_card(relevant_paths[0])

    def _looks_like_file_readback_followup(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> bool:
        text = str(user_msg or "").strip()
        if not _FILE_READBACK_FOLLOWUP_RE.match(text):
            return False
        runtime = extract_latest_runtime_context_fields(recent_history)
        if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
            return False
        return len(self._extract_runtime_relevant_paths(runtime)) == 1

    @staticmethod
    def _extract_runtime_relevant_paths(runtime: dict[str, str]) -> list[str]:
        raw = str(runtime.get("relevant_paths") or "").strip()
        if not raw:
            return []
        paths = [part.strip().replace("\\", "/") for part in raw.split("|")]
        return [path for path in paths if path]

    @staticmethod
    def _build_file_readback_card(path: str) -> RouteDecision:
        clean_path = str(path or "").strip().replace("\\", "/")
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Read the exact contents of '{clean_path}'.",
                "context": [
                    "The workspace root is '.'.",
                    f"The active follow-up file path is '{clean_path}'.",
                    "This is a contextual exact-read follow-up after prior FILE_WORK. Read the same file directly instead of doing a fuzzy lookup.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Read the exact contents of the file '{clean_path}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"The exact contents of '{clean_path}' are read once.",
                        "allowed_tools": ["FILE_OP"],
                        "active_targets": [clean_path],
                    }
                ],
            },
        }

    @staticmethod
    def _build_file_dependency_override_move_card(
        *,
        path: str,
        kind: str,
        name: str,
        runtime: dict[str, str],
    ) -> RouteDecision:
        clean_path = str(path or "").strip().replace("\\", "/")
        clean_kind = kind or "item"
        clean_name = name or "unknown"
        previous_request = str(runtime.get("previous_user_request") or "").strip()
        task_goal = str(runtime.get("task_goal") or "").strip()
        success_condition = task_goal or f"The previously requested move for '{clean_path}' is reflected in the workspace."
        context = [
            "The workspace root is '.'.",
            f"The blocked source path is '{clean_path}'.",
            f"The user explicitly authorized overriding the active {clean_kind} dependency '{clean_name}'.",
            "Retry the FILE_WORK move directly. Do not create TASK_EVENT_WORK stages and do not mutate task/event state as part of this override.",
        ]
        if previous_request:
            context.append(f"Original file operation request: {previous_request}")
        return {
            "decision": "TASK",
            "card": {
                "goal": previous_request or f"Complete the previously requested move for '{clean_path}' with explicit dependency override authorization.",
                "context": context,
                "stages": [
                    {
                        "stage_goal": previous_request or f"Complete the previously requested move for '{clean_path}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": success_condition,
                        "allowed_tools": ["FILE_OP", "RUN_CODE"],
                        "active_targets": [clean_path],
                        "dependency_override_authorized": True,
                    }
                ],
            },
        }

    @staticmethod
    def _build_file_dependency_retry_move_card(
        *,
        path: str,
        runtime: dict[str, str],
    ) -> RouteDecision:
        clean_path = str(path or "").strip().replace("\\", "/")
        previous_request = str(runtime.get("previous_user_request") or "").strip()
        task_goal = str(runtime.get("task_goal") or "").strip()
        return {
            "decision": "TASK",
            "card": {
                "goal": previous_request or task_goal or f"Complete the previously requested move for '{clean_path}'.",
                "context": [
                    "The workspace root is '.'.",
                    f"The blocked source path is '{clean_path}'.",
                    "The user clarified they mean the file target. This is not permission to mutate task/event state or override the dependency block.",
                    "Retry the FILE_WORK move directly and keep task/event state unchanged unless the user explicitly asks to modify it too.",
                ],
                "stages": [
                    {
                        "stage_goal": previous_request or task_goal or f"Complete the previously requested move for '{clean_path}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": task_goal or f"The previously requested move for '{clean_path}' is reflected in the workspace.",
                        "allowed_tools": ["FILE_OP", "RUN_CODE"],
                        "active_targets": [clean_path],
                    }
                ],
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
    def _previous_assistant_was_offer(recent_history: Iterable[dict[str, Any]]) -> bool:
        """Return True when the most recent assistant turn looks like an offer or question.

        Used to detect the pattern: Piper asks "Should I do X?" and the user
        replies with a bare affirmative ("Yes please", "Go ahead", etc.).
        """
        for item in reversed(list(recent_history or [])):
            if item.get("role") != "assistant":
                continue
            content = str(item.get("content") or "").strip()
            if not content or _THINKING_RE.match(content):
                continue
            return bool(_OFFER_PHRASE_RE.search(content))
        return False

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
