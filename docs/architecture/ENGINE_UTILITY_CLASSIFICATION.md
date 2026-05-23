# Engine / Utility Classification for `core/engines/`

Status: Active · Audit date 2026-05-23  
Authority: live code in `core/engines/` and direct call sites in `core/orchestrator.py`, `core/orchestrator_phases.py`, `core/executor.py`, `ui/controller.py`, `core/prompt_context.py`.

---

## 1. Methodology

Classification is based on **runtime integration behavior**, not directory name or module docstring.

| Category | Definition |
|----------|------------|
| **Pure registry-driven engine** | All feature behavior integrates through registries/hooks/interceptors/tail-blocks. No orchestrator/executor/prompt/UI code imports the module for direct feature calls. The module only needs to be imported once (e.g. in `__init__.py`) so decorators execute. |
| **Hybrid module** | Contains **both** registry-driven behavior (hooks, interceptors, tail blocks) **and** direct-call/service behavior that is imported and invoked explicitly by orchestrator/executor/prompt/UI layers. |
| **Direct-call utility** | No registry registration found. Imported and called directly by orchestrator/executor/prompt/UI code. May live under `core/engines/` for historical reasons. |

**Important:** A module that registers one hook but is also directly instantiated and called by orchestrator code is **hybrid**, not pure. The registry behavior is real, but the direct-call behavior is equally real and must be documented.

---

## 2. Classification Table — Every File in `core/engines/`

| File | Classification | Registry Evidence | Direct-Call Evidence | Notes |
|------|---------------|-------------------|----------------------|-------|
| `proactive_monitor.py` | **Hybrid** | `@register_route_interceptor`, `@register_tail_block`, `@register_hook("on_turn_end")` | `ui/controller.py` directly instantiates `ProactiveMonitor` and calls `.start()` / `.stop()` for background lifecycle. `core/orchestrator_phases.py` directly imports `parse_reminder_request`, `ReminderStore`, `display_fire_at_local`, `parse_proactive_trigger_message`. | Reminder route/persona behavior is registry-driven, but UI owns the monitor thread lifecycle directly. |
| `conversation_compressor.py` | **Hybrid** | `@register_hook("on_turn_end")` (`_hook_deferred_conversation_summary`) | `core/orchestrator.py` directly owns `ConversationCompressor` as `orc.conversation_compressor` and calls `.load_summary()`, `.save_summary()`. `orchestrator_phases.py` accesses it via `orc.conversation_compressor`. | Hook fires deferred summarization, but load/save/update summary behavior is direct-call. |
| `context_pack.py` | **Hybrid** | `@register_tail_block` (many), `@register_hook("on_turn_end")` (`_hook_upsert_runtime_context`) | `core/prompt_context.py` directly imports and calls `ContextPackEngine` methods. `core/orchestrator_phases.py` directly imports `_hook_upsert_runtime_context`. | Tail-block registry and turn-end hook are registry-driven, but persona pack assembly and runtime context building are direct-call services. |
| `change_journal.py` | **Hybrid** | `@register_hook("on_task_verified")` (`_hook_record_change_journal`) | `core/orchestrator.py` directly owns `ChangeJournal` as `orc.change_journal`. `core/executor.py` directly imports `ChangeJournal` and calls `.prepare_file_op_capture()`, `.finalize_file_op_capture()`, `.record_turn()`. | Journal writing is hook-driven, but snapshot helpers and direct ownership are direct-call. |
| `stats_collector.py` | **Hybrid** | `@register_hook("on_pre_route")` (`_hook_note_pre_route_user_msg`) | `core/orchestrator.py` directly owns `StatsCollector` as `orc.stats_collector` and calls `.startup_check_once()`, `.resume_or_start_turn()`, `.record_turn()`. `ui/controller.py` directly owns a second `StatsCollector` and calls `.build_dashboard_snapshot()`. | Pre-route hook records user message, but phase timing capture, dashboard building, and direct ownership are direct-call. |
| `file_work.py` | **Utility** | None | `core/executor.py`, `core/file_stage_policy.py`, `core/routing/route_normalizer.py` directly import and call `FileWorkEngine` static/class methods. | Centralized file-work evidence handling. No registry integration. |
| `followup_resolution.py` | **Utility** | None | `core/orchestrator_phases.py` directly instantiates `FollowupResolutionEngine` and calls `.refine_with_llm()`. | Route-level follow-up resolution. No registry integration. |
| `route_clarity.py` | **Utility** | None | `core/orchestrator_phases.py` directly instantiates `RouteClarifier` and calls `.refine_with_llm()`. | Ambiguous task route refinement. No registry integration. |
| `state_mutation.py` | **Utility** | None | `core/orchestrator_phases.py` directly instantiates `StateMutationEngine`. `core/routing/route_normalizer.py` directly imports and calls `StateMutationEngine.normalize_route_decision()`. | Task/event/knowledge mutation intent parsing. No registry integration. |
| `summary.py` | **Utility** | None | `core/orchestrator_phases.py` directly calls `SummaryEngine` static methods. `core/engines/context_pack.py` directly calls `SummaryEngine` methods. | Scratchpad extraction and outcome block construction. No registry integration. |
| `verification.py` | **Utility** | None | `core/executor.py` and `core/orchestrator_phases.py` directly instantiate and call `VerificationEngine`. | Stage success verification. No registry integration. |
| `computer_use_engine.py` | **Utility** | None | `core/executor.py` and `tools/` directly instantiate and call `ComputerUseEngine`. | Browser automation engine. No registry integration. |
| `computer_use_verifier.py` | **Utility** | None | `core/executor.py` and `core/engines/verification.py` directly call `computer_use_verifier` functions. | Browser stage verification helpers. No registry integration. |
| `rollback_engine.py` | **Utility** | None | `core/executor.py` and `core/orchestrator_phases.py` directly call `record_manifest()` and `invert_manifest()`. | Bulk mutation rollback manifests. No registry integration. |
| `__init__.py` | Package surface | N/A | Re-exports `ConversationCompressor`, `ContextPackEngine`, `FileWorkEngine`, `FollowupResolutionEngine`, `RouteClarifier`, `StateMutationEngine`, `SummaryEngine`, `VerificationEngine`. | Does not contain behavior. |

