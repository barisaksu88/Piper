# StatsCollector Split Readiness Audit

**Status:** Split complete  
**Scope:** `core/engines/stats_collector.py` + `core/services/stats_collector.py`  
**Branch:** `split/stats-collector-service`  
**Date:** 2026-05-24

---

## Recommended Decision

**B** — Split service class/dataclass/helpers to `core/services/`, keep hook in `core/engines/`.

Rationale:
- `StatsCollector` is ~570 lines of direct-call service behavior and ~3 lines of lifecycle hook.
- The service has no dependency on the hook system (it does not import `register_hook`).
- The hook (`_hook_note_pre_route_user_msg`) is extremely thin: it delegates to `orc.stats_collector.note_user_msg(...)`.
- This is the same split pattern already proven for `ConversationCompressor`, `ContextPackService`/`ContextPackDirectiveEngine`, and `ChangeJournal`.

---

## Current Runtime Shape

`core/engines/stats_collector.py` (593 lines) contains:

1. **Module constants** — `_PENDING_SEARCH_ATTR`, `_PENDING_SEARCH_OWNER_ATTR`, `_STARTUP_CHECKED_PATHS`
2. **Module helpers** — `_utc_now_iso()`, `_duration_ms()`, `_safe_float()`, `_percentile()`, `_upper_control_bound()`, `_phase_bucket()`
3. **`TurnStatsState` dataclass** (lines 75–137) — mutable state carrier with `finalize()` and `to_record()`
4. **`StatsCollector` class** (lines 139–588) — direct-call service with file I/O and math
5. **`_hook_note_pre_route_user_msg`** (lines 590–593) — `@register_hook("on_pre_route")`

The class owns two append-only files:
- `stats.jsonl` (default `data/stats.jsonl`) — JSONL append, pruned on write to `history_limit` (default 500)
- `alerts.log` (default `data/debug/stats_alerts.log`) — free-text alert lines

---

## Caller Map

### Production callers / imports

| Caller | Import / Usage | Direct or Hook | Risk |
|---|---|---|---|
| `core/orchestrator.py` | `from core.services.stats_collector import StatsCollector`; `self.stats_collector = StatsCollector(CFG.STATS_PATH, CFG.STATS_ALERTS_PATH)` | Direct (instantiation) | Medium — orchestrator holds the reference |
| `core/orchestrator.py` | `self.stats_collector.startup_check_once()` | Direct | Low |
| `core/orchestrator.py` | `self.stats_collector.resume_or_start_turn(...)` | Direct | Low |
| `core/orchestrator.py` | `self.stats_collector.record_turn(self.turn_stats)` / `record_aborted_turn(...)` | Direct | Low |
| `core/executor.py` | `self.stats_collector = stats_collector` (injected); `note_constraint_violation(...)` | Direct | Low |
| `core/orchestrator_phases.py` | `orc.stats_collector.*` (numerous calls: `start_phase`, `end_phase`, `note_route`, `note_reporter_query`, `add_stage`, `finalize_outcome`, `defer_search_turn`, `build_dashboard_snapshot`) | Direct | Low — read/write through orchestrator |
| `core/prompt_context.py` | `@register_hook("on_pre_route")` (separate hook, unrelated to stats collector) | Hook | Low — different hook owner |

### Test / script callers / imports

| Caller | Import / Usage |
|---|---|
| `scripts/stats_collector_smoke_test.py` | `from core.services.stats_collector import StatsCollector, TurnStatsState`; full smoke test |
| `scripts/executor_budget_smoke_test.py` | `from core.services.stats_collector import StatsCollector, TurnStatsState`; budget/timeout smoke test |
| `scripts/planner_schema_compliance_smoke_test.py` | `from core.services.stats_collector import StatsCollector`; `note_constraint_violation` test |
| `scripts/search_error_contract_smoke_test.py` | `DummyStatsCollector` stub (no real import) |
| `scripts/search_thread_cleanup_smoke_test.py` | `DummyStatsCollector` stub (no real import) |
| `scripts/stage_pause_snapshot_smoke_test.py` | `DummyStatsCollector` stub (no real import) |
| `scripts/voice_identity_name_smoke_test.py` | `_FakeStatsCollector` stub (no real import) |

### No direct `_hook_note_pre_route_user_msg` callers

Grep confirms `_hook_note_pre_route_user_msg` is **only** referenced inside `core/engines/stats_collector.py` (definition). It is fired indirectly via `fire_hooks("on_pre_route", ...)` in `core/orchestrator_phases.py`. `StatsCollector` and `TurnStatsState` are no longer defined in `core/engines/stats_collector.py`.

