"""Snapshot-style tests for ContextPack output.

These tests lock current rendered output before any refactor of
``core/engines/context_pack.py``.  They require no LLM, no web search,
no threading, and no external services.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.engines.context_pack import (
    ContextPackEngine,
    _hook_upsert_runtime_context,
)
from core.services.context_pack_paths import (
    collect_runtime_context_paths,
    normalize_runtime_context_path,
)
from core.services.context_pack_renderer import (
    ContextPackRenderer,
    resolve_persona_turn_type,
    render_context_arbitration_block,
)
import core.engines.proactive_monitor  # noqa: F401 — registers proactive tail blocks
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


# ── F. Proactive tail blocks ─────────────────────────────────────────


class TestProactiveTailBlocks:
    def _build(self, *, route_decision=None, persona_runtime=None):
        engine = _make_engine()
        return engine.build_persona_directive_pack(
            route_decision=route_decision,
            ingested_document_chat=False,
            document_focus_active=False,
            reporter_just_ran=False,
            active_skill={},
            persona_runtime=persona_runtime or PersonaRuntimePack(),
        )

    def test_proactive_trigger_block_present(self):
        pack = self._build(
            route_decision={
                "decision": "CHAT",
                "system_notice": {
                    "kind": "proactive_trigger",
                    "message": "test",
                    "fire_at_local": "now",
                },
            },
        )
        blocks = pack.tail_system_blocks
        block = next(b for b in blocks if b.startswith("[PROACTIVE_TRIGGER]"))
        assert "test" in block
        assert "now" in block
        # Ordering: proactive block comes after arbitration
        arb_idx = next(i for i, b in enumerate(blocks) if "[CONTEXT_ARBITRATION_RULE]" in b)
        pro_idx = next(i for i, b in enumerate(blocks) if b.startswith("[PROACTIVE_TRIGGER]"))
        assert arb_idx < pro_idx

    def test_reminder_set_result_scheduled_block_present(self):
        pack = self._build(
            route_decision={
                "decision": "CHAT",
                "system_notice": {
                    "kind": "reminder_set_result",
                    "status": "scheduled",
                    "message": "reminder",
                    "fire_at_local": "later",
                },
            },
        )
        blocks = pack.tail_system_blocks
        block = next(b for b in blocks if b.startswith("[REMINDER_SET_RESULT]"))
        assert "reminder" in block
        assert "later" in block

    def test_reminder_set_result_error_block_present(self):
        pack = self._build(
            route_decision={
                "decision": "CHAT",
                "system_notice": {
                    "kind": "reminder_set_result",
                    "status": "error",
                    "error": "bad time",
                },
            },
        )
        blocks = pack.tail_system_blocks
        block = next(b for b in blocks if b.startswith("[REMINDER_SET_RESULT]"))
        assert "bad time" in block


# ── G. Runtime context path helpers ──────────────────────────────────


class TestRuntimeContextPaths:
    def test_normalize_empty_and_invalid(self):
        assert normalize_runtime_context_path("", None) == ""
        assert normalize_runtime_context_path("   ", Path("/tmp")) == ""
        assert normalize_runtime_context_path("`", Path("/tmp")) == ""

    def test_normalize_relative_existing(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("x")
        result = normalize_runtime_context_path("app.py", tmp_path)
        assert result == "app.py"

    def test_normalize_relative_missing(self, tmp_path: Path):
        assert normalize_runtime_context_path("missing.py", tmp_path) == ""

    @pytest.mark.skipif(os.name != "nt", reason="Windows absolute path conversion")
    def test_normalize_absolute_inside_workspace(self, tmp_path: Path):
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "main.py").write_text("x")
        abs_path = str(tmp_path / "src" / "main.py")
        result = normalize_runtime_context_path(abs_path, tmp_path)
        assert result == "src/main.py"

    @pytest.mark.skipif(os.name != "nt", reason="WSL path conversion only on Windows")
    def test_normalize_wsl_inside_workspace(self, tmp_path: Path):
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "main.py").write_text("x")
        posix = tmp_path.as_posix()
        drive = tmp_path.drive[0].lower()
        wsl_path = posix.replace(f"{drive.upper()}:/", f"/mnt/{drive}/") + "/src/main.py"
        result = normalize_runtime_context_path(wsl_path, tmp_path)
        assert result == "src/main.py"

    def test_collect_deduplication_and_order(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("x")

        class _FakeBrain:
            workspace = tmp_path

        orc = type(
            "O",
            (),
            {
                "brain": _FakeBrain(),
                "user_msg": "look at a.py and b.py",
                "context_card": {},
                "route_decision": {
                    "card": {"goal": "check a.py", "context": ["see b.py"], "stages": []}
                },
                "scratchpad": [],
            },
        )()
        paths = collect_runtime_context_paths(orc)
        assert paths == ["a.py", "b.py"]

    def test_collect_scratchpad_path_extraction(self, tmp_path: Path):
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "main.py").write_text("x")

        class _FakeBrain:
            workspace = tmp_path

        orc = type(
            "O",
            (),
            {
                "brain": _FakeBrain(),
                "user_msg": "",
                "context_card": {},
                "route_decision": {},
                "scratchpad": [
                    "=== STAGE 1 START ===",
                    "OBSERVATION_TEXT: Read text file: src/main.py",
                    "=== STAGE 1 OUTCOME ===\nRESULT: SUCCESS",
                ],
            },
        )()
        paths = collect_runtime_context_paths(orc)
        assert "src/main.py" in paths

    def test_collect_ignores_duplicate_paths(self, tmp_path: Path):
        (tmp_path / "dup.py").write_text("x")

        class _FakeBrain:
            workspace = tmp_path

        orc = type(
            "O",
            (),
            {
                "brain": _FakeBrain(),
                "user_msg": "dup.py and DUP.py",
                "context_card": {},
                "route_decision": {},
                "scratchpad": [],
            },
        )()
        paths = collect_runtime_context_paths(orc)
        assert paths == ["dup.py"]

    def test_collect_user_msg_path_extraction(self, tmp_path: Path):
        (tmp_path / "user.py").write_text("x")

        class _FakeBrain:
            workspace = tmp_path

        orc = type(
            "O",
            (),
            {
                "brain": _FakeBrain(),
                "user_msg": "check user.py please",
                "context_card": {},
                "route_decision": {},
                "scratchpad": [],
            },
        )()
        paths = collect_runtime_context_paths(orc)
        assert paths == ["user.py"]

    def test_collect_context_card_stage_paths(self, tmp_path: Path):
        (tmp_path / "stage.py").write_text("x")

        class _FakeBrain:
            workspace = tmp_path

        orc = type(
            "O",
            (),
            {
                "brain": _FakeBrain(),
                "user_msg": "",
                "context_card": {},
                "route_decision": {
                    "card": {
                        "goal": "",
                        "context": [],
                        "stages": [
                            {
                                "stage_goal": "edit stage.py",
                                "success_condition": "stage.py is correct",
                            }
                        ],
                    }
                },
                "scratchpad": [],
            },
        )()
        paths = collect_runtime_context_paths(orc)
        assert "stage.py" in paths


# ── H. _hook_upsert_runtime_context direct-call path ─────────────────


class TestHookUpsertRuntimeContext:
    def _make_orc(self, payload="", route_decision=None):
        class _FakeChat:
            def __init__(self):
                self.messages: list[dict] = []

            def upsert_hidden_system_message(self, prefix, content):
                self.messages = [
                    m
                    for m in self.messages
                    if not (
                        m.get("role") == "system"
                        and str(m.get("content", "")).startswith(prefix)
                    )
                ]
                self.messages.append({"role": "system", "content": content, "hidden": True})

            def append_message(self, msg):
                self.messages.append(msg)

            def remove_hidden_system_message(self, prefix):
                self.messages = [
                    m
                    for m in self.messages
                    if not (
                        m.get("role") == "system"
                        and str(m.get("content", "")).startswith(prefix)
                    )
                ]

        class _FakePromptContext:
            def __init__(self, p):
                self._p = p

            def build_runtime_context_message(self, orc, *, reporter_just_ran=False):
                return self._p

        orc = type("O", (), {})()
        orc.prompt_context = _FakePromptContext(payload)
        orc.chat = _FakeChat()
        orc.route_decision = route_decision or {}
        return orc

    def test_inserts_hidden_message(self):
        orc = self._make_orc(payload="[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK")
        _hook_upsert_runtime_context(orc, reporter_just_ran=False)
        assert len(orc.chat.messages) == 1
        assert orc.chat.messages[0]["content"].startswith("[LATEST_RUNTIME_CONTEXT]")
        assert orc.chat.messages[0]["hidden"] is True

    def test_upsert_replaces_on_repeat(self):
        orc = self._make_orc(payload="[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK")
        _hook_upsert_runtime_context(orc, reporter_just_ran=False)
        orc.prompt_context._p = "[LATEST_RUNTIME_CONTEXT]\nPrevious route: SEARCH"
        _hook_upsert_runtime_context(orc, reporter_just_ran=False)
        assert len(orc.chat.messages) == 1
        assert "SEARCH" in orc.chat.messages[0]["content"]

    def test_reporter_just_ran_passed_through(self):
        class _FakePromptContext:
            def __init__(self):
                self.calls = []

            def build_runtime_context_message(self, orc, *, reporter_just_ran=False):
                self.calls.append(reporter_just_ran)
                return ""

        orc = type("O", (), {})()
        orc.prompt_context = _FakePromptContext()
        orc.chat = type(
            "C",
            (),
            {
                "upsert_hidden_system_message": lambda *a, **k: None,
                "append_message": lambda *a, **k: None,
                "remove_hidden_system_message": lambda *a, **k: None,
            },
        )()
        orc.route_decision = {}
        _hook_upsert_runtime_context(orc, reporter_just_ran=True)
        assert orc.prompt_context.calls == [True]

    def test_file_target_confirmation_cancelled_removes_message(self):
        orc = self._make_orc(payload="[LATEST_RUNTIME_CONTEXT]\nPrevious route: TASK")
        _hook_upsert_runtime_context(orc, reporter_just_ran=False)
        assert len(orc.chat.messages) == 1
        orc.route_decision = {"system_notice": {"kind": "file_target_confirmation_cancelled"}}
        _hook_upsert_runtime_context(orc, reporter_just_ran=False)
        assert len(orc.chat.messages) == 0


# ── I. build_runtime_context_pack reporter branches ──────────────────


class TestBuildRuntimeContextPack:
    def _make_engine(self):
        return _make_engine()

    def _make_orc(self, **kwargs):
        class _FakeBrain:
            workspace = Path(kwargs.get("workspace", "."))

        defaults = {
            "route_decision": {"decision": "SEARCH", "card": {"query": "weather"}},
            "context_card": {},
            "user_msg": "hello",
            "latest_search_query": "",
            "latest_search_failed": False,
            "latest_search_error": "",
            "scratchpad": [],
            "brain": _FakeBrain(),
        }
        defaults.update(kwargs)
        return type("O", (), defaults)()

    def test_reporter_just_ran_search_completed(self):
        engine = self._make_engine()
        orc = self._make_orc()
        pack = engine.build_runtime_context_pack(orc, reporter_just_ran=True)
        assert pack.previous_route == "SEARCH"
        assert pack.reporter_just_ran is True
        assert pack.search_failed is False
        assert pack.search_error == ""
        assert pack.task_goal == ""
        assert pack.execution_status == ""
        assert pack.runtime_note == ""

    def test_reporter_just_ran_search_failed_with_error(self):
        engine = self._make_engine()
        orc = self._make_orc(latest_search_failed=True, latest_search_error="Timeout")
        pack = engine.build_runtime_context_pack(orc, reporter_just_ran=True)
        assert pack.search_failed is True
        assert "Timeout" in pack.search_error

    def test_reporter_uses_latest_search_query(self):
        engine = self._make_engine()
        orc = self._make_orc(
            latest_search_query="custom query",
            route_decision={"decision": "SEARCH", "card": {}},
        )
        pack = engine.build_runtime_context_pack(orc, reporter_just_ran=True)
        assert pack.search_query == "custom query"

    def test_non_reporter_task_includes_goal_and_status(self):
        engine = self._make_engine()
        orc = self._make_orc(
            route_decision={"decision": "TASK", "card": {"goal": "fix bug"}},
            scratchpad=[
                "=== STAGE 1 START ===",
                "=== STAGE 1 OUTCOME ===\nRESULT: FILE OPERATION SUCCESS\nLAST_LOG: Done",
            ],
        )
        pack = engine.build_runtime_context_pack(orc, reporter_just_ran=False)
        assert pack.previous_route == "TASK"
        assert pack.task_goal == "fix bug"
        assert "FILE OPERATION SUCCESS" in pack.execution_status
        assert pack.runtime_note != ""


# ── J. apply_context_arbitration turn types ──────────────────────────


class TestApplyContextArbitrationTurnTypes:
    def _base_pack(self):
        return PersonaContextPack(
            world_state="ws",
            situational_state="ss",
            intent_state="is",
            operational_state="os",
            env_block="env",
            brain_hits=[{"t": 1}],
            document_hits=[{"d": 1}],
            document_focus="df",
            document_references=["r1"],
            document_sources=["s1"],
        )

    def test_task(self):
        engine = _make_engine()
        pack = engine.apply_context_arbitration(self._base_pack(), route_decision={"decision": "TASK"})
        assert pack.operational_state == "os"
        assert pack.world_state == "ws"
        assert pack.brain_hits == [{"t": 1}]
        assert pack.env_block == ""
        assert pack.situational_state == ""
        assert pack.intent_state == ""
        assert pack.document_hits == []
        assert pack.document_focus == ""

    def test_search_first_pass(self):
        engine = _make_engine()
        pack = engine.apply_context_arbitration(self._base_pack(), route_decision={"decision": "SEARCH"})
        assert pack.env_block == "env"
        assert pack.world_state == "ws"
        assert pack.brain_hits == [{"t": 1}]
        assert pack.operational_state == ""
        assert pack.situational_state == ""
        assert pack.intent_state == ""
        assert pack.document_hits == []

    def test_doc_focus(self):
        engine = _make_engine()
        pack = engine.apply_context_arbitration(
            self._base_pack(),
            route_decision={"decision": "CHAT"},
            ingested_document_chat=True,
            document_focus_active=True,
        )
        assert pack.document_focus == "df"
        assert pack.document_references == ["r1"]
        assert pack.document_sources == ["s1"]
        assert pack.intent_state == "is"
        assert pack.brain_hits == [{"t": 1}]
        assert pack.env_block == ""
        assert pack.world_state == ""
        assert pack.situational_state == ""
        assert pack.operational_state == ""
        assert pack.document_hits == []

    def test_proactive_trigger(self):
        engine = _make_engine()
        pack = engine.apply_context_arbitration(
            self._base_pack(),
            route_decision={"decision": "CHAT", "system_notice": {"kind": "proactive_trigger"}},
        )
        assert pack.operational_state == "os"
        assert pack.env_block == ""
        assert pack.world_state == ""
        assert pack.situational_state == ""
        assert pack.intent_state == ""
        assert pack.brain_hits == []
        assert pack.document_hits == []

    def test_explain(self):
        engine = _make_engine()
        pack = engine.apply_context_arbitration(
            self._base_pack(),
            route_decision={"decision": "CHAT", "system_notice": {"kind": "explain_last_turn"}},
        )
        assert pack.env_block == ""
        assert pack.world_state == ""
        assert pack.situational_state == ""
        assert pack.intent_state == ""
        assert pack.operational_state == ""
        assert pack.brain_hits == []
        assert pack.document_hits == []
        assert pack.document_focus == ""
