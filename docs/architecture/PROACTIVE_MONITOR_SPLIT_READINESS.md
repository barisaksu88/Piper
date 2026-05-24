# ProactiveMonitor Split Readiness Audit

**Status:** Split complete — Stage 2 extracted  
**Scope:** `core/engines/proactive_monitor.py` + `core/services/reminders.py`  
**Date:** 2026-05-24

---

## Status

**Recommended decision: B**

Split pure reminder store / parser / message helpers to `core/services/`, keep all registry / lifecycle behavior in `core/engines/`.

Rationale:
- `ReminderStore`, `ReminderParseResult`, and all parsing/serialization helpers are **pure direct-call services** with no registry participation.
- `ProactiveMonitor` owns a background `threading.Thread` daemon loop and is instantiated / started / stopped by `ui/controller.py`. This is **lifecycle engine behavior** and should stay in `core/engines/`.
- The four registry decorators (`@register_route_interceptor`, two `@register_tail_block`, `@register_hook("on_turn_end")`) are **engine behavior by definition** and must remain in `core/engines/`.

---

## Current Runtime Shape

`core/engines/proactive_monitor.py` (572 lines) is a **hybrid** module containing five distinct responsibility clusters:

1. **Storage** — `ReminderStore` (thread-safe JSON load/save/add/due/mark_fired) and JSON I/O helpers (`_load_json_list`, `_atomic_write_json`).
2. **Parsing** — `ReminderParseResult` dataclass, `parse_reminder_request()`, and private helpers (`_strip_reminder_prefix`, `_extract_reminder_subject`, `_parse_relative_fire_time`, `_parse_time_of_day`).
3. **Message serialization** — `build_proactive_trigger_message()`, `parse_proactive_trigger_message()`, `build_proactive_consumed_message()`, and prefix constants.
4. **Background monitor lifecycle** — `ProactiveMonitor` class (start/stop/_run_loop, daemon thread, dispatch gating).
5. **Registry / lifecycle hooks** — `@register_route_interceptor _registered_reminder_set_interceptor`, `@register_tail_block _tail_block_proactive_trigger`, `@register_tail_block _tail_block_reminder_set_result`, `@register_hook("on_turn_end") _hook_finalize_proactive_trigger`.

---

## Caller Map

| Caller | Import / Usage | Direct / Registry / App-start | Risk |
|--------|----------------|------------------------------|------|
| `ui/controller.py` | `from core.engines.proactive_monitor import ProactiveMonitor` | App-start — instantiates, starts, stops `ProactiveMonitor` | **High if broken.** App startup depends on this import. |
| `ui/controller_actions.py` | `from core.engines.proactive_monitor import build_proactive_trigger_message, build_proactive_consumed_message` | Direct — called during synthetic reminder dispatch | Medium |
| `core/orchestrator_phases.py` | `PROACTIVE_TRIGGER_PREFIX`, `ReminderStore`, `display_fire_at_local`, `parse_proactive_trigger_message`, `parse_reminder_request` | Direct — `phase_reminder_set()` uses `parse_reminder_request` + `ReminderStore.add()`; proactive trigger short-circuit uses `PROACTIVE_TRIGGER_PREFIX` + `parse_proactive_trigger_message` + `display_fire_at_local` | High |
| `scripts/proactive_monitor_smoke_test.py` | `ProactiveMonitor`, `ReminderStore`, `build_proactive_trigger_message`, `parse_reminder_request` | Direct — unit checks + harness flow | Low (script) |
| `tests/test_context_pack_snapshots.py` | `import core.engines.proactive_monitor  # noqa: F401` | Registry side-effect — ensures tail blocks are registered for snapshot tests | Medium if import chain broken |
| `core/prompting.py` | None — hardcodes `_PROACTIVE_TRIGGER_PREFIX = "[PROACTIVE_TRIGGER]"` and `_PROACTIVE_TRIGGER_CONSUMED_PREFIX = "[PROACTIVE_TRIGGER CONSUMED]"` | Direct (string constants) | **None** — independent copy of constants. If constants diverge, behavior breaks silently. |

---

## Direct-Call / Service-Like Behavior

