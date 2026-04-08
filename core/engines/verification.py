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
        # Constraint-first path: derive typed constraints from stage + tool result,
        # check each against the actual filesystem.  No LLM call needed.
        # Falls through to RULES → LLM when no constraints are derivable.
        from core.engines.file_work import FileWorkEngine as _FileWorkEngine
        _constraints = _FileWorkEngine.derive_constraints(stage, tool_result)
        if _constraints:
            _constraint_result = self.evaluate_with_constraints(_constraints, workspace)
            if _constraint_result is not None:
                return _constraint_result

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

    # ------------------------------------------------------------------
    # Constraint-based verification
    # ------------------------------------------------------------------

    def evaluate_with_constraints(
        self,
        constraints: list[dict],
        workspace: "Path",
    ) -> "VerificationResult | None":
        """Evaluate a PlanConstraint list against actual filesystem state.

        Returns None when the list is empty or contains only unknown/skipped
        constraint types — caller should fall through to RULES → LLM path.
        Returns a VerificationResult when at least one constraint is evaluable.
        A single failed constraint produces FAILED with a specific reason.
        """
        if not constraints:
            return None

        failures: list[str] = []
        passed: list[str] = []

        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            ctype = str(constraint.get("type") or "").upper()
            if ctype == "EXCLUSION":
                r = self._check_exclusion(constraint, workspace)
            elif ctype == "MOVED":
                r = self._check_moved(constraint, workspace)
            elif ctype == "DELETED":
                r = self._check_deleted(constraint, workspace)
            elif ctype == "CREATED":
                r = self._check_created(constraint, workspace)
            elif ctype == "MODIFIED":
                r = self._check_modified(constraint, workspace)
            elif ctype == "COUNT":
                r = self._check_count(constraint, workspace)
            else:
                continue  # unknown type — skip, don't count as passed or failed
            if r["passed"]:
                passed.append(r["reason"])
            else:
                failures.append(r["reason"])

        if not passed and not failures:
            return None  # nothing evaluable — fall through

        if failures:
            evidence = "; ".join(failures[:3])
            if len(failures) > 3:
                evidence += f" (and {len(failures) - 3} more)"
            return VerificationResult.failed(evidence, checker_path="RULES")

        evidence = f"All {len(passed)} constraint(s) satisfied: " + "; ".join(passed[:3])
        return VerificationResult.verified(evidence, checker_path="RULES")

    @staticmethod
    def _check_exclusion(constraint: dict, workspace: "Path") -> dict:
        pattern = str(constraint.get("pattern") or "").strip()
        directory = str(constraint.get("directory") or "").strip()
        if not pattern:
            return {"passed": True, "reason": "EXCLUSION: no pattern — skipped"}
        search_root = (workspace / directory) if directory else workspace
        if not search_root.exists():
            return {"passed": True, "reason": f"EXCLUSION: '{directory or '.'}' absent — treated as empty"}
        matches = [
            str(p.relative_to(workspace).as_posix())
            for p in search_root.rglob("*")
            if p.is_file() and pattern.lower() in p.name.lower()
        ]
        if matches:
            sample = ", ".join(f"'{m}'" for m in matches[:3])
            return {"passed": False, "reason": f"EXCLUSION failed: {len(matches)} file(s) matching '{pattern}' still present: {sample}"}
        scope_label = f"'{directory}'" if directory else "workspace"
        return {"passed": True, "reason": f"EXCLUSION: no files matching '{pattern}' in {scope_label}"}

    @staticmethod
    def _check_moved(constraint: dict, workspace: "Path") -> dict:
        from_path = str(constraint.get("from_path") or "").strip()
        to_path = str(constraint.get("to_path") or "").strip()
        if not from_path or not to_path:
            return {"passed": True, "reason": "MOVED: incomplete paths — skipped"}
        src = workspace / from_path
        dst = workspace / to_path
        dst_ok = dst.exists()
        src_gone = not src.exists()
        if dst_ok and src_gone:
            return {"passed": True, "reason": f"MOVED: '{from_path}' → '{to_path}' verified"}
        reasons = []
        if not dst_ok:
            reasons.append(f"'{to_path}' missing at destination")
        if not src_gone:
            reasons.append(f"'{from_path}' still present at source")
        return {"passed": False, "reason": "MOVED failed: " + "; ".join(reasons)}

    @staticmethod
    def _check_deleted(constraint: dict, workspace: "Path") -> dict:
        path = str(constraint.get("path") or "").strip()
        if not path:
            return {"passed": True, "reason": "DELETED: no path — skipped"}
        if (workspace / path).exists():
            return {"passed": False, "reason": f"DELETED failed: '{path}' still exists"}
        return {"passed": True, "reason": f"DELETED: '{path}' confirmed absent"}

    @staticmethod
    def _check_created(constraint: dict, workspace: "Path") -> dict:
        path = str(constraint.get("path") or "").strip()
        if not path:
            return {"passed": True, "reason": "CREATED: no path — skipped"}
        if not (workspace / path).exists():
            return {"passed": False, "reason": f"CREATED failed: '{path}' not found"}
        return {"passed": True, "reason": f"CREATED: '{path}' confirmed present"}

    @staticmethod
    def _check_modified(constraint: dict, workspace: "Path") -> dict:
        path = str(constraint.get("path") or "").strip()
        if not path:
            return {"passed": True, "reason": "MODIFIED: no path — skipped"}
        target = workspace / path
        if not target.is_file():
            return {"passed": False, "reason": f"MODIFIED failed: '{path}' not found"}
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"passed": False, "reason": f"MODIFIED failed: could not read '{path}': {exc}"}
        for text in [str(t) for t in (constraint.get("expected_present") or []) if str(t).strip()]:
            if text not in content:
                return {"passed": False, "reason": f"MODIFIED failed: '{path}' missing expected text '{text[:60]}'"}
        for text in [str(t) for t in (constraint.get("expected_absent") or []) if str(t).strip()]:
            if text in content:
                return {"passed": False, "reason": f"MODIFIED failed: '{path}' still contains '{text[:60]}'"}
        return {"passed": True, "reason": f"MODIFIED: '{path}' content verified"}

    @staticmethod
    def _check_count(constraint: dict, workspace: "Path") -> dict:
        path = str(constraint.get("path") or "").strip()
        expected = constraint.get("expected")
        if not path or expected is None:
            return {"passed": True, "reason": "COUNT: incomplete — skipped"}
        target = workspace / path
        if not target.exists():
            actual = 0
        elif target.is_dir():
            actual = sum(1 for _ in target.iterdir() if _.is_file())
        else:
            actual = 1 if target.is_file() else 0
        try:
            expected_int = int(expected)
        except (TypeError, ValueError):
            return {"passed": True, "reason": "COUNT: non-integer expected — skipped"}
        if actual == expected_int:
            return {"passed": True, "reason": f"COUNT: '{path}' has {actual} file(s) as expected"}
        return {"passed": False, "reason": f"COUNT failed: '{path}' has {actual} file(s), expected {expected_int}"}
