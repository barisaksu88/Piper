"""core/graph_nodes.py

LangGraph node implementations for the Piper orchestrator.

Phase 1 — ROUTE node extraction.
Phase 2 — MANAGER node extraction.
Phase 3 — VERIFY + PERSONA node extraction.
Phase 5 — Interrupt integration (AWAIT_INTERRUPT node + resume helpers).

Each node is a pure function PiperState -> PiperState that delegates to the
existing orchestrator logic.  This lets us verify behavior against the golden
corpus before any wiring changes.
"""

from __future__ import annotations

from typing import Any, TypedDict

try:
    from langgraph.graph.message import add_messages
except ImportError:  # pragma: no cover
    def add_messages(left, right):  # type: ignore[no-redef]
        return list(left or []) + list(right or [])


class PiperState(TypedDict, total=False):
    """LangGraph state schema — mirrors current orchestrator state."""

    messages: Any  # Annotated[list, add_messages] at runtime
    stage: str
    route_decision: dict[str, Any] | None
    manager_result: dict[str, Any] | None
    verification_passed: bool
    pre_persona_output: str | None
    persona_output: str | None
    workspace_path: str
    interrupt_payload: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Helper: resume text extraction
# ---------------------------------------------------------------------------


def _resume_text(resume_value: Any) -> str:
    if isinstance(resume_value, dict):
        for key in ("user_msg", "text", "answer", "response", "resume"):
            value = resume_value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(resume_value or "").strip()


# ---------------------------------------------------------------------------
# ROUTE
# ---------------------------------------------------------------------------


def route_node(state: PiperState, config=None) -> PiperState:
    """ROUTE stage — decide which domain handles the user input."""
    runtime = (config or {}).get("configurable", {}) if config else {}
    orc = runtime.get("orchestrator")
    if orc is None:
        raise RuntimeError(
            "route_node requires an orchestrator instance in config['configurable']['orchestrator']. "
            "Pass it when building the graph or invoking the node."
        )

    from core.orchestrator_phases import _run_route_core  # local: avoid circular import at module load

    _run_route_core(orc)
    return {
        **state,
        "stage": "ROUTE",
        "route_decision": dict(orc.route_decision) if getattr(orc, "route_decision", None) else None,
    }


def _dispatch_stage_node(state: PiperState, config, *, stage_name: str) -> PiperState:
    runtime = (config or {}).get("configurable", {}) if config else {}
    orc = runtime.get("orchestrator")
    if orc is None:
        raise RuntimeError(
            f"{stage_name.lower()}_node requires an orchestrator instance in "
            "config['configurable']['orchestrator']. Pass it when building the graph or invoking the node."
        )

    orc.dispatch_stage(stage_name)
    return {
        **state,
        "stage": stage_name,
        "route_decision": dict(orc.route_decision) if getattr(orc, "route_decision", None) else None,
    }


def document_focus_node(state: PiperState, config=None) -> PiperState:
    """DOC_FOCUS stage — condense ingested-document context before persona."""
    return _dispatch_stage_node(state, config, stage_name="DOC_FOCUS")


def search_node(state: PiperState, config=None) -> PiperState:
    """SEARCH stage — emit first-pass reply and launch background search."""
    return _dispatch_stage_node(state, config, stage_name="SEARCH")


def reporter_node(state: PiperState, config=None) -> PiperState:
    """REPORTER stage — summarize completed background search results."""
    return _dispatch_stage_node(state, config, stage_name="REPORTER")


def undo_node(state: PiperState, config=None) -> PiperState:
    """UNDO stage — invert the most recent reversible file mutation."""
    return _dispatch_stage_node(state, config, stage_name="UNDO")


def reminder_set_node(state: PiperState, config=None) -> PiperState:
    """REMINDER_SET stage — schedule a reminder via the reminder store."""
    return _dispatch_stage_node(state, config, stage_name="REMINDER_SET")


def explain_node(state: PiperState, config=None) -> PiperState:
    """EXPLAIN stage — produce an explanation turn from the explain interceptor."""
    return _dispatch_stage_node(state, config, stage_name="EXPLAIN")


# ---------------------------------------------------------------------------
# MANAGER
# ---------------------------------------------------------------------------


def manager_node(state: PiperState, config=None) -> PiperState:
    """MANAGER stage — execute TASK stages through StageExecutor."""
    runtime = (config or {}).get("configurable", {}) if config else {}
    orc = runtime.get("orchestrator")
    if orc is None:
        raise RuntimeError(
            "manager_node requires an orchestrator instance in config['configurable']['orchestrator']. "
            "Pass it when building the graph or invoking the node."
        )

    from core.orchestrator_phases import _run_manager_core  # local: avoid circular import at module load

    _run_manager_core(orc)

    verification_passed = bool(
        getattr(orc, "last_verification", None)
        and str(getattr(orc.last_verification, "verdict", "")).strip().upper() == "VERIFIED"
    )
    return {
        **state,
        "stage": "MANAGER",
        "manager_result": {
            "next_stage": orc.next_stage,
            "verification_passed": verification_passed,
        },
    }


