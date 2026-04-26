"""core/orchestrator_graph_builder.py

LangGraph StateGraph builder for the Piper orchestrator.

Phase 4 — Wires the four extracted nodes (ROUTE, MANAGER, VERIFY, PERSONA)
into a compiled graph with checkpoint support.
"""

from __future__ import annotations

from typing import Any

from core.graph_nodes import PiperState, manager_node, persona_node, route_node, verify_node


def build_piper_graph(*, checkpointer: Any | None = None) -> Any:
    """Compile a LangGraph that runs ROUTE → MANAGER → VERIFY → PERSONA.

    Conditional edge from ROUTE:
      - decision in ("CHAT", "SEARCH") → PERSONA (skip MANAGER)
      - decision == "TASK"              → MANAGER

    MANAGER always flows to VERIFY, and VERIFY always flows to PERSONA.
    The orchestrator instance must be supplied at invocation time via
    ``config["configurable"]["orchestrator"]``.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph dependencies are not installed. Install `langgraph` to enable the graph runtime."
        ) from exc

    builder = StateGraph(PiperState)

    builder.add_node("ROUTE", route_node)
    builder.add_node("MANAGER", manager_node)
    builder.add_node("VERIFY", verify_node)
    builder.add_node("PERSONA", persona_node)

    def _route_routing(state: PiperState) -> str:
        decision = str((state.get("route_decision") or {}).get("decision") or "").strip().upper()
        if decision in ("CHAT", "SEARCH"):
            return "PERSONA"
        return "MANAGER"

    builder.add_edge(START, "ROUTE")
    builder.add_conditional_edges(
        "ROUTE",
        _route_routing,
        {"MANAGER": "MANAGER", "PERSONA": "PERSONA"},
    )
    builder.add_edge("MANAGER", "VERIFY")
    builder.add_edge("VERIFY", "PERSONA")
    builder.add_edge("PERSONA", END)

    return builder.compile(checkpointer=checkpointer)
