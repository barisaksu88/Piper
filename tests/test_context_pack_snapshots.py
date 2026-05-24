"""Snapshot-style tests for ContextPack output.

These tests lock current rendered output before any refactor of
``core/engines/context_pack.py``.  They require no LLM, no web search,
no threading, and no external services.
"""

from __future__ import annotations

import pytest

from core.engines.context_pack import (
    ContextPackEngine,
    ContextPackRenderer,
    resolve_persona_turn_type,
    render_context_arbitration_block,
)
from core.contracts import (
    PersonaContextPack,
    PersonaDirectivePack,
    PersonaRuntimePack,
    RuntimeContextPack,
)


# ── helpers ──────────────────────────────────────────────────────────

def _make_engine(**overrides) -> ContextPackEngine:
    """Return a ContextPackEngine backed by minimal deterministic stubs."""
    class _Stub:
        pass

    env = _Stub()
    env.render_block = lambda: overrides.get("env_block", "")

    ops = _Stub()
    ops.render_block = lambda query="": overrides.get("operational_state", "")

    knowledge = _Stub()
    knowledge.load = lambda: overrides.get("knowledge", {})
    knowledge.render_prompt_state = lambda user_msg: overrides.get("world_state", "")
    knowledge.render_situational_state = lambda user_msg: overrides.get("situational_state", "")

    brain = _Stub()
    brain.recall = lambda user_msg, n_results=5: list(overrides.get("brain_hits", []))

    docs = _Stub()
    docs.render_prompt_hits = lambda user_msg, limit=5: list(overrides.get("document_hits", []))

    instruction_loader = _Stub()
    instruction_loader.load = lambda: overrides.get("instructions", "")

    return ContextPackEngine(
        instruction_loader=instruction_loader,
        environment_service=env,
        operational_state_service=ops,
        knowledge_mgr=knowledge,
        brain=brain,
        document_memory=docs,
        vision_session_memory=None,
        transient_state_mgr=None,
        user_runtime=None,
    )


# ── A. ContextPackRenderer.render_runtime_context_message ────────────

class TestRenderRuntimeContextMessage:
    def test_empty_pack_returns_empty_string(self) -> None:
        renderer = ContextPackRenderer()
        assert renderer.render_runtime_context_message(RuntimeContextPack()) == ""

    def test_task_pack_includes_all_fields(self) -> None:
        renderer = ContextPackRenderer()
        pack = RuntimeContextPack(
            previous_route="TASK",
            previous_user_request="edit main.py",
            task_goal="Add logging to main.py",
            execution_status="FILE OPERATION SUCCESS",
            runtime_note="Updated main.py and verified.",
            relevant_paths=["main.py", "utils.py"],
        )
        msg = renderer.render_runtime_context_message(pack)
        assert msg.startswith("[LATEST_RUNTIME_CONTEXT]")
        assert "Previous route: TASK" in msg
        assert "Previous user request: edit main.py" in msg
        assert "Task goal: Add logging to main.py" in msg
        assert "Execution status: FILE OPERATION SUCCESS" in msg
        assert "Runtime note: Updated main.py and verified." in msg
        assert "Relevant paths: main.py | utils.py" in msg
        assert "Use this block as authoritative runtime context" in msg

    def test_search_reporter_pack(self) -> None:
        renderer = ContextPackRenderer()
        pack = RuntimeContextPack(
            previous_route="SEARCH",
            previous_user_request="weather in London",
            search_query="weather in London",
            reporter_just_ran=True,
        )
        msg = renderer.render_runtime_context_message(pack)
        assert "Search query: weather in London" in msg
        assert "Execution status: SEARCH COMPLETED" in msg
        assert "Runtime note: Search summary was prepared for the user." in msg
        assert "Task goal:" not in msg

    def test_failed_search_reporter_pack(self) -> None:
        renderer = ContextPackRenderer()
        pack = RuntimeContextPack(
            previous_route="SEARCH",
            previous_user_request="weather in London",
            search_query="weather in London",
            reporter_just_ran=True,
            search_failed=True,
            search_error="Zero results",
        )
        msg = renderer.render_runtime_context_message(pack)
        assert "Execution status: SEARCH FAILED" in msg
        assert "Zero results" in msg

    def test_reporter_ignores_task_goal_and_execution_status(self) -> None:
        renderer = ContextPackRenderer()
        pack = RuntimeContextPack(
            previous_route="SEARCH",
            reporter_just_ran=True,
            task_goal="Should not appear",
            execution_status="Should not appear",
            runtime_note="Should not appear",
        )
        msg = renderer.render_runtime_context_message(pack)
        assert "Task goal: Should not appear" not in msg
        assert "Execution status: Should not appear" not in msg
        assert "Runtime note: Should not appear" not in msg