| Symbol | Responsibility | Side effects | Dependencies | Move candidate? | Notes |
|--------|---------------|--------------|--------------|-----------------|-------|
| `ReminderParseResult` | Dataclass for parse outcome | None | `dataclass` | **Yes** | Pure data shape |
| `ReminderStore` | Thread-safe JSON reminder persistence | Reads/writes `reminders_path` JSON file | `threading.Lock`, `uuid`, `json`, `tempfile` | **Yes** | Pure storage service; no registry |
| `_load_json_list` | Safe JSON list loader with corrupt-file fallback | Reads file | `json`, `pathlib` | **Yes** | Helper, no state |
| `_atomic_write_json` | Atomic JSON write via temp+replace | Writes file | `tempfile`, `os` | **Yes** | Helper, no state |
| `_local_now` | Current time in local tz | None | `datetime` | **Yes** | Helper |
| `_utc_iso` | Format datetime as UTC ISO with `Z` suffix | None | `datetime` | **Yes** | Helper |
| `_display_local_time` | Human-readable local time string | None | `datetime` | **Yes** | Helper |
| `display_fire_at_local` | Convert UTC ISO string to human-readable local time | None | `_display_local_time` | **Yes** | Called by `orchestrator_phases.py` |
| `_strip_reminder_prefix` | Remove leading reminder verbs from user text | None | `re` | **Yes** | Helper |
| `_extract_reminder_subject` | Extract subject, strip date/time fragments | None | `re`, `extract_date_phrase` | **Yes** | Helper |
| `_parse_relative_fire_time` | Parse "in N minutes/hours/days" | None | `re`, `datetime` | **Yes** | Helper |
| `_parse_time_of_day` | Parse "at 3:30pm" into (hour, minute) | None | `re` | **Yes** | Helper |
| `parse_reminder_request` | Full reminder intent → `ReminderParseResult` | None | All parsing helpers, `extract_date_phrase`, `resolve_date_phrase` | **Yes** | Called by `orchestrator_phases.py` and route interceptor |
| `_build_task_event_fallback_route` | Build TASK route for dated/undated reminders without fire time | None | `extract_date_phrase`, `resolve_date_phrase` | **Yes** | Called by interceptor for fallback routing |
| `build_proactive_trigger_message` | Serialize reminder entry to trigger transport string | None | `json` | **Yes** | Called by `controller_actions.py` and smoke test |
| `parse_proactive_trigger_message` | Deserialize trigger transport string → dict | None | `json` | **Yes** | Called by `orchestrator_phases.py` |
| `build_proactive_consumed_message` | Build hidden system consumed marker | None | None | **Yes** | Called by `controller_actions.py` and hook |
| `ProactiveMonitor` | Background daemon thread polling reminder store for due entries | Spawns thread, calls `dispatch_callback` | `threading`, `ReminderStore` | **No** | Owns lifecycle (start/stop/_run_loop). Imported by `ui/controller.py` for app startup. Engine-like by definition. |
| `PROACTIVE_TRIGGER_PREFIX` | String constant "[PROACTIVE_TRIGGER]" | None | None | **Yes** | Currently imported by `orchestrator_phases.py`. Hardcoded duplicate exists in `core/prompting.py` — risk of divergence. |
| `PROACTIVE_TRIGGER_CONSUMED_PREFIX` | String constant "[PROACTIVE_TRIGGER CONSUMED]" | None | None | **Yes** | Hardcoded duplicate exists in `core/prompting.py` — risk of divergence. |

---

## Registry / Lifecycle Behavior

| Symbol | Registry | Trigger | What it mutates/returns | Must stay in engines? | Notes |
|--------|----------|---------|------------------------|----------------------|-------|
| `_registered_reminder_set_interceptor` | `@register_route_interceptor` | `route_normalizer` calls interceptors before router LLM | Returns `REMINDER_SET` or `REMINDER_TASK_EVENT` bypass dict | **Yes** | Lifecycle interception |
| `_tail_block_proactive_trigger` | `@register_tail_block` | `ContextPackDirectiveEngine.build_persona_directive_pack` iterates registry | Returns `[PROACTIVE_TRIGGER]` prompt block | **Yes** | Registry side-effect at import time |
| `_tail_block_reminder_set_result` | `@register_tail_block` | Same as above | Returns `[REMINDER_SET_RESULT]` prompt block | **Yes** | Registry side-effect at import time |
| `_hook_finalize_proactive_trigger` | `@register_hook("on_turn_end")` | `orchestrator_phases.py:fire_hooks("on_turn_end", ...)` | Calls `ReminderStore.mark_fired()`; replaces last system message with consumed marker | **Yes** | Lifecycle hook |

---

## File I/O and Safety Behavior

