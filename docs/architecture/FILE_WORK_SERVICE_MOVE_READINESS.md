# FileWorkEngine Service Move Readiness Audit

**Status:** Audit complete — recommendation: add tests first  
**Branch:** `audit/file-work-service-move-readiness`  
**Date:** 2026-05-24  
**Source:** `core/engines/file_work.py`  
**Possible target:** `core/services/file_work.py`

---

## 1. Behavior Classification

FileWorkEngine is a **pure direct-call utility**.

- All public methods are `staticmethod` or `classmethod` — zero instance state.
- No hooks, registries, tail-blocks, interceptors, or lifecycle participation.
- No background threads or async loops.
- No mutable module-level state.

**Behaviorally identical to** `SearchWorkflowEngine`, `SummaryEngine`, and
`VerificationEngine` — all already relocated to `core/services/`.

---

## 2. Caller Map

### 2.1 Production code (direct import of `core.engines.file_work`)

| File | Usage |
|------|-------|
| `core/engines/__init__.py` | Package export |
| `core/executor.py` | `should_block`, `candidate_paths`, `exact_read_paths_from_scratchpad`, `capture_exact_read`, `render_artifact_view`, `recovery_hint` |
| `core/file_checker.py` | `candidate_paths` |
| `core/file_stage_policy.py` | `recovery_hint` (deprecation wrapper) |
| `core/planner_boundary.py` | `classify` |
| `core/prompt_builder.py` | `exact_read_paths_from_scratchpad` |
| `core/routing/route_normalizer.py` | `classify` |
| `core/services/verification.py` | `derive_constraints` (lazy import to avoid circular) |

### 2.2 Test / smoke code

| File | Usage |
|------|-------|
| `scripts/test_engines.py` | 26 unit tests in `TestFileWorkEngine` |
| `scripts/file_work_engine_smoke_test.py` | Full public API smoke (8 test functions) |
| `scripts/redundant_code_read_guard_smoke_test.py` | Guard 2 (redundant read) |
| `scripts/code_file_write_fallback_smoke_test.py` | Guards 2 & 3 (redundant read + write_text embedding) |
| `scripts/planner_schema_compliance_smoke_test.py` | `derive_constraints` |
| `scripts/file_work_state_isolation_smoke_test.py` | End-to-end persona isolation |

---

## 3. Safety Responsibilities Owned by FileWorkEngine

### 3.1 Path extraction & evidence handling
- `candidate_paths(tool_result)` — unified superset extraction from tool result dicts.
- `exact_read_paths_from_scratchpad(scratchpad)` — parses `FILE_READ_EXACT_PATH` entries.
- `render_artifact_view(tool_result)` — code preview for UI (caps at 3 files / 6 dict entries).
- `capture_exact_read(stage, tool_result, existing_read_paths)` — decides if a read result is captured to scratchpad, with file-count and char-count budgets.
- `collect_evidence(...)` — convenience bundle of the above three.

### 3.2 Blocked-write guards (`should_block`)
Three guards, checked in order:

| Guard | Trigger | Severity | Tested? |
|-------|---------|----------|---------|
| **1. Cross-domain dependency** | DELETE or MOVE targets a path referenced by an active task/event (R-6 State Mutex). | **FATAL** — executor stops the stage entirely. | **NO** |
| **2. Redundant exact read** | Planned read of a file already captured in scratchpad this stage. | Blocked — retry allowed. | Yes |
| **3. Full-source embedding** | `write_text` payload for a code file exceeds 50k chars or exact-read paths exist. | Blocked — retry allowed. | Yes |

### 3.3 RUN_CODE safety guards
| Guard | Method | Trigger | Tested? |
|-------|--------|---------|---------|
| Domain escape | `_check_run_code_task_event_escape` | FILE_WORK `RUN_CODE` tries to import/call task/event helpers (`add_event`, `list_tasks`, etc.) | **NO** |
| Active dependency | `_check_run_code_dependency` | `RUN_CODE` body deletes/moves a path referenced by an active task/event | **NO** |

### 3.4 Other responsibilities
- `recovery_hint(stage, tool_result, file_check)` — guides planner past FAILED verification.
- `classify(stage)` — maps `StageCard` → `FileStageKind`.
- `derive_constraints(stage, tool_result)` — derives `PlanConstraint` list for `VerificationEngine`.
- `CODE_FILE_EXTENSIONS` registry — single definition site for executor-side code extensions.

---

## 4. Behavior That Must Not Change During Relocation

- All guard logic, ordering, and error message wording.
- Exact-read budget constants (`EXACT_READ_MAX_FILES=2`, `EXACT_READ_MAX_TOTAL_CHARS=14_000`).
- `CODE_WRITE_TEXT_TAG_MAX_CHARS=50_000`.
- `TASK_EVENT_RUN_CODE_HELPERS` and `TASK_EVENT_RUN_CODE_STORES` frozensets.
- Path normalization (backslash → forward-slash, lower-case dedup in scratchpad parsing).
- The lazy import of `FileWorkEngine` inside `core/services/verification.py` (avoid circular).
- The lazy import inside `core/file_stage_policy.py` deprecation wrapper.

---

## 5. Current Test Coverage

### 5.1 Unit tests (`scripts/test_engines.py::TestFileWorkEngine`)
26 tests total:

