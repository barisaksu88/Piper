# VerificationEngine Contract

Status: Complete — extracted and frozen 2026-03-15
Date: 2026-03-15

This file defines the contract for `VerificationEngine` before any code is moved.
No existing logic is changed until this contract is proven stable.

Companion docs:
- [BLUEPRINT.md](BLUEPRINT.md)
- [EXECUTION_ROADMAP.md](EXECUTION_ROADMAP.md)

---

## 1. Purpose

`VerificationEngine` is the single owner of the question:

> "Did this stage succeed, and what is the evidence?"

Right now that question is answered in pieces across:
- `core/executor.py` — holds `_last_file_verdict`, manages retry loop, runs fallback checks
- `core/file_checker.py` — coordinates deterministic rules vs LLM checker
- `core/file_checker_rules.py` — deterministic filesystem checks
- `core/file_stage_policy.py` — gates whether verification runs at all
- `core/services/state_mutation.py` — packages mutation outcomes under a separate label scheme

The engine does not replace any of that logic.
It absorbs it under one owner with one output contract.

---

## 2. What VerificationEngine Owns

- deciding whether a given stage + tool combination requires verification
- running the deterministic rule checker (currently `LocalFileOpRuleChecker`)
- running the LLM file checker when rules do not match
- running the current-state fallback check (currently inline in executor)
- upgrading PARTIAL verdicts when current state proves VERIFIED
- packaging mutation outcomes into the same result shape as file-work outcomes
- emitting a single `VerificationResult` with a clear continuation recommendation

What it does NOT own:
- the retry loop itself (executor still controls the step loop)
- planner decisions (what tool to call next)
- persona output (how to narrate the result)

---

## 3. Input Contract

```
stage           StageCard       what the stage wants to achieve
tool_result     ToolResult      what the tool actually returned
workspace       Path            filesystem root for evidence checks
step            int             current step number inside the stage
retry_budget    int             steps remaining before forced stop
```

For mutation stages, `tool_result` is replaced by:

```
outcome_pack    StageOutcomePack    the mutation outcome from StateMutationEngine
```

---

## 4. Output Contract

`VerificationResult` — one object, always returned.

```
verdict             VERIFIED | PARTIAL | FAILED
effective_success   bool
evidence_summary    str     what proved it, or what failed
recommendation      STOP_SUCCESS | RETRY | STOP_FAILED
checker_path        RULES | LLM | STATE_CHECK | MUTATION | NONE
```

Rules are encoded here, not scattered in the caller:

| verdict  | retries left | recommendation  |
|----------|-------------|-----------------|
| VERIFIED | any         | STOP_SUCCESS    |
| PARTIAL  | yes         | RETRY           |
| PARTIAL  | no          | STOP_FAILED     |
| FAILED   | any         | STOP_FAILED     |

`effective_success` is `True` only when verdict is `VERIFIED`.
`PARTIAL` is never narrated as success.

---

## 5. Checker Path Priority

When verification runs, the engine tries paths in this order:

1. **RULES** — `LocalFileOpRuleChecker.evaluate(tool_result)`
   - deterministic, no LLM
   - returns a result or `None` if the action type is not covered

2. **LLM** — `FileChecker.run_file_checker(stage, tool_result)`
   - used when RULES returns `None`
   - injects stage + evidence into `data/prompts/file_checker.txt`
   - normalises LLM output to VERIFIED / PARTIAL / FAILED

3. **STATE_CHECK** — `FileChecker.verify_current_file_stage_state(stage, tool_result)`
   - used as upgrade pass when tool succeeded but initial verdict was not VERIFIED
   - reads actual filesystem state instead of relying on tool output alone
   - can upgrade PARTIAL → VERIFIED; cannot downgrade VERIFIED

4. **MUTATION** — outcome from `StateMutationEngine`
   - no file-system evidence; verdict comes from explicit tool contracts
   - VERIFIED if the mutation tool returned authoritative success
   - FAILED otherwise

5. **NONE** — stage does not require verification
   - policy gate returned false (inspection, planning, script-launch stages)
   - result is returned with `verdict=VERIFIED, effective_success=True` so executor continues normally

---

## 6. Policy Gate (should_verify)

Before running any checker, the engine checks whether verification is required.

Currently this logic lives in `FileStagePolicy.stage_requires_file_verification()`
and `FileStagePolicy.tool_requires_file_checker()`.

Those rules move into the engine's `should_verify(stage, tool_name)` method.
`FileStagePolicy` can remain as a helper until migration is complete.

Stages that skip verification:
- inspection / read-only FILE_WORK stages
- planning / proposal stages
- script-launch stages
- dependency recovery stages

---

## 7. What Moves In (Migration Map)

This table shows where each piece currently lives and where it will move.

| Current location                                      | Moves to                           |
|-------------------------------------------------------|------------------------------------|
| `executor.py` — `_last_file_verdict` state            | `VerificationEngine.evaluate()`    |
| `executor.py` — verdict upgrade pass (PARTIAL→VERIFIED) | `VerificationEngine.evaluate()`  |
| `executor.py` — final fallback `verify_current_file_stage_state` call | `VerificationEngine.evaluate()` |
| `executor.py` — retry-budget decision per verdict    | `VerificationResult.recommendation` |
| `file_checker.py` — checker path coordination        | `VerificationEngine._run_checker()`|
| `file_stage_policy.py` — `stage_requires_file_verification` | `VerificationEngine.should_verify()` |
| `state_mutation.py` — `build_stage_outcome_pack` status labels | `VerificationEngine.evaluate_mutation()` |

`file_checker_rules.py` stays as-is.
`file_checker.py` stays as-is.
Both become internal helpers called by the engine.

---

## 8. What Stays Put (Do Not Move)

- `file_checker_rules.py` — all deterministic rule logic stays here
- `file_checker.py` — LLM checker coordination stays here
- `executor.py` — step loop, planner calls, scratchpad management
- `state_mutation.py` — mutation execution and tool dispatch
- `contracts.py` — `FileCheckDecision`, `StageOutcomePack`, `StageCard` stay as-is

The engine is a new owner, not a replacement for its helpers.

---

## 9. Migration Steps (when ready)

Follow the standard v1 phase workflow:

1. Add `VerificationResult` to `contracts.py`
2. Implement `VerificationEngine` shell in `core/services/verification.py`
3. Route executor verification calls through the engine
4. Prove parity with existing smoke tests (file_edit, file_crud, file_chaos, file_lookup, state_mutation_engine)
5. Remove the scattered inline verdict logic from `executor.py`
6. Update `EXECUTION_ROADMAP.md` phase 4 status

Do not begin step 3 until the smoke tests in step 4 are confirmed passing on the current path.

---

## 10. Definition of Done for This Engine

- executor no longer holds `_last_file_verdict` as instance state
- VERIFIED / PARTIAL / FAILED decisions come from one call site only
- mutation outcomes and file-work outcomes share the same result shape
- retry/stop recommendation is in the result, not inlined in the executor loop
- no regression on the smoke test pack