---

## 3. Pure / Cleanest Registry-Driven Feature Behavior

**There is no pure engine under `core/engines/` today.**

Every module that registers hooks, interceptors, or tail blocks is also directly imported and called by orchestrator/executor/prompt/UI code. The cleanest registry-driven behavior exists in:

- `proactive_monitor.py` — reminder-set interceptor and proactive-trigger tail blocks are fully registry-driven.
- `conversation_compressor.py` — deferred summary hook is fully registry-driven.
- `context_pack.py` — tail block registry is the primary integration path for persona directives.
- `change_journal.py` — `on_task_verified` hook is the primary write path.
- `stats_collector.py` — `on_pre_route` hook is the primary message capture path.

But **all five** require direct-call companions for lifecycle, ownership, or service behavior.

---

## 4. Hybrid Modules (Detailed)

### 4.1 `proactive_monitor.py`
- **Registry-driven:** `_registered_reminder_set_interceptor` (pre-route), `_tail_block_proactive_trigger`, `_tail_block_reminder_set_result`, `_hook_finalize_proactive_trigger` (turn-end).
- **Direct-call:** `ui/controller.py` instantiates `ProactiveMonitor` directly, passes lambdas for `can_dispatch`, `is_inflight`, `dispatch_callback`, and calls `.start()` / `.stop()` in the UI event loop. `orchestrator_phases.py` directly imports `parse_reminder_request`, `ReminderStore`, etc.
- **Verdict:** Hybrid. The UI owns the background thread lifecycle; the registry owns the routing and persona behavior.

### 4.2 `conversation_compressor.py`
- **Registry-driven:** `_hook_deferred_conversation_summary` (turn-end).
- **Direct-call:** `core/orchestrator.py` owns `ConversationCompressor` as an instance attribute and calls `.load_summary()`, `.save_summary()`, `.compress_history()` directly.
- **Verdict:** Hybrid. Orchestrator directly manages summary persistence; the hook only fires the deferred LLM summarization.

