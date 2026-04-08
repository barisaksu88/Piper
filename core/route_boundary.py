from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.contracts import FollowupResolution, RouteClarifierResolution, RouteDecision
from core.json_utils import parse_json_response


@dataclass(frozen=True)
class BoundaryValidationError(ValueError):
    boundary: str
    message: str
    fallback: Any = None

    def __str__(self) -> str:
        return f"{self.boundary}: {self.message}"


class RouterBoundary:
    _VALID_DECISIONS = {"CHAT", "SEARCH", "TASK"}
    _VALID_CONFIDENCE = {"low", "medium", "high"}
    _VALID_SOURCE_SCOPES = {"web", "workspace", "unknown"}

    @staticmethod
    def fallback() -> RouteDecision:
        return {"decision": "CHAT"}

    @classmethod
    def validate(cls, raw_text: str) -> RouteDecision:
        payload = parse_json_response(raw_text)
        if not isinstance(payload, dict) or not payload:
            raise BoundaryValidationError(
                boundary="RouterBoundary",
                message="router output was not valid JSON object content",
                fallback=cls.fallback(),
            )

        decision = str(payload.get("decision") or "").strip().upper()
        if decision not in cls._VALID_DECISIONS:
            raise BoundaryValidationError(
                boundary="RouterBoundary",
                message=f"invalid decision '{decision or '<missing>'}'",
                fallback=cls.fallback(),
            )

        card = payload.get("card")
        if card is not None and not isinstance(card, dict):
            raise BoundaryValidationError(
                boundary="RouterBoundary",
                message="card must be an object when present",
                fallback=cls.fallback(),
            )

        if decision == "TASK":
            if not isinstance(card, dict):
                raise BoundaryValidationError(
                    boundary="RouterBoundary",
                    message="TASK decision requires a card object",
                    fallback=cls.fallback(),
                )
            stages = card.get("stages")
            if not isinstance(stages, list) or not stages:
                raise BoundaryValidationError(
                    boundary="RouterBoundary",
                    message="TASK decision requires a non-empty stages list",
                    fallback=cls.fallback(),
                )
            if any(not isinstance(stage, dict) for stage in stages):
                raise BoundaryValidationError(
                    boundary="RouterBoundary",
                    message="TASK stages must be objects",
                    fallback=cls.fallback(),
                )

        skill = payload.get("skill")
        if skill is not None and not isinstance(skill, dict):
            raise BoundaryValidationError(
                boundary="RouterBoundary",
                message="skill must be an object when present",
                fallback=cls.fallback(),
            )

        confidence = str(payload.get("confidence") or "").strip().lower()
        if confidence and confidence not in cls._VALID_CONFIDENCE:
            raise BoundaryValidationError(
                boundary="RouterBoundary",
                message=f"invalid confidence '{confidence}'",
                fallback=cls.fallback(),
            )

        source_scope = str(payload.get("source_scope") or "").strip().lower()
        if source_scope and source_scope not in cls._VALID_SOURCE_SCOPES:
            raise BoundaryValidationError(
                boundary="RouterBoundary",
                message=f"invalid source_scope '{source_scope}'",
                fallback=cls.fallback(),
            )

        question_if_uncertain = " ".join(str(payload.get("question_if_uncertain") or "").split()).strip()

        validated: RouteDecision = {"decision": decision}
        if isinstance(card, dict):
            validated["card"] = dict(card)
        if isinstance(skill, dict) and skill:
            validated["skill"] = dict(skill)
        if confidence:
            validated["confidence"] = confidence
        if source_scope:
            validated["source_scope"] = source_scope
        if question_if_uncertain:
            validated["question_if_uncertain"] = question_if_uncertain
        return validated


class FollowupResolutionBoundary:
    _VALID_DECISIONS = {
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
    }
    _VALID_CONFIDENCE = {"low", "medium", "high"}
    _TARGET_REQUIRED = {
        "complete_task",
        "delete_task",
        "complete_event",
        "delete_event",
        "remove_knowledge",
    }

    @staticmethod
    def fallback() -> None:
        return None

    @classmethod
    def validate(cls, raw_text: str) -> FollowupResolution:
        payload = parse_json_response(raw_text)
        if not isinstance(payload, dict) or not payload:
            raise BoundaryValidationError(
                boundary="FollowupResolutionBoundary",
                message="follow-up resolver output was not valid JSON object content",
                fallback=cls.fallback(),
            )

        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in cls._VALID_DECISIONS:
            raise BoundaryValidationError(
                boundary="FollowupResolutionBoundary",
                message=f"invalid decision '{decision or '<missing>'}'",
                fallback=cls.fallback(),
            )

        confidence = str(payload.get("confidence") or "").strip().lower() or "low"
        if confidence not in cls._VALID_CONFIDENCE:
            raise BoundaryValidationError(
                boundary="FollowupResolutionBoundary",
                message=f"invalid confidence '{confidence}'",
                fallback=cls.fallback(),
            )

        target = str(payload.get("target") or "").strip()
        value = str(payload.get("value") or "").strip()
        query = str(payload.get("query") or "").strip()
        question = " ".join(str(payload.get("question") or "").split()).strip()

        if decision in cls._TARGET_REQUIRED and not target:
            raise BoundaryValidationError(
                boundary="FollowupResolutionBoundary",
                message=f"decision '{decision}' requires a target",
                fallback=cls.fallback(),
            )
        if decision == "store_knowledge" and (not target or not value):
            raise BoundaryValidationError(
                boundary="FollowupResolutionBoundary",
                message="store_knowledge requires both target and value",
                fallback=cls.fallback(),
            )

        return FollowupResolution(
            decision=decision,
            target=target,
            value=value,
            query=query,
            question=question,
            confidence=confidence,
            reason=str(payload.get("reason") or "").strip(),
        )


class RouteClarifierBoundary:
    _VALID_DECISIONS = {"keep_task", "clarify_chat"}

    @staticmethod
    def fallback() -> None:
        return None

    @classmethod
    def validate(cls, raw_text: str) -> RouteClarifierResolution:
        payload = parse_json_response(raw_text)
        if not isinstance(payload, dict) or not payload:
            raise BoundaryValidationError(
                boundary="RouteClarifierBoundary",
                message="route clarifier output was not valid JSON object content",
                fallback=cls.fallback(),
            )

        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in cls._VALID_DECISIONS:
            raise BoundaryValidationError(
                boundary="RouteClarifierBoundary",
                message=f"invalid decision '{decision or '<missing>'}'",
                fallback=cls.fallback(),
            )

        question = " ".join(str(payload.get("question") or "").split()).strip()
        if decision == "clarify_chat" and not question:
            raise BoundaryValidationError(
                boundary="RouteClarifierBoundary",
                message="clarify_chat requires a non-empty question",
                fallback=cls.fallback(),
            )

        return RouteClarifierResolution(
            decision=decision,
            question=question,
            reason=str(payload.get("reason") or "").strip(),
        )
