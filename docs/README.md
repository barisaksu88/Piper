# Piper Docs Hub

This folder groups the operational design/checklist material for `Piper v1`.

It is not the doctrine layer.
Authoritative doctrine remains in [AGENTS.md](../AGENTS.md).

## Read Order

1. [AGENTS.md](../AGENTS.md)
   - non-negotiable architecture and behavior doctrine

2. [BLUEPRINT.md](v1/BLUEPRINT.md)
   - philosophy, target stack, anti-drift rules

3. [EXECUTION_ROADMAP.md](v1/EXECUTION_ROADMAP.md)
   - concrete staged plan for the v1 redesign

4. [V1_GUARDRAILS.md](v1/checklists/V1_GUARDRAILS.md)
   - how to execute architecture work without drifting

5. [TRIAGE_MAP.md](v1/checklists/TRIAGE_MAP.md)
   - where to look first when something breaks

6. [RELEASE_READINESS.md](v1/checklists/RELEASE_READINESS.md)
   - what must be true before Piper is considered ready for actual use

## Current Structure

- [AGENTS.md](../AGENTS.md)
- [docs/architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md)
- [docs/architecture/CAPABILITIES.md](architecture/CAPABILITIES.md)
- [docs/v1/BLUEPRINT.md](v1/BLUEPRINT.md)
- [docs/v1/EXECUTION_ROADMAP.md](v1/EXECUTION_ROADMAP.md)
- [docs/v1/checklists/V1_GUARDRAILS.md](v1/checklists/V1_GUARDRAILS.md)
- [docs/v1/checklists/TRIAGE_MAP.md](v1/checklists/TRIAGE_MAP.md)
- [docs/v1/checklists/RELEASE_READINESS.md](v1/checklists/RELEASE_READINESS.md)

Why:
- doctrine stays at the repo root in `AGENTS.md`
- descriptive and redesign docs now live under one `docs/` tree
- `docs/architecture/` is for repo-shape reference
- `docs/v1/` is for the active redesign plan and checklists

## Existing Operational Memory

Short repo-local continuity notes remain in:

- [notes/known-good.md](../notes/known-good.md)
- [notes/known-issues.md](../notes/known-issues.md)
- [notes/coder-log.md](../notes/coder-log.md)

Use those for validated state and coding history.
Use `docs/` for planned direction and repeatable operating checklists.