### 4.3 `context_pack.py`
- **Registry-driven:** Multiple `@register_tail_block` builders (`_tail_block_no_mutation_rule`, `_tail_block_context_arbitration`, `_tail_block_document_qa_rule`, `_tail_block_search_report_rule`, `_tail_block_explain_last_turn`, `_tail_block_active_skill`, `_tail_block_verification_result`, `_tail_block_file_work_report`, `_tail_block_failed_verification`, `_tail_block_failed_outcome_no_verification`, `_tail_block_workspace_boundary`) and `_hook_upsert_runtime_context` (turn-end).
- **Direct-call:** `core/prompt_context.py` directly constructs `ContextPackEngine` and calls `.build_persona_pack()`, `.build_persona_directive_pack()`, `.build_runtime_context_pack()`, `.build_persona_runtime_pack()`, `.to_prompt_context()`, etc.
- **Verdict:** Hybrid. Tail blocks and turn-end hook are registry-driven, but the core persona context assembly service is direct-call.

### 4.4 `change_journal.py`
- **Registry-driven:** `_hook_record_change_journal` (on_task_verified).
- **Direct-call:** `core/orchestrator.py` owns `ChangeJournal` as `orc.change_journal`. `core/executor.py` directly imports `ChangeJournal` and calls `.prepare_file_op_capture()`, `.finalize_file_op_capture()`, `.record_turn()`. `orchestrator_phases.py` calls `.undo_latest()` and `.mark_entry_undone()` directly in `phase_undo`.
- **Verdict:** Hybrid. Hook records the turn, but snapshot preparation, execution-time capture, and undo are direct-call.

### 4.5 `stats_collector.py`
- **Registry-driven:** `_hook_note_pre_route_user_msg` (on_pre_route).
- **Direct-call:** `core/orchestrator.py` owns `StatsCollector` and calls `.startup_check_once()`, `.resume_or_start_turn()`, `.record_turn()`, `.finalize_outcome()`, `.add_stage()`, etc. `ui/controller.py` owns a separate `StatsCollector` and calls `.build_dashboard_snapshot()` for the stats tab.
- **Verdict:** Hybrid. Hook captures the user message, but all timing, stage aggregation, dashboard rendering, and direct ownership are direct-call.

---

## 5. Direct-Call Utilities Currently Living Under `core/engines/`

These modules contain no hook/interceptor/tail-block registration. They are imported and called directly by orchestrator/executor/prompt layers.

| File | Primary Callers | Responsibility |
|------|----------------|--------------|
| `file_work.py` | `executor.py`, `file_stage_policy.py`, `route_normalizer.py` | File/code evidence handling, stage classification, blocked-write guards, recovery hints. |
| `followup_resolution.py` | `orchestrator_phases.py` | Follow-up route resolution (pronouns, confirmations, browser context). |
| `route_clarity.py` | `orchestrator_phases.py` | Ambiguous task route refinement / clarification. |
| `state_mutation.py` | `orchestrator_phases.py`, `route_normalizer.py` | Task/event/knowledge mutation intent parsing and route building. |
| `summary.py` | `orchestrator_phases.py`, `context_pack.py` | Scratchpad extraction, outcome blocks, runtime notes. |
| `verification.py` | `executor.py`, `orchestrator_phases.py` | Stage verification (RULES → LLM → STATE_CHECK). |
| `computer_use_engine.py` | `executor.py`, `tools/` | Browser automation (Playwright + local HTML parser). |
| `computer_use_verifier.py` | `executor.py`, `verification.py` | Browser stage evidence evaluation. |
| `rollback_engine.py` | `executor.py`, `orchestrator_phases.py` | Bulk mutation rollback manifest write/replay. |

---

## 6. Refactor Notes

### 6.1 No file is a "pure engine" today
The `core/engines/` directory contains **zero** modules that integrate exclusively through registries. All five registry-registering modules are hybrid. This is not a bug; it is the live architecture. Any cleanup pass must start from this truth.

