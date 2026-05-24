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

| Path | Risk if broken during move |
|------|---------------------------|
| `should_force_clarification` | Clarification logic silently changes; ambiguous inputs route to task execution |
| `should_refine_task_route` | Refinement gate changes; clear inputs waste LLM calls or ambiguous inputs bypass refinement |
| `_task_is_targeted_file_lookup` | File lookups incorrectly trigger clarification |
| `_task_is_computer_use` | Computer-use tasks incorrectly trigger clarification |
| `_build_route_from_proposal_confirmation` | Proposal confirmations missed; user gets clarification instead of action |
| `_extract_date_from_schedule_proposal` | Wrong date extracted from assistant proposal |
| `_extract_subject_from_schedule_proposal` | Wrong subject extracted from assistant proposal |
| `_extract_subject_from_user_correction` | Wrong subject extracted from user correction |

The smoke test's `_DummyLLM` provides **no regression protection** for the heuristic paths because it short-circuits before `should_force_clarification` and `should_refine_task_route` are evaluated.

---

## 7. Recommendation

**B) Safe only after adding tests.**

RouteClarifier is behaviorally a pure service and structurally safe to move,
but its two primary deterministic public methods (`should_force_clarification`,
`should_refine_task_route`) are completely untested. The existing smoke test
only exercises the LLM-dependent orchestration path and provides no coverage
for the heuristic gates that actually decide whether clarification happens.

**Minimum tests to add before relocation:**

1. `should_force_clarification`:
   - Returns `True` for ≤4 token non-action inputs (e.g., "a temporary tree").
   - Returns `False` for inputs with action verbs (e.g., "create a file").
   - Returns `False` for path-like inputs (e.g., "C:\\docs\\file.txt").
   - Returns `False` when history contains assistant messages (mid-thread follow-up).
   - Returns `False` for retry hints ("try again", "redo").
   - Returns `False` for targeted file lookups and computer-use decisions.

2. `should_refine_task_route`:
   - Returns `True` for ≤6 token non-action inputs.
   - Returns `False` for inputs with action verbs.
   - Returns `False` for path-like inputs.
   - Returns `True` for correction fragments ("no, wrong", "actually…") without action verbs.
   - Returns `False` for targeted file lookups and computer-use decisions.

3. `_task_is_targeted_file_lookup`:
   - Returns `True` for FILE_WORK stages with `stage_requires_targeted_read` or `stage_requires_targeted_lookup`.
   - Returns `False` for non-TASK decisions and non-file stages.

4. `_task_is_computer_use`:
   - Returns `True` for stages with `stage_type == "COMPUTER_USE"`.
   - Returns `False` for non-TASK decisions and non-computer-use stages.

Once those tests exist and pass, RouteClarifier can be relocated with the
same zero-behavior-change pattern used for the other service moves.

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
