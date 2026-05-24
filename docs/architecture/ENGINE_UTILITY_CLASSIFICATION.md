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

*None currently in `core/engines/`.* All modules that register hooks also expose direct-call behavior.

### 2. Hybrid Modules

Modules that **both** register hooks / tail-blocks / interceptors **and** expose direct-call service behavior. These are the most common pattern in `core/engines/`.

| Module | Registry Behavior | Direct-Call Service Behavior |
|--------|-------------------|------------------------------|
| `conversation_compressor.py` | `@register_hook("on_turn_end")` for deferred conversation summarization | `ConversationCompressor` class split to `core/services/conversation_compressor.py`; hook remains in `core/engines/conversation_compressor.py` |
| `context_pack.py` | Owns `_TAIL_BLOCK_REGISTRY` + `register_tail_block()`; 11 tail-block builders in this file + 2 in `proactive_monitor.py`; `@register_hook("on_turn_end")` | `ContextPackEngine.build_persona_pack()`, `.build_runtime_context_pack()`, `ContextPackRenderer.render_runtime_context_message()` |

`context_pack.py` audit completed — see `docs/architecture/CONTEXT_PACK_SPLIT_READINESS.md`. Recommendation: **split first, then move pure service pieces** (registry and hook must remain in `core/engines/`).
| `change_journal.py` | `@register_hook("on_task_verified")` to record change journal after task verification | `ChangeJournal.record_turn()`, `.prepare_file_op_capture()`, `.finalize_file_op_capture()`, `.undo_latest()` |
| `stats_collector.py` | `@register_hook("on_pre_route")` to note user message before routing | `StatsCollector.resume_or_start_turn()`, `.note_route()`, `.record_turn()`, `.build_dashboard_snapshot()` |
| `proactive_monitor.py` | `@register_tail_block`, `@register_hook("on_turn_end")`, `@register_route_interceptor` for reminder interception | `ProactiveMonitor` lifecycle (start/stop/loop), `ReminderStore` (add/due_entries/mark_fired), `parse_reminder_request()` |

`conversation_compressor.py` has been **split**. The `ConversationCompressor` class now lives in `core/services/conversation_compressor.py`; only the `_hook_deferred_conversation_summary` hook remains in `core/engines/conversation_compressor.py`. See `docs/architecture/CONVERSATION_COMPRESSOR_SPLIT_READINESS.md`.

### 3. Direct-Call Utilities

Modules that expose a direct-call service API and **do not** register hooks, tail-blocks, interceptors, or any other lifecycle mechanism. These are pure utilities imported and invoked by orchestrator or controller code.

*All eligible direct-call utilities have been relocated to `core/services/`.*

Remaining modules in `core/engines/` are **hybrids or lifecycle/resource-owning engines** (see below), not pure direct-call utilities.

---

### 3A. Engines That Stay in `core/engines/`

Modules that own mutable state, manage external resources, use threading, or participate in lifecycle management. These are **not** candidates for `core/services/`.

| Module | Why it stays in `core/engines/` |
|--------|--------------------------------|
| `computer_use_engine.py` | `ComputerUseEngine` owns a live Playwright browser session (`_BrowserSessionState`), uses `_playwright_lock` (RLock), has `shutdown()`/`suspend()` lifecycle methods, and is lazily initialized by `core/agent.py`. See `docs/architecture/COMPUTER_USE_ENGINE_SERVICE_READINESS.md`.

### 3B. Split Completed

`conversation_compressor.py` — the `ConversationCompressor` class and `ConversationCompressionResult` dataclass have been moved to `core/services/conversation_compressor.py`. The `@register_hook("on_turn_end")` hook (`_hook_deferred_conversation_summary`) remains in `core/engines/conversation_compressor.py`.

## Services outside `core/engines/`

`SearchWorkflowEngine` was relocated from `core/engines/search_workflow.py` to `core/services/search_workflow.py` because it is a pure direct-call service with no hooks, registries, or lifecycle participation.

`SummaryEngine` was relocated from `core/engines/summary.py` to `core/services/summary.py` for the same reason.

`VerificationEngine` (and `VerificationResult`) was relocated from `core/engines/verification.py` to `core/services/verification.py` for the same reason.

`FileWorkEngine` was relocated from `core/engines/file_work.py` to `core/services/file_work.py` for the same reason.  It was the last high-risk file-operation service move in this pass.

`RouteClarifier` was relocated from `core/engines/route_clarity.py` to `core/services/route_clarity.py` for the same reason.

`FollowupResolutionEngine` was relocated from `core/engines/followup_resolution.py` to `core/services/followup_resolution.py` for the same reason.

`RollbackEngine` / `rollback_engine.py` was relocated from `core/engines/rollback_engine.py` to `core/services/rollback_engine.py` because it is a pure direct-call utility with no hooks, registries, or lifecycle participation.

`StateMutationEngine` / `state_mutation.py` was relocated from `core/engines/state_mutation.py` to `core/services/state_mutation.py` because it is a pure direct-call utility with no hooks, registries, or lifecycle participation.

`computer_use_verifier` module (`new_stage_evidence`, `update_stage_evidence`, `evaluate_stage`, `build_verified_payload`) was relocated from `core/engines/computer_use_verifier.py` to `core/services/computer_use_verifier.py` because it is a pure direct-call utility module with no hooks, registries, or lifecycle participation.

---

## Migration Rules

- A **Utility** can become **Hybrid** if it later acquires registry hooks.
- A **Hybrid** can become **Utility** only by removing all registry participation.
- Pure services with no engine behavior may move from `core/engines/` to `core/services/`.
- `AGENTS.md` remains the architectural authority; this doc is a lookup reference.
