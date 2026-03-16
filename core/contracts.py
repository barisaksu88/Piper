from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict

FileStageKind = Literal[
    "INSPECTION",
    "CONTENT_EDIT",
    "STRUCTURE_PREP",
    "BROAD_REORG",
    "SCRIPT_LAUNCH",
    "DEPENDENCY_RECOVERY",
    "UNKNOWN",
]


class ChatMessage(TypedDict, total=False):
    role: str
    content: str
    hidden: bool


class StateMutationRequest(TypedDict, total=False):
    state_owner: Literal["task_event", "world_model", "transient_state", "intent_state"]
    entity_kind: Literal["task", "event", "knowledge", "situational", "intent"]
    action: Literal["add", "delete", "complete", "schedule", "remove", "store", "inspect"]
    target: str
    value: str
    scheduled_date: str


class StageCard(TypedDict, total=False):
    stage_goal: str
    stage_type: str
    success_condition: str
    allowed_tools: List[str]
    mutation: StateMutationRequest
    skill: "SkillDecision"
    # Planner-boundary fields (may be set by router or filled by PlannerBoundary.validate_input)
    objective: str          # Parent route-card goal; why this workflow exists
    active_targets: List[str]   # Files or entities being acted on this stage
    evidence_required: str      # What constitutes verified completion (defaults to success_condition)


class RouteCard(TypedDict, total=False):
    goal: str
    query: str
    context: Any
    stages: List[StageCard]
    skill: "SkillDecision"


class SkillDecision(TypedDict, total=False):
    name: str
    family: Literal["chat", "search", "task"]
    reason: str
    score: int
    procedure: List[str]
    planner_hint: str
    persona_hint: str


class RouteDecision(TypedDict, total=False):
    decision: Literal["CHAT", "SEARCH", "TASK"]
    card: RouteCard
    skill: SkillDecision


class PlannerDecision(TypedDict, total=False):
    thought: str
    tool: Optional[str]
    is_complete: bool
    proposal: str
    # Explicit output contract fields (normalized by PlannerBoundary.normalize_output)
    clarification_requested: bool   # True when the planner needs user input before continuing
    stop_recommended: bool          # True when the planner believes the stage is unrecoverable


class ToolResult(TypedDict, total=False):
    tag: str
    payload: Optional[str]
    result: Any
    success: bool


class FileCheckDecision(TypedDict, total=False):
    verdict: Literal["VERIFIED", "PARTIAL", "FAILED"]
    reason: str
    evidence_files: List[str]


class RuntimeSignal(TypedDict, total=False):
    kind: str
    severity: Literal["info", "warning", "error"]
    source: str
    summary: str
    details: str
    stage_goal: str
    stage_type: str
    step: int
    tool: str
    count: int
    evidence_files: List[str]


class EscalationDecision(TypedDict, total=False):
    decision: Literal["monitor", "ask_codex"]
    reason: str
    summary: str
    brief_path: str
    manual: bool
    signal_count: int
    trigger_kind: str


@dataclass(frozen=True)
class StateMutationIntent:
    decision: Literal["none", "chat_correction", "complete_task", "complete_event", "inspect_event"] = "none"
    subject: str = ""
    reason: str = ""


@dataclass(frozen=True)
class KnowledgeMutationIntent:
    decision: Literal["none", "query_knowledge", "store_knowledge", "remove_knowledge"] = "none"
    subject: str = ""
    value: str = ""
    reason: str = ""


@dataclass(frozen=True)
class StageOutcomePack:
    status: str = ""
    detail: str = ""
    effective_success: bool = False
    state_owner: str = ""
    mutation_kind: str = ""
    auto_reroute: bool = False
    reroute_reason: str = ""


@dataclass(frozen=True)
class StateReadonlyPack:
    answer: str = ""
    state_owner: str = ""
    query_kind: str = ""


@dataclass(frozen=True)
class FollowupResolution:
    decision: Literal[
        "keep_route",
        "chat",
        "clarify",
        "complete_task",
        "delete_task",
        "complete_event",
        "delete_event",
        "store_knowledge",
        "remove_knowledge",
        "query_tasks",
        "query_events",
        "query_tasks_and_events",
        "query_memory",
    ] = "keep_route"
    target: str = ""
    value: str = ""
    query: str = ""
    question: str = ""
    confidence: Literal["low", "medium", "high"] = "low"
    reason: str = ""


@dataclass(frozen=True)
class UiEvent:
    kind: str
    payload: Any = ""


@dataclass(frozen=True)
class PromptContext:
    instructions: str = ""
    style_overlay: str = ""
    knowledge: Dict[str, Any] = field(default_factory=dict)
    world_state: str = ""
    situational_state: str = ""
    intent_state: str = ""
    operational_state: str = ""
    env_block: str = ""
    brain_hits: List[Dict[str, Any]] = field(default_factory=list)
    vision_notes: List[str] = field(default_factory=list)
    document_hits: List[Dict[str, Any]] = field(default_factory=list)
    document_focus: str = ""
    document_references: List[str] = field(default_factory=list)
    document_sources: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PersonaContextPack:
    user_msg: str = ""
    knowledge_enabled: bool = True
    instructions: str = ""
    style_overlay: str = ""
    knowledge: Dict[str, Any] = field(default_factory=dict)
    world_state: str = ""
    situational_state: str = ""
    intent_state: str = ""
    operational_state: str = ""
    env_block: str = ""
    brain_hits: List[Dict[str, Any]] = field(default_factory=list)
    vision_notes: List[str] = field(default_factory=list)
    document_hits: List[Dict[str, Any]] = field(default_factory=list)
    document_focus: str = ""
    document_references: List[str] = field(default_factory=list)
    document_sources: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeContextPack:
    previous_route: str = ""
    previous_user_request: str = ""
    task_goal: str = ""
    search_query: str = ""
    execution_status: str = ""
    runtime_note: str = ""
    relevant_paths: List[str] = field(default_factory=list)
    reporter_just_ran: bool = False


@dataclass(frozen=True)
class PersonaRuntimePack:
    outcome_block: str = ""
    outcome_failed: bool = False
    outcome_paused: bool = False
    proposal_answer: str = ""
    analysis_report_answer: str = ""
    exact_file_read_answer: str = ""
    file_lookup_answer: str = ""
    verified_file_work_answer: str = ""
    latest_stage_requires_analysis_report: bool = False
    latest_stage_is_targeted_read: bool = False
    latest_stage_is_targeted_lookup: bool = False
    needs_file_work_report_rule: bool = False


@dataclass(frozen=True)
class PersonaDirectivePack:
    tail_system_blocks: List[str] = field(default_factory=list)
    direct_answer: str = ""


@dataclass(frozen=True)
class FileWorkEvidence:
    """Collected evidence from a file/code tool result.

    Returned by FileWorkEngine.collect_evidence().
    """

    candidate_paths: List[str] = field(default_factory=list)
    artifact_view: str = ""
    exact_read_note: str = ""


@dataclass(frozen=True)
class FileWorkBlock:
    """Result of FileWorkEngine.should_block().

    blocked=True means the proposed tool call must be suppressed and
    reason inserted into the scratchpad as a SYSTEM ERROR.
    """

    blocked: bool = False
    reason: str = ""