# ---------------------------------------------------------------------------
# VERIFY
# ---------------------------------------------------------------------------


def verify_node(state: PiperState, config=None) -> PiperState:
    """VERIFY stage — read verification result and detect pending interrupts."""
    runtime = (config or {}).get("configurable", {}) if config else {}
    orc = runtime.get("orchestrator")
    if orc is None:
        raise RuntimeError(
            "verify_node requires an orchestrator instance in config['configurable']['orchestrator']. "
            "Pass it when building the graph or invoking the node."
        )

    verification_passed = bool(
        getattr(orc, "last_verification", None)
        and str(getattr(orc.last_verification, "verdict", "")).strip().upper() == "VERIFIED"
    )

    # Detect pending interrupts set by StageExecutor during MANAGER
    interrupt_payload = None
    pending_confirmation = dict(getattr(orc, "pending_file_target_confirmation", {}) or {})
    pending_pause = dict(getattr(orc, "pending_stage_pause", {}) or {})

    if pending_confirmation:
        interrupt_payload = {
            "kind": "missing_file_target_confirmation",
            "question": str(pending_confirmation.get("question") or "Please confirm or name the intended target.").strip(),
            "pending_file_target_confirmation": pending_confirmation,
        }
    elif pending_pause:
        pause_type = str(pending_pause.get("pause_type") or "").strip().lower()
        if pause_type == "approval":
            interrupt_payload = {
                "kind": "stage_approval_pause",
                "question": str(pending_pause.get("question") or "Please confirm whether I should continue.").strip(),
                "pending_stage_pause": pending_pause,
            }
        elif pause_type == "user_input":
            interrupt_payload = {
                "kind": "stage_user_input_pause",
                "question": str(pending_pause.get("question") or "Please provide the requested details.").strip(),
                "pending_stage_pause": pending_pause,
            }

    return {
        **state,
        "stage": "VERIFY",
        "verification_passed": verification_passed,
        "interrupt_payload": interrupt_payload,
    }


# ---------------------------------------------------------------------------
# AWAIT_INTERRUPT
# ---------------------------------------------------------------------------


def await_interrupt_node(state: PiperState, config=None) -> PiperState:
    """AWAIT_INTERRUPT stage — pause graph execution for user input.

    Calls ``langgraph.types.interrupt()`` with the payload from VERIFY.
    On resume, applies the user's response via the appropriate resume helper
    and routes back to VERIFY for re-evaluation.
    """
    from langgraph.types import interrupt

    payload = state.get("interrupt_payload")
    if not payload:
        return {**state, "stage": "AWAIT_INTERRUPT"}

    resume_value = interrupt(payload)

    runtime = (config or {}).get("configurable", {}) if config else {}
    orc = runtime.get("orchestrator")
    if orc is None:
        raise RuntimeError(
            "await_interrupt_node requires an orchestrator instance in config['configurable']['orchestrator']. "
            "Pass it when building the graph or invoking the node."
        )

    kind = str(payload.get("kind") or "").strip().lower()
    if kind == "missing_file_target_confirmation":
        _apply_file_target_resume(orc, payload, resume_value)
    elif kind == "stage_approval_pause":
        _apply_stage_approval_resume(orc, payload, resume_value)
    elif kind == "stage_user_input_pause":
        _apply_user_input_resume(orc, payload, resume_value)

    # Propagate next_stage into state so VERIFY routing can direct flow
    manager_result = dict(state.get("manager_result") or {})
    manager_result["next_stage"] = getattr(orc, "next_stage", "PERSONA")

    return {
        **state,
        "stage": "AWAIT_INTERRUPT",
        "interrupt_payload": None,
        "manager_result": manager_result,
    }


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------


def _apply_file_target_resume(orc, payload: dict[str, Any], resume_value: Any) -> None:
    """Apply a file-target confirmation resume to the orchestrator."""
    from core.file_target_confirmation import (
        build_confirmed_route_decision,
        classify_pending_file_target_confirmation_reply,
    )

    pending = dict(payload.get("pending_file_target_confirmation") or {})
    resolution = classify_pending_file_target_confirmation_reply(_resume_text(resume_value), pending)

    if resolution is None:
        # Invalid response — keep pending state so VERIFY will re-detect it
        return

    exact_target = str(pending.get("exact_target") or "").strip()
    candidates = [str(item).strip() for item in (pending.get("candidates") or []) if str(item).strip()]
    chosen_target = str(resolution.get("chosen_target") or "").strip()
    decision = str(resolution.get("decision") or "").strip().lower()

    if decision in {"confirm", "choose"} and exact_target and chosen_target:
        confirmed_route = build_confirmed_route_decision(
            dict(pending.get("route_decision") or {}),
            exact_target=exact_target,
            chosen_target=chosen_target,
        )
        orc.route_decision = confirmed_route
        orc.context_card = dict(confirmed_route.get("card") or {})
        orc.pending_file_target_confirmation = None
        orc.next_stage = "MANAGER"
        if getattr(orc, "scratchpad", None) is not None:
            orc.scratchpad.append(
                f"LANGGRAPH_INTERRUPT_RESUME: file target confirmed as {chosen_target}"
            )
        return

    if decision == "decline":
        reply = "Understood. I will leave the workspace unchanged."
        if exact_target and candidates:
            reply = f"Understood. I will not substitute `{candidates[0]}` for `{exact_target}`."
        orc.route_decision = {
            "decision": "CHAT",
            "interceptor": "FILE_TARGET_CONFIRMATION_CANCELLED",
            "system_notice": {
                "kind": "file_target_confirmation_cancelled",
                "reply": reply,
                "exact_target": exact_target,
                "candidates": candidates[:3],
            },
        }
        orc.context_card = {}
        orc.pending_file_target_confirmation = None
        orc.next_stage = "PERSONA"
        if getattr(orc, "scratchpad", None) is not None:
            orc.scratchpad.append("LANGGRAPH_INTERRUPT_RESUME: file target confirmation declined.")
        return

    # Fallback — treat as invalid and keep pending


