# StateMutationEngine Service Readiness Audit

**Status:** Active audit  
**Source:** `core/engines/state_mutation.py`  
**Possible target:** `core/services/state_mutation.py`  
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
| `core/executor.py` | `from core.engines.state_mutation import StateMutationEngine` | `self.state_mutation_engine = StateMutationEngine()`; calls `memory_remove_listing_confirms_absent()` |
| `core/orchestrator_phases.py` | `from core.engines.state_mutation import StateMutationEngine` | `_STATE_MUTATION_ENGINE = StateMutationEngine()`; injected into `FollowupResolutionEngine` |
| `core/prompt_context.py` | `from core.engines.state_mutation import StateMutationEngine` | `state_mutation_engine: StateMutationEngine` field; calls `build_readonly_answer()` |
| `core/routing/route_normalizer.py` | `from core.engines.state_mutation import StateMutationEngine` | `_STATE_MUTATION_ENGINE = StateMutationEngine()`; calls `_registered_state_mutation_normalization()` |
| `core/scratchpad_formatter.py` | `from core.engines.state_mutation import StateMutationEngine` | `_STATE_MUTATION_ENGINE = StateMutationEngine()`; calls `build_outcome_pack()` |
| `core/services/followup_resolution.py` | accepts `state_mutation_engine` param | Injected dependency; calls `build_task_event_delete_route()`, `build_task_event_completion_route()`, `build_memory_store_route()`, `build_memory_remove_route()` |
| `core/engines/__init__.py` | `from core.engines.state_mutation import StateMutationEngine` | Re-exports `StateMutationEngine` |
| `scripts/state_mutation_engine_smoke_test.py` | `from core.engines.state_mutation import StateMutationEngine` | Comprehensive smoke test |
| `scripts/followup_resolution_engine_smoke_test.py` | `from core.engines.state_mutation import StateMutationEngine` | Injects into `FollowupResolutionEngine` |

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

## 7. Missing Tests / Smokes

The following public methods have **no dedicated test coverage** beyond what the smoke test may incidentally touch:

| Method | Risk level | Notes |
|--------|-----------|-------|
| `_normalize_schedule_correction_to_chat` | Low | Edge-case date correction routing |
| `_normalize_event_followup_inspection` | Low | Event inspection followup |
| `_normalize_retry_from_latest_runtime_context` | Low | "try again" retry routing |
| `_normalize_reminder_task_override_followup` | Low | Reminder → task override |
| `_normalize_casual_completion_to_chat` | Low | Casual completion downgrade |
| `_normalize_chat_task_event_followup` | Medium | Chat → task/event completion upgrade |
| `stage_entries_indicate_terminal_failure` | Low | Wrapper around `build_outcome_pack` |
| `_extract_contextual_memory_remove_subject` | Low | Contextual memory removal |
| `_extract_work_state_remove_subject` | Low | Work-state removal parsing |
| `_bind_completion_target_from_recent_context` | Medium | Completion target binding from history |
| `_looks_like_file_work_request` | Low | File work detection |
| `build_task_event_delete_route` | Low | Indirectly covered by followup smoke |
| `build_task_event_completion_route` | Low | Indirectly covered by followup smoke |

**Critical observation:** The smoke test covers the **main public API surface** well, but edge-case normalizers (particularly `_normalize_chat_task_event_followup` and `_bind_completion_target_from_recent_context`) are undertested. These normalizers affect routing decisions and could cause regressions if accidentally modified.

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

**B) Safe only after adding tests.**

**Rationale:**

`StateMutationEngine` is correctly classified as a **pure direct-call utility** with no hooks, registries, or lifecycle participation. In principle, it is eligible for relocation to `core/services/` under the established doctrine.

However, unlike `RollbackEngine` (262 lines, 3 callers, 12-case smoke test covering all public functions), `StateMutationEngine` is:

- **2,279 lines** — the largest direct-call utility in the codebase
- **7 production callers** — a wide blast radius for import updates
- **Governs routing decisions** for tasks, events, and knowledge — a behavioral regression here would be user-visible
- **Missing unit tests** — only a single smoke test exists; edge-case normalizers are not covered

The smoke test is **good but not comprehensive**. Edge-case routing normalizers (`_normalize_chat_task_event_followup`, `_bind_completion_target_from_recent_context`) that affect task/event completion vs. chat downgrade decisions are undertested. A relocation itself is mechanical, but if any import cycle or initialization-order issue surfaces (e.g., `core/services/followup_resolution.py` already imports from `core/engines/state_mutation.py`), the lack of unit tests makes regression detection harder.

**Recommended next steps before relocation:**

1. Add a `tests/test_state_mutation.py` covering:
   - `normalize_route_decision()` for all major route types (task add, event schedule, knowledge store/remove, reminder, delete followup, completion followup, plural followup)
   - `build_outcome_pack()` for all stage types with success/failure/edge cases
   - `build_readonly_answer()` for knowledge, operational, and profile summary queries
   - `classify_knowledge_intent()` for all decision branches
   - `classify_task_event_followup()` for all decision branches
   - `memory_remove_listing_confirms_absent()` for absent/present/ambiguous cases

2. Once unit tests pass, proceed with relocation on a separate `move/state-mutation-service` branch.

3. After relocation, run the full validation suite: `compileall` + `pytest tests/` + `pytest web_ui/bridge/` + `state_mutation_engine_smoke_test.py` + `followup_resolution_engine_smoke_test.py`.

---

## 10. Stale Doc References Found

The following doc files reference `core/engines/state_mutation.py` and will need updating **after** relocation (not in this audit branch):

- `docs/architecture/TRIGGER_FLOW.md` — lines 716, 1476
- `docs/checkpoints/CODE_CLEANUP_AUDIT.md` — line 21
- `docs/foundation/VERIFICATION_ENGINE.md` — lines 26, 63, 112

These are documentation-only references. No runtime code changes.
