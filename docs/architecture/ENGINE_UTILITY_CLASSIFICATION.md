# Engine / Utility Classification

**Status:** Active reference  
**Scope:** Behavior-based classification of `core/engines/` modules

---

## Doctrine

Classification is **behavior-based, not directory-based**.

A module is classified by what it actually does at runtime, not by which folder it lives in.

---

## Three Buckets

### 1. Registry-Only / Clean Lifecycle Behavior

Modules that participate exclusively in the lifecycle hook/registry system. They do not expose a direct-call service API that orchestrator code imports and invokes imperatively.

| Module | Registry Behavior | What it delegates to |
|--------|-------------------|----------------------|
| `change_journal.py` | `@register_hook("on_task_verified")` — `_hook_record_change_journal` | `ChangeJournal` in `core/services/change_journal.py` |
| `conversation_compressor.py` | `@register_hook("on_turn_end")` — `_hook_deferred_conversation_summary` | `ConversationCompressor` in `core/services/conversation_compressor.py` |
| `stats_collector.py` | `@register_hook("on_pre_route")` — `_hook_note_pre_route_user_msg` | `StatsCollector` in `core/services/stats_collector.py` |
| `environment_query.py` | `@register_route_interceptor` — `_registered_environment_query_interceptor` | `looks_like_live_environment_query()` in `core/routing/environment_queries.py` |
| `operational_state_answer.py` | `@register_route_interceptor` — `_registered_operational_state_interceptor` | `build_readonly_state_answer()` in `core/prompt_context.py` |

These modules are **thin wrappers**: they register a hook and immediately delegate to a service class. They do not re-export the service class.

### 2. Hybrid Modules

Modules that **both** register hooks / tail-blocks / interceptors **and** retain engine-level behavior (lifecycle management, mutable state, or registry-bound builder classes).

| Module | Registry Behavior | Engine Behavior |
|--------|-------------------|-----------------|
| `context_pack.py` | `@register_hook("on_turn_end")`; `ContextPackDirectiveEngine` (registry-bound `build_persona_directive_pack`) remains here. Tail-block registry lives in `core/engines/tail_block_registry.py`. | `ContextPackDirectiveEngine` is a registry-bound builder class with no direct-call public API. |
| `proactive_monitor.py` | `@register_tail_block` (×2), `@register_hook("on_turn_end")`, `@register_route_interceptor` for reminder interception | `ProactiveMonitor` lifecycle class (start/stop/loop, daemon thread) remains here. |

### 3. Direct-Call Utilities

Modules that expose a direct-call service API and **do not** register hooks, tail-blocks, interceptors, or any other lifecycle mechanism. These are pure utilities imported and invoked by orchestrator or controller code.

*All eligible direct-call utilities have been relocated to `core/services/`.*

Remaining modules in `core/engines/` are **hybrids, registry wrappers, or lifecycle/resource-owning engines** (see below), not pure direct-call utilities.

---

### 3A. Engines That Stay in `core/engines/`

Modules that own mutable state, manage external resources, use threading, or participate in lifecycle management. These are **not** candidates for `core/services/`.

| Module | Why it stays in `core/engines/` |
|--------|--------------------------------|
| `computer_use_engine.py` | `ComputerUseEngine` owns a live Playwright browser session (`_BrowserSessionState`), uses `_playwright_lock` (RLock), has `shutdown()`/`suspend()` lifecycle methods, and is lazily initialized by `core/agent.py`. See `docs/architecture/COMPUTER_USE_ENGINE_SERVICE_READINESS.md`. |
| `tail_block_registry.py` | Registry infrastructure (`TailBlockContext`, `TailBlockBuilder`, `_TAIL_BLOCK_REGISTRY`, `register_tail_block`, and all `@register_tail_block` builders). This is a core lifecycle mechanism, not a service. |

### 3B. Split Completed

All splits are complete. No pure service classes remain in `core/engines/`.

| Module | What moved to `core/services/` | What remains in `core/engines/` |
|--------|-------------------------------|--------------------------------|
| `context_pack.py` | `ContextPackService` → `core/services/context_pack_service.py`; renderer/helpers → `core/services/context_pack_renderer.py`; runtime path helpers → `core/services/context_pack_paths.py` | `ContextPackDirectiveEngine` (registry-bound builder); `@register_hook("on_turn_end")` |
| `conversation_compressor.py` | `ConversationCompressor`, `ConversationCompressionResult` → `core/services/conversation_compressor.py` | `@register_hook("on_turn_end")` — `_hook_deferred_conversation_summary` |
| `change_journal.py` | `ChangeJournal` → `core/services/change_journal.py` | `@register_hook("on_task_verified")` — `_hook_record_change_journal` |
| `stats_collector.py` | `StatsCollector`, `TurnStatsState` → `core/services/stats_collector.py` | `@register_hook("on_pre_route")` — `_hook_note_pre_route_user_msg` |
| `proactive_monitor.py` | `ReminderStore`, `ReminderParseResult`, parser/message helpers → `core/services/reminders.py` | `ProactiveMonitor` lifecycle class; `@register_tail_block` (×2); `@register_hook("on_turn_end")`; `@register_route_interceptor` |
| `search_workflow.py` | `SearchWorkflowEngine` → `core/services/search_workflow.py` | *(module removed)* |
| `summary.py` | `SummaryEngine` → `core/services/summary.py` | *(module removed)* |
| `verification.py` | `VerificationEngine`, `VerificationResult` → `core/services/verification.py` | *(module removed)* |
| `file_work.py` | `FileWorkEngine` → `core/services/file_work.py` | *(module removed)* |
| `route_clarity.py` | `RouteClarifier` → `core/services/route_clarity.py` | *(module removed)* |
| `followup_resolution.py` | `FollowupResolutionEngine` → `core/services/followup_resolution.py` | *(module removed)* |
| `rollback_engine.py` | `RollbackEngine` → `core/services/rollback_engine.py` | *(module removed)* |
| `state_mutation.py` | `StateMutationEngine` → `core/services/state_mutation.py` | *(module removed)* |
| `computer_use_verifier.py` | `new_stage_evidence`, `update_stage_evidence`, `evaluate_stage`, `build_verified_payload` → `core/services/computer_use_verifier.py` | *(module removed)* |

---

## Migration Rules

- A **Utility** can become **Hybrid** if it later acquires registry hooks.
- A **Hybrid** can become **Utility** only by removing all registry participation.
- Pure services with no engine behavior may move from `core/engines/` to `core/services/`.
- `AGENTS.md` remains the architectural authority; this doc is a lookup reference.