def _apply_stage_approval_resume(orc, payload: dict[str, Any], resume_value: Any) -> None:
    """Apply a stage-approval resume to the orchestrator."""
    from core.stage_approval import classify_stage_approval_reply

    pending = dict(payload.get("pending_stage_pause") or {})
    decision = classify_stage_approval_reply(_resume_text(resume_value))

    if decision is None:
        # Invalid response — keep pending so VERIFY will re-detect it
        return

    stage_goal = str(pending.get("stage_goal") or "").strip()

    if decision == "decline":
        orc.route_decision = {
            "decision": "CHAT",
            "interceptor": "STAGE_APPROVAL_CANCELLED",
            "system_notice": {
                "kind": "stage_approval_cancelled",
                "stage_goal": stage_goal,
                "reply": "Understood. I will stop here and leave things unchanged.",
            },
        }
        orc.context_card = {}
        orc.pending_stage_pause = None
        orc.next_stage = "PERSONA"
        if getattr(orc, "scratchpad", None) is not None:
            orc.scratchpad.append("LANGGRAPH_INTERRUPT_RESUME: approval declined; task stopped before execution.")
        return

    approved_route = dict(pending.get("approved_route_decision") or pending.get("route_decision") or {})
    approved_card = dict(approved_route.get("card") or {})
    approved_stages = [dict(item) for item in (approved_card.get("stages") or []) if isinstance(item, dict)]
    if not approved_stages:
        orc.route_decision = {
            "decision": "CHAT",
            "interceptor": "STAGE_APPROVAL_NO_REMAINING_WORK",
            "system_notice": {
                "kind": "stage_approval_no_remaining_work",
                "stage_goal": stage_goal,
            },
        }
        orc.context_card = {}
        orc.pending_stage_pause = None
        orc.next_stage = "PERSONA"
        if getattr(orc, "scratchpad", None) is not None:
            orc.scratchpad.append("LANGGRAPH_INTERRUPT_RESUME: approval received but no remaining execution stage was recorded.")
        return

    for s in approved_stages:
        s["approved"] = True
    approved_card["stages"] = approved_stages
    approved_route["card"] = approved_card
    orc.route_decision = approved_route
    orc.context_card = approved_card
    orc.pending_stage_pause = None
    orc.next_stage = "MANAGER"
    orc.reporter_just_ran = False
    orc.synthetic_user_turn = False
    orc.is_search_result = False
    if getattr(orc, "scratchpad", None) is not None:
        orc.scratchpad.append("LANGGRAPH_INTERRUPT_RESUME: approval received; continuing approved task.")


def _apply_user_input_resume(orc, payload: dict[str, Any], resume_value: Any) -> None:
    """Apply a stage-user-input resume to the orchestrator."""
    pending = dict(payload.get("pending_stage_pause") or {})
    reply_text = _resume_text(resume_value)

    if not reply_text:
        # Invalid response — keep pending so VERIFY will re-detect it
        return

    orc.user_msg = reply_text
    orc.route_decision = {}
    orc.context_card = {}
    orc.pending_stage_pause = None
    orc.next_stage = "ROUTE"
    orc.reporter_just_ran = False
    orc.synthetic_user_turn = False
    orc.is_search_result = False
    if getattr(orc, "scratchpad", None) is not None:
        orc.scratchpad.append(f"LANGGRAPH_INTERRUPT_RESUME: user input received — {reply_text!r}")


# ---------------------------------------------------------------------------
# PERSONA
# ---------------------------------------------------------------------------


def persona_node(state: PiperState, config=None) -> PiperState:
    """PERSONA stage — generate the assistant response."""
    runtime = (config or {}).get("configurable", {}) if config else {}
    orc = runtime.get("orchestrator")
    if orc is None:
        raise RuntimeError(
            "persona_node requires an orchestrator instance in config['configurable']['orchestrator']. "
            "Pass it when building the graph or invoking the node."
        )

    from core.orchestrator_phases import _run_persona_core  # local: avoid circular import at module load

    _run_persona_core(orc)

    return {
        **state,
        "stage": "PERSONA",
        "persona_output": orc.chat.get_messages_snapshot()[-1].get("content", "") if orc.chat.get_messages_snapshot() else None,
    }