### No frontend/bridge direct consumers

`web_ui/bridge/` uses generic event names (`status_widget_dashboard_activity`, `stats_view_refresh`) via `adapter.py` and `message_schema.py`. There are no direct imports of `StatsCollector`, `TurnStatsState`, or `build_dashboard_snapshot` in the frontend codebase. The dashboard data is pushed through the bridge as generic UI events, not as typed stats objects.

---

## Direct-Call Service Behavior

| Method / Helper | Side effects | Dependencies | Move candidate? | Notes |
|---|---|---|---|---|
| `TurnStatsState` dataclass | None (mutable state carrier) | `time.perf_counter`, `_utc_now_iso` | **Yes** | Pure dataclass |
| `TurnStatsState.finalize()` | None (computes `phase_ms["total"]`) | `_duration_ms` | **Yes** | |
| `TurnStatsState.to_record()` | None | `finalize()` | **Yes** | Returns JSON-serializable dict |
| `StatsCollector.__init__` | None | `Path` | **Yes** | Pure init with clamps |
| `startup_check_once()` | File read + possible alert write | `_STARTUP_CHECKED_PATHS` (global set) | **Yes** | Deduplicated by resolved path |
| `resume_or_start_turn()` | None (reads cancel_token/fallback_owner attrs) | `_PENDING_SEARCH_ATTR` / `_PENDING_SEARCH_OWNER_ATTR` | **Yes** | Returns `TurnStatsState` |
| `defer_search_turn()` | None (sets attrs on cancel_token/fallback_owner) | Same attr constants | **Yes** | Stores deferred state |
| `start_phase()` | None (mutates state) | `time.perf_counter` | **Yes** | |
| `end_phase()` | None (mutates state) | `time.perf_counter`, `_duration_ms` | **Yes** | Returns elapsed ms |
| `note_user_msg()` | None (mutates state) | Pure string assign | **Yes** | |
| `note_route()` | None (mutates state) | Pure string assign | **Yes** | |
| `note_reporter_query()` | None (mutates state) | Pure string assign | **Yes** | |
| `note_router_reroute()` | None (mutates state) | Bool toggle | **Yes** | |
| `note_constraint_violation()` | File append | `_utc_now_iso`, `ensure_parent` | **Yes** | Writes to `alerts_path` |
| `note_persona_error()` | None (mutates state) | Pure string assign | **Yes** | |
| `note_tts_metrics()` | None (mutates state) | Sum of `tts_ms` values | **Yes** | |
| `add_stage()` | None (mutates state) | Pure dict append | **Yes** | Accumulates planner/executor ms |
| `finalize_outcome()` | None (mutates state) | `_infer_outcome` | **Yes** | |
| `record_turn()` | File append + possible prune + possible alert | `append_jsonl`, `prune_jsonl_tail`, `_check_latest_record` | **Yes** | |
| `record_aborted_turn()` | File append | Delegates to `record_turn` | **Yes** | |
| `load_records()` | File read | `json.loads` | **Yes** | Graceful on missing/corrupt |
| `load_alert_lines()` | File read | `Path.read_text` | **Yes** | Graceful on missing |
| `build_readonly_report()` | File read | `load_records`, `load_alert_lines` | **Yes** | Returns plain text |
| `build_dashboard_snapshot()` | File read + math | `load_records`, `load_alert_lines`, `_upper_control_bound` | **Yes** | Returns dict for UI |
| `_infer_outcome()` | None | Pure state inspection | **Yes** | Staticmethod-like logic |
| `_check_latest_record()` | File read + possible alert append | `_field_values`, `_upper_control_bound`, `_append_alert` | **Yes** | Outlier detection |
| `_append_alert()` | File append | `ensure_parent` | **Yes** | |
| `_build_readonly_report_from_records()` | None | Pure text formatting | **Yes** | |
| `_record_field_value()` | None | `_phase_bucket` | **Yes** | |
| `_short_turn_label()` | None | Pure string slicing | **Yes** | |
| `_field_values()` | None | `_phase_bucket`, `_safe_float` | **Yes** | |
| `_utc_now_iso()` | None | `datetime.now(timezone.utc)` | **Yes** | Free function |
| `_duration_ms()` | None | `time.perf_counter` | **Yes** | Free function |
| `_safe_float()` | None | `float()` | **Yes** | Free function |
| `_percentile()` | None | `math.floor`, `math.ceil` | **Yes** | Free function |
| `_upper_control_bound()` | None | `math.sqrt` | **Yes** | Free function |
| `_phase_bucket()` | None | Pure dict traversal | **Yes** | Free function |
| `_PENDING_SEARCH_ATTR` | None | String constant | **Yes** | Module constant |
| `_PENDING_SEARCH_OWNER_ATTR` | None | String constant | **Yes** | Module constant |
| `_STARTUP_CHECKED_PATHS` | Mutable global set | `set()` | **Yes** | Module mutable state (path dedup) |

