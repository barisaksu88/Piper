"""planner_boundary.py
~~~~~~~~~~~~~~~~~~~~~
Formal contract enforcement for the planner loop boundary.

Validates stage inputs before the executor LLM loop begins, resolves tools,
and normalizes planner decisions into explicitly-typed outputs.

This is a **contract enforcement module**, not an engine.  It owns no state
and makes no LLM calls — it only validates, resolves, and normalizes.

Planner-boundary inputs (from §3.1 of EXECUTION_ROADMAP.md):
    - objective          — parent route-card goal (why this workflow exists)
    - stage_goal         — this stage's specific goal (required, non-empty)
    - success_condition  — what proves the stage is done (required, non-empty)
    - stage_type         — domain: FILE_WORK, CHAT, SEARCH, etc.
    - allowed_tools      — resolved and validated tool list
    - active_targets     — files/entities being acted on (extracted if absent)
    - evidence_required  — what constitutes verified completion proof

Planner-boundary outputs (from §3.1):
    - thought                 — planner's reasoning trace
    - tool                    — chosen tool tag (or None)
    - is_complete             — stage done signal
    - clarification_requested — planner needs user input before continuing
    - stop_recommended        — planner believes stage is unrecoverable
    - proposal                — user-facing text for clarification or completion note
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from core.contracts import PlannerDecision, StageCard
from core.engines.file_work import FileWorkEngine
from core.stage_policy import stage_is_chat
from tools.registry import resolve_domain_tools

# ---------------------------------------------------------------------------
# Target extraction
# ---------------------------------------------------------------------------

# File extensions considered "active targets" worth tracking.
_TARGET_EXTENSIONS: str = (
    r"py|js|ts|jsx|tsx|json|yaml|yml|toml|cfg|ini"
    r"|txt|md|csv|html|css|sh|bat|ps1"
    r"|java|cs|cpp|c|h|rs|go|rb|lua|php"
)
_TARGET_RE: re.Pattern = re.compile(
    r"\b[\w./\\-]+\.(?:" + _TARGET_EXTENSIONS + r")\b",
    re.IGNORECASE,
)


def _extract_targets(text: str) -> List[str]:
    """Heuristically extract file/path targets from free-form stage text."""
    seen: set[str] = set()
    result: List[str] = []
    for match in _TARGET_RE.finditer(text):
        token = match.group(0)
        key = token.lower()
        if key not in seen:
            seen.add(key)
            result.append(token)
    return result


# ---------------------------------------------------------------------------
# Typed input/output contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannerInput:
    """All required inputs to the planner LLM loop, validated and resolved.

    Produced by ``PlannerBoundary.validate_input()``.  Every field is
    non-None and non-empty (or an empty list for ``active_targets`` when no
    targets can be identified from stage text).
    """

    objective: str          # Parent route-card goal; why this workflow exists
    stage_goal: str         # This stage's specific goal (required)
    stage_type: str         # Domain: FILE_WORK, CHAT, SEARCH, etc.
    success_condition: str  # What proves the stage is done (required)
    allowed_tools: List[str] = field(default_factory=list)
    active_targets: List[str] = field(default_factory=list)
    evidence_required: str = ""  # Defaults to success_condition when absent


@dataclass(frozen=True)
class PlannerOutput:
    """Normalized, explicitly-typed output from one planner LLM step.

    Produced by ``PlannerBoundary.normalize_output()``.  The ambiguous
    ``is_complete + proposal`` pattern is resolved into explicit flags so
    callers don't have to infer intent from English text.
    """

    thought: str
    tool: Optional[str]
    is_complete: bool
    clarification_requested: bool   # Planner needs user input before continuing
    stop_recommended: bool          # Planner believes stage is unrecoverable
    proposal: str                   # User-facing text for clarif. or completion note


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------

class PlannerBoundary:
    """Validates planner inputs and normalizes planner outputs.

    All methods are static — this class exists as a named namespace for the
    contract, not as a stateful object.
    """

    # Heuristic: a proposal ending with '?' strongly suggests a clarification
    # question rather than a completion note, even when is_complete is True.
    _CLARIFICATION_SUFFIXES: tuple[str, ...] = ("?",)

    @staticmethod
    def validate_input(stage: StageCard, objective: str = "") -> PlannerInput:
        """Validate and resolve all planner inputs from a stage card.

        Fills missing optional fields with reasonable defaults.
        Raises ``ValueError`` for required fields that are empty.

        Side-effect: writes resolved ``allowed_tools`` back into ``stage``
        so the executor's existing prompt-build path sees the same list.
        """
        stage_goal = str(stage.get("stage_goal", "") or "").strip()
        if not stage_goal:
            raise ValueError(
                "PlannerBoundary: stage_goal is required and must be non-empty"
            )

        success_condition = str(stage.get("success_condition", "") or "").strip()
        if not success_condition:
            raise ValueError(
                "PlannerBoundary: success_condition is required and must be non-empty"
            )

        stage_type = str(stage.get("stage_type", "FILE_WORK") or "FILE_WORK").strip().upper()

        # --- Tool resolution (moved here from executor.run lines 231-243) ---
        if stage_is_chat(stage):
            # CHAT stages never expose runtime tools.
            allowed_tools: List[str] = []
        else:
            allowed_tools = list(stage.get("allowed_tools", []) or [])
            if not allowed_tools:
                allowed_tools = resolve_domain_tools(stage_type)
            if not allowed_tools:
                allowed_tools = ["RUN_CODE"]  # Safe fallback
        # Write back so the executor's prompt builder sees the resolved list.
        stage["allowed_tools"] = list(allowed_tools)

        # --- Active targets: explicit or extracted from free-form text ---
        active_targets: List[str] = list(stage.get("active_targets", []) or [])
        if not active_targets:
            target_text = stage_goal + " " + success_condition
            active_targets = _extract_targets(target_text)

        # --- Evidence required: explicit or defaults to success_condition ---
        evidence_required = str(stage.get("evidence_required", "") or "").strip()
        if not evidence_required:
            evidence_required = success_condition

        objective_text = str(objective or stage.get("objective", "") or "").strip()

        # Write the normalized contract back into the stage card so every
        # downstream consumer sees the same resolved planner boundary.
        stage["objective"] = objective_text
        stage["stage_type"] = stage_type
        stage["active_targets"] = list(active_targets)
        stage["evidence_required"] = evidence_required
        if stage_type == "FILE_WORK":
            file_stage_kind = str(stage.get("file_stage_kind", "") or "").strip().upper()
            if file_stage_kind not in {
                "INSPECTION",
                "CONTENT_EDIT",
                "STRUCTURE_PREP",
                "BROAD_REORG",
                "SCRIPT_LAUNCH",
                "DEPENDENCY_RECOVERY",
                "UNKNOWN",
            }:
                stage["file_stage_kind"] = FileWorkEngine.classify(stage)

        return PlannerInput(
            objective=objective_text,
            stage_goal=stage_goal,
            stage_type=stage_type,
            success_condition=success_condition,
            allowed_tools=allowed_tools,
            active_targets=active_targets,
            evidence_required=evidence_required,
        )

    @staticmethod
    def normalize_output(decision: PlannerDecision) -> PlannerOutput:
        """Normalize a raw ``PlannerDecision`` into explicit typed output.

        Resolves the ambiguous ``is_complete + proposal`` pattern:
        - If the planner explicitly sets ``clarification_requested`` → honour it.
        - Otherwise, if ``is_complete`` is True and the proposal ends with '?'
          → infer clarification (question, not completion note).
        """
        thought = str(decision.get("thought", "") or "")
        raw_tool = decision.get("tool") or None
        tool: Optional[str] = str(raw_tool).strip() if raw_tool else None
        is_complete = bool(decision.get("is_complete", False))
        proposal = str(decision.get("proposal", "") or "")

        # Explicit flag wins; otherwise infer from proposal shape.
        clarification_requested = bool(decision.get("clarification_requested", False))
        if not clarification_requested and is_complete and proposal:
            if any(proposal.strip().endswith(s) for s in PlannerBoundary._CLARIFICATION_SUFFIXES):
                clarification_requested = True

        stop_recommended = bool(decision.get("stop_recommended", False))

        return PlannerOutput(
            thought=thought,
            tool=tool,
            is_complete=is_complete,
            clarification_requested=clarification_requested,
            stop_recommended=stop_recommended,
            proposal=proposal,
        )
