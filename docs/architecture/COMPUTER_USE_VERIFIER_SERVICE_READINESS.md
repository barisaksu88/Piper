# ComputerUseVerifier Service Readiness Audit

**Status:** Active audit  
**Source:** `core/engines/computer_use_verifier.py`  
**Possible target:** `core/services/computer_use_verifier.py`  
**Date:** 2026-05-23

---

## 1. Behavior Classification

**Bucket:** Direct-Call Utility  
**No hooks, registries, tail-blocks, interceptors, or lifecycle participation.**

`core/engines/computer_use_verifier.py` is a module of standalone pure functions (not a class). It is imported and invoked directly by `core/executor.py` during COMPUTER_USE stage execution. It does not register itself with any engine lifecycle system.

> **Note:** `ENGINE_UTILITY_CLASSIFICATION.md` incorrectly labels this as `ComputerUseVerifier` (implying a class). The module exposes four top-level functions: `new_stage_evidence`, `update_stage_evidence`, `evaluate_stage`, and `build_verified_payload`.

---

## 2. Caller Map

| Caller | Import line | Usage |
|--------|-------------|-------|
| `core/executor.py` | `from core.engines.computer_use_verifier import (...)` | `new_stage_evidence` (stage init), `update_stage_evidence` (post-tool accumulation), `evaluate_stage` (verification), `build_verified_payload` (payload assembly) |

**Total production callers:** 1 file.  
**Total test/script callers:** 0 files.

---

## 3. Import / Export Map

**Current exports (`core/engines/computer_use_verifier.py`):**
- `new_stage_evidence(stage)` → `dict[str, Any]`
- `update_stage_evidence(evidence, tool_result)` → `dict[str, Any]`
- `evaluate_stage(stage, evidence)` → `VerificationResult`
- `build_verified_payload(stage, evidence, verification)` → `dict[str, Any]`

**No package re-export.** `core/engines/__init__.py` does not export these functions.

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

**None.** No `test_computer_use_verifier.py` exists in `tests/`.

### 7.2 Smoke Tests

**None.** No script in `scripts/` exercises `computer_use_verifier.py`.

### 7.3 Indirect Coverage

**None.** The only caller (`core/executor.py`) is tested by executor-level tests, but `computer_use_verifier.py` functions are not directly exercised by any focused test.

---

## 8. Missing Tests / Smokes

The following public functions have **no dedicated test coverage**:

| Function | Risk level | Notes |
|----------|-----------|-------|
| `evaluate_stage` | **High** | Safety-critical verification logic with threshold scoring |
| `build_verified_payload` | Medium | Payload assembly affects persona context |
| `update_stage_evidence` | Low | Dict accumulation, mostly mechanical |
| `new_stage_evidence` | Low | Simple dict builder |

**Critical observation:** `evaluate_stage()` is entirely untested. It contains complex scoring logic (download hint matching with token aliases, form fill verification with selector aliases, navigation back/forward detection, extraction topic matching) that could silently regress if modified.

---

## 9. Recommended Target Path

```
core/services/computer_use_verifier.py
```

The module already imports from `core.services.verification` (a relocated service), confirming it belongs in the services layer.

---

## 10. Recommendation

**B) Safe only after adding tests.**

**Rationale:**

`core/engines/computer_use_verifier.py` is correctly classified as a **pure direct-call utility** with no hooks, registries, or lifecycle participation. In principle, it is eligible for relocation to `core/services/` under the established doctrine.

However, `evaluate_stage()` is **safety-critical** — it controls whether a `COMPUTER_USE` stage is classified as `VERIFIED`, `PARTIAL`, or `FAILED`, which directly affects retry budgets and user-facing success claims. The function contains complex threshold scoring (download hint matching ≥ 28, form fill alias resolution, navigation back/forward detection, extraction topic matching) and **has zero test coverage**.

Under Piper's architecture cleanup workflow, safety-critical untested verifier logic must get deterministic tests before relocation. A relocation itself is mechanical and low-risk (1 caller, no import cycles), but moving an untested safety-critical module would leave the codebase without a regression baseline.

**Recommended next steps:**

1. Add `tests/test_computer_use_verifier.py` covering:
   - `evaluate_stage()` — download verification (success, missing, hint mismatch)
   - `evaluate_stage()` — form fill verification (success, missing, alias match)
   - `evaluate_stage()` — navigation verification (forward click, back navigation, missing)
   - `evaluate_stage()` — extraction verification (topic match, selector match, title report, status text)
   - `evaluate_stage()` — fallback (page opened vs. nothing done)
   - `evaluate_stage()` — partial vs. failed boundary
   - `build_verified_payload()` — output shape for all major evidence types

2. Once tests pass, create `move/computer-use-verifier-service` branch and relocate.

3. Run validation: `compileall` + `pytest tests/` + `pytest web_ui/bridge/` + relevant smoke tests.

---

## 11. Stale Doc References Found

The following doc files reference `core/engines/computer_use_verifier.py` and will need updating **after** relocation:

- `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` — incorrectly labels it as `ComputerUseVerifier` class; should be updated to list the module and its functions
- `docs/architecture/TRIGGER_FLOW.md` — line 1476 caller map
- `docs/specs/computer-use.md` — line 36 path reference
- `docs/specs/engine-directory-audit.md` — lists the file
- `docs/WIP.md` — line 60 lists the file

These are documentation-only references. No runtime code changes.
