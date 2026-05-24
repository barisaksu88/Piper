from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List

from core.engines.context_pack import ContextPackEngine
from core.feature_hooks import register_hook
from core.engines.state_mutation import StateMutationEngine
from core.engines.verification import VerificationResult
from core.contracts import (
    PersonaContextPack,
    PersonaDirectivePack,
    PersonaRuntimePack,
    PromptContext,
    RuntimeContextPack,
)

if TYPE_CHECKING:
    from memory.brain import PiperBrain
    from memory.documents import DocumentMemoryManager
    from memory.vision_session import VisionSessionMemory


@dataclass
class PromptContextService:
    instruction_loader: Any
    environment_service: Any
    operational_state_service: Any
    knowledge_mgr: Any
    brain: PiperBrain
    document_memory: DocumentMemoryManager
    vision_session_memory: VisionSessionMemory | None = None
    transient_state_mgr: Any | None = None
    user_runtime: Any | None = None
    engine: ContextPackEngine = field(init=False)
    state_mutation_engine: StateMutationEngine = field(init=False)

    def __post_init__(self) -> None:
        self.engine = ContextPackEngine(
            instruction_loader=self.instruction_loader,
            environment_service=self.environment_service,
            operational_state_service=self.operational_state_service,
            knowledge_mgr=self.knowledge_mgr,
            transient_state_mgr=self.transient_state_mgr,
            brain=self.brain,
            document_memory=self.document_memory,
            vision_session_memory=self.vision_session_memory,
            user_runtime=self.user_runtime,
        )
        self.state_mutation_engine = StateMutationEngine()

    def build_persona_pack(
        self,
        *,
        user_msg: str,
        style_overlay: str = "",
        knowledge_enabled: bool = True,
        brain_limit: int = 9,
        document_limit: int = 5,
    ) -> PersonaContextPack:
        return self.engine.build_persona_pack(
            user_msg=user_msg,
            style_overlay=style_overlay,
            knowledge_enabled=knowledge_enabled,
            brain_limit=brain_limit,
            document_limit=document_limit,
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
        return self.engine.apply_document_focus(
            pack,
            focus_text=focus_text,
            references=references,
            sources=sources,
            clear_document_hits=clear_document_hits,
        )

    def clear_memory_for_file_work(self, pack: PersonaContextPack) -> PersonaContextPack:
        return self.engine.clear_memory_for_file_work(pack)

    def apply_context_arbitration(
        self,
        pack: PersonaContextPack,
        *,
        route_decision: dict[str, Any] | None = None,
        ingested_document_chat: bool = False,
        reporter_just_ran: bool = False,
        document_focus_active: bool = False,
    ) -> PersonaContextPack:
        return self.engine.apply_context_arbitration(
            pack,
            route_decision=route_decision,
            ingested_document_chat=ingested_document_chat,
            reporter_just_ran=reporter_just_ran,
            document_focus_active=document_focus_active,
        )

    def to_prompt_context(self, pack: PersonaContextPack) -> PromptContext:
        return self.engine.to_prompt_context(pack)

    def build_persona_context(
        self,
        *,
        user_msg: str,
        style_overlay: str = "",
        knowledge_enabled: bool = True,
        brain_limit: int = 9,
        document_limit: int = 5,
    ) -> PromptContext:
        pack = self.build_persona_pack(
            user_msg=user_msg,
            style_overlay=style_overlay,
            knowledge_enabled=knowledge_enabled,
            brain_limit=brain_limit,
            document_limit=document_limit,
        )
        return self.to_prompt_context(pack)

    def build_runtime_context_pack(self, orc, *, reporter_just_ran: bool = False) -> RuntimeContextPack:
        return self.engine.build_runtime_context_pack(orc, reporter_just_ran=reporter_just_ran)

    def render_runtime_context_message(self, pack: RuntimeContextPack) -> str:
        return self.engine.render_runtime_context_message(pack)

    def build_runtime_context_message(self, orc, *, reporter_just_ran: bool = False) -> str:
        pack = self.build_runtime_context_pack(orc, reporter_just_ran=reporter_just_ran)
        return self.render_runtime_context_message(pack)

    def build_persona_runtime_pack(
        self,
        scratchpad: list[str],
        *,
        latest_stage: dict[str, Any] | None = None,
        reporter_just_ran: bool = False,
        escalation_active: bool = False,
        verification_result: VerificationResult | None = None,
        outcome_pack: Any | None = None,
    ) -> PersonaRuntimePack:
        return self.engine.build_persona_runtime_pack(
            scratchpad,
            latest_stage=latest_stage,
            reporter_just_ran=reporter_just_ran,
            escalation_active=escalation_active,
            verification_result=verification_result,
            outcome_pack=outcome_pack,
        )

    def build_persona_directive_pack(
        self,
        *,
        route_decision: dict[str, Any] | None = None,
        ingested_document_chat: bool = False,
        document_focus_active: bool = False,
        reporter_just_ran: bool = False,
        active_skill: dict[str, Any] | None = None,
        persona_runtime: PersonaRuntimePack | None = None,
    ) -> PersonaDirectivePack:
        return self.engine.build_persona_directive_pack(
            route_decision=route_decision,
            ingested_document_chat=ingested_document_chat,
            document_focus_active=document_focus_active,
            reporter_just_ran=reporter_just_ran,
            active_skill=active_skill,
            persona_runtime=persona_runtime,
        )

    def build_readonly_state_answer(self, query: str) -> str:
        # Knowledge queries are intentionally excluded — the hardcoded subject
        # extraction and "Your {subject} is {value}." template produce unnatural
        # output ("Your which drink i like is coke.").  The persona already has
        # [WORLD STATE] in context and will answer knowledge questions naturally.
        # Only operational state queries (tasks / events) use the fast-path.
        pack = self.state_mutation_engine.build_readonly_answer(
            query=query,
            knowledge_mgr=self.knowledge_mgr,
            operational_state_service=self.operational_state_service,
        )
        if pack.query_kind == "knowledge":
            return ""
        return pack.answer

    def build_readonly_knowledge_answer(self, query: str) -> str:
        pack = self.state_mutation_engine.build_readonly_answer(
            query=query,
            knowledge_mgr=self.knowledge_mgr,
            operational_state_service=self.operational_state_service,
        )
        return pack.answer if pack.query_kind == "knowledge" else ""

    def record_user_turn(self, user_msg: str) -> None:
        if self.transient_state_mgr is None:
            return
        self.transient_state_mgr.ingest_user_turn(user_msg)


@register_hook("on_pre_route")
def _hook_record_user_turn_once(orc, *, recent_history: list[dict[str, Any]] | None = None) -> None:
    del recent_history
    if bool(getattr(orc, "synthetic_user_turn", False)):
        return
    msg_to_ingest = str(orc.user_msg or "").strip()
    if not msg_to_ingest or getattr(orc, "_last_ingested_user_msg", None) == msg_to_ingest:
        return
    try:
        orc.prompt_context.record_user_turn(msg_to_ingest)
        orc._last_ingested_user_msg = msg_to_ingest
    except Exception:
        pass
