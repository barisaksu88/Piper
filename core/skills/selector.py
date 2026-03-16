from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Sequence

from core.contracts import RouteDecision, SkillDecision, StageCard
from core.file_stage_policy import FileStagePolicy


@dataclass(frozen=True)
class SkillSelectionContext:
    user_msg: str
    route_decision: RouteDecision
    recent_history: tuple[dict[str, Any], ...]

    @property
    def decision(self) -> str:
        return str(self.route_decision.get("decision") or "").strip().upper()

    @property
    def card(self) -> dict[str, Any]:
        return dict(self.route_decision.get("card") or {})

    @property
    def stages(self) -> list[StageCard]:
        return [dict(stage) for stage in (self.card.get("stages") or []) if isinstance(stage, dict)]


@dataclass(frozen=True)
class SkillSpec:
    name: str
    family: str
    priority: int
    matcher: Callable[[SkillSelectionContext], tuple[int, str]]
    procedure: tuple[str, ...] = ()
    planner_hint: str = ""
    persona_hint: str = ""


def _stage_goal_text(stage: StageCard) -> str:
    return " ".join(
        part.strip()
        for part in (
            str(stage.get("stage_goal") or ""),
            str(stage.get("success_condition") or ""),
        )
        if part
    ).lower()


def _task_has_stage(ctx: SkillSelectionContext, predicate: Callable[[StageCard], bool]) -> bool:
    return any(predicate(stage) for stage in ctx.stages)


def _task_has_mutating_file_stage(ctx: SkillSelectionContext) -> bool:
    for stage in ctx.stages:
        if not FileStagePolicy.stage_is_file_work(stage):
            continue
        if not FileStagePolicy.stage_is_non_mutating_file_stage(stage):
            return True
    return False


def _task_has_code_targets(ctx: SkillSelectionContext) -> bool:
    for stage in ctx.stages:
        targets = FileStagePolicy.stage_named_file_targets(stage)
        if targets and FileStagePolicy.paths_are_code_files(targets):
            return True
    return False


def _match_search_research(ctx: SkillSelectionContext) -> tuple[int, str]:
    if ctx.decision != "SEARCH":
        return (0, "")
    return (90, "Search turns benefit from a consistent research-and-report workflow.")


def _match_task_event(ctx: SkillSelectionContext) -> tuple[int, str]:
    if ctx.decision != "TASK" or not ctx.stages:
        return (0, "")
    if all(str(stage.get("stage_type") or "").upper() == "TASK_EVENT_WORK" for stage in ctx.stages):
        return (85, "Task/event turns already follow a stable create/update/check workflow.")
    return (0, "")


def _match_workspace_cleanup(ctx: SkillSelectionContext) -> tuple[int, str]:
    if ctx.decision != "TASK":
        return (0, "")
    if _task_has_stage(ctx, FileStagePolicy.stage_is_extension_file_reorg):
        return (110, "Broad extension-based cleanup should use the dedicated inventory/consolidation procedure.")
    return (0, "")


def _match_script_run(ctx: SkillSelectionContext) -> tuple[int, str]:
    if ctx.decision != "TASK":
        return (0, "")
    if _task_has_stage(ctx, FileStagePolicy.stage_is_script_launch_stage):
        return (100, "Script launch turns benefit from a fixed launch-and-report workflow.")
    return (0, "")


def _match_code_fix(ctx: SkillSelectionContext) -> tuple[int, str]:
    if ctx.decision != "TASK":
        return (0, "")
    lowered = str(ctx.user_msg or "").lower()
    if not _task_has_stage(ctx, FileStagePolicy.stage_is_file_work):
        return (0, "")
    if not (
        _task_has_stage(ctx, FileStagePolicy.stage_requires_analysis_report)
        or _task_has_stage(ctx, FileStagePolicy.stage_is_content_edit_stage)
    ):
        return (0, "")
    if _task_has_code_targets(ctx) or any(
        token in lowered for token in ("bug", "error", "fix", "crash", "traceback", "keyboard", "controls", "input handler")
    ):
        return (105, "Code diagnosis and repair work should use a stable inspect-diagnose-edit-verify procedure.")
    return (0, "")


