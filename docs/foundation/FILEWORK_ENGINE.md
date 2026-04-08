# FileWorkEngine Contract

Status: Complete — extracted and frozen 2026-03-15
Date: 2026-03-15

This file defines the contract for `FileWorkEngine` before any code is moved.
No existing logic is changed until this contract is proven stable.

Companion docs:
- [BLUEPRINT.md](BLUEPRINT.md)
- [EXECUTION_ROADMAP.md](EXECUTION_ROADMAP.md)
- [VERIFICATION_ENGINE.md](VERIFICATION_ENGINE.md)

---

## 1. Purpose

`FileWorkEngine` is the single owner of the question:

> "What evidence does this file/code work stage have, and how should the executor handle it?"

Right now that question is answered in pieces scattered across:

- `core/executor.py` — holds evidence extraction, code view rendering, exact read capture,
  blocked-write guards, and artifact annotation in a 1456-line executor loop
- `core/file_checker.py` — workspace state re-reads for verification fallback
- `core/file_checker_rules.py` — ~10 `_stage_*` methods that duplicate stage classification
  already owned by `FileStagePolicy`
- `core/file_stage_policy.py` — stage classification and recovery hints, but these are called
  inconsistently from both executor and file_checker_rules

The engine does not replace `LocalFileOpRuleChecker`, `FileWorkChecker`, or `VerificationEngine`.
It absorbs the evidence-handling and stage-interpretation mechanics that currently live in the
executor loop, and gives them a stable API.

---

## 2. What FileWorkEngine Owns

### Evidence Collection
- extracting candidate file paths from any tool result (currently three divergent implementations)
- de-duplicating and ordering paths for verification and display
- unified `candidate_paths(tool_result) -> list[str]` entry point

### Artifact Rendering
- formatting code file previews for the UI queue (currently `executor._render_code_view`)
- deciding whether a tool result warrants a code view emission
- `render_artifact_view(tool_result, workspace) -> str | None`

### Exact Read Capture
- deciding whether a file read result should be written to the planner scratchpad
  (currently `executor._should_capture_exact_file_read_for_planner`)
- formatting the exact-read scratchpad note (currently `executor._append_exact_file_read_note_from_result`)
- enforcing the per-stage 2-file / 14KB budget
- `capture_exact_read(stage, tool_result, existing_reads) -> str | None`

### Blocked-Write Guards
- detecting when the planner is trying to embed full source in a write_text payload
  (currently `executor._should_block_code_file_write_text`)
- detecting redundant exact reads (currently `executor._should_block_redundant_exact_read`)
- `should_block(stage, tool_call) -> tuple[bool, str]`

### Recovery Hint Generation
- consolidating `FileStagePolicy.file_checker_recovery_hint()` into the engine
- mapping (verdict, stage_type, checker_path) → recovery strategy string
- `recovery_hint(verdict, stage, checker_path) -> str`

### Stage Classification Delegation
- the ~10 `_stage_*` methods in `file_checker_rules.py` duplicate patterns already in
  `FileStagePolicy`; they should call `FileStagePolicy` instead of re-parsing stage text
- the engine's `classify(stage) -> FileStageKind` becomes the single dispatch point
  for anything that needs to know what a file/code stage is doing

### Code Extension Registry
- `CODE_FILE_EXTENSIONS` and related extension sets are currently defined in both
  `executor.py` (lines 37-65) and `file_stage_policy.py` (lines 33-61)
- a single canonical set lives in this engine and is imported by both callers

### What FileWorkEngine Does NOT Own

- the step loop itself (executor still drives step iteration)
- VERIFIED / PARTIAL / FAILED decisions (VerificationEngine owns these)
- deterministic rule checking (LocalFileOpRuleChecker owns this)
- LLM-based file checking (FileWorkChecker owns this)
- workspace mutation (workspace_mutation_actions.py owns this)
- planner calls and scratchpad turn management (executor owns this)

---

## 3. Input Contract

```
stage           StageCard           what the stage wants to achieve
tool_result     dict                what the tool actually returned
workspace       Path                filesystem root
step            int                 current step inside the stage
exact_reads     list[str]           file paths already captured this stage
```

---

## 4. Output Contracts

### FileWorkEvidence

Returned by `collect_evidence(stage, tool_result, workspace)`:

```
candidate_paths     list[str]   de-duplicated ordered file paths from the result
artifact_view       str | None  formatted code/content preview, or None
exact_read_note     str | None  scratchpad note for planner, or None (budget enforced)
```

### FileWorkBlock

Returned by `should_block(stage, tool_call)`:

```
blocked     bool
reason      str     empty if not blocked
```

### FileStageKind

Returned by `classify(stage)`:

```
INSPECTION          read-only, no mutation expected
CONTENT_EDIT        file text or code is being written/patched
STRUCTURE_PREP      directory creation, extension reorg
BROAD_REORG         multi-file move/copy across workspace
SCRIPT_LAUNCH       interactive runtime execution
DEPENDENCY_RECOVERY install/repair step
UNKNOWN             cannot determine; treat as requiring verification
```

---

## 5. Code Extension Registry

The canonical extension sets currently duplicated across executor and file_stage_policy:

```python
CODE_FILE_EXTENSIONS: frozenset[str]       # .py .js .ts .cpp .c .h .cs .java .rs .go ...
CODE_VIEW_EXTENSIONS: frozenset[str]       # subset shown in code preview
DOCUMENT_EXTENSIONS: frozenset[str]        # .pdf .docx .xlsx .pptx .txt .md ...
```