- **ReminderStore.load()** — safe: missing file → `[]`, corrupt JSON → `[]`, non-list → `[]`, filters non-dict items.
- **ReminderStore.save()** — safe: uses `_atomic_write_json` (temp file + fsync + replace). Thread-safe via `threading.Lock`.
- **ReminderStore.add()** — appends entry with `uuid.uuid4()`, `fired=False`, then saves.
- **ReminderStore.due_entries()** — loads all, filters `fired=True`, filters unparseable `fire_at`, filters future times, sorts by `fire_at` string. No max cap on returned list (unbounded if many reminders are due).
- **ReminderStore.mark_fired()** — idempotent: returns `False` if already fired or ID not found. Writes only if changed.
- **ProactiveMonitor background thread** — daemon thread with `threading.Event` stop signal. `_run_loop` catches all exceptions and logs them. Stop join timeout is capped at `min(poll_interval_s, 1.0)`.
- **Config dependency** — `CFG.REMINDERS_PATH` used by `_hook_finalize_proactive_trigger` (line 562) and `orchestrator_phases.py` (via `ReminderStore(CFG.REMINDERS_PATH)`).

---

## Existing Test Coverage

| File | What it tests |
|------|---------------|
| `tests/test_context_pack_snapshots.py` | Proactive trigger tail block presence (`test_proactive_trigger_block_present`), reminder set result scheduled block (`test_reminder_set_result_scheduled_block_present`), reminder set result error block (`test_reminder_set_result_error_block_present`), proactive trigger persona turn type |
| `tests/test_state_mutation.py` | Reminder route normalization (`test_reminder_route_normalized`), reminder task override followup (`test_reminder_task_override`) |
| `scripts/proactive_monitor_smoke_test.py` | Parse roundtrip (`parse_reminder_request`), route interceptor detection (`detect_route_interceptor`), `ReminderStore` add/load, `ProactiveMonitor` deferral/dispatch, full harness reminder turn + proactive turn end-to-end |

**No dedicated unit tests exist for:**
- `ReminderStore` edge cases (missing file, corrupt JSON, non-list payload, empty add, mark_fired idempotency)
- `parse_reminder_request` error branches (no-subject, invalid time, past time, no time at all)
- `display_fire_at_local` invalid input
- `_build_task_event_fallback_route` output shape
- `build_proactive_trigger_message` / `parse_proactive_trigger_message` roundtrip
- `build_proactive_consumed_message` format
- `_hook_finalize_proactive_trigger` behavior (marks fired, replaces system message, no-op on non-FINISHED)

---

## Missing Guard Tests Before Any Split

The following tests should be added **before** any file move, because they lock behavior that currently has zero unit coverage:

### ReminderStore
1. `test_load_missing_file_returns_empty_list`
2. `test_load_corrupt_json_returns_empty_list`
3. `test_load_non_dict_items_filtered`
4. `test_add_generates_uuid_and_sets_fired_false`
5. `test_due_entries_excludes_already_fired`
6. `test_due_entries_excludes_unparseable_fire_at`
7. `test_due_entries_sorts_by_fire_at`
8. `test_mark_fired_returns_true_then_false_idempotent`
9. `test_mark_fired_unknown_id_returns_false`

### Parsing
10. `test_parse_reminder_request_relative_time_success`
11. `test_parse_reminder_request_no_reminder_pattern_returns_error`
12. `test_parse_reminder_request_no_subject_returns_error`
13. `test_parse_reminder_request_date_without_time_returns_error`
14. `test_parse_reminder_request_past_time_returns_error`
15. `test_parse_reminder_request_no_time_info_returns_error`

### Display / Messages
16. `test_display_fire_at_local_valid_iso`
17. `test_display_fire_at_local_invalid_returns_raw_fallback`
18. `test_build_proactive_trigger_message_roundtrip_with_parse`
19. `test_build_proactive_consumed_message_includes_id`

### Route Interceptor
20. `test_interceptor_reminder_set_for_parsable_reminder`
21. `test_interceptor_reminder_task_event_for_dated_no_time`
22. `test_interceptor_reminder_task_event_for_undated_no_time`

### Tail Blocks
23. `test_tail_block_proactive_trigger_returns_nonempty_for_matching_notice`
24. `test_tail_block_proactive_trigger_returns_empty_for_non_matching_notice`
25. `test_tail_block_reminder_set_result_scheduled`
26. `test_tail_block_reminder_set_result_error`

### Hook
27. `test_hook_finalize_marks_fired_only_on_finished_turn`
28. `test_hook_finalize_noop_on_non_proactive_trigger`
29. `test_hook_finalize_noop_on_persona_error`

### ProactiveMonitor lifecycle
30. `test_start_does_not_double_start`
31. `test_stop_joins_thread_within_timeout`
32. `test_dispatch_loop_respects_can_dispatch_false`
33. `test_dispatch_loop_respects_is_inflight_true`
34. `test_dispatch_breaks_after_first_successful_callback`

---

## Recommended Staging

If the project proceeds with the B split, stages should be small and independently reviewable:

