from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.prompt_context import PromptContextService  # noqa: E402
from core.instructions_loader import InstructionLoader  # noqa: E402


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
    workspace = ROOT_DIR / "data" / "workspace"

    def recall(self, user_msg: str, n_results: int = 5):
        return [{"text": "remember the grocery list flow", "metadata": {"date": "Mar 10, 2026"}}]


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
    def __init__(self) -> None:
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
        self.brain = _DummyBrain()


def main() -> int:
    service = PromptContextService(
        instruction_loader=InstructionLoader(ROOT_DIR / "data" / "prompts" / "instructions.txt"),
        environment_service=_DummyEnv(),
        operational_state_service=_DummyOps(),
        knowledge_mgr=_DummyKnowledge(),
        transient_state_mgr=_DummyTransient(),
        brain=_DummyBrain(),
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
    file_work_pack = service.clear_memory_for_file_work(base_pack)
    prompt_context = service.to_prompt_context(focused_pack)
    persona_runtime = service.build_persona_runtime_pack(
        _DummyOrchestrator().scratchpad,
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
    directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=True,
        reporter_just_ran=False,
        active_skill={
            "name": "file_edit",
            "procedure": ["inspect", "modify", "verify"],
            "persona_hint": "Speak from verified artifact state.",
        },
        latest_codex_escalation={
            "summary": "Planner looped during file verification.",
            "brief_path": "data/debug/codex_escalations.jsonl",
        },
        persona_runtime=persona_runtime,
    )
    read_directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=False,
        reporter_just_ran=False,
        active_skill={},
        latest_codex_escalation={},
        persona_runtime=targeted_read_runtime,
    )
    paused_directive_pack = service.build_persona_directive_pack(
        route_decision={"decision": "TASK"},
        ingested_document_chat=False,
        reporter_just_ran=False,
        active_skill={},
        latest_codex_escalation={},
        persona_runtime=paused_runtime,
    )

    runtime_message = service.build_runtime_context_message(_DummyOrchestrator())
    search_orc = _DummyOrchestrator()
    search_orc.route_decision = {
        "decision": "SEARCH",
        "card": {
            "query": "weather in London",
        },
    }
    search_orc.context_card = {}
    search_message = service.build_runtime_context_message(search_orc, reporter_just_ran=True)

    success = (
        base_pack.knowledge == {"profile": "active"}
        and base_pack.brain_hits == [{"text": "remember the grocery list flow", "metadata": {"date": "Mar 10, 2026"}}]
        and base_pack.vision_notes == ["Something looks slightly off."]
        and len(base_pack.document_hits) == 1
        and base_pack.situational_state == "[SITUATIONAL STATE]\nUser is debugging Piper."
        and base_pack.intent_state == "[INTENT STATE]\n- Tentative: create a fuzzy words code"
        and focused_pack.document_focus == "The grocery list was updated."
        and focused_pack.document_references == ["Page 2"]
        and focused_pack.document_sources == ["manual.pdf"]
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
        and len(directive_pack.tail_system_blocks) == 3
        and directive_pack.direct_answer == ""
        and "[DOCUMENT_QA_RULE]" in directive_pack.tail_system_blocks[0]
        and "[ACTIVE_SKILL]" in directive_pack.tail_system_blocks[1]
        and "[FILE_WORK_REPORT_RULE]" in directive_pack.tail_system_blocks[2]
        and read_directive_pack.direct_answer == "Apples\nBananas"
        and paused_directive_pack.direct_answer == "Which specific fact from your memory would you like me to remove?"
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
                "directive_pack": {
                    "tail_system_blocks": directive_pack.tail_system_blocks,
                    "direct_answer": directive_pack.direct_answer,
                },
                "read_directive_pack": {
                    "direct_answer": read_directive_pack.direct_answer,
                },
                "paused_directive_pack": {
                    "direct_answer": paused_directive_pack.direct_answer,
                },
                "runtime_message": runtime_message,
                "search_message": search_message,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
