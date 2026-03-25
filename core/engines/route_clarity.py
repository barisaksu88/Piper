from __future__ import annotations

import json
import re
from typing import Any, Iterable

from core.contracts import RouteClarifierResolution, RouteDecision
from core.file_stage_policy import FileStagePolicy
from core.route_boundary import RouteClarifierBoundary
from core.runtime_context import extract_latest_runtime_context_fields
from core.routing.route_patterns import COMPLETION_HINT_RE
from core.routing.route_dates import resolve_date_phrase

_RETRY_HINT_RE = re.compile(
    r"(?i)^\s*(?:"
    r"try\s+(?:it\s+|that\s+)?again|"
    r"again|"
    r"retry|"
    r"redo(?:\s+(?:it|that))?|"
    r"one\s+more\s+time|"
    r"once\s+more|"
    r"do\s+(?:it|that)\s+again|"
    r"repeat(?:\s+(?:it|that))?"
    r")\s*[.!?]*\s*$"
)
# Catches retry intent as a *prefix* — e.g. "try again the appointment"
_RETRY_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:try\s+(?:it\s+|that\s+)?again|retry|redo)\b"
)

_CLEAR_ACTION_HINT_RE = re.compile(
    r"(?i)\b("
    r"add|create|make|schedule|set|remember|forget|remove|delete|read|open|show|tell|list|find|"
    r"move|copy|rename|run|execute|launch|fix|update|change|edit|write|summari[sz]e|search|check|"
    r"inspect|analy[sz]e|review|ingest|upload|import|organize|tidy|clean|group|sort"
    r")\b"
)
_CORRECTION_FRAGMENT_RE = re.compile(
    r"(?i)^\s*(?:no\b|not\b|wrong\b|that(?:'s| is)\s+not\b|actually\b|instead\b)"
)
_PATHISH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/mnt/[a-z]/|[\w./\\-]+\.[A-Za-z0-9]{1,8})")
_AFFIRMATIVE_CONFIRM_RE = re.compile(
    r"(?is)^\s*(?:yes(?:\s*[, ]\s*please)?|please do|please do so|go ahead|sure|okay|ok|alright|all right|sounds good|do it|schedule it|schedule that)\s*[.!?]*\s*$"
)
_SCHEDULE_PROPOSAL_RE = re.compile(r"(?is)\bshall i schedule\b")
_PROPOSAL_EVENT_TITLE_RE = re.compile(
    r'(?is)\b(?:event titled|activity titled|event called|activity called)\s*["\']([^"\']+)["\']'
)
_FOR_DATE_RE = re.compile(
    r"(?is)\bfor\s+(?P<date>tomorrow|today|\d{4}-\d{2}-\d{2}|next\s+\w+|this\s+\w+)\b"
)
class RouteClarifier:
    def should_force_clarification(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Iterable[dict[str, Any]] | None = None,
    ) -> bool:
        if str((decision or {}).get("decision") or "").strip().upper() != "TASK":
            return False
        if _task_is_targeted_file_lookup(decision):
            return False
        text = str(user_msg or "").strip()
        if not text or _PATHISH_RE.search(text):
            return False

        # If there is existing conversation context the user is almost certainly
        # following up — don't demand clarification mid-thread.
        history = list(recent_history or [])
        if any(str((m or {}).get("role") or "").lower() == "assistant" for m in history):
            return False

        tokens = re.findall(r"[a-z0-9']+", text.lower())
        if not tokens or _RETRY_HINT_RE.match(text) or _RETRY_PREFIX_RE.match(text) or _CLEAR_ACTION_HINT_RE.search(text) or COMPLETION_HINT_RE.search(text):
            return False
        if len(tokens) <= 4:
            return True
        if len(tokens) <= 5 and _CORRECTION_FRAGMENT_RE.search(text):
            return True
        return False

    def should_refine_task_route(self, *, decision: RouteDecision, user_msg: str) -> bool:
        if str((decision or {}).get("decision") or "").strip().upper() != "TASK":
            return False
        if _task_is_targeted_file_lookup(decision):
            return False
        text = str(user_msg or "").strip()
        if not text:
            return False
        if _PATHISH_RE.search(text):
            return False

        tokens = re.findall(r"[a-z0-9']+", text.lower())
        if not tokens:
            return False
        if COMPLETION_HINT_RE.search(text):
            return False
        if len(tokens) <= 6 and not _CLEAR_ACTION_HINT_RE.search(text):
            return True
        if _CORRECTION_FRAGMENT_RE.search(text) and not _CLEAR_ACTION_HINT_RE.search(text):
            return True
        return False

    def refine_with_llm(
        self,
        *,
        llm: Any,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Iterable[dict[str, Any]] | None = None,
        cancel_token: Any | None = None,
    ) -> RouteDecision | None:
        proposal_confirmation = self._build_route_from_proposal_confirmation(
            decision=decision,
            user_msg=user_msg,
            recent_history=recent_history or [],
        )
        if proposal_confirmation is not None:
            return proposal_confirmation
        if self.should_force_clarification(decision=decision, user_msg=user_msg, recent_history=recent_history):
            return self._build_clarification_route(user_msg=user_msg)
        if not self.should_refine_task_route(decision=decision, user_msg=user_msg):
            return None

        messages = self._build_classifier_messages(
            decision=decision,
            user_msg=user_msg,
            recent_history=recent_history or [],
        )
        raw = llm.generate(messages, temperature=0.0, cancel_token=cancel_token)
        parsed = RouteClarifierBoundary.validate(raw)
        return self._build_route_from_resolution(parsed, user_msg=user_msg)

    def _build_classifier_messages(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Iterable[dict[str, Any]],
    ) -> list[dict[str, str]]:
        history_tail = []
        for item in list(recent_history or [])[-6:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role and content:
                history_tail.append({"role": role, "content": content})

        sys_prompt = (
            "Decide whether the latest user turn is specific enough to continue the current TASK route, "
            "or whether Piper should ask a clarification question instead.\n"
            "Return ONLY valid JSON with keys: decision, question, reason.\n"
            "Allowed decisions: keep_task, clarify_chat.\n"
            "Choose clarify_chat when the latest user text is fragmentary, corrective, or underspecified "
            "and does not clearly say what action to take.\n"
            "Choose keep_task only when the latest user text gives a concrete actionable instruction.\n"
            "If you choose clarify_chat, provide one short question asking what the user wants changed or clarified."
        )
        payload = {
            "latest_user_input": str(user_msg or "").strip(),
            "current_route_decision": decision,
            "recent_history": history_tail,
        }
        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]

    def _build_route_from_resolution(
        self,
        resolution: RouteClarifierResolution,
        *,
        user_msg: str,
    ) -> RouteDecision | None:
        decision = str(resolution.decision or "").strip().lower()
        if decision != "clarify_chat":
            return None
        question = " ".join(str(resolution.question or "").split()).strip()
        return self._build_clarification_route(user_msg=user_msg, question=question)

    def _build_clarification_route(self, *, user_msg: str, question: str = "") -> RouteDecision:
        context = [
            "The latest user turn is too ambiguous to execute safely as a concrete task.",
            "Ask the user what they specifically want before taking further action.",
        ]
        if question:
            context.append(f"Preferred clarification question: {question}")
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Clarify the user's ambiguous request: {str(user_msg or '').strip()}",
                "context": context,
                "stages": [
                    {
                        "stage_goal": f"Ask the user to clarify what they mean by: {str(user_msg or '').strip()}",
                        "stage_type": "CHAT",
                        "success_condition": "A concise clarification question is ready for the user.",
                        "allowed_tools": [],
                    }
                ],
            },
        }

    def _build_route_from_proposal_confirmation(
        self,
        *,
        decision: RouteDecision,
        user_msg: str,
        recent_history: Iterable[dict[str, Any]],
    ) -> RouteDecision | None:
        if str((decision or {}).get("decision") or "").strip().upper() != "TASK":
            return None
        text = str(user_msg or "").strip()
        if not _AFFIRMATIVE_CONFIRM_RE.match(text):
            return None

        history = [dict(item) for item in (recent_history or []) if isinstance(item, dict)]
        assistant_msg = self._latest_assistant_message(history)
        if not assistant_msg or not _SCHEDULE_PROPOSAL_RE.search(assistant_msg):
            return None

        subject = self._extract_subject_from_schedule_proposal(assistant_msg)
        if not subject:
            subject = self._extract_subject_from_runtime_or_user_context(history)
        if not subject:
            return None

        date_phrase = self._extract_date_from_schedule_proposal(assistant_msg)
        if not date_phrase:
            return None
        resolved_date = resolve_date_phrase(date_phrase) or date_phrase

        return {
            "decision": "TASK",
            "card": {
                "goal": f"Add an event for {subject} on {resolved_date}",
                "context": [
                    "The user explicitly confirmed Piper's immediately previous proposal.",
                    f"Piper had just proposed scheduling '{subject}' for {resolved_date}.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Schedule the event '{subject}' for {resolved_date}",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": "Event is created once with the requested date",
                        "allowed_tools": ["ADD_EVENT"],
                    }
                ],
            },
        }

    @staticmethod
    def _latest_assistant_message(recent_history: Iterable[dict[str, Any]]) -> str:
        for item in reversed(list(recent_history or [])):
            if str(item.get("role") or "").strip().lower() == "assistant":
                content = str(item.get("content") or "").strip()
                if content and content.lower() != "thinking...":
                    return content
        return ""

    @staticmethod
    def _extract_date_from_schedule_proposal(assistant_msg: str) -> str:
        match = _FOR_DATE_RE.search(str(assistant_msg or ""))
        return str(match.group("date") or "").strip() if match else ""

    @staticmethod
    def _extract_subject_from_schedule_proposal(assistant_msg: str) -> str:
        match = _PROPOSAL_EVENT_TITLE_RE.search(str(assistant_msg or ""))
        if match:
            return " ".join(str(match.group(1) or "").split()).strip()
        return ""

    def _extract_subject_from_runtime_or_user_context(self, recent_history: Iterable[dict[str, Any]]) -> str:
        runtime = extract_latest_runtime_context_fields(recent_history)
        previous_request = str(runtime.get("previous_user_request") or "").strip()
        if previous_request:
            subject = self._extract_subject_from_user_correction(previous_request)
            if subject:
                return subject
        for item in reversed(list(recent_history or [])):
            if str(item.get("role") or "").strip().lower() != "user":
                continue
            content = str(item.get("content") or "").strip()
            if not content or _AFFIRMATIVE_CONFIRM_RE.match(content):
                continue
            subject = self._extract_subject_from_user_correction(content)
            if subject:
                return subject
        return ""

    @staticmethod
    def _extract_subject_from_user_correction(text: str) -> str:
        candidate = str(text or "").strip().strip(".?!")
        if not candidate:
            return ""
        parts = [part.strip() for part in re.split(r"[;,]", candidate) if part.strip()]
        if parts:
            candidate = parts[-1]
        candidate = re.sub(r"(?i)^(?:not|no|actually|instead)\b\s*", "", candidate).strip()
        candidate = re.sub(r"(?i)^bike loot\b", "", candidate).strip(" ,.-")
        candidate = re.sub(r"\s+", " ", candidate)
        return candidate.strip(" '\"")


def _task_is_targeted_file_lookup(decision: RouteDecision) -> bool:
    if str((decision or {}).get("decision") or "").strip().upper() != "TASK":
        return False
    card = dict((decision or {}).get("card") or {})
    stages = [dict(stage) for stage in card.get("stages") or [] if isinstance(stage, dict)]
    if not stages:
        return False
    return any(
        FileStagePolicy.stage_requires_targeted_read(stage)
        or FileStagePolicy.stage_requires_targeted_lookup(stage)
        for stage in stages
    )