def _match_file_lookup(ctx: SkillSelectionContext) -> tuple[int, str]:
    if ctx.decision != "TASK":
        return (0, "")
    if _task_has_mutating_file_stage(ctx):
        return (0, "")
    if _task_has_stage(ctx, FileStagePolicy.stage_requires_targeted_lookup) or _task_has_stage(
        ctx,
        FileStagePolicy.stage_requires_targeted_read,
    ):
        return (95, "Targeted file reads/lookups should use a direct locate-or-read workflow.")
    return (0, "")


def _match_file_edit(ctx: SkillSelectionContext) -> tuple[int, str]:
    if ctx.decision != "TASK":
        return (0, "")
    if _task_has_stage(ctx, FileStagePolicy.stage_is_content_edit_stage):
        return (92, "Content-edit file work should follow inspect -> mutate -> verify.")
    return (0, "")


SKILL_SPECS: tuple[SkillSpec, ...] = (
    SkillSpec(
        name="workspace_cleanup",
        family="task",
        priority=110,
        matcher=_match_workspace_cleanup,
        procedure=(
            "Inspect the workspace through extension inventory.",
            "Consolidate each extension into a single destination folder.",
            "Delete empty folders only after consolidation is verified.",
        ),
        planner_hint=(
            "Prefer the extension-cleanup workflow: extension_inventory -> consolidate_by_extension -> delete_empty_dirs. "
            "Do not hand-write broad move/copy batches unless the dedicated extension workflow fails."
        ),
        persona_hint=(
            "Report cleanup outcomes in terms of verified workspace state: what was consolidated, whether duplicates were removed, "
            "and whether empty folders were cleared."
        ),
    ),
    SkillSpec(
        name="code_fix",
        family="task",
        priority=105,
        matcher=_match_code_fix,
        procedure=(
            "Inspect the active code artifact and diagnose the concrete issue.",
            "Apply the smallest real fix that satisfies the request.",
            "Verify the artifact state, then run or explain remaining validation limits.",
        ),
        planner_hint=(
            "Use a stable code-fix procedure: inspect -> diagnose -> edit -> verify. "
            "Do not loop on unchanged rereads once the exact source is already in scratchpad."
        ),
        persona_hint=(
            "For code-fix turns, separate diagnosis, applied fix, and proven validation. "
            "If runtime validation is still interactive, say exactly what remains unproven."
        ),
    ),
    SkillSpec(
        name="script_run",
        family="task",
        priority=100,
        matcher=_match_script_run,
        procedure=(
            "Locate or reuse the exact script target.",
            "Launch it through the supported runtime path.",
            "Report launch state honestly and wait for runtime or user feedback for interactive behavior.",
        ),
        planner_hint=(
            "Treat this as a script-launch workflow, not a generic file lookup. "
            "Once the script target is known, proceed to launch instead of re-searching the workspace."
        ),
        persona_hint=(
            "For script-run turns, only claim the script is launched or exited unless runtime evidence proves more."
        ),
    ),
    SkillSpec(
        name="file_lookup",
        family="task",
        priority=95,
        matcher=_match_file_lookup,
        procedure=(
            "Resolve the target file directly.",
            "Read or list only the requested artifact.",
            "Stop once the exact path or exact contents are proven.",
        ),
        planner_hint=(
            "Prefer direct file lookup/read procedures over broad workspace exploration when a target can be inferred."
        ),
        persona_hint=(
            "For file-lookup turns, answer directly from the verified path or exact readback without extra narration."
        ),
    ),
    SkillSpec(
        name="file_edit",
        family="task",
        priority=92,
        matcher=_match_file_edit,
        procedure=(
            "Inspect the current artifact state.",
            "Apply the requested change once.",
            "Verify the final file state before reporting success.",
        ),
        planner_hint=(
            "Use inspect -> mutate -> verify. "
            "Do not stop after the read, and do not repeat unchanged edits without new evidence."
        ),
        persona_hint=(
            "For file-edit turns, report only the verified final artifact state, not an intended change."
        ),
    ),
    SkillSpec(
        name="task_event",
        family="task",
        priority=85,
        matcher=_match_task_event,
        procedure=(
            "Resolve the target task or event.",
            "Apply the requested create/update/completion once.",
            "Report the verified task/event state directly.",
        ),
        planner_hint=(
            "Keep task/event work bounded to the target record. Avoid unrelated file or search detours."
        ),
        persona_hint=(
            "For task/event turns, answer directly from the verified task or event outcome."
        ),
    ),
    SkillSpec(
        name="search_research",
        family="search",
        priority=90,
        matcher=_match_search_research,
        procedure=(
            "Gather search results.",
            "Compare and condense the findings.",
            "Report the final answer from the completed search summary.",
        ),
        planner_hint="",
        persona_hint=(
            "For search turns, synthesize the completed findings instead of drifting into generic chat."
        ),
    ),
)

