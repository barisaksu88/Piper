# FollowupResolutionEngine Service Move Readiness Audit

**Status:** Audit complete — recommendation: add tests first  
**Branch:** `audit/followup-resolution-service-readiness`  
**Date:** 2026-05-24  
**Source:** `core/services/followup_resolution.py` (relocated from `core/engines/followup_resolution.py`)  
**Status:** Relocated ✅

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

| Path | Risk if broken during move | Status |
|------|---------------------------|--------|
| `should_resolve` gate logic | Follow-up detection silently changes; new requests mis-routed as follow-ups or vice versa | ✅ Covered |
| `_looks_like_dependency_override_followup` | False override approvals or missed overrides | ✅ Covered |
| `_looks_like_file_readback_followup` | Missed "read it back" requests | ✅ Covered |
| `_should_resolve_event_detail_followup` | Missed calendar detail queries | ✅ Covered |
| `_should_resolve_memory_recall_followup` | Missed memory recall queries | ✅ Covered |
| `_looks_like_dependency_file_clarification_followup` | Missed dependency file clarifications | ✅ Covered |
| `_should_resolve_runtime_context_followup` | Missed runtime-context follow-ups | ✅ Covered |
| `_looks_like_browser_context_followup` | Missed browser continuations | ✅ Covered |
| FILE_WORK route blocking in `should_resolve` | FILE_WORK stages incorrectly intercepted as follow-ups | ✅ Covered |

**Note:** Tests revealed that "do it anyway" and "do it" are dead code
paths in `_looks_like_dependency_override_followup` — the
`_QUESTION_START_RE` regex blocks them before the explicit override regex
can match. This is a pre-existing production issue, not caused by tests.

---

## 7. Test Additions (completed on branch `test/followup-resolution-heuristics`)

### 7.1 `should_resolve`

| Test | What it verifies |
|------|-----------------|
| `test_should_resolve_true_contextual_remember` | Contextual remember follow-up → `True` |
| `test_should_resolve_true_ambiguous_memory` | Ambiguous memory follow-up → `True` |
| `test_should_resolve_true_affirmative_to_offer` | Affirmative confirmation to assistant offer → `True` |
| `test_should_resolve_true_readonly_task_query` | Readonly short task query → `True` |
| `test_should_resolve_false_file_work_route` | FILE_WORK route without explicit follow-up type → `False` |
| `test_should_resolve_false_slash_prefix` | Slash-prefixed message → `False` |
| `test_should_resolve_false_empty` | Empty message → `False` |

### 7.2 `_looks_like_dependency_override_followup`

| Test | What it verifies |
|------|-----------------|
| `test_dependency_override_true_override_it` | "override it" with active dependency → `True` |
| `test_dependency_override_true_proceed` | "proceed" with active dependency → `True` |
| `test_dependency_override_true_force_it` | "force it" with active dependency → `True` |
| `test_dependency_override_true_ignore_dependency` | "ignore the dependency" with active dependency → `True` |
| `test_dependency_override_false_no_context` | Override text without dependency context → `False` |
| `test_dependency_override_false_unrelated` | Unrelated text → `False` |

### 7.3 `_looks_like_file_readback_followup`

| Test | What it verifies |
|------|-----------------|
| `test_file_readback_true_read_it_back` | "read it back" with single relevant path → `True` |
| `test_file_readback_true_show_exactly` | "show that exactly" with single relevant path → `True` |
| `test_file_readback_false_no_path` | Readback text without relevant path → `False` |
| `test_file_readback_false_unrelated` | Unrelated text → `False` |

### 7.4 `_should_resolve_event_detail_followup`

| Test | What it verifies |
|------|-----------------|
| `test_event_detail_true_after_event_context` | Event detail query after event context → `True` |
| `test_event_detail_false_no_event_context` | Event detail query without event context → `False` |
| `test_event_detail_false_unrelated` | Unrelated text → `False` |

### 7.5 `_should_resolve_memory_recall_followup`

| Test | What it verifies |
|------|-----------------|
| `test_memory_recall_true_after_memory_context` | Memory recall query after memory context → `True` |
| `test_memory_recall_false_no_memory_context` | Memory recall query without memory context → `False` |
| `test_memory_recall_false_unrelated` | Unrelated text → `False` |

### 7.6 Optional helpers

| Test | What it verifies |
|------|-----------------|
| `test_dependency_file_clarification_true` | Dependency file clarification with active dependency → `True` |
| `test_dependency_file_clarification_false_no_context` | Without dependency context → `False` |
| `test_runtime_context_followup_true` | Short lookup clarification after TASK route → `True` |
| `test_runtime_context_followup_false_non_task_route` | Non-TASK previous route → `False` |
| `test_browser_context_followup_true` | Browser context follow-up with URL in history → `True` |
| `test_browser_context_followup_false` | Without browser context → `False` |

---

## 8. Recommendation

**A) Safe to relocate after tests are green.**

All missing deterministic heuristic tests have been added and pass.
FollowupResolutionEngine remains a pure direct-call utility with no hooks,
registries, or lifecycle participation. The relocation can proceed using the
same zero-behavior-change pattern as the other service moves.

**Pre-existing issue discovered during testing:**
"do it anyway" and "do it" are dead code paths in
`_looks_like_dependency_override_followup` — the `_QUESTION_START_RE`
regex blocks them before the explicit override regex can match.
This should be addressed in a separate follow-up fix, not in the relocation.

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
