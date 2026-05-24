# Route Bypass Engine Readiness Audit

**Branch:** `split/route-bypass-engines-clean`  
**Scope:** Move environment-query and operational-state bypasses from procedural `_run_route_core()` into registry-driven engines  
**Date:** 2026-05-24  
**Status:** ✅ IMPLEMENTED

---

## 1. Current Procedural Bypasses

| Bypass | Current location | Detector / service | Output shape | Stats bypass label | Next stage |
|--------|-----------------|--------------------|--------------|-------------------|------------|
| **Environment query** | `core/orchestrator_phases.py` `_run_route_core()` lines 923–933 | `core.routing.environment_queries.looks_like_live_environment_query()` | `{"decision": "CHAT", "card": {"query": user_msg}}` | `environment_query` | `PERSONA` |
| **Operational state readonly answer** | `core/orchestrator_phases.py` `_run_route_core()` lines 935–949 | `core.prompt_context.PromptContextService.build_readonly_state_answer()` → delegates to `StateMutationEngine.build_readonly_answer()` + `OperationalStateService` | `{"decision": "CHAT", "card": {"query": user_msg}}` | `operational_state_query` | `PERSONA` |

### Additional operational-state consumption site

The **actual answer text** is not produced in `_run_route_core()`. The bypass there only decides *whether* to skip the router. The answer text is recomputed inside `phase_persona()` (lines 2593–2610) via a second `build_readonly_state_answer()` call and streamed via `_finish_persona_fast_path()`.

This means:
- The route bypass needs only a boolean: *does this query have a readonly answer?*
- The persona phase needs the actual answer string.
- An interceptor can cache the answer on `orc` to avoid the double computation, or it can simply decide to bypass and let persona recompute.

---

## 2. Route Interceptor Registry Fit

### Signature extension applied

`detect_route_interceptor()` now accepts an optional `orc` parameter and dispatches to interceptors using `inspect.signature` arity-checking:

```python
def detect_route_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]] | None = None,
    orc=None,
) -> dict[str, Any] | None:
    ...
    for interceptor in _ROUTE_INTERCEPTOR_REGISTRY:
        sig = inspect.signature(interceptor)
        if len(sig.parameters) >= 3:
            result = interceptor(text, history, orc)
        else:
            result = interceptor(text, history)
        if result is not None:
            return result
    return None
```

**Why `inspect.signature` instead of `TypeError` fallback:**
- Avoids catching arbitrary `TypeError` raised inside interceptor bodies
- Evaluated once per interceptor per call — negligible overhead
- Existing 2-arg interceptors require zero changes
- New 3-arg interceptors opt-in explicitly

---

## 3. Ordering Analysis

### Current order in `_run_route_core()`

```
1. Pending search payload  → REPORTER
2. Proactive trigger        → PERSONA
3. detect_route_interceptor() → interceptor-specific stage
4. Environment query        → PERSONA   ← target for extraction
5. Operational state query  → PERSONA   ← target for extraction
6. Document chat heuristic  → DOC_FOCUS
7. Live screen visual chat  → PERSONA
8. Secretary / router LLM
```

### Target order after implementation

```
1. Pending search payload  → REPORTER
2. Proactive trigger        → PERSONA
3. detect_route_interceptor()
   ├── existing interceptors (UNDO, FILE_TARGET, DESTRUCTIVE_PROMPT_INJECTION, EXPLAIN, REMINDER_SET, …)
   ├── environment_query interceptor    → PERSONA
   └── operational_state interceptor    → PERSONA
4. Document chat heuristic  → DOC_FOCUS
5. Live screen visual chat  → PERSONA
6. Secretary / router LLM
```

**Ordering risks:**
- Environment query must run **after** safety interceptors (destructive-prompt-injection, file-target-confirmation, undo, explain) because those represent higher-priority user intent.
- Operational state must run **after** environment query to preserve current precedence (environment queries are cheaper and more specific).
- Both must run **before** document-chat and live-screen heuristics to preserve current behavior.
- Registration order is controlled by module import order. `core/engines/environment_query.py` and `core/engines/operational_state_answer.py` should be imported **after** `core/routing/route_normalizer.py` (which registers the existing interceptors) and **before** `app.py` finishes startup.

---

## 4. Proposed Engine Design

### EnvironmentQueryEngine

**File:** `core/engines/environment_query.py`

**Behavior:**
```python
from core.routing.environment_queries import looks_like_live_environment_query
from core.routing.route_normalizer import register_route_interceptor

@register_route_interceptor
def _registered_environment_query_interceptor(
    user_msg: str, recent_history: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    del recent_history
    if not looks_like_live_environment_query(user_msg):
        return None
    return {
        "kind": "ENVIRONMENT_QUERY",
        "next_stage": "PERSONA",
        "stats_decision": "CHAT",
        "bypass": "environment_query",
        "log_message": "   -> Live environment query. Skipping Secretary/router LLM and answering in PERSONA.",
        "route_decision": {
            "decision": "CHAT",
            "interceptor": "ENVIRONMENT_QUERY",
            "card": {"query": user_msg},
        },
    }
```

