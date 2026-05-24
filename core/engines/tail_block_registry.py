"""core/engines/tail_block_registry.py

Tail-block registry for persona directive packs.

This module owns the global `_TAIL_BLOCK_REGISTRY` and all tail-block
builders that append to it at import time.  It is engine/lifecycle
behavior and must stay under `core/engines/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from core.contracts import PersonaRuntimePack
from core.services.context_pack_renderer import (
    render_context_arbitration_block,
    resolve_persona_turn_type,
)
from core.turn_explanation import render_explain_last_turn_block


@dataclass(frozen=True)
class TailBlockContext:
    route: Dict[str, Any]
    runtime: PersonaRuntimePack
    ingested_document_chat: bool
    document_focus_active: bool
    reporter_just_ran: bool
    skill: Dict[str, Any]


TailBlockBuilder = Callable[[TailBlockContext], str]
_TAIL_BLOCK_REGISTRY: list[TailBlockBuilder] = []


def register_tail_block(fn: TailBlockBuilder) -> TailBlockBuilder:
    _TAIL_BLOCK_REGISTRY.append(fn)
    return fn


@register_tail_block
def _tail_block_no_mutation_rule(ctx: TailBlockContext) -> str:
    if ctx.runtime.outcome_block or str(ctx.route.get("decision") or "").upper() != "CHAT":
        return ""
    return (
        "[NO_MUTATION_RULE]\n"
        "This turn did not execute any task, event, or record update.\n"
        "Do not claim that you updated records, logged anything, scheduled anything, "
        "or changed the user's state unless a completed system outcome explicitly says so."
    )


@register_tail_block
def _tail_block_context_arbitration(ctx: TailBlockContext) -> str:
    return render_context_arbitration_block(
        resolve_persona_turn_type(
            route_decision=ctx.route,
            reporter_just_ran=ctx.reporter_just_ran,
            ingested_document_chat=ctx.ingested_document_chat,
            document_focus_active=ctx.document_focus_active,
        )
    )


@register_tail_block
def _tail_block_document_qa_rule(ctx: TailBlockContext) -> str:
    if not ctx.ingested_document_chat:
        return ""
    return (
        "[DOCUMENT_QA_RULE]\n"
        "This is a read-only question about ingested document memory already supplied in system context.\n"
        "Use [DOCUMENT FOCUS] as the only authoritative document evidence for this turn.\n"
        "Do not supplement from raw [INGESTED DOCUMENTS], retrieved memory, earlier turns, or general/world knowledge.\n"
        "Do not narrate file-tool failures, PDF read attempts, or timeout errors unless the current outcome block explicitly says they happened in this turn.\n"
        "If [DOCUMENT FOCUS] says no grounded answer could be extracted, say you do not know from the supplied document instead of inventing missing content."
    )


@register_tail_block
def _tail_block_search_report_rule(ctx: TailBlockContext) -> str:
    if not ctx.reporter_just_ran:
        return ""
    return (
        "[SEARCH_REPORT_RULE]\n"
        "This turn is the final user-facing summary of a search attempt that already finished.\n"
        "The user already received an initial response while the search was running.\n"
        "Make the handoff visible: phrase the answer as the completed web search result, not as a second standalone opinion.\n"
        "Use the search summary or search failure note to extend, refine, or correct that earlier response.\n"
        "Do not say you need to check, will check, are checking, or should search; the search already happened.\n"
        "Do not restart from scratch or repeat unchanged context when the search findings simply confirm it.\n"
        "If the findings are thin, partial, off-topic, or only snippet-backed, say that directly instead of padding.\n"
        "Do not add likely winners, likely companies, dates, specs, rankings, causes, or other guesses that are not present in the search summary.\n"
        "Answer directly from the search summary. Do not append [ROUTER] unless the user asked for a brand-new action beyond this finished search attempt."
    )


@register_tail_block
def _tail_block_explain_last_turn(ctx: TailBlockContext) -> str:
    notice = dict((ctx.route or {}).get("system_notice") or {})
    if str(notice.get("kind") or "").strip().lower() != "explain_last_turn":
        return ""
    return render_explain_last_turn_block(
        dict(notice.get("snapshot") or {}),
        detail_level=str(notice.get("detail_level") or "default").strip().lower() or "default",
    )


@register_tail_block
def _tail_block_active_skill(ctx: TailBlockContext) -> str:
    # Local import avoids a circular dependency: context_pack imports
    # _TAIL_BLOCK_REGISTRY from this module, and this builder calls a
    # staticmethod on ContextPackEngine.
    from core.engines.context_pack import ContextPackEngine
    return ContextPackEngine._render_persona_active_skill_block(ctx.skill)


@register_tail_block
def _tail_block_verification_result(ctx: TailBlockContext) -> str:
    # Local import avoids a circular dependency (same reason as above).
    from core.engines.context_pack import ContextPackEngine
    return ContextPackEngine._render_verification_result_block(ctx.runtime)


@register_tail_block
def _tail_block_file_work_report(ctx: TailBlockContext) -> str:
    if not ctx.runtime.needs_file_work_report_rule:
        return ""
    if ctx.runtime.verification_verdict == "PARTIAL":
        detail_parts: list[str] = []
        if ctx.runtime.verification_checker_path:
            detail_parts.append(f"Checker path: {ctx.runtime.verification_checker_path}.")
        if ctx.runtime.verification_recommendation:
            detail_parts.append(f"Recommendation: {ctx.runtime.verification_recommendation}.")
        if ctx.runtime.verification_evidence:
            detail_parts.append(f"Evidence gap: {ctx.runtime.verification_evidence}")
        return (
            "[PARTIAL_VERIFICATION_RULE]\n"
            "Verification returned PARTIAL - the stage executed but artifact state is not fully confirmed.\n"
            + ("\n".join(detail_parts) + "\n" if detail_parts else "")
            + "Report only what was actually verified. Do not narrate full success.\n"
            + "Acknowledge the gap explicitly: say what was done and what could not be confirmed.\n"
            + "Do not claim the file, code, or task is complete unless the outcome block says VERIFIED."
        )
    return (
        "[FILE_WORK_REPORT_RULE]\n"
        "This completed turn was a FILE_WORK task.\n"
        "Use LAST_LOG and the stage success condition as the only authoritative completion evidence.\n"
        "Do not restate or infer full file contents unless the current runtime evidence explicitly contains an exact readback.\n"
        "If the evidence only proves a state change, report the verified change only.\n"
        "Do not claim that code, a file, or an executable is ready merely because RUN_CODE or FILE_OP executed.\n"
        "If current runtime evidence does not verify the requested artifact state, say only what was actually verified."
    )


@register_tail_block
def _tail_block_failed_verification(ctx: TailBlockContext) -> str:
    if ctx.runtime.verification_verdict != "FAILED":
        return ""
    detail_parts: list[str] = []
    if ctx.runtime.verification_checker_path:
        detail_parts.append(f"Checker path: {ctx.runtime.verification_checker_path}.")
    if ctx.runtime.verification_recommendation:
        detail_parts.append(f"Recommendation: {ctx.runtime.verification_recommendation}.")
    if ctx.runtime.verification_evidence:
        detail_parts.append(f"Failure evidence: {ctx.runtime.verification_evidence}")
    return (
        "[FAILED_VERIFICATION_RULE]\n"
        "Verification returned FAILED for the latest stage.\n"
        + ("\n".join(detail_parts) + "\n" if detail_parts else "")
        + "Report the failure honestly. Do not claim the requested change, update, or completion happened.\n"
        + "Do not claim any file was moved, copied, renamed, created, or deleted unless "
        + "FILE_CHECKER VERIFIED evidence explicitly confirms it — the tool ran, but the "
        + "requested end state was not reached.\n"
        + "If a state mutation was attempted, do not say records were changed unless the verification result says VERIFIED."
    )


@register_tail_block
def _tail_block_failed_outcome_no_verification(ctx: TailBlockContext) -> str:
    """Fire when the stage outcome is FAILED but no typed verification verdict was recorded.

    This covers the gap where the verifier never ran (e.g. the source file was missing,
    the planner exhausted its steps before reaching the mutating action, or the stage
    failed during planning rather than execution).  Without this block the persona only
    sees the [INSTRUCTION] directive in the outcome block — no tail rule reinforces it.
    """
    if not ctx.runtime.outcome_failed:
        return ""
    # _tail_block_failed_verification already fires when a typed verdict exists — skip.
    if ctx.runtime.verification_verdict:
        return ""
    if not ctx.runtime.outcome_block:
        return ""
    return (
        "[FAILED_OUTCOME_RULE]\n"
        "This stage FAILED or was incomplete before file-state verification could confirm any mutation.\n"
        "Do not claim any file was moved, copied, renamed, created, or deleted.\n"
        "Only report what FILE_CHECKER VERIFIED evidence explicitly confirms.\n"
        "Use LAST_LOG as the sole authoritative cause of the failure — do not invent a reason."
    )


@register_tail_block
def _tail_block_workspace_boundary(ctx: TailBlockContext) -> str:
    if not ctx.runtime.needs_file_work_report_rule:
        return ""
    return (
        "[WORKSPACE_BOUNDARY_RULE]\n"
        "FILE_WORK tools are restricted to the workspace folder.\n"
        "If a file operation failed because the path is outside the workspace, "
        "do not invent file contents, line numbers, or code snippets.\n"
        "Do not offer to create a replacement file in a different location.\n"
        "State honestly that you cannot access files outside the workspace."
    )
