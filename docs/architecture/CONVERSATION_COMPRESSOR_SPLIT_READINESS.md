# ConversationCompressor Split Readiness Audit

**Status:** Split completed ✅  
**Service:** `core/services/conversation_compressor.py`  
**Hook:** `core/engines/conversation_compressor.py`  
**Date:** 2026-05-23

---

## 1. Behavior Classification

**Bucket:** Hybrid Module — cleanly splittable.

The original `core/engines/conversation_compressor.py` contained two distinct concerns. It has now been split:

1. **Pure utility class** (`ConversationCompressor`) → `core/services/conversation_compressor.py`
2. **Lifecycle hook** (`_hook_deferred_conversation_summary`) → stays in `core/engines/conversation_compressor.py`

---

## 2. Class Analysis — `ConversationCompressor`

### 2.1 State
- **No mutable instance state.** The only instance field is `token_budget` (set at `__init__` and never mutated).
- **No shared state.** All methods are deterministic given their inputs.
- **No external resource ownership.** No files kept open, no network sockets, no browser sessions.

### 2.2 Lifecycle
- **No `shutdown()` / `suspend()` / `__enter__` / `__exit__`.**
- **No lazy initialization or teardown.**
- **No threading primitives inside the class.** Threading is owned exclusively by the hook wrapper.

### 2.3 Dependencies
- `config.CFG` — read-only config access (`CONVERSATION_SUMMARY_MAX_TOKENS`).
- `llm` parameter — injected at call time, not stored.
- `pathlib.Path`, `json`, `re` — standard library only.

### 2.4 Public API

| Method | Type | Description |
|--------|------|-------------|
| `__init__(token_budget=400)` | instance | Sets budget. |
| `compress_history(history, existing_summary, max_turns, llm, cancel_token)` | instance | Main compression logic. Returns `ConversationCompressionResult`. |
| `load_summary(path)` | `@staticmethod` | Reads `data/conversation_summary.json`. |
| `save_summary(path, summary)` | `@staticmethod` | Writes `data/conversation_summary.json`. |
| `build_summary_message(summary)` | `@staticmethod` | Builds hidden system message with summary header. |

### 2.5 Internal Helpers
- `_summarize_candidate()` — LLM-backed summarization with fallback truncation.
- `_truncate_to_budget()` — deterministic token truncation.
- `_normalize_summary()` — strips markdown fences and header noise.
- `_sanitize_summary_text()` — removes low-value lines.
- `_token_count()` — regex-based word counting.
- `_summary_line_is_low_value()` — filters control/system noise.
- `_clean_messages()` — filters system/empty/UI messages.
- `_build_candidate_summary()` — merges existing summary + dropped transcript.
- `_render_transcript()` — formats messages as `Role: content` lines.

---

## 3. Hook Analysis — `_hook_deferred_conversation_summary`

### 3.1 Registration
```python
@register_hook("on_turn_end")
def _hook_deferred_conversation_summary(orc, *, reporter_just_ran: bool = False) -> None:
```

### 3.2 Responsibilities
1. **Guard clauses** — skip if `reporter_just_ran`, synthetic turn, or knowledge disabled.
2. **Read orchestrator state** — `orc.get_context()`, `orc.conversation_summary`, `orc.llm`, `orc.cancel_token`.
3. **Instantiate / reuse compressor** — `orc.conversation_compressor` (injected by `Orchestrator.__init__`).
4. **Spawn daemon thread** — calls `compressor.compress_history()` asynchronously.
5. **Write results back** — `orc.update_conversation_summary(result.summary)` + UI log.

### 3.3 Threading
- Creates a `threading.Thread(target=_run, daemon=True)`.
- This is **orchestration behavior**, not service behavior. The service itself is thread-safe (no mutable state).

---

## 4. Caller Map

### 4.1 Production Callers — `ConversationCompressor` class

| Caller | Import / Usage |
|--------|----------------|
| `core/orchestrator.py:133` | `self.conversation_compressor = ConversationCompressor()` |
| `core/orchestrator.py:187` | `self.conversation_compressor.load_summary(self.conversation_summary_path)` |
| `core/orchestrator.py:190` | `self.conversation_compressor.save_summary(...)` |
| `core/orchestrator_phases.py:2540` | `orc.conversation_compressor.compress_history(...)` |
| `_hook_deferred_conversation_summary` | `compressor.compress_history(...)` (via `orc.conversation_compressor`) |

### 4.2 Production Callers — Hook function

| Caller | Usage |
|--------|-------|
| `core/feature_hooks.py` | Discovers hook via `@register_hook("on_turn_end")` |
| `core/orchestrator_phases.py:528` | `fire_hooks("on_turn_end", orc, reporter_just_ran=...)` |

### 4.3 Test / Script Callers

| Caller | Usage |
|--------|-------|
| `scripts/conversation_compressor_smoke_test.py` | Imports `ConversationCompressor` directly; tests `compress_history`, `load_summary`, `save_summary` |

