from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List

from config import CFG
from core.contracts import (
    PersonaContextPack,
    PersonaDirectivePack,
    PersonaRuntimePack,
    PromptContext,
    RuntimeContextPack,
)
from core.engines.summary import SummaryEngine
from core.engines.verification import VerificationResult
from core.file_stage_policy import FileStagePolicy

_LATEST_RUNTIME_CONTEXT_PREFIX = "[LATEST_RUNTIME_CONTEXT]"
_RUNTIME_CONTEXT_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/][^\s`\"'<>|]+|/mnt/[a-z]/[^\s`\"'<>|]+|[\w./\\-]+\.[A-Za-z0-9]{1,8})"
)


@dataclass(frozen=True)
class ContextPackRenderer:
    def to_prompt_context(self, pack: PersonaContextPack) -> PromptContext:
        return PromptContext(
            instructions=pack.instructions,
            style_overlay=pack.style_overlay or "",
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


@dataclass
class ContextPackEngine:
    instruction_loader: Any
    environment_service: Any
    operational_state_service: Any
    knowledge_mgr: Any
    brain: Any
    document_memory: Any
    vision_session_memory: Any | None = None
    transient_state_mgr: Any | None = None
    renderer: ContextPackRenderer = field(default_factory=ContextPackRenderer)

    def build_persona_pack(
        self,
        *,
        user_msg: str,
        style_overlay: str = "",
        knowledge_enabled: bool = True,
        brain_limit: int = 5,
        document_limit: int = 5,
    ) -> PersonaContextPack:
        instructions = self.instruction_loader.load()
        situational_state = ""
        intent_state = ""
        if knowledge_enabled and self.transient_state_mgr is not None:
            situational_state = self.transient_state_mgr.render_situational_state(user_msg)
            intent_state = self.transient_state_mgr.render_intent_state(user_msg)
        elif knowledge_enabled:
            situational_state = self.knowledge_mgr.render_situational_state(user_msg)
        knowledge = self.knowledge_mgr.load() if knowledge_enabled else {}
        world_state = self.knowledge_mgr.render_prompt_state(user_msg) if knowledge_enabled else ""
        operational_state = self.operational_state_service.render_block(query=user_msg) if knowledge_enabled else ""
        env_block = self.environment_service.render_block()

        brain_hits: List[Dict[str, Any]] = []
        if knowledge_enabled and user_msg:
            try:
                brain_hits = self.brain.recall(user_msg, n_results=max(int(brain_limit), 0))
            except Exception:
                brain_hits = []

        vision_notes: List[str] = []
        if self.vision_session_memory is not None and self.vision_session_memory.is_active():
            vision_notes = self.vision_session_memory.recent_notes(limit=5)

        document_hits: List[Dict[str, Any]] = []
        if knowledge_enabled and int(document_limit) > 0:
            try:
                document_hits = self.document_memory.render_prompt_hits(
                    user_msg,
                    limit=max(int(document_limit), 0),
                )
            except Exception:
                document_hits = []

        return PersonaContextPack(
            user_msg=str(user_msg or ""),
            knowledge_enabled=bool(knowledge_enabled),
            instructions=instructions,
            style_overlay=style_overlay or "",
            knowledge=knowledge,
            world_state=world_state,
            situational_state=situational_state,
            intent_state=intent_state,
            operational_state=operational_state,
            env_block=env_block,
            brain_hits=brain_hits,
            vision_notes=vision_notes,
            document_hits=document_hits,
        )

    def apply_document_focus(
        self,
        pack: PersonaContextPack,
        *,
        focus_text: str,
        references: List[str] | None = None,
        sources: List[str] | None = None,
        clear_document_hits: bool = True,
    ) -> PersonaContextPack:
        return replace(
            pack,
            document_hits=[] if clear_document_hits else list(pack.document_hits),
            document_focus=str(focus_text or "").strip(),
            document_references=list(references or []),
            document_sources=list(sources or []),
        )

    def clear_memory_for_file_work(self, pack: PersonaContextPack) -> PersonaContextPack:
        return replace(
            pack,
            brain_hits=[],
            document_hits=[],
        )

    def to_prompt_context(self, pack: PersonaContextPack) -> PromptContext:
        return self.renderer.to_prompt_context(pack)

    def build_runtime_context_pack(self, orc, *, reporter_just_ran: bool = False) -> RuntimeContextPack:
        decision = str(getattr(orc, "route_decision", {}).get("decision") or "").strip().upper()
        card = dict(getattr(orc, "context_card", {}) or getattr(orc, "route_decision", {}).get("card") or {})
        if decision not in {"TASK", "SEARCH"} and not reporter_just_ran:
            return RuntimeContextPack()
        user_msg = str(getattr(orc, "user_msg", "") or "").strip()
        return RuntimeContextPack(
            previous_route=decision,
            previous_user_request=user_msg,
            task_goal="" if reporter_just_ran else str(card.get("goal") or "").strip(),
            search_query=str(card.get("query") or user_msg).strip() if (decision == "SEARCH" or reporter_just_ran) else "",
            execution_status="" if reporter_just_ran else SummaryEngine.extract_stage_status(getattr(orc, "scratchpad", []) or []),
            runtime_note="" if reporter_just_ran else SummaryEngine.build_runtime_note(getattr(orc, "scratchpad", []) or []),
            relevant_paths=self._collect_runtime_context_paths(orc),
            reporter_just_ran=bool(reporter_just_ran),
        )

    def render_runtime_context_message(self, pack: RuntimeContextPack) -> str:
        return self.renderer.render_runtime_context_message(pack)

    def build_persona_runtime_pack(
        self,
        scratchpad: list[str],
        *,
        latest_stage: Dict[str, Any] | None = None,
        reporter_just_ran: bool = False,
        escalation_active: bool = False,
        verification_result: VerificationResult | None = None,
    ) -> PersonaRuntimePack:
        stage = dict(latest_stage or {})
        outcome_block = SummaryEngine.build_outcome_block(scratchpad, escalation_active=escalation_active)
        outcome_upper = outcome_block.upper()
        # Primary source: typed VerificationResult from VerificationEngine.
        # Fallback: infer from scratchpad text (for stages where verification
        # was not run, e.g. CHAT, MEMORY_WORK).
        verification_verdict = ""
        verification_evidence = ""
        if verification_result is not None:
            verification_verdict = str(verification_result.verdict or "")
            verification_evidence = str(verification_result.evidence_summary or "")
            # PARTIAL is not success — authoritative override.
            outcome_failed = verification_verdict in ("PARTIAL", "FAILED")
        else:
            outcome_failed = "FAILED / INCOMPLETE" in outcome_upper or "RESULT: FAILED" in outcome_upper
        outcome_paused = "PAUSED / AWAITING USER" in outcome_upper
        proposal_answer = SummaryEngine.extract_proposal(scratchpad)
        exact_file_read_answer = SummaryEngine.extract_exact_file_read(scratchpad)
        file_lookup_answer = SummaryEngine.extract_file_lookup(scratchpad)
        verified_file_work_answer = SummaryEngine.extract_verified_result(scratchpad)
        analysis_report_answer = (
            proposal_answer
            if stage and FileStagePolicy.stage_requires_analysis_report(stage)
            else ""
        )
        latest_stage_is_targeted_read = bool(stage) and FileStagePolicy.stage_requires_targeted_read(stage)
        latest_stage_is_targeted_lookup = bool(stage) and FileStagePolicy.stage_requires_targeted_lookup(stage)
        latest_stage_requires_analysis_report = bool(stage) and FileStagePolicy.stage_requires_analysis_report(stage)
        # PARTIAL verdict: stage is file-work that needs reporting (partial evidence
        # is still evidence — persona must narrate what was and wasn't verified).
        is_partial = verification_verdict == "PARTIAL"
        needs_file_work_report_rule = (
            bool(stage)
            and FileStagePolicy.stage_is_file_work(stage)
            and (not outcome_failed or is_partial)
            and not outcome_paused
            and not exact_file_read_answer
            and not file_lookup_answer
            and not reporter_just_ran
        )
        return PersonaRuntimePack(
            outcome_block=outcome_block,
            outcome_failed=outcome_failed,
            outcome_paused=outcome_paused,
            proposal_answer=proposal_answer,
            analysis_report_answer=analysis_report_answer,
            exact_file_read_answer=exact_file_read_answer,
            file_lookup_answer=file_lookup_answer,
            verified_file_work_answer=verified_file_work_answer,
            latest_stage_requires_analysis_report=latest_stage_requires_analysis_report,
            latest_stage_is_targeted_read=latest_stage_is_targeted_read,
            latest_stage_is_targeted_lookup=latest_stage_is_targeted_lookup,
            needs_file_work_report_rule=needs_file_work_report_rule,
            verification_verdict=verification_verdict,
            verification_evidence=verification_evidence,
        )

    def build_persona_directive_pack(
        self,
        *,
        route_decision: Dict[str, Any] | None = None,
        ingested_document_chat: bool = False,
        reporter_just_ran: bool = False,
        active_skill: Dict[str, Any] | None = None,
        latest_codex_escalation: Dict[str, Any] | None = None,
        persona_runtime: PersonaRuntimePack | None = None,
    ) -> PersonaDirectivePack:
        runtime = persona_runtime or PersonaRuntimePack()
        route = dict(route_decision or {})
        skill = dict(active_skill or {})
        codex_escalation = dict(latest_codex_escalation or {})
        tail_system_blocks: list[str] = []

        if not runtime.outcome_block and str(route.get("decision") or "").upper() == "CHAT":
            tail_system_blocks.append(
                "[NO_MUTATION_RULE]\n"
                "This turn did not execute any task, event, or record update.\n"
                "Do not claim that you updated records, logged anything, scheduled anything, "
                "or changed the user's state unless a completed system outcome explicitly says so."
            )
        if ingested_document_chat:
            tail_system_blocks.append(
                "[DOCUMENT_QA_RULE]\n"
                "This is a read-only question about ingested document memory already supplied in system context.\n"
                "Use [DOCUMENT FOCUS] as the only authoritative document evidence for this turn.\n"
                "Do not supplement from raw [INGESTED DOCUMENTS], retrieved memory, earlier turns, or general/world knowledge.\n"
                "Do not narrate file-tool failures, PDF read attempts, or timeout errors unless the current outcome block explicitly says they happened in this turn.\n"
                "If [DOCUMENT FOCUS] says no grounded answer could be extracted, say you do not know from the supplied document instead of inventing missing content."
            )
        if reporter_just_ran:
            tail_system_blocks.append(
                "[SEARCH_REPORT_RULE]\n"
                "This turn is the final user-facing summary of a search that already completed.\n"
                "Answer directly from the completed search summary. Do not append [ROUTER] unless the user asked for a brand-new action beyond this completed search."
            )
        skill_block = self._render_persona_active_skill_block(skill)
        if skill_block:
            tail_system_blocks.append(skill_block)
        if codex_escalation and runtime.outcome_failed:
            tail_system_blocks.append(
                "[ENGINEERING_SUPPORT_RULE]\n"
                "This task prepared an engineering support brief because the runtime detected a real execution issue.\n"
                "Be honest that engineering support has been prepared.\n"
                "Do not claim the issue is already fixed.\n"
                f"Escalation summary: {str(codex_escalation.get('summary') or '').strip()}\n"
                f"Escalation log: {str(codex_escalation.get('brief_path') or '').strip()}"
            )
        if runtime.needs_file_work_report_rule:
            if runtime.verification_verdict == "PARTIAL":
                evidence_note = (
                    f" Evidence gap: {runtime.verification_evidence}"
                    if runtime.verification_evidence
                    else ""
                )
                tail_system_blocks.append(
                    "[PARTIAL_VERIFICATION_RULE]\n"
                    "Verification returned PARTIAL — the stage executed but artifact state is not fully confirmed."
                    + evidence_note + "\n"
                    "Report only what was actually verified. Do not narrate full success.\n"
                    "Acknowledge the gap explicitly: say what was done and what could not be confirmed.\n"
                    "Do not claim the file, code, or task is complete unless the outcome block says VERIFIED."
                )
            else:
                tail_system_blocks.append(
                    "[FILE_WORK_REPORT_RULE]\n"
                    "This completed turn was a FILE_WORK task.\n"
                    "Use LAST_LOG and the stage success condition as the only authoritative completion evidence.\n"
                    "Do not restate or infer full file contents unless the current runtime evidence explicitly contains an exact readback.\n"
                    "If the evidence only proves a state change, report the verified change only.\n"
                    "Do not claim that code, a file, or an executable is ready merely because RUN_CODE or FILE_OP executed.\n"
                    "If current runtime evidence does not verify the requested artifact state, say only what was actually verified."
                )

        direct_answer = ""
        if runtime.outcome_paused and runtime.proposal_answer:
            direct_answer = runtime.proposal_answer
        elif not runtime.outcome_failed and not runtime.outcome_paused and not reporter_just_ran:
            if runtime.analysis_report_answer:
                direct_answer = runtime.analysis_report_answer
            elif runtime.exact_file_read_answer and runtime.latest_stage_is_targeted_read and not runtime.latest_stage_requires_analysis_report:
                direct_answer = runtime.exact_file_read_answer
            elif runtime.file_lookup_answer and runtime.latest_stage_is_targeted_lookup:
                direct_answer = runtime.file_lookup_answer
            elif runtime.verified_file_work_answer:
                direct_answer = runtime.verified_file_work_answer

        return PersonaDirectivePack(
            tail_system_blocks=tail_system_blocks,
            direct_answer=direct_answer,
        )

    # latest_stage_entries, extract_exact_file_read_answer, extract_file_lookup_answer,
    # _is_generic_file_work_summary, extract_verified_file_work_answer,
    # extract_latest_stage_proposal_answer, _extract_latest_exact_file_read_path,
    # _extract_latest_file_lookup_brief, _sanitize_runtime_note, _build_outcome_block,
    # _extract_latest_stage_status, _extract_latest_runtime_note
    # → moved to SummaryEngine (core/engines/summary.py)

    @staticmethod
    def _render_persona_active_skill_block(skill: Dict[str, Any]) -> str:
        name = str(skill.get("name") or "").strip()
        persona_hint = str(skill.get("persona_hint") or "").strip()
        procedure = [str(item).strip() for item in (skill.get("procedure") or []) if str(item).strip()]
        if not name and not persona_hint and not procedure:
            return ""
        lines = ["[ACTIVE_SKILL]"]
        if name:
            lines.append(f"Skill: {name}")
        if procedure:
            lines.append("Procedure: " + " -> ".join(procedure))
        if persona_hint:
            lines.append(persona_hint)
        return "\n".join(lines)

    def _collect_runtime_context_paths(self, orc) -> List[str]:
        workspace_root = Path(getattr(getattr(orc, "brain", None), "workspace", CFG.DATA_DIR / "workspace")).resolve()
        blobs: list[str] = [str(getattr(orc, "user_msg", "") or "").strip()]
        card = dict(getattr(orc, "context_card", {}) or getattr(orc, "route_decision", {}).get("card") or {})
        blobs.append(str(card.get("goal") or "").strip())
        blobs.extend(str(item or "").strip() for item in (card.get("context") or []))
        for stage in card.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            blobs.append(str(stage.get("stage_goal") or "").strip())
            blobs.append(str(stage.get("success_condition") or "").strip())
        blobs.extend(str(entry or "") for entry in SummaryEngine.latest_stage_entries(getattr(orc, "scratchpad", []) or []))

        ordered: list[str] = []
        seen: set[str] = set()
        for blob in blobs:
            for match in _RUNTIME_CONTEXT_PATH_RE.findall(blob):
                normalized = self._normalize_runtime_context_path(str(match or ""), workspace_root)
                if not normalized:
                    continue
                lowered = normalized.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                ordered.append(normalized)
        return ordered

    @staticmethod
    def _normalize_runtime_context_path(raw_path: str, workspace_root: Path | None) -> str:
        candidate = str(raw_path or "").strip().strip("`\"'.,;:()[]{}")
        if not candidate:
            return ""
        normalized = candidate.replace("\\", "/").strip()
        if not normalized or normalized.endswith(":"):
            return ""

        resolved: Path | None = None
        windows_match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
        if windows_match:
            drive = windows_match.group(1).lower()
            suffix = windows_match.group(2)
            if os.name == "nt":
                windows_suffix = suffix.replace("/", "\\")
                resolved = Path(f"{drive.upper()}:\\{windows_suffix}")
            else:
                resolved = Path(f"/mnt/{drive}/{suffix}")
        elif normalized.startswith("/mnt/"):
            if os.name == "nt" and len(normalized) > 6:
                drive = normalized[5].upper()
                suffix = normalized[7:].replace("/", "\\")
                resolved = Path(f"{drive}:\\{suffix}")
            else:
                resolved = Path(normalized)
        else:
            resolved = (workspace_root / normalized).resolve() if workspace_root is not None else Path(normalized)

        if workspace_root is not None:
            try:
                canonical_workspace = Path(os.path.normcase(os.path.realpath(workspace_root)))
                canonical_candidate = Path(os.path.normcase(os.path.realpath(resolved)))
                rel = canonical_candidate.relative_to(canonical_workspace)
                if canonical_candidate.exists():
                    return rel.as_posix()
                return ""
            except Exception:
                return ""

        return normalized
