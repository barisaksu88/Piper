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


PlanConstraintType = Literal[
    "EXCLUSION",   # no file matching pattern should exist in scope
    "MOVED",       # file should exist at to_path, not at from_path
    "DELETED",     # file/dir should not exist
    "CREATED",     # file/dir should exist
    "MODIFIED",    # file content must satisfy expected_present / expected_absent
    "COUNT",       # directory should contain exactly N files
]


ComputerUseBackend = Literal["browser"]
ComputerUseGoalKind = Literal["navigate", "extract", "form_fill", "download"]


class ComputerUseRequest(TypedDict, total=False):
    backend: ComputerUseBackend
    start_url: str
    allowed_domains: List[str]
    goal_kind: ComputerUseGoalKind
    download_dir: str
    download_hint: str
    selector_hint: str
    requested_topic: str
    input_text: str
    expected_text: str
    submit_requested: bool
    require_download: bool
    require_extract: bool
    require_form_fill: bool
    require_navigation: bool
    report_title: bool
    report_status_text: bool
    navigation_hint: str


class PlanConstraint(TypedDict, total=False):
    type: PlanConstraintType
    scope: Literal["FILE", "FILENAME", "DIRECTORY", "EXTENSION"]
    path: str               # used by DELETED, CREATED, MODIFIED, COUNT
    from_path: str          # MOVED: source path
    to_path: str            # MOVED: destination path
    pattern: str            # EXCLUSION / FILENAME scope: substring to match
    directory: str          # EXCLUSION: restrict search to this subdirectory
    expected: int           # COUNT: expected number of files
    expected_present: List[str]   # MODIFIED: text that must appear in file
    expected_absent: List[str]    # MODIFIED: text that must not appear in file


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
    dependency_override_authorized: bool
    file_stage_kind: FileStageKind
    mutation: StateMutationRequest
    computer_use: ComputerUseRequest
    skill: "SkillDecision"
    # Planner-boundary fields (may be set by router or filled by PlannerBoundary.validate_input)
    objective: str          # Parent route-card goal; why this workflow exists
    active_targets: List[str]   # Files or entities being acted on this stage
    evidence_required: str      # What constitutes verified completion (defaults to success_condition)
    constraints: List[PlanConstraint]   # Optional typed success constraints (router or planner emitted)


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
    source_scope: Literal["web", "workspace", "unknown"]
    confidence: Literal["low", "medium", "high"]
    question_if_uncertain: str


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
    allow_persona_reroute: bool = True


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
class RouteClarifierResolution:
    decision: Literal["keep_task", "clarify_chat"] = "keep_task"
    question: str = ""
    reason: str = ""


PersonaTurnType = Literal[
    "CHAT",
    "TASK",
    "DOC_FOCUS",
    "SEARCH_FIRST_PASS",
    "REPORTER",
    "EXPLAIN",
    "PROACTIVE_TRIGGER",
]


@dataclass(frozen=True)
class PersonaArbitrationProfile:
    primary: tuple[str, ...] = ()
    secondary: tuple[str, ...] = ()
    suppressed: tuple[str, ...] = ()


PERSONA_CONTEXT_ARBITRATION_TABLE: Dict[PersonaTurnType, PersonaArbitrationProfile] = {
    "CHAT": PersonaArbitrationProfile(
        primary=("[ENVIRONMENT]", "[RETRIEVED MEMORY]"),
        secondary=("[WORLD STATE]", "[SITUATIONAL STATE]", "[OPERATIONAL STATE]"),
        suppressed=("[DOCUMENT MATCHES]", "[EXPLAIN_LAST_TURN]"),
    ),
    "TASK": PersonaArbitrationProfile(
        primary=("[FINAL_STAGE_OUTCOME]", "[OPERATIONAL STATE]"),
        secondary=("[WORLD STATE]", "[RETRIEVED MEMORY]"),
        suppressed=("[DOCUMENT MATCHES]", "[PATTERN HINTS]"),
    ),
    "DOC_FOCUS": PersonaArbitrationProfile(
        primary=("[DOCUMENT FOCUS]",),
        secondary=("[INTENT STATE]", "[RETRIEVED MEMORY]"),
        suppressed=("[WORLD STATE]", "[SITUATIONAL STATE]", "[PATTERN HINTS]"),
    ),
    "SEARCH_FIRST_PASS": PersonaArbitrationProfile(
        primary=("[ENVIRONMENT]", "[RETRIEVED MEMORY]"),
        secondary=("[WORLD STATE]",),
        suppressed=("[DOCUMENT MATCHES]", "[OPERATIONAL STATE]"),
    ),
    "REPORTER": PersonaArbitrationProfile(
        primary=("[SEARCH_REPORT_RULE]", "[SEARCH SUMMARY]"),
        secondary=("[RETRIEVED MEMORY]",),
        suppressed=("[WORLD STATE]", "[SITUATIONAL STATE]", "[PATTERN HINTS]"),
    ),
    "EXPLAIN": PersonaArbitrationProfile(
        primary=("[EXPLAIN_LAST_TURN]",),
        secondary=(),
        suppressed=("[ALL OTHER BLOCKS]",),
    ),
    "PROACTIVE_TRIGGER": PersonaArbitrationProfile(
        primary=("[PROACTIVE_TRIGGER]",),
        secondary=("[OPERATIONAL STATE]",),
        suppressed=("[ALL OTHER BLOCKS]",),
    ),
}


@dataclass(frozen=True)
class UiEvent:
    kind: str
    payload: Any = ""


@dataclass(frozen=True)
class PromptContext:
    instructions: str = ""
    style_overlay: str = ""
    active_user_block: str = ""
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
    active_user_block: str = ""
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
    allow_persona_reroute: bool = True
    proposal_answer: str = ""
    analysis_report_answer: str = ""
    exact_file_read_answer: str = ""
    file_lookup_answer: str = ""
    verified_file_work_answer: str = ""
    verified_browser_answer: str = ""
    latest_stage_requires_analysis_report: bool = False
    latest_stage_is_targeted_read: bool = False
    latest_stage_is_targeted_lookup: bool = False
    needs_file_work_report_rule: bool = False
    # Typed verification result surfaced directly from VerificationEngine —
    # not inferred from scratchpad text.  Empty string when not evaluated.
    verification_verdict: str = ""      # "VERIFIED", "PARTIAL", "FAILED", or ""
    verification_evidence: str = ""     # evidence_summary from VerificationResult
    verification_recommendation: str = ""  # STOP_SUCCESS | RETRY | STOP_FAILED
    verification_checker_path: str = ""    # RULES | LLM | STATE_CHECK | MUTATION | NONE


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

    fatal=True means the block cannot be resolved by the planner retrying
    (e.g. cross-domain dependency on DELETE/MOVE) — the executor must stop
    the entire stage immediately rather than continuing the step loop.
    """

    blocked: bool = False
    reason: str = ""
    fatal: bool = False