# ── B. ContextPackEngine.build_persona_directive_pack ────────────────

class TestBuildPersonaDirectivePack:
    def _build(
        self,
        *,
        route_decision=None,
        ingested_document_chat=False,
        document_focus_active=False,
        reporter_just_ran=False,
        active_skill=None,
        persona_runtime=None,
    ) -> PersonaDirectivePack:
        engine = _make_engine()
        return engine.build_persona_directive_pack(
            route_decision=route_decision,
            ingested_document_chat=ingested_document_chat,
            document_focus_active=document_focus_active,
            reporter_just_ran=reporter_just_ran,
            active_skill=active_skill or {},
            persona_runtime=persona_runtime or PersonaRuntimePack(),
        )

    def test_chat_includes_no_mutation_rule_first(self) -> None:
        pack = self._build(
            route_decision={"decision": "CHAT"},
            persona_runtime=PersonaRuntimePack(),
        )
        blocks = pack.tail_system_blocks
        assert any("[NO_MUTATION_RULE]" in b for b in blocks)
        # NO_MUTATION_RULE should come before CONTEXT_ARBITRATION_RULE
        no_mut_idx = next(i for i, b in enumerate(blocks) if "[NO_MUTATION_RULE]" in b)
        arb_idx = next(i for i, b in enumerate(blocks) if "[CONTEXT_ARBITRATION_RULE]" in b)
        assert no_mut_idx < arb_idx

    def test_chat_does_not_include_file_work_rules(self) -> None:
        pack = self._build(
            route_decision={"decision": "CHAT"},
            persona_runtime=PersonaRuntimePack(),
        )
        blocks = "\n".join(pack.tail_system_blocks)
        assert "[FILE_WORK_REPORT_RULE]" not in blocks
        assert "[WORKSPACE_BOUNDARY_RULE]" not in blocks
        assert "[FAILED_VERIFICATION_RULE]" not in blocks
        assert "[PARTIAL_VERIFICATION_RULE]" not in blocks

    def test_context_arbitration_block_always_present(self) -> None:
        for decision in ["CHAT", "TASK", "SEARCH"]:
            pack = self._build(route_decision={"decision": decision})
            assert any("[CONTEXT_ARBITRATION_RULE]" in b for b in pack.tail_system_blocks)

    def test_document_qa_rule_when_ingested(self) -> None:
        pack = self._build(
            route_decision={"decision": "CHAT"},
            ingested_document_chat=True,
        )
        assert any("[DOCUMENT_QA_RULE]" in b for b in pack.tail_system_blocks)

    def test_no_document_qa_rule_when_not_ingested(self) -> None:
        pack = self._build(
            route_decision={"decision": "CHAT"},
            ingested_document_chat=False,
        )
        assert not any("[DOCUMENT_QA_RULE]" in b for b in pack.tail_system_blocks)

    def test_search_report_rule_for_reporter(self) -> None:
        pack = self._build(
            route_decision={"decision": "SEARCH"},
            reporter_just_ran=True,
        )
        assert any("[SEARCH_REPORT_RULE]" in b for b in pack.tail_system_blocks)

    def test_no_search_report_rule_when_not_reporter(self) -> None:
        pack = self._build(
            route_decision={"decision": "SEARCH"},
            reporter_just_ran=False,
        )
        assert not any("[SEARCH_REPORT_RULE]" in b for b in pack.tail_system_blocks)

    def test_active_skill_block_present_with_skill(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            active_skill={"name": "file_edit", "persona_hint": "Be precise."},
        )
        assert any("[ACTIVE_SKILL]" in b for b in pack.tail_system_blocks)
        skill_block = next(b for b in pack.tail_system_blocks if "[ACTIVE_SKILL]" in b)
        assert "file_edit" in skill_block
        assert "Be precise." in skill_block

    def test_active_skill_block_empty_without_skill(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            active_skill={},
        )
        assert not any("[ACTIVE_SKILL]" in b for b in pack.tail_system_blocks)

    def test_failed_verification_rule(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(
                outcome_failed=True,
                verification_verdict="FAILED",
                verification_evidence="Key not found",
                verification_checker_path="RULES",
            ),
        )
        assert any("[FAILED_VERIFICATION_RULE]" in b for b in pack.tail_system_blocks)
        block = next(b for b in pack.tail_system_blocks if "[FAILED_VERIFICATION_RULE]" in b)
        assert "Key not found" in block
        assert "Checker path: RULES" in block

    def test_failed_outcome_rule_without_typed_verdict(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(
                outcome_failed=True,
                outcome_block="=== STAGE 1 OUTCOME ===\nRESULT: FAILED",
                verification_verdict="",
            ),
        )
        assert any("[FAILED_OUTCOME_RULE]" in b for b in pack.tail_system_blocks)
        # When typed verdict exists, FAILED_OUTCOME_RULE should not appear
        pack2 = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(
                outcome_failed=True,
                outcome_block="=== STAGE 1 OUTCOME ===\nRESULT: FAILED",
                verification_verdict="FAILED",
            ),
        )
        assert not any("[FAILED_OUTCOME_RULE]" in b for b in pack2.tail_system_blocks)

    def test_partial_verification_rule(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(
                needs_file_work_report_rule=True,
                verification_verdict="PARTIAL",
                verification_checker_path="STATE_CHECK",
                verification_recommendation="RETRY",
                verification_evidence="Could not confirm",
            ),
        )
        assert any("[PARTIAL_VERIFICATION_RULE]" in b for b in pack.tail_system_blocks)
        block = next(b for b in pack.tail_system_blocks if "[PARTIAL_VERIFICATION_RULE]" in b)
        assert "Could not confirm" in block
        assert "STATE_CHECK" in block

    def test_file_work_report_and_workspace_boundary(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(
                needs_file_work_report_rule=True,
                verification_verdict="",
            ),
        )
        assert any("[FILE_WORK_REPORT_RULE]" in b for b in pack.tail_system_blocks)
        assert any("[WORKSPACE_BOUNDARY_RULE]" in b for b in pack.tail_system_blocks)

    def test_verification_result_block_when_verdict_present(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(
                verification_verdict="VERIFIED",
                verification_checker_path="RULES",
                verification_evidence="File change confirmed",
            ),
        )
        assert any("[VERIFICATION_RESULT]" in b for b in pack.tail_system_blocks)
        block = next(b for b in pack.tail_system_blocks if "[VERIFICATION_RESULT]" in b)
        assert "VERIFIED" in block
        assert "File change confirmed" in block

    def test_verification_result_block_absent_when_no_verdict(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(verification_verdict=""),
        )
        assert not any("[VERIFICATION_RESULT]" in b for b in pack.tail_system_blocks)

    def test_direct_answer_for_targeted_read(self) -> None:
        pack = self._build(
            route_decision={"decision": "TASK"},
            persona_runtime=PersonaRuntimePack(
                exact_file_read_answer="Hello world",
                latest_stage_is_targeted_read=True,
                latest_stage_requires_analysis_report=False,
            ),
        )
        assert pack.direct_answer == "Hello world"

    def test_direct_answer_for_paused_with_proposal(self) -> None:
        pack = self._build(
            route_decision={"decision": "CHAT"},
            persona_runtime=PersonaRuntimePack(
                outcome_paused=True,
                proposal_answer="Which file?",
            ),
        )
        assert pack.direct_answer == "Which file?"


