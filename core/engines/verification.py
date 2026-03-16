"""
VerificationEngine

Status: Active — logic migrated from executor.py, file_checker.py, file_stage_policy.py.
See docs/v1/VERIFICATION_ENGINE.md for the full contract.

Migration completed 2026-03-15.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from core.file_stage_policy import FileStagePolicy

if TYPE_CHECKING:
    from core.contracts import StageCard, StageOutcomePack
    from core.file_checker import FileWorkChecker


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerificationResult:
    """
    Single output from VerificationEngine.

    verdict
        VERIFIED  — stage goal is proven by evidence
        PARTIAL   — some progress happened; completion not proven
        FAILED    — execution failed, blocked, or produced no meaningful evidence

    effective_success
        True only when verdict is VERIFIED.
        PARTIAL is never effective success.

    evidence_summary
        Human-readable description of what proved the verdict, or what failed.
        Populated from checker reason / tool result / filesystem state.

    recommendation
        STOP_SUCCESS  — stage is done; move on
        RETRY         — PARTIAL with retries remaining; planner should try again
        STOP_FAILED   — no retries left, or hard FAILED; stop this stage

    checker_path
        Which path produced this verdict.
        RULES       — deterministic LocalFileOpRuleChecker
        LLM         — LLM file-checker with file_checker.txt template
        STATE_CHECK — current-filesystem fallback (upgrade pass)
        MUTATION    — state mutation outcome from StateMutationEngine
        NONE        — verification not required for this stage
    """

    verdict: Literal["VERIFIED", "PARTIAL", "FAILED"] = "FAILED"
    effective_success: bool = False
    evidence_summary: str = ""
    recommendation: Literal["STOP_SUCCESS", "RETRY", "STOP_FAILED"] = "STOP_FAILED"
    checker_path: Literal["RULES", "LLM", "STATE_CHECK", "MUTATION", "NONE"] = "NONE"

    @classmethod
    def verified(cls, evidence: str, checker_path: str = "RULES") -> "VerificationResult":
        return cls(
            verdict="VERIFIED",
            effective_success=True,
            evidence_summary=evidence,
            recommendation="STOP_SUCCESS",
            checker_path=checker_path,
        )

    @classmethod
    def partial(cls, evidence: str, retry_budget: int, checker_path: str = "RULES") -> "VerificationResult":
        return cls(
            verdict="PARTIAL",
            effective_success=False,
            evidence_summary=evidence,
            recommendation="RETRY" if retry_budget > 0 else "STOP_FAILED",
            checker_path=checker_path,
        )

    @classmethod
    def failed(cls, evidence: str, checker_path: str = "RULES") -> "VerificationResult":
        return cls(
            verdict="FAILED",
            effective_success=False,
            evidence_summary=evidence,
            recommendation="STOP_FAILED",
            checker_path=checker_path,
        )

    @classmethod
    def not_required(cls) -> "VerificationResult":
        """Stage does not require verification — executor continues normally."""
        return cls(
            verdict="VERIFIED",
            effective_success=True,
            evidence_summary="verification not required for this stage type",
            recommendation="STOP_SUCCESS",
            checker_path="NONE",
        )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class VerificationEngine:
    """
    Single owner of the question: "Did this stage succeed, and what is the evidence?"

    Coordinates the checker path priority (RULES → LLM → STATE_CHECK) and emits
    a single VerificationResult with a clear continuation recommendation.

    The executor retains the step loop, planner calls, and scratchpad management.
    This engine owns only the verdict decision and its evidence.
    """

    def __init__(self, file_checker: Optional["FileWorkChecker"] = None) -> None:
        self._file_checker = file_checker

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def should_verify(self, stage: "StageCard", tool_name: str, tool_result: Any = None) -> bool:
        """
        Return True if this stage + tool combination requires verification.

        Delegates to FileStagePolicy which remains as a helper.
        """
        if not FileStagePolicy.stage_requires_file_verification(stage):
            return False
        return FileStagePolicy.tool_requires_file_checker(tool_name, tool_result)

    def evaluate(
        self,
        stage: "StageCard",
        tool_result: Any,
        workspace: Path,
        step: int,
        retry_budget: int,
        *,
        tool_succeeded: bool = False,
    ) -> VerificationResult:
        """
        Main entry point for FILE_WORK stage verification.

        Checker path priority:
        1. RULES       — LocalFileOpRuleChecker (deterministic, no LLM)
        2. LLM         — FileChecker.run_file_checker with file_checker.txt
        3. STATE_CHECK — verify_current_file_stage_state upgrade pass
                         (only attempted when tool_succeeded=True and
                          initial verdict is not already VERIFIED)

        Returns a VerificationResult with verdict + recommendation.
        The executor uses recommendation to decide continue / stop.
        """
        if self._file_checker is None:
            return VerificationResult.failed(
                "VerificationEngine has no file_checker configured.",
                checker_path="RULES",
            )

        # Step 1 & 2: RULES path first, LLM if no rules match
        result = self._run_checker(stage, tool_result, retry_budget)

        # Step 3: STATE_CHECK upgrade pass
        # Only attempt when tool succeeded but verdict is not yet VERIFIED.
        # This catches cases where the tool returned success but the initial
        # checker was PARTIAL or FAILED — reading the actual filesystem state
        # may confirm the goal was met.
        if tool_succeeded and result.verdict != "VERIFIED":
            state_check = self._file_checker.verify_current_file_stage_state(stage, tool_result)
            if state_check:
                state_verdict = str(state_check.get("verdict", "")).upper()
                if state_verdict == "VERIFIED":
                    result = self._map_check_to_result(state_check, retry_budget, "STATE_CHECK")
                elif state_verdict == "PARTIAL" and result.verdict != "VERIFIED":
                    # Upgrade from FAILED to PARTIAL if the state check is better
                    result = self._map_check_to_result(state_check, retry_budget, "STATE_CHECK")

        return result

    def evaluate_mutation(
        self,
        stage: "StageCard",
        outcome_pack: "StageOutcomePack",
    ) -> VerificationResult:
        """
        Verification for state mutation stages (MEMORY_WORK, task/event mutations).

        No filesystem evidence — verdict comes from the StateMutationEngine
        outcome pack. Produces the same VerificationResult shape as evaluate()
        so the executor has a single verdict interface regardless of stage type.
        """
        effective = bool(getattr(outcome_pack, "effective_success", False))
        detail = str(
            getattr(outcome_pack, "detail", "") or getattr(outcome_pack, "status", "")
        ).strip() or ("Mutation succeeded." if effective else "Mutation failed.")
        if effective:
            return VerificationResult.verified(detail, checker_path="MUTATION")
        return VerificationResult.failed(detail, checker_path="MUTATION")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_checker(
        self,
        stage: "StageCard",
        tool_result: Any,
        retry_budget: int,
    ) -> VerificationResult:
        """
        Coordinate RULES → LLM checker path selection.

        Tries the deterministic rule checker first. Falls back to the LLM
        checker only when no rule matches. The checker_path field in the
        result records which path produced the verdict.
        """
        # RULES: deterministic, no LLM call
        local_check = self._file_checker.run_local_file_op_checker(stage, tool_result)
        if local_check is not None:
            return self._map_check_to_result(local_check, retry_budget, "RULES")

        # LLM: file_checker.txt template with stage + evidence
        # (run_file_checker re-calls run_local_file_op_checker internally,
        #  gets None again, then proceeds to LLM — one redundant rule check,
        #  but correct and avoids splitting run_file_checker's internal logic)
        llm_check = self._file_checker.run_file_checker(stage, tool_result)
        return self._map_check_to_result(llm_check, retry_budget, "LLM")

    @staticmethod
    def _map_check_to_result(
        file_check: dict,
        retry_budget: int,
        checker_path: str,
    ) -> VerificationResult:
        """Convert a FileCheckDecision dict into a VerificationResult."""
        verdict = str(file_check.get("verdict", "FAILED")).upper()
        reason = str(file_check.get("reason", "")).strip() or "No checker reason provided."
        if verdict == "VERIFIED":
            return VerificationResult.verified(reason, checker_path=checker_path)
        if verdict == "PARTIAL":
            return VerificationResult.partial(reason, retry_budget=retry_budget, checker_path=checker_path)
        return VerificationResult.failed(reason, checker_path=checker_path)