_PATH_LITERAL_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s\"']+|/mnt/[a-z]/[^\s\"']+|[\w./\\-]+\.[A-Za-z0-9]{1,8})")


def _build_skill_payload(spec: SkillSpec, score: int, reason: str) -> SkillDecision:
    return {
        "name": spec.name,
        "family": spec.family,
        "reason": reason,
        "score": int(score),
        "procedure": list(spec.procedure),
        "planner_hint": spec.planner_hint,
        "persona_hint": spec.persona_hint,
    }


def _refine_skill_payload_for_context(skill: SkillDecision, context: SkillSelectionContext) -> SkillDecision:
    payload = dict(skill)
    if str(payload.get("name") or "").strip() != "file_lookup":
        return payload

    has_lookup = _task_has_stage(context, FileStagePolicy.stage_requires_targeted_lookup)
    has_read = _task_has_stage(context, FileStagePolicy.stage_requires_targeted_read)
    if has_lookup and not has_read:
        payload["procedure"] = [
            "Resolve the likely filename directly.",
            "Return the verified matching path or paths.",
            "Stop once the path match is proven; do not read file contents unless the stage explicitly asks for them.",
        ]
        payload["planner_hint"] = (
            "This is a lookup-only filename/path-resolution workflow. "
            "A verified find_paths match satisfies the stage. "
            "Do not call read_text or read_many after a successful path match unless the stage explicitly requests file contents."
        )
        payload["persona_hint"] = (
            "For lookup-only turns, answer with the verified matching path or paths instead of the file contents."
        )
        return payload
    if has_read and not has_lookup:
        payload["procedure"] = [
            "Resolve the target file directly.",
            "Read the exact contents of the verified artifact.",
            "Stop once the exact readback is proven.",
        ]
        payload["planner_hint"] = (
            "This is an exact-read workflow. "
            "Once the target file is known, read it directly and stop at the verified readback."
        )
        payload["persona_hint"] = (
            "For direct file-read turns, answer from the exact verified readback without extra narration."
        )
    return payload


def select_route_skill(
    route_decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]] | None = None,
) -> SkillDecision | None:
    context = SkillSelectionContext(
        user_msg=str(user_msg or ""),
        route_decision=dict(route_decision or {}),
        recent_history=tuple(dict(item) for item in (recent_history or []) if isinstance(item, dict)),
    )
    best_payload: SkillDecision | None = None
    best_priority = -1
    for spec in SKILL_SPECS:
        score, reason = spec.matcher(context)
        if score <= 0:
            continue
        if spec.priority > best_priority:
            best_payload = _refine_skill_payload_for_context(_build_skill_payload(spec, score, reason), context)
            best_priority = spec.priority
    return best_payload


