"""core/graph_nodes.py

LangGraph node implementations for the Piper orchestrator.

Phase 1 — ROUTE node extraction.
Each node is a pure function PiperState -> PiperState that delegates to the
existing orchestrator logic.  This lets us verify behavior against the golden
corpus before any wiring changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

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


def route_node(state: PiperState, config: dict[str, Any] | None = None) -> PiperState:
    """ROUTE stage — decide which domain handles the user input.

    For Phase 1 this delegates to the existing ``phase_route`` logic via the
    orchestrator instance supplied in *config*.  Future phases will inline the
    pure decision logic here once the side-effect boundary is fully drawn.
    """
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


def manager_node(state: PiperState, config: dict[str, Any] | None = None) -> PiperState:
    """MANAGER stage — execute TASK stages through StageExecutor.

    For Phase 2 this delegates to the existing ``phase_manager`` logic via the
    orchestrator instance supplied in *config*.
    """
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


def verify_node(state: PiperState, config: dict[str, Any] | None = None) -> PiperState:
    """VERIFY stage — read the authoritative verification result from the orchestrator.

    In the legacy runtime verification happens inside StageExecutor during the
    MANAGER phase.  This node acts as a state adapter that surfaces the result
    into the LangGraph state so downstream nodes (e.g. PERSONA) can read it.
    """
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
    return {
        **state,
        "stage": "VERIFY",
        "verification_passed": verification_passed,
    }


def persona_node(state: PiperState, config: dict[str, Any] | None = None) -> PiperState:
    """PERSONA stage — generate the assistant response.

    For Phase 3 this delegates to the existing ``phase_persona`` logic via the
    orchestrator instance supplied in *config*.
    """
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
