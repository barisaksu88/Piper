# ChangeJournal Split Readiness Audit

**Status:** Split complete  
**Scope:** `core/engines/change_journal.py` + `core/services/change_journal.py`  
**Branch:** `split/change-journal-service`  
**Date:** 2026-05-24

---

## Recommended Decision

**B** — Split service class to `core/services/`, keep hook in `core/engines/`.

Rationale:
- `ChangeJournal` is ~420 lines of direct-call service behavior and ~20 lines of lifecycle hook.
- The service has no dependency on the hook system (it does not import `register_hook`).
- The hook (`_hook_record_change_journal`) is thin: it delegates to `ChangeJournal.record_turn()` and sets two `orc` attributes.
- This is the same split pattern already proven for `ConversationCompressor` → `core/services/conversation_compressor.py` + `core/engines/conversation_compressor.py` and `ContextPackEngine` → `core/services/context_pack_service.py` + `core/engines/context_pack.py`.

---

## Current Runtime Shape

`core/engines/change_journal.py` (531 lines) contains:

1. **Module constants** — `MAX_SNAPSHOT_CONTENT_CHARS`, `BINARY_EXTENSIONS`
2. **Module helpers** — `_utc_now_iso()`, `_remove_path()`, `_path_depth()`
3. **`ChangeJournal` class** (lines 82–508) — direct-call service with file I/O
4. **`_hook_record_change_journal`** (lines 510–531) — `@register_hook("on_task_verified")`

The class owns a single JSON file (`self.path`, default `data/change_journal.json`) with a hard `max_entries` cap (default 10, minimum 5). All file writes go through `save_entries()`, which prunes on write.

---

## Caller Map

### Production callers / imports

| Caller | Import / Usage | Direct or Hook | Risk |
|---|---|---|---|
| `core/executor.py` | `from core.engines.change_journal import ChangeJournal`; `self.change_journal = ChangeJournal(CFG.CHANGE_JOURNAL_PATH)` | Direct (instantiation) | Medium — executor holds the journal reference |
| `core/executor.py` | `self.change_journal.prepare_file_op_capture_from_tool_tag(...)` | Direct | Low — read-only capture |
| `core/executor.py` | `self.change_journal.finalize_file_op_capture(...)` | Direct | Low — post-mutation finalize |
| `core/orchestrator.py` | `from core.engines.change_journal import ChangeJournal`; `self.change_journal = ChangeJournal(CFG.CHANGE_JOURNAL_PATH)` | Direct (instantiation) | Medium — orchestrator holds the journal reference |
| `core/orchestrator_phases.py` | `fire_hooks("on_task_verified", ...)` (line ~1841) | Hook-fired | Low — hook self-registers |
| `core/orchestrator_phases.py` | `orc.change_journal.peek_latest_entry()` (line ~1887) | Direct | Low — read-only |
| `core/orchestrator_phases.py` | `orc.change_journal.mark_entry_undone(...)` (line ~1898) | Direct | Low — mutation after undo |
| `core/orchestrator_phases.py` | `orc.change_journal.undo_latest(workspace)` (line ~1904) | Direct | Medium — file restore |
| `core/services/rollback_engine.py` | Comments only — documents `ChangeJournal.undo_latest` relationship | None | None |

### Test / script callers / imports

| Caller | Import / Usage |
|---|---|
| `scripts/change_journal_smoke_test.py` | `from core.services.change_journal import ChangeJournal`; full smoke test |
| `scripts/bulk_rollback_manifest_smoke_test.py` | `from core.services.change_journal import ChangeJournal`; tests `record_turn` + `mark_entry_undone` |
| `scripts/undo_flow_smoke_test.py` | Clears `change_journal.json` before harness run; integration-level undo flow |
| `scripts/file_target_correction_undo_smoke_test.py` | Clears `change_journal.json` before harness run |
| `scripts/run_smoke_tests.py` | Filters `stem.startswith("change_journal_")` |

### No direct `_hook_record_change_journal` callers

