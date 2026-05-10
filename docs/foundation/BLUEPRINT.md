# Piper v1 Engine Blueprint

Status: Active working blueprint
Date: 2026-03-13

This file is the source-of-truth for the `Piper v1` architecture push.

Purpose:
- preserve the original intent of the redesign
- prevent the work from drifting into patch-by-patch improvisation
- define what counts as progress and what counts as distraction

If this file conflicts with [AGENTS.md](../../AGENTS.md), follow `AGENTS.md`.

Reference baseline:
- `Piper v0` source snapshot lives in [versions/piper_v0](../../versions/piper_v0)
- the live root working tree is `Piper v1`

Companion execution docs:
- [EXECUTION_ROADMAP.md](EXECUTION_ROADMAP.md)
- [V1_GUARDRAILS.md](checklists/V1_GUARDRAILS.md)
- [TRIAGE_MAP.md](checklists/TRIAGE_MAP.md)
- [RELEASE_READINESS.md](checklists/RELEASE_READINESS.md)

## 1. Why v1 Exists

Piper has grown into a capable local agent, but too much repeated heavy logic is still spread across:
- route normalization
- executor rails
- checker rules
- prompt assembly
- workflow-specific special cases

This creates three pressures:
- context pressure
- behavior drift
- feature integration cost

`Piper v1` exists to reduce those pressures by turning repeated mechanics into reusable engines and repeated procedures into explicit workflows.

This is not a cosmetic refactor.
This is a survivability refactor.

## 2. Core Philosophy

The central idea is simple:

- skills are reusable workflows
- engines are reusable machinery
- verification remains the authority

Piper must stop relying on one long improvised thought to carry an entire job.
Instead, Piper should:
- retrieve the active slice
- pack the working context
- execute a bounded procedure
- verify reality
- summarize the result
- discard the noise

The model is not the system.
The model is one component inside the system.

## 3. What v1 Is Trying To Achieve

`Piper v1` should feel:
- more consistent on repeated task types
- less likely to wander across similar file/code tasks
- less dependent on stuffing large amounts of context into one prompt
- easier to test at the workflow/mechanics layer
- easier to extend without scattering new logic across unrelated modules

The redesign is successful if Piper becomes more disciplined, not merely more abstract.

## 4. What v1 Is Not

`Piper v1` is not:
- a full rewrite of every module at once
- an excuse to replace proven verification rails with vague abstractions
- a framework exercise
- a “universal reasoning engine”
- a feature-flag maze

Important rule:
- avoid long-lived on/off bypass switches as an architecture strategy
- use the `piper_v0` snapshot as the rollback baseline instead of preserving multiple permanent runtime paths inside v1

Temporary extraction seams are acceptable.
Permanent parallel architectures are not.

## 5. Architectural Beliefs

### 5.1 Context Is a Permanent Constraint

Context limit is real and will remain a hard constraint.
So v1 must optimize for:
- working-set context
- targeted retrieval
- structured handoff
- explicit summaries
- externalized state

v1 must not assume:
- the whole repo fits in one thought
- the full debug trail fits in one prompt
- the model can preserve long-horizon consistency by memory alone

### 5.2 Truth Lives Outside Narration

The redesign must preserve Piper’s existing doctrine:
- tool results matter
- repository-backed state matters
- checker outputs matter
- narration is not authority

No engine or skill may weaken this rule.

### 5.3 Repeated Mechanics Belong in Engines

If the same class of problem is solved repeatedly across multiple workflows, that burden wants an engine.

Good engine candidates are repeated mechanics such as:
- retrieval
- context packing
- patch application
- verification
- stage loop control
- summarization

### 5.5 Deterministic First, Bounded LLM When Language Is The Problem

Piper should prefer deterministic Python ownership for:
- contracts
- state mutation
- verification
- final success/failure authority

However, not every ambiguity problem should be solved with more hardcoded phrasing.

If a failure keeps recurring because users can express the same intention in many natural-language forms, and regex or heuristic rescue paths keep multiplying, the correct move is to add a bounded LLM layer at the ambiguity boundary.

This is especially appropriate for:
- follow-up reference resolution
- correction vs mutation classification
- vague memory-intent turns
- soft-intent vs durable-fact distinction

Rules for such LLM layers:
- keep the scope narrow
- require structured outputs
- keep final truth and verification Python-owned
- use them to classify or normalize, not to declare success
- prefer one bounded classifier over many scattered phrasing patches

### 5.4 Repeated Procedures Belong in Skills

If a user-facing job follows a known procedure, that burden wants a skill.

Examples:
- file lookup
- file edit
- code fix
- workspace cleanup
- document question
- search research

Skills express the procedure.
Engines perform the heavy mechanics underneath.

## 6. Target v1 Stack

The intended stack is:

1. Intent / Route Layer
- decides the domain of the request

2. Skill / Workflow Layer
- decides the procedure for this class of request

