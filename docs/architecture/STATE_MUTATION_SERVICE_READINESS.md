# StateMutationEngine Service Readiness Audit

**Status:** Active audit  
**Source:** `core/services/state_mutation.py`  
**Relocated to:** `core/services/state_mutation.py`  
**Date:** 2026-05-23

---

## 1. Behavior Classification

**Bucket:** Direct-Call Utility  
**No hooks, registries, tail-blocks, interceptors, or lifecycle participation.**

`StateMutationEngine` is a frozen `@dataclass` exposing only `@staticmethod` and regular instance methods. It is imported and invoked directly by orchestrator, routing, and prompt-building code. It does not register itself with any engine lifecycle system.

---

## 2. Caller Map

| Caller | Import line | Usage |
|--------|-------------|-------|
| `core/executor.py` | `from core.services.state_mutation import StateMutationEngine` | `self.state_mutation_engine = StateMutationEngine()`; calls `memory_remove_listing_confirms_absent()` |
| `core/orchestrator_phases.py` | `from core.services.state_mutation import StateMutationEngine` | `_STATE_MUTATION_ENGINE = StateMutationEngine()`; injected into `FollowupResolutionEngine` |
| `core/prompt_context.py` | `from core.services.state_mutation import StateMutationEngine` | `state_mutation_engine: StateMutationEngine` field; calls `build_readonly_answer()` |
| `core/routing/route_normalizer.py` | `from core.services.state_mutation import StateMutationEngine` | `_STATE_MUTATION_ENGINE = StateMutationEngine()`; calls `_registered_state_mutation_normalization()` |
| `core/scratchpad_formatter.py` | `from core.services.state_mutation import StateMutationEngine` | `_STATE_MUTATION_ENGINE = StateMutationEngine()`; calls `build_outcome_pack()` |
| `core/services/followup_resolution.py` | accepts `state_mutation_engine` param | Injected dependency; calls `build_task_event_delete_route()`, `build_task_event_completion_route()`, `build_memory_store_route()`, `build_memory_remove_route()` |
| `core/engines/__init__.py` | `from core.services.state_mutation import StateMutationEngine` | Re-exports `StateMutationEngine` |
| `scripts/state_mutation_engine_smoke_test.py` | `from core.services.state_mutation import StateMutationEngine` | Comprehensive smoke test |
| `scripts/followup_resolution_engine_smoke_test.py` | `from core.services.state_mutation import StateMutationEngine` | Injects into `FollowupResolutionEngine` |

**Total production callers:** 7 files (plus package init).  
**Total test/script callers:** 2 files.

---

## 3. Import / Export Map

**Current exports (`core/engines/__init__.py`):**
- `StateMutationEngine`

**No other re-exports.** `core/services/__init__.py` does not yet export `StateMutationEngine`.

---

## 4. Runtime Responsibilities

`StateMutationEngine` owns the following runtime responsibilities:

### 4.1 Route Decision Normalization
- `normalize_route_decision()` — main entry point for post-router route correction
- Task/event stage collapse (merges multi-stage task cards into single stages)
- Event-vs-task disambiguation (`_request_should_be_event`)
- Reminder request normalization (`_normalize_reminder_request`)
- Schedule correction routing (`_normalize_schedule_correction_to_chat`)
- Casual completion → CHAT downgrade (`_normalize_casual_completion_to_chat`)
- Event inspection followup routing (`_normalize_event_followup_inspection`)
- Plural task/event followup (`_normalize_plural_task_event_followup`)
- Chat task/event followup completion (`_normalize_chat_task_event_followup`)
- Retry-from-runtime-context (`_normalize_retry_from_latest_runtime_context`)
- Reminder-task override followup (`_normalize_reminder_task_override_followup`)
- Contextual remember followup (`_normalize_contextual_remember_followup`)
- Knowledge route normalization (`_normalize_knowledge_route`)
- Task/event completion routing (`_normalize_task_event_completion_route`)
- Task/event delete routing (`_normalize_task_event_delete_followup`)