### Stage 1 — Guard tests (required before any move)
- Add unit tests for all items in **Missing Guard Tests** above.
- Target: `tests/test_proactive_monitor.py`.
- Validation: `pytest tests/test_proactive_monitor.py -q` passes.

### Stage 2 — Extract pure service helpers (docs + code)
- Create `core/services/reminders.py` containing:
  - `ReminderParseResult`, `ReminderStore`, `_load_json_list`, `_atomic_write_json`
  - `_local_now`, `_utc_iso`, `_display_local_time`, `display_fire_at_local`
  - `_strip_reminder_prefix`, `_extract_reminder_subject`, `_parse_relative_fire_time`, `_parse_time_of_day`
  - `parse_reminder_request`, `_build_task_event_fallback_route`
  - `build_proactive_trigger_message`, `parse_proactive_trigger_message`, `build_proactive_consumed_message`
  - `PROACTIVE_TRIGGER_PREFIX`, `PROACTIVE_TRIGGER_CONSUMED_PREFIX`
- Reduce `core/engines/proactive_monitor.py` to:
  - `ProactiveMonitor` class
  - Four registry decorators + their functions
  - Import service symbols from `core.services.reminders` instead of local definitions
- Update `core/services/__init__.py` to export moved symbols.

### Stage 3 — Update callers
- `core/orchestrator_phases.py` — update imports to `core.services.reminders`
- `ui/controller.py` — `ProactiveMonitor` import stays from `core.engines.proactive_monitor` (it remains there)
- `ui/controller_actions.py` — update imports to `core.services.reminders`
- `scripts/proactive_monitor_smoke_test.py` — update imports
- `tests/test_context_pack_snapshots.py` — may still need `import core.engines.proactive_monitor` for tail-block registration side effect, or import from `core.engines.tail_block_registry` if tail blocks are registered via a different path

### Stage 4 — Constant deduplication (optional but recommended)
- `core/prompting.py` hardcodes `_PROACTIVE_TRIGGER_PREFIX` and `_PROACTIVE_TRIGGER_CONSUMED_PREFIX`. After the move, import them from `core.services.reminders` to eliminate divergence risk.

### Stage 5 — Docs + validation
- Update `ENGINE_UTILITY_CLASSIFICATION.md`, `engine-directory-audit.md`, and create split-readiness completion doc.
- Validation: full compile + test suite + smoke tests.

---

## Collision Notes With Frontend / App Startup

- **Do not touch `web_ui/` or `web_ui/frontend/`** for this backend path.
- **`ui/controller.py`** imports `ProactiveMonitor` from `core.engines.proactive_monitor` for app startup. A future split must **not** break this import. `ProactiveMonitor` itself stays in `core/engines/`, so this import line does not need to change.
- **`ui/controller_actions.py`** imports `build_proactive_trigger_message` and `build_proactive_consumed_message`. These are pure helpers and should move to `core/services/reminders`. Only the import line changes; no other `ui/` code is affected.
- **`app.py` and `config.py`** should not be touched unless a future validation failure proves it is necessary.
- **Tail-block registration** — `tests/test_context_pack_snapshots.py` imports `core.engines.proactive_monitor` solely for the `@register_tail_block` side effect. If the tail block functions move to a different engine module or the registration path changes, this import must be updated. The safest path is to keep the tail block functions in `core/engines/proactive_monitor.py` (which is the plan).

---

## Uncertainty / Risk

1. **Constant divergence:** `core/prompting.py` hardcodes `[PROACTIVE_TRIGGER]` and `[PROACTIVE_TRIGGER CONSUMED]`. If the service module changes these constants, `prompting.py` will silently desync. This is a pre-existing risk, not introduced by the split.
2. **`ReminderStore.due_entries` is unbounded:** If many reminders accumulate, `due_entries()` loads the entire JSON file every poll cycle. There is no max-returned limit. This is pre-existing and not a split blocker.
3. **`_build_task_event_fallback_route` dependency on route shape:** This function builds raw route dicts that must match the orchestrator's expected `RouteDecision` / stage card shape. Moving it to services is safe, but any change to its output risks routing breakage. Guard tests should lock the output shape.
4. **`ProactiveMonitor` borderline:** Some may argue `ProactiveMonitor` is a "service" because it has a clean API (start/stop). But it owns a daemon thread and is managed by `ui/controller.py` lifecycle. The established pattern in this repo is that thread-owning modules stay in `core/engines/` (see `computer_use_engine.py`). Keeping it in engines is consistent.
5. **No dedicated `tests/test_proactive_monitor.py` exists today.** The smoke test is LLM-backed and slow. Guard tests must be fast, deterministic, and require no LLM.
