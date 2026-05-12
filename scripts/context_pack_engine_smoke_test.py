from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.prompt_context import PromptContextService  # noqa: E402
from core.instructions_loader import InstructionLoader  # noqa: E402
from core.engines.verification import VerificationResult  # noqa: E402


class _DummyEnv:
    def render_block(self) -> str:
        return "[ENVIRONMENT]\nmode=smoke"


class _DummyOps:
    def render_block(self, query: str = "") -> str:
        return f"[OPERATIONAL STATE]\nquery={query}"


class _DummyKnowledge:
    def load(self):
        return {"profile": "active"}

    def render_prompt_state(self, user_msg: str) -> str:
        return "[WORLD STATE]\nPiper is in task mode."


class _DummyBrain:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or (ROOT_DIR / "data" / "workspace")
        self.calls: list[dict[str, int | str]] = []

    def recall(self, user_msg: str, n_results: int = 5):
        self.calls.append({"query": user_msg, "n_results": n_results})
        return [
            {
                "text": "remember the grocery list flow",
                "metadata": {"date": "Mar 10, 2026"},
                "distance": 0.18,
            },
            {
                "text": "irrelevant grocery drift",
                "metadata": {"date": "Mar 10, 2026"},
                "distance": 0.55,
            },
        ]


class _DummyDocs:
    def render_prompt_hits(self, user_msg: str, limit: int = 5):
        return [{"content": "Page 2 discusses grocery_list.txt", "metadata": {"name": "manual.pdf", "page_number": 2}}]


class _DummyVision:
    def is_active(self) -> bool:
        return True

    def recent_notes(self, limit: int = 5):
        return ["Something looks slightly off."]


class _DummyTransient:
    def render_situational_state(self, user_msg: str) -> str:
        return "[SITUATIONAL STATE]\nUser is debugging Piper."

    def render_intent_state(self, user_msg: str) -> str:
        return "[INTENT STATE]\n- Tentative: create a fuzzy words code"

    def ingest_user_turn(self, user_msg: str) -> None:
        return None


class _DummyOrchestrator:
    def __init__(self, brain: "_DummyBrain | None" = None) -> None:
        self.user_msg = "Remove bread from grocery_list.txt and read it back."
        self.route_decision = {
            "decision": "TASK",
            "card": {
                "goal": "Remove bread from grocery_list.txt and read it back.",
                "context": ["The target file is grocery_list.txt."],
                "stages": [
                    {
                        "stage_goal": "Edit grocery_list.txt to remove bread.",
                        "success_condition": "grocery_list.txt no longer contains bread.",
                    }
                ],
            },
        }
        self.context_card = {}
        self.scratchpad = [
            "=== STAGE 1 START ===",
            "OBSERVATION_TEXT: Read text file: grocery_list.txt",
            "=== STAGE 1 OUTCOME ===\nRESULT: FILE OPERATION SUCCESS\nLAST_LOG: Removed bread from grocery_list.txt.",
        ]
        self.brain = brain if brain is not None else _DummyBrain()


def main() -> int:
    # Create a self-contained temp workspace so path-normalisation tests don't
    # depend on grocery_list.txt existing in the live data/workspace/ directory.
    with tempfile.TemporaryDirectory(prefix="piper-cpe-smoke-") as _tmpdir:
        tmp_workspace = Path(_tmpdir)
        (tmp_workspace / "grocery_list.txt").write_text("Bread\nMilk\nApples\n")
        return _run(tmp_workspace)


