# Piper Docs Hub

This folder groups Piper's design, planning, and reference material.

`AGENTS.md` is the top-level doctrine.
[DOCUMENTS_MAP.md](DOCUMENTS_MAP.md) is the fastest navigation guide.

## Recommended Read Order

1. [AGENTS.md](../AGENTS.md)
   - non-negotiable architecture and behavior doctrine

2. [DOCUMENTS_MAP.md](DOCUMENTS_MAP.md)
   - "what do I read for this specific need?"

3. [architecture/TRIGGER_FLOW.md](architecture/TRIGGER_FLOW.md)
   - prescriptive runtime spec and ownership map

4. [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md)
   - current codebase structure

5. [architecture/CAPABILITIES.md](architecture/CAPABILITIES.md)
   - current user-facing surface

6. [WIP.md](WIP.md)
   - active implementation work

7. [ROADMAP.md](ROADMAP.md)
   - future intended work

## Structure

- [DOCUMENTS_MAP.md](DOCUMENTS_MAP.md) — primary navigation layer
- [architecture/TRIGGER_FLOW.md](architecture/TRIGGER_FLOW.md) — live runtime flow and logic placement
- [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) — current structure and owning modules
- [architecture/CAPABILITIES.md](architecture/CAPABILITIES.md) — current behavior summary
- [WIP.md](WIP.md) — active in-flight work
- [ROADMAP.md](ROADMAP.md) — planned future work
- [specs/](specs/) — focused feature specs and consolidation docs
- [specs/README.md](specs/README.md) — what belongs in `docs/specs/`
- [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) — implementation workflow
- [foundation/](foundation/) — deeper design/context documents and checklists
- [archive/](archive/) — superseded material

## Notes vs Docs

- Use [notes/](../notes/) for operational memory, debugging lessons, and current validated state.
- Use [docs/](.) for navigation, architecture references, repeatable workflow, and future direction.

## Spec Rule

- Put dense feature concepts and high-risk design notes in `docs/specs/`.
- Keep `ROADMAP.md` as the planning index and priority surface, not the long-form home for every idea.

## Important Rule

If documents disagree:

1. `AGENTS.md`
2. `docs/architecture/TRIGGER_FLOW.md`
3. current code
4. other docs and notes