### 4.2 Knowledge Intent Classification
- `classify_knowledge_intent()` — decides query/store/remove/none for knowledge mutations
- `classify_contextual_remember_intent()` — handles "remember that..." followups
- `memory_remove_target()` — extracts target from stage/entries for memory removal
- `memory_remove_listing_confirms_absent()` — checks if a LIST result confirms absence

### 4.3 Task/Event Intent Classification
- `classify_task_event_followup()` — decides complete/delete/inspect/none for task/event followups

### 4.4 Outcome Packaging
- `build_outcome_pack()` — produces `StageOutcomePack` for TASK_EVENT_WORK and MEMORY_WORK stages
- `stage_entries_indicate_terminal_failure()` — wrapper around outcome pack
- `_build_task_event_outcome()` — task/event specific outcome logic
- `_build_memory_outcome()` — memory/knowledge specific outcome logic

### 4.5 Readonly Answer Building
- `build_readonly_answer()` — answers knowledge queries and task/event readonly questions
- `_build_profile_summary_answer()` — builds "what do you know about me" summary

### 4.6 Mutation Request Building
- `build_mutation_request()` — builds `StateMutationRequest` dicts
- `stage_mutation_request()` — extracts mutation from `StageCard`
- Various `_build_*_card()` helpers for tasks, events, deletions, completions

### 4.7 Transient State Detection
- `looks_like_transient_remember_request()` — detects "I am hungry", "I am working on..."
- `looks_like_transient_assertion()` — detects transient user state assertions
- `looks_like_contextual_remember_followup()` — detects "just remember that"
- `looks_like_ambiguous_memory_followup()` — detects vague memory followups

---

## 5. Behavior That Must Not Change During Relocation

All of the above. Zero behavior change. This is an import-only relocation.

Specifically:
- `normalize_route_decision()` semantics govern task/event/knowledge routing
- `build_outcome_pack()` semantics govern stage success/failure classification
- `build_readonly_answer()` semantics govern knowledge and operational readonly responses
- `classify_knowledge_intent()` semantics govern durable memory mutations
- `classify_task_event_followup()` semantics govern task/event lifecycle

---

## 6. Current Tests / Smokes

### 6.1 Smoke Test: `scripts/state_mutation_engine_smoke_test.py`

A single comprehensive smoke test (`run_smoke()`) covering approximately 28 assertions:

| Method | Cases covered |
|--------|---------------|
| `classify_task_event_followup` | Chat correction (event already booked), Task completion (bought milk) |
| `classify_knowledge_intent` | Query (favorite drink), Store (favorite drink = coffee), Remove (forget), Project remove (not working on...) |
| `build_outcome_pack` | False success (event not found), Proposal only, Empty list mutation + auto-reroute, Specific memory listing (generic world state), Knowledge success, Knowledge failure |
| `memory_remove_listing_confirms_absent` | Target absent (confirmed), Target present (not confirmed) |
| `build_readonly_answer` | Knowledge query, Event query, State assertion |
| `normalize_route_decision` | Task delete followup, Contextual remember, Reminder with date, Plural delete, Natural event completion (bind from runtime context) |
| `ScratchpadFormatter.build_outcome_pack` | Formatter delegation (false success, specific memory listing) |

**Result:** Passes on current main.

### 6.2 Indirect Coverage

- `scripts/followup_resolution_engine_smoke_test.py` — injects `StateMutationEngine` into `FollowupResolutionEngine` and exercises `build_task_event_delete_route()`, `build_task_event_completion_route()`, `build_memory_store_route()`, `build_memory_remove_route()` through followup resolution paths.

### 6.3 Pytest Unit Tests

**None in `tests/` directory.** No `test_state_mutation.py` or `test_engines.py` entries found.

---

## 7. Tests Added

`tests/test_state_mutation.py` (56 tests) was added on branch `test/state-mutation-core-behavior`. It covers:

