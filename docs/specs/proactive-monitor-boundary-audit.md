# Proactive Monitor Boundary Audit

> **Branch:** `audit/proactive-monitor-boundary`  
> **Scope:** Audit/docs/tests only. No runtime behavior changed.  
> **Date:** 2026-05-26

---

## 1. Current Responsibilities in `core/engines/proactive_monitor.py`

| Responsibility | Lines | Description |
|---|---|---|
| **Background service class** | 27–72 | `ProactiveMonitor` — daemon thread that polls `ReminderStore.due_entries()` and dispatches via callback. |
| **Route interceptor** | 75–126 | `_registered_reminder_set_interceptor` — matches reminder utterances, parses timing, routes to `REMINDER_SET` or falls back to `REMINDER_TASK_EVENT`. |
| **Tail block (proactive trigger)** | 129–152 | `_tail_block_proactive_trigger` — persona directive when `system_notice.kind == "proactive_trigger"`. |
| **Tail block (reminder set result)** | 155–191 | `_tail_block_reminder_set_result` — persona directive when `system_notice.kind == "reminder_set_result"` (scheduled or error). |
| **Feature hook (turn end)** | 194–218 | `_hook_finalize_proactive_trigger` — marks reminder fired in `ReminderStore` and replaces raw trigger message with consumed marker in chat history. |

The module **does NOT** contain parsing logic, storage implementation, or message builders — those already live in `core/services/reminders.py`.

---

## 2. Registry Map

### Route Interceptors (9 total)

| Index | Module | Function | Kind |
|---|---|---|---|
| 6 | `core.engines.proactive_monitor` | `_registered_reminder_set_interceptor` | `REMINDER_SET` / `REMINDER_TASK_EVENT` |

### Feature Hooks — `on_turn_end` (7 total)

| Index | Module | Function |
|---|---|---|
| 3 | `core.engines.proactive_monitor` | `_hook_finalize_proactive_trigger` |

### Tail Blocks (13 total)

| Index | Module | Function | Condition |
|---|---|---|---|
| 11 | `core.engines.proactive_monitor` | `_tail_block_proactive_trigger` | `system_notice.kind == "proactive_trigger"` |
| 12 | `core.engines.proactive_monitor` | `_tail_block_reminder_set_result` | `system_notice.kind == "reminder_set_result"` |

### Import Path

All registrations are triggered by:

```python
# core/orchestrator.py:30
from core.engines import proactive_monitor as _proactive_monitor_registration  # noqa: F401
```

Additional consumers that import the module directly:
- `ui/controller.py` imports `ProactiveMonitor` (class instantiation)
- `scripts/proactive_monitor_smoke_test.py` imports `ProactiveMonitor`
- `tests/test_context_pack_snapshots.py` imports for tail-block side-effects
- `tests/test_proactive_monitor.py` imports for all guard tests

---

## 3. Service Boundary Recommendation

| Symbol | Current Location | Recommendation | Rationale |
|---|---|---|---|
| `ProactiveMonitor` class | `core/engines/proactive_monitor.py` | **Move** to `core/services/proactive_monitor.py` (or into `core/services/reminders.py`) | It is a background polling service, not a registry wrapper. `ui/controller.py` already treats it as a service dependency. |
| `_registered_reminder_set_interceptor` | `core/engines/proactive_monitor.py` | **Keep** or move to `core/routing/route_normalizer.py` | Reminder-specific routing logic. Keeping it here is acceptable; moving it to `route_normalizer.py` would colocate it with other interceptors but increases file size. |
| `_tail_block_proactive_trigger` | `core/engines/proactive_monitor.py` | **Keep** | Tail blocks are engine/persona-boundary wrappers. This is the correct layer. |
| `_tail_block_reminder_set_result` | `core/engines/proactive_monitor.py` | **Keep** | Same as above. |
| `_hook_finalize_proactive_trigger` | `core/engines/proactive_monitor.py` | **Keep** | `on_turn_end` hook wrappers belong in `core/engines/`. However, the inline `ReminderStore` instantiation and chat mutation should be delegated to a service helper. |

### Preferred split outcome

```
core/engines/proactive_monitor.py      # interceptors, hooks, tail blocks only
core/services/proactive_monitor.py     # ProactiveMonitor class + service helpers
```

---

## 4. Proposed Split Plan

### PR 1 — Extract `ProactiveMonitor` service class *(low risk)*
- Move `ProactiveMonitor` to `core/services/proactive_monitor.py`.
- Re-export from `core/engines/proactive_monitor.py` for backward compatibility, or update `ui/controller.py` and `scripts/proactive_monitor_smoke_test.py` to import from services.
- Add a smoke test run to confirm monitor still starts/stops/dispatches.

### PR 2 — Delegate hook storage/chat mutation *(low risk)*
- Extract the body of `_hook_finalize_proactive_trigger` into a service function (e.g. `finalize_proactive_trigger_turn(orc, notice)`).
- Keep the decorator in `core/engines/proactive_monitor.py`; move the imperative logic to `core/services/reminders.py`.

### PR 3 — Unify interceptor fallback logic *(medium risk)*
- The interceptor re-implements date/time extraction that `parse_reminder_request` already does. Refactor `parse_reminder_request` to expose structured fallback metadata (e.g. `fallback_kind: "event" | "task" | None`) so the interceptor does not duplicate parsing.
- **Not recommended unless test coverage for edge cases is complete first.**