3. Engine Layer
- performs reusable mechanics

4. Tool Layer
- performs concrete file/search/runtime/model actions

5. Verification Layer
- decides what is proven, partial, or failed

6. Persona Layer
- speaks from verified runtime state

This preserves the current doctrine:
- route -> plan -> act -> verify -> speak

The difference is that plan/act should increasingly compose engines instead of repeatedly inventing mechanics.

## 6.1 Planner Boundary

`Piper v1` also requires a stricter planner boundary.

This is not a seventh engine.
It is the contract between:
- route/workflow selection
- stage execution

The planner boundary should define exactly what enters execution and what execution is allowed to decide for itself.

Planner-boundary input should include:
- objective
- current stage goal
- success condition
- allowed domain/tools
- active targets
- evidence required for completion
- stop condition or clarification requirement

Planner-boundary output should include:
- next concrete action proposal
- justification tied to the current stage
- whether clarification is required
- whether the stage should stop, continue, or retry

Rules:
- the planner may choose the next step inside the allowed stage domain
- the planner may not rewrite the request into a different domain
- the planner may not invent success
- the planner may not bypass verification

Why it matters:
- Piper needs a crisp boundary between \"what kind of job is this\" and \"what is the next step inside this job\"
- without that boundary, route logic, follow-up logic, and executor logic drift back into each other

## 7. Frozen v1 Engine Set

`Piper v1` is now frozen around six engines.

That freeze exists to stop architecture drift.
For v1, do not invent a seventh engine unless this blueprint is explicitly revised first.

The six engines are:
- `ContextPackEngine`
- `StateResolutionEngine`
- `StateMutationEngine`
- `VerificationEngine`
- `FileWorkEngine`
- `SummaryEngine`

Supporting helpers are allowed.
Workflows/skills are allowed.
But they must map back to one of these owners instead of creating a shadow engine.

### 7.1 ContextPackEngine

Purpose:
- build the smallest useful working packet for routing, planning, and persona

Inputs:
- latest user turn
- route decision
- recent session/history tail
- active stage card
- scratchpad summary
- task/event/memory state summaries
- latest verified outcome

Outputs:
- manager pack
- persona pack
- runtime pack
- explicit constraints and active targets

Why it matters:
- this is the main defense against context-window sprawl

Current v1 status:
- active

### 7.2 StateResolutionEngine

Purpose:
- resolve what kind of state-related thing the user means before execution

Inputs:
- latest user turn
- recent user/assistant turns
- pending proposal, if any
- latest runtime context
- current task/event snapshot
- current world-model/transient summaries

Outputs:
- `domain`
- `intent`
- `target`
- `confidence`
- `ask_clarification`
- canonical readonly query, if applicable

Why it matters:
- Piper must stop letting multiple modules guess what `it`, `that`, `remove it`, `done it`, or `remember that fact` mean

Current v1 status:
- partially active through the current follow-up resolver and route-clarity work

### 7.3 StateMutationEngine

Purpose:
- own explicit state-changing work after state meaning is resolved

Inputs:
- resolved state intent
- target state owner
- owned stores/services
- latest verified state snapshot when needed

Outputs:
- stage card or mutation request
- authoritative mutation outcome
- state-owner label
- reroute/retry hints when execution proves the chosen owner was wrong

Why it matters:
- tasks, events, durable knowledge, transient state, and soft intent must not mutate through scattered ad hoc paths

Current v1 status:
- active, but still being hardened

### 7.4 VerificationEngine

Purpose:
- decide what is proven after action

Inputs:
- stage goal
- tool result
- before/after state
- checker evidence
- expected final condition

Outputs:
- `VERIFIED`, `PARTIAL`, or `FAILED`
- `effective_success`
- authoritative log/status
- retry, reroute, or stop recommendation

Why it matters:
- this is the truth boundary that keeps narration from outranking reality

Current v1 status:
- partially implicit across executor/checkers/outcome packaging; not yet extracted cleanly enough

### 7.5 FileWorkEngine

Purpose:
- own disciplined file/code workspace mechanics

Inputs:
- file/code task
- workspace context
- allowed tool domain
- current artifact state

Outputs:
- bounded file-work plan
- structured file/code action requests
- artifact-state evidence prepared for verification

Why it matters:
- file/code work is the highest-risk area for false success and repeated local patch logic

Current v1 status:
- not yet extracted as a clear owner

### 7.6 SummaryEngine

Purpose:
- compress verified work into useful carry-forward state without diary-style noise

Inputs:
- scratchpad
- final outcomes
- verified state deltas
- unresolved constraints and next-step hints

Outputs:
- carry-forward summary
- preserved constraints
- preserved decisions
- cleanup or note-update candidates

Why it matters:
- this is anti-amnesia equipment and the handoff layer between turns

Current v1 status:
- partially implicit; not yet explicit enough

### 7.7 What v1 Is Not Freezing As Standalone Engines

