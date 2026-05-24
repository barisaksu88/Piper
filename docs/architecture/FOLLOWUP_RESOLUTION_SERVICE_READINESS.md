# FollowupResolutionEngine Service Move Readiness Audit

**Status:** Audit complete — recommendation: add tests first  
**Branch:** `audit/followup-resolution-service-readiness`  
**Date:** 2026-05-24  
**Source:** `core/engines/followup_resolution.py`  
**Possible target:** `core/services/followup_resolution.py`

---

## 1. Behavior Classification

FollowupResolutionEngine is a **pure direct-call utility**.

- No hooks, registries, tail-blocks, interceptors, or lifecycle participation.
- No background threads or async loops.
- Carries minimal instance state (`state_mutation_engine` reference only).
- All routing logic is deterministic heuristics + one LLM call.

**Behaviorally identical to** `SearchWorkflowEngine`, `SummaryEngine`,
`VerificationEngine`, `FileWorkEngine`, and `RouteClarifier` — all already
relocated to `core/services/`.

---

## 2. Caller Map

### 2.1 Production code

| File | Usage |
|------|-------|
| `core/engines/__init__.py` | Package export |
| `core/orchestrator_phases.py` | Creates `FollowupResolutionEngine(state_mutation_engine=...)` singleton |

### 2.2 Test / smoke code

| File | Usage |
|------|-------|
| `scripts/followup_resolution_engine_smoke_test.py` | 14-scenario smoke test for `refine_with_llm` |

**No unit tests exist** in `scripts/test_engines.py` or `tests/`.

---

## 3. Runtime Responsibilities Owned by FollowupResolutionEngine

| Method | Responsibility | Risk if broken |
|--------|---------------|---------------|
| `should_resolve` | Deterministic gate: decides whether a user message is a follow-up that needs resolution | Mis-routing — follow-ups treated as new requests, or new requests treated as follow-ups |
| `refine_with_llm` | Orchestrates follow-up resolution via LLM + deterministic fallbacks | Wrong route decisions, wrong stage cards |
| `_build_state_payload` | Builds state snapshot for LLM context | LLM makes decisions based on stale/incomplete state |
| `_build_deterministic_fallback_route` | Fallback routing when LLM is unavailable | Wrong fallback action |
| `_should_prefer_fallback_route` | Decides whether to skip LLM and use fallback | Unnecessary LLM calls, or missed precision |
| `_should_resolve_runtime_context_followup` | Detects runtime-context-based follow-ups | Missed context-aware follow-ups |
| `_should_resolve_memory_recall_followup` | Detects memory recall follow-ups | Missed memory queries |
| `_should_resolve_event_detail_followup` | Detects event detail follow-ups | Missed calendar queries |
| `_looks_like_file_readback_followup` | Detects "read it back" follow-ups | Missed file readback requests |
| `_looks_like_dependency_override_followup` | Detects dependency override follow-ups | False override approvals |
| `_looks_like_dependency_file_clarification_followup` | Detects dependency file clarification | Missed file clarifications |
| `_looks_like_browser_context_followup` | Detects browser context follow-ups | Missed browser continuations |
| `_build_*_route` (many) | Builds specific route decisions for each follow-up type | Wrong stage cards, wrong tools |

---

## 4. Behavior That Must Not Change During Relocation

- `should_resolve` gate logic and ordering.
- All regex patterns (`_AMBIGUOUS_REFERENCE_RE`, `_CONTEXTUAL_REMEMBER_RE`, `_AMBIGUOUS_MEMORY_FOLLOWUP_RE`, `_FOLLOWUP_ACTION_RE`, `_READONLY_SHORT_RE`, `_TASK_WORD_RE`, `_EVENT_WORD_RE`, `_MEMORY_WORD_RE`, `_ACK_ONLY_RE`, `_AFFIRMATIVE_CONFIRM_RE`, `_OFFER_PHRASE_RE`, `_MEMORY_RECALL_OFFER_RE`, `_MEMORY_RECALL_COMMIT_RE`, `_NEGATIVE_OR_CANCEL_RE`, `_QUESTION_START_RE`, `_VERIFICATION_QUESTION_RE`, `_EVENT_DETAIL_HINT_RE`, `_THINKING_RE`, `_EXPLICIT_DEPENDENCY_OVERRIDE_RE`, `_ACTIVE_DEPENDENCY_RUNTIME_RE`, `_FILE_READBACK_FOLLOWUP_RE`, `_DEPENDENCY_FILE_CLARIFICATION_RE`).
- `_MATCH_SKIP_WORDS` frozenset contents.
- `_SHORT_CONTEXTUAL_FOLLOWUP_MAX_TOKENS` and `_SHORT_MEMORY_RECALL_FOLLOWUP_MAX_TOKENS`.
- Prompt wording in `_build_classifier_messages`.
- All `_build_*_route` stage card structures.
- FILE_WORK route blocking logic in `should_resolve`.

