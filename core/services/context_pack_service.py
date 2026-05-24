"""core/services/context_pack_service.py

Pure direct-call service for persona context pack construction,
runtime pack building, and context arbitration.

No lifecycle hooks, no registries, no engine dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List

from core.contracts import (
    PERSONA_CONTEXT_ARBITRATION_TABLE,
    PersonaArbitrationProfile,
    PersonaContextPack,
    PersonaRuntimePack,
    PromptContext,
    RuntimeContextPack,
)
from core.file_stage_policy import FileStagePolicy
from core.services.context_pack_paths import collect_runtime_context_paths
from core.services.context_pack_renderer import (
    ContextPackRenderer,
    _PACK_BLOCK_FIELD_MAP,
    _clear_pack_field_value,
    resolve_persona_turn_type,
)
from core.services.summary import SummaryEngine
from core.services.verification import VerificationResult


@dataclass
class ContextPackService:
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
        previous_route = "SEARCH" if reporter_just_ran else decision
        search_query = str(
            getattr(orc, "latest_search_query", "")
            or card.get("query")
            or (user_msg if decision == "SEARCH" else "")
        ).strip()
        return RuntimeContextPack(
            previous_route=previous_route,
            previous_user_request=user_msg,
            task_goal="" if reporter_just_ran else str(card.get("goal") or "").strip(),
            search_query=search_query if (previous_route == "SEARCH" or reporter_just_ran) else "",
            execution_status="" if reporter_just_ran else SummaryEngine.extract_stage_status(getattr(orc, "scratchpad", []) or []),
            runtime_note="" if reporter_just_ran else SummaryEngine.build_runtime_note(getattr(orc, "scratchpad", []) or []),
            relevant_paths=collect_runtime_context_paths(orc),
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