# ── C. ContextPackEngine.build_persona_pack ──────────────────────────

class TestBuildPersonaPack:
    def test_minimal_empty_services(self) -> None:
        engine = _make_engine()
        pack = engine.build_persona_pack(user_msg="hello")
        assert pack.user_msg == "hello"
        assert pack.knowledge_enabled is True
        assert pack.instructions == ""
        assert pack.brain_hits == []
        assert pack.document_hits == []
        assert pack.world_state == ""
        assert pack.situational_state == ""
        assert pack.intent_state == ""
        assert pack.operational_state == ""
        assert pack.env_block == ""

    def test_with_knowledge_and_brain_hits(self) -> None:
        engine = _make_engine(
            knowledge={"key": "value"},
            world_state="[WORLD STATE]\nTest",
            situational_state="[SITUATIONAL]\nDebug",
            operational_state="[OPS]\nIdle",
            env_block="[ENV]\nLocal",
            brain_hits=[{"text": "hit1", "distance": 0.2}],
        )
        pack = engine.build_persona_pack(user_msg="test", brain_limit=5)
        assert pack.knowledge == {"key": "value"}
        assert pack.world_state == "[WORLD STATE]\nTest"
        assert pack.situational_state == "[SITUATIONAL]\nDebug"
        assert pack.operational_state == "[OPS]\nIdle"
        assert pack.env_block == "[ENV]\nLocal"
        assert len(pack.brain_hits) == 1
        assert pack.brain_hits[0]["text"] == "hit1"

    def test_filters_high_distance_brain_hits(self) -> None:
        engine = _make_engine(
            brain_hits=[
                {"text": "close", "distance": 0.18},
                {"text": "far", "distance": 0.55},
            ],
        )
        pack = engine.build_persona_pack(user_msg="test")
        assert len(pack.brain_hits) == 1
        assert pack.brain_hits[0]["text"] == "close"

    def test_document_hits_included(self) -> None:
        engine = _make_engine(
            document_hits=[{"content": "doc1"}],
        )
        pack = engine.build_persona_pack(user_msg="test")
        assert len(pack.document_hits) == 1
        assert pack.document_hits[0]["content"] == "doc1"

    def test_apply_document_focus(self) -> None:
        engine = _make_engine()
        base = engine.build_persona_pack(user_msg="test")
        focused = engine.apply_document_focus(
            base, focus_text="Focus text", references=["R1"], sources=["S1"]
        )
        assert focused.document_focus == "Focus text"
        assert focused.document_references == ["R1"]
        assert focused.document_sources == ["S1"]

    def test_clear_memory_for_file_work(self) -> None:
        engine = _make_engine(brain_hits=[{"text": "hit"}], document_hits=[{"content": "doc"}])
        base = engine.build_persona_pack(user_msg="test")
        cleared = engine.clear_memory_for_file_work(base)
        assert cleared.brain_hits == []
        assert cleared.document_hits == []

    def test_context_arbitration_suppresses_blocks(self) -> None:
        engine = _make_engine(
            world_state="world",
            situational_state="sit",
            operational_state="ops",
            env_block="env",
            brain_hits=[{"text": "hit"}],
            document_hits=[{"content": "doc"}],
        )
        pack = engine.build_persona_pack(user_msg="test")
        # REPORTER turn type only allows [SEARCH_REPORT_RULE], [SEARCH SUMMARY],
        # and [RETRIEVED MEMORY]; everything else is suppressed.
        reporter = engine.apply_context_arbitration(
            pack, route_decision={"decision": "SEARCH"}, reporter_just_ran=True
        )
        assert reporter.world_state == ""
        assert reporter.situational_state == ""
        assert reporter.intent_state == ""
        assert reporter.env_block == ""
        assert reporter.operational_state == ""
        assert reporter.document_hits == []
        # brain_hits are allowed (secondary: [RETRIEVED MEMORY])
        assert len(reporter.brain_hits) == 1

    def test_to_prompt_context_mapping(self) -> None:
        engine = _make_engine(knowledge={"a": "b"}, world_state="ws")
        pack = engine.build_persona_pack(user_msg="test")
        ctx = engine.to_prompt_context(pack)
        assert ctx.knowledge == {"a": "b"}
        assert ctx.world_state == "ws"