| Method | Tests | Count |
|--------|-------|-------|
| `candidate_paths` | extracts from dict, normalizes backslashes, deduplicates, extracts from `files` dict, extracts from snippets, empty for non-dict | 6 |
| `exact_read_paths_from_scratchpad` | parses entries, deduplicates case-insensitively | 2 |
| `_is_code_path` | detects code, rejects non-code | 2 |
| `render_artifact_view` | renders code, uses snippets, empty for non-code | 3 |
| `capture_exact_read` | single file, multiple under limit, respects existing paths, none for non-read | 4 |
| `should_block` | redundant exact read, allows new read, write_text embedding, allows write after exact read | 4 |
| `classify` | returns correct kinds for all stage types | 1 |
| `recovery_hint` | invalid JSON hint, code mismatch hint | 2 |
| `collect_evidence` | combines all three | 1 |
| `should_verify` integration (in `TestEngineIntegration`) | file work stage triggers verification | 1 |

### 5.2 Smoke tests

| Script | Coverage |
|--------|----------|
| `file_work_engine_smoke_test.py` | candidate_paths, exact_read_paths, render_artifact_view, capture_exact_read, should_block, recovery_hint, classify, CODE_FILE_EXTENSIONS |
| `redundant_code_read_guard_smoke_test.py` | Guard 2 via executor + prompt builder integration |
| `code_file_write_fallback_smoke_test.py` | Guards 2 & 3 |
| `planner_schema_compliance_smoke_test.py` | derive_constraints (explicit, MOVED, DELETED, CREATED-not-derived, bulk-empty) |
| `file_work_state_isolation_smoke_test.py` | End-to-end FILE_WORK → chat isolation |

---

## 6. Missing Coverage (Critical Gaps)

The following safety-critical paths have **zero automated tests**:

| Path | Risk if broken during move |
|------|---------------------------|
| `_check_active_dependency` | FATAL blocks stop firing → active task/event files can be deleted/moved silently |
| `_check_run_code_dependency` | RUN_CODE can delete/move active files without detection |
| `_check_run_code_task_event_escape` | FILE_WORK stages can mutate task/event state via RUN_CODE |
| `dependency_override_authorized=True` path | Override flag is ignored → false fatal blocks or bypassed blocks |
| `operational_state_service.find_references()` integration | Cross-domain dependency guard is a no-op at runtime |

**These are the three most safety-critical guards in FileWorkEngine.**
Their absence means a relocation could break an import or parameter-passing
path and the failure would not be caught by any existing test.

---

## 7. Recommendation

**B) Safe only after adding tests.**

FileWorkEngine is behaviorally a pure service and structurally safe to move,
but the three safety guards (`_check_active_dependency`,
`_check_run_code_dependency`, `_check_run_code_task_event_escape`) are
completely untested. Moving now would carry unquantified risk.

**Minimum tests to add before relocation:**

1. `_check_active_dependency`:
   - Returns `fatal=True` block when DELETE targets an active-referenced path.
   - Returns `fatal=True` block when MOVE targets an active-referenced path.
   - Returns non-blocked when no conflict.
   - Returns non-blocked when `dependency_override_authorized=True`.

2. `_check_run_code_dependency`:
   - Detects `os.remove("active_file.txt")` and returns fatal block.
   - Detects `shutil.move("active_file.txt", ...)` and returns fatal block.
   - Skips dynamic/path-variable code silently.
   - Returns non-blocked when `dependency_override_authorized=True`.

3. `_check_run_code_task_event_escape`:
   - Blocks `from workspace import add_event`.
   - Blocks direct call to `add_task(...)`.
   - Blocks `event_store` attribute access.
   - Allows plain file I/O without blocking.

Once those tests exist and pass, FileWorkEngine can be relocated with the
same zero-behavior-change pattern used for `SearchWorkflowEngine`,
`SummaryEngine`, and `VerificationEngine`.

---

## 8. Import / Export Map (for future move)

Files that would need import updates:

```
core/engines/__init__.py          — remove FileWorkEngine export
core/services/__init__.py         — add FileWorkEngine export
core/executor.py                  — update import
core/file_checker.py              — update import
core/file_stage_policy.py         — update import (deprecation wrapper)
core/planner_boundary.py          — update import
core/prompt_builder.py            — update import
core/routing/route_normalizer.py  — update import
core/services/verification.py     — update lazy import

scripts/test_engines.py                          — update imports
scripts/file_work_engine_smoke_test.py           — update import
scripts/redundant_code_read_guard_smoke_test.py  — update import
scripts/code_file_write_fallback_smoke_test.py   — update import
scripts/planner_schema_compliance_smoke_test.py  — update import

docs/architecture/TRIGGER_FLOW.md                     — path updates
docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md    — table update
docs/foundation/FILEWORK_ENGINE.md                    — path updates
docs/foundation/BLUEPRINT.md                          — path updates
```

---

## 9. Notes

- `FileWorkEngine` is the **last remaining** Direct-Call Utility in `core/engines/`.
  All others (`search_workflow.py`, `summary.py`, `verification.py`) have already
  been relocated to `core/services/`.
- `file_work.py` has **no registry behavior** and is therefore the cleanest
  remaining candidate for relocation.
- The only blocker is test coverage for the safety guards, not architecture.