### 6.2 `core/engines/` is a mixed bag
The directory name implies a uniform "engine" population, but the actual population is:
- 5 hybrid modules
- 9 direct-call utilities
- 1 package init

This mismatch creates onboarding friction. The directory either needs a name that reflects the mix, or the utilities need a new home.

### 6.3 Direct-call utilities are stable and valuable
`FileWorkEngine`, `SummaryEngine`, `VerificationEngine`, `StateMutationEngine`, `FollowupResolutionEngine`, `RouteClarifier`, `ComputerUseEngine`, `ComputerUseVerifier`, and `RollbackEngine` are all actively used, well-factored, and have no registry integration. Moving them for purity's sake would be churn without behavioral gain.

### 6.4 Hybrid modules could be split
If the long-term goal is a pure registry-driven `core/engines/`, the hybrid modules are candidates for split:
- Registry behavior stays in `core/engines/` (or moves to `core/features/`).
- Direct-call service behavior moves to `core/services/` or `core/runtime_services/`.

But splitting a module just to satisfy taxonomy is **not recommended** until there is a concrete runtime benefit (e.g. faster startup, clearer dependency graph, or test isolation).

---

## 7. Staging Plan

**Stage 0 — Documentation (this file)** ✅  
Document the live classification without moving any files.

**Stage 1 — Compatibility shims**  
If any utility is renamed or relocated, leave a re-export shim in `core/engines/` with a `DeprecationWarning` so existing imports continue to work. Remove shims only after a full release cycle.

**Stage 2 — Optional relocation (future)**  
If a `core/services/` or `core/runtime_services/` directory is created for non-registry services, migrate utilities there **only** when:
- The move is paired with a real architectural benefit (not just taxonomy).
- All call sites are updated in the same commit.
- Compatibility shims are provided.
- The legacy loop has been proven stable with LangGraph burn-in (see §8).

**Stage 3 — Optional split of hybrid modules (future)**  
Split registry behavior from direct-call behavior in the five hybrid modules **only** if:
- A new hook/interceptor system makes the split materially cleaner.
- The split reduces cross-module coupling (measured by import graph).
- Tests pass without regression.

---

## 8. Compatibility-Shim Rule

Any relocation or rename of a module under `core/engines/` must provide a backward-compatible shim:

```python
# core/engines/<old_name>.py (shim)
import warnings
warnings.warn(
    "core.engines.<old_name> is deprecated; use <new_path> instead",
    DeprecationWarning,
    stacklevel=2,
)
from <new_path> import *  # noqa: F401,F403
```

Shims must be kept for at least one full release cycle. Do not break imports in `core/orchestrator.py`, `core/executor.py`, `ui/controller.py`, or `core/prompt_context.py` without a shim.

---

## 9. Hard Rules

### 9.1 No mass moves
Do not relocate multiple `core/engines/` files in a single commit unless every import is updated and every shim is tested. Mass moves create merge conflicts and silent import failures.

### 9.2 Legacy loop stays until LangGraph burn-in is proven
The legacy `while`-loop runtime in `core/orchestrator_phases.py` must remain intact. Any engine classification or relocation work must not disrupt the legacy path. Removal of the legacy loop requires:
- LangGraph runtime passing the full smoke suite for N consecutive days.
- Explicit sign-off in `docs/ROADMAP.md`.
- A dedicated migration issue, not a side effect of directory cleanup.

### 9.3 Document live-code truth over wishful taxonomy
If the live code contradicts an ideal classification, the live code wins. This document is an audit, not a spec for a future rewrite.

---

## 10. Cross-References

- `docs/architecture/TRIGGER_FLOW.md` — Turn lifecycle, hook registry design, and the original "Engine vs. Utility" appendix.
- `docs/ROADMAP.md` — Contains a related cleanup note under "Engine directory audit and lifecycle cleanup".
- `AGENTS.md` — Architecture doctrine; consult before adding new registries or relocating modules.