# ── D. resolve_persona_turn_type ─────────────────────────────────────

class TestResolvePersonaTurnType:
    def test_chat_default(self) -> None:
        assert resolve_persona_turn_type() == "CHAT"

    def test_task(self) -> None:
        assert resolve_persona_turn_type(route_decision={"decision": "TASK"}) == "TASK"

    def test_search_first_pass(self) -> None:
        assert resolve_persona_turn_type(route_decision={"decision": "SEARCH"}) == "SEARCH_FIRST_PASS"

    def test_reporter(self) -> None:
        assert resolve_persona_turn_type(route_decision={"decision": "SEARCH"}, reporter_just_ran=True) == "REPORTER"

    def test_proactive_trigger(self) -> None:
        assert (
            resolve_persona_turn_type(route_decision={"system_notice": {"kind": "proactive_trigger"}})
            == "PROACTIVE_TRIGGER"
        )

    def test_explain_last_turn(self) -> None:
        assert (
            resolve_persona_turn_type(route_decision={"system_notice": {"kind": "explain_last_turn"}})
            == "EXPLAIN"
        )

    def test_doc_focus(self) -> None:
        assert resolve_persona_turn_type(ingested_document_chat=True) == "DOC_FOCUS"


# ── E. render_context_arbitration_block ──────────────────────────────

class TestRenderContextArbitrationBlock:
    def test_contains_turn_type(self) -> None:
        block = render_context_arbitration_block("TASK")
        assert "Turn type: TASK" in block
        assert "[CONTEXT_ARBITRATION_RULE]" in block

    def test_reporter_has_reporter_text(self) -> None:
        block = render_context_arbitration_block("REPORTER")
        assert "completed-search follow-on turn" in block

    def test_search_first_pass_has_search_text(self) -> None:
        block = render_context_arbitration_block("SEARCH_FIRST_PASS")
        assert "web search is still running" in block