Grep confirms `_hook_record_change_journal` is **only** referenced inside `core/engines/change_journal.py` (definition). It is fired indirectly via `fire_hooks("on_task_verified", ...)` in `core/orchestrator_phases.py`. `ChangeJournal` itself is no longer defined in `core/engines/change_journal.py`.

---

## Direct-Call Service Behavior

| Method / Helper | Side effects | Dependencies | Move candidate? | Notes |
|---|---|---|---|---|
| `ChangeJournal.__init__` | None | `Path` | **Yes** | Pure init |
| `load_entries()` | File read | `json`, `self.path` | **Yes** | Returns `[]` on missing/corrupt file |
| `save_entries()` | File write | `json`, `memory.storage.ensure_parent` | **Yes** | Prunes to `max_entries` on write |
| `prepare_file_op_capture_from_tool_tag()` | None (read-only) | `tools.file_ops.extract_tag_payload_text` | **Yes** | Extracts payload, delegates to `prepare_file_op_capture` |
| `prepare_file_op_capture()` | None (read-only) | `tools.file_ops.parse_file_op_payload`, `resolve_workspace_path` | **Yes** | Snapshots pre-mutation state |
| `finalize_file_op_capture()` | None (read-only) | `normalize_file_op_action` | **Yes** | Builds operation dict from prepared + tool_result |
| `record_turn()` | File write | `load_entries` / `save_entries` | **Yes** | Appends entry, prunes, returns entry dict |
| `mark_entry_undone()` | File write | `load_entries` / `save_entries` | **Yes** | Sets `undone_at` by `turn_id` |
| `has_pending_undo()` | File read | `peek_latest_entry` | **Yes** | Checks `undone_at` on latest |
| `peek_latest_entry()` | File read | `load_entries` | **Yes** | Returns copy of latest entry or `None` |
| `undo_latest()` | File write + workspace mutation | `load_entries` / `save_entries`, `_restore_snapshot`, `_remove_path`, `shutil.rmtree` | **Yes** | Restores snapshots; returns tool-result-shaped dict |
| `_extract_file_op_payload_text()` | None | `tools.file_ops.extract_tag_payload_text` | **Yes** | Staticmethod |
| `_capture_snapshot_paths()` | None (read-only) | `resolve_workspace_path` | **Yes** | Classmethod; workspace path resolution |
| `_missing_parent_dirs()` | None (read-only) | `resolve_workspace_path` | **Yes** | Staticmethod |
| `_snapshot_path()` | None (read-only) | `resolve_workspace_path`, `Path.stat`, `read_text` | **Yes** | Staticmethod; decides `metadata_only` vs content |
| `_restore_snapshot()` | File write + workspace mutation | `resolve_workspace_path`, `_remove_path`, `shutil.rmtree`, `Path.mkdir`, `Path.write_text` | **Yes** | Staticmethod; restores file/dir/absent state |
| `_candidate_paths()` | None | Pure dict traversal | **Yes** | Staticmethod |
| `_primary_paths_from_operations()` | None | Pure dict traversal | **Yes** | Staticmethod |
| `_utc_now_iso()` | None | `datetime.now(timezone.utc)` | **Yes** | Free function |
| `_remove_path()` | File deletion | `shutil.rmtree`, `Path.unlink` | **Yes** | Free function |
| `_path_depth()` | None | Pure string math | **Yes** | Free function |
| `MAX_SNAPSHOT_CONTENT_CHARS` | None | Constant | **Yes** | Module constant |
| `BINARY_EXTENSIONS` | None | Constant | **Yes** | Module constant |

**All direct-call behavior is safe to move.** `ChangeJournal` has no dependency on `register_hook`, `_TAIL_BLOCK_REGISTRY`, or any other engine lifecycle system.

---

## Lifecycle / Hook Behavior

| Hook | Trigger | What it mutates | Must stay in engines? | Notes |
|---|---|---|---|---|
| `_hook_record_change_journal` | `fire_hooks("on_task_verified", orc, ...)` | `orc.last_change_journal_entry`, `orc.undo_notice_pending` | **Yes** | Self-registers via `@register_hook`. Thin wrapper around `ChangeJournal.record_turn()`. |

