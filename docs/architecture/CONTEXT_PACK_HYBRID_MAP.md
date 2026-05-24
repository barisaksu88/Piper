# ContextPack Hybrid Module Map

**Status:** Audit map — no refactor committed  
**Scope:** `core/engines/context_pack.py`  
**Branch:** `audit/context-pack-hybrid-map`

---

## 1. Summary

`core/engines/context_pack.py` is a **Hybrid** module per `ENGINE_UTILITY_CLASSIFICATION.md`.
It simultaneously owns:

- **Registry behavior:** tail-block registry + `on_turn_end` hook
- **Direct-call service behavior:** persona pack building, runtime context building, rendering

This file is **not split yet** because the tail-block builders, the engine, and the renderer share the same `TailBlockContext` dataclass and the same contract types (`PersonaContextPack`, `PersonaRuntimePack`, `PersonaDirectivePack`, `RuntimeContextPack`).

---

## 2. Registry Behavior Owned Here

### 2.1 Tail-Block Registry

```python
_TAIL_BLOCK_REGISTRY: list[TailBlockBuilder] = []

def register_tail_block(fn: TailBlockBuilder) -> TailBlockBuilder:
    _TAIL_BLOCK_REGISTRY.append(fn)
    return fn
```

- **Global mutable registry** populated at import time by `@register_tail_block` decorators.
- **Order matters:** blocks are appended in decorator-evaluation order.
- `ContextPackEngine.build_persona_directive_pack()` iterates `_TAIL_BLOCK_REGISTRY` and calls every builder.

### 2.2 Tail-Block Builders (11 in this file)

| Builder | Condition | Injected Block |
|---|---|---|
| `_tail_block_no_mutation_rule` | Decision == CHAT and no outcome block | `[NO_MUTATION_RULE]` |
| `_tail_block_context_arbitration` | Always | `[CONTEXT_ARBITRATION_RULE]` with turn-type profile |
| `_tail_block_document_qa_rule` | `ingested_document_chat` | `[DOCUMENT_QA_RULE]` |
| `_tail_block_search_report_rule` | `reporter_just_ran` | `[SEARCH_REPORT_RULE]` |
| `_tail_block_explain_last_turn` | `system_notice.kind == "explain_last_turn"` | `[EXPLAIN_LAST_TURN]` |
| `_tail_block_active_skill` | Always (returns empty if no skill) | `[ACTIVE_SKILL]` |
| `_tail_block_verification_result` | Always (returns empty if no verdict) | `[VERIFICATION_RESULT]` |
| `_tail_block_file_work_report` | `needs_file_work_report_rule` | `[FILE_WORK_REPORT_RULE]` or `[PARTIAL_VERIFICATION_RULE]` |
| `_tail_block_failed_verification` | `verification_verdict == "FAILED"` | `[FAILED_VERIFICATION_RULE]` |
| `_tail_block_failed_outcome_no_verification` | `outcome_failed` but no typed verdict | `[FAILED_OUTCOME_RULE]` |
| `_tail_block_workspace_boundary` | `needs_file_work_report_rule` | `[WORKSPACE_BOUNDARY_RULE]` |

**External tail-block builders that also register into this registry:**
- `core/engines/proactive_monitor.py` — `_tail_block_proactive_trigger`, `_tail_block_reminder_set_result`

### 2.3 Hook

```python
@register_hook("on_turn_end")
def _hook_upsert_runtime_context(orc, *, reporter_just_ran: bool = False) -> None:
```

- **Fired by:** `core/orchestrator_phases.py` line 528 via `fire_hooks("on_turn_end", orc, ...)`
- **Also called directly** by `core/orchestrator_phases.py` line 1824 (stage-completion path that bypasses the generic hook fire)
- **Side effect:** Upserts a hidden system message with `[LATEST_RUNTIME_CONTEXT]` into `orc.chat`

---

## 3. Direct-Call Service Behavior Owned Here

### 3.1 `ContextPackRenderer` (dataclass, frozen)

| Method | Callers |
|---|---|
| `to_prompt_context(pack: PersonaContextPack) -> PromptContext` | `ContextPackEngine.to_prompt_context()`; `PromptContextService.to_prompt_context()`; `orchestrator_phases.py` |
| `render_runtime_context_message(pack: RuntimeContextPack) -> str` | `ContextPackEngine.render_runtime_context_message()`; `PromptContextService.render_runtime_context_message()`; `PromptContextService.build_runtime_context_message()`; `_hook_upsert_runtime_context`; `search_error_contract_smoke_test.py` |

### 3.2 `ContextPackEngine` (dataclass)