**All direct-call behavior is safe to move.** `StatsCollector` and `TurnStatsState` have no dependency on `register_hook`, `_TAIL_BLOCK_REGISTRY`, or any other engine lifecycle system.

---

## Lifecycle / Hook Behavior

| Hook | Trigger | What it mutates | Must stay in engines? | Notes |
|---|---|---|---|---|
| `_hook_note_pre_route_user_msg` | `fire_hooks("on_pre_route", orc, ...)` | `orc.turn_stats.user_msg` via `orc.stats_collector.note_user_msg` | **Yes** | Self-registers via `@register_hook`. Extremely thin wrapper. |

The hook should remain in `core/engines/stats_collector.py` because it is lifecycle behavior. After the split, the hook does not need to import `StatsCollector`; it delegates to `orc.stats_collector.note_user_msg(...)`.

---

## File I/O and Safety Behavior

### Stats JSONL append behavior
- `record_turn()` calls `append_jsonl(self.stats_path, record)` — atomic append.
- `record_turn()` also calls `prune_jsonl_tail(self.stats_path, max_lines=self.history_limit)` — write-path pruning.
- **No unbounded growth** — pruned on every write.

### Alert file writing
- `note_constraint_violation()` and `_append_alert()` both append lines to `alerts_path`.
- `ensure_parent(self.alerts_path)` is called before every open.
- No pruning on alerts file (uncertain whether this is intentional — mark as uncertain).

### Startup check behavior
- `startup_check_once()` uses a global `_STARTUP_CHECKED_PATHS` set keyed by resolved path.
- Prevents duplicate startup checks when the same stats path is reused across restarts.
- Calls `_check_latest_record(reason="startup")` which may append an alert if the latest record is an outlier.

### Readonly report generation
- `build_readonly_report()` reads stats + alerts and produces a plain-text summary.
- Includes phase latency averages / p95, recent turn list, and alert list.
- Safe to call from UI layer.

### Dashboard snapshot generation
- `build_dashboard_snapshot()` reads stats + alerts and produces a structured dict.
- Includes graph window data (`turn_numbers`, `turn_labels`, `total_ms`, per-phase ms arrays).
- Includes outlier detection (`total_outlier_x`, `total_outlier_y`) using `_upper_control_bound`.
- Safe to call from UI layer.

### Deferred search turn state
- `defer_search_turn()` stores `TurnStatsState` on `cancel_token` or `fallback_owner` via setattr.
- `resume_or_start_turn()` retrieves and deletes the pending state from the same object.
- This is orchestrator state management, not engine lifecycle, but it uses string attr names stored as module constants.

---

## Existing Test Coverage

### Smoke tests (scripts/)

| File | Coverage |
|---|---|
| `scripts/stats_collector_smoke_test.py` | `record_turn` (7 records), `load_alert_lines`, `build_readonly_report`, `build_dashboard_snapshot`, outlier detection (persona_ms spike triggers alert), graph window count, dashboard outlier arrays |
| `scripts/executor_budget_smoke_test.py` | `TurnStatsState` creation, `add_stage` with timeout_hit / action_budget_hit, `record_turn` with ABORTED outcome, phase timing accumulation |
| `scripts/planner_schema_compliance_smoke_test.py` | `note_constraint_violation` appends correctly-formatted alert lines |

### Unit tests (tests/)

**None found.** No `tests/test_*.py` file imports `StatsCollector` or `TurnStatsState`.

---

## Missing Guard Tests Before Split

Before moving `StatsCollector` to `core/services/stats_collector.py`, the following guard tests should exist in `tests/`:

1. **`test_turn_stats_state_to_record_shape`** — verify `to_record()` output schema (turn_id, timestamp, user_msg, decision, phase_ms keys, llm_tokens, stages).
2. **`test_turn_stats_state_finalize_sets_total`** — `finalize()` computes total from `started_at_monotonic`.
3. **`test_start_phase_end_phase_accumulation`** — start/end pair accumulates elapsed ms into `phase_ms`.
4. **`test_record_turn_appends_jsonl_and_prunes_history`** — write 3 records with `history_limit=2`, assert file has 2 lines.
5. **`test_record_turn_returns_none_when_deferred`** — `record_deferred=True` → `None`.
6. **`test_record_aborted_turn_records_aborted`** — outcome is `ABORTED`, detail preserved.
7. **`test_load_records_handles_missing_file`** — returns `[]`.
8. **`test_load_records_handles_corrupt_lines`** — skips corrupt JSON lines.
9. **`test_load_records_respects_limit`** — limit=2 returns last 2 records.
10. **`test_load_alert_lines_handles_missing_file`** — returns `[]`.
11. **`test_load_alert_lines_respects_limit`** — limit=3 returns last 3 lines.
12. **`test_build_dashboard_snapshot_empty`** — no records → all arrays empty, `record_count=0`.
13. **`test_build_dashboard_snapshot_non_empty_shape`** — records present → correct array lengths, outlier detection.
14. **`test_alert_creation_when_latency_outlier_detected`** — persona_ms spike triggers alert with expected text format.
15. **`test_note_constraint_violation_appends_alert`** — alert line contains `constraint_violation` and stage_goal.
16. **`test_defer_search_turn_and_resume_or_start_turn`** — defer stores state on object, resume retrieves and clears it.
17. **`test_resume_or_start_turn_returns_new_state_when_no_pending`** — no pending → fresh `TurnStatsState`.
18. **`test_note_route_sets_decision_and_bypass`** — decision uppercased, bypass lowercased.
19. **`test_infer_outcome_from_stages`** — timeout_hit → TIMEOUT, verification VERIFIED → VERIFIED, etc.
20. **`test_startup_check_once_deduplicates_by_path`** — second call with same path is a no-op.

**Minimum viable guard set (if pruning):**
- `TurnStatsState.to_record` shape (1)
- `record_turn` append + prune + deferred behavior (4, 5, 6)
- `load_records` / `load_alert_lines` graceful handling (7, 8, 9, 10)
- `build_dashboard_snapshot` empty and non-empty (12, 13)
- Outlier alert creation (14)
- `defer_search_turn` / `resume_or_start_turn` (16, 17)
- `startup_check_once` dedup (20)

---

## Recommended Staging

### Stage 1 — Add guard tests ✅
- `tests/test_stats_collector.py` created with 21 unit tests.
- Kept green against `core/engines/stats_collector.py` (now `core/services/stats_collector.py`).

### Stage 2 — Move service code ✅
- `TurnStatsState` dataclass, `StatsCollector` class, module constants, and all free functions moved to `core/services/stats_collector.py`.
- `core/engines/stats_collector.py` reduced to `_hook_note_pre_route_user_msg` only.

### Stage 3 — Update imports and exports ✅
- Imports updated in `core/orchestrator.py`, `tests/test_stats_collector.py`, smoke scripts.
- `core/services/__init__.py` exports `StatsCollector` and `TurnStatsState`.

### Stage 4 — Update docs and exports
- Update `core/engines/__init__.py` if it re-exports symbols.
- Update `core/services/__init__.py` to export `StatsCollector` and `TurnStatsState`.
- Update `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` and `docs/specs/engine-directory-audit.md`.

### Stage 5 — Run full validation
```
python -m compileall app.py config.py core ui memory tools llm web_ui
python -m pytest tests/ -q
python -m pytest web_ui/bridge/ -q
python scripts/stats_collector_smoke_test.py --json
python scripts/executor_budget_smoke_test.py --json
python scripts/planner_schema_compliance_smoke_test.py --json
```

---

## Collision Notes With Frontend Branch

- This backend path **must not** touch `web_ui/`, `ui/`, `app.py`, `config.py`, or any frontend bridge/startup files.
- Stats dashboard consumers may exist in frontend code, but they consume data through generic bridge events (`status_widget_dashboard_activity`, `stats_view_refresh`) rather than direct `StatsCollector` imports. The frontend does not import `StatsCollector`, `TurnStatsState`, or `build_dashboard_snapshot`.
- The frontend branch **must not** touch `core/engines/stats_collector.py`, `core/services/stats_collector.py` (future), or architecture docs for stats-collector split readiness.
- `core/orchestrator.py`, `core/executor.py`, and `core/orchestrator_phases.py` are shared backend wiring; coordinate if the frontend branch also touches them.