- `classify_knowledge_intent()` — query, store, remove, none, empty, transient
- `classify_task_event_followup()` — complete task, chat correction, inspect event, none, file work override
- `memory_remove_listing_confirms_absent()` — absent confirmed, present not confirmed, ambiguous not confirmed, non-memory stage
- `build_readonly_answer()` — knowledge query, knowledge not found, task/event readonly, profile summary, empty query
- `build_outcome_pack()` — TASK_EVENT_WORK success/failure, MEMORY_WORK success/failure, proposal-only, empty-list auto-reroute, IMAGE_WORK, FILE_WORK
- `normalize_route_decision()` — task add, event schedule, knowledge store/remove, reminder, delete followup, completion followup, plural followup, contextual remember, chat stays chat
- Regression tests for undertested paths:
  - `_normalize_chat_task_event_followup`
  - `_bind_completion_target_from_recent_context` (via natural event completion)
  - `_normalize_schedule_correction_to_chat`
  - `_normalize_event_followup_inspection`
  - `_normalize_retry_from_latest_runtime_context`
  - `_normalize_reminder_task_override_followup`
  - `_normalize_casual_completion_to_chat`
  - `_extract_contextual_memory_remove_subject`
  - `_extract_work_state_remove_subject`
  - `_looks_like_file_work_request`

## 7A. Remaining Gaps

The following paths still have **no dedicated test coverage**:

| Method | Risk level | Notes |
|--------|-----------|-------|
| `stage_entries_indicate_terminal_failure` | Low | Wrapper around `build_outcome_pack` |
| `build_task_event_delete_route` | Low | Indirectly covered by followup smoke |
| `build_task_event_completion_route` | Low | Indirectly covered by followup smoke |

All previously undertested private normalizers are now covered by the new unit tests.

However, a pure import relocation **cannot** break these normalizers because no code changes.

---

## 8. Recommended Target Path

```
core/services/state_mutation.py
```

`StateMutationEngine` should be exported from `core/services/__init__.py` alongside the other relocated services.

`core/engines/__init__.py` should remove `StateMutationEngine` from its exports.

---

## 9. Recommendation

**A) Safe to relocate after tests are green.**

**Rationale:**

`StateMutationEngine` is correctly classified as a **pure direct-call utility** with no hooks, registries, or lifecycle participation. It is eligible for relocation to `core/services/` under the established doctrine.

It is **2,279 lines** with **7 production callers** and governs routing for tasks, events, and knowledge. The blast radius is large, but the relocation itself is mechanical (import-only).

**Unit tests have now been added** (`tests/test_state_mutation.py`, 56 tests) covering:
- All main public API methods (`classify_knowledge_intent`, `classify_task_event_followup`, `memory_remove_listing_confirms_absent`, `build_readonly_answer`, `build_outcome_pack`, `normalize_route_decision`)
- All previously undertested private normalizers identified in the audit

The remaining gaps (`stage_entries_indicate_terminal_failure`, `build_task_event_delete_route`, `build_task_event_completion_route`) are low-risk wrappers or indirectly covered by followup smoke tests.

**Recommended next steps for relocation:**

1. Create `move/state-mutation-service` branch.
2. Move `core/services/state_mutation.py` → `core/services/state_mutation.py`.
3. Update imports in 7 production callers + 2 test/script files.
4. Update `core/services/__init__.py` and `core/engines/__init__.py` exports.
5. Run validation: `compileall` + `pytest tests/` + `pytest web_ui/bridge/` + `state_mutation_engine_smoke_test.py` + `followup_resolution_engine_smoke_test.py` + `route_boundary_smoke_test.py`.

---

## 10. Stale Doc References Found

The following doc files reference `core/services/state_mutation.py` and will need updating **after** relocation (not in this audit branch):

- `docs/architecture/TRIGGER_FLOW.md` — lines 716, 1476
- `docs/checkpoints/CODE_CLEANUP_AUDIT.md` — line 21
- `docs/foundation/VERIFICATION_ENGINE.md` — lines 26, 63, 112

These are documentation-only references. No runtime code changes.
