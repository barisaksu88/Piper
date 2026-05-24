# Context Pack Split Readiness Audit

**Status:** ContextPackService extracted — split complete  
**Scope:** `core/engines/context_pack.py` + `core/services/context_pack_service.py`  
**Branch:** `split/context-pack-service-core`  
**Date:** 2026-05-24  

---

## A. Behavior Classification

`core/engines/context_pack.py` is a **Hybrid** module that is **too coupled to move whole** safely without a staged split first.

| Behavior category | Owner in this file | Verdict |
|---|---|---|
| Registry / tail-block behavior | `_TAIL_BLOCK_REGISTRY`, `register_tail_block()`, 11 `@register_tail_block` builders (now in `core/engines/tail_block_registry.py`), `@register_hook("on_turn_end")` | Must stay in `core/engines/` |
| Direct-call service behavior | `ContextPackService.build_persona_pack()`, `.build_runtime_context_pack()`, `.build_persona_runtime_pack()`, etc. (now in `core/services/context_pack_service.py`) | **Moved** to `core/services/` |
| Pure helper/value behavior | `resolve_persona_turn_type()`, `render_context_arbitration_block()`, `_clear_pack_field_value()` | **Extracted** to `core/services/context_pack_renderer.py` |
| Registry / tail-block surface | `TailBlockContext`, `TailBlockBuilder`, `_TAIL_BLOCK_REGISTRY`, `register_tail_block`, all `@register_tail_block` builders | **Extracted** to `core/engines/tail_block_registry.py` |
| Runtime path helpers | `_collect_runtime_context_paths`, `_normalize_runtime_context_path` | **Extracted** to `core/services/context_pack_paths.py` |

Historically, `core/engines/context_pack.py` was a **hybrid** module: it exposed a wide direct-call API *and* owned global mutable registry state plus a lifecycle hook. After the staged split, all direct-call service behavior lives in `core/services/context_pack_service.py` (`ContextPackService`). The remaining `core/engines/context_pack.py` is a thin engine module containing only `ContextPackDirectiveEngine` and `_hook_upsert_runtime_context`. It remains under `core/engines/` because directive generation reads the tail-block registry (which lives in `core/engines/tail_block_registry.py`) and the hook is lifecycle behavior.

---

## B. Caller Map

### Production callers / imports

| File | Import / Usage |
|---|---|
| `core/engines/__init__.py` | `from core.engines.context_pack import ContextPackDirectiveEngine` (re-export) |
| `core/prompt_context.py` | Composes `ContextPackService` (from `core.services.context_pack_service`) and `ContextPackDirectiveEngine` (from `core.engines.context_pack`); wraps every public method in `PromptContextService` |
| `core/orchestrator_phases.py` | `from core.engines.context_pack import _hook_upsert_runtime_context`; direct call at line 1824; also calls `orc.prompt_context.*` |
| `core/engines/proactive_monitor.py` | `from core.engines.tail_block_registry import TailBlockContext, register_tail_block`; registers 2 proactive tail blocks |
| `core/services/summary.py` | Comments only (method origin documentation); no runtime import |

### Test / script callers / imports

| File | Import / Usage |
|---|---|
| `tests/test_context_pack_snapshots.py` | `ContextPackService`, `ContextPackDirectiveEngine`, `ContextPackRenderer`, `resolve_persona_turn_type`, `render_context_arbitration_block` |
| `scripts/test_engines.py` | `ContextPackService` / `ContextPackDirectiveEngine` (class `TestContextPackEngine`) |
| `scripts/context_pack_engine_smoke_test.py` | Uses `PromptContextService` (integration smoke) |
| `scripts/search_error_contract_smoke_test.py` | `ContextPackRenderer` |
| `scripts/run_smoke_tests.py` | Filters `stem.startswith("context_pack_")` |

### Docs references

- `docs/architecture/CONTEXT_PACK_HYBRID_MAP.md` — prior hybrid map
- `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` — hybrid table entry
- `docs/architecture/TRIGGER_FLOW.md` — multiple references to tail blocks, arbitration, runtime context, hook inventory
- `docs/foundation/BLUEPRINT.md` — `ContextPackEngine` ownership
- `docs/foundation/SUMMARY_ENGINE.md` — method migration history (12 methods already moved to `SummaryEngine`)
- `docs/specs/engine-directory-audit.md` — lists `context_pack.py` for review
- `docs/specs/full-text-search.md` — single reference
- `notes/known-good.md` — compile/smoke commands referencing `context_pack.py`
- `notes/debug-protocol.md` — smoke test command reference