def _run(tmp_workspace: Path) -> int:
    brain = _DummyBrain(workspace=tmp_workspace)
    service = PromptContextService(
        instruction_loader=InstructionLoader(ROOT_DIR / "data" / "prompts" / "instructions.txt"),
        environment_service=_DummyEnv(),
        operational_state_service=_DummyOps(),
        knowledge_mgr=_DummyKnowledge(),
        transient_state_mgr=_DummyTransient(),
        brain=brain,
        document_memory=_DummyDocs(),
        vision_session_memory=_DummyVision(),
    )

    base_pack = service.build_persona_pack(
        user_msg="Tell me about the grocery list.",
        style_overlay="[STYLE]\nKeep it crisp.",
        knowledge_enabled=True,
    )
    focused_pack = service.apply_document_focus(
        base_pack,
        focus_text="The grocery list was updated.",
        references=["Page 2"],
        sources=["manual.pdf"],
    )
    search_first_pass_pack = service.apply_context_arbitration(
        base_pack,
        route_decision={"decision": "SEARCH"},
    )
    reporter_pack = service.apply_context_arbitration(
        base_pack,
        route_decision={"decision": "SEARCH"},
        reporter_just_ran=True,
    )
    doc_focus_pack = service.apply_context_arbitration(
        focused_pack,
        route_decision={"decision": "CHAT"},
        ingested_document_chat=True,
        document_focus_active=True,
    )
    explain_pack = service.apply_context_arbitration(
        base_pack,
        route_decision={"decision": "CHAT", "system_notice": {"kind": "explain_last_turn"}},
    )
    proactive_pack = service.apply_context_arbitration(
        base_pack,
        route_decision={"decision": "CHAT", "system_notice": {"kind": "proactive_trigger"}},
    )
    file_work_pack = service.clear_memory_for_file_work(base_pack)
    prompt_context = service.to_prompt_context(focused_pack)
    persona_runtime = service.build_persona_runtime_pack(
        _DummyOrchestrator(brain=brain).scratchpad,
        latest_stage={
            "stage_goal": "Edit grocery_list.txt to remove bread.",
            "stage_type": "FILE_WORK",
            "success_condition": "grocery_list.txt no longer contains bread.",
        },
        reporter_just_ran=False,
    )
    targeted_read_runtime = service.build_persona_runtime_pack(
        [
            "=== STAGE 1 START ===",
            "FILE_READ_EXACT_PATH: grocery_list.txt\nFILE_READ_EXACT_CONTENT:\nApples\nBananas\n",
            "=== STAGE 1 OUTCOME ===\nRESULT: FILE OPERATION SUCCESS\nLAST_LOG: Read text file: grocery_list.txt",
        ],
        latest_stage={
            "stage_goal": "Open and read the exact contents of grocery_list.txt.",
            "stage_type": "FILE_WORK",
            "success_condition": "The full text content of grocery_list.txt is retrieved and ready to present.",
        },
        reporter_just_ran=False,
    )
    paused_runtime = service.build_persona_runtime_pack(
        [
            "=== STAGE 1 START ===",
            "STAGE_GOAL: Ask the user: Which specific fact from your memory would you like me to remove?",
            "PROPOSAL: Which specific fact from your memory would you like me to remove?",
            "=== STAGE 1 OUTCOME ===\nRESULT: PAUSED / AWAITING USER INPUT\nLAST_LOG: PROPOSAL: Which specific fact from your memory would you like me to remove?",
        ],
        latest_stage={
            "stage_goal": "Ask the user: Which specific fact from your memory would you like me to remove?",
            "stage_type": "CHAT",
            "success_condition": "A concise clarification question is ready for the user.",
        },
        reporter_just_ran=False,
    )
    partial_runtime = service.build_persona_runtime_pack(
        [
            "=== STAGE 1 START ===",
            "STAGE_GOAL: Edit grocery_list.txt to remove bread.",
            "=== STAGE 1 OUTCOME ===\nRESULT: FAILED / INCOMPLETE\nLAST_LOG: Could not confirm the final artifact state.",
        ],
        latest_stage={
            "stage_goal": "Edit grocery_list.txt to remove bread.",
            "stage_type": "FILE_WORK",
            "success_condition": "grocery_list.txt no longer contains bread.",
        },
        reporter_just_ran=False,
        verification_result=VerificationResult.partial(
            "Current state could not confirm the final artifact.",
            retry_budget=1,
            checker_path="STATE_CHECK",
        ),
    )
    failed_mutation_runtime = service.build_persona_runtime_pack(
        [
            "=== STAGE 1 START ===",
            "STAGE_GOAL: Remove the durable fact favorite drink from memory.",
            "=== STAGE 1 OUTCOME ===\nRESULT: FAILED / INCOMPLETE\nLAST_LOG: Key not found: favorite drink",
        ],
        latest_stage={
            "stage_goal": "Remove the durable fact favorite drink from memory.",
            "stage_type": "MEMORY_WORK",
            "success_condition": "The favorite drink fact is absent from durable memory.",
        },
        reporter_just_ran=False,
        verification_result=VerificationResult.failed(
            "Key not found: favorite drink",
            checker_path="MUTATION",
        ),
    )
    directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=True,
        reporter_just_ran=False,
        active_skill={
            "name": "file_edit",
            "procedure": ["inspect", "modify", "verify"],
            "persona_hint": "Speak from verified artifact state.",
        },
        persona_runtime=persona_runtime,
    )
    read_directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=False,
        reporter_just_ran=False,
        active_skill={},
        persona_runtime=targeted_read_runtime,
    )
    paused_directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=False,
        reporter_just_ran=False,
        active_skill={},
        persona_runtime=paused_runtime,
    )
    partial_directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=False,
        reporter_just_ran=False,
        active_skill={},
        persona_runtime=partial_runtime,
    )
    failed_directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=False,
        reporter_just_ran=False,
        active_skill={},
        persona_runtime=failed_mutation_runtime,
    )
    search_directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "SEARCH"},
        ingested_document_chat=False,
        reporter_just_ran=True,
        active_skill={},
        persona_runtime=service.build_persona_runtime_pack([], reporter_just_ran=True),
    )

    runtime_message = service.build_runtime_context_message(_DummyOrchestrator(brain=brain))
    search_orc = _DummyOrchestrator(brain=brain)
    search_orc.route_decision = {
        "decision": "SEARCH",
        "card": {
            "query": "weather in London",
        },
    }
    search_orc.context_card = {}
    search_message = service.build_runtime_context_message(search_orc, reporter_just_ran=True)
    search_rule_block = next(
        (
            block
            for block in search_directive_pack.tail_system_blocks
            if block.startswith("[SEARCH_REPORT_RULE]")
        ),
        "",
    )

    success = (
        base_pack.knowledge == {"profile": "active"}
        and brain.calls
        and brain.calls[0] == {"query": "Tell me about the grocery list.", "n_results": 9}
        and base_pack.brain_hits == [{"text": "remember the grocery list flow", "metadata": {"date": "Mar 10, 2026"}, "distance": 0.18}]
        and base_pack.vision_notes == ["Something looks slightly off."]
        and len(base_pack.document_hits) == 1
        and base_pack.situational_state == "[SITUATIONAL STATE]\nUser is debugging Piper."
        and base_pack.intent_state == "[INTENT STATE]\n- Tentative: create a fuzzy words code"
        and focused_pack.document_focus == "The grocery list was updated."
        and focused_pack.document_references == ["Page 2"]
        and focused_pack.document_sources == ["manual.pdf"]
        and search_first_pass_pack.env_block == "[ENVIRONMENT]\nmode=smoke"
        and search_first_pass_pack.world_state == "[WORLD STATE]\nPiper is in task mode."
        and search_first_pass_pack.operational_state == ""
        and search_first_pass_pack.document_hits == []
        and search_first_pass_pack.situational_state == ""
        and search_first_pass_pack.intent_state == ""
        and reporter_pack.brain_hits == [{"text": "remember the grocery list flow", "metadata": {"date": "Mar 10, 2026"}, "distance": 0.18}]
        and reporter_pack.world_state == ""
        and reporter_pack.situational_state == ""
        and reporter_pack.intent_state == ""
        and reporter_pack.operational_state == ""
        and reporter_pack.env_block == ""
        and reporter_pack.document_hits == []
        and doc_focus_pack.document_focus == "The grocery list was updated."
        and doc_focus_pack.world_state == ""
        and doc_focus_pack.situational_state == ""
        and doc_focus_pack.intent_state == "[INTENT STATE]\n- Tentative: create a fuzzy words code"
        and doc_focus_pack.operational_state == ""
        and doc_focus_pack.env_block == ""
        and explain_pack.env_block == ""
        and explain_pack.world_state == ""
        and explain_pack.situational_state == ""
        and explain_pack.intent_state == ""
        and explain_pack.operational_state == ""
        and explain_pack.brain_hits == []
        and proactive_pack.operational_state == "[OPERATIONAL STATE]\nquery=Tell me about the grocery list."
        and proactive_pack.env_block == ""
        and proactive_pack.world_state == ""
        and proactive_pack.brain_hits == []
        and file_work_pack.brain_hits == []
        and file_work_pack.document_hits == []
        and prompt_context.document_focus == "The grocery list was updated."
        and prompt_context.knowledge == {"profile": "active"}
        and prompt_context.intent_state == "[INTENT STATE]\n- Tentative: create a fuzzy words code"
        and persona_runtime.outcome_block.startswith("=== STAGE 1 OUTCOME ===")
        and not persona_runtime.outcome_failed
        and not persona_runtime.outcome_paused
        and persona_runtime.verified_file_work_answer == ""
        and persona_runtime.exact_file_read_answer == ""
        and persona_runtime.file_lookup_answer == ""
        and persona_runtime.needs_file_work_report_rule
        and targeted_read_runtime.exact_file_read_answer == "Apples\nBananas"
        and targeted_read_runtime.latest_stage_is_targeted_read
        and not targeted_read_runtime.needs_file_work_report_rule
        and paused_runtime.outcome_paused
        and paused_runtime.proposal_answer == "Which specific fact from your memory would you like me to remove?"
        and partial_runtime.outcome_failed
        and partial_runtime.verification_verdict == "PARTIAL"
        and partial_runtime.verification_recommendation == "RETRY"
        and partial_runtime.verification_checker_path == "STATE_CHECK"
        and failed_mutation_runtime.outcome_failed
        and failed_mutation_runtime.verification_verdict == "FAILED"
        and failed_mutation_runtime.verification_checker_path == "MUTATION"
        and len(directive_pack.tail_system_blocks) == 5
        and directive_pack.direct_answer == ""
        and "[CONTEXT_ARBITRATION_RULE]" in directive_pack.tail_system_blocks[0]
        and "[DOCUMENT_QA_RULE]" in directive_pack.tail_system_blocks[1]
        and "[ACTIVE_SKILL]" in directive_pack.tail_system_blocks[2]
        and "[FILE_WORK_REPORT_RULE]" in directive_pack.tail_system_blocks[3]
        and "[WORKSPACE_BOUNDARY_RULE]" in directive_pack.tail_system_blocks[4]
        and read_directive_pack.direct_answer == "Apples\nBananas"
        and paused_directive_pack.direct_answer == "Which specific fact from your memory would you like me to remove?"
        and any("[VERIFICATION_RESULT]" in block for block in partial_directive_pack.tail_system_blocks)
        and any("[PARTIAL_VERIFICATION_RULE]" in block for block in partial_directive_pack.tail_system_blocks)
        and any("[VERIFICATION_RESULT]" in block for block in failed_directive_pack.tail_system_blocks)
        and any("[FAILED_VERIFICATION_RULE]" in block for block in failed_directive_pack.tail_system_blocks)
        and any("[CONTEXT_ARBITRATION_RULE]" in block for block in search_directive_pack.tail_system_blocks)
        and "[SEARCH_REPORT_RULE]" in search_rule_block
        and "extend, refine, or correct" in search_rule_block
        and "[LATEST_RUNTIME_CONTEXT]" in runtime_message
        and "Previous route: TASK" in runtime_message
        and "Task goal: Remove bread from grocery_list.txt and read it back." in runtime_message
        and "Execution status: FILE OPERATION SUCCESS" in runtime_message
        and "Runtime note: Removed bread from grocery_list.txt." in runtime_message
        and "Relevant paths: grocery_list.txt" in runtime_message
        and "Search query: weather in London" in search_message
        and "Execution status: SEARCH COMPLETED" in search_message
    )

    print(
        json.dumps(
            {
                "success": bool(success),
                "base_pack": {
                    "knowledge": base_pack.knowledge,
                    "brain_calls": brain.calls,
                    "brain_hits": base_pack.brain_hits,
                    "vision_notes": base_pack.vision_notes,
                    "document_hit_count": len(base_pack.document_hits),
                    "situational_state": base_pack.situational_state,
                    "intent_state": base_pack.intent_state,
                },
        "focused_pack": {
            "document_focus": focused_pack.document_focus,
            "document_references": focused_pack.document_references,
            "document_sources": focused_pack.document_sources,
        },
        "search_first_pass_pack": {
            "world_state": search_first_pass_pack.world_state,
            "operational_state": search_first_pass_pack.operational_state,
            "document_hit_count": len(search_first_pass_pack.document_hits),
        },
        "reporter_pack": {
            "world_state": reporter_pack.world_state,
            "operational_state": reporter_pack.operational_state,
            "env_block": reporter_pack.env_block,
            "brain_hits": reporter_pack.brain_hits,
        },
        "doc_focus_pack": {
            "document_focus": doc_focus_pack.document_focus,
            "world_state": doc_focus_pack.world_state,
            "intent_state": doc_focus_pack.intent_state,
            "operational_state": doc_focus_pack.operational_state,
        },
        "explain_pack": {
            "env_block": explain_pack.env_block,
            "world_state": explain_pack.world_state,
            "brain_hits": explain_pack.brain_hits,
        },
        "proactive_pack": {
            "operational_state": proactive_pack.operational_state,
            "world_state": proactive_pack.world_state,
            "brain_hits": proactive_pack.brain_hits,
        },
        "file_work_pack": {
            "brain_hits": file_work_pack.brain_hits,
            "document_hits": file_work_pack.document_hits,
        },
                "persona_runtime": {
                    "outcome_failed": persona_runtime.outcome_failed,
                    "outcome_paused": persona_runtime.outcome_paused,
                    "needs_file_work_report_rule": persona_runtime.needs_file_work_report_rule,
                },
                "targeted_read_runtime": {
                    "exact_file_read_answer": targeted_read_runtime.exact_file_read_answer,
                    "latest_stage_is_targeted_read": targeted_read_runtime.latest_stage_is_targeted_read,
                },
                "paused_runtime": {
                    "outcome_paused": paused_runtime.outcome_paused,
                    "proposal_answer": paused_runtime.proposal_answer,
                },
                "partial_runtime": {
                    "outcome_failed": partial_runtime.outcome_failed,
                    "verification_verdict": partial_runtime.verification_verdict,
                    "verification_recommendation": partial_runtime.verification_recommendation,
                    "verification_checker_path": partial_runtime.verification_checker_path,
                },
                "failed_mutation_runtime": {
                    "outcome_failed": failed_mutation_runtime.outcome_failed,
                    "verification_verdict": failed_mutation_runtime.verification_verdict,
                    "verification_checker_path": failed_mutation_runtime.verification_checker_path,
                },
                "directive_pack": {
                    "tail_system_blocks": directive_pack.tail_system_blocks,
                    "direct_answer": directive_pack.direct_answer,
                },
                "partial_directive_pack": {
                    "tail_system_blocks": partial_directive_pack.tail_system_blocks,
                },
                "failed_directive_pack": {
                    "tail_system_blocks": failed_directive_pack.tail_system_blocks,
                },
                "read_directive_pack": {
                    "direct_answer": read_directive_pack.direct_answer,
                },
                "paused_directive_pack": {
                    "direct_answer": paused_directive_pack.direct_answer,
                },
                "search_directive_pack": {
                    "tail_system_blocks": search_directive_pack.tail_system_blocks,
                },
                "runtime_message": runtime_message,
                "search_message": search_message,
                "search_rule_block": search_rule_block,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
