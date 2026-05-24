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
- `change_journal.py` — split complete. `ChangeJournal` class moved to `core/services/change_journal.py`; only the `@register_hook("on_task_verified")` `_hook_record_change_journal` remains in `core/engines/change_journal.py`. See `docs/architecture/CHANGE_JOURNAL_SPLIT_READINESS.md`.
- `proactive_monitor.py`
- `stats_collector.py`

Direct-call services/utilities to review:
- `context_pack.py` — audited, see `docs/architecture/CONTEXT_PACK_SPLIT_READINESS.md`. Split complete. `ContextPackService` moved to `core/services/context_pack_service.py`; `ContextPackDirectiveEngine` and the `on_turn_end` hook remain in `core/engines/context_pack.py`. Tail-block registry lives in `core/engines/tail_block_registry.py`; renderer/helpers in `core/services/context_pack_renderer.py`; runtime path helpers in `core/services/context_pack_paths.py`.

Audited — keep in `core/engines/`:
- `computer_use_engine.py` — lifecycle engine with mutable browser session state, see `docs/architecture/COMPUTER_USE_ENGINE_SERVICE_READINESS.md`

Already relocated to `core/services/`:
- `conversation_compressor.py` — `ConversationCompressor` class and `ConversationCompressionResult` moved to `core/services/conversation_compressor.py`; hook remains in `core/engines/conversation_compressor.py`
- `file_work.py`
- `followup_resolution.py`
- `route_clarity.py`
- `rollback_engine.py`
- `search_workflow.py`
- `state_mutation.py`
- `summary.py`
- `verification.py`
- `computer_use_verifier.py`

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