Both `executor.py` and `file_stage_policy.py` import from the engine after migration.
No logic changes — just a single definition site.

---

## 6. Duplication Eliminated

| Current duplication                              | After migration                        |
|--------------------------------------------------|----------------------------------------|
| `executor._file_result_candidate_paths()`        | `FileWorkEngine.collect_evidence()`    |
| `file_checker._candidate_paths_from_evidence()`  | `FileWorkEngine.collect_evidence()`    |
| `file_checker_rules._ordered_file_targets()`     | `FileWorkEngine.collect_evidence()`    |
| `executor` CODE_VIEW_EXTENSIONS (lines 37-65)   | `FileWorkEngine.CODE_FILE_EXTENSIONS`  |
| `file_stage_policy` _CODE_FILE_EXTENSIONS        | `FileWorkEngine.CODE_FILE_EXTENSIONS`  |
| `executor._render_code_view()`                   | `FileWorkEngine.render_artifact_view()`|
| `executor._should_capture_exact_file_read_*`     | `FileWorkEngine.capture_exact_read()`  |
| `executor._should_block_code_file_write_text()`  | `FileWorkEngine.should_block()`        |
| `executor._should_block_redundant_exact_read()`  | `FileWorkEngine.should_block()`        |
| `file_stage_policy.file_checker_recovery_hint()` | `FileWorkEngine.recovery_hint()`       |
| `file_checker_rules` ~10 `_stage_*` methods      | delegate to `FileStagePolicy`          |

---

## 7. What Stays Put (Do Not Move)

- `core/file_checker_rules.py` — all deterministic rule logic stays here; `_stage_*` methods
  are refactored to call `FileStagePolicy`, not replaced
- `core/file_checker.py` — LLM checker coordination stays here
- `core/file_stage_policy.py` — stage classification methods stay here; become the sole
  classification authority once `_stage_*` duplication is removed from file_checker_rules
- `core/engines/verification.py` — already extracted; no changes
- `core/executor.py` — step loop, planner calls, scratchpad management
- `tools/workspace_*` — runtime mutation/query handlers

---

## 8. What Moves In (Migration Map)

| Current location                                              | Moves to                                     |
|---------------------------------------------------------------|----------------------------------------------|
| `executor._file_result_candidate_paths()` (203-240)          | `FileWorkEngine.collect_evidence()`          |
| `executor._render_code_view()` (246-300)                     | `FileWorkEngine.render_artifact_view()`      |
| `executor._maybe_emit_code_view()` (302-305)                 | caller uses `render_artifact_view()` result  |
| `executor._should_capture_exact_file_read_for_planner()` (1142-1162) | `FileWorkEngine.capture_exact_read()` |
| `executor._append_exact_file_read_note_from_result()` (1163-1188)    | `FileWorkEngine.capture_exact_read()` |
| `executor._should_block_code_file_write_text()` (343-374)   | `FileWorkEngine.should_block()`              |
| `executor._should_block_redundant_exact_read()` (376-411)   | `FileWorkEngine.should_block()`              |
| `executor` CODE_VIEW_EXTENSIONS constant (37-65)            | `FileWorkEngine.CODE_FILE_EXTENSIONS`        |
| `file_stage_policy._CODE_FILE_EXTENSIONS` (33-61)           | `FileWorkEngine.CODE_FILE_EXTENSIONS`        |
| `file_stage_policy.file_checker_recovery_hint()`            | `FileWorkEngine.recovery_hint()`             |
| `file_checker._candidate_paths_from_evidence()` (24-50)     | `FileWorkEngine.collect_evidence()`          |
| `file_checker._build_current_state_read_result()` (116-168) | stays in `file_checker.py`; calls engine for paths |

---

## 9. Migration Steps (when ready)

Follow the standard v1 phase workflow:

1. Define `FileWorkEvidence`, `FileWorkBlock`, `FileStageKind` in `contracts.py`
2. Implement `FileWorkEngine` shell in `core/engines/file_work.py`
   - start with `CODE_FILE_EXTENSIONS` constant migration (zero-risk, easy to verify)
   - then `collect_evidence()` — consolidate the three path extractors
   - then `render_artifact_view()` + `capture_exact_read()`
   - then `should_block()`
   - then `recovery_hint()` + stage classification delegation
3. Write smoke test: `scripts/file_work_engine_smoke_test.py`
   - evidence collection from known tool result shapes (FILE_OP, FILE_READ, etc.)
   - code extension classification
   - should_block cases
   - recovery hint cases
4. Route executor calls through the engine one method at a time
5. Prove parity: all existing file/code smoke tests still pass
6. Remove the now-redundant methods from executor, file_checker, file_checker_rules
7. Update `EXECUTION_ROADMAP.md` phase 5 status

Do not begin step 4 until step 3 smoke tests are passing.

---

## 10. Definition of Done for This Engine

- `executor.py` no longer holds any evidence-extraction, path-extraction, or code-view methods
- `file_checker.py` imports `candidate_paths` from the engine instead of implementing its own
- `file_checker_rules.py` `_stage_*` methods are gone — they call `FileStagePolicy` directly
- code extension sets have one definition site
- recovery hints come from one call site
- no regression on the full smoke test pack
