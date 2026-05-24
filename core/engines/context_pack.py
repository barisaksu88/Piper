from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List

from config import CFG
from core.contracts import (
    PERSONA_CONTEXT_ARBITRATION_TABLE,
    PersonaArbitrationProfile,
    PersonaContextPack,
    PersonaDirectivePack,
    PersonaRuntimePack,
    PromptContext,
    RuntimeContextPack,
)
from core.services.context_pack_renderer import (
    ContextPackRenderer,
    _LATEST_RUNTIME_CONTEXT_PREFIX,
    _PACK_BLOCK_FIELD_MAP,
    _clear_pack_field_value,
    render_context_arbitration_block,
    resolve_persona_turn_type,
)
from core.services.summary import SummaryEngine
from core.services.verification import VerificationResult
from core.feature_hooks import register_hook
from core.file_stage_policy import FileStagePolicy
from core.engines.tail_block_registry import (
    TailBlockContext,
    _TAIL_BLOCK_REGISTRY,
)

_RUNTIME_CONTEXT_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/][^\s`\"'<>|]+|/mnt/[a-z]/[^\s`\"'<>|]+|[\w./\\-]+\.[A-Za-z0-9]{1,8})"
)


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
    # → moved to SummaryEngine (core/services/summary.py)

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
