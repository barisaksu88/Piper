# Engine Directory Audit

Status: Completed (final verification branch `audit/engine-service-boundary-final`)  
Last verified: 2026-05-24

This document tracked the focused cleanup of the `core/engines/` lifecycle boundary. The work is now complete.

---

## Purpose (Historical)

`core/engines/` originally mixed:
- true self-registering lifecycle engines
- direct-call services/utilities

This made the package boundary unclear. Over multiple split waves, every pure service was relocated to `core/services/`, leaving only lifecycle engines, registry wrappers, and infrastructure in `core/engines/`.

---

## Completed Splits

### Registry-wrapper modules (hook remains, service class moved)

| Engine file | Service file | What moved |
|-------------|-------------|------------|
| `core/engines/change_journal.py` | `core/services/change_journal.py` | `ChangeJournal` class |
| `core/engines/conversation_compressor.py` | `core/services/conversation_compressor.py` | `ConversationCompressor` class, `ConversationCompressionResult` dataclass |
| `core/engines/stats_collector.py` | `core/services/stats_collector.py` | `StatsCollector` class, `TurnStatsState` dataclass |

### Hybrid modules (partial split — engine behavior stays, pure helpers moved)

| Engine file | Service file(s) | What moved | What stays |
|-------------|----------------|------------|------------|
| `core/engines/context_pack.py` | `core/services/context_pack_service.py`, `core/services/context_pack_renderer.py`, `core/services/context_pack_paths.py` | `ContextPackService`, renderer/helpers, runtime path helpers | `ContextPackDirectiveEngine` (registry-bound builder), `@register_hook("on_turn_end")` |
| `core/engines/proactive_monitor.py` | `core/services/reminders.py` | `ReminderStore`, `ReminderParseResult`, all parser/message helpers, constants | `ProactiveMonitor` lifecycle class (daemon thread), `@register_tail_block` (×2), `@register_hook("on_turn_end")`, `@register_route_interceptor` |

### Pure service relocations (module removed from `core/engines/`)

| Old engine file | New service file | Notes |
|-----------------|-----------------|-------|
| `core/engines/search_workflow.py` | `core/services/search_workflow.py` | `SearchWorkflowEngine` |
| `core/engines/summary.py` | `core/services/summary.py` | `SummaryEngine` |
| `core/engines/verification.py` | `core/services/verification.py` | `VerificationEngine`, `VerificationResult` |
| `core/engines/file_work.py` | `core/services/file_work.py` | `FileWorkEngine` |
| `core/engines/route_clarity.py` | `core/services/route_clarity.py` | `RouteClarifier` |
| `core/engines/followup_resolution.py` | `core/services/followup_resolution.py` | `FollowupResolutionEngine` |
| `core/engines/rollback_engine.py` | `core/services/rollback_engine.py` | `RollbackEngine` |
| `core/engines/state_mutation.py` | `core/services/state_mutation.py` | `StateMutationEngine` |
| `core/engines/computer_use_verifier.py` | `core/services/computer_use_verifier.py` | `new_stage_evidence`, `update_stage_evidence`, `evaluate_stage`, `build_verified_payload` |

---

## Remaining `core/engines/` modules (post-split)

| Module | Classification | Why it stays |
|--------|---------------|------------|
| `computer_use_engine.py` | Lifecycle / resource engine | Owns live Playwright browser session, RLock, `shutdown()`/`suspend()` lifecycle. |
| `context_pack.py` | Hybrid | `ContextPackDirectiveEngine` is a registry-bound builder; `@register_hook("on_turn_end")` remains. |
| `proactive_monitor.py` | Hybrid | `ProactiveMonitor` owns a daemon thread; registry decorators remain. |
| `change_journal.py` | Registry wrapper only | `@register_hook("on_task_verified")` delegates to `core.services.change_journal.ChangeJournal`. |
| `conversation_compressor.py` | Registry wrapper only | `@register_hook("on_turn_end")` delegates to `core.services.conversation_compressor.ConversationCompressor`. |
| `stats_collector.py` | Registry wrapper only | `@register_hook("on_pre_route")` delegates to `core.services.stats_collector.StatsCollector`. |
| `tail_block_registry.py` | Registry infrastructure | `TailBlockContext`, `TailBlockBuilder`, `_TAIL_BLOCK_REGISTRY`, `register_tail_block`, and all `@register_tail_block` builders. |
| `__init__.py` | Package init | Re-exports `ContextPackDirectiveEngine`. |

---

## Boundary Rules (Enforced)

- **`core/services/` must never import from `core/engines/`** — verified across all 16 service modules.
- **`core/engines/` may import from `core/services/`** — this is expected for hook wrappers and hybrid modules that delegate to their extracted service classes.
- **No compatibility re-exports** — engine modules do not re-export service classes that moved. Callers import directly from `core.services.<module>`.
- **No legacy imports remain in runtime code** — all call sites updated.

---

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` is the live behavior-based reference.
- `docs/specs/engine-service-boundary-final.md` contains the final audit checklist.