`tests/test_conversation_compressor.py` imports `ConversationCompressor` from `core.services.conversation_compressor` and covers the service behavior.

---

## 5. Import / Export Map

**Current exports (`core/engines/__init__.py`):**
- No `ConversationCompressor` export.

**Current exports (`core/services/conversation_compressor.py`):**
- `ConversationCompressor` (class)
- `ConversationCompressionResult` (frozen dataclass)

**Current exports (`core/engines/conversation_compressor.py`):**
- `_hook_deferred_conversation_summary` (hook function, module-private by convention)

---

## 6. Split Plan

The completed split is:

### 6.1 Moved to `core/services/conversation_compressor.py`
- `ConversationCompressor` class
- `ConversationCompressionResult` dataclass
- Module-level constants `_TOKEN_RE`, `_SUMMARY_HEADERS`

### 6.2 Kept in `core/engines/conversation_compressor.py`
- `_hook_deferred_conversation_summary` function
- Imports `ConversationCompressor` from `core.services.conversation_compressor`

### 6.3 Updated callers
- `core/orchestrator.py` — imports from `core.services.conversation_compressor`
- `core/orchestrator_phases.py` — no change (uses `orc.conversation_compressor`)
- `core/engines/__init__.py` — no longer exports `ConversationCompressor`
- `core/services/__init__.py` — exports `ConversationCompressor` and `ConversationCompressionResult`
- `tests/test_conversation_compressor.py` — imports from `core.services.conversation_compressor`
- `scripts/conversation_compressor_smoke_test.py` — imports from `core.services.conversation_compressor`

---

## 7. Test Coverage

### 7.1 Smoke Tests

`scripts/conversation_compressor_smoke_test.py` exercises `compress_history`, `load_summary`, and `save_summary`.

**Fixed:** The `_StubLLM.generate()` signature now accepts `max_tokens`. The smoke test correctly exercises the over-budget LLM path.

### 7.2 Pytest Unit Tests

`tests/test_conversation_compressor.py` — **58 tests** added, covering:
- `compress_history` (empty, under-budget, over-budget truncation, over-budget LLM, LLM failure fallback, existing summary merge, max_turns edge cases)
- `load_summary` / `save_summary` (missing file, malformed JSON, valid JSON, round-trip, parent directory creation, whitespace stripping)
- `build_summary_message` (empty and non-empty summaries)
- `_truncate_to_budget` (exact budget, over-budget, empty/whitespace input)
- `_normalize_summary` (markdown fences, header noise, newline collapsing)
- `_sanitize_summary_text` (low-value line removal, system/control filtering)
- `_clean_messages` (system, thinking, UI, runtime context, summary headers, empty, error, copied messages)
- Edge cases (None history, non-dict items, token_budget falsy values)

---

## 9. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Import path breakage in `orchestrator.py` | Low | Single import line change, well-grepped. |
| Smoke test import breakage | Low | Single import line change in `scripts/`. |
| Hook still references `orc.conversation_compressor` | Low | The hook already accesses the compressor through the orchestrator; the field type doesn't change. |
| No unit test coverage | Medium | **Resolved.** `tests/test_conversation_compressor.py` (58 tests) covers all public methods and edge cases. |
| `core/engines/__init__.py` no longer exports `ConversationCompressor` | Low | No external consumers of `core.engines.ConversationCompressor` outside the orchestrator. |

---

## 10. Recommended Next Step

### A) Safe to move whole module

Not applicable. This module registers `@register_hook("on_turn_end")`, so moving the entire file to `core/services/` would break the engine/service boundary.

### B) Safe only after adding tests

Partial fit. Tests are required, but the correct action is a **split**, not a whole-module move.

### C) Do not move

Not recommended. The `ConversationCompressor` class is a pure direct-call utility with no engine characteristics. Leaving it in `core/engines/` perpetuates the hybrid-module pattern.

### D) Split after tests are green — move only pure service pieces ✅ **Completed**

**Decision:**
- **Do not move the whole module.** ✅ Done.
- **Keep `_hook_deferred_conversation_summary`** in `core/engines/conversation_compressor.py` — it is engine lifecycle behavior. ✅ Done.
- **Move only the pure service pieces** to `core/services/conversation_compressor.py`: ✅ Done.
  - `ConversationCompressor` class
  - `ConversationCompressionResult` dataclass
  - Pure helper constants (`_TOKEN_RE`, `_SUMMARY_HEADERS`)
- **Tests were green before the split.** `tests/test_conversation_compressor.py` (58 tests) and the fixed smoke test both passed.

---

## 11. Precedent

This split follows the same pattern as other pure-utility relocations:

- `StateMutationEngine` → `core/services/state_mutation.py`
- `ComputerUseVerifier` functions → `core/services/computer_use_verifier.py`
- `VerificationEngine` → `core/services/verification.py`

The difference is that `conversation_compressor.py` also owns a hook, so only the class moves — the hook stays. This is the first **split** (as opposed to **full relocation**) in this migration series.
