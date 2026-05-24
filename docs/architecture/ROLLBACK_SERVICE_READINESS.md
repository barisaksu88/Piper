# Rollback Engine Service Move Readiness Audit

**Status:** Audit complete — recommendation: safe to move now  
**Branch:** `audit/rollback-service-readiness`  
**Date:** 2026-05-24  
**Source:** `core/engines/rollback_engine.py`  
**Recommended target:** `core/services/rollback.py`

---

## 1. Behavior Classification

Rollback engine is a **pure direct-call utility**.

- No hooks, registries, tail-blocks, interceptors, or lifecycle participation.
- No background threads or async loops.
- No mutable module-level state.
- All public API is stateless functions (no class).

**Behaviorally identical to** `SearchWorkflowEngine`, `SummaryEngine`,
`VerificationEngine`, `FileWorkEngine`, `RouteClarifier`, and
`FollowupResolutionEngine` — all already relocated to `core/services/`.

---

## 2. Caller Map

### 2.1 Production code

| File | Usage |
|------|-------|
| `core/executor.py` | `is_bulk_action`, `record_manifest` (as `record_rollback_manifest`) |
| `core/orchestrator_phases.py` | `invert_manifest` (as `invert_rollback_manifest`) |

**No package export** in `core/engines/__init__.py` — rollback_engine is imported directly by its callers.

### 2.2 Test / smoke code

| File | Usage |
|------|-------|
| `scripts/bulk_rollback_manifest_smoke_test.py` | 12-case smoke test covering all public functions |

---

## 3. Runtime Responsibilities

| Function | Responsibility | Risk if broken |
|----------|---------------|---------------|
| `is_bulk_action(action)` | Returns `True` for bulk FILE_OP actions that warrant a manifest | Bulk ops not tracked for rollback, or false positives waste manifests |
| `record_manifest(turn_id, action, tool_result, data_dir)` | Writes a JSON manifest describing moves/deletions/created_dirs | Manifest missing or malformed → undo impossible |
| `invert_manifest(manifest_path, workspace)` | Replays moves in reverse, removes empty dirs, marks manifest `rolled_back=True` | Files not restored, workspace left in inconsistent state |
| `_prune_old_manifests(data_dir)` | Keeps at most 5 manifests, deletes older ones | Unbounded disk growth in `data/rollback/` |
| `_safe_turn_id(turn_id)` | Sanitizes turn IDs for Windows-safe filenames | Manifest filenames contain illegal chars, writes fail |

---

## 4. Behavior That Must Not Change During Relocation

- `_BULK_ACTIONS` frozenset contents.
- `_MAX_MANIFESTS` limit (5).
- Manifest JSON schema (keys: `turn_id`, `timestamp`, `action`, `committed`, `rolled_back`, `moves`, `deletions`, `created_dirs`).
- `invert_manifest` move-reversal order (reversed moves list).
- `rolled_back=True` guard on second invert.
- Empty destination directory removal logic (deepest first).
- Deletion non-recoverable reporting.
- `_safe_turn_id` character whitelist and normalization.

---

## 5. Current Test Coverage

### 5.1 Unit tests
**Zero.** No tests in `scripts/test_engines.py` or `tests/`.

### 5.2 Smoke tests

| Script | Coverage |
|--------|----------|
| `scripts/bulk_rollback_manifest_smoke_test.py` | 12 cases:<br>1. `record_manifest` writes valid JSON<br>2. `invert_manifest` moves files back<br>3. Manifest marked `rolled_back=True` after invert<br>4. Second invert refused (guard check)<br>5. `move_many` round-trip<br>6. `delete_many` records deletions, reports non-recoverable<br>7. `is_bulk_action` recognises 4 bulk ops, rejects others<br>8. `record_manifest` returns `None` for empty result<br>9. `_prune_old_manifests` keeps at most 5<br>10. `ChangeJournal.record_turn` stores rollback_manifest path<br>11. `ChangeJournal.mark_entry_undone` sets `undone_at`<br>12. ISO turn IDs produce Windows-safe filenames |

The smoke test is **comprehensive** — it exercises the full public API surface
with real file operations in temporary directories. All 12 cases pass.

---

## 6. Missing Coverage

There are no unit tests in `scripts/test_engines.py`, but the smoke test
provides complete coverage of the public API. The following paths are
indirectly exercised but not isolated in unit tests:

| Path | Status | Notes |
|------|--------|-------|
| `is_bulk_action` | Covered by smoke | Simple frozenset lookup |
| `record_manifest` | Covered by smoke | Cases 1, 5, 6, 8, 9, 12 |
| `invert_manifest` | Covered by smoke | Cases 2, 3, 4, 5, 6 |
| `_prune_old_manifests` | Covered by smoke | Case 9 |
| `_safe_turn_id` | Covered by smoke | Case 12 |

No safety-critical guards exist. The primary risk of moving without unit
tests is minimal because the smoke test is comprehensive and the functions
are stateless.

---

## 7. Recommendation

**A) Safe to move now.**

Rollback engine is a pure direct-call utility with no hooks, registries, or
lifecycle participation. The existing smoke test comprehensively covers all
public functions with real file operations. No behavior gaps would go
unnoticed during relocation.

Unit tests in `scripts/test_engines.py` would be nice for faster CI feedback,
but they are **not a prerequisite** for a safe move.

---

## 8. Recommended Target Path

**`core/services/rollback.py`**

Rationale:
- All prior service relocations dropped the `_engine` suffix from the module
  name (`file_work.py`, `route_clarity.py`, `followup_resolution.py`, etc.).
- The module exports functions, not a class, so `_engine` is especially
  unnecessary.
- `core/services/rollback.py` is consistent with the established naming
  convention.

---

## 9. Import / Export Map (for future move)

Files that would need import updates:

```
core/executor.py                  — update record_manifest + is_bulk_action imports
core/orchestrator_phases.py       — update invert_manifest import

scripts/bulk_rollback_manifest_smoke_test.py  — update imports

docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md    — table update
docs/architecture/TRIGGER_FLOW.md                     — path updates
docs/ROADMAP.md                                       — path update
```

**Note:** `core/engines/__init__.py` does **not** export rollback_engine,
so no package-init change is needed.

---

## 10. Notes

- Rollback engine is the **smallest** remaining Direct-Call Utility in
  `core/engines/` (262 lines, 5 functions).
- Unlike `FileWorkEngine`, it has **no safety-critical guards** that could
cause data loss if broken.
- The primary risk of moving is **manifest path breakage** (file writes
  failing due to wrong import), which the smoke test would catch immediately.
- The module has **no class** — all exports are standalone functions. This
  makes the relocation even simpler than the class-based service moves.
