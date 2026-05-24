"""core/services/context_pack_renderer.py

Pure renderer and arbitration helpers for persona context packs.

These functions and classes have no side effects, no lifecycle hooks,
and no dependency on engine registries.  They are deterministic
utilities that transform typed pack models into prompt strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from core.contracts import (
    PERSONA_CONTEXT_ARBITRATION_TABLE,
    PersonaArbitrationProfile,
    PersonaContextPack,
    PersonaTurnType,
    PromptContext,
    RuntimeContextPack,
)

_LATEST_RUNTIME_CONTEXT_PREFIX = "[LATEST_RUNTIME_CONTEXT]"

_PACK_BLOCK_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "[ENVIRONMENT]": ("env_block",),
    "[WORLD STATE]": ("world_state",),
    "[SITUATIONAL STATE]": ("situational_state",),
    "[INTENT STATE]": ("intent_state",),
    "[OPERATIONAL STATE]": ("operational_state",),
    "[RETRIEVED MEMORY]": ("brain_hits",),
    "[DOCUMENT MATCHES]": ("document_hits",),
    "[DOCUMENT FOCUS]": ("document_focus", "document_references", "document_sources"),
}


def resolve_persona_turn_type(
    *,
    route_decision: Dict[str, Any] | None = None,
    reporter_just_ran: bool = False,
    ingested_document_chat: bool = False,
    document_focus_active: bool = False,
) -> PersonaTurnType:
    route = dict(route_decision or {})
    notice = dict(route.get("system_notice") or {})
    notice_kind = str(notice.get("kind") or "").strip().lower()
    if notice_kind == "proactive_trigger":
        return "PROACTIVE_TRIGGER"
    if notice_kind == "explain_last_turn":
        return "EXPLAIN"
    if reporter_just_ran:
        return "REPORTER"
    if ingested_document_chat or document_focus_active:
        return "DOC_FOCUS"
    decision = str(route.get("decision") or "").strip().upper()
    if decision == "TASK":
        return "TASK"
    if decision == "SEARCH":
        return "SEARCH_FIRST_PASS"
    return "CHAT"


def _clear_pack_field_value(field_name: str) -> Any:
    if field_name in {"brain_hits", "vision_notes", "document_hits", "document_references", "document_sources"}:
        return []
    if field_name == "knowledge":
        return {}
    return ""


def render_context_arbitration_block(turn_type: PersonaTurnType) -> str:
    profile = PERSONA_CONTEXT_ARBITRATION_TABLE.get(turn_type, PersonaArbitrationProfile())
    lines = ["[CONTEXT_ARBITRATION_RULE]"]
    lines.append(f"Turn type: {turn_type}")
    if profile.primary:
        lines.append("Primary blocks: " + " | ".join(profile.primary))
    if profile.secondary:
        lines.append("Secondary blocks: " + " | ".join(profile.secondary))
    if profile.suppressed:
        lines.append("Suppressed unless needed: " + " | ".join(profile.suppressed))
    lines.append("Prefer primary blocks first. Use secondary blocks only when they directly help.")
    if turn_type == "REPORTER":
        lines.append("This is the completed-search follow-on turn.")
        lines.append("Extend, sharpen, or correct the earlier answer. Do not restart the topic as a fresh speaker.")
    elif turn_type == "SEARCH_FIRST_PASS":
        lines.append("Give a useful immediate answer from the current context while the web search is still running.")
    elif turn_type == "EXPLAIN":
        lines.append("Explain the last turn only. Ignore unrelated context.")
    elif turn_type == "PROACTIVE_TRIGGER":
        lines.append("Deliver the reminder cleanly and do not wander into unrelated context.")
    return "\n".join(lines)


@dataclass(frozen=True)
class ContextPackRenderer:
    def to_prompt_context(self, pack: PersonaContextPack) -> PromptContext:
        return PromptContext(
            instructions=pack.instructions,
            style_overlay=pack.style_overlay or "",
            active_user_block=pack.active_user_block or "",
            knowledge=dict(pack.knowledge),
            world_state=pack.world_state,
            situational_state=pack.situational_state,
            intent_state=pack.intent_state,
            operational_state=pack.operational_state,
            env_block=pack.env_block,
            brain_hits=list(pack.brain_hits),
            vision_notes=list(pack.vision_notes),
            document_hits=list(pack.document_hits),
            document_focus=pack.document_focus,
            document_references=list(pack.document_references),
            document_sources=list(pack.document_sources),
        )

    def render_runtime_context_message(self, pack: RuntimeContextPack) -> str:
        if not any(
            (
                pack.previous_route,
                pack.previous_user_request,
                pack.task_goal,
                pack.search_query,
                pack.execution_status,
                pack.runtime_note,
                pack.relevant_paths,
                pack.reporter_just_ran,
            )
        ):
            return ""

        lines = [_LATEST_RUNTIME_CONTEXT_PREFIX]
        if pack.previous_route:
            lines.append(f"Previous route: {pack.previous_route}")
        if pack.previous_user_request:
            lines.append(f"Previous user request: {pack.previous_user_request}")
        if pack.reporter_just_ran:
            if pack.search_query:
                lines.append(f"Search query: {pack.search_query}")
            if pack.search_failed:
                lines.append("Execution status: SEARCH FAILED")
                note = "Search failed before usable web results were retrieved."
                if pack.search_error:
                    note += f" Error: {pack.search_error[:500]}"
                lines.append(f"Runtime note: {note}")
            else:
                lines.append("Execution status: SEARCH COMPLETED")
                lines.append("Runtime note: Search summary was prepared for the user.")
        else:
            if pack.task_goal:
                lines.append(f"Task goal: {pack.task_goal}")
            if pack.execution_status:
                lines.append(f"Execution status: {pack.execution_status}")
            if pack.runtime_note:
                lines.append(f"Runtime note: {pack.runtime_note}")
        if pack.relevant_paths:
            lines.append("Relevant paths: " + " | ".join(pack.relevant_paths[:4]))
        lines.append(
            "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
        )
        return "\n".join(line for line in lines if str(line or "").strip())