def _append_unique_context_lines(existing: Any, additions: Sequence[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    raw_existing: list[str]
    if isinstance(existing, list):
        raw_existing = [str(item).strip() for item in existing if str(item).strip()]
    elif str(existing or "").strip():
        raw_existing = [str(existing).strip()]
    else:
        raw_existing = []

    for item in [*raw_existing, *[str(line).strip() for line in additions if str(line).strip()]]:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def _extract_recent_path_hint(context: SkillSelectionContext) -> str:
    for message in reversed(context.recent_history[-8:]):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        matches = [str(item).strip().rstrip(".,;:!?") for item in _PATH_LITERAL_RE.findall(content) if str(item).strip()]
        if matches:
            return matches[-1].replace("\\", "/")
    for stage in context.stages:
        targets = FileStagePolicy.stage_named_file_targets(stage)
        if targets:
            return str(targets[0]).strip()
    return ""


def _lookup_query_hint(context: SkillSelectionContext) -> str:
    recent_path = _extract_recent_path_hint(context)
    if recent_path:
        return recent_path
    for stage in context.stages:
        terms = FileStagePolicy.stage_target_terms(stage)
        if terms:
            return str(terms[0]).strip()
    return "the target file"


def _rewrite_lookup_only_card(card: dict[str, Any], context: SkillSelectionContext, skill: SkillDecision) -> dict[str, Any]:
    query_hint = _lookup_query_hint(context)
    normalized_query = str(query_hint or "the target file").strip()
    updated = dict(card)
    updated["goal"] = f"Find the workspace path that best matches '{normalized_query}'."
    updated["context"] = _append_unique_context_lines(
        updated.get("context"),
        [
            "Lookup-only stage: return the matching path only unless the stage explicitly asks for file contents.",
        ],
    )
    stages = [dict(stage) for stage in (updated.get("stages") or []) if isinstance(stage, dict)]
    rewritten: list[StageCard] = []
    for stage in stages:
        if FileStagePolicy.stage_requires_targeted_lookup(stage) and not FileStagePolicy.stage_requires_targeted_read(stage):
            stage["stage_goal"] = f"Search workspace filenames for files matching '{normalized_query}'."
            stage["success_condition"] = "Matching file paths are identified, or the absence of any plausible filename match is confirmed."
            stage["context"] = _append_unique_context_lines(
                stage.get("context"),
                [
                    "Lookup-only stage: stop after a verified path match.",
                ],
            )
        stage["skill"] = dict(skill)
        rewritten.append(stage)
    updated["stages"] = rewritten
    return updated


def _skill_context_lines(skill: SkillDecision) -> list[str]:
    name = str(skill.get("name") or "").strip()
    lines = [f"Active workflow skill: {name}."] if name else []
    procedure = [str(item).strip() for item in (skill.get("procedure") or []) if str(item).strip()]
    if procedure:
        lines.append("Skill procedure: " + " -> ".join(procedure))
    planner_hint = str(skill.get("planner_hint") or "").strip()
    if planner_hint:
        lines.append("Skill guidance: " + planner_hint)
    return lines


def apply_route_skill_layer(
    route_decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]] | None = None,
    *,
    enabled: bool = True,
) -> RouteDecision:
    decision = dict(route_decision or {})
    if not enabled or not decision:
        return decision

    skill = select_route_skill(decision, user_msg, recent_history)
    if not skill:
        return decision

    decision["skill"] = dict(skill)
    card = dict(decision.get("card") or {})
    if not card:
        return decision

    card["skill"] = dict(skill)
    card["context"] = _append_unique_context_lines(card.get("context"), _skill_context_lines(skill))
    stages = [dict(stage) for stage in (card.get("stages") or []) if isinstance(stage, dict)]
    if (
        str(skill.get("name") or "").strip() == "file_lookup"
        and _task_has_stage(
            SkillSelectionContext(
                user_msg=str(user_msg or ""),
                route_decision=decision,
                recent_history=tuple(dict(item) for item in (recent_history or []) if isinstance(item, dict)),
            ),
            FileStagePolicy.stage_requires_targeted_lookup,
        )
        and not _task_has_stage(
            SkillSelectionContext(
                user_msg=str(user_msg or ""),
                route_decision=decision,
                recent_history=tuple(dict(item) for item in (recent_history or []) if isinstance(item, dict)),
            ),
            FileStagePolicy.stage_requires_targeted_read,
        )
    ):
        decision["card"] = _rewrite_lookup_only_card(card, SkillSelectionContext(
            user_msg=str(user_msg or ""),
            route_decision=decision,
            recent_history=tuple(dict(item) for item in (recent_history or []) if isinstance(item, dict)),
        ), skill)
        return decision
    if stages:
        updated_stages: list[StageCard] = []
        for stage in stages:
            stage["skill"] = dict(skill)
            updated_stages.append(stage)
        card["stages"] = updated_stages
    decision["card"] = card
    return decision
