# Piper Docs Hub

This folder groups all design, planning, and reference material for Piper.

Authoritative doctrine remains in [AGENTS.md](../AGENTS.md).

## Read Order

1. [AGENTS.md](../AGENTS.md)
   - non-negotiable architecture and behavior doctrine

2. [OVERVIEW.md](OVERVIEW.md)
   - what Piper does today, in plain language; one page, no implementation detail

3. [VISION.md](VISION.md)
   - where Piper is going; your control point — update this when something feels wrong or missing

4. [foundation/BLUEPRINT.md](foundation/BLUEPRINT.md)
   - philosophy, target stack, anti-drift rules

5. [architecture/TRIGGER_FLOW.md](architecture/TRIGGER_FLOW.md)
   - prescriptive runtime spec: full turn lifecycle, route kinds, context blocks, all implemented features

6. [ROADMAP.md](ROADMAP.md)
   - planned future work; specs must be written here before Codex touches anything

5. [foundation/checklists/V1_GUARDRAILS.md](foundation/checklists/V1_GUARDRAILS.md)
   - how to execute architecture work without drifting

6. [foundation/checklists/TRIAGE_MAP.md](foundation/checklists/TRIAGE_MAP.md)
   - where to look first when something breaks

7. [foundation/checklists/RELEASE_READINESS.md](foundation/checklists/RELEASE_READINESS.md)
   - what must be true before Piper is considered ready for actual use

## Current Structure

- [AGENTS.md](../AGENTS.md)
- [docs/ROADMAP.md](ROADMAP.md) — planned work queue; document-first
- [docs/architecture/TRIGGER_FLOW.md](architecture/TRIGGER_FLOW.md) — authoritative runtime spec
- [docs/foundation/BLUEPRINT.md](foundation/BLUEPRINT.md)
- [docs/foundation/EXECUTION_ROADMAP.md](foundation/EXECUTION_ROADMAP.md) — v1 redesign plan (complete, archived)
- [docs/foundation/GLM_ADVICE.md](foundation/GLM_ADVICE.md) — annotated external architectural review
- [docs/foundation/FILEWORK_ENGINE.md](foundation/FILEWORK_ENGINE.md)
- [docs/foundation/SUMMARY_ENGINE.md](foundation/SUMMARY_ENGINE.md)
- [docs/foundation/VERIFICATION_ENGINE.md](foundation/VERIFICATION_ENGINE.md)
- [docs/foundation/checklists/V1_GUARDRAILS.md](foundation/checklists/V1_GUARDRAILS.md)
- [docs/foundation/checklists/TRIAGE_MAP.md](foundation/checklists/TRIAGE_MAP.md)
- [docs/foundation/checklists/RELEASE_READINESS.md](foundation/checklists/RELEASE_READINESS.md)

Why:
- doctrine stays at the repo root in `AGENTS.md`
- `docs/architecture/` holds the live prescriptive runtime spec (`TRIGGER_FLOW.md`)
- `docs/ROADMAP.md` is the active build queue (document-first doctrine)
- `docs/foundation/` holds completed v1 foundation docs — read for context, not direction

## Existing Operational Memory

Short repo-local continuity notes remain in:

- [notes/known-good.md](../notes/known-good.md)
- [notes/known-issues.md](../notes/known-issues.md)
- [notes/coder-log.md](../notes/coder-log.md)

Use those for validated state and coding history.
Use `docs/` for planned direction and repeatable operating checklists.