| Method | Callers | Notes |
|---|---|---|
| `build_persona_pack(...)` | `PromptContextService.build_persona_pack()`; `orchestrator_phases.py` (phase_persona, phase_search_preview) | Loads instructions, brain hits, document hits, knowledge, env block. **Mutates nothing.** |
| `apply_document_focus(pack, ...)` | `PromptContextService.apply_document_focus()`; `orchestrator_phases.py` | Returns replaced pack with focus fields. |
| `clear_memory_for_file_work(pack)` | `PromptContextService.clear_memory_for_file_work()`; `orchestrator_phases.py` | Returns replaced pack with empty brain/document hits. |
| `apply_context_arbitration(pack, ...)` | `PromptContextService.apply_context_arbitration()`; `orchestrator_phases.py` | Suppresses pack fields based on `PERSONA_CONTEXT_ARBITRATION_TABLE`. |
| `to_prompt_context(pack)` | `PromptContextService.to_prompt_context()` | Delegates to `self.renderer.to_prompt_context()`. |
| `build_runtime_context_pack(orc, ...)` | `PromptContextService.build_runtime_context_pack()` | Reads `orc.route_decision`, `orc.scratchpad`, `orc.latest_search_query`, etc. |
| `render_runtime_context_message(pack)` | `PromptContextService.render_runtime_context_message()` | Delegates to `self.renderer.render_runtime_context_message()`. |
| `build_runtime_context_message(orc, ...)` | `PromptContextService.build_runtime_context_message()` | Convenience: `build_runtime_context_pack` → `render_runtime_context_message`. |
| `build_persona_runtime_pack(scratchpad, ...)` | `PromptContextService.build_persona_runtime_pack()`; `orchestrator_phases.py` | Calls `SummaryEngine` 6× to extract scratchpad evidence. |
| `build_persona_directive_pack(...)` | `PromptContextService.build_persona_directive_pack()`; `orchestrator_phases.py` | Iterates `_TAIL_BLOCK_REGISTRY`. |

### 3.3 Free functions

| Function | Callers | Notes |
|---|---|---|
| `resolve_persona_turn_type(...)` | `apply_context_arbitration()`; `_tail_block_context_arbitration()` | Pure logic, no side effects. |
| `render_context_arbitration_block(turn_type)` | `resolve_persona_turn_type()` caller | Returns arbitration rule text. |
| `register_tail_block(fn)` | Decorator on 11 functions in this file + 2 in `proactive_monitor.py` | Registry side effect at import time. |

---

## 4. PromptContextService Wrappers

`core/prompt_context.py` owns `PromptContextService`, which **wraps** `ContextPackEngine`.
Every `ContextPackEngine` public method is re-exported through `PromptContextService` with the same signature.

The orchestrator calls `orc.prompt_context.*`, not `ContextPackEngine` directly.
This means any split of `context_pack.py` must also update `prompt_context.py` or keep the wrapper layer stable.

---

## 5. What Is True Engine vs Utility/Service

### True Engine Behavior (must stay orchestrated)
- `build_persona_pack` — reads live brain, documents, knowledge, environment
- `build_runtime_context_pack` — reads `orc` state (route decision, scratchpad, search state)
- `build_persona_runtime_pack` — reads scratchpad + typed `VerificationResult`
- `_hook_upsert_runtime_context` — mutates chat history

### Service/Renderer Behavior (could separate later)
- `ContextPackRenderer` — pure mapping from dataclass → string / `PromptContext`
- `render_context_arbitration_block` — pure text generation from turn type
- `resolve_persona_turn_type` — pure logic

### Registry Behavior (could separate later)
- `_TAIL_BLOCK_REGISTRY` + `register_tail_block()`
- All `@register_tail_block` functions
- `_hook_upsert_runtime_context` (the hook body is engine-like, but the *registration* is registry behavior)

---

## 6. What Must NOT Move Yet

1. **Tail-block registry cannot move** without also moving every `@register_tail_block` decorator in `proactive_monitor.py` and any future modules.
2. **`build_persona_directive_pack` cannot move** without the registry, because it iterates `_TAIL_BLOCK_REGISTRY`.
3. **`build_persona_runtime_pack` cannot move** without `SummaryEngine` being co-importable, which it already is — but the orchestrator calls it through `PromptContextService`.
4. **`_hook_upsert_runtime_context` cannot move** without updating `orchestrator_phases.py` import and the direct-call site at line 1824.
5. **`ContextPackRenderer` should not move** until we have snapshot tests proving identical output.

---

## 7. Biggest Risks If We Split Too Early

| Risk | Why |
|---|---|
| **Tail-block ordering drift** | If registry moves to a new module, import order may change. Tail blocks are order-sensitive (e.g. `[NO_MUTATION_RULE]` should come before `[CONTEXT_ARBITRATION_RULE]`). |
| **`TailBlockContext` contract breakage** | 13 builder functions + `build_persona_directive_pack` all depend on the same dataclass. Splitting without freezing the contract first risks silent signature mismatches. |
| **Renderer output drift** | `render_runtime_context_message` builds a string that the persona sees as authoritative system context. Any whitespace or ordering change is a behavior change. |
| **PromptContextService wrapper churn** | Every direct-call method is wrapped by `PromptContextService`. A split would require updating or removing those wrappers. |
| **Hook import path breakage** | `orchestrator_phases.py` imports `_hook_upsert_runtime_context` directly (line 17) and calls it directly (line 1824). Moving the function breaks both. |

---

## 8. Recommended Next Step

**Do not split `context_pack.py` yet.**

First, create snapshot tests that capture the rendered output of:

1. `ContextPackRenderer.render_runtime_context_message()` — for each `RuntimeContextPack` permutation (TASK, SEARCH, reporter, empty)
2. `ContextPackEngine.build_persona_directive_pack()` — for each turn type + verification verdict combination
3. `ContextPackEngine.build_persona_pack()` — with/without knowledge, documents, brain hits

Only after these snapshots exist and are green on `main` should we consider separating:

- **A)** `_TAIL_BLOCK_REGISTRY` + `register_tail_block()` + all builders → `core/engines/tail_block_registry.py`
- **B)** `ContextPackEngine` + `PromptContextService` wrappers → `core/engines/context_pack.py` (or merge into service)
- **C)** `ContextPackRenderer` → `core/engines/context_pack_renderer.py`

Until then, this file stays Hybrid and unchanged.
