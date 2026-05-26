# Task-Verified Memory Insertion Audit

> **Branch:** `audit/task-verified-memory-insertion`  
> **Scope:** Audit/docs only. No runtime behavior changed.  
> **Date:** 2026-05-26

---

## 1. Current Memory Insertion Map

| Hook type | Module | Function | Trigger timing | Memory method called | Test coverage |
|---|---|---|---|---|---|
| `on_turn_end` | `core.engines.memory_insertion` | `_hook_consolidate_recent_memory` | End of every non-synthetic turn | `orc.knowledge.consolidate_memory_async(recent_messages)` (last 3 chat messages) | `tests/test_memory_insertion_engine.py` — 6 tests |
| `on_turn_end` | `core.engines.memory_insertion` | `_hook_refresh_profile_knowledge` | End of every non-synthetic turn | `orc.knowledge.update_knowledge_async(profile_messages)` (last 8 chat messages) | `tests/test_memory_insertion_engine.py` — 6 tests |

Both hooks:
- Skip `synthetic_user_turn` (proactive reminders)
- Require `orc.knowledge_enabled == True`
- Require minimum message counts (3 for consolidation, 4 for profile refresh)
- Are deduplication-guarded in `_HOOKS`

The actual memory graph mutation lives in `memory/world_model.py` (`WorldModelManager`).

---

## 2. Current `on_task_verified` Map

| Aspect | Detail |
|---|---|
| **Fire site** | `core/orchestrator_phases.py` line ~1811, inside `_run_manager_core` |
| **kwargs passed** | `completed_change_operations`, `completed_rollback_manifests`, `completed_all_stages`, `task_failed`, `task_paused` |
| **Current hooks** | 1 — `_hook_record_change_journal` (`core/engines/change_journal.py`) |
| **What the hook does** | Records task outcome to `ChangeJournal`; sets `orc.undo_notice_pending` |
| **Memory touch?** | **None** — change journal is a separate file-backed audit log, not the knowledge graph |

### Task result data shape

`completed_change_operations` is a list of dicts like:
```python
[{"path": "...", "operation": "write", "before_hash": "...", "after_hash": "..."}]
```

This is **implementation detail** (file hashes, paths), not user-facing conversational memory.

`task_failed` and `task_paused` are booleans indicating overall terminal state.

---

## 3. Duplication / Noise Risk

### 3.1 Would task-verified memory duplicate `on_turn_end` memory?

**Yes, substantially.**

- `on_turn_end` already captures the full conversation text (user request + assistant response + system notices).
- The memory archivist prompt (`memory/knowledge_prompts.py`) extracts facts from natural language conversation snippets.
- Task outcomes are already narrated back to the user in the Persona phase and therefore appear in chat history.
- Adding an `on_task_verified` memory hook would feed the same semantic information into memory a second time, likely through a different format.

### 3.2 Would it store implementation details rather than useful user-facing memory?

**Yes.**

- `completed_change_operations` contains file paths, hashes, and operation types.
- The existing memory prompts (`build_memory_archivist_prompt`, `build_world_model_extraction_prompt`) are designed for conversation text, not structured file-operation audit records.
- There is no formatter that converts `completed_change_operations` into natural-language memory sentences.

### 3.3 Could failed/paused tasks pollute memory?

**Yes.**

- A failed task may contain partial or incorrect file mutations.
- A paused task may contain unverified user intent.
- The existing `on_turn_end` hooks already have `fact_is_grounded` filtering and user-statement-authoritative rules.
- Task execution logs do not have equivalent grounding rules.

### 3.4 Could file-operation details become too noisy?

**Yes.**

- Even successful FILE_WORK tasks may touch many files (read, edit, verify).
- The user typically cares about the *outcome* ("the script now prints hello world"), not the intermediate file operations.
- The outcome is already captured in the assistant's response text, which `consolidate_memory_async` processes.

---

## 4. Candidate Designs

### A. LEAVE_AS_IS

- No new `on_task_verified` memory hook.
- Rely on existing `on_turn_end` hooks to capture task outcomes from chat history.

**Pros:** Simple, no noise, no duplication.  
**Cons:** May miss task-specific metadata that could be useful for future task routing.

### B. ADD_FILTERED_TASK_MEMORY

- Register a new `on_task_verified` hook in `core/engines/memory_insertion.py`.
- Only fire when `completed_all_stages and not task_failed and not task_paused`.
- Construct a synthetic message summarizing the task goal and outcome.
- Feed the synthetic message to `consolidate_memory_async` or a new memory method.

**Pros:** Could capture durable task outcomes explicitly.  
**Cons:** Requires new prompt design, new formatter, and careful filtering. Risk of duplicating conversation memory.

### C. ADD_TESTS_FIRST

- Write tests defining expected behavior before adding any hook.
- Useful if we choose B later.
- Does not answer the architectural question now.

### D. DEFER_UNTIL_MEMORY_POLICY_EXISTS

- Do not add an `on_task_verified` memory hook today.
- Wait until Piper has an explicit policy document defining:
  - What task outcomes should become durable memory
  - How task metadata should be formatted for the memory archivist
  - How failed/paused tasks should be handled
  - How to avoid duplication with `on_turn_end` conversation memory

**Pros:** Prevents premature design; keeps memory system clean.  
**Cons:** Slightly slower if task-aware routing becomes a priority.

---

## 5. Recommendation

**`DEFER_UNTIL_MEMORY_POLICY_EXISTS`**

Rationale:
1. The existing `on_turn_end` memory hooks already capture task outcomes through conversation text.
2. `on_task_verified` carries implementation-detail data (`completed_change_operations`) that is not designed for the memory archivist.
3. There is no policy or prompt that defines how task execution metadata should be transformed into durable memory.
4. Adding a hook now would likely create noise and duplication without clear user benefit.
5. If future routing or recall needs task-specific memory, the correct path is:
   - Define a memory policy for task outcomes
   - Design a formatter that converts task metadata into archivist-compatible text
   - Then add a filtered `on_task_verified` hook behind a policy gate

---

## 6. Issues / Smells

| Severity | Issue | Location | Notes |
|---|---|---|---|
| **WATCH** | `on_task_verified` passes rich task metadata but only the change journal uses it. | `core/orchestrator_phases.py:1811` | This is by design today, but it means the hook payload is under-utilized. |
| **IGNORE_FOR_NOW** | `completed_change_operations` contains raw file paths and hashes that are not suitable for memory storage. | `core/orchestrator_phases.py` | Change journal owns this data; memory does not need it. |
| **IGNORE_FOR_NOW** | `consolidate_memory_async` and `update_knowledge_async` expect chat-history-shaped input, not task metadata. | `memory/world_model.py` | A task-memory hook would need a new formatter or memory method. |

---

## Validation Log

```
python -m compileall app.py config.py core ui memory tools llm web_ui   → OK
python scripts/engine_registry_inventory.py --json                        → 9 interceptors, 10 hooks, 13 tail blocks
python -m pytest tests/test_memory_insertion_engine.py -q                → 12 passed
python -m pytest tests/test_engine_hook_registration.py -q               → 12 passed
python -m pytest tests/test_engine_registry_inventory.py -q              → 17 passed
```