---

## 5. Current Test Coverage

### 5.1 Unit tests
**Zero.** No tests in `scripts/test_engines.py` or `tests/`.

### 5.2 Smoke tests

| Script | Coverage |
|--------|----------|
| `scripts/followup_resolution_engine_smoke_test.py` | 14 scenarios via `refine_with_llm`:<br>- delete task follow-up<br>- fallback delete task<br>- complete task follow-up<br>- query tasks follow-up<br>- chat/ack follow-up<br>- store memory follow-up<br>- remove memory follow-up<br>- 8 browser follow-up variants (title, topic, anything-else, details, go-back, download) |

The smoke test exercises the **orchestration path** (`refine_with_llm`) but
provides **no direct coverage** for `should_resolve` or its individual
heuristic helpers. Many code paths in `should_resolve` are not hit by the
smoke test:

- FILE_WORK route blocking
- Dependency override follow-up
- File readback follow-up
- Event detail follow-up
- Short contextual follow-up (≤8 tokens with action words)
- Ambiguous reference without action words

---

## 6. Missing Coverage

The following deterministic paths have **zero direct automated tests**:

| Path | Risk if broken during move |
|------|---------------------------|
| `should_resolve` gate logic | Follow-up detection silently changes; new requests mis-routed as follow-ups or vice versa |
| `_looks_like_dependency_override_followup` | False override approvals or missed overrides |
| `_looks_like_file_readback_followup` | Missed "read it back" requests |
| `_should_resolve_event_detail_followup` | Missed calendar detail queries |
| `_should_resolve_memory_recall_followup` | Missed memory recall queries |
| `_looks_like_dependency_file_clarification_followup` | Missed dependency file clarifications |
| `_should_resolve_runtime_context_followup` | Missed runtime-context follow-ups |
| `_looks_like_browser_context_followup` | Missed browser continuations |
| FILE_WORK route blocking in `should_resolve` | FILE_WORK stages incorrectly intercepted as follow-ups |

The smoke test provides **indirect regression protection** for the 14
scenarios it exercises, but a relocation could break an unexercised code
path in `should_resolve` and the failure would not be caught.

---

## 7. Recommendation

**B) Safe only after adding tests.**

FollowupResolutionEngine is behaviorally a pure service and structurally safe
to move, but its primary deterministic gate (`should_resolve`) and its many
heuristic helpers are completely untested at the unit level. The existing
smoke test covers 14 `refine_with_llm` scenarios but provides no direct
coverage for the individual heuristic gates.

**Minimum tests to add before relocation:**

1. `should_resolve` — core gate behavior:
   - Returns `True` for contextual remember follow-ups ("just remember that").
   - Returns `True` for ambiguous memory follow-ups ("remove it from memory").
   - Returns `True` for affirmative confirmations to offers ("yes" after "Should I…?").
   - Returns `True` for readonly short task/event queries ("any tasks left?").
   - Returns `False` for FILE_WORK routes (unless memory/event/file-readback/dependency).
   - Returns `False` for messages starting with "/".
   - Returns `False` for empty messages.

2. `_looks_like_dependency_override_followup`:
   - Returns `True` for "override it", "proceed", "do it anyway".
   - Returns `False` for unrelated text.

3. `_looks_like_file_readback_followup`:
   - Returns `True` for "read it back", "show that exactly".
   - Returns `False` for unrelated text.

4. `_should_resolve_event_detail_followup`:
   - Returns `True` for event detail queries after event-related context.
   - Returns `False` for unrelated text.

5. `_should_resolve_memory_recall_followup`:
   - Returns `True` for memory recall queries after memory-related context.
   - Returns `False` for unrelated text.

Once those tests exist and pass, FollowupResolutionEngine can be relocated
with the same zero-behavior-change pattern used for the other service moves.

---

## 8. Import / Export Map (for future move)

Files that would need import updates:

```
core/engines/__init__.py          — remove FollowupResolutionEngine export
core/services/__init__.py         — add FollowupResolutionEngine export
core/orchestrator_phases.py       — update import

scripts/followup_resolution_engine_smoke_test.py  — update import

docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md    — table update
docs/architecture/TRIGGER_FLOW.md                     — path updates
```

---

## 9. Notes

- FollowupResolutionEngine is the **largest** remaining Direct-Call Utility
  in `core/engines/` (1,349 lines).
- Unlike `FileWorkEngine`, it has **no safety-critical guards** that could
cause data loss if broken.
- The primary risk of moving without tests is **routing behavior drift**,
  not safety.
- The smoke test is **more comprehensive** than RouteClarifier's was
  (14 scenarios vs 3), but still indirect.
