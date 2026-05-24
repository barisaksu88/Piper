# RouteClarifier Service Move Readiness Audit

**Status:** Audit complete — recommendation: add tests first  
**Branch:** `audit/route-clarity-service-readiness`  
**Date:** 2026-05-24  
**Source:** `core/engines/route_clarity.py`  
**Possible target:** `core/services/route_clarity.py`

---

## 1. Behavior Classification

RouteClarifier is a **pure direct-call utility**.

- No hooks, registries, tail-blocks, interceptors, or lifecycle participation.
- No background threads or async loops.
- No mutable module-level state.
- Carries no meaningful instance state (all logic is deterministic heuristics + one LLM call).

**Behaviorally identical to** `SearchWorkflowEngine`, `SummaryEngine`,
`VerificationEngine`, and `FileWorkEngine` — all already relocated to `core/services/`.

---

## 2. Caller Map

### 2.1 Production code

| File | Usage |
|------|-------|
| `core/engines/__init__.py` | Package export |
| `core/orchestrator_phases.py` | Creates `_ROUTE_CLARIFIER = RouteClarifier()` singleton; imports `_PATHISH_RE` for fallback clarification logic |

### 2.2 Test / smoke code

| File | Usage |
|------|-------|
| `scripts/route_clarifier_smoke_test.py` | Smoke test for `refine_with_llm` (ambiguous, explicit, proposal confirmation) |

**No unit tests exist** in `scripts/test_engines.py` or `tests/`.

---

## 3. Routing Responsibilities Owned by RouteClarifier

| Method | Responsibility | Risk if broken |
|--------|---------------|---------------|
| `should_force_clarification` | Deterministic heuristic: forces clarification for short/fragmentary TASK inputs (≤4 tokens, or ≤5 tokens with correction prefix) | Ambiguous tasks execute without clarification, causing wrong action |
| `should_refine_task_route` | Deterministic heuristic: decides whether LLM refinement is needed (≤6 tokens without action verb, or correction fragment) | Wastes LLM calls on already-clear inputs, or misses ambiguous ones |
| `refine_with_llm` | Orchestrates proposal confirmation → force clarification → refine via LLM → build route | Broken routing pipeline, wrong stage cards |
| `_build_route_from_proposal_confirmation` | Detects affirmative replies to scheduling proposals ("yes", "ok", "sure"…) and converts to TASK_EVENT_WORK | Missed confirmations cause clarification loops |
| `_build_clarification_route` | Builds a CHAT clarification stage card | Wrong stage type, confusing user experience |
| `_extract_date_from_schedule_proposal` | Extracts date phrase from assistant schedule proposal | Wrong event date |
| `_extract_subject_from_schedule_proposal` | Extracts event title from assistant proposal | Wrong event title |
| `_extract_subject_from_user_correction` | Cleans user correction text for event title | Wrong event title |
| `_task_is_targeted_file_lookup` | Detects FILE_WORK stages needing targeted read/lookup | File lookups get incorrectly routed to clarification |
| `_task_is_computer_use` | Detects COMPUTER_USE stages | Computer-use tasks get incorrectly routed to clarification |

---

## 4. Behavior That Must Not Change During Relocation

- `should_force_clarification` token thresholds (≤4 tokens, ≤5 with correction).
- `should_refine_task_route` token threshold (≤6 tokens without action verb).
- All regex patterns (`_RETRY_HINT_RE`, `_RETRY_PREFIX_RE`, `_CLEAR_ACTION_HINT_RE`, `_CORRECTION_FRAGMENT_RE`, `_PATHISH_RE`, `_AFFIRMATIVE_CONFIRM_RE`, `_SCHEDULE_PROPOSAL_RE`, `_PROPOSAL_EVENT_TITLE_RE`, `_FOR_DATE_RE`).
- Prompt wording in `_build_classifier_messages`.
- `_build_clarification_route` stage card structure.
- Proposal confirmation logic and subject extraction heuristics.

---

## 5. Current Test Coverage

### 5.1 Unit tests
**Zero.** No tests in `scripts/test_engines.py` or `tests/`.

### 5.2 Smoke tests

| Script | Coverage |
|--------|----------|
| `scripts/route_clarifier_smoke_test.py` | `refine_with_llm` with ambiguous input → CHAT clarification route<br>`refine_with_llm` with explicit input → `None`<br>`refine_with_llm` with proposal confirmation → TASK_EVENT_WORK route |

The smoke test covers the **orchestration path** (`refine_with_llm`) but does **not** exercise:
- `should_force_clarification` directly
- `should_refine_task_route` directly
- `_build_route_from_proposal_confirmation` edge cases (non-matching inputs)
- Subject/date extraction helpers
- `_task_is_targeted_file_lookup` / `_task_is_computer_use`

---

## 6. Missing Coverage

The following deterministic public APIs have **zero automated tests**:

