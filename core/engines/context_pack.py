from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List

from config import CFG
from core.contracts import (
    PERSONA_CONTEXT_ARBITRATION_TABLE,
    PersonaArbitrationProfile,
    PersonaContextPack,
    PersonaDirectivePack,
    PersonaRuntimePack,
    PersonaTurnType,
    PromptContext,
    RuntimeContextPack,
)
from core.engines.summary import SummaryEngine
from core.engines.verification import VerificationResult
from core.feature_hooks import register_hook
from core.file_stage_policy import FileStagePolicy
from core.turn_explanation import render_explain_last_turn_block

_LATEST_RUNTIME_CONTEXT_PREFIX = "[LATEST_RUNTIME_CONTEXT]"
_RUNTIME_CONTEXT_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/][^\s`\"'<>|]+|/mnt/[a-z]/[^\s`\"'<>|]+|[\w./\\-]+\.[A-Za-z0-9]{1,8})"
)
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
    user_runtime: Any | None = None
    renderer: ContextPackRenderer = field(default_factory=ContextPackRenderer)

    def build_persona_pack(
        self,
        *,
        user_msg: str,
        style_overlay: str = "",
        knowledge_enabled: bool = True,
        brain_limit: int = 9,
        document_limit: int = 5,
    ) -> PersonaContextPack:
        instructions = self.instruction_loader.load()
        active_user_block = ""
        if self.user_runtime is not None and hasattr(self.user_runtime, "render_active_user_block"):
            try:
                active_user_block = str(self.user_runtime.render_active_user_block() or "").strip()
            except Exception:
                active_user_block = ""
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

        # Strip memories that assert a specific calendar date ("today is X") when
        # they are older than 1 day.  These become actively misleading once stale —
        # the model may trust them over the live [ENVIRONMENT] block.
        import datetime as _dt, re as _re
        _DATE_CLAIM_RE = _re.compile(
            r"\btoday is\b|\bcurrent(?:ly)?[,\s]+(?:the\s+)?date|\bthe date is\b",
            _re.IGNORECASE,
        )
        _today = _dt.date.today()

        def _is_stale_date_claim(hit: Dict[str, Any]) -> bool:
            text = str(hit.get("text") or "")
            if not _DATE_CLAIM_RE.search(text):
                return False
            meta_date = str((hit.get("metadata") or {}).get("date") or "").strip()
            if not meta_date:
                return False
            try:
                mem_date = _dt.datetime.strptime(meta_date, "%b %d, %Y").date()
                return (_today - mem_date).days > 1
            except ValueError:
                return False

        def _brain_hit_relevant(hit: Dict[str, Any]) -> bool:
            raw_distance = hit.get("distance")
            if raw_distance is None:
                return True
            try:
                return float(raw_distance) < 0.40
            except (TypeError, ValueError):
                return True

        brain_hits = [
            h for h in brain_hits
            if _brain_hit_relevant(h) and not _is_stale_date_claim(h)
        ]

        vision_notes: List[str] = []
        if knowledge_enabled and self.vision_session_memory is not None and self.vision_session_memory.is_active():
            vision_notes = self.vision_session_memory.recent_notes(limit=5)

        document_hits: List[Dict[str, Any]] = []
        if knowledge_enabled and int(document_limit) > 0:
            try:
                raw_hits = self.document_memory.render_prompt_hits(
                    user_msg,
                    limit=max(int(document_limit), 0),
                )
                # Filter out low-relevance hits.
                # Threshold 0.35: cosine distance ≥ 0.35 means the query has no
                # meaningful overlap with the document chunk.  0.45 was too loose —
                # unrelated queries (e.g. "check if file exists") were pulling in
                # FCOM chunks that happen to contain generic terms.
                # Exact/mock hits have no distance field and always pass.
                def _hit_relevant(h: Dict[str, Any]) -> bool:
                    raw = h.get("distance")
                    if raw is None:
                        return True  # no distance → treat as relevant (exact/mock)
                    return float(raw) < 0.35

                document_hits = [h for h in raw_hits if _hit_relevant(h)]
            except Exception:
                document_hits = []

        return PersonaContextPack(
            user_msg=str(user_msg or ""),
            knowledge_enabled=bool(knowledge_enabled),
            instructions=instructions,
            style_overlay=style_overlay or "",
            active_user_block=active_user_block,
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

    def apply_context_arbitration(
        self,
        pack: PersonaContextPack,
        *,
        route_decision: Dict[str, Any] | None = None,
        ingested_document_chat: bool = False,
        reporter_just_ran: bool = False,
        document_focus_active: bool = False,
    ) -> PersonaContextPack:
        turn_type = resolve_persona_turn_type(
            route_decision=route_decision,
            reporter_just_ran=reporter_just_ran,
            ingested_document_chat=ingested_document_chat,
            document_focus_active=document_focus_active,
        )
        profile = PERSONA_CONTEXT_ARBITRATION_TABLE.get(turn_type, PersonaArbitrationProfile())
        allowed_blocks = set(profile.primary) | set(profile.secondary)
        updates: dict[str, Any] = {}
        for block_name, field_names in _PACK_BLOCK_FIELD_MAP.items():
            if block_name in allowed_blocks:
                continue
            for field_name in field_names:
                updates[field_name] = _clear_pack_field_value(field_name)
        if turn_type == "REPORTER":
            # Reporter tone should stay neutral and grounded regardless of the
            # currently selected persona style.
            updates["style_overlay"] = ""
        if not updates:
            return pack
        return replace(pack, **updates)

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
            search_failed=bool(getattr(orc, "latest_search_failed", False)) if reporter_just_ran else False,
            search_error=str(getattr(orc, "latest_search_error", "") or "") if reporter_just_ran else "",
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
        outcome_pack: Any | None = None,
    ) -> PersonaRuntimePack:
        stage = dict(latest_stage or {})
        allow_persona_reroute = bool(getattr(outcome_pack, "allow_persona_reroute", True))
        outcome_block = SummaryEngine.build_outcome_block(
            scratchpad,
            escalation_active=escalation_active,
            allow_persona_reroute=allow_persona_reroute,
        )
        outcome_upper = outcome_block.upper()
        # Primary source: typed VerificationResult from VerificationEngine.
        # Fallback: infer from scratchpad text (for stages where verification
        # was not run, e.g. CHAT, MEMORY_WORK).
        verification_verdict = ""
        verification_evidence = ""
        verification_recommendation = ""
        verification_checker_path = ""
        if verification_result is not None:
            verification_verdict = str(verification_result.verdict or "")
            verification_evidence = str(verification_result.evidence_summary or "")
            verification_recommendation = str(verification_result.recommendation or "")
            verification_checker_path = str(verification_result.checker_path or "")
            # Typed verification is authoritative for task-turn success/failure.
            outcome_failed = not bool(getattr(verification_result, "effective_success", False))
        else:
            stage_status_upper = str(SummaryEngine.extract_stage_status(scratchpad) or "").strip().upper()
            outcome_failed = (
                stage_status_upper in {"FAILED / INCOMPLETE", "TIMEOUT", "ACTION BUDGET EXHAUSTED"}
                or "RESULT: FAILED" in outcome_upper
            )
        outcome_paused = "PAUSED / AWAITING USER" in outcome_upper
        proposal_answer = SummaryEngine.extract_proposal(scratchpad)
        exact_file_read_answer = SummaryEngine.extract_exact_file_read(scratchpad)
        file_lookup_answer = SummaryEngine.extract_file_lookup(scratchpad)
        verified_file_work_answer = SummaryEngine.extract_verified_result(scratchpad)
        verified_browser_answer = SummaryEngine.extract_verified_browser_answer(scratchpad)
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
            allow_persona_reroute=allow_persona_reroute,
            proposal_answer=proposal_answer,
            analysis_report_answer=analysis_report_answer,
            exact_file_read_answer=exact_file_read_answer,
            file_lookup_answer=file_lookup_answer,
            verified_file_work_answer=verified_file_work_answer,
            verified_browser_answer=verified_browser_answer,
            latest_stage_requires_analysis_report=latest_stage_requires_analysis_report,
            latest_stage_is_targeted_read=latest_stage_is_targeted_read,
            latest_stage_is_targeted_lookup=latest_stage_is_targeted_lookup,
            needs_file_work_report_rule=needs_file_work_report_rule,
            verification_verdict=verification_verdict,
            verification_evidence=verification_evidence,
            verification_recommendation=verification_recommendation,
            verification_checker_path=verification_checker_path,
        )

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

    @staticmethod
    def _render_verification_result_block(runtime: PersonaRuntimePack) -> str:
        verdict = str(runtime.verification_verdict or "").strip().upper()
        if not verdict:
            return ""
        lines = ["[VERIFICATION_RESULT]"]
        lines.append(f"Verdict: {verdict}")
        if runtime.verification_checker_path:
            lines.append(f"Checker path: {runtime.verification_checker_path}")
        if runtime.verification_recommendation:
            lines.append(f"Recommendation: {runtime.verification_recommendation}")
        if runtime.verification_evidence:
            lines.append(f"Evidence: {runtime.verification_evidence}")
        lines.append("Treat this block as the authoritative verification outcome for the latest stage.")
        if verdict != "VERIFIED":
            lines.append("Do not narrate full success unless this block says VERIFIED.")
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
        "This is a background inference handoff, not a personality moment.\n"
        "If the user already received an initial response while the search was running, use the search summary or search failure note to extend, refine, or correct that earlier response.\n"
        "If there was no earlier preview reply, treat this as the only user-facing answer for the search turn.\n"
        "Do not restart from scratch or repeat unchanged context when the search findings simply confirm it.\n"
        "Answer directly from the search summary.\n"
        "Keep the tone neutral, calm, and matter-of-fact.\n"
        "Do not tease, scold, mock, challenge, flirt, or editorialize.\n"
        "Do not add attitude from the selected persona style.\n"
        "Prefer short factual wording over banter, sarcasm, or performative commentary.\n"
        "Do not say you are still searching, still scanning, still loading, waiting for results, or that more details are still on the way.\n"
        "Do not say 'while it loads' or preserve any provisional first-pass phrasing from before the search finished.\n"
        "Do not ask identity questions, speaker questions, or side-channel runtime questions on this turn.\n"
        "Do not narrate the search as ongoing. The search is already finished on this turn.\n"
        "If the search summary says the evidence was insufficient or the verdict is NOT_VERIFIED, tell the user honestly that you could not verify the answer. Do not guess.\n"
        "Do not append [ROUTER] unless the user asked for a brand-new action beyond this finished search attempt."
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
    return ContextPackEngine._render_persona_active_skill_block(ctx.skill)


@register_tail_block
def _tail_block_verification_result(ctx: TailBlockContext) -> str:
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
