from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from core.contracts import (
    KnowledgeMutationIntent,
    RouteDecision,
    StageCard,
    StageOutcomePack,
    StateMutationRequest,
    StateMutationIntent,
    StateReadonlyPack,
)
from core.runtime_context import extract_latest_runtime_context_fields, extract_previous_user_message
from core.task_event_context import (
    extract_latest_task_event_candidates,
    extract_recent_list_subjects,
    extract_runtime_followup_subject,
)
from core.routing.route_patterns import (
    CANCEL_HINT_RE,
    COMPLETION_HINT_RE,
    CORRECTION_ONLY_HINT_RE,
    DATE_HINT_RE,
    DIRECT_EVENT_ASSERTION_RE,
    EVENT_INSPECTION_HINT_RE,
    EVENT_WORD_RE,
    GENERIC_EVENT_STAGE_RE,
    KNOWLEDGE_QUERY_RE,
    KNOWLEDGE_REMOVE_RE,
    KNOWLEDGE_STORE_RE,
    MUTATION_REQUEST_RE,
    READONLY_TASK_EVENT_QUERY_RE,
    REMINDER_REQUEST_RE,
    SCHEDULE_HINT_RE,
    TASK_REQUEST_RE,
    VAGUE_EVENT_FOLLOWUP_RE,
    WORKLIKE_HINT_RE,
)
from core.routing.environment_queries import looks_like_live_environment_query
from core.routing.route_dates import extract_date_phrase, resolve_date_phrase
from core.routing.route_subjects import (
    extract_event_subject,
    extract_event_reference_subject,
    extract_reference_subject,
    has_existing_record_context,
    looks_like_event_followup,
    looks_like_task_creation,
    looks_like_task_followup,
    extract_task_phrase,
    extract_task_phrase_from_stage,
    strip_event_prefix,
    strip_followup_wrapper,
    subject_looks_like_event,
)


_TASK_EVENT_SUCCESS_PREFIXES = (
    ("Task added:", "TASK ADDED", "task", "add"),
    ("Task deleted:", "TASK DELETED", "task", "delete"),
    ("Task completed and archived:", "TASK COMPLETED", "task", "complete"),
    ("Event scheduled:", "EVENT SCHEDULED", "event", "schedule"),
    ("Event rescheduled:", "EVENT RESCHEDULED", "event", "reschedule"),
    ("Event removed:", "EVENT REMOVED", "event", "remove"),
    ("Event completed and archived:", "EVENT COMPLETED", "event", "complete"),
    ("Pending Tasks:", "TASKS LISTED", "task", "inspect"),
    ("No pending tasks.", "TASKS LISTED", "task", "inspect"),
    ("Upcoming Events:", "EVENTS LISTED", "event", "inspect"),
    ("No upcoming events.", "EVENTS LISTED", "event", "inspect"),
)

_TASK_EVENT_FAILURE_PREFIXES = (
    "Task not found:",
    "Event not found:",
    "Invalid format.",
    "Error ",
    "ERROR:",
)

_MEMORY_SUCCESS_PREFIXES = (
    ("Knowledge removed:", "KNOWLEDGE REMOVED", "world_model", "remove"),
    ("Knowledge already absent:", "KNOWLEDGE ALREADY ABSENT", "world_model", "remove"),
    ("System confirmation: Knowledge base updated successfully.", "KNOWLEDGE UPDATED", "world_model", "update"),
    ("[WORLD STATE]", "WORLD STATE LISTED", "world_model", "inspect"),
    ("No world model stored.", "WORLD STATE LISTED", "world_model", "inspect"),
    ("User Knowledge:", "KNOWLEDGE LISTED", "world_model", "inspect"),
    ("No knowledge stored.", "KNOWLEDGE LISTED", "world_model", "inspect"),
)

_MEMORY_FAILURE_PREFIXES = (
    "Key not found:",
    "Invalid format.",
    "Error reading knowledge:",
    "Error: Could not update world model memory.",
    "No knowledge file.",
)
_PROPOSAL_ONLY_PREFIX = "PROPOSAL:"

_OBSERVATION_RE = re.compile(r"^OBSERVATION_TEXT:\s*(.+)$", flags=re.MULTILINE | re.DOTALL)
_MUTATING_STAGE_GOAL_RE = re.compile(
    r"(?i)\b(add|create|schedule|set|mark|complete|archive|remove|delete|cancel|update)\b"
)
_SPECIFIC_MEMORY_VALUE_HINT_RE = re.compile(
    r"(?i)\b("
    r"exact|specific|precise|actual|verify|confirmed?|confirm|whether|"
    r"time|date|day|when|where|who|which|what\s+time|what\s+date|what\s+day|"
    r"did i say|did the user say|what was|what is"
    r")\b"
)
_MEMORY_LISTING_INTENT_RE = re.compile(
    r"(?i)\b(list|show|display|render|everything|all\b|full world state|world state)\b"
)
_MEMORY_REMOVE_TARGET_RE = re.compile(
    r"(?i)\bremove the durable user fact\s+['\"]([^'\"]+)['\"]\s+from memory\b"
)
_MEMORY_SUCCESS_CONDITION_TARGET_RE = re.compile(
    r"(?i)\bknowledge store no longer contains the fact\s+(.+?)\s*$"
)
_TRANSIENT_REMEMBER_ACTIVITY_RE = re.compile(
    r"(?is)^(?:please\s+)?remember(?:\s+that)?\s+"
    r"(?:i am|i'm|im|we are|we're|were)\s+"
    r"(?:(?:watching|playing|debugging|working on|testing|using)\b.+)$"
)
_TRANSIENT_REMEMBER_TRYING_RE = re.compile(
    r"(?is)^(?:please\s+)?remember(?:\s+that)?\s+"
    r"(?:i am|i'm|im)\s+trying\s+to\b.+$"
)
_TRANSIENT_REMEMBER_STATE_RE = re.compile(
    r"(?is)^(?:please\s+)?remember(?:\s+that)?\s+"
    r"(?:i am|i'm|im|feeling)\b[^.?!]*\b"
    r"(hungry|tired|sleepy|sad|stressed|anxious|sick|ill|bored|frustrated|annoyed|overwhelmed|busy)\b"
)
_TRANSIENT_ASSERTION_ACTIVITY_RE = re.compile(
    r"(?is)^(?:i am|i'm|im|we are|we're|were)\s+"
    r"(?:(?:watching|playing|debugging|working on|testing|using|reading)\b.+)$"
)
_TRANSIENT_ASSERTION_TRYING_RE = re.compile(
    r"(?is)^(?:i am|i'm|im)\s+trying\s+to\b.+$"
)
_TRANSIENT_ASSERTION_STATE_RE = re.compile(
    r"(?is)^(?:i am|i'm|im|feeling)\b[^.?!]*\b"
    r"(hungry|tired|sleepy|sad|stressed|anxious|sick|ill|bored|frustrated|annoyed|overwhelmed|busy)\b"
)
_TRANSIENT_ASSERTION_FOCUS_RE = re.compile(
    r"(?is)^my\s+(?:biggest|main|primary|current)\s+"
    r"(?:project|focus|priority)\s+(?:is(?:\s+currently)?\s+)?"
    r"(?P<activity>.+?)[.?!]*$"
)
_CONTEXTUAL_REMEMBER_RE = re.compile(
    r"(?is)^\s*(?:just\s+)?(?:remember|don't forget|dont forget)"
    r"(?:\s+(?:that(?:\s+fact)?|it|this|the fact))?\s*[.?!]*\s*$"
)
_AMBIGUOUS_MEMORY_FOLLOWUP_RE = re.compile(
    r"(?is)\b(?:remember|forget|remove|delete)\b[^.?!]*\b(?:it|that|this|fact|memory)\b"
    r"|^\s*(?:just\s+)?(?:remember|forget|remove|delete)(?:\s+(?:it|that|this|the fact|that fact))?\s*[.?!]*\s*$"
)
_TASK_EVENT_CONTAINER_REFERENCE_RE = re.compile(
    r"(?i)\b(task|tasks|task list|to-?do|to-?dos|to-?do list|event|events|calendar)\b"
)
_BARE_KNOWLEDGE_ASSERTION_RE = re.compile(
    r"(?is)^my\s+(?P<subject>.+?)\s+(?:is|are)\s+(?P<value>.+?)[.?!]*$"
)
_FIRST_PERSON_STATEMENT_RE = re.compile(
    r"(?is)^i\s+(?P<predicate>.{5,}?)[.?!]*$"
)
_GENERIC_REFERENCE_SUBJECTS = {"it", "this", "that", "thing", "the thing"}
_GENERIC_MEMORY_REMOVE_RE = re.compile(
    r"(?i)\b(?:remove|delete|forget|drop|clear)\s+(?:it|that|this)\b"
)
_TASK_EVENT_DELETE_FROM_LIST_RE = re.compile(
    r"(?is)^(?:please\s+)?(?:remove|delete|drop|take(?:\s+\w+)?\s+off)\s+"
    r"(?P<subject>.+?)\s+(?:from|off)\s+(?:the\s+|my\s+)?"
    r"(?P<container>tasks?|task list|to-?do(?:\s+list)?|events?|calendar)\b[.?!]*$"
)
_PLURAL_TASK_EVENT_FOLLOWUP_RE = re.compile(
    r"(?is)\b(?:them all|all of them|remove them all|delete them all|clear them all|remove all|delete all|complete them all|finish them all)\b"
)
_WORK_STATE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:working on|project|script|game|app|application|code)\b"
)
_RENDERED_WORLD_FACT_RE = re.compile(r"(?im)^\s*[-*]?\s*([^:\n]+):\s*(.+?)\s*$")
_COMPLETION_SUBJECT_FILLER_RE = re.compile(
    r"(?i)\b(?:cool|okay|ok|alright|right|fine|thanks|thank you|forgot|those|that|but|already)\b"
)
_COMPLETION_TOKEN_RE = re.compile(r"[a-z0-9]+")
_COMPLETION_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "about",
    "already",
    "am",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "cool",
    "did",
    "do",
    "done",
    "for",
    "forgot",
    "got",
    "have",
    "i",
    "im",
    "i'm",
    "it",
    "my",
    "of",
    "on",
    "okay",
    "ok",
    "thank",
    "thanks",
    "that",
    "the",
    "those",
    "to",
    "was",
    "we",
    "went",
    "with",
    "you",
}
_COMPLETION_IRREGULAR_TOKEN_MAP = {
    "bought": "buy",
    "buying": "buy",
    "completed": "complete",
    "completing": "complete",
    "did": "do",
    "done": "do",
    "finished": "finish",
    "finishing": "finish",
    "forgot": "forget",
    "handled": "handle",
    "handling": "handle",
    "scheduled": "schedule",
    "scheduling": "schedule",
    "washed": "wash",
    "washing": "wash",
    "went": "go",
}


