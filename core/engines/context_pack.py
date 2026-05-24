from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict

from core.contracts import (
    PersonaDirectivePack,
    PersonaRuntimePack,
)
from core.feature_hooks import register_hook
from core.engines.tail_block_registry import (
    TailBlockContext,
    _TAIL_BLOCK_REGISTRY,
)
from core.services.context_pack_renderer import _LATEST_RUNTIME_CONTEXT_PREFIX


@dataclass
class ContextPackDirectiveEngine:
    def build_persona_directive_pack(
        self,
        *,
        route_decision: Dict[str, Any] | None = None,
        ingested_document_chat: bool = False,
        document_focus_active: bool = False,
        reporter_just_ran: bool = False,
        active_skill: Dict[str, Any] | None = None,
        persona_runtime: PersonaRuntimePack | None = None,
    ) -> PersonaDirectivePack:
        runtime = persona_runtime or PersonaRuntimePack()
        route = dict(route_decision or {})
        skill = dict(active_skill or {})
        tail_context = TailBlockContext(
            route=route,
            runtime=runtime,
            ingested_document_chat=bool(ingested_document_chat),
            document_focus_active=bool(document_focus_active),
            reporter_just_ran=bool(reporter_just_ran),
            skill=skill,
        )
        tail_system_blocks: list[str] = []
        for builder in _TAIL_BLOCK_REGISTRY:
            block = builder(tail_context)
            if block:
                tail_system_blocks.append(block)

        direct_answer = ""
        if runtime.outcome_paused and runtime.proposal_answer:
            direct_answer = runtime.proposal_answer
        elif runtime.outcome_failed:
            direct_answer = self._build_dependency_failure_direct_answer(runtime)
        elif not runtime.outcome_failed and not runtime.outcome_paused and not reporter_just_ran:
            if runtime.analysis_report_answer:
                direct_answer = runtime.analysis_report_answer
            elif runtime.exact_file_read_answer and runtime.latest_stage_is_targeted_read and not runtime.latest_stage_requires_analysis_report:
                direct_answer = runtime.exact_file_read_answer
            elif runtime.file_lookup_answer and runtime.latest_stage_is_targeted_lookup:
                direct_answer = runtime.file_lookup_answer
            elif runtime.verified_file_work_answer and runtime.outcome_block.count("=== STAGE") <= 1:
                # For multi-stage tasks (more than one STAGE block) let the LLM
                # produce a full summary that covers every stage.  The fast-path
                # only covers single-stage, single-file completions reliably.
                direct_answer = runtime.verified_file_work_answer
            elif runtime.verified_browser_answer and runtime.outcome_block.count("=== STAGE") <= 1:
                direct_answer = runtime.verified_browser_answer

        return PersonaDirectivePack(
            tail_system_blocks=tail_system_blocks,
            direct_answer=direct_answer,
        )

    @staticmethod
    def _build_dependency_failure_direct_answer(runtime: PersonaRuntimePack) -> str:
        outcome_text = str(runtime.outcome_block or "").strip()
        if not outcome_text:
            return ""
        match = re.search(
            r"ACTIVE_(?:TASK|EVENT)_DEPENDENCY:\s*Cannot\s+(?P<verb>delete|move)\s+'(?P<path>[^']+)':\s*"
            r"referenced by active (?P<kind>task|event) '(?P<name>[^']+)'\.",
            outcome_text,
            re.IGNORECASE,
        )
        if not match:
            return ""
        verb = str(match.group("verb") or "").strip().lower() or "change"
        path = str(match.group("path") or "").strip()
        kind = str(match.group("kind") or "").strip().lower() or "item"
        name = str(match.group("name") or "").strip()
        if not path or not name:
            return ""
        return (
            f"I couldn't {verb} `{path}` because it's referenced by the active {kind} `{name}`. "
            f"Close or update that {kind} first, or tell me to override it explicitly."
        )


@register_hook("on_turn_end")
def _hook_upsert_runtime_context(orc, *, reporter_just_ran: bool = False) -> None:
    notice = dict((getattr(orc, "route_decision", {}) or {}).get("system_notice") or {})
    if str(notice.get("kind") or "").strip().lower() == "file_target_confirmation_cancelled":
        try:
            orc.chat.remove_hidden_system_message(_LATEST_RUNTIME_CONTEXT_PREFIX)
        except AttributeError:
            pass
        return
    payload = orc.prompt_context.build_runtime_context_message(
        orc,
        reporter_just_ran=reporter_just_ran,
    )
    if not payload:
        return
    try:
        orc.chat.upsert_hidden_system_message(_LATEST_RUNTIME_CONTEXT_PREFIX, payload)
    except AttributeError:
        orc.chat.append_message({"role": "system", "content": payload, "hidden": True})
