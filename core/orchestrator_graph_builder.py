"""core/orchestrator_graph_builder.py

LangGraph StateGraph builder for the Piper orchestrator.

Phase 4 — Wires the top-level nodes into a compiled graph with checkpoint support.

Phase 5 — Adds AWAIT_INTERRUPT node for pause-and-resume approval flows.
"""

from __future__ import annotations

from typing import Any

from core.graph_nodes import (
    PiperState,
    await_interrupt_node,
    document_focus_node,
    explain_node,
    manager_node,
    persona_node,
    reminder_set_node,
    reporter_node,
    route_node,
    search_node,
    undo_node,
    verify_node,
)


def build_piper_graph(*, checkpointer: Any | None = None) -> Any:
    """Compile a LangGraph that runs Piper's top-level phases.

    Conditional edge from ROUTE:
      - dispatch based on ``orc.next_stage`` so SEARCH / REPORTER /
        DOC_FOCUS / UNDO / REMINDER_SET / EXPLAIN bypasses are preserved
      - fallback to route_decision only if ``next_stage`` is missing

    Conditional edge from VERIFY:
      - interrupt_payload present       → AWAIT_INTERRUPT
      - next_stage == "MANAGER"         → MANAGER (resume after confirmation)
      - next_stage == "ROUTE"           → ROUTE (resume after user input)
      - otherwise                       → PERSONA

    AWAIT_INTERRUPT always loops back to VERIFY so the orchestrator can
    re-evaluate with the confirmed/denied state applied.

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
    builder.add_node("DOC_FOCUS", document_focus_node)
    builder.add_node("SEARCH", search_node)
    builder.add_node("REPORTER", reporter_node)
    builder.add_node("MANAGER", manager_node)
    builder.add_node("UNDO", undo_node)
    builder.add_node("REMINDER_SET", reminder_set_node)
    builder.add_node("EXPLAIN", explain_node)
    builder.add_node("VERIFY", verify_node)
    builder.add_node("AWAIT_INTERRUPT", await_interrupt_node)
    builder.add_node("PERSONA", persona_node)

    # ------------------------------------------------------------------
    # ROUTE routing
    # ------------------------------------------------------------------

    def _route_routing(state: PiperState, config=None) -> str:
        runtime = (config or {}).get("configurable", {}) if config else {}
        orc = runtime.get("orchestrator")
        next_stage = str(getattr(orc, "next_stage", "") or "").strip().upper() if orc is not None else ""
        if next_stage in {
            "DOC_FOCUS",
            "SEARCH",
            "REPORTER",
            "MANAGER",
            "UNDO",
            "REMINDER_SET",
            "EXPLAIN",
            "PERSONA",
        }:
            return next_stage
        decision = str((state.get("route_decision") or {}).get("decision") or "").strip().upper()
        if decision == "TASK":
            return "MANAGER"
        if decision == "SEARCH":
            return "SEARCH"
        return "PERSONA"

    builder.add_edge(START, "ROUTE")
    builder.add_conditional_edges(
        "ROUTE",
        _route_routing,
        {
            "DOC_FOCUS": "DOC_FOCUS",
            "SEARCH": "SEARCH",
            "REPORTER": "REPORTER",
            "MANAGER": "MANAGER",
            "UNDO": "UNDO",
            "REMINDER_SET": "REMINDER_SET",
            "EXPLAIN": "EXPLAIN",
            "PERSONA": "PERSONA",
        },
    )

    def _next_stage_routing(state: PiperState, config=None) -> str:
        runtime = (config or {}).get("configurable", {}) if config else {}
        orc = runtime.get("orchestrator")
        next_stage = str(getattr(orc, "next_stage", "") or "").strip().upper()
        if next_stage in {
            "ROUTE",
            "DOC_FOCUS",
            "SEARCH",
            "REPORTER",
            "MANAGER",
            "UNDO",
            "REMINDER_SET",
            "EXPLAIN",
            "PERSONA",
        }:
            return next_stage
        return "END"

    for stage_name in ("DOC_FOCUS", "SEARCH", "REPORTER", "UNDO", "REMINDER_SET", "EXPLAIN"):
        builder.add_conditional_edges(
            stage_name,
            _next_stage_routing,
            {
                "ROUTE": "ROUTE",
                "DOC_FOCUS": "DOC_FOCUS",
                "SEARCH": "SEARCH",
                "REPORTER": "REPORTER",
                "MANAGER": "MANAGER",
                "UNDO": "UNDO",
                "REMINDER_SET": "REMINDER_SET",
                "EXPLAIN": "EXPLAIN",
                "PERSONA": "PERSONA",
                "END": END,
            },
        )

    # ------------------------------------------------------------------
    # MANAGER → VERIFY
    # ------------------------------------------------------------------

    builder.add_edge("MANAGER", "VERIFY")

    # ------------------------------------------------------------------
    # VERIFY routing (Phase 5 interrupt support)
    # ------------------------------------------------------------------

    def _verify_routing(state: PiperState, config=None) -> str:
        if state.get("interrupt_payload"):
            return "AWAIT_INTERRUPT"
        runtime = (config or {}).get("configurable", {}) if config else {}
        orc = runtime.get("orchestrator")
        next_stage = str(getattr(orc, "next_stage", "") or "").strip().upper()
        if next_stage == "MANAGER":
            return "MANAGER"
        if next_stage == "ROUTE":
            return "ROUTE"
        return "PERSONA"

    builder.add_conditional_edges(
        "VERIFY",
        _verify_routing,
        {
            "AWAIT_INTERRUPT": "AWAIT_INTERRUPT",
            "MANAGER": "MANAGER",
            "ROUTE": "ROUTE",
            "PERSONA": "PERSONA",
        },
    )

    # ------------------------------------------------------------------
    # AWAIT_INTERRUPT → VERIFY (loop back for re-evaluation)
    # ------------------------------------------------------------------

    builder.add_edge("AWAIT_INTERRUPT", "VERIFY")

    # ------------------------------------------------------------------
    # PERSONA routing
    # ------------------------------------------------------------------
    # Legacy while-loop behaviour: after PERSONA, next_stage may be ROUTE
    # (auto-reroute / loopback) or FINISHED (normal termination).  Mirror
    # that logic so the graph does not silently drop retry markers.

    def _persona_routing(state: PiperState, config=None) -> str:
        runtime = (config or {}).get("configurable", {}) if config else {}
        orc = runtime.get("orchestrator")
        next_stage = str(getattr(orc, "next_stage", "") or "").strip().upper()
        if next_stage == "ROUTE":
            return "ROUTE"
        if next_stage == "MANAGER":
            return "MANAGER"
        return "END"

    builder.add_conditional_edges(
        "PERSONA",
        _persona_routing,
        {
            "ROUTE": "ROUTE",
            "MANAGER": "MANAGER",
            "END": END,
        },
    )

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Phase 6 — Visual debug traces
# ---------------------------------------------------------------------------


def save_piper_graph_visualization(graph, *, path=None):
    """Render compiled graph to PNG or Mermaid text fallback.

    PNG is preferred because it renders the full graph with styling.
    If anything fails (missing playwright, graphviz, etc.) we fall back
    to plain Mermaid markdown so developers still have a readable diagram.
    """
    from pathlib import Path

    out_dir = Path(path or Path("data") / "debug")
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / "langgraph_visualization.png"
    md_path = out_dir / "langgraph_visualization.md"

    try:
        png_bytes = graph.get_graph().draw_mermaid_png()
        png_path.write_bytes(png_bytes)
        if md_path.exists():
            md_path.unlink()
        return png_path
    except Exception:
        text = graph.get_graph().draw_mermaid()
        md_path.write_text(text, encoding="utf-8")
        if png_path.exists():
            png_path.unlink()
        return md_path
