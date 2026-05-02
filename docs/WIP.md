# Piper WIP

Status: Active work register

This document tracks work that is actively in flight.
It reflects active work at time of writing. Verify branch state and commits from Git before acting.

Use it for:
- current branch focus
- features being built right now
- temporary implementation notes that are too active for `ROADMAP.md`
- explicit "not finished yet" status

Do not use it for:
- settled doctrine
- shipped runtime truth
- long-term archive/history
- vague future ideas with no active work

## Document Lifecycle

- `AGENTS.md`
  - doctrine and non-negotiable rules

- `docs/ROADMAP.md`
  - proposed and prioritized future work

- `docs/WIP.md`
  - active implementation work and near-term branch focus

- `docs/architecture/TRIGGER_FLOW.md`
  - shipped runtime truth and behavior placement

- `notes/`
  - operational coder memory, debugging facts, validation notes

## Migration Rule

When work starts:
- move or summarize the active item here from `ROADMAP.md`

When work ships:
- remove it from `WIP.md`
- place the relevant durable behavior in `docs/architecture/TRIGGER_FLOW.md`
- if needed, update `docs/architecture/ARCHITECTURE.md`, `docs/architecture/CAPABILITIES.md`, or notes

When work is abandoned or superseded:
- remove it from `WIP.md`
- either return the idea to `ROADMAP.md` in reduced form or move old material to `docs/archive/`

## Current Use Pattern

Keep entries short and high-signal:

## Active Now

- Browser computer use v0
  - goal: ship the browser-first `COMPUTER_USE` path with deterministic verification and harness coverage
  - current state: partially implemented; follow-up work and stabilization are still active
  - main files: `core/engines/computer_use_engine.py`, `core/engines/computer_use_verifier.py`, `core/executor.py`, `core/planner_boundary.py`, `core/prompt_builder.py`, `core/routing/route_normalizer.py`, `tools/registry.py`
  - next proof: stable browser-use regression coverage and confirmation that the shipped path matches `docs/architecture/TRIGGER_FLOW.md`
  - roadmap source: `docs/ROADMAP.md` under `Computer use v0 (browser-first)`
  - primary spec: `docs/specs/computer-use.md`

- Voice identity follow-up
  - goal: finish the active voice-identification path cleanly and collapse duplicate planning into one coherent spec
  - current state: foundation and multiple follow-up fixes already landed; thresholds, drift handling, and persona/runtime integration are active and should be treated as in-flight until the remaining voice-embedding/spec cleanup is settled
  - main files: `core/voice_recognition.py`, `tools/stt.py`, `memory/user_runtime.py`, `core/orchestrator_phases.py`, `ui/controller_actions.py`, `core/prompt_context.py`, `data/prompts/instructions.txt`
  - next proof: confirm what is still genuinely unshipped versus already true in runtime/notes, now that the spec is consolidated in `docs/specs/voice-identity.md`
  - evidence trail: `notes/coder-log.md` entries around the recent voice identity fixes and drift calibration
  - primary spec: `docs/specs/voice-identity.md`

- LangGraph/default-runtime stabilization
  - goal: keep the LangGraph path aligned with the legacy/runtime spec and close active resume/interrupt/search edge cases without reopening architecture drift
  - current state: major pieces are landed, but recent coder-log entries show this is still an active stabilization surface rather than fully boring background infrastructure
  - main files: `core/orchestrator.py`, `core/orchestrator_graph_builder.py`, `core/orchestrator_graph.py`, `core/graph_nodes.py`, `core/orchestrator_phases.py`
  - next proof: continued smoke/harness stability and no fresh divergence from `docs/architecture/TRIGGER_FLOW.md`
  - evidence trail: `notes/coder-log.md` entries for 2026-04-14 through 2026-04-27
  - primary spec: `docs/specs/langgraph-stabilization.md`

## Recently Landed

- Document navigation spine
  - `docs/DOCUMENTS_MAP.md` added
  - `docs/README.md` repaired to point at real navigation targets
  - `AGENTS.md` now explicitly describes the document hierarchy

- WIP lifecycle layer
  - `docs/WIP.md` added to separate active implementation from future planning and shipped runtime truth

## Parked

- Desktop computer use expansion
  - paused until browser-first computer use is stable and boring
  - remaining spec lives in `docs/ROADMAP.md` under `Desktop computer use expansion (phase 2)`

- Tiered smoke-suite audit
  - intentionally deferred until browser computer use stabilizes
  - remaining spec lives in `docs/ROADMAP.md` under `Tiered smoke-suite audit (after computer use v0 stabilizes)`