@dataclass(frozen=True)
class StateMutationEngine:
    @staticmethod
    def build_mutation_request(
        *,
        state_owner: str,
        entity_kind: str,
        action: str,
        target: str = "",
        value: str = "",
        scheduled_date: str = "",
    ) -> StateMutationRequest:
        payload: StateMutationRequest = {
            "state_owner": str(state_owner or "").strip(),
            "entity_kind": str(entity_kind or "").strip(),
            "action": str(action or "").strip(),
        }
        if str(target or "").strip():
            payload["target"] = str(target).strip()
        if str(value or "").strip():
            payload["value"] = str(value).strip()
        if str(scheduled_date or "").strip():
            payload["scheduled_date"] = str(scheduled_date).strip()
        return payload

    @staticmethod
    def stage_mutation_request(stage: StageCard | None) -> StateMutationRequest:
        mutation = (stage or {}).get("mutation")
        if isinstance(mutation, dict):
            return dict(mutation)
        return {}

    @staticmethod
    def looks_like_transient_remember_request(text: str) -> bool:
        candidate = str(text or "").strip()
        if not candidate:
            return False
        return bool(
            _TRANSIENT_REMEMBER_ACTIVITY_RE.match(candidate)
            or _TRANSIENT_REMEMBER_TRYING_RE.match(candidate)
            or _TRANSIENT_REMEMBER_STATE_RE.match(candidate)
        )

    @staticmethod
    def looks_like_contextual_remember_followup(text: str) -> bool:
        return bool(_CONTEXTUAL_REMEMBER_RE.match(str(text or "").strip()))

    @staticmethod
    def looks_like_ambiguous_memory_followup(text: str) -> bool:
        return bool(_AMBIGUOUS_MEMORY_FOLLOWUP_RE.search(str(text or "").strip()))

    @staticmethod
    def looks_like_transient_assertion(text: str) -> bool:
        candidate = str(text or "").strip()
        if not candidate:
            return False
        return bool(
            _TRANSIENT_ASSERTION_ACTIVITY_RE.match(candidate)
            or _TRANSIENT_ASSERTION_TRYING_RE.match(candidate)
            or _TRANSIENT_ASSERTION_STATE_RE.match(candidate)
            or _TRANSIENT_ASSERTION_FOCUS_RE.match(candidate)
        )

    def classify_contextual_remember_intent(
        self,
        *,
        previous_user_msg: str,
    ) -> KnowledgeMutationIntent:
        text = str(previous_user_msg or "").strip()
        if not text:
            return KnowledgeMutationIntent()
        if self.looks_like_transient_assertion(text):
            return KnowledgeMutationIntent(
                decision="none",
                reason="contextual remember follow-up refers to transient user state",
            )

        match = _BARE_KNOWLEDGE_ASSERTION_RE.match(text)
        if match:
            subject = self._normalize_knowledge_subject(match.group("subject"))
            value = self._normalize_knowledge_value(match.group("value"))
            if not subject or not value:
                return KnowledgeMutationIntent()
            if self._looks_like_soft_subject(subject, value):
                return KnowledgeMutationIntent(
                    decision="none",
                    reason="contextual remember follow-up refers to soft or current state",
                )
            return KnowledgeMutationIntent(
                decision="store_knowledge",
                subject=subject,
                value=value,
                reason="contextual remember follow-up resolves prior user fact into durable knowledge",
            )

        # Fallback: first-person statement ("I slept the whole day", "I went to the gym", etc.)
        # These are personal facts/events that should be stored as durable knowledge notes.
        fp_match = _FIRST_PERSON_STATEMENT_RE.match(text)
        if fp_match:
            predicate = fp_match.group("predicate").strip()
            words = predicate.split()
            subject = " ".join(words[:5]).rstrip(".,;")
            if subject and not self._looks_like_soft_subject(subject, predicate):
                return KnowledgeMutationIntent(
                    decision="store_knowledge",
                    subject=subject,
                    value=predicate,
                    reason="first-person statement stored as durable knowledge note",
                )

        return KnowledgeMutationIntent()

    def memory_remove_target(
        self,
        *,
        stage: StageCard | None = None,
        stage_entries: Iterable[str] | None = None,
    ) -> str:
        mutation = self.stage_mutation_request(stage)
        if (
            str(mutation.get("state_owner") or "").strip() == "world_model"
            and str(mutation.get("action") or "").strip() == "remove"
        ):
            structured_target = self._normalize_memory_remove_target(str(mutation.get("target") or "").strip())
            if structured_target:
                return structured_target

        stage_goal = str((stage or {}).get("stage_goal", "")).strip()
        if stage_goal:
            match = _MEMORY_REMOVE_TARGET_RE.search(stage_goal)
            if match:
                return self._normalize_memory_remove_target(match.group(1))

        success_condition = str((stage or {}).get("success_condition", "")).strip()
        if success_condition:
            match = _MEMORY_SUCCESS_CONDITION_TARGET_RE.search(success_condition)
            if match:
                return self._normalize_memory_remove_target(match.group(1))

        entries = [str(entry or "") for entry in (stage_entries or []) if str(entry or "").strip()]
        header_blob = "\n".join(entries[:2])
        if header_blob:
            match = _MEMORY_REMOVE_TARGET_RE.search(header_blob)
            if match:
                return self._normalize_memory_remove_target(match.group(1))
            match = _MEMORY_SUCCESS_CONDITION_TARGET_RE.search(header_blob)
            if match:
                return self._normalize_memory_remove_target(match.group(1))
        return ""

    def memory_remove_listing_confirms_absent(
        self,
        *,
        stage: StageCard | None = None,
        list_result_text: str,
        stage_entries: Iterable[str] | None = None,
    ) -> str:
        if str((stage or {}).get("stage_type", "")).upper() != "MEMORY_WORK":
            return ""

        target = self.memory_remove_target(stage=stage, stage_entries=stage_entries)
        if not target:
            return ""

        stage_blob = " ".join(
            [
                str((stage or {}).get("stage_goal", "")),
                str((stage or {}).get("success_condition", "")),
                " ".join(str(item) for item in ((stage or {}).get("context") or [])),
            ]
        ).lower()
        if not re.search(r"\b(remove|delete|forget)\b", stage_blob):
            return ""

        listing = str(list_result_text or "").strip()
        if not listing or listing.lower().startswith("error"):
            return ""
        if listing in {"No knowledge stored.", "No world model stored."}:
            return target

        normalized_listing = self._normalize_memory_listing_text(listing)
        normalized_target = self._normalize_memory_listing_text(target)
        if not normalized_target or normalized_target in normalized_listing:
            return ""
        # Guard: if significant words from the target appear in the listing, the fact
        # may be stored under a different key format — don't auto-resolve as absent.
        significant_words = [w for w in normalized_target.split() if len(w) > 4]
        if significant_words and any(w in normalized_listing for w in significant_words):
            return ""
        return target

    def classify_knowledge_intent(
        self,
        *,
        user_msg: str,
    ) -> KnowledgeMutationIntent:
        text = str(user_msg or "").strip()
        if not text:
            return KnowledgeMutationIntent()
        if self.looks_like_transient_remember_request(text):
            return KnowledgeMutationIntent()

        query_match = KNOWLEDGE_QUERY_RE.match(text)
        if query_match:
            raw_subject = query_match.group("subject").strip()
            # "what is / what's / whats" is too broad — it matches temporal,
            # environmental, and conversational queries like "What's the date?" or
            # "What's up with you?".  For these forms, require a personal possessive
            # (my, your, '<name>'s) in the raw subject so that only genuine
            # personal-fact lookups ("What's my drink?", "What's Dora's job?")
            # reach the knowledge fast path.  Everything else falls through to the
            # persona LLM which can read [ENVIRONMENT] and answer naturally.
            broad_form = bool(re.match(r"(?i)^(?:so\s+)?(?:what is|what's|whats)\s+", text))
            if broad_form:
                has_possessive = bool(re.search(r"(?i)\bmy\b|\byour\b|\b\w+'s\b", raw_subject))
                if not has_possessive:
                    return KnowledgeMutationIntent()
            subject = self._normalize_knowledge_subject(raw_subject)
            return KnowledgeMutationIntent(
                decision="query_knowledge",
                subject=subject,
                reason="explicit durable-memory query",
            )

        store_match = KNOWLEDGE_STORE_RE.match(text)
        if store_match:
            subject = self._normalize_knowledge_subject(store_match.group("subject"))
            value = self._normalize_knowledge_value(store_match.group("value"))
            if subject and value:
                return KnowledgeMutationIntent(
                    decision="store_knowledge",
                    subject=subject,
                    value=value,
                    reason="explicit durable-memory store request",
                )

        remove_subject = self._extract_knowledge_remove_subject(text)
        if remove_subject:
            return KnowledgeMutationIntent(
                decision="remove_knowledge",
                subject=remove_subject,
                reason="explicit durable-memory remove request",
            )

        work_state_remove_subject = self._extract_work_state_remove_subject(text)
        if work_state_remove_subject:
            return KnowledgeMutationIntent(
                decision="remove_knowledge",
                subject=f"works on: {work_state_remove_subject}",
                reason="explicit project/work-state removal request",
            )

        return KnowledgeMutationIntent()

    def build_knowledge_route_decision(
        self,
        intent: KnowledgeMutationIntent,
    ) -> RouteDecision | None:
        if intent.decision == "none":
            return None
        if intent.decision == "query_knowledge":
            return {"decision": "CHAT"}
        if intent.decision == "store_knowledge":
            mutation = self.build_mutation_request(
                state_owner="world_model",
                entity_kind="knowledge",
                action="store",
                target=intent.subject,
                value=intent.value,
            )
            return {
                "decision": "TASK",
                "card": {
                    "goal": f"Store the user fact {intent.subject} = {intent.value}",
                    "context": [
                        "The user explicitly asked to store a durable fact in memory.",
                        "Use durable knowledge memory, not tasks or events.",
                    ],
                    "stages": [
                        {
                            "stage_goal": f"Store the durable user fact '{intent.subject}' as '{intent.value}'",
                            "stage_type": "MEMORY_WORK",
                            "success_condition": f"Knowledge store contains the fact {intent.subject} = {intent.value}",
                            "allowed_tools": ["UPDATE_KNOWLEDGE"],
                            "mutation": mutation,
                        }
                    ],
                },
            }
        if intent.decision == "remove_knowledge":
            mutation = self.build_mutation_request(
                state_owner="world_model",
                entity_kind="knowledge",
                action="remove",
                target=intent.subject,
            )
            return {
                "decision": "TASK",
                "card": {
                    "goal": f"Remove the user fact '{intent.subject}' from memory",
                    "context": [
                        "The user explicitly asked to remove a durable fact from memory.",
                        "Use durable knowledge memory, not tasks or events.",
                        "If direct removal says the key was not found, inspect memory once with LIST_KNOWLEDGE to find the exact rendered fact or confirm it is already absent.",
                    ],
                    "stages": [
                        {
                            "stage_goal": f"Remove the durable user fact '{intent.subject}' from memory",
                            "stage_type": "MEMORY_WORK",
                            "success_condition": f"Knowledge store no longer contains the fact {intent.subject}",
                            "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
                            "mutation": mutation,
                        }
                    ],
                },
            }
        return None

    def build_memory_store_route(self, *, subject: str, value: str) -> RouteDecision | None:
        return self.build_knowledge_route_decision(
            KnowledgeMutationIntent(
                decision="store_knowledge",
                subject=str(subject or "").strip(),
                value=str(value or "").strip(),
                reason="explicit durable-memory store request",
            )
        )

    def build_memory_remove_route(self, *, subject: str) -> RouteDecision | None:
        return self.build_knowledge_route_decision(
            KnowledgeMutationIntent(
                decision="remove_knowledge",
                subject=str(subject or "").strip(),
                reason="explicit durable-memory remove request",
            )
        )

    def build_task_event_delete_route(self, *, subject: str, is_event: bool) -> RouteDecision:
        return self._build_task_event_delete_card(str(subject or "").strip(), is_event=is_event)

    def build_task_event_completion_route(self, *, subject: str, is_event: bool) -> RouteDecision:
        return self._build_task_event_completion_card(str(subject or "").strip(), is_event=is_event)

    def normalize_route_decision(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]] | None = None,
    ) -> RouteDecision | None:
        history = [dict(item) for item in (recent_history or []) if isinstance(item, dict)]
        text = str(user_msg or "").strip()
        if not text:
            return None

        plural_followup = self._normalize_plural_task_event_followup(
            decision=decision,
            user_msg=text,
            recent_history=history,
        )
        if plural_followup is not None:
            return plural_followup

        delete_followup = self._normalize_task_event_delete_followup(
            decision=decision,
            user_msg=text,
            recent_history=history,
        )
        if delete_followup is not None:
            return delete_followup

        knowledge_route = self._normalize_knowledge_route(
            decision=decision,
            user_msg=text,
            recent_history=history,
        )
        if knowledge_route is not None:
            return knowledge_route

        contextual_remember = self._normalize_contextual_remember_followup(
            decision=decision,
            user_msg=text,
            recent_history=history,
        )
        if contextual_remember is not None:
            return contextual_remember

        retry_replay = self._normalize_retry_from_latest_runtime_context(
            decision=decision,
            user_msg=text,
            recent_history=history,
        )
        if retry_replay is not None:
            return retry_replay

        chat_followup = self._normalize_chat_task_event_followup(
            decision=decision,
            user_msg=text,
            recent_history=history,
        )
        if chat_followup is not None:
            return chat_followup

        if str((decision or {}).get("decision") or "").strip().upper() != "TASK":
            return None

        card = dict((decision or {}).get("card") or {})
        stages = [dict(stage) for stage in (card.get("stages") or []) if isinstance(stage, dict)]
        if not stages:
            return None

        casual_completion = self._normalize_casual_completion_to_chat(
            card=card,
            stages=stages,
            user_msg=text,
        )
        if casual_completion is not None:
            return casual_completion

        status_query = self._normalize_task_event_status_query_to_chat(
            user_msg=text,
        )
        if status_query is not None:
            return status_query

        reminder_route = self._normalize_reminder_request(
            decision=decision,
            card=card,
            stages=stages,
            user_msg=text,
        )
        if reminder_route is not None:
            return reminder_route

        completion_route = self._normalize_task_event_completion_route(
            card=card,
            stages=stages,
            user_msg=text,
            recent_history=history,
        )
        if completion_route is not None:
            return completion_route

        if any(str(stage.get("stage_type") or "").upper() != "TASK_EVENT_WORK" for stage in stages):
            return None

        correction_route = self._normalize_schedule_correction_to_chat(
            card=card,
            stages=stages,
            user_msg=text,
        )
        if correction_route is not None:
            return correction_route

        inspection_route = self._normalize_event_followup_inspection(
            decision=decision,
            card=card,
            stages=stages,
            user_msg=text,
        )
        if inspection_route is not None:
            return inspection_route

        if self._request_should_be_event(text, stages):
            event_name, date_phrase = self._extract_event_parts(text, stages)
            if event_name and date_phrase:
                resolved_date = resolve_date_phrase(date_phrase) or date_phrase
                event_stage = {
                    "stage_goal": f"Schedule the event '{event_name}' for {resolved_date}",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Event is created once with the requested date",
                    "allowed_tools": ["ADD_EVENT"],
                    "mutation": self.build_mutation_request(
                        state_owner="task_event",
                        entity_kind="event",
                        action="schedule",
                        target=event_name,
                        scheduled_date=resolved_date,
                    ),
                }
                normalized = dict(decision)
                new_card = dict(card)
                new_card["goal"] = f"Add an event for {event_name} on {resolved_date}"
                new_card["stages"] = [event_stage]
                normalized["card"] = new_card
                return normalized

        if len(stages) < 2 or not self._needs_task_stage_collapse(stages):
            return None

        task_phrase = extract_task_phrase(text) or extract_task_phrase_from_stage(stages[0])
        if not task_phrase:
            return None

        merged_stage = dict(stages[0])
        merged_stage["stage_goal"] = f"Create a task to {task_phrase}"
        merged_stage["success_condition"] = "Task is created once with the requested details"
        normalized = dict(decision)
        new_card = dict(card)
        new_card["stages"] = [merged_stage]
        normalized["card"] = new_card
        return normalized

    def _normalize_knowledge_route(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not text:
            return None

        if REMINDER_REQUEST_RE.search(text):
            return None
        if _TASK_EVENT_DELETE_FROM_LIST_RE.match(text):
            return None

        intent = self.classify_knowledge_intent(user_msg=text)
        if intent.decision == "none":
            if self.looks_like_transient_remember_request(text):
                return {"decision": "CHAT"}
            contextual_subject = self._extract_contextual_memory_remove_subject(
                user_msg=text,
                recent_history=recent_history,
            )
            if contextual_subject:
                intent = self.classify_knowledge_intent(
                    user_msg=f"Forget that {contextual_subject}.",
                )
        return self.build_knowledge_route_decision(intent)

    def _normalize_contextual_remember_followup(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not self.looks_like_contextual_remember_followup(text):
            return None

        previous_user_msg = extract_previous_user_message(recent_history, current_text=text)
        if not previous_user_msg:
            return {"decision": "CHAT"}

        intent = self.classify_contextual_remember_intent(
            previous_user_msg=previous_user_msg,
        )
        route = self.build_knowledge_route_decision(intent)
        if route is not None:
            return route
        return {"decision": "CHAT"}

    def _normalize_retry_from_latest_runtime_context(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not text or not re.match(r"(?is)^\s*(?:please\s+)?(?:try again|retry|again|do it again|run it again|attempt again)\s*[.?!]*\s*$", text):
            return None

        runtime = extract_latest_runtime_context_fields(recent_history)
        if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
            return None

        previous_request = str(runtime.get("previous_user_request") or "").strip()
        if not previous_request or re.match(r"(?is)^\s*(?:please\s+)?(?:try again|retry|again|do it again|run it again|attempt again)\s*[.?!]*\s*$", previous_request):
            return None

        # Only handle state-domain retries here; other domains stay with the
        # broader route normalizer.
        state_retry = self._normalize_knowledge_route(
            decision=decision,
            user_msg=previous_request,
            recent_history=recent_history,
        )
        if state_retry is not None:
            return state_retry

        if str((decision or {}).get("decision") or "").strip().upper() != "TASK":
            return None
        base_card = dict((decision or {}).get("card") or {})
        base_stages = [dict(stage) for stage in (base_card.get("stages") or []) if isinstance(stage, dict)]
        reminder_retry = self._normalize_reminder_request(
            decision=decision,
            card=base_card,
            stages=base_stages,
            user_msg=previous_request,
        )
        if reminder_retry is not None:
            return reminder_retry
        return None

    def _normalize_chat_task_event_followup(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        if decision and str(decision.get("decision") or "").strip().upper() != "CHAT":
            return None

        text = str(user_msg or "").strip()
        if not text or not COMPLETION_HINT_RE.search(text) or CANCEL_HINT_RE.search(text):
            return None

        runtime = extract_latest_runtime_context_fields(recent_history)
        if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
            return None

        runtime_note = str(runtime.get("runtime_note") or "").strip()
        if runtime_note.startswith("Task added:"):
            subject = runtime_note.partition("Task added:")[2].strip()
            if subject:
                return self._build_task_event_completion_card(subject, is_event=False)
        if runtime_note.startswith("Event scheduled:"):
            subject = runtime_note.partition("Event scheduled:")[2].strip()
            subject = re.sub(r"\s+on\s+\d{4}-\d{2}-\d{2}$", "", subject).strip()
            if subject:
                return self._build_task_event_completion_card(subject, is_event=True)

        execution_status = str(runtime.get("execution_status") or "").strip().upper()
        task_goal = str(runtime.get("task_goal") or "").strip()
        if execution_status == "TASK ADDED" and task_goal:
            subject = extract_task_phrase(task_goal) or extract_reference_subject(
                task_goal,
                {"goal": task_goal, "context": []},
                [],
            )
            if subject:
                return self._build_task_event_completion_card(subject, is_event=False)
        if execution_status == "EVENT SCHEDULED" and task_goal:
            subject = extract_event_subject(task_goal)
            if subject:
                return self._build_task_event_completion_card(subject, is_event=True)

        return None

    def _normalize_task_event_delete_followup(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        match = _TASK_EVENT_DELETE_FROM_LIST_RE.match(text)
        if not match:
            return None

        container = str(match.group("container") or "").strip().lower()
        is_event = "event" in container or "calendar" in container
        subject = str(match.group("subject") or "").strip().strip(".")
        if self._subject_is_generic_reference(subject):
            listed_subjects = extract_latest_task_event_candidates(recent_history, is_event=is_event)
            if len(listed_subjects) == 1:
                subject = listed_subjects[0]
            else:
                return self._build_task_event_delete_clarification_card(
                    is_event=is_event,
                    listed_subjects=listed_subjects,
                )
        if not subject or self._subject_is_generic_reference(subject):
            return self._build_task_event_delete_clarification_card(
                is_event=is_event,
                listed_subjects=extract_recent_list_subjects(recent_history, is_event=is_event),
            )
        return self._build_task_event_delete_card(subject, is_event=is_event)

    def _normalize_plural_task_event_followup(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not text or not _PLURAL_TASK_EVENT_FOLLOWUP_RE.search(text):
            return None

        runtime = extract_latest_runtime_context_fields(recent_history)
        previous_route = str(runtime.get("previous_route") or "").strip().upper()
        explicit_task = looks_like_task_followup(text)
        explicit_event = looks_like_event_followup(text)
        task_candidates = extract_latest_task_event_candidates(recent_history, is_event=False)
        event_candidates = extract_latest_task_event_candidates(recent_history, is_event=True)
        if not explicit_task and not explicit_event and previous_route != "TASK":
            if task_candidates and not event_candidates:
                explicit_task = True
            elif event_candidates and not task_candidates:
                explicit_event = True
            else:
                return None

        is_event = explicit_event and not explicit_task
        if not explicit_task and not explicit_event:
            runtime_note = str(runtime.get("runtime_note") or runtime.get("last_log") or "").strip()
            runtime_status = str(runtime.get("execution_status") or "").strip().upper()
            is_event = runtime_note.startswith("Event ") or runtime_status.startswith("EVENT")

        targets = event_candidates if is_event else task_candidates
        if not targets:
            fallback_subject = extract_runtime_followup_subject(recent_history, is_event=is_event)
            if fallback_subject and not self._subject_is_generic_reference(fallback_subject):
                targets = [fallback_subject]
        normalized_targets = [target for target in targets if target and not self._subject_is_generic_reference(target)]
        if not normalized_targets:
            return None

        completion_mode = bool(COMPLETION_HINT_RE.search(text))
        if len(normalized_targets) == 1:
            subject = normalized_targets[0]
            return (
                self._build_task_event_completion_card(subject, is_event=is_event)
                if completion_mode
                else self._build_task_event_delete_card(subject, is_event=is_event)
            )
        return self._build_plural_task_event_followup_card(
            normalized_targets,
            is_event=is_event,
            completion_mode=completion_mode,
        )

    def _normalize_task_event_completion_route(
        self,
        *,
        card: Dict[str, Any],
        stages: List[StageCard],
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> RouteDecision | None:
        intent = self.classify_task_event_followup(
            card=card,
            stages=stages,
            user_msg=user_msg,
        )
        if intent.decision == "none":
            return None
        if intent.decision == "chat_correction":
            return {"decision": "CHAT"}

        subject = intent.subject
        is_event = intent.decision == "complete_event"
        bound_target = self._bind_completion_target_from_recent_context(
            user_msg=user_msg,
            recent_history=recent_history,
            current_subject=subject,
            current_is_event=is_event,
        )
        if bound_target is not None:
            subject, is_event = bound_target
        if self._subject_is_generic_reference(subject):
            runtime_subject = extract_runtime_followup_subject(recent_history, is_event=is_event)
            if runtime_subject:
                subject = runtime_subject
        if not subject or self._subject_is_generic_reference(subject):
            return None
        return self._build_task_event_completion_card(subject, is_event=is_event)

    def _normalize_event_followup_inspection(
        self,
        *,
        decision: RouteDecision,
        card: Dict[str, Any],
        stages: List[StageCard],
        user_msg: str,
    ) -> RouteDecision | None:
        if MUTATION_REQUEST_RE.search(user_msg or ""):
            return None

        stage_blob = " ".join(
            f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
            for stage in stages
        )
        context_blob = " ".join(str(item) for item in card.get("context") or [])
        combined = " ".join(filter(None, [user_msg, card.get("goal", ""), stage_blob, context_blob]))
        if not combined or not (EVENT_INSPECTION_HINT_RE.search(combined) and (EVENT_WORD_RE.search(combined) or "calendar" in combined.lower())):
            return None
        user_hint = bool(EVENT_INSPECTION_HINT_RE.search(user_msg or ""))
        stage_hint = (
            bool(GENERIC_EVENT_STAGE_RE.search(stage_blob))
            or "calendar" in stage_blob.lower()
            or "calendar" in context_blob.lower()
            or ("related to" in stage_blob.lower() and "event" in stage_blob.lower())
            or "completed" in str(stages[0].get("success_condition", "")).lower()
        )
        direct_assertion = bool(DIRECT_EVENT_ASSERTION_RE.search(user_msg or ""))
        vague_followup = bool(VAGUE_EVENT_FOLLOWUP_RE.search(user_msg or ""))
        if direct_assertion and not user_hint:
            return None
        if not (user_hint or (vague_followup and stage_hint)):
            return None

        subject = extract_event_reference_subject(user_msg, card, stages)
        if not subject:
            return None

        stage = {
            "stage_goal": f"Check upcoming events for '{subject}'",
            "stage_type": "TASK_EVENT_WORK",
            "success_condition": "Matching event is identified and its scheduled date is reported, or its absence is confirmed",
            "allowed_tools": ["LIST_EVENTS"],
        }
        normalized = dict(decision)
        new_card = dict(card)
        new_card["goal"] = f"Check whether the event '{subject}' is scheduled"
        new_card["stages"] = [stage]
        normalized["card"] = new_card
        return normalized

    def _normalize_casual_completion_to_chat(
        self,
        *,
        card: Dict[str, Any],
        stages: List[StageCard],
        user_msg: str,
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not text or not COMPLETION_HINT_RE.search(text):
            return None
        if MUTATION_REQUEST_RE.search(text) or CANCEL_HINT_RE.search(text):
            return None

        stage_blob = " ".join(
            f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
            for stage in stages
        )
        context_blob = " ".join(str(item) for item in card.get("context") or [])
        combined = " ".join(filter(None, [str(card.get("goal", "")), stage_blob, context_blob]))

        if has_existing_record_context(combined):
            return None

        return {"decision": "CHAT"}

    def _normalize_task_event_status_query_to_chat(
        self,
        *,
        user_msg: str,
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not text or not READONLY_TASK_EVENT_QUERY_RE.search(text):
            return None
        if MUTATION_REQUEST_RE.search(text):
            return None
        return {"decision": "CHAT"}

    def _normalize_schedule_correction_to_chat(
        self,
        *,
        card: Dict[str, Any],
        stages: List[StageCard],
        user_msg: str,
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not text:
            return None
        if MUTATION_REQUEST_RE.search(text):
            return None
        if not CORRECTION_ONLY_HINT_RE.search(text):
            return None
        if not DATE_HINT_RE.search(text):
            return None

        stage_blob = " ".join(
            f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
            for stage in stages
        )
        context_blob = " ".join(str(item) for item in card.get("context") or [])
        combined = " ".join(filter(None, [text, str(card.get("goal", "")), stage_blob, context_blob]))
        if not WORKLIKE_HINT_RE.search(combined):
            return None
        return {"decision": "CHAT"}

    def _normalize_reminder_request(
        self,
        *,
        decision: RouteDecision,
        card: Dict[str, Any],
        stages: List[StageCard],
        user_msg: str,
    ) -> RouteDecision | None:
        text = str(user_msg or "").strip()
        if not text or not REMINDER_REQUEST_RE.search(text):
            return None
        if CANCEL_HINT_RE.search(text):
            return None

        subject = self._extract_reminder_request_subject(text)
        if not subject:
            return None

        date_phrase = extract_date_phrase(text)
        if date_phrase:
            resolved_date = resolve_date_phrase(date_phrase) or date_phrase
            stage = {
                "stage_goal": f"Schedule the event '{subject}' for {resolved_date}",
                "stage_type": "TASK_EVENT_WORK",
                "success_condition": "Event is created once with the requested date",
                "allowed_tools": ["ADD_EVENT"],
                "mutation": self.build_mutation_request(
                    state_owner="task_event",
                    entity_kind="event",
                    action="schedule",
                    target=subject,
                    scheduled_date=resolved_date,
                ),
            }
            normalized = dict(decision)
            new_card = dict(card)
            new_card["goal"] = f"Add an event for {subject} on {resolved_date}"
            new_card["stages"] = [stage]
            normalized["card"] = new_card
            return normalized

        stage = {
            "stage_goal": f"Create a task to {subject}",
            "stage_type": "TASK_EVENT_WORK",
            "success_condition": "Task is created once with the requested details",
            "allowed_tools": ["ADD_TASK"],
            "mutation": self.build_mutation_request(
                state_owner="task_event",
                entity_kind="task",
                action="add",
                target=subject,
            ),
        }
        normalized = dict(decision)
        new_card = dict(card)
        new_card["goal"] = f"Add the task '{subject}'"
        new_card["stages"] = [stage]
        normalized["card"] = new_card
        return normalized

    def classify_task_event_followup(
        self,
        *,
        card: Dict[str, Any],
        stages: List[StageCard],
        user_msg: str,
    ) -> StateMutationIntent:
        text = str(user_msg or "").strip()
        if not text:
            return StateMutationIntent()

        stage_blob = " ".join(
            f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
            for stage in stages
        )
        context_blob = " ".join(str(item) for item in (card.get("context") or []))
        combined = " ".join(filter(None, [text, str(card.get("goal", "")), stage_blob, context_blob]))

        if COMPLETION_HINT_RE.search(text) and not CANCEL_HINT_RE.search(text):
            subject = extract_reference_subject(text, card, stages)
            if not subject:
                return StateMutationIntent()
            task_like = looks_like_task_followup(
                " ".join(filter(None, [str(card.get("goal", "")), stage_blob, context_blob]))
            )
            subject_event_like = subject_looks_like_event(subject)
            event_like = looks_like_event_followup(combined)
            direct_assertion = bool(DIRECT_EVENT_ASSERTION_RE.search(text))
            if direct_assertion and (subject_event_like or event_like or "calendar" in combined.lower()):
                return StateMutationIntent(
                    decision="chat_correction",
                    subject=subject,
                    reason="direct appointment/event assertion should correct state, not complete it",
                )
            if not has_existing_record_context(combined):
                return StateMutationIntent(
                    decision="chat_correction",
                    subject=subject,
                    reason="completion hint without existing record context should stay conversational",
                )
            if task_like and not subject_event_like:
                return StateMutationIntent(decision="complete_task", subject=subject, reason="existing task follow-up")
            if event_like and not task_like:
                return StateMutationIntent(decision="complete_event", subject=subject, reason="existing event follow-up")
            return StateMutationIntent(
                decision="complete_event" if subject_event_like else "complete_task",
                subject=subject,
                reason="subject shape resolved completion target",
            )

        if not MUTATION_REQUEST_RE.search(text) and (EVENT_INSPECTION_HINT_RE.search(text) or VAGUE_EVENT_FOLLOWUP_RE.search(text)):
            subject = extract_event_reference_subject(text, card, stages)
            if subject:
                return StateMutationIntent(
                    decision="inspect_event",
                    subject=subject,
                    reason="follow-up asks to inspect event/calendar state",
                )

        return StateMutationIntent()

    def build_outcome_pack(
        self,
        *,
        success: bool,
        stage_type: str,
        fallback_observation: str = "",
        status_override: str = "",
        stage_entries: Iterable[str] | None = None,
        stage: StageCard | None = None,
    ) -> StageOutcomePack:
        stage_type_upper = str(stage_type or "").upper()
        entries = [str(entry or "") for entry in (stage_entries or []) if str(entry or "").strip()]

        if stage_type_upper == "TASK_EVENT_WORK":
            return self._build_task_event_outcome(
                success=success,
                fallback_observation=fallback_observation,
                status_override=status_override,
                entries=entries,
            )
        if stage_type_upper == "MEMORY_WORK":
            return self._build_memory_outcome(
                success=success,
                fallback_observation=fallback_observation,
                status_override=status_override,
                entries=entries,
                stage=stage,
            )

        status = status_override or ("SUCCESS" if success else "FAILED / INCOMPLETE")
        if success and stage_type_upper == "IMAGE_WORK":
            status = "IMAGE GENERATED"
        elif success and stage_type_upper == "FILE_WORK":
            status = "FILE OPERATION SUCCESS"
        detail = self._clean_detail(fallback_observation)
        return StageOutcomePack(
            status=status,
            detail=detail,
            effective_success=bool(success or status_override),
        )

    def stage_entries_indicate_terminal_failure(
        self,
        *,
        stage_type: str,
        fallback_observation: str = "",
        stage_entries: Iterable[str] | None = None,
        stage: StageCard | None = None,
    ) -> bool:
        pack = self.build_outcome_pack(
            success=True,
            stage_type=stage_type,
            fallback_observation=fallback_observation,
            stage_entries=stage_entries,
            stage=stage,
        )
        return not pack.effective_success

    def _build_task_event_outcome(
        self,
        *,
        success: bool,
        fallback_observation: str,
        status_override: str,
        entries: List[str],
    ) -> StageOutcomePack:
        detail = self._extract_latest_state_detail(
            entries,
            prefixes=[prefix for prefix, _, _, _ in _TASK_EVENT_SUCCESS_PREFIXES],
            failure_prefixes=_TASK_EVENT_FAILURE_PREFIXES,
            fallback=fallback_observation,
        )
        if status_override:
            return StageOutcomePack(
                status=status_override,
                detail=detail,
                effective_success=True,
                state_owner="task_event",
            )

        if detail in {"No pending tasks.", "No upcoming events."} and self._entries_indicate_mutating_state_goal(entries):
            return StageOutcomePack(
                status="FAILED / INCOMPLETE",
                detail=detail,
                effective_success=False,
                state_owner="task_event",
                auto_reroute=True,
                reroute_reason="state-owner mismatch candidate: mutating task/event stage only produced readonly empty-list evidence",
            )

        for prefix, status, owner, mutation_kind in _TASK_EVENT_SUCCESS_PREFIXES:
            if detail.startswith(prefix):
                return StageOutcomePack(
                    status=status if success else "FAILED / INCOMPLETE",
                    detail=detail,
                    effective_success=bool(success),
                    state_owner=owner,
                    mutation_kind=mutation_kind,
                )

        if any(detail.startswith(prefix) for prefix in _TASK_EVENT_FAILURE_PREFIXES):
            return StageOutcomePack(
                status="FAILED / INCOMPLETE",
                detail=detail,
                effective_success=False,
                state_owner="task_event",
            )

        if detail.startswith(_PROPOSAL_ONLY_PREFIX):
            return StageOutcomePack(
                status="FAILED / INCOMPLETE",
                detail=detail,
                effective_success=False,
                state_owner="task_event",
            )

        return StageOutcomePack(
            status="SUCCESS" if success else "FAILED / INCOMPLETE",
            detail=detail,
            effective_success=bool(success),
            state_owner="task_event",
        )

    def _build_memory_outcome(
        self,
        *,
        success: bool,
        fallback_observation: str,
        status_override: str,
        entries: List[str],
        stage: StageCard | None = None,
    ) -> StageOutcomePack:
        detail = self._extract_latest_state_detail(
            entries,
            prefixes=[prefix for prefix, _, _, _ in _MEMORY_SUCCESS_PREFIXES],
            failure_prefixes=_MEMORY_FAILURE_PREFIXES,
            fallback=fallback_observation,
        )
        if status_override:
            return StageOutcomePack(
                status=status_override,
                detail=detail,
                effective_success=True,
                state_owner="memory",
            )

        if self._memory_stage_requires_specific_value(stage=stage, entries=entries) and self._memory_detail_is_generic_listing(detail):
            return StageOutcomePack(
                status="FAILED / INCOMPLETE",
                detail="Specific requested value was not retrieved from durable memory. LIST_KNOWLEDGE returned only general world-state information.",
                effective_success=False,
                state_owner="world_model",
            )

        for prefix, status, owner, mutation_kind in _MEMORY_SUCCESS_PREFIXES:
            if detail.startswith(prefix):
                return StageOutcomePack(
                    status=status if success else "FAILED / INCOMPLETE",
                    detail=detail,
                    effective_success=bool(success),
                    state_owner=owner,
                    mutation_kind=mutation_kind,
                )

        if any(detail.startswith(prefix) for prefix in _MEMORY_FAILURE_PREFIXES):
            return StageOutcomePack(
                status="FAILED / INCOMPLETE",
                detail=detail,
                effective_success=False,
                state_owner="memory",
            )

        if detail.startswith(_PROPOSAL_ONLY_PREFIX):
            return StageOutcomePack(
                status="FAILED / INCOMPLETE",
                detail=detail,
                effective_success=False,
                state_owner="memory",
            )

        return StageOutcomePack(
            status=("KNOWLEDGE UPDATED" if success else "FAILED / INCOMPLETE"),
            detail=detail,
            effective_success=bool(success),
            state_owner="world_model",
        )

    def _memory_stage_requires_specific_value(
        self,
        *,
        stage: StageCard | None,
        entries: List[str],
    ) -> bool:
        if not stage or str((stage or {}).get("stage_type", "")).upper() != "MEMORY_WORK":
            return False
        if self._entries_indicate_mutating_state_goal(entries):
            return False
        blobs = [
            str((stage or {}).get("stage_goal") or ""),
            str((stage or {}).get("success_condition") or ""),
            str((stage or {}).get("objective") or ""),
        ]
        blobs.extend(str(item or "") for item in ((stage or {}).get("context") or []))
        combined = " ".join(part for part in blobs if part).strip().lower()
        if not combined:
            return False
        if _MEMORY_LISTING_INTENT_RE.search(combined) and not _SPECIFIC_MEMORY_VALUE_HINT_RE.search(combined):
            return False
        return bool(_SPECIFIC_MEMORY_VALUE_HINT_RE.search(combined))

    @staticmethod
    def _memory_detail_is_generic_listing(detail: str) -> bool:
        clean = str(detail or "").strip()
        if not clean:
            return False
        return any(
            clean.startswith(prefix)
            for prefix in (
                "[WORLD STATE]",
                "No world model stored.",
                "User Knowledge:",
                "No knowledge stored.",
            )
        )

    def _extract_latest_state_detail(
        self,
        entries: List[str],
        *,
        prefixes: List[str],
        failure_prefixes: Iterable[str],
        fallback: str,
    ) -> str:
        candidates: List[str] = []
        for entry in reversed(entries):
            match = _OBSERVATION_RE.search(entry)
            if not match:
                continue
            cleaned = self._clean_detail(match.group(1))
            if not cleaned:
                continue
            candidates.append(cleaned)
            if any(cleaned.startswith(prefix) for prefix in prefixes):
                return cleaned
            if any(cleaned.startswith(prefix) for prefix in failure_prefixes):
                return cleaned

        if candidates:
            return candidates[0]

        return self._clean_detail(fallback)

    @staticmethod
    def _entries_indicate_mutating_state_goal(entries: List[str]) -> bool:
        if not entries:
            return False
        header_blob = "\n".join(str(entry or "") for entry in entries[:2])
        match = re.search(r"(?im)^STAGE_GOAL:\s*(.+)$", header_blob)
        if not match:
            return False
        stage_goal = str(match.group(1) or "").strip()
        if not stage_goal:
            return False
        return bool(_MUTATING_STAGE_GOAL_RE.search(stage_goal))

    def build_readonly_answer(
        self,
        *,
        query: str,
        knowledge_mgr: Any,
        operational_state_service: Any,
    ) -> StateReadonlyPack:
        text = str(query or "").strip()
        if not text:
            return StateReadonlyPack()
        if looks_like_live_environment_query(text):
            return StateReadonlyPack()

        if self._is_profile_summary_query(text):
            summary = self._build_profile_summary_answer(
                knowledge_mgr=knowledge_mgr,
                operational_state_service=operational_state_service,
            )
            if summary:
                return StateReadonlyPack(
                    answer=summary,
                    state_owner="world_model",
                    query_kind="knowledge",
                )

        # Task/event readonly questions like "What's on my to-do list?" should
        # not fall through the broader "what is/what's <subject>" knowledge path.
        if operational_state_service is not None and READONLY_TASK_EVENT_QUERY_RE.search(text):
            answer = str(operational_state_service.build_readonly_answer(text) or "").strip()
            if answer:
                return StateReadonlyPack(
                    answer=answer,
                    state_owner="task_event",
                    query_kind="operational",
                )

        knowledge_intent = self.classify_knowledge_intent(user_msg=text)
        if knowledge_intent.decision == "query_knowledge" and knowledge_intent.subject:
            knowledge = {}
            if knowledge_mgr is not None:
                try:
                    knowledge = knowledge_mgr.load() or {}
                except Exception:
                    knowledge = {}
            value = self._find_knowledge_value(knowledge, knowledge_intent.subject)
            if value:
                return StateReadonlyPack(
                    answer=f"Your {knowledge_intent.subject} is {value}.",
                    state_owner="world_model",
                    query_kind="knowledge",
                )
            return StateReadonlyPack(
                answer=f"I do not have a stored {knowledge_intent.subject}.",
                state_owner="world_model",
                query_kind="knowledge",
            )

        if operational_state_service is None:
            return StateReadonlyPack()

        answer = str(operational_state_service.build_readonly_answer(text) or "").strip()
        if not answer:
            return StateReadonlyPack()
        return StateReadonlyPack(
            answer=answer,
            state_owner="task_event",
            query_kind="operational",
        )

    @staticmethod
    def _is_profile_summary_query(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        return any(
            phrase in lowered
            for phrase in (
                "tell me everything you know about me",
                "what do you know about me",
                "summarize what you know about me",
                "give me a summary of what you know about me",
            )
        )

    @staticmethod
    def _strip_world_state_heading(text: str) -> str:
        lines = [line.rstrip() for line in str(text or "").splitlines()]
        if lines and lines[0].strip() == "[WORLD STATE]":
            lines = lines[1:]
        return "\n".join(line for line in lines if line.strip()).strip()

    def _build_profile_summary_answer(
        self,
        *,
        knowledge_mgr: Any,
        operational_state_service: Any,
    ) -> str:
        world_state = ""
        if knowledge_mgr is not None:
            try:
                if hasattr(knowledge_mgr, "list_for_display"):
                    world_state = str(knowledge_mgr.list_for_display() or "").strip()
                elif hasattr(knowledge_mgr, "render_prompt_state"):
                    world_state = str(knowledge_mgr.render_prompt_state("", max_entities=8) or "").strip()
            except Exception:
                world_state = ""

        world_block = self._strip_world_state_heading(world_state)
        operational_block = ""
        if operational_state_service is not None:
            try:
                operational_block = str(
                    operational_state_service.build_readonly_answer("What tasks and events do I have scheduled?") or ""
                ).strip()
            except Exception:
                operational_block = ""

        parts = [part for part in [world_block, operational_block] if part]
        if not parts:
            return "I do not have any stored profile details right now."
        return "\n\n".join(parts)

    @staticmethod
    def _clean_detail(text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        if value.startswith("[WORLD STATE]"):
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            return lines[0] if lines else "[WORLD STATE]"
        return value[:300]

    @staticmethod
    def _normalize_knowledge_subject(text: str) -> str:
        subject = str(text or "").strip().strip(".?!")
        subject = re.sub(r"(?i)^that\s+", "", subject).strip()
        subject = re.sub(r"(?i)^my\s+", "", subject).strip()
        subject = re.sub(r"(?i)\s+now$", "", subject).strip()
        subject = re.sub(r"\s+", " ", subject)
        return subject.strip("'\" ")

    @staticmethod
    def _normalize_knowledge_value(text: str) -> str:
        value = str(text or "").strip().strip(".?!")
        value = re.sub(r"\s+", " ", value)
        return value.strip("'\" ")

    @staticmethod
    def _normalize_memory_remove_target(text: str) -> str:
        value = str(text or "").strip().strip(".?!")
        value = re.sub(r"\s+", " ", value)
        return value.strip("'\" ")

    @staticmethod
    def _normalize_memory_listing_text(text: str) -> str:
        value = str(text or "").strip().lower()
        value = re.sub(r"\s+", " ", value)
        return value

    def _extract_knowledge_remove_subject(self, text: str) -> str:
        match = KNOWLEDGE_REMOVE_RE.match(str(text or "").strip())
        if not match:
            return ""
        body = str(match.group("body") or "").strip()
        body = re.sub(r"(?i)^that\s+", "", body).strip()
        body = re.sub(r"(?i)\s+(?:is|are)\s+.+$", "", body).strip()
        return self._normalize_knowledge_subject(body)

    def _extract_work_state_remove_subject(self, text: str) -> str:
        candidate = str(text or "").strip().strip(".?!")
        lowered = candidate.lower()
        if "working on" not in lowered:
            return ""
        if not any(
            marker in lowered
            for marker in (
                "not working on",
                "not really working on",
                "no longer working on",
                "remove it",
                "delete it",
                "forget it",
                "remove that",
                "delete that",
                "forget that",
                "anymore",
            )
        ):
            return ""

        match = re.search(r"(?i)\bworking on\b", candidate)
        if not match:
            return ""
        subject = candidate[match.end() :].strip()
        subject = re.sub(
            r"(?i)^\s*(?:that|this|the)?\s*(?:project|script|game|app|application|code)\s*(?:called|named|about|to)?\s*",
            "",
            subject,
        ).strip()
        subject = re.sub(
            r"(?i)\b(?:please\s+)?(?:remove|delete|forget|drop|clear)\s+(?:it|that|this)\b.*$",
            "",
            subject,
        ).strip()
        subject = re.sub(r"(?i)\b(?:anymore|now)\b.*$", "", subject).strip()
        subject = re.sub(r"(?i)^\s*(?:on|with)\s+", "", subject).strip()
        subject = re.sub(r"\s+", " ", subject).strip(" ,.-")
        if not subject or subject.lower() in {"it", "that", "this"}:
            return ""
        return subject

    def _extract_contextual_memory_remove_subject(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
    ) -> str:
        text = str(user_msg or "").strip()
        if not text:
            return ""
        if not (
            _GENERIC_MEMORY_REMOVE_RE.search(text)
            or ("working on" in text.lower() and "not" in text.lower())
            or ("working on" in text.lower() and "anymore" in text.lower())
        ):
            return ""
        if not _WORK_STATE_CONTEXT_RE.search(text):
            return ""

        for message in reversed(list(recent_history or [])):
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            if role not in {"assistant", "system"}:
                continue
            content = str(message.get("content") or "")
            for relation, value in reversed(_RENDERED_WORLD_FACT_RE.findall(content)):
                relation_text = str(relation or "").strip().lower()
                value_text = str(value or "").strip()
                if relation_text == "works on" and value_text:
                    return f"works on: {value_text}"
        return ""

    def _bind_completion_target_from_recent_context(
        self,
        *,
        user_msg: str,
        recent_history: Sequence[dict[str, Any]],
        current_subject: str,
        current_is_event: bool,
    ) -> Tuple[str, bool] | None:
        current_subject = str(current_subject or "").strip()
        current_score = self._score_completion_candidate(user_msg, current_subject)
        current_suspicious = self._looks_like_unresolved_completion_subject(
            subject=current_subject,
            user_msg=user_msg,
        )

        candidate_map: Dict[Tuple[bool, str], int] = {}
        for is_event in (False, True):
            runtime_subject = extract_runtime_followup_subject(recent_history, is_event=is_event)
            if runtime_subject:
                candidate_map[(is_event, runtime_subject)] = max(
                    candidate_map.get((is_event, runtime_subject), 0),
                    self._score_completion_candidate(user_msg, runtime_subject) + 2,
                )
            for subject in extract_latest_task_event_candidates(recent_history, is_event=is_event):
                candidate_map[(is_event, subject)] = max(
                    candidate_map.get((is_event, subject), 0),
                    self._score_completion_candidate(user_msg, subject),
                )

        ranked = sorted(
            (
                (score, is_event, subject)
                for (is_event, subject), score in candidate_map.items()
                if str(subject or "").strip()
            ),
            key=lambda item: (item[0], len(str(item[2] or ""))),
            reverse=True,
        )
        if not ranked:
            return None

        best_score, best_is_event, best_subject = ranked[0]
        if best_score <= 0:
            if self._subject_is_generic_reference(current_subject):
                return best_subject, best_is_event
            return None

        if not current_subject:
            return best_subject, best_is_event
        if self._subject_is_generic_reference(current_subject):
            return best_subject, best_is_event
        if current_suspicious and best_score > 0:
            return best_subject, best_is_event
        if best_score > current_score:
            return best_subject, best_is_event
        if best_is_event != current_is_event and best_score == current_score and current_suspicious:
            return best_subject, best_is_event
        return None

    def _score_completion_candidate(self, user_msg: str, subject: str) -> int:
        subject_tokens = set(self._completion_tokens(subject))
        if not subject_tokens:
            return 0
        user_tokens = set(self._completion_tokens(user_msg))
        if not user_tokens:
            return 0
        overlap = len(subject_tokens & user_tokens)
        if overlap <= 0:
            return 0
        exact_phrase_bonus = 1 if " ".join(subject_tokens) and str(subject or "").lower() in str(user_msg or "").lower() else 0
        return overlap + exact_phrase_bonus

    def _looks_like_unresolved_completion_subject(self, *, subject: str, user_msg: str) -> bool:
        candidate = str(subject or "").strip()
        if not candidate:
            return True
        if self._subject_is_generic_reference(candidate):
            return True
        if len(candidate.split()) > 7 or "," in candidate:
            return True
        cleaned_candidate = self._normalize_completion_subject_text(candidate)
        cleaned_user = self._normalize_completion_subject_text(user_msg)
        if cleaned_candidate and cleaned_candidate == cleaned_user:
            return True
        filler_hits = len(_COMPLETION_SUBJECT_FILLER_RE.findall(candidate))
        return filler_hits >= 2

    def _completion_tokens(self, text: str) -> List[str]:
        normalized = self._normalize_completion_subject_text(text)
        tokens: List[str] = []
        for raw in _COMPLETION_TOKEN_RE.findall(normalized.lower()):
            token = _COMPLETION_IRREGULAR_TOKEN_MAP.get(raw, raw)
            if token.endswith("ing") and len(token) > 5:
                token = token[:-3]
            elif token.endswith("ed") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("es") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            if token in _COMPLETION_STOPWORDS or len(token) <= 1:
                continue
            tokens.append(token)
        return tokens

    def _normalize_completion_subject_text(self, text: str) -> str:
        candidate = strip_event_prefix(strip_followup_wrapper(str(text or "")))
        candidate = _COMPLETION_SUBJECT_FILLER_RE.sub(" ", candidate)
        candidate = re.sub(r"(?i)\b(?:it|that|this|thing)\b", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate)
        return candidate.strip(" ,.-")

    @staticmethod
    def _subject_is_generic_reference(subject: str) -> bool:
        normalized = str(subject or "").strip().lower()
        return normalized in _GENERIC_REFERENCE_SUBJECTS

    @staticmethod
    def _build_task_event_delete_card(subject: str, *, is_event: bool) -> RouteDecision:
        tool_name = "REMOVE_EVENT" if is_event else "DELETE_TASK"
        noun = "event" if is_event else "task"
        list_name = "calendar" if is_event else "task list"
        mutation = StateMutationEngine.build_mutation_request(
            state_owner="task_event",
            entity_kind=noun,
            action="remove" if is_event else "delete",
            target=subject,
        )
        stage = {
            "stage_goal": f"Remove the {noun} '{subject}' from the active {list_name}",
            "stage_type": "TASK_EVENT_WORK",
            "success_condition": f"Active {noun} is removed from the {list_name} without treating it as completed",
            "allowed_tools": [tool_name],
            "mutation": mutation,
        }
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Delete the {noun} '{subject}' from the active {list_name}",
                "context": [
                    f"The user asked to remove an existing {noun} from the active {list_name}.",
                    f"Use {tool_name} for cleanup or cancellation, not completion.",
                ],
                "stages": [stage],
            },
        }

    @staticmethod
    def _build_task_event_completion_card(subject: str, *, is_event: bool) -> RouteDecision:
        tool_name = "COMPLETE_EVENT" if is_event else "COMPLETE_TASK"
        list_tool_name = "LIST_EVENTS" if is_event else "LIST_TASKS"
        noun = "event" if is_event else "task"
        mutation = StateMutationEngine.build_mutation_request(
            state_owner="task_event",
            entity_kind=noun,
            action="complete",
            target=subject,
        )
        stage = {
            "stage_goal": f"Mark the {noun} '{subject}' as completed and archive it",
            "stage_type": "TASK_EVENT_WORK",
            "success_condition": f"Active {noun} is removed from the list and the completion is archived as memory",
            "allowed_tools": [tool_name, list_tool_name],
            "mutation": mutation,
        }
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Complete the {noun} '{subject}'",
                "context": [
                    f"The user indicated they completed the {noun}.",
                    "Use the latest runtime context as the authoritative source for the active target.",
                    f"If direct completion misses the target, inspect the active {noun} list once with {list_tool_name} and retry against the exact live record.",
                ],
                "stages": [stage],
            },
        }

    @staticmethod
    def _build_plural_task_event_followup_card(
        subjects: Sequence[str],
        *,
        is_event: bool,
        completion_mode: bool,
    ) -> RouteDecision:
        clean_subjects = [str(subject).strip() for subject in subjects if str(subject).strip()]
        noun = "event" if is_event else "task"
        list_name = "calendar" if is_event else "task list"
        action_label = "complete" if completion_mode else "remove"
        tool_name = (
            "COMPLETE_EVENT" if is_event and completion_mode
            else "REMOVE_EVENT" if is_event
            else "COMPLETE_TASK" if completion_mode
            else "DELETE_TASK"
        )
        list_tool_name = "LIST_EVENTS" if is_event else "LIST_TASKS"
        stages: List[StageCard] = []
        for subject in clean_subjects:
            mutation = StateMutationEngine.build_mutation_request(
                state_owner="task_event",
                entity_kind=noun,
                action="complete" if completion_mode else ("remove" if is_event else "delete"),
                target=subject,
            )
            stage_goal = (
                f"Mark the {noun} '{subject}' as completed and archive it"
                if completion_mode
                else f"Remove the {noun} '{subject}' from the active {list_name}"
            )
            success_condition = (
                f"Active {noun} '{subject}' is archived and no longer appears in the active {list_name}"
                if completion_mode
                else f"Active {noun} '{subject}' is removed from the active {list_name} without treating it as completed"
            )
            stages.append(
                {
                    "stage_goal": stage_goal,
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": success_condition,
                    "allowed_tools": [tool_name, list_tool_name] if completion_mode else [tool_name],
                    "mutation": mutation,
                }
            )
        return {
            "decision": "TASK",
            "card": {
                "goal": f"{action_label.capitalize()} the resolved {noun} targets from the active {list_name}",
                "context": [
                    f"The user's plural follow-up resolved to these active {noun} targets: {', '.join(clean_subjects)}.",
                    f"Apply {tool_name} once per resolved {noun}. Do not touch unrelated {noun}s.",
                    *(
                        [f"If direct completion misses a target, inspect the active {noun} list once with {list_tool_name} before retrying."]
                        if completion_mode
                        else []
                    ),
                ],
                "stages": stages,
            },
        }

    @staticmethod
    def _build_task_event_delete_clarification_card(
        *,
        is_event: bool,
        listed_subjects: Sequence[str],
    ) -> RouteDecision:
        noun = "event" if is_event else "task"
        list_name = "calendar" if is_event else "task list"
        context = [
            f"The user asked to remove an existing {noun}, but the target is ambiguous.",
            f"Ask which {noun} should be removed from the active {list_name} before mutating state.",
        ]
        if listed_subjects:
            choices = ", ".join(str(item).strip() for item in listed_subjects if str(item).strip())
            if choices:
                context.append(f"Current visible choices: {choices}")
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Clarify which {noun} to remove from the active {list_name}",
                "context": context,
                "stages": [
                    {
                        "stage_goal": f"Ask which {noun} the user wants removed from the active {list_name}",
                        "stage_type": "CHAT",
                        "success_condition": f"The user identifies the exact {noun} to remove.",
                        "allowed_tools": [],
                    }
                ],
            },
        }

    @staticmethod
    def _extract_reminder_request_subject(user_msg: str) -> str:
        text = str(user_msg or "").strip().strip(".")
        if not text:
            return ""

        reminder_patterns = (
            r"(?i)^.*?\bremind me to\s+",
            r"(?i)^.*?\bremember to\s+",
            r"(?i)^.*?\bset a reminder to\s+",
            r"(?i)^.*?\bset reminder to\s+",
            r"(?i)^.*?\bremind me about\s+",
            r"(?i)^.*?\bset a reminder for\s+",
            r"(?i)^.*?\bremind me that\s+",
        )
        subject = text
        for pattern in reminder_patterns:
            updated = re.sub(pattern, "", subject).strip()
            if updated != subject:
                subject = updated
                break

        subject = re.sub(r"(?i)\b(on|by)\s+\d{4}-\d{2}-\d{2}\b.*$", "", subject).strip(" ,.-")
        subject = re.sub(r"(?i)\b(?:on\s+)?(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\b.*$", "", subject).strip(" ,.-")
        subject = re.sub(r"(?i)\bfor that\b$", "", subject).strip(" ,.-")
        subject = re.sub(r"(?i)\bfor it\b$", "", subject).strip(" ,.-")
        subject = re.sub(r"\s+", " ", subject)
        return subject.strip("'\" ")

    @staticmethod
    def _request_should_be_event(user_msg: str, stages: List[StageCard]) -> bool:
        text = str(user_msg or "").strip()
        if not text:
            return False
        if not DATE_HINT_RE.search(text):
            return False
        if TASK_REQUEST_RE.match(text):
            return True
        if EVENT_WORD_RE.search(text):
            return True

        stage_blob = " ".join(
            f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
            for stage in stages
        ).lower()
        return "event" in stage_blob or "schedule" in stage_blob or bool(SCHEDULE_HINT_RE.search(stage_blob))

    @staticmethod
    def _extract_event_parts(user_msg: str, stages: List[StageCard]) -> Tuple[str, str]:
        date_phrase = extract_date_phrase(user_msg)
        subject = extract_event_subject(user_msg)
        if subject and date_phrase:
            return subject, date_phrase

        for stage in stages:
            goal = str(stage.get("stage_goal", "")).strip().strip(".")
            stage_date = extract_date_phrase(goal)
            stage_subject = strip_event_prefix(goal)
            if stage_subject and stage_date:
                return stage_subject, stage_date
        return "", date_phrase

    @staticmethod
    def _needs_task_stage_collapse(stages: List[StageCard]) -> bool:
        first_goal = str(stages[0].get("stage_goal", ""))
        if not looks_like_task_creation(first_goal):
            return False

        for stage in stages[1:]:
            goal = str(stage.get("stage_goal", ""))
            success = str(stage.get("success_condition", ""))
            if SCHEDULE_HINT_RE.search(goal) or SCHEDULE_HINT_RE.search(success):
                return True
        return False

    @staticmethod
    def _looks_like_soft_subject(subject: str, value: str) -> bool:
        blob = " ".join(
            part for part in [str(subject or "").strip().lower(), str(value or "").strip().lower()] if part
        )
        if not blob:
            return False
        soft_markers = (
            "project",
            "focus",
            "priority",
            "currently",
            "right now",
            "working on",
            "watching",
            "playing",
            "debugging",
            "testing",
            "using",
            "reading",
            "trying to",
        )
        return any(marker in blob for marker in soft_markers)

    @staticmethod
    def _find_knowledge_value(knowledge: dict[str, Any], subject: str) -> str:
        target = StateMutationEngine._normalize_knowledge_subject(subject).lower().replace("_", " ")
        target_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", target)
            if len(token) > 2
        }
        if not target_tokens:
            return ""

        best_value = ""
        best_score = -1
        for raw_key, payload in (knowledge or {}).items():
            key_text = str(raw_key or "").strip().lower().replace("_", " ")
            key_tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", key_text)
                if len(token) > 2
            }
            score = len(target_tokens & key_tokens)
            if score <= 0:
                continue
            value = ""
            if isinstance(payload, dict):
                value = str(payload.get("value") or "").strip()
            else:
                value = str(payload or "").strip()
            if not value:
                continue
            if score > best_score:
                best_score = score
                best_value = value
        return best_value
