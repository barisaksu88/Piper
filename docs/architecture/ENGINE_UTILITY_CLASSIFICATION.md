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
| `conversation_compressor.py` | `@register_hook("on_turn_end")` for deferred conversation summarization | `ConversationCompressor.compress_history()`, `.load_summary()`, `.save_summary()` |
| `context_pack.py` | Owns `_TAIL_BLOCK_REGISTRY` + `register_tail_block()`; 11 tail-block builders in this file + 2 in `proactive_monitor.py`; `@register_hook("on_turn_end")` | `ContextPackEngine.build_persona_pack()`, `.build_runtime_context_pack()`, `ContextPackRenderer.render_runtime_context_message()` |
| `change_journal.py` | `@register_hook("on_task_verified")` to record change journal after task verification | `ChangeJournal.record_turn()`, `.prepare_file_op_capture()`, `.finalize_file_op_capture()`, `.undo_latest()` |
| `stats_collector.py` | `@register_hook("on_pre_route")` to note user message before routing | `StatsCollector.resume_or_start_turn()`, `.note_route()`, `.record_turn()`, `.build_dashboard_snapshot()` |
| `proactive_monitor.py` | `@register_tail_block`, `@register_hook("on_turn_end")`, `@register_route_interceptor` for reminder interception | `ProactiveMonitor` lifecycle (start/stop/loop), `ReminderStore` (add/due_entries/mark_fired), `parse_reminder_request()` |

### 3. Direct-Call Utilities

Modules that expose a direct-call service API and **do not** register hooks, tail-blocks, interceptors, or any other lifecycle mechanism. These are pure utilities imported and invoked by orchestrator or controller code.

| Module | Direct-Call Service Behavior |
|--------|------------------------------|
| `summary.py` | `SummaryEngine` — scratchpad extraction, outcome building, text utilities. No hooks. |
| `verification.py` | `VerificationEngine` — `evaluate()`, `evaluate_mutation()`, `evaluate_with_constraints()`. No hooks. |
| `file_work.py` | `FileWorkEngine` — file operation planning and execution. No hooks. |
| `followup_resolution.py` | `FollowupResolutionEngine` — follow-up intent resolution. No hooks. |
| `route_clarity.py` | `RouteClarifier` — route clarification logic. No hooks. |
| `state_mutation.py` | `StateMutationEngine` — state mutation planning. No hooks. |
| `computer_use_engine.py` | `ComputerUseEngine` — computer-use orchestration. No hooks. |
| `computer_use_verifier.py` | `ComputerUseVerifier` — computer-use verification. No hooks. |
| `rollback_engine.py` | `invert_manifest()` and rollback utilities. No hooks. |

---

## Migration Rules

## Services outside `core/engines/`

`SearchWorkflowEngine` was relocated from `core/engines/search_workflow.py` to `core/services/search_workflow.py` because it is a pure direct-call service with no hooks, registries, or lifecycle participation.  It is imported by orchestrator and UI layers exactly like a utility, but lives in `core/services/` to keep `core/engines/` reserved for modules that own runtime engine behavior.

---

## Migration Rules

- A **Utility** can become **Hybrid** if it later acquires registry hooks.
- A **Hybrid** can become **Utility** only by removing all registry participation.
- Pure services with no engine behavior may move from `core/engines/` to `core/services/`.
- `AGENTS.md` remains the architectural authority; this doc is a lookup reference.