---

## 5. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| **Import cycle** | Low | `core/services/reminders.py` already imports nothing from `core/engines/`. Moving `ProactiveMonitor` into services keeps the DAG `ui → core/services → core/engines` safe. |
| **Registry registration loss** | Medium | If `core/engines/proactive_monitor.py` is emptied of decorators, `core/orchestrator.py` must import the replacement registration module. A re-export or updated import line prevents dead registration. |
| **Behavior drift** | Low | All logic is deterministic (regex, parsing, storage). Existing unit tests (`tests/test_proactive_monitor.py`, 42 tests) provide strong regression coverage. |
| **Test coverage gaps** | Medium | Missing guards for past-time routing, empty `raw_message` in hook, and hook behavior when `reminder_id` is blank (see §6). |
| **User-visible behavior** | Low | No user-visible changes if only `ProactiveMonitor` moves. Changing interceptor logic could alter routing. |

---

## 6. Tests Needed Before Refactor

The following tests should be added to `tests/test_proactive_monitor.py` **before** any code is moved:

1. **Route interceptor — past time with date+time**
   - Input: `"remind me to call mom today at 9:00 am"` (now = noon)
   - Current behavior: returns `REMINDER_SET` (phase will error later)
   - Test should document this behavior so a future refactor does not silently change routing.

2. **Route interceptor — regex match but unparseable time**
   - Input: `"remind me to check the oven at 99:00"`
   - Verify it does not crash and returns a sensible route.

3. **Hook — empty `raw_message` skips chat replacement**
   - `notice` with `kind="proactive_trigger"`, `id="abc"`, `raw_message=""`
   - Verify `mark_fired` is still called but `replace_last_system_message` is **not** invoked.

4. **Hook — empty `reminder_id` skips `mark_fired`**
   - `notice` with `kind="proactive_trigger"`, `id=""`, `raw_message="msg"`
   - Verify chat replacement happens but `ReminderStore.mark_fired` is not called.

5. **Hook — exception during chat replace is swallowed**
   - The hook has a bare `except Exception: pass` around `replace_last_system_message`.
   - Verify it does not propagate.

6. **Monitor — exception inside `_run_loop` is logged, not raised**
   - Inject a `can_dispatch` that raises; verify `log_callback` receives the exception text and the loop continues.

---

## 7. Recommendation

**`ADD_TESTS_FIRST`**

Rationale:
- The module is hybrid (class + interceptor + hooks + tail blocks) but the boundary with `core/services/reminders.py` is already clean.
- The highest-value cleanup is moving `ProactiveMonitor` to `core/services/`, which is safe but requires updating imports in `ui/controller.py` and smoke tests.
- There are real test gaps (§6) that should be closed before any code motion so that regressions are caught automatically.
- After tests are added, the next branch should be **`refactor/proactive-monitor-extract-service`** (PR 1 in §4).

---

## 8. Issues / Smells

| Severity | Issue | Location | Notes |
|---|---|---|---|
| **SHOULD_FIX** | Route interceptor routes past-time reminders to `REMINDER_SET` instead of failing fast or routing to task/event. | `_registered_reminder_set_interceptor` lines 84–126 | The phase handles it gracefully, but the interceptor does redundant work. |
| **SHOULD_FIX** | Hook directly instantiates `ReminderStore` and mutates chat history inline instead of delegating to a service helper. | `_hook_finalize_proactive_trigger` lines 207–217 | Violates "service owns storage" boundary. |
| **WATCH** | `ProactiveMonitor` is a background service class living in `core/engines/`. | `core/engines/proactive_monitor.py` lines 27–72 | Not wrong, but `core/services/` is the more natural home. |
| **WATCH** | `ui/controller.py` imports `ProactiveMonitor` from `core/engines/`. | `ui/controller.py:15` | If the class moves, this import must update. |
| **WATCH** | Bare `except Exception: pass` around chat mutation in hook. | `_hook_finalize_proactive_trigger` lines 215–218 | Hides real bugs silently. Prefer logging at minimum. |
| **IGNORE_FOR_NOW** | Interceptor duplicates date/time extraction logic already present in `core/services/reminders.py`. | `_registered_reminder_set_interceptor` lines 94–118 | Requires `parse_reminder_request` API change to unify. |
| **IGNORE_FOR_NOW** | `tests/test_context_pack_snapshots.py` imports `core.engines.proactive_monitor` only for tail-block side-effects. | `tests/test_context_pack_snapshots.py:29` | Acceptable pattern; importing `core.orchestrator` would also work now that registration is unified. |

---

## Validation Log

```
python -m compileall app.py config.py core ui memory tools llm web_ui   → OK
python scripts/engine_registry_inventory.py --json                        → 9 interceptors, 10 hooks, 13 tail blocks
python -m pytest tests/test_engine_registry_inventory.py -q               → 17 passed
python -m pytest tests/test_registry_dedup_guards.py -q                   → 11 passed
python -m pytest tests/test_route_bypass_interceptors.py -q              → 23 passed
python -m pytest tests/test_memory_insertion_engine.py -q                → 12 passed
python -m pytest tests/test_proactive_monitor.py -q                      → 42 passed
python -m pytest tests/test_engine_hook_registration.py -q               → 10 passed
```