**Notes:**
- The `_registered_live_environment_chat` **normalizer** in `route_normalizer.py` must remain as a safety net for any SEARCH route that slips past the interceptor.
- The `_is_live_environment_chat_query` helper in `orchestrator_phases.py` becomes redundant and can be removed.
- The `looks_like_live_environment_query` import in `orchestrator_phases.py` becomes redundant and can be removed.

### OperationalStateAnswerEngine

**File:** `core/engines/operational_state_answer.py`

**Behavior:**
```python
from core.routing.route_normalizer import register_route_interceptor

@register_route_interceptor
def _registered_operational_state_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
    orc,
) -> dict[str, Any] | None:
    del recent_history
    try:
        answer = orc.prompt_context.build_readonly_state_answer(user_msg)
    except Exception:
        return None
    if not answer:
        return None
    # Cache the answer so persona does not recompute it
    orc._cached_readonly_state_answer = answer
    return {
        "kind": "OPERATIONAL_STATE_QUERY",
        "next_stage": "PERSONA",
        "stats_decision": "CHAT",
        "bypass": "operational_state_query",
        "log_message": "   -> Operational state query. Skipping Secretary/router LLM and answering in PERSONA.",
        "route_decision": {
            "decision": "CHAT",
            "interceptor": "OPERATIONAL_STATE_QUERY",
            "card": {"query": user_msg},
        },
    }
```

**Persona phase optimization:**
In `phase_persona()`, replace the unconditional `build_readonly_state_answer()` call with a cache check:

```python
readonly_state_answer = getattr(orc, "_cached_readonly_state_answer", "")
if not readonly_state_answer:
    readonly_state_answer = orc.prompt_context.build_readonly_state_answer(readonly_query)
```

This eliminates the double computation.

---

## 5. Test Plan

### Guard tests for `test_route_bypass_interceptors.py` (new file)

| # | Test | Expected |
|---|------|----------|
| 1 | Environment: "What's the date?" | Returns interceptor dict, `kind=ENVIRONMENT_QUERY`, `next_stage=PERSONA` |
| 2 | Environment: "What time is it?" | Same |
| 3 | Environment: "What day is it?" | Same |
| 4 | Environment: "Today's date" | Same |
| 5 | Environment false positive: "What's the date of the meeting?" | Returns `None` |
| 6 | Environment false positive: "Set a reminder for tomorrow at 3" | Returns `None` |
| 7 | Environment false positive: "What time is the flight?" | Returns `None` |
| 8 | Operational state: "What tasks do I have?" (with tasks in store) | Returns interceptor dict, `kind=OPERATIONAL_STATE_QUERY` |
| 9 | Operational state: "Any events?" (with events in store) | Same |
| 10 | Operational state: "What's on my schedule for tomorrow?" | Same |
| 11 | Operational state empty store: "What tasks do I have?" (no tasks) | Returns interceptor dict (answer is "No pending tasks.") |
| 12 | Operational state mutation: "Add task buy milk" | Returns `None` |
| 13 | Operational state mutation: "Delete event dentist" | Returns `None` |
| 14 | Operational state mutation: "Reschedule meeting to Friday" | Returns `None` |
| 15 | Ordering: undo interceptor + env query in same turn | Undo interceptor wins (higher priority) |
| 16 | Ordering: explain interceptor + op-state query | Explain interceptor wins |
| 17 | Stats bypass label: environment interceptor | `bypass="environment_query"` |
| 18 | Stats bypass label: operational state interceptor | `bypass="operational_state_query"` |
| 19 | App-start import check | Both modules import without error; interceptors appear in registry |

### Regression tests to run

- `scripts/live_environment_chat_smoke_test.py` — validates `pre_llm_bypass == "environment_query"` in stats
- `scripts/operational_state_readonly_smoke_test.py` — validates `OperationalStateService.build_readonly_answer()` behavior
- `scripts/turn_explanation_smoke_test.py` — validates route interceptor ordering with explain interceptor
- `scripts/route_boundary_smoke_test.py` — validates existing interceptor behavior is unchanged
- `scripts/proactive_monitor_smoke_test.py` — validates reminder interceptor ordering

---

## 6. Runtime Change Plan

### Files to create

| File | Purpose |
|------|---------|
| `core/engines/environment_query.py` | `@register_route_interceptor` for environment queries |
| `core/engines/operational_state_answer.py` | `@register_route_interceptor` for operational state readonly queries |
| `tests/test_route_bypass_interceptors.py` | Guard tests (42 tests following proactive monitor test style) |

### Files to modify

