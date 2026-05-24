# ComputerUseVerifier Service Readiness Audit

**Status:** Relocated ✅  
**Source:** `core/services/computer_use_verifier.py` (relocated from `core/engines/computer_use_verifier.py`)  
**Date:** 2026-05-23

---

## 1. Behavior Classification

**Bucket:** Direct-Call Utility  
**No hooks, registries, tail-blocks, interceptors, or lifecycle participation.**

`core/services/computer_use_verifier.py` is a module of standalone pure functions. It is imported and invoked directly by `core/executor.py` during COMPUTER_USE stage execution. It does not register itself with any engine lifecycle system.

---

## 2. Caller Map

| Caller | Import line | Usage |
|--------|-------------|-------|
| `core/executor.py` | `from core.services.computer_use_verifier import (...)` | `new_stage_evidence` (stage init), `update_stage_evidence` (post-tool accumulation), `evaluate_stage` (verification), `build_verified_payload` (payload assembly) |
| `tests/test_computer_use_verifier.py` | `from core.services.computer_use_verifier import (...)` | Direct unit tests for all four public functions |

**Total production callers:** 1 file.  
**Total test/script callers:** 1 file.

---

## 3. Import / Export Map

**Current exports (`core/services/computer_use_verifier.py`):**
- `new_stage_evidence(stage)` → `dict[str, Any]`
- `update_stage_evidence(evidence, tool_result)` → `dict[str, Any]`
- `evaluate_stage(stage, evidence)` → `VerificationResult`
- `build_verified_payload(stage, evidence, verification)` → `dict[str, Any]`

**Package export.** `core/services/__init__.py` now exports `new_stage_evidence`, `update_stage_evidence`, `evaluate_stage`, and `build_verified_payload`. `core/engines/__init__.py` does not export these functions.

---

## 4. Runtime Responsibilities

### 4.1 Evidence Initialization
- `new_stage_evidence(stage)` — creates empty evidence dict with fields for URL, title, actions, extracts, downloads, field values, element inventory, and history navigation.

### 4.2 Evidence Accumulation
- `update_stage_evidence(evidence, tool_result)` — accumulates BROWSER_OP tool results into the evidence dict. Tracks actions, current URL, page title, text extracts, downloads, form field values, and element inventory.

### 4.3 Stage Verification
- `evaluate_stage(stage, evidence)` — evaluates whether accumulated browser evidence satisfies the stage's `computer_use` metadata requirements. Returns `VerificationResult.verified()`, `.partial()`, or `.failed()`.

Checks performed:
- **Download verification** — confirms artifact was downloaded to expected directory with optional hint matching (scoring threshold ≥ 28)
- **Form fill verification** — confirms selector was filled with expected text
- **Navigation verification** — confirms page navigation occurred (click or go_back)
- **Extraction verification** — confirms text extraction, title reporting, status text, or topic-matched extract
- **Fallback** — if no specific requirements, checks that a browser page was opened

### 4.4 Verified Payload Building
- `build_verified_payload(stage, evidence, verification)` — assembles the structured payload returned to downstream layers (persona, context pack, etc.). Includes extracts, downloads, field values, status text, heading text, and reported title.

---

## 5. Safety Responsibilities

`evaluate_stage()` is **safety-critical**. It governs whether a browser automation stage is considered:
- `VERIFIED` — safe to proceed / report success
- `PARTIAL` — retry budget may be consumed
- `FAILED` — stage failed, no retry

Specific safety-sensitive logic:
- Download hint scoring (`_score_download_hint_haystack`) uses a threshold of **28** to accept/reject downloads
- Form fill matching uses selector aliases and inventory traversal
- Navigation verification distinguishes forward navigation (`click` + URL change) from back-navigation (`go_back` + history evidence)

---

## 6. Behavior That Must Not Change During Relocation

All of the above. Zero behavior change. This is an import-only relocation.

Specifically:
- `evaluate_stage()` scoring thresholds and matching logic
- `update_stage_evidence()` accumulation semantics
- `build_verified_payload()` output shape and field population rules
- `new_stage_evidence()` default evidence structure

---

## 7. Current Tests / Smokes

### 7.1 Pytest Unit Tests

`tests/test_computer_use_verifier.py` (49 tests) covers all four public functions.

### 7.2 Smoke Tests

**None.** No script in `scripts/` directly exercises `computer_use_verifier.py`.

### 7.3 Indirect Coverage

`core/executor.py` integration tests may exercise these functions through COMPUTER_USE stage execution, but the unit test file is the primary coverage.

---

## 8. Test Coverage Summary

`tests/test_computer_use_verifier.py` (49 tests) covers:
- `new_stage_evidence()` — default structure, stage metadata preservation
- `update_stage_evidence()` — URL/title, actions, extracts, downloads, field values, inventory, history navigation; deduplication; non-BROWSER_OP filtering
- `evaluate_stage()` download verification — success, missing, hint match above threshold, hint mismatch below threshold, download directory filtering
- `evaluate_stage()` form fill — selector/value match, inventory alias match, missing field, value mismatch (partial), no-selector-hint fallback
- `evaluate_stage()` navigation — forward click + URL change, missing click (partial), unchanged URL (partial), go_back success, go_back missing history (partial)
- `evaluate_stage()` extraction — topic match, missing topic (partial), title report, missing title, selector match, status text, missing extraction
- `evaluate_stage()` fallback — verified when page opened, failed when nothing done, partial when extracts exist without requirements
- `build_verified_payload()` — extracts, downloads, field values, status text, heading text, reported title, summary preservation, download label/href, requested topic, extracted text from expected_text, topic match sets extracted_text

---

## 9. Recommended Target Path

```
core/services/computer_use_verifier.py
```

The module already imports from `core.services.verification` (a relocated service), confirming it belongs in the services layer.

---

## 10. Recommendation

**A) Safe to relocate after tests are green.**

**Rationale:**

`core/services/computer_use_verifier.py` is correctly classified as a **pure direct-call utility** with no hooks, registries, or lifecycle participation. It has:

- **Only 1 production caller** (`core/executor.py`) — minimal blast radius
- **No cross-engine dependencies** — it already imports from `core.services.verification`
- **Deterministic pure functions** — no side effects, no state, no threading
- **No registry participation** — no hooks, no tail-blocks, no interceptors

**Unit tests have now been added** (`tests/test_computer_use_verifier.py`, 49 tests) covering all four public functions and the major behavioral branches of `evaluate_stage()`, including threshold boundary behavior for download hint scoring, form fill alias resolution, navigation back/forward detection, and extraction topic matching.

The relocation is mechanical: move file, update 1 import line in `core/executor.py`, update doc references. `compileall` + pytest + smoke tests would catch any import issue.

**Relocation completed on branch:** `move/computer-use-verifier-service`

- File moved from `core/engines/computer_use_verifier.py` → `core/services/computer_use_verifier.py`
- Import updated in `core/executor.py`
- Test import updated in `tests/test_computer_use_verifier.py`
- `core/services/__init__.py` now exports `new_stage_evidence`, `update_stage_evidence`, `evaluate_stage`, `build_verified_payload`
- `core/engines/__init__.py` does not export these functions
- All stale doc references to the old path were updated

---

## 11. Doc References

Stale `core/engines/computer_use_verifier.py` references in the following docs were updated during relocation:

- `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` — removed from Direct-Call Utilities, added to Services outside `core/engines/`
- `docs/architecture/TRIGGER_FLOW.md` — updated caller map path
- `docs/specs/computer-use.md` — updated path reference
- `docs/WIP.md` — updated path reference

The only remaining reference to the old path is in `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md`, which intentionally records the historical relocation.