---

## C. Registry / Hook / Lifecycle Map

### Tail-block registry ownership

```python
_TAIL_BLOCK_REGISTRY: list[TailBlockBuilder] = []

def register_tail_block(fn: TailBlockBuilder) -> TailBlockBuilder:
    _TAIL_BLOCK_REGISTRY.append(fn)
    return fn
```

- **Global mutable list** mutated at import time by decorators.
- **Order matters:** blocks are appended in decorator-evaluation order; `build_persona_directive_pack()` iterates the list sequentially.

### `register_tail_block` usage

| File | Builder | Condition |
|---|---|---|
| `core/engines/tail_block_registry.py` | `_tail_block_no_mutation_rule` | CHAT + no outcome |
| `core/engines/tail_block_registry.py` | `_tail_block_context_arbitration` | Always |
| `core/engines/tail_block_registry.py` | `_tail_block_document_qa_rule` | `ingested_document_chat` |
| `core/engines/tail_block_registry.py` | `_tail_block_search_report_rule` | `reporter_just_ran` |
| `core/engines/tail_block_registry.py` | `_tail_block_explain_last_turn` | `system_notice.kind == "explain_last_turn"` |
| `core/engines/tail_block_registry.py` | `_tail_block_active_skill` | Always (empty if no skill) |
| `core/engines/tail_block_registry.py` | `_tail_block_verification_result` | Always (empty if no verdict) |
| `core/engines/tail_block_registry.py` | `_tail_block_file_work_report` | `needs_file_work_report_rule` |
| `core/engines/tail_block_registry.py` | `_tail_block_failed_verification` | `verification_verdict == "FAILED"` |
| `core/engines/tail_block_registry.py` | `_tail_block_failed_outcome_no_verification` | `outcome_failed` + no typed verdict |
| `core/engines/tail_block_registry.py` | `_tail_block_workspace_boundary` | `needs_file_work_report_rule` |
| `core/engines/proactive_monitor.py` | `_tail_block_proactive_trigger` | `system_notice.kind == "proactive_trigger"` |
| `core/engines/proactive_monitor.py` | `_tail_block_reminder_set_result` | `system_notice.kind == "reminder_set_result"` |

### Hooks registered in this module

```python
@register_hook("on_turn_end")
def _hook_upsert_runtime_context(orc, *, reporter_just_ran: bool = False) -> None:
```

### Hook firing paths

1. **Generic fire:** `core/orchestrator_phases.py` line ~528 calls `fire_hooks("on_turn_end", orc, ...)`.
2. **Direct call:** `core/orchestrator_phases.py` line 1824 calls `_hook_upsert_runtime_context(orc, reporter_just_ran=False)` explicitly during auto-reroute after a failed stage.

### External modules registering into `context_pack`

- `core/engines/proactive_monitor.py` registers 2 tail blocks (see table above).
- Any future engine that needs a persona tail block must import `register_tail_block` from this module.

---

## D. Direct-Call Service Behavior Map

### `ContextPackService` methods (moved to `core/services/context_pack_service.py`)

| Method | Side effects | Dependencies | Safe to move later? |
|---|---|---|---|
| `build_persona_pack(...)` | None (read-only) | `instruction_loader`, `environment_service`, `operational_state_service`, `knowledge_mgr`, `brain`, `document_memory`, `vision_session_memory`, `transient_state_mgr`, `user_runtime` | **Moved** |
| `apply_document_focus(pack, ...)` | None | Pure dataclass replace | **Moved** |
| `clear_memory_for_file_work(pack)` | None | Pure dataclass replace | **Moved** |
| `apply_context_arbitration(pack, ...)` | None | `PERSONA_CONTEXT_ARBITRATION_TABLE` | **Moved** |
| `to_prompt_context(pack)` | None | Delegates to renderer | **Moved** |
| `build_runtime_context_pack(orc, ...)` | None (read-only) | `orc.route_decision`, `orc.scratchpad`, `orc.latest_search_query`, `SummaryEngine` | **Moved** |
| `render_runtime_context_message(pack)` | None | Delegates to renderer | **Moved** |
| `build_persona_runtime_pack(scratchpad, ...)` | None (read-only) | `SummaryEngine` (6×), `FileStagePolicy`, `VerificationResult` | **Moved** |

