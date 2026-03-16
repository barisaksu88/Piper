"""core/executor.py

The Stage Executor.
Responsible for running the step-by-step loop for a SINGLE stage.
"""

import json
import re
from pathlib import Path
from typing import Any, List, Tuple

from config import CFG
from core.prompting import ScratchpadFormatter, PromptBuilder
from core.debug_tools import log_prompt_debug
from llm.llm_server_client import LLMClientError
from core.contracts import FileCheckDecision, PlannerDecision, StageCard
from core.json_utils import parse_json_response
from core.file_stage_policy import FileStagePolicy
from core.file_checker import FileWorkChecker
from core.engines.file_work import FileWorkEngine
from core.engines.state_mutation import StateMutationEngine
from core.engines.verification import VerificationEngine, VerificationResult
from core.executor_support import (
    decision_signature,
    extract_installable_packages,
    format_tool_result_for_log,
    looks_like_completion_thought,
    normalize_completion_handoff,
    run_inspector,
    tool_result_text,
    tool_signature,
)
from core.stage_policy import stage_is_chat
from core.runtime_control import CancellationToken
from tools.registry import get_tool_spec, resolve_domain_tools, tool_result_is_success


class StageExecutor:
    def __init__(
        self,
        llm_client,
        agent_brain,
        img_gen,
        boot_mgr,
        ui_queue,
        cancel_token: CancellationToken | None = None,
        signal_emitter=None,
    ):
        self.llm = llm_client
        self.brain = agent_brain
        self.img_gen = img_gen
        self.boot = boot_mgr
        self.ui = ui_queue
        self.cancel_token = cancel_token
        self.signal_emitter = signal_emitter
        
        # Internal state for this execution
        self.scratchpad = []
        self._consecutive_fails = 0
        self._last_tool_signature = ""
        self._repeat_count = 0
        self._last_file_verdict = ""
        self.pause_requested = False
        self._last_decision_signature = ""
        self._decision_repeat_count = 0
        self._last_successful_tool_name = ""
        self._last_successful_tool_result: Any = None
        self._last_dashboard_thought = ""
        self.pause_mode = ""
        self.file_checker = FileWorkChecker(self.llm, self.ui, self.brain, cancel_token=self.cancel_token)
        self.state_mutation_engine = StateMutationEngine()
        self.verification_engine = VerificationEngine(file_checker=self.file_checker)
        self._last_verification: VerificationResult | None = None

    def _log_dashboard(self, text: str):
        """Logs a clean message to the UI Dashboard."""
        self.ui.put(("status_widget_dashboard_activity", text))

    @staticmethod
    def _memory_remove_recovery_hint(stage: StageCard, tool_name: str, tool_result: Any) -> str:
        if str(stage.get("stage_type", "")).upper() != "MEMORY_WORK":
            return ""
        stage_text = " ".join(
            [
                str(stage.get("stage_goal", "")),
                str(stage.get("success_condition", "")),
                " ".join(str(item) for item in (stage.get("context") or [])),
            ]
        ).lower()
        if not re.search(r"\b(remove|delete|forget)\b", stage_text):
            return ""

        tool_upper = str(tool_name or "").upper()
        result_text = tool_result_text(tool_result).strip()
        result_lower = result_text.lower()

        if tool_upper == "REMOVE_KNOWLEDGE" and "not found" in result_lower:
            return (
                "SYSTEM HINT: REMOVE_KNOWLEDGE could not find that fact. "
                "Use LIST_KNOWLEDGE once to inspect the exact rendered world-state fact, "
                "then retry REMOVE_KNOWLEDGE with the exact key if it appears. "
                "If the target is already absent from the listing, return is_complete true and report that current state honestly."
            )

        if tool_upper == "LIST_KNOWLEDGE" and result_text:
            return (
                "SYSTEM HINT: The current world-state listing is now in the scratchpad. "
                "If the target appears there, retry REMOVE_KNOWLEDGE with the exact rendered key. "
                "If it does not appear, finish and state that the fact is already absent."
            )

        return ""

    def _log_thought(self, text: str) -> None:
        clean = " ".join(str(text or "").split()).strip()
        if not clean or clean == self._last_dashboard_thought:
            return
        self._last_dashboard_thought = clean
        self._log_dashboard(f"Thinking: {clean}")

    def _raise_if_cancelled(self) -> None:
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()

    def _emit_runtime_signal(
        self,
        *,
        kind: str,
        severity: str,
        source: str,
        summary: str,
        stage: StageCard,
        step: int = 0,
        tool: str = "",
        details: str = "",
        count: int = 0,
        evidence_files: list[str] | None = None,
    ) -> None:
        if self.signal_emitter is None:
            return
        self.signal_emitter(
            {
                "kind": kind,
                "severity": severity,
                "source": source,
                "summary": summary,
                "details": details,
                "stage_goal": str(stage.get("stage_goal", "")).strip(),
                "stage_type": str(stage.get("stage_type", "")).strip(),
                "step": int(step or 0),
                "tool": str(tool or "").strip(),
                "count": int(count or 0),
                "evidence_files": list(evidence_files or []),
            }
        )

    def _append_file_checker_note(self, decision: FileCheckDecision) -> None:
        verdict = str(decision.get("verdict", "FAILED")).upper()
        reason = str(decision.get("reason", "")).strip()
        evidence_files = decision.get("evidence_files") or []
        note_lines = [
            f"FILE_CHECKER_VERDICT: {verdict}",
            f"FILE_CHECKER_REASON: {reason or 'No reason provided.'}",
        ]
        if evidence_files:
            note_lines.append("FILE_CHECKER_EVIDENCE: " + ", ".join(str(path) for path in evidence_files))
        self.scratchpad.append("\n".join(note_lines))

    # _file_result_candidate_paths → FileWorkEngine.candidate_paths()
    # _is_code_path / _render_code_view / _maybe_emit_code_view → FileWorkEngine.render_artifact_view()
    # _scratchpad_exact_read_paths → FileWorkEngine.exact_read_paths_from_scratchpad()

    def _latest_stage_has_proposal(self) -> bool:
        seen_stage_start = False
        for entry in reversed(self.scratchpad):
            text = str(entry or "")
            if "PROPOSAL:" in text:
                return True
            stripped = text.lstrip()
            if stripped.startswith("=== STAGE ") and " START ===" in stripped:
                seen_stage_start = True
                break
        return False if seen_stage_start else any("PROPOSAL:" in str(entry or "") for entry in self.scratchpad)

    def _inspector_finish_has_stage_evidence(self) -> bool:
        return bool(
            self._last_successful_tool_name
            or self._latest_stage_has_proposal()
            or self.pause_requested
        )

    # _should_block_code_file_write_text / _should_block_redundant_exact_read
    # → FileWorkEngine.should_block()



    def run(self, stage: StageCard, stage_num: int, total_stages: int) -> Tuple[bool, List[str]]:
        """
        Executes one stage.
        Returns: (success_bool, log_entries_list)
        """
        
        # 1. Inject Header
        header = ScratchpadFormatter.format_stage_header(stage_num, stage)
        self.scratchpad.append(header)
        
        # Dashboard Log (Stage Start)
        goal = stage.get("stage_goal", "Unknown Goal")
        self._log_dashboard(f"=== Stage {stage_num}: {goal} ===")
        self._raise_if_cancelled()
        
        max_steps = max(1, int(getattr(CFG, "EXECUTOR_MAX_STEPS", 12) or 12))
        step_count = 0
        success = False
        self._last_file_verdict = ""
        self._last_verification = None
        self.pause_requested = False
        self.pause_mode = ""
        self._last_decision_signature = ""
        self._decision_repeat_count = 0
        self._last_successful_tool_name = ""
        self._last_successful_tool_result = None
        self._last_dashboard_thought = ""
        
        # --- AUTO UNLOCK TOOLS ---
        # Resolve tools from the domain type if not provided
        stage_type = stage.get("stage_type", "FILE_WORK")
        chat_stage = stage_is_chat(stage)

        # If Router didn't send tools, or sent empty list, we resolve them
        allowed_tools = stage.get("allowed_tools", [])
        if chat_stage:
            if allowed_tools:
                self.ui.put(("agent_log", "   -> CHAT stage does not expose runtime tools. Clearing routed tool list."))
            allowed_tools = []
        elif not allowed_tools:
            allowed_tools = resolve_domain_tools(stage_type)
            self.ui.put(("agent_log", f"   -> Auto-unlocked tools for {stage_type}: {allowed_tools}"))

        # Fallback safety
        if not allowed_tools and not chat_stage:
            allowed_tools = ["RUN_CODE"]  # Safe default
        stage["allowed_tools"] = list(allowed_tools)
        
        # Load Planner Template
        prompt_path = CFG.DATA_DIR / "prompts" / "manager.txt"
        sys_base = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "Planner Prompt Missing."

        while step_count < max_steps:
            self._raise_if_cancelled()
            step_count += 1
            self.ui.put(("status_widget_mode", "THINKING"))
            self.ui.put(("status_widget_step", f"Stage {stage_num}/{total_stages} | Step {step_count}"))

            # Build Prompt via Architect
            scratch_text = "\n".join(self.scratchpad)
            sys_prompt = PromptBuilder.build_planner_prompt(sys_base, stage, scratch_text, step_count)

            messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": "What is the next step?"}]
            
            # Debug Dump (LLM Prompt)
            if CFG.DEBUG_LLM_PROMPTS:
                log_prompt_debug(CFG.LLM_PROMPT_DEBUG_PATH, messages, f"STAGE_{stage_num}_STEP_{step_count}")
            
            # Debug Dump (Manager Debug File)
            if CFG.DEBUG_MANAGER_PROMPTS:
                try:
                    with open(CFG.MANAGER_DEBUG_PATH, "w", encoding="utf-8") as f:
                        f.write(f"STAGE: {stage_num}, STEP: {step_count}\n{'='*40}\n{sys_prompt}")
                except Exception:
                    pass

            # Generate
            try:
                raw = ""
                for delta in self.llm.generate_stream(messages, temperature=0.0, cancel_token=self.cancel_token):
                    raw += delta
            except LLMClientError as e:
                self._emit_runtime_signal(
                    kind="planner_error",
                    severity="error",
                    source="planner",
                    summary=f"Planner error: {e}",
                    details=str(e),
                    stage=stage,
                    step=step_count,
                )
                self.ui.put(("error", f"Planner Error: {e}"))
                return False, self.scratchpad
            self._raise_if_cancelled()

            self.ui.put(("agent_log", f"[PLANNER] {raw.strip()}"))
            decision: PlannerDecision = parse_json_response(raw)

            # CHECK FOR PARSE FAILURE
            if not decision:
                self.ui.put(("agent_log", "   -> WARNING: JSON parsing failed! Retrying..."))
                self._log_dashboard("Parse error, retrying...")
                self.scratchpad.append("SYSTEM ERROR: Invalid JSON format. Fix your JSON output.")
                self._emit_runtime_signal(
                    kind="planner_parse_error",
                    severity="warning",
                    source="planner",
                    summary="Planner emitted invalid JSON.",
                    stage=stage,
                    step=step_count,
                )
                continue

            thought = decision.get("thought", "") or ""
            tool_tag = decision.get("tool", "") or ""
            tool_tag_normalized = str(tool_tag or "").strip().lower()
            decision_sig = decision_signature(decision)
            if decision_sig and decision_sig == self._last_decision_signature:
                self._decision_repeat_count += 1
            else:
                self._decision_repeat_count = 0
            self._last_decision_signature = decision_sig
            
            # Dashboard Log (Clean Thought)
            if thought:
                self._log_thought(thought)

            if chat_stage:
                completion_handoff = normalize_completion_handoff(decision)
                if tool_tag_normalized not in {"", "null"}:
                    err = (
                        "SYSTEM ERROR: CHAT stage must not execute runtime tools. "
                        "Return tool null with is_complete true and put the exact user-facing clarification in proposal."
                    )
                    self.scratchpad.append(err)
                    self.ui.put(("agent_log", f"   -> {err}"))
                    continue
                if decision.get("is_complete", False) or tool_tag_normalized == "null":
                    if not completion_handoff:
                        err = (
                            "SYSTEM ERROR: CHAT stage is not complete until you provide the user-facing clarification "
                            "or missing-detail request in the proposal field."
                        )
                        self.scratchpad.append(err)
                        self.ui.put(("agent_log", f"   -> {err}"))
                        continue
                    entry = ScratchpadFormatter.format_step(
                        step_count,
                        thought or "Clarification ready",
                        "[NO_TOOL_PROPOSAL]",
                        f"PROPOSAL: {completion_handoff}",
                    )
                    self.scratchpad.append(entry)
                    self.pause_requested = True
                    self.pause_mode = "user_input"
                    self.ui.put(("agent_log", "   -> CHAT stage produced a clarification handoff for the user."))
                    self._log_dashboard("Awaiting user input.")
                    success = True
                    break
                err = (
                    "SYSTEM ERROR: CHAT stage has no runtime tools. "
                    "Return tool null with is_complete true and provide the user-facing clarification in proposal."
                )
                self.scratchpad.append(err)
                self.ui.put(("agent_log", f"   -> {err}"))
                continue

            repeated_completion_without_progress = (
                self._decision_repeat_count >= 1
                and looks_like_completion_thought(thought)
                and FileStagePolicy.is_file_read_result(self._last_successful_tool_name, self._last_successful_tool_result)
            )
            if repeated_completion_without_progress:
                self._emit_runtime_signal(
                    kind="planner_repeat",
                    severity="warning",
                    source="planner",
                    summary="Planner repeated a completion-like decision without new progress.",
                    stage=stage,
                    step=step_count,
                    count=self._decision_repeat_count + 1,
                    tool=self._last_successful_tool_name,
                )
                if FileStagePolicy.stage_is_file_work(stage):
                    if FileStagePolicy.stage_requires_file_verification(stage):
                        if self._accept_current_workspace_verification(
                            stage,
                            "   -> Completion accepted from current workspace verification after repeated completion-like planner decision.",
                        ):
                            self._log_dashboard("FILE_WORK verified from current state.")
                            self._append_file_lookup_note_if_available(stage)
                            self._append_exact_file_read_note_if_available(stage)
                            success = True
                            break
                        self.ui.put(("agent_log", "   -> Completion-like planner decision still lacks VERIFIED current-state evidence."))
                        continue
                    if FileStagePolicy.stage_requires_analysis_report(stage) and not self._latest_stage_has_proposal():
                        hint = (
                            "SYSTEM ERROR: This diagnosis stage still lacks an explicit diagnosis summary. "
                            "Return tool null with is_complete true and put the diagnosis in the proposal field."
                        )
                        if not self.scratchpad or self.scratchpad[-1] != hint:
                            self.scratchpad.append(hint)
                        self.ui.put(("agent_log", f"   -> {hint}"))
                        continue
                    self.ui.put(("agent_log", "   -> Repeated completion-like planner decision after successful inspection. Auto-finishing stage from existing evidence."))
                    self._log_dashboard("Inspection complete from existing evidence.")
                    self._append_file_lookup_note_if_available(stage)
                    self._append_exact_file_read_note_if_available(stage)
                    success = True
                    break

            # CHECK COMPLETION (Priority: Explicit Flag)
            if decision.get("is_complete", False):
                completion_handoff = normalize_completion_handoff(decision)
                if FileStagePolicy.stage_requires_analysis_report(stage) and not completion_handoff:
                    hint = (
                        "SYSTEM ERROR: This inspection stage is not complete until you state the diagnosis explicitly. "
                        "Return tool null with is_complete true and put the diagnosis summary in the proposal field."
                    )
                    if not self.scratchpad or self.scratchpad[-1] != hint:
                        self.scratchpad.append(hint)
                    self.ui.put(("agent_log", f"   -> {hint}"))
                    continue
                if completion_handoff:
                    entry = ScratchpadFormatter.format_step(
                        step_count,
                        thought or "Stage complete",
                        "[NO_TOOL_PROPOSAL]",
                        f"PROPOSAL: {completion_handoff}",
                    )
                    self.scratchpad.append(entry)
                if self._completion_is_supported_by_non_mutating_file_evidence(stage):
                    self.ui.put(("agent_log", "   -> Completion accepted from existing non-mutating FILE_WORK evidence."))
                    self._log_dashboard("FILE_WORK non-mutating stage complete.")
                    self._append_file_lookup_note_if_available(stage)
                    self._append_exact_file_read_note_if_available(stage)
                    success = True
                    break
                if FileStagePolicy.stage_requires_file_verification(stage) and self._last_file_verdict != "VERIFIED":
                    if not self._accept_current_workspace_verification(stage, "   -> Completion accepted from current workspace verification."):
                        self._emit_runtime_signal(
                            kind="verification_block",
                            severity="warning",
                            source="executor",
                            summary="FILE_WORK completion was blocked because verification is still missing.",
                            stage=stage,
                            step=step_count,
                            tool=self._last_successful_tool_name,
                        )
                        self.ui.put(("agent_log", "   -> Completion blocked: FILE_WORK requires VERIFIED checker evidence."))
                        self.scratchpad.append("SYSTEM ERROR: FILE_WORK cannot complete until FILE_CHECKER_VERDICT is VERIFIED.")
                        continue
                self.ui.put(("agent_log", "   -> Planner signaled completion."))
                self._log_dashboard("Stage Complete.")
                success = True
                break

            # SAFETY: Require EXPLICIT null string for implicit completion
            if tool_tag == "null":
                completion_handoff = normalize_completion_handoff(decision)
                if FileStagePolicy.stage_requires_analysis_report(stage) and not completion_handoff:
                    hint = (
                        "SYSTEM ERROR: This inspection stage is not complete until you state the diagnosis explicitly. "
                        "Return tool null with is_complete true and put the diagnosis summary in the proposal field."
                    )
                    if not self.scratchpad or self.scratchpad[-1] != hint:
                        self.scratchpad.append(hint)
                    self.ui.put(("agent_log", f"   -> {hint}"))
                    continue
                if completion_handoff:
                    entry = ScratchpadFormatter.format_step(
                        step_count,
                        thought or "Stage complete",
                        "[NO_TOOL_PROPOSAL]",
                        f"PROPOSAL: {completion_handoff}",
                    )
                    self.scratchpad.append(entry)
                if self._completion_is_supported_by_non_mutating_file_evidence(stage):
                    self.ui.put(("agent_log", "   -> Completion accepted from existing non-mutating FILE_WORK evidence."))
                    self._log_dashboard("FILE_WORK non-mutating stage complete.")
                    self._append_file_lookup_note_if_available(stage)
                    self._append_exact_file_read_note_if_available(stage)
                    success = True
                    break
                if FileStagePolicy.stage_requires_file_verification(stage) and self._last_file_verdict != "VERIFIED":
                    if not self._accept_current_workspace_verification(stage, "   -> Completion accepted from current workspace verification."):
                        self._emit_runtime_signal(
                            kind="verification_block",
                            severity="warning",
                            source="executor",
                            summary="FILE_WORK completion was blocked because verification is still missing.",
                            stage=stage,
                            step=step_count,
                            tool=self._last_successful_tool_name,
                        )
                        self.ui.put(("agent_log", "   -> Completion blocked: FILE_WORK requires VERIFIED checker evidence."))
                        self.scratchpad.append("SYSTEM ERROR: FILE_WORK cannot complete until FILE_CHECKER_VERDICT is VERIFIED.")
                        continue
                self.ui.put(("agent_log", "   -> Planner signaled completion (tool: null)."))
                self._log_dashboard("Stage Complete.")
                success = True
                break
            
            # If tool is empty but not "null", it's an error - retry
            if not tool_tag.strip():
                self.ui.put(("agent_log", "   -> WARNING: Empty tool received. Prompting fix."))
                err = "SYSTEM ERROR: Tool field was empty. Output a valid tool."
                self.scratchpad.append(err)
                if self._decision_repeat_count >= 1:
                    repeat_hint = (
                        "SYSTEM ERROR: Repeated identical planner decision without a usable tool is not progress. "
                        "Either return is_complete true or choose one concrete next action."
                    )
                    if not self.scratchpad or self.scratchpad[-1] != repeat_hint:
                        self.scratchpad.append(repeat_hint)
                    self.ui.put(("agent_log", f"   -> {repeat_hint}"))
                continue

            # TOOL ENFORCEMENT
            tag_match = re.match(r'\[([A-Za-z_]+)', tool_tag)
            base_tag = tag_match.group(1).upper() if tag_match else "UNKNOWN"
            tool_spec = get_tool_spec(base_tag)

            if base_tag not in allowed_tools:
                err_msg = f"SECURITY VIOLATION: Tool [{base_tag}] not allowed in this stage."
                entry = ScratchpadFormatter.format_step(step_count, thought, tool_tag, err_msg)
                self.scratchpad.append(entry)
                continue

            if FileStagePolicy.stage_is_non_mutating_file_stage(stage) and base_tag == "RUN_CODE" and not FileStagePolicy.stage_requires_file_computation(stage):
                err_msg = "SECURITY VIOLATION: Non-mutating FILE_WORK stage must use FILE_OP read/list actions unless the stage explicitly requires computation."
                entry = ScratchpadFormatter.format_step(step_count, thought, tool_tag, err_msg)
                self.scratchpad.append(entry)
                self.ui.put(("agent_log", f"   -> {err_msg}"))
                continue

            if FileStagePolicy.stage_is_non_mutating_file_stage(stage) and base_tag == "FILE_OP":
                planned_action = FileStagePolicy.planned_file_op_action(tool_tag)
                if planned_action and planned_action not in {"read_text", "read_many", "read_file", "read_files", "list_tree", "find_paths", "extension_inventory"}:
                    err_msg = f"SECURITY VIOLATION: Non-mutating FILE_WORK stage cannot use mutating FILE_OP action '{planned_action}'."
                    entry = ScratchpadFormatter.format_step(step_count, thought, tool_tag, err_msg)
                    self.scratchpad.append(entry)
                    self.ui.put(("agent_log", f"   -> {err_msg}"))
                    if FileStagePolicy.is_file_planning_stage(stage) or FileStagePolicy.stage_requires_user_approval(stage):
                        hint = (
                            "SYSTEM HINT: Proposal/approval stages must not write files. "
                            "Return tool null with is_complete true and put the proposal text in the optional proposal field."
                        )
                        if not self.scratchpad or self.scratchpad[-1] != hint:
                            self.scratchpad.append(hint)
                        self.ui.put(("agent_log", f"   -> {hint}"))
                    continue

            if FileStagePolicy.stage_is_structure_prep_stage(stage) and base_tag == "FILE_OP":
                planned_action = FileStagePolicy.planned_file_op_action(tool_tag)
                if planned_action and planned_action not in {"ensure_dir", "ensure_dirs", "read_text", "read_many", "list_tree", "find_paths", "extension_inventory"}:
                    err_msg = f"SECURITY VIOLATION: Folder-structure stage cannot perform relocation or deletion action '{planned_action}'."
                    entry = ScratchpadFormatter.format_step(step_count, thought, tool_tag, err_msg)
                    self.scratchpad.append(entry)
                    self.ui.put(("agent_log", f"   -> {err_msg}"))
                    continue

            if base_tag == "FILE_OP":
                _exact_paths = FileWorkEngine.exact_read_paths_from_scratchpad(self.scratchpad)
                _block = FileWorkEngine.should_block(stage, tool_tag, _exact_paths)
                if _block.blocked:
                    entry = ScratchpadFormatter.format_step(step_count, thought, tool_tag, _block.reason)
                    self.scratchpad.append(entry)
                    self.ui.put(("agent_log", f"   -> {_block.reason}"))
                    continue

            # EXECUTION
            if base_tag == "INSTALL_PACKAGE":
                pkg_match = re.match(r"\[INSTALL_PACKAGE:\s*(.*?)\]$", tool_tag.strip(), re.DOTALL | re.IGNORECASE)
                pkg_name = pkg_match.group(1).strip() if pkg_match else ""
                self._log_dashboard(f"Installing package: {pkg_name or 'unknown'}")
                self.ui.put(("agent_log", f"   -> Installing package: {pkg_name or 'unknown'}"))
            action = self.brain.parse_and_execute(tool_tag, cancel_token=self.cancel_token)
            self._raise_if_cancelled()

            parsed_tag = str(action.tag or "").upper()
            if action.action_type != "TOOL" or parsed_tag != base_tag:
                tool_result = {
                    "tool": base_tag,
                    "status": "FAILED",
                    "summary": f"Malformed [{base_tag}] invocation could not be parsed or executed.",
                    "action": "",
                    "workspace_changed": False,
                    "created_files": [],
                    "updated_files": [],
                    "deleted_files": [],
                    "created_dirs": [],
                    "deleted_dirs": [],
                    "evidence_files": [],
                    "file_snippets": {},
                }
            # Handle Image Gen
            elif action.tag in ["CREATE_IMAGE", "MODIFY_IMAGE"]:
                self.ui.put(("status", "Pausing LLM for Image Gen..."))
                self.boot.pause_server()

                try:
                    self._raise_if_cancelled()
                    if action.tag == "CREATE_IMAGE":
                        result = self.img_gen.generate(action.payload or "art", cancel_token=self.cancel_token)
                    else:
                        result = self.img_gen.edit_image(action.payload or "enhance", cancel_token=self.cancel_token)
                finally:
                    self.boot.resume_server()
                self._raise_if_cancelled()
                self.ui.put(("show_image", result))
                tool_result = result
            else:
                tool_result = action.execute_result or "Done."

            # LOG RESULT
            self._raise_if_cancelled()
            entry = ScratchpadFormatter.format_step(step_count, thought, tool_tag, tool_result)
            self.scratchpad.append(entry)
            self.ui.put(("agent_log", format_tool_result_for_log(base_tag, tool_result)))
            _view = FileWorkEngine.render_artifact_view(tool_result)
            if _view:
                self.ui.put(("code_view", _view))
            if tool_result_is_success(tool_spec, tool_result):
                self._last_successful_tool_name = base_tag
                self._last_successful_tool_result = tool_result
                self._maybe_launch_code_session(tool_result)
                if (
                    FileStagePolicy.stage_is_interactive_runtime_verification(stage)
                    and base_tag == "RUN_CODE"
                    and isinstance(tool_result, dict)
                    and str(tool_result.get("action", "")).lower() == "run_workspace_script"
                ):
                    launched = str(tool_result.get("launched_script") or "").strip() or "the running script"
                    proposal = (
                        f"I started `{launched}`. Please try the controls now and tell me what you observe, "
                        "for example whether left/right movement works and whether the game responds correctly."
                    )
                    entry = ScratchpadFormatter.format_step(
                        step_count,
                        thought or "Awaiting user gameplay report",
                        "[NO_TOOL_PROPOSAL]",
                        f"PROPOSAL: {proposal}",
                    )
                    self.scratchpad.append(entry)
                    self.pause_requested = True
                    self.pause_mode = "user_input"
                    self.ui.put(("agent_log", "   -> Interactive runtime verification requires user observation. Pausing instead of relaunching."))
                    self._log_dashboard("Awaiting user gameplay feedback.")
                    success = True
                    break
                if base_tag == "FILE_OP" and isinstance(tool_result, dict):
                    _existing = FileWorkEngine.exact_read_paths_from_scratchpad(self.scratchpad)
                    _note = FileWorkEngine.capture_exact_read(stage, tool_result, _existing)
                    if _note and _note not in self.scratchpad:
                        self.scratchpad.append(_note)
                    if FileStagePolicy.stage_requires_analysis_report(stage):
                        hint = (
                            "SYSTEM HINT: The inspection evidence is now in the scratchpad. "
                            "Do not stop at the read. Summarize the diagnosis explicitly in the proposal field "
                            "when you return is_complete true."
                        )
                        if not self.scratchpad or self.scratchpad[-1] != hint:
                            self.scratchpad.append(hint)
                        self.ui.put(("agent_log", f"   -> {hint}"))
                    elif self._auto_finish_verified_current_state_after_successful_read(stage):
                        success = True
                        break
                if (
                    FileStagePolicy.stage_is_file_work(stage)
                    and not FileStagePolicy.stage_is_non_mutating_file_stage(stage)
                    and isinstance(tool_result, dict)
                    and str(tool_result.get("tool", "")).upper() in {"FILE_OP", "RUN_CODE"}
                    and (
                        base_tag == "RUN_CODE"
                        or str(tool_result.get("action", "")).lower()
                        in {
                            "append_text",
                            "consolidate_by_extension",
                            "copy_many",
                            "copy_path",
                            "delete_empty_dirs",
                            "delete_many",
                            "delete_path",
                            "ensure_dir",
                            "ensure_dirs",
                            "move_many",
                            "move_path",
                            "update_json",
                            "write_json",
                            "write_text",
                        }
                    )
                    and str(tool_result.get("action", "")).lower() not in {"run_workspace_script"}
                    and not bool(tool_result.get("workspace_changed"))
                ):
                    self._emit_runtime_signal(
                        kind="mutation_no_effect",
                        severity="warning",
                        source="executor",
                        summary="A mutating file step succeeded without changing workspace state.",
                        details=str(tool_result.get("summary", "")).strip(),
                        stage=stage,
                        step=step_count,
                        tool=base_tag,
                        evidence_files=[str(item) for item in (tool_result.get("evidence_files") or []) if str(item).strip()],
                    )

            recovery_hint = FileStagePolicy.file_recovery_hint(stage, tool_result)
            if recovery_hint and (not self.scratchpad or self.scratchpad[-1] != recovery_hint):
                self.scratchpad.append(recovery_hint)
                self.ui.put(("agent_log", f"   -> {recovery_hint}"))

            memory_recovery_hint = self._memory_remove_recovery_hint(stage, base_tag, tool_result)
            if memory_recovery_hint and (not self.scratchpad or self.scratchpad[-1] != memory_recovery_hint):
                self.scratchpad.append(memory_recovery_hint)
                self.ui.put(("agent_log", f"   -> {memory_recovery_hint}"))

            memory_absent_target = self._memory_remove_already_absent_target(stage, base_tag, tool_result)
            if memory_absent_target:
                auto_entry = ScratchpadFormatter.format_step(
                    step_count,
                    thought or "Memory remove stage resolved from current listing",
                    "[AUTO_RESOLVE_MEMORY_STATE]",
                    f"Knowledge already absent: {memory_absent_target}",
                )
                self.scratchpad.append(auto_entry)
                self.ui.put(("agent_log", "   -> Auto-finish MEMORY_WORK: requested fact is already absent from current world state."))
                self._log_dashboard("Memory fact already absent.")
                success = True
                break

            if base_tag == "INSTALL_PACKAGE" and tool_result_is_success(tool_spec, tool_result):
                hint = "SYSTEM HINT: Package install succeeded. Retry the original action that previously failed."
                if not self.scratchpad or self.scratchpad[-1] != hint:
                    self.scratchpad.append(hint)
                self.ui.put(("agent_log", f"   -> {hint}"))

            if (
                FileStagePolicy.stage_is_script_launch_stage(stage)
                and base_tag == "RUN_CODE"
                and tool_result_is_success(tool_spec, tool_result)
            ):
                self.ui.put(("agent_log", "   -> Auto-finish after successful workspace script launch."))
                self._log_dashboard("Workspace script launched.")
                success = True
                break

            install_candidates = extract_installable_packages(tool_result)
            if (
                install_candidates
                and FileStagePolicy.stage_is_file_work(stage)
                and "INSTALL_PACKAGE" not in allowed_tools
            ):
                allowed_tools.append("INSTALL_PACKAGE")
                stage["allowed_tools"] = list(allowed_tools)
                package_hint = install_candidates[0]
                hint = (
                    "SYSTEM HINT: Missing third-party module detected. "
                    f"INSTALL_PACKAGE is now temporarily allowed for this stage. "
                    f"If needed, use [INSTALL_PACKAGE: {package_hint}] and then retry the original action."
                )
                self.scratchpad.append(hint)
                self.ui.put(("agent_log", f"   -> {hint}"))
                self.ui.put(("agent_log", "   -> Auto-unlocked tools for FILE_WORK recovery: INSTALL_PACKAGE"))

            if self.verification_engine.should_verify(stage, base_tag, tool_result):
                vr = self.verification_engine.evaluate(
                    stage, tool_result,
                    Path(getattr(self.brain, "workspace", ".")),
                    step_count, max_steps - step_count,
                    tool_succeeded=tool_result_is_success(tool_spec, tool_result),
                )
                self._last_verification = vr
                self._last_file_verdict = vr.verdict
                file_check = {"verdict": vr.verdict, "reason": vr.evidence_summary, "evidence_files": []}
                self._append_file_checker_note(file_check)
                if vr.verdict == "VERIFIED":
                    self._append_verified_file_work_result_note(stage, file_check)
                    self.ui.put(("agent_log", "   -> FILE_CHECKER verified artifact state."))
                    self._log_dashboard("FILE_WORK verified.")
                    success = True
                    break
                self._emit_runtime_signal(
                    kind="file_checker_failed",
                    severity="error" if vr.verdict == "FAILED" else "warning",
                    source="file_checker",
                    summary=f"FILE_CHECKER {vr.verdict}: {vr.evidence_summary}",
                    details=vr.evidence_summary,
                    stage=stage,
                    step=step_count,
                    tool=base_tag,
                    evidence_files=[],
                )
                self.ui.put(("agent_log", f"   -> FILE_CHECKER {vr.verdict}: {vr.evidence_summary}"))
                if vr.verdict == "FAILED" and FileStagePolicy.stage_is_content_edit_stage(stage):
                    current_read = self.file_checker.build_current_file_stage_read_result(stage, tool_result)
                    if current_read is not None:
                        _existing2 = FileWorkEngine.exact_read_paths_from_scratchpad(self.scratchpad)
                        _note2 = FileWorkEngine.capture_exact_read(stage, current_read, _existing2)
                        if _note2 and _note2 not in self.scratchpad:
                            self.scratchpad.append(_note2)
                        _view2 = FileWorkEngine.render_artifact_view(current_read)
                        if _view2:
                            self.ui.put(("code_view", _view2))
                        current_paths = FileStagePolicy.file_read_paths(current_read)
                        current_target = current_paths[0] if len(current_paths) == 1 else ", ".join(current_paths[:3]) or "the current file"
                        current_hint = (
                            f"SYSTEM HINT: The current on-disk state of '{current_target}' after the failed edit is now in the scratchpad. "
                            "Use that exact source as the new baseline instead of repeating the same rewrite."
                        )
                        if not self.scratchpad or self.scratchpad[-1] != current_hint:
                            self.scratchpad.append(current_hint)
                        self.ui.put(("agent_log", f"   -> {current_hint}"))
                checker_hint = FileWorkEngine.recovery_hint(stage, tool_result, file_check)
                if checker_hint and (not self.scratchpad or self.scratchpad[-1] != checker_hint):
                    self.scratchpad.append(checker_hint)
                    self.ui.put(("agent_log", f"   -> {checker_hint}"))

            if (
                FileStagePolicy.stage_is_non_mutating_file_stage(stage)
                and tool_result_is_success(tool_spec, tool_result)
                and FileStagePolicy.non_mutating_file_stage_is_satisfied(stage, base_tag, tool_result)
            ):
                self.ui.put(("agent_log", f"   -> Auto-finish after successful non-mutating file stage via [{base_tag}]."))
                self._log_dashboard("FILE_WORK non-mutating stage complete.")
                self._append_file_lookup_note_if_available(stage)
                self._append_exact_file_read_note_if_available(stage)
                success = True
                break

            # CONTRACT-LEVEL SHORT CIRCUIT FOR DIRECT STATE-CHANGE TOOLS
            if tool_spec and tool_spec.auto_finish_on_success and tool_result_is_success(tool_spec, tool_result):
                self.ui.put(("agent_log", f"   -> Auto-finish after successful [{base_tag}]."))
                self._log_dashboard(f"{base_tag} succeeded. Stage complete.")
                success = True
                break

            if self._should_auto_finish_inspection(stage, tool_spec, tool_result):
                self.ui.put(("agent_log", f"   -> Auto-finish after successful inspection via [{base_tag}]."))
                self._log_dashboard(f"{base_tag} inspection complete.")
                success = True
                break

            # SMART INSPECTOR LOGIC
            if self._should_run_inspector(stage, tool_result, base_tag, step_count, decision):
                if self.pause_requested:
                    self._log_dashboard("FILE_WORK paused for approval.")
                    success = True
                    break
                self.ui.put(("agent_log", "   -> Inspector Triggered."))
                if self._run_inspector(stage):
                    if not self._inspector_finish_has_stage_evidence():
                        hint = (
                            "SYSTEM ERROR: Inspector cannot finish this stage because no successful tool result "
                            "or proposal evidence exists yet."
                        )
                        if not self.scratchpad or self.scratchpad[-1] != hint:
                            self.scratchpad.append(hint)
                        self.ui.put(("agent_log", f"   -> {hint}"))
                        success = False
                        break
                    if FileStagePolicy.stage_requires_analysis_report(stage) and not self._latest_stage_has_proposal():
                        hint = (
                            "SYSTEM ERROR: Inspector cannot finish this diagnosis stage without an explicit diagnosis summary. "
                            "Return tool null with is_complete true and put the diagnosis in the proposal field."
                        )
                        if not self.scratchpad or self.scratchpad[-1] != hint:
                            self.scratchpad.append(hint)
                        self.ui.put(("agent_log", f"   -> {hint}"))
                        continue
                    if FileStagePolicy.stage_requires_file_verification(stage) and self._last_file_verdict != "VERIFIED":
                        self.ui.put(("agent_log", "   -> Inspector finished the stage without VERIFIED evidence; treating stage as incomplete."))
                        success = False
                    else:
                        success = True
                    break
                else:
                    self._consecutive_fails = 0  # Reset if inspector says continue

        if not success and FileStagePolicy.stage_requires_file_verification(stage):
            current_check = self.file_checker.verify_current_file_stage_state(stage, self._last_successful_tool_result)
            current_verdict = str((current_check or {}).get("verdict", "")).upper()
            if current_verdict == "VERIFIED":
                self._last_file_verdict = "VERIFIED"
                self._append_file_checker_note(current_check or {})
                self._append_verified_file_work_result_note(stage, current_check or {}, current_state_only=True)
                self.ui.put(("agent_log", "   -> Final current-state verification recovered the stage."))
                self._log_dashboard("FILE_WORK recovered from verified current state.")
                return True, self.scratchpad
        if (
            not success
            and FileStagePolicy.stage_requires_analysis_report(stage)
            and self._latest_stage_has_proposal()
            and FileStagePolicy.is_file_read_result(self._last_successful_tool_name, self._last_successful_tool_result)
        ):
            self.ui.put(("agent_log", "   -> Final diagnosis proposal recovered the stage from existing inspection evidence."))
            self._log_dashboard("Diagnosis complete from existing evidence.")
            return True, self.scratchpad

        return success, self.scratchpad

    def _append_exact_file_read_note_if_available(self, stage: StageCard) -> None:
        if self._last_successful_tool_name != "FILE_OP" or not isinstance(self._last_successful_tool_result, dict):
            return
        existing = FileWorkEngine.exact_read_paths_from_scratchpad(self.scratchpad)
        note = FileWorkEngine.capture_exact_read(stage, self._last_successful_tool_result, existing)
        if note and note not in self.scratchpad:
            self.scratchpad.append(note)

    def _auto_finish_verified_current_state_after_successful_read(self, stage: StageCard) -> bool:
        if not FileStagePolicy.stage_requires_file_verification(stage):
            return False
        if self._last_successful_tool_name != "FILE_OP" or not isinstance(self._last_successful_tool_result, dict):
            return False
        action = str(self._last_successful_tool_result.get("action", "")).lower()
        if action not in {"read_text", "read_many"}:
            return False
        if not self._accept_current_workspace_verification(
            stage,
            "   -> Current file state already satisfies the requested end state after inspection.",
        ):
            return False
        self._log_dashboard("FILE_WORK already satisfied from current state.")
        self._append_file_lookup_note_if_available(stage)
        self._append_exact_file_read_note_if_available(stage)
        return True

    # _should_capture_exact_file_read_for_planner / _append_exact_file_read_note_from_result
    # → FileWorkEngine.capture_exact_read()

    def _accept_current_workspace_verification(self, stage: StageCard, success_log: str) -> bool:
        current_check = self.file_checker.verify_current_file_stage_state(stage, self._last_successful_tool_result)
        current_verdict = str((current_check or {}).get("verdict", "")).upper()
        if current_verdict != "VERIFIED":
            return False
        self._last_file_verdict = "VERIFIED"
        self._append_file_checker_note(current_check or {})
        self._append_verified_file_work_result_note(stage, current_check or {}, current_state_only=True)
        self.ui.put(("agent_log", success_log))
        return True

    def _append_file_lookup_note_if_available(self, stage: StageCard) -> None:
        if not FileStagePolicy.stage_requires_targeted_lookup(stage):
            return
        if self._last_successful_tool_name != "FILE_OP" or not isinstance(self._last_successful_tool_result, dict):
            return
        action = str(self._last_successful_tool_result.get("action", "")).lower()
        if action != "find_paths":
            return
        matches = [str(item).strip() for item in (self._last_successful_tool_result.get("matches") or []) if str(item).strip()]
        note = "FILE_LOOKUP_MATCHES:\n" + "\n".join(matches) if matches else "FILE_LOOKUP_MATCHES:\n"
        if note not in self.scratchpad:
            self.scratchpad.append(note)

    def _append_verified_file_work_result_note(
        self,
        stage: StageCard,
        file_check: FileCheckDecision,
        *,
        current_state_only: bool = False,
    ) -> None:
        if not FileStagePolicy.stage_is_file_work(stage):
            return
        tool_result = self._last_successful_tool_result if isinstance(self._last_successful_tool_result, dict) else {}
        tool_name = str(self._last_successful_tool_name or "").upper()
        action = str(tool_result.get("action", "")).lower()
        if tool_name == "FILE_OP" and action in {"read_text", "read_many", "find_paths", "list_tree", "extension_inventory"} and not current_state_only:
            return

        reason = str(file_check.get("reason", "")).strip()
        paths = [str(path).strip() for path in (file_check.get("evidence_files") or []) if str(path).strip()]
        if not paths:
            paths = FileWorkEngine.candidate_paths(tool_result)
        if not paths and not reason:
            return

        payload: dict[str, Any] = {
            "kind": "state_already_satisfied" if current_state_only else "mutation_verified",
            "tool": tool_name or str(tool_result.get("tool", "")).upper(),
            "action": action,
            "paths": paths[:6],
            "summary": str(tool_result.get("summary", "")).strip(),
            "reason": reason,
        }

        file_snippets = tool_result.get("file_snippets") or {}
        if isinstance(file_snippets, dict) and len(paths) == 1:
            snippet = file_snippets.get(paths[0])
            if isinstance(snippet, dict) and str(snippet.get("status", "")).lower() == "text":
                payload["content"] = str(snippet.get("content") or "")

        note = "FILE_WORK_VERIFIED_RESULT: " + json.dumps(payload, ensure_ascii=False)
        if note not in self.scratchpad:
            self.scratchpad.append(note)

    def _maybe_launch_code_session(self, tool_result: Any) -> None:
        if not isinstance(tool_result, dict):
            return
        if str(tool_result.get("tool", "")).upper() != "RUN_CODE":
            return
        if str(tool_result.get("action", "")).lower() != "run_workspace_script":
            return
        if str(tool_result.get("launch_mode", "")).lower() != "embedded_code_tab":
            return
        script_path = str(tool_result.get("launched_script") or "").strip()
        if not script_path:
            return
        self.ui.put(
            (
                "code_session_launch",
                {
                    "path": script_path,
                    "summary": str(tool_result.get("summary") or "").strip(),
                },
            )
        )

    def _memory_remove_already_absent_target(self, stage: StageCard, tool_name: str, tool_result: Any) -> str:
        if str(tool_name or "").upper() != "LIST_KNOWLEDGE":
            return ""
        result_text = tool_result_text(tool_result).strip()
        if not result_text:
            return ""
        return self.state_mutation_engine.memory_remove_listing_confirms_absent(
            stage=stage,
            list_result_text=result_text,
            stage_entries=self.scratchpad,
        )

    def _completion_is_supported_by_non_mutating_file_evidence(self, stage: StageCard) -> bool:
        if not FileStagePolicy.stage_is_non_mutating_file_stage(stage):
            return False
        tool_name = self._last_successful_tool_name
        tool_result = self._last_successful_tool_result
        if FileStagePolicy.stage_requires_analysis_report(stage):
            return FileStagePolicy.is_file_read_result(tool_name, tool_result)
        return FileStagePolicy.non_mutating_file_stage_is_satisfied(stage, tool_name, tool_result)

    def _should_auto_finish_inspection(self, stage: StageCard, tool_spec, tool_result: Any) -> bool:
        if tool_spec is None or tool_spec.name not in {"LIST_TASKS", "LIST_EVENTS", "LIST_KNOWLEDGE"}:
            return False
        if not tool_result_is_success(tool_spec, tool_result):
            return False
        explicit_tools = [str(name).upper() for name in stage.get("allowed_tools", [])]
        if explicit_tools == [tool_spec.name]:
            return True

        stage_text = FileStagePolicy.stage_file_text(stage)
        inspection_keywords = ("fetch", "list", "show", "retrieve", "return", "check", "confirm", "inspect", "identify", "calendar")
        if re.search(r"\b(add|create|schedule|remove|delete|cancel|modify|change)\b", stage_text):
            return False
        return any(keyword in stage_text for keyword in inspection_keywords)

    def _should_run_inspector(self, stage: StageCard, tool_result: Any, tag: str, step: int, decision: PlannerDecision) -> bool:
        """Determines if Inspector is needed."""
        result_text = tool_result_text(tool_result).lower()
        is_fail = "error" in result_text or "failed" in result_text
        if is_fail:
            self._consecutive_fails += 1
        else:
            self._consecutive_fails = 0

        current_sig = tool_signature(tag, tool_result)
        is_repeating = current_sig == self._last_tool_signature
        self._last_tool_signature = current_sig
        self._repeat_count = self._repeat_count + 1 if is_repeating else 0

        file_stage = FileStagePolicy.stage_is_file_work(stage)
        content_edit_stage = FileStagePolicy.stage_is_content_edit_stage(stage)
        list_tree_repeat = (
            str(tag or "").upper() == "FILE_OP"
            and isinstance(tool_result, dict)
            and str(tool_result.get("action", "")).lower() == "list_tree"
            and is_repeating
            and not bool(tool_result.get("workspace_changed"))
        )
        repeated_content_read = (
            content_edit_stage
            and str(tag or "").upper() == "FILE_OP"
            and isinstance(tool_result, dict)
            and str(tool_result.get("action", "")).lower() in {"read_text", "read_many"}
            and is_repeating
            and not bool(tool_result.get("workspace_changed"))
        )
        file_retry_budget = 7 if file_stage else 3
        file_repeat_budget = 3 if file_stage else 1

        if decision.get("is_complete"):
            return True
        if file_stage and self._last_file_verdict == "PARTIAL" and step < file_retry_budget:
            return False
        if list_tree_repeat and step < file_retry_budget:
            self._emit_runtime_signal(
                kind="planner_repeat",
                severity="warning",
                source="executor",
                summary="Planner is repeating unchanged workspace inspection.",
                stage=stage,
                step=step,
                tool=str(tag or "").upper(),
                count=self._repeat_count + 1,
                evidence_files=[str(item) for item in (tool_result.get("evidence_files") or []) if str(item).strip()],
            )
            root = FileStagePolicy.file_op_root(tool_result) or "."
            if FileStagePolicy.stage_is_extension_file_reorg(stage):
                hint = (
                    f"SYSTEM ERROR: Repeated identical list_tree on unchanged root '{root}'. "
                    "Use FILE_OP extension_inventory on the workspace root, then FILE_OP consolidate_by_extension "
                    "or delete_empty_dirs. Repeating list_tree is not progress."
                )
            elif FileStagePolicy.stage_requires_targeted_lookup(stage):
                targets = FileStagePolicy.stage_target_terms(stage)
                query_hint = targets[0] if targets else "<filename>"
                hint = (
                    f"SYSTEM ERROR: Repeated identical list_tree on unchanged root '{root}'. "
                    f"This stage is about a specific file. Use FILE_OP find_paths with query '{query_hint}' "
                    "instead of repeating list_tree."
                )
            else:
                hint = (
                    f"SYSTEM ERROR: Repeated identical list_tree on unchanged root '{root}'. "
                    "Use the existing inventory to plan concrete FILE_OP actions, inspect a different subdirectory, "
                    "or pause for approval. Repeating the same list_tree is not progress."
                )
            if not self.scratchpad or self.scratchpad[-1] != hint:
                self.scratchpad.append(hint)
            self.ui.put(("agent_log", f"   -> {hint}"))
            if self._repeat_count >= 2 and FileStagePolicy.stage_is_broad_file_reorg(stage):
                top_dirs = [str(item) for item in (tool_result.get("top_level_dirs") or [])[:6]]
                top_files = [str(item) for item in (tool_result.get("top_level_files") or [])[:6]]
                observed_bits = []
                if top_dirs:
                    observed_bits.append("Observed directories: " + ", ".join(top_dirs) + ".")
                if top_files:
                    observed_bits.append("Observed root files: " + ", ".join(top_files) + ".")
                pause_note = (
                    "SYSTEM PAUSE: Broad FILE_WORK task is inspection-looping without a reliable target taxonomy. "
                    + " ".join(observed_bits)
                    + " Present a proposal grounded only in this observed structure and ask for user approval before more moves."
                )
                if not self.scratchpad or self.scratchpad[-1] != pause_note:
                    self.scratchpad.append(pause_note)
                self.ui.put(("agent_log", "   -> Pausing broad FILE_WORK stage for proposal/approval instead of rerouting another blind retry."))
                self.pause_requested = True
                self.pause_mode = "approval"
                return True
            return False
        if repeated_content_read and step < file_retry_budget:
            self._emit_runtime_signal(
                kind="planner_repeat",
                severity="warning",
                source="executor",
                summary="Planner is rereading the same unchanged file in an edit stage.",
                stage=stage,
                step=step,
                tool=str(tag or "").upper(),
                count=self._repeat_count + 1,
                evidence_files=[str(item) for item in (tool_result.get("evidence_files") or []) if str(item).strip()],
            )
            read_paths = FileStagePolicy.file_read_paths(tool_result)
            target = read_paths[0] if len(read_paths) == 1 else ", ".join(read_paths[:3]) or "<file>"
            if read_paths and all(path.lower().endswith(".json") for path in read_paths):
                hint = (
                    f"SYSTEM ERROR: Repeated identical read on unchanged file '{target}'. "
                    "You already have the current JSON content in the scratchpad. "
                    "This stage requires changing the artifact, so use FILE_OP update_json or write_json next "
                    "instead of rereading the same file."
                )
            elif FileStagePolicy.paths_are_code_files(read_paths):
                hint = (
                    f"SYSTEM ERROR: Repeated identical read on unchanged code file '{target}'. "
                    "You already have the current source in the scratchpad. "
                    "Use RUN_CODE to read-modify-write the file instead of rereading it or pasting a full program into FILE_OP JSON."
                )
            else:
                hint = (
                    f"SYSTEM ERROR: Repeated identical read on unchanged file '{target}'. "
                    "You already have the current file contents in the scratchpad. "
                    "This stage requires changing the artifact, so compute the final text and use FILE_OP write_text next "
                    "unless the transformation truly requires RUN_CODE."
                )
            if not self.scratchpad or self.scratchpad[-1] != hint:
                self.scratchpad.append(hint)
            self.ui.put(("agent_log", f"   -> {hint}"))
            return False
        if step >= file_retry_budget:
            return True
        if self._consecutive_fails >= 2:
            return True
        if self._repeat_count >= file_repeat_budget:
            return True

        return False

    def _run_inspector(self, stage: StageCard) -> bool:
        return run_inspector(
            llm=self.llm,
            ui=self.ui,
            scratchpad=self.scratchpad,
            stage=stage,
            cancel_token=self.cancel_token,
        )
