# V1 Guardrails Checklist

Use this before and during v1 architecture work.

## Before Starting a Change

- State the repeated burden being targeted in one sentence.
- Decide whether this wants an engine, a workflow/skill, or a local bugfix.
- If it wants an engine, name which one of the frozen v1 six owns it.
- If it is planner/executor work, state the planner boundary explicitly before changing behavior.
- Name the target clearly.
- Reject vague names like `UniversalEngine`, `ActionEngine`, or `ReasoningEngine`.
- List the invariants that must remain true after the change.
- Identify the current owner files.
- Identify the tests or smokes that must prove parity.

## During the Change

- Extract one repeated mechanic at a time.
- Keep one concern per active owner where practical:
  - state meaning/classification
  - mutation/execution
  - verification
  - prompt/rendering
- Keep the planner boundary explicit:
  - route/workflow decides the job class
  - planner decides the next step inside the allowed stage
- Do not invent a new v1 engine outside the frozen set unless the blueprint is revised first.
- Keep verification authority intact.
- Keep route -> plan -> act -> verify -> speak intact.
- Avoid permanent dual-path architecture.
- Do not add a long-lived bypass switch just to avoid convergence.
- Prefer removing duplicate logic over wrapping duplicate logic.
- Keep prompt rendering pure; do not move side effects into prompt builders.
- Keep lower layers independent of higher layers.
- If two modules are still interpreting the same user intent or reading/writing the same shared state for the same purpose, consolidate to one owner instead of adding another rescue path.
- If a helper is reused across owners, extract it into a shared utility module instead of copying it sideways.
- Keep changes scoped to one phase target where possible.

## When You Feel Drift Starting

- Ask: is this a local bugfix or an architecture extraction?
- Ask: what repeated burden is actually being removed?
- Ask: am I reducing duplication, or renaming it?
- Ask: am I weakening verification to make the refactor easier?
- Ask: is this ambiguity genuinely linguistic, and am I starting to hardcode too many phrasings to rescue it?
- If repeated regex/heuristic rescue is piling up for the same intent class, stop and consider a bounded LLM classifier instead.
- If the answer is unclear, stop broadening the change.

## Before Calling a Refactor Successful

- Focused regression coverage exists.
- Existing high-value smokes still pass.
- Old duplicate path is removed or clearly scheduled for removal next.
- Ownership is clearer after the change than before:
  - fewer cross-layer decisions
  - fewer sideways imports
  - fewer modules interpreting the same semantic class
- Docs are updated:
  - [BLUEPRINT.md](../BLUEPRINT.md) if philosophy changed
  - [V1_EXECUTION_ROADMAP.md](../V1_EXECUTION_ROADMAP.md) if phase status changed
  - [notes/coder-log.md](../../../notes/coder-log.md)
  - [notes/known-good.md](../../../notes/known-good.md) or [notes/known-issues.md](../../../notes/known-issues.md) as needed
- No stray test or model processes are left behind.

## Hard Stops

Do not proceed with a broad extraction if:
- you cannot name the repeated burden precisely
- you cannot describe how verification stays authoritative
- you cannot name the parity tests
- the change only adds indirection
- the change leaves two architectures alive with no plan to converge