### `ContextPackDirectiveEngine` methods (remain in `core/engines/context_pack.py`)

| Method | Side effects | Dependencies | Safe to move later? |
|---|---|---|---|
| `build_persona_directive_pack(...)` | None (reads registry) | Iterates `_TAIL_BLOCK_REGISTRY` | **No** — bound to registry |
| `_build_dependency_failure_direct_answer(...)` | None | Pure regex / text | Stays with directive engine |
| `_render_persona_active_skill_block(...)` | None | Pure text | Moved to `core/engines/tail_block_registry.py` |
| `_render_verification_result_block(...)` | None | Pure text | Moved to `core/engines/tail_block_registry.py` |
| `collect_runtime_context_paths(orc)` | None | `orc.brain.workspace`, `orc.user_msg`, `orc.context_card`, `orc.scratchpad`, `CFG.DATA_DIR` | **Moved** to `core/services/context_pack_paths.py` |
| `normalize_runtime_context_path(...)` | None | `os.name`, `Path` | **Moved** to `core/services/context_pack_paths.py` |

### `ContextPackRenderer` methods

| Method | Side effects | Safe to move later? |
|---|---|---|
| `to_prompt_context(pack)` | None | **Yes** — pure mapping |
| `render_runtime_context_message(pack)` | None | **Yes** — pure mapping |

### Pure helper / value object behavior

| Symbol | Type | Safe to move later? |
|---|---|---|
| `resolve_persona_turn_type(...)` | Free function | Yes |
| `render_context_arbitration_block(...)` | Free function | Yes |
| `TailBlockContext` | `@dataclass` | Yes, with registry |
| `_clear_pack_field_value(...)` | Free function | Yes |
| `_PACK_BLOCK_FIELD_MAP` | `dict` constant | Yes |
| `_LATEST_RUNTIME_CONTEXT_PREFIX` | `str` constant | Yes |
| `_RUNTIME_CONTEXT_PATH_RE` | `re.Pattern` | Yes |

**Which pieces look safe to move to `core/services/` later?**

- ✅ `ContextPackRenderer` + `resolve_persona_turn_type` + `render_context_arbitration_block` + `_clear_pack_field_value` + `_PACK_BLOCK_FIELD_MAP` + `_LATEST_RUNTIME_CONTEXT_PREFIX` → extracted to `core/services/context_pack_renderer.py`
- `ContextPackEngine` methods **except** `build_persona_directive_pack` could move in a future stage, but because they live on the same class as the registry-bound method, splitting the class is a larger refactor than a simple file move.

---

## E. State and Side-Effect Analysis

### Mutable state

- `_TAIL_BLOCK_REGISTRY: list[TailBlockBuilder]` — global list mutated at import time by decorators.
- No instance-level mutable state on `ContextPackService`, `ContextPackDirectiveEngine`, or `ContextPackRenderer`.

### File I/O

- No direct file writes.
- `_collect_runtime_context_paths` performs `Path.exists()` and `os.path.realpath` reads against the workspace; no writes.

### Memory / service dependencies

`ContextPackService` is injected with:
- `instruction_loader`, `environment_service`, `operational_state_service`, `knowledge_mgr`, `brain`, `document_memory`, `vision_session_memory`, `transient_state_mgr`, `user_runtime`

These are all read-only in the direct-call paths (no mutations on dependencies).

### Orchestrator dependencies

- `build_runtime_context_pack` receives `orc` and reads `route_decision`, `context_card`, `scratchpad`, `latest_search_query`, `latest_search_failed`, `latest_search_error`, `user_msg`, `brain.workspace`.
- `_hook_upsert_runtime_context` receives `orc` and mutates `orc.chat` (upserts hidden system message).

### Config dependencies

- `CFG` is imported once: `CFG.DATA_DIR` is used as a fallback workspace root in `_collect_runtime_context_paths`.

