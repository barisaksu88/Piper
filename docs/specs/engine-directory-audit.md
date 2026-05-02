# Engine Directory Audit

Status: Proposal spec

This document is the focused planning surface for `core/engines/` lifecycle cleanup.

## Purpose

`core/engines/` currently mixes:
- true self-registering lifecycle engines
- direct-call services/utilities

This is not a runtime bug by itself, but it does make the package boundary less clear than it should be.

## Goals

- document the difference between lifecycle engines and direct-call services
- improve naming and placement where it clarifies ownership
- avoid churn-only reshuffles with no behavioral gain
- keep orchestration and integration surfaces thinner over time

## Non-Goals

- forcing every service into a hook-registration pattern
- large mechanical moves without clear payoff
- treating this as urgent ahead of higher-value user-facing work

## Candidate Classification

Lifecycle engines:
- `change_journal.py`
- `proactive_monitor.py`
- `stats_collector.py`

Direct-call services/utilities to review:
- `summary.py`
- `conversation_compressor.py`
- `context_pack.py`
- `verification.py`
- `file_work.py`
- `followup_resolution.py`
- `route_clarity.py`
- `state_mutation.py`
- `computer_use_engine.py`
- `computer_use_verifier.py`
- `rollback_engine.py`

## Likely File Surfaces

- `core/engines/`
- possible `core/services/` or `core/runtime_services/`
- `core/orchestrator_phases.py`
- `core/routing/route_normalizer.py`
- `core/engines/__init__.py`
- `docs/architecture/TRIGGER_FLOW.md`

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