The hook remains in `core/engines/change_journal.py` because it is lifecycle behavior. The hook does not import `ChangeJournal`; it delegates to `orc.change_journal.record_turn(...)`.

---

## File I/O and Safety Behavior

### Journal JSON load/save
- `load_entries()` returns `[]` if file missing, unreadable, or not a list. Defensive.
- `save_entries()` calls `ensure_parent(self.path)` before write and truncates to `max_entries`.
- **No unbounded growth** — pruned on every write.

### Snapshot capture (`prepare_file_op_capture`)
- Reads workspace paths via `resolve_workspace_path`.
- Returns `None` for non-mutating actions (only `_SUPPORTED_MUTATING_ACTIONS` are captured).
- Snapshots include absent files/dirs, directory markers, and file content or metadata.

### Snapshot policy (`_snapshot_path`)
- **Binary files** → `metadata_only` (no content, no `bytes_b64`).
- **Large text files** (≥ `MAX_SNAPSHOT_CONTENT_CHARS`) → `metadata_only` + `truncated=True`.
- **Unicode decode failures** → `metadata_only`.
- **Normal text files** → `kind: file` + `content`.
- **Directories** → `kind: directory`.
- **Absent paths** → `kind: absent`.

### Restore behavior (`_restore_snapshot`)
- `absent` → removes file/dir.
- `file` with `bytes_b64` → **fails gracefully** with warning (legacy data).
- `file` with `metadata_only` → **fails gracefully** with descriptive error.
- `file` with `content` → writes text back.
- `directory` → removes then recreates empty dir (legacy `entries` payload skipped with warning).

### Rollback manifest relationship
- `record_turn` accepts `rollback_manifests` list and stores it on the entry.
- `undo_latest` does **not** read rollback manifests; `core/orchestrator_phases.py` `phase_undo` handles manifest-first rollback, then calls `mark_entry_undone`.
- `core/services/rollback_engine.py` owns `record_manifest`, `invert_manifest`, `is_bulk_action`.

### Workspace path resolution
- All paths are resolved through `tools.file_ops.resolve_workspace_path`, which enforces workspace boundaries.

---

## Existing Test Coverage

### Smoke tests (scripts/)

| File | Coverage |
|---|---|
| `scripts/change_journal_smoke_test.py` | Overwrite restore, create-remove, binary metadata-only, large-text metadata-only, no `bytes_b64` written, legacy `bytes_b64` graceful fail, entry count, latest undone, interceptor detection |
| `scripts/bulk_rollback_manifest_smoke_test.py` | `record_turn` stores rollback_manifest path; `mark_entry_undone` sets `undone_at` and `undo_last_status` |
| `scripts/undo_flow_smoke_test.py` | Integration-level undo flow via `PiperHarness` (creates file, asks "undo that", verifies reversion) |
| `scripts/file_target_correction_undo_smoke_test.py` | Integration-level undo after file-target correction mistake |

### Unit tests (tests/)

**None found.** No `tests/test_*.py` file imports `ChangeJournal` or exercises journal logic in isolation.

---

## Missing Guard Tests Before Split

Before moving `ChangeJournal` to `core/services/change_journal.py`, the following guard tests should exist in `tests/`:

1. **`test_load_entries_returns_empty_on_missing_file`** — verify `load_entries()` returns `[]` when path does not exist.
2. **`test_load_entries_returns_empty_on_corrupt_json`** — verify graceful handling of malformed JSON.
3. **`test_save_entries_prunes_to_max_entries`** — verify write-path pruning (e.g., 12 entries → 10).
4. **`test_prepare_file_op_capture_skips_non_mutating_action`** — `RUN_CODE`, `read_text`, etc. return `None`.
5. **`test_prepare_file_op_capture_snapshots_missing_parents`** — `write_text` / `ensure_dir` captures parent dirs.
6. **`test_finalize_file_op_capture_requires_executed_status`** — non-EXECUTED returns `None`.
7. **`test_finalize_file_op_capture_requires_workspace_changed`** — `workspace_changed=False` returns `None`.
8. **`test_record_turn_returns_none_when_no_ops_and_no_manifests`** — empty turn produces no entry.
9. **`test_record_turn_includes_rollback_manifests`** — already covered in smoke; should have unit test too.
10. **`test_undo_latest_fails_when_no_entries`** — returns FAILED with correct message.
11. **`test_undo_latest_fails_when_already_undone`** — returns FAILED with correct message.
12. **`test_undo_latest_restores_file_content`** — content snapshot round-trip.
13. **`test_undo_latest_removes_created_file`** — absent snapshot round-trip.
14. **`test_undo_latest_restores_directory`** — directory snapshot round-trip.
15. **`test_undo_latest_fails_gracefully_on_metadata_only`** — metadata-only snapshot produces FAILED.
16. **`test_mark_entry_undone_finds_by_turn_id`** — sets fields on correct entry.
17. **`test_has_pending_undo_true_and_false`** — respects `undone_at` field.
18. **`test_snapshot_path_metadata_only_for_binary`** — e.g., `.png` → `metadata_only`.
19. **`test_snapshot_path_metadata_only_for_large_text`** — size ≥ 1M chars → `metadata_only` + `truncated`.
20. **`test_snapshot_path_content_for_normal_text`** — reads and stores content.

**Minimum viable guard set (if pruning):**
- Load/save round-trip (1, 3)
- Capture finalize round-trip for `write_text` (4, 5, 6, 7)
- `undo_latest` success + failure paths (10, 12, 13, 15)
- `mark_entry_undone` + `has_pending_undo` (16, 17)
- Binary + large file metadata-only (18, 19)

---

## Recommended Staging

### Stage 1 — Add guard tests
- Create `tests/test_change_journal.py` with the minimum viable guard set above.
- Keep tests green against `core/engines/change_journal.py` (now `core/services/change_journal.py` for `ChangeJournal`).
- **Do not move code yet.**

### Stage 2 — Move `ChangeJournal` class and helpers to `core/services/change_journal.py`
- Move `ChangeJournal` class, module constants, and all free functions.
- Update imports inside `ChangeJournal` if any relative references change.
- `core/engines/change_journal.py` becomes a thin module containing only `_hook_record_change_journal` and its imports.
- Update `core/executor.py`, `core/orchestrator.py`, and smoke scripts to import `ChangeJournal` from `core.services.change_journal`.

### Stage 3 — Update `core/engines/change_journal.py` to re-export or thin-wrap
- Option A: Keep `_hook_record_change_journal` in `core/engines/change_journal.py` with `from core.services.change_journal import ChangeJournal`.
- Option B: Rename `core/engines/change_journal.py` → `core/engines/change_journal_hooks.py` and update all hook importers (not needed if the file stays thin).

### Stage 4 — Update docs and exports
- Update `core/engines/__init__.py` if it re-exports symbols.
- Update `core/services/__init__.py` to export `ChangeJournal`.
- Update `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` and `docs/specs/engine-directory-audit.md`.

### Stage 5 — Run full validation
```
python -m compileall app.py config.py core ui memory tools llm web_ui
python -m pytest tests/ -q
python -m pytest web_ui/bridge/ -q
python scripts/change_journal_smoke_test.py --json
python scripts/bulk_rollback_manifest_smoke_test.py
python scripts/undo_flow_smoke_test.py --json
python scripts/file_target_correction_undo_smoke_test.py --json
```

---

## Collision Notes With Frontend Branch

- This backend path **must not** touch `web_ui/`, `ui/`, `app.py`, `config.py`, or any frontend bridge/startup files.
- The frontend branch **must not** touch `core/engines/change_journal.py`, `core/services/change_journal.py` (future), or architecture docs for change-journal split readiness.
- `core/executor.py` and `core/orchestrator.py` are shared backend wiring; coordinate if the frontend branch also touches them.