### Threading / async / lifecycle

- No threading or async inside `context_pack.py`.
- The `@register_hook("on_turn_end")` decorator registers a synchronous callback that is fired by the orchestrator turn loop.

### Prompt / context side effects

- `_hook_upsert_runtime_context` inserts or replaces a hidden system message prefixed with `[LATEST_RUNTIME_CONTEXT]` in `orc.chat`. This directly changes the prompt context for the next turn.
- `apply_context_arbitration` suppresses fields in the pack, which affects what the persona sees. No mutation of external state.

---

## F. Risk Analysis

| Risk | Impact | Likelihood if split incorrectly | Mitigation |
|---|---|---|---|
| **Tail-block ordering drift** | Persona receives blocks in wrong order; behavioral rules may be overridden or ignored. | High if registry moves to a module with different import order. | Lock block order in tests before any move; use explicit ordering metadata instead of list append. |
| **Prompt drift** | `render_runtime_context_message` whitespace / field ordering change alters persona context. | Medium if renderer is refactored without snapshot tests. | Existing `tests/test_context_pack_snapshots.py` covers renderer; keep snapshots green. |
| **Token budget / context rendering drift** | `build_persona_pack` filters brain hits (`distance < 0.40`) and document hits (`distance < 0.35`). Accidentally changing thresholds or ordering changes token count and evidence quality. | Low if logic is copied verbatim, but human error is possible. | Unit tests already assert hit counts and distances; keep them. |
| **Hook registration miss** | If `context_pack.py` is no longer imported, the `on_turn_end` hook and all tail blocks disappear. | High if import chain is broken. | `core/prompt_context.py` and `core/orchestrator_phases.py` both import from this file, ensuring it is loaded early. Any move must preserve that. |
| **Tail-block ordering from external modules** | `proactive_monitor.py` registers 2 blocks. If registry moves, `proactive_monitor.py` must update its import. Forgetting this breaks the build. | Low (static import), but catastrophic if missed. | Compile check + pytest catches import errors. |
| **Dependency direction violation** | `core/services/` must not depend on `core/engines/`. Moving `ContextPackRenderer` to `core/services/` is safe (no engine deps). Moving `ContextPackEngine` is not safe while it still references `_TAIL_BLOCK_REGISTRY`. | High if move is staged incorrectly. | Move only renderer + pure helpers first; keep engine and registry in `core/engines/`. |
| **Direct hook import breakage** | `orchestrator_phases.py` imports `_hook_upsert_runtime_context` directly. Moving the function without updating the import breaks the auto-reroute path. | High | Update import or re-export through a stable `core/engines/hooks.py` surface. |
| **`TailBlockContext` contract breakage** | 13 builder functions + `build_persona_directive_pack` depend on the dataclass shape. | Low if dataclass is frozen, but renaming fields silently breaks builders. | Keep dataclass unchanged during any move. |

---

## G. Test / Smoke Coverage

### Existing pytest coverage

| Test file | What's covered |
|---|---|
| `tests/test_context_pack_snapshots.py` | `ContextPackRenderer.render_runtime_context_message` (empty, task, search reporter, failed search), `ContextPackDirectiveEngine.build_persona_directive_pack` (tail block presence, ordering, direct answers), `ContextPackService.build_persona_pack` (minimal, knowledge, brain hit filtering, document hits, apply_document_focus, clear_memory, context arbitration, to_prompt_context), `resolve_persona_turn_type`, `render_context_arbitration_block` |
| `scripts/test_engines.py` (`TestContextPackEngine`) | `ContextPackService.build_persona_pack` (instructions, style overlay, brain recall, filtering), `apply_document_focus`, `clear_memory_for_file_work`, `build_persona_runtime_pack` (success, failure, pause, typed verification), `ContextPackDirectiveEngine.build_persona_directive_pack` (no-mutation, search, partial, failed), delegation to `SummaryEngine` |

### Existing smoke coverage

| Smoke file | What's covered |
|---|---|
| `scripts/context_pack_engine_smoke_test.py` | Full `PromptContextService` integration: persona pack, document focus, context arbitration (search first pass, reporter, doc focus, explain, proactive, file work), runtime pack (file work, targeted read, paused, partial, failed), directive packs, runtime messages |
| `scripts/search_error_contract_smoke_test.py` | `ContextPackRenderer.render_runtime_context_message` for failed search reporter |