| File | Changes |
|------|---------|
| `core/routing/route_normalizer.py` | Extend `detect_route_interceptor()` with optional `orc` parameter and backward-compatible call pattern |
| `core/orchestrator_phases.py` | Remove procedural bypass blocks (lines 923–949); remove `_is_live_environment_chat_query` helper; remove `looks_like_live_environment_query` import; add cache-aware readonly answer read in `phase_persona()` |
| `core/orchestrator.py` | Add import lines for new engine modules (registration side effects) |

### Files to review but not modify

| File | Reason |
|------|--------|
| `core/routing/route_normalizer.py` `_registered_live_environment_chat` normalizer | Keep as safety net for SEARCH → CHAT downgrade |
| `core/routing/environment_queries.py` | No changes needed — pure predicate stays where it is |
| `core/prompt_context.py` | No changes needed — `build_readonly_state_answer()` stays |
| `core/operational_state_service.py` | No changes needed — `build_readonly_answer()` stays |

---

## 7. Critical Finding: Existing Engine Hook Modules Are Dead Code

During this audit, a registry wiring gap was discovered in the completed engine/service split wave.

### Modules affected

| Module | Hook type | Status |
|--------|-----------|--------|
| `core/engines/change_journal.py` | `@register_hook("on_task_verified")` | **Never imported at runtime** — hook never registers |
| `core/engines/conversation_compressor.py` | `@register_hook("on_turn_end")` | **Never imported at runtime** — hook never registers |
| `core/engines/stats_collector.py` | `@register_hook("on_pre_route")` | **Never imported at runtime** — hook never registers |

### Evidence

Python runtime check after full orchestrator import:
```python
>>> from core.feature_hooks import list_hooks
>>> list_hooks()
{
  'on_pre_route': ['core.prompt_context._hook_record_user_turn_once'],
  'on_turn_end': [
    'core.turn_explanation._hook_upsert_last_turn_explanation_context',
    'core.engines.context_pack._hook_upsert_runtime_context',
    'core.file_target_confirmation._hook_upsert_pending_file_target_confirmation',
    'core.engines.proactive_monitor._hook_finalize_proactive_trigger',
  ],
}
```

Missing: `core.engines.change_journal._hook_record_change_journal`, `core.engines.conversation_compressor._hook_deferred_conversation_summary`, `core.engines.stats_collector._hook_note_pre_route_user_msg`.

### Why the system still works

The orchestrator phase code calls the service methods **directly** instead of relying on the hooks:
- `orc.change_journal.record_turn()` — called directly in `phase_manager()` and `phase_undo()`
- `orc.conversation_compressor.compress_history()` — called directly in `phase_persona()`
- `orc.stats_collector.note_user_msg()` — **never called** (data gap in stats)

### Impact on this audit

Any new engine modules **must** be explicitly imported for their registry decorators to execute. The pattern used for `proactive_monitor` in `core/orchestrator.py` is correct:

```python
from core.engines import proactive_monitor as _proactive_monitor_registration  # noqa: F401
```

New engine modules should follow the same pattern:

```python
from core.engines import environment_query as _environment_query_registration  # noqa: F401
from core.engines import operational_state_answer as _operational_state_answer_registration  # noqa: F401
```

### Recommendation

Create a follow-up branch `fix/engine-hook-import-wiring` to:
1. Add missing import lines for `change_journal`, `conversation_compressor`, and `stats_collector` in `core/orchestrator.py`
2. Verify `note_user_msg` behavior after the hook is wired
3. Confirm no duplicate calls exist between direct phase code and hooks

---

## 8. Docs To Update Later

| Document | Update after implementation |
|----------|----------------------------|
| `docs/architecture/TRIGGER_FLOW.md` | Update §2 pre-LLM bypass list to show interceptors instead of procedural checks |
| `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` | Add new engine modules to registry-only or hybrid table |
| `docs/specs/engine-directory-audit.md` | Add new modules to remaining `core/engines/` inventory |
| `docs/specs/engine-service-boundary-final.md` | Update module counts and classification |

---

## 9. Recommendation

**Status:** Ready to implement.

**Next branch:** `split/route-bypass-engines`

**Rationale:**
- Environment query is a clean `@register_route_interceptor` with zero signature changes.
- Operational state needs only the smallest possible extension (optional `orc` parameter with `TypeError` fallback).
- Both bypasses map cleanly to existing interceptor return shapes.
- No behavior drift if the normalizer safety net and persona cache are preserved.
- The dead-code finding (§7) is pre-existing and does not block this work, but new engines must be imported explicitly to avoid repeating the same wiring gap.

**Implementation completed:**
1. ✅ Extended `detect_route_interceptor` signature in `route_normalizer.py`
2. ✅ Created `core/engines/environment_query.py`
3. ✅ Created `core/engines/operational_state_answer.py`
4. ✅ Wired imports in `core/orchestrator.py`
5. ✅ Removed procedural bypasses from `orchestrator_phases.py`
6. ✅ Added cache check in `phase_persona()`
7. ✅ Added guard tests in `tests/test_route_bypass_interceptors.py`
8. ✅ Ran full regression pack