For v1, these burdens should be handled inside the frozen owners unless reality proves otherwise:
- retrieval lives under `ContextPackEngine` and `StateResolutionEngine`
- patch mechanics live under `FileWorkEngine`
- task-loop mechanics stay primarily under the existing executor/orchestrator boundary
- speech shaping stays under persona/output handling unless it becomes a repeated architectural burden on its own

## 8. Early Skills v1 Should Support

These are workflow modules, not engines.

Strong early candidates:
- `FileLookupSkill`
- `FileEditSkill`
- `CodeFixSkill`
- `WorkspaceCleanupSkill`
- `DocumentQuestionSkill`
- `SearchResearchSkill`
- `TaskEventSkill`
- `EngineeringEscalationSkill`

Each skill should define:
- purpose
- trigger profile
- expected procedure
- allowed tool/domain posture
- verification expectation
- persona/reporting posture

## 9. Migration Workflow

This is the operational workflow for the v1 redesign.

### Step 1: Identify repeated burden

Before extracting anything, ask:
- is this logic repeated?
- does it need consistent behavior?
- is the failure cost high?

If the answer is not clearly yes, do not build an engine for it.

### Step 2: Name the burden precisely

Bad names:
- `UniversalEngine`
- `ActionEngine`
- `ReasoningEngine`

Good names:
- `ContextPackEngine`
- `VerificationEngine`
- `PatchEngine`

If the name is vague, the abstraction is probably premature.

### Step 3: Extract one mechanism at a time

Do not redesign five layers at once.

Preferred sequence:
1. extract interface
2. route existing behavior through it
3. prove parity with focused tests
4. remove the old duplicate logic

### Step 4: Migrate one workflow family at a time

Recommended order:
1. file lookup / file read
2. file edit
3. code repair
4. workspace cleanup
5. document question / search research

### Step 5: Verify after every extraction

After each engine or workflow migration:
- run the relevant smoke tests
- confirm no false-success regression
- confirm no route drift regression
- confirm no orphan runtime processes are left behind

### Step 6: Collapse old paths

Do not keep old and new paths around forever.

Once the new engine path proves parity:
- remove the scattered old path
- keep the repo simpler
- rely on `piper_v0` for rollback reference if needed

## 10. Anti-Sidetrack Rules

These rules exist to stop the redesign from dissolving into endless local fixes.

### 10.1 Do Not Chase Every Bug Into Architecture

If a bug is local, fix it locally.
Do not redesign the whole system because one parser or one prompt failed.

### 10.2 Do Not Add Abstractions Without Load

An engine must remove repeated burden.
If it does not remove repeated burden, it is decoration.

### 10.3 Do Not Bypass Verification

No new engine, skill, or helper may weaken Piper’s verification doctrine.

### 10.4 Do Not Build Around the Hope of Infinite Context

Any design that assumes “the model will remember all this if prompted carefully enough” is rejected by default.

### 10.5 Do Not Keep Long-Lived Parallel Architectures

The redesign should converge.
It should not leave permanent duplicate orchestration paths behind.

## 11. Definition of Progress

Work counts as real progress if it does at least one of these:
- removes repeated logic from multiple places
- reduces context payload size in a measurable way
- improves retrieval/packing discipline
- improves verification clarity
- reduces workflow drift on repeated tasks
- makes a repeated behavior easier to regression test

Work is not real progress if it only:
- renames things without reducing burden
- adds wrappers without simplifying behavior
- adds options without converging architecture
- creates fallback-on-fallback complexity

## 12. Definition of Done for v1

`Piper v1` is not “done” when every possible engine exists.

It is done when:
- the core repeated mechanics have clear owners
- file/code workflows are skill-driven and engine-backed
- context packing is explicit and disciplined
- verification remains authoritative
- the system is easier to extend than the current patch-layer shape

## 13. Immediate v1 Priorities

The first practical priorities are now frozen to this order:

1. finish `ContextPackEngine` ownership cleanup
2. finish `StateResolutionEngine` ownership cleanup
3. finish `StateMutationEngine` ownership cleanup
4. extract `VerificationEngine` ownership explicitly
5. extract `FileWorkEngine`
6. extract `SummaryEngine`

That order matters because it attacks:
- context pressure first
- semantic ownership second
- truthfulness third
- file/code risk fourth
- carry-forward discipline last

## 14. Working Rule for Future Sessions

Before doing substantial v1 architecture work:
- read this file
- verify whether the change is a local bugfix or a v1 engine/workflow extraction
- keep changes aligned with the target stack above

If a new change does not clearly serve the v1 philosophy, challenge it before implementing it.

## 15. Short Version

If the long document is forgotten, remember this:

- Piper v0 is the backup
- Piper v1 is the live redesign
- skills own procedures
- engines own repeated mechanics
- verification stays sacred
- context must be packed, not stuffed
- retrieval must be targeted
- old paths should be removed after parity, not kept forever
- architecture must become steel, not ceremony