| Path | Risk if broken during move | Status |
|------|---------------------------|--------|
| `should_force_clarification` | Clarification logic silently changes; ambiguous inputs route to task execution | ✅ Covered |
| `should_refine_task_route` | Refinement gate changes; clear inputs waste LLM calls or ambiguous inputs bypass refinement | ✅ Covered |
| `_task_is_targeted_file_lookup` | File lookups incorrectly trigger clarification | ✅ Covered |
| `_task_is_computer_use` | Computer-use tasks incorrectly trigger clarification | ✅ Covered |
| `_build_route_from_proposal_confirmation` | Proposal confirmations missed; user gets clarification instead of action | Smoke only |
| `_extract_date_from_schedule_proposal` | Wrong date extracted from assistant proposal | Untested |
| `_extract_subject_from_schedule_proposal` | Wrong subject extracted from assistant proposal | Untested |
| `_extract_subject_from_user_correction` | Wrong subject extracted from user correction | Untested |

The four primary deterministic gates are now covered by unit tests.
The extraction helpers and proposal-confirmation edge cases remain
untested but are lower-risk for a pure file move.

---

## 7. Test Additions (completed on branch `test/route-clarifier-heuristics`)

### 7.1 `should_force_clarification`

| Test | What it verifies |
|------|-----------------|
| `test_force_clarification_true_for_short_non_action` | ≤4 token non-action input → `True` |
| `test_force_clarification_false_for_action_verb` | Input with action verb → `False` |
| `test_force_clarification_false_for_path_like` | Path-like input → `False` |
| `test_force_clarification_false_mid_thread` | History contains assistant message → `False` |
| `test_force_clarification_false_for_retry_hint` | Retry hint ("try again") → `False` |
| `test_force_clarification_false_for_targeted_read` | Targeted file read decision → `False` |
| `test_force_clarification_false_for_computer_use` | Computer-use decision → `False` |

### 7.2 `should_refine_task_route`

| Test | What it verifies |
|------|-----------------|
| `test_refine_route_true_for_short_non_action` | ≤6 token non-action input → `True` |
| `test_refine_route_false_for_action_verb` | Input with action verb → `False` |
| `test_refine_route_false_for_path_like` | Path-like input → `False` |
| `test_refine_route_true_for_correction_fragment` | Correction fragment ("no, wrong") → `True` |
| `test_refine_route_true_for_actually_tomorrow` | Correction fragment ("actually tomorrow") → `True` |
| `test_refine_route_false_for_targeted_lookup` | Targeted file lookup decision → `False` |
| `test_refine_route_false_for_computer_use` | Computer-use decision → `False` |

### 7.3 `_task_is_targeted_file_lookup`

| Test | What it verifies |
|------|-----------------|
| `test_task_is_targeted_file_lookup_true_for_read` | Targeted read stage → `True` |
| `test_task_is_targeted_file_lookup_true_for_lookup` | Targeted lookup stage → `True` |
| `test_task_is_targeted_file_lookup_false_for_non_task` | Non-TASK decision → `False` |
| `test_task_is_targeted_file_lookup_false_for_non_file_stage` | Non-file stage → `False` |

### 7.4 `_task_is_computer_use`

| Test | What it verifies |
|------|-----------------|
| `test_task_is_computer_use_true` | COMPUTER_USE stage → `True` |
| `test_task_is_computer_use_false_for_non_task` | Non-TASK decision → `False` |
| `test_task_is_computer_use_false_for_non_computer_stage` | Non-computer-use stage → `False` |

---

## 8. Recommendation

**A) Safe to relocate after tests are green.**

All missing deterministic heuristic tests have been added and pass.
RouteClarifier remains a pure direct-call utility with no hooks, registries,
or lifecycle participation. The relocation can proceed using the same
zero-behavior-change pattern as `SearchWorkflowEngine`, `SummaryEngine`,
`VerificationEngine`, and `FileWorkEngine`.

---

## 8. Import / Export Map (for future move)

Files that would need import updates:

```
core/engines/__init__.py          — remove RouteClarifier export
core/services/__init__.py         — add RouteClarifier export
core/orchestrator_phases.py       — update import + _PATHISH_RE import

scripts/route_clarifier_smoke_test.py  — update import

docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md    — table update
docs/architecture/TRIGGER_FLOW.md                     — path updates
```

**Note on `_PATHISH_RE`:**
`core/orchestrator_phases.py` imports `_PATHISH_RE` directly from `core.engines.route_clarity`.
If RouteClarifier moves, this regex import must move with it or be re-exported.
The regex is used in fallback clarification logic outside the class.

---

## 9. Notes

- RouteClarifier is the **simplest** remaining Direct-Call Utility in `core/engines/`.
- Unlike `FileWorkEngine`, RouteClarifier has **no safety-critical guards** that could cause data loss if broken.
- The primary risk of moving without tests is **routing behavior drift**, not safety.
- The `_PATHISH_RE` direct import in `orchestrator_phases.py` is a coupling detail
  that must not be forgotten during relocation.
