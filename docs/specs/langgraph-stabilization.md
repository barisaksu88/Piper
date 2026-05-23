# LangGraph Stabilization

Status: Active spec and consolidation point

This document is the single planning/reference surface for LangGraph/default-runtime stabilization.

Use it for:
- current stabilization goals
- relationship between live runtime truth and older migration material
- active follow-up risks
- validation expectations

## Purpose

Piper now has a LangGraph runtime plus a legacy fallback runtime.

The main job is no longer "decide whether to migrate."
The main job is:
- keep graph behavior aligned with the runtime spec
- prevent drift from the legacy phase helpers
- stabilize checkpoint, interrupt, resume, and recovery behavior
- keep evidence stronger than narration

## Current Runtime Truth

The authoritative live runtime description is already in:
- [`docs/architecture/TRIGGER_FLOW.md`](../architecture/TRIGGER_FLOW.md)

Important LangGraph sections there:
- dual runtime overview
- graph structure and nodes
- checkpoint modes
- interrupt/resume flow
- recovery records
- visual trace/debug behavior
- feature-flag fallback behavior
- implemented migration section

Primary runtime surfaces:
- [`core/orchestrator.py`](../../core/orchestrator.py)
- [`core/orchestrator_graph_builder.py`](../../core/orchestrator_graph_builder.py)
- [`core/orchestrator_graph.py`](../../core/orchestrator_graph.py)
- [`core/graph_nodes.py`](../../core/graph_nodes.py)
- [`core/orchestrator_phases.py`](../../core/orchestrator_phases.py)

Validation surface examples:
- [`scripts/langgraph_interrupt_smoke_test.py`](../../scripts/langgraph_interrupt_smoke_test.py)
- [`scripts/langgraph_checkpoint_recovery_smoke_test.py`](../../scripts/langgraph_checkpoint_recovery_smoke_test.py)
- [`scripts/langgraph_recovery_command_smoke_test.py`](../../scripts/langgraph_recovery_command_smoke_test.py)
- [`scripts/langgraph_checkpoint_inspect_smoke_test.py`](../../scripts/langgraph_checkpoint_inspect_smoke_test.py)

## Historical Context

Older migration-planning material still exists in:
- [`docs/PIPER_LANGGRAPH_MIGRATION_SPEC_v1.2.md`](../PIPER_LANGGRAPH_MIGRATION_SPEC_v1.2.md)
- [`docs/archive/Piper_LangGraph_Migration_Spec.md`](../archive/Piper_LangGraph_Migration_Spec.md)

Those are useful for historical rationale, but they are not the best starting point for current runtime truth.

Current reading order should be:
1. `AGENTS.md`
2. `docs/architecture/TRIGGER_FLOW.md`
3. this stabilization doc
4. older migration specs only if historical context is needed

## Shipped Foundation

- LangGraph is the default runtime path.
- The legacy while-loop runtime still exists as fallback.
- The graph delegates to the same core phase helpers rather than re-implementing the whole system separately.
- Checkpointing, interrupt/resume, and recovery surfaces are real runtime features.
- There are dedicated smoke tests for the critical graph behaviors.

## Design Center

LangGraph work should optimize for:
- behavioral equivalence
- durable recovery
- visible interrupt state
- conservative fallback
- test-backed stability

It should not optimize for:
- novelty
- large architectural reshuffles without evidence
- graph-specific behavior drift that leaves legacy/runtime truth inconsistent

## Current Active Behavior

From trigger-flow docs, notes, and smoke surfaces, the current active stabilization concerns include:
- keeping node routing aligned with `orc.next_stage`
- preserving checkpoint/recovery correctness
- maintaining interrupt/resume integrity
- avoiding graph-specific regressions where a stage is "routed" but not actually executed
- keeping fallback behavior safe when graph setup fails

This means the problem is not missing architecture anymore.
The problem is drift control and edge-case hardening.

## Remaining Active Work

### 1. Drift control

Keep LangGraph behavior aligned with:
- shared phase helpers
- `TRIGGER_FLOW.md`
- legacy fallback expectations

### 2. Recovery and interrupt confidence

Checkpoint persistence, interrupt persistence, resume, and clear/recovery commands should remain a first-class regression surface.

### 3. Historical-doc demotion

Older migration specs should stay clearly secondary to the live runtime docs so future agents do not restart already-finished migration debates.

### 4. Burn-in clarity

Where the repo still says "burn-in" or "stabilization," keep distinguishing:
- code-complete
- automated-test proven
- real-world boring/stable

## Future Improvements

These remain future-facing unless code or live evidence proves they are already done:
- clearer stabilization checklist ownership
- more explicit real-world burn-in evidence tracking
- additional regression fixtures for recovery edge cases
- better operator-facing diagnostics for checkpoint/interrupt state

## Deprecated Assumptions

Future agents should avoid these stale assumptions:
- "LangGraph migration is still mostly unimplemented"
- "the old migration spec is the best current reference"
- "graph-specific behavior can diverge as long as tests are green"
- "interrupt/resume is just a nice extra instead of a core runtime truth surface"

## Doc Placement Rules

- `AGENTS.md` defines doctrine and boundaries.
- `docs/architecture/TRIGGER_FLOW.md` is the main live runtime truth.
- `docs/WIP.md` tracks current stabilization follow-up.
- this file is the focused stabilization reference
- older migration specs are historical/supporting context