### Missing coverage (now added in `test/context-pack-split-guards`)

1. **`_hook_upsert_runtime_context` direct-call path** — ✅ covered by `TestHookUpsertRuntimeContext` (insert, upsert/replace, `reporter_just_ran` passthrough, cancellation removal).
2. **`_collect_runtime_context_paths` / `_normalize_runtime_context_path`** — ✅ covered by `TestRuntimeContextPaths` (empty/invalid, relative existing/missing, absolute inside workspace, WSL inside workspace, deduplication, scratchpad extraction, user-msg extraction, context-card stage extraction).
3. **Proactive tail blocks in directive pack** — ✅ covered by `TestProactiveTailBlocks` (`proactive_trigger` presence and ordering, `reminder_set_result` scheduled and error cases).
4. **`build_runtime_context_pack` reporter-just-ran branches** — ✅ covered by `TestBuildRuntimeContextPack` (`reporter_just_ran=True` with search completed, search failed with error, latest search query override, non-reporter task goal and status extraction).
5. **`apply_context_arbitration` for all turn types** — ✅ covered by `TestApplyContextArbitrationTurnTypes` (TASK, SEARCH_FIRST_PASS, DOC_FOCUS, PROACTIVE_TRIGGER, EXPLAIN).

### Minimum tests needed before any split

- **Snapshot tests for `ContextPackRenderer`** — ✅ already exist.
- **Snapshot tests for `build_persona_directive_pack` with proactive / reminder tail blocks** — ✅ added.
- **Unit tests for `_normalize_runtime_context_path`** — ✅ added.
- **Unit or smoke test for `_hook_upsert_runtime_context` direct-call path** — ✅ added.
- **Compile + full pytest green** — ✅ must remain green after each incremental move.

---

## H. Recommendation

**D) Split complete.**

Rationale:

- The staged split is **finished**. `ContextPackService` now lives in `core/services/context_pack_service.py` and owns all pure direct-call service behavior.
- `ContextPackDirectiveEngine` remains in `core/engines/context_pack.py` alongside the `@register_hook("on_turn_end")` `_hook_upsert_runtime_context`. These are true engine/lifecycle behavior and must stay in `core/engines/`.
- The tail-block registry (`_TAIL_BLOCK_REGISTRY`), `register_tail_block`, `TailBlockContext`, all 11 builders, and tail-block-specific helpers (`_render_persona_active_skill_block`, `_render_verification_result_block`) live in `core/engines/tail_block_registry.py`.
- `ContextPackRenderer`, `resolve_persona_turn_type`, `render_context_arbitration_block`, and related pure helpers live in `core/services/context_pack_renderer.py`.
- Runtime path helpers (`collect_runtime_context_paths`, `normalize_runtime_context_path`) live in `core/services/context_pack_paths.py`.
- `PromptContextService` composes both `ContextPackService` and `ContextPackDirectiveEngine`, providing a unified façade.

### Staging history

1. ✅ **Add missing tests** (proactive tail blocks, path normalization, hook direct-call path) — completed in `test/context-pack-split-guards`.
2. ✅ **Extract `_TAIL_BLOCK_REGISTRY` + `register_tail_block` + `TailBlockContext` + all builders** into `core/engines/tail_block_registry.py`. Tail-block-specific helpers moved into `tail_block_registry.py`.
3. ✅ **Extract runtime context path helpers** into `core/services/context_pack_paths.py`.
4. ✅ **Move `ContextPackRenderer` + pure helpers** to `core/services/context_pack_renderer.py`.
5. ✅ **Extract `ContextPackService`** from `ContextPackEngine` into `core/services/context_pack_service.py`, leaving `ContextPackDirectiveEngine` in `core/engines/context_pack.py`.
6. ✅ **Run regression pack** after each stage: `python -m compileall …`, `pytest tests/ -q`, `scripts/context_pack_engine_smoke_test.py --json`.

`core/engines/context_pack.py` is no longer a hybrid monolith. It is a thin engine module containing only the directive engine and the `on_turn_end` hook.
