# Piper v1 Execution Roadmap

Status: Active
Scope: staged redesign toward engines, explicit workflows, and disciplined context packing

This file turns the v1 blueprint into an execution plan.

## 1. Mission

Transform Piper from a system with many strong but scattered mechanisms into a system with:
- explicit workflows
- reusable engines for repeated heavy mechanics
- disciplined context packing
- preserved truthfulness rails

The redesign must reduce drift, not merely move code around.

## 2. Non-Negotiables

- Keep [AGENTS.md](../../AGENTS.md) doctrine intact.
- Keep verification authoritative.
- Do not keep permanent dual-path architecture behind long-lived switches.
- Use git tag `v1-engine-redesign-complete` as rollback/reference baseline (versions/piper_v0 has been removed from disk; restore with `git checkout v1-engine-redesign-complete -- versions/piper_v0`).
- Remove duplicated old paths after parity is proven.

## 3. Frozen v1 Engine Set

`Piper v1` is now frozen around six engines.

1. `ContextPackEngine`
2. `StateResolutionEngine`
3. `StateMutationEngine`
4. `VerificationEngine`
5. `FileWorkEngine`
6. `SummaryEngine`

For v1, these six are the target.
Do not add a seventh engine unless [BLUEPRINT.md](BLUEPRINT.md) is revised first.

Current status (all complete as of 2026-03-15):
- `ContextPackEngine`: ✓ done
- `StateResolutionEngine`: ✓ done
- `StateMutationEngine`: ✓ done
- `VerificationEngine`: ✓ done
- `FileWorkEngine`: ✓ done
- `SummaryEngine`: ✓ done

For v1, these remain subordinate responsibilities rather than standalone engine targets:
- retrieval lives under `ContextPackEngine` and `StateResolutionEngine`
- patch mechanics live under `FileWorkEngine`
- loop mechanics remain primarily under the executor/orchestrator boundary
- speech shaping remains under persona/output handling

## 3.1 Planner Boundary

`Piper v1` also has one explicit non-engine boundary:
- the planner boundary between route/workflow selection and executor step choice

This boundary must become clearer as the engine work progresses.

Required planner-boundary input:
- objective
- stage goal
- success condition
- allowed domain/tools
- active targets
- evidence required for completion

Required planner-boundary output:
- next action proposal
- clarification vs continue decision
- stop/retry recommendation tied to the current stage

This is a contract problem, not a new engine target.

## 4. Working Sequence

### Phase 0: Baseline Freeze

Status:
- done

Artifacts:
- git tag `v1-engine-redesign-complete` (versions/piper_v0 snapshot; removed from disk 2026-03-17)
- [docs/v1/BLUEPRINT.md](BLUEPRINT.md)
- [docs/v1/checklists](checklists)

Exit criteria:
- v0 snapshot exists
- v1 philosophy is written down
- initial checklists exist

### Phase 1: ContextPackEngine

Status:
- done
- extracted seams on 2026-03-13:
  - `ContextPackEngine` became the shared owner for persona working-set assembly and hidden runtime-context rendering
  - `PromptContextService` became the integration facade over that engine
  - scratchpad-to-persona carry-forward state now flows through explicit pack contracts
  - persona tail rules and direct-answer fast-path selection now flow through explicit directive packs

Primary target:
- make context packing an explicit owner with stable input/output contracts

Exit criteria:
- one clear owner for task/persona working-set assembly
- fewer ad hoc context append sites
- parity on existing file/code/document flows

### Phase 2: StateResolutionEngine

Status: **DONE** (2026-03-15)
- extracted seams on 2026-03-14:
  - bounded LLM follow-up resolution now owns ambiguous follow-ups that kept recurring across task/event/memory paths
  - proposal confirmation, clarification pause, and canonical readonly follow-up queries now pass through explicit resolution logic
  - route clarification and runtime-context parsing helpers were consolidated to support this owner cleanly
- ownership cleanup on 2026-03-15:
  - `looks_like_contextual_remember_followup()` and `looks_like_ambiguous_memory_followup()` moved out of `StateMutationEngine` into `FollowupResolutionEngine` as owned static methods
  - `should_resolve()` no longer calls into `StateMutationEngine` for detection; cross-engine dependency removed
  - `_CONTEXTUAL_REMEMBER_RE` and `_AMBIGUOUS_MEMORY_FOLLOWUP_RE` patterns now live in `followup_resolution.py`
  - `FollowupResolutionEngine`, `RouteClarifier`, and `VerificationEngine` added to `core/engines/__init__.py` exports
- exit criteria met 2026-03-15:
  - ambiguous state follow-ups resolve through `FollowupResolutionEngine` only — no cross-engine bouncing
  - clarification triggers go through `RouteClarifier` only
  - task/event/memory follow-up meaning no longer depends on persona drift

Primary target:
- make one engine responsible for state-related meaning before mutation

Concrete goals:
- resolve:
  - `it`
  - `that`
  - `remove it`
  - `done it`
  - `remember that fact`
  - short readonly follow-ups like `Any tasks?`
- emit one structured resolution result:
  - `domain`
  - `intent`
  - `target`
  - `confidence`
  - `ask_clarification`
  - canonical readonly query, when applicable

Exit criteria:
- ambiguous state follow-ups stop bouncing across multiple interpreters
- clarification triggers from one owner only
- task/event/memory follow-up meaning no longer depends on persona drift

### Phase 3: StateMutationEngine

Status: **DONE** (2026-03-15)
- extracted seams on 2026-03-13 and 2026-03-14:
  - task/event/knowledge mutation outcome packaging is now explicit
  - durable-knowledge intent classification and readonly knowledge vs task/event ownership moved under the engine
  - active state-domain route rewrites moved out of `route_normalizer`
  - route normalization now delegates state-domain meaning to engine-owned entry points
- additional seam extracted on 2026-03-14:
  - state mutation stages now carry explicit structured mutation metadata instead of relying only on English `stage_goal` / `success_condition` text
  - the contract now records owner, entity kind, action, target, and scheduled date/value when applicable
  - `FollowupResolutionEngine` now delegates task/event/memory mutation-card construction back into `StateMutationEngine`, so resolution owns meaning and mutation owns stage shape
- exit criteria met 2026-03-15:
  - one clear mutation owner for task/event/world-model/transient state
  - mutation outcomes are explicit and authoritative via structured `mutation` payloads
  - old duplicated state rewrite paths removed from `route_normalizer`

Primary target:
- make one engine responsible for explicit state change work after resolution

Concrete goals:
- own:
  - task add/complete/delete
  - event add/complete/delete
  - durable knowledge store/remove/query routing
  - transient/soft-intent mutation handoff
  - mutation outcome packaging and reroute hints
- stop generic `SUCCESS` style labels from leaking upward

Exit criteria:
- one clear mutation owner for task/event/world-model/transient state
- mutation outcomes are explicit and authoritative
- old duplicated state rewrite paths are removed

### Phase 4: VerificationEngine

Status: **DONE** (2026-03-15)
- contract defined and engine implemented:
  - `VerificationResult` dataclass and `VerificationEngine` fully implemented in `core/engines/verification.py`
  - full contract documented in `docs/v1/VERIFICATION_ENGINE.md`
  - `VerificationResult` factory methods: `verified()`, `partial()`, `failed()`, `not_required()`
  - checker path priority RULES → LLM → STATE_CHECK → MUTATION implemented
- executor wired:
  - `StageExecutor.__init__` instantiates `self.verification_engine = VerificationEngine(file_checker=self.file_checker)`
  - inline verification block (was lines 982–1032) replaced with `self.verification_engine.should_verify()` + `evaluate()`
  - `_last_file_verdict` kept in sync from `vr.verdict` for backward compat with downstream checks
  - `_last_verification: VerificationResult | None` added as the authoritative typed result

Primary target:
- centralize verified/partial/failed decision ownership

Concrete goals:
- reduce verification logic duplication across:
  - executor
  - file checker
  - state fallback
  - persona handoff
- standardize stage outcome packets

Exit criteria:
- explicit verification boundary
- clearer stage outcome contracts
- no regression on known file/code/state smokes

### Phase 5: FileWorkEngine

Status: **DONE** (2026-03-15)
- contract defined 2026-03-15: [FILEWORK_ENGINE.md](FILEWORK_ENGINE.md)
- audit complete: duplication mapped across executor.py, file_checker.py, file_checker_rules.py, file_stage_policy.py
- implemented 2026-03-15: `core/engines/file_work.py`
- exit criteria met 2026-03-15:
  - `executor.py` no longer holds evidence-extraction, path-extraction, or code-view methods (9 methods removed)
  - `file_checker.py` uses `FileWorkEngine.candidate_paths()` instead of its own `_candidate_paths_from_evidence`
  - code extension constant defined once in `core/file_extensions.py`; `file_stage_policy.py` and `executor.py` import from there
  - recovery hint consolidated in `FileWorkEngine.recovery_hint()` — one call site in executor
  - `FileWorkEvidence`, `FileWorkBlock`, `FileStageKind` in contracts.py
  - `FileWorkEngine` added to `core/engines/__init__.py` exports
  - smoke test `scripts/file_work_engine_smoke_test.py` — all 28 cases pass
  - full regression pack passes (consolidate_exclusion, extension_reorg, file_stage_policy smokes)
- deferred to Phase 6 or future cleanup:
  - `file_checker_rules.py` `_stage_*` methods delegating to `FileStagePolicy` (low risk, no duplication harm)
  - `route_normalizer.py` code extension constant (has extra types: `.cs`, `.lua`, `.sh`)

Primary target:
- remove file/code evidence-handling mechanics from the executor loop

Exit criteria:
- executor.py no longer holds any evidence-extraction, path-extraction, or code-view methods ✓
- file_checker.py imports candidate_paths from the engine ✓
- one code extension definition site ✓
- one recovery hint call site ✓
- no regression on the full smoke test pack ✓

### Phase 6: SummaryEngine

Status: **DONE** (2026-03-15)
- contract defined 2026-03-15: [SUMMARY_ENGINE.md](SUMMARY_ENGINE.md)
- audit complete: duplication mapped across `ContextPackEngine`, `ScratchpadFormatter`, `PromptBuilder`, and `PromptContextService`
- implemented 2026-03-15: `core/engines/summary.py` (490 lines, 14 public methods)
- exit criteria met 2026-03-15:
  - all scratchpad-level extraction methods (`latest_stage_entries`, `extract_verified_result`, `extract_proposal`, `extract_exact_file_read`, `extract_file_lookup`, `extract_stage_status`) owned by `SummaryEngine`
  - carry-forward pipeline (`build_runtime_note`) owns priority chain: verified result → exact-read path → file-lookup brief → LAST_LOG → OBSERVATION_TEXT
  - `_is_generic_file_work_summary` deduplication resolved: single definition in `SummaryEngine`; removed from both `ContextPackEngine` and `ScratchpadFormatter`
  - `_truncate_text` / `_truncate_scratchpad` consolidated into `SummaryEngine.truncate_text` / `truncate_scratchpad`; removed from `ScratchpadFormatter` and `PromptBuilder`
  - `_scratchpad_exact_read_paths` removed from `PromptBuilder`; replaced with `FileWorkEngine.exact_read_paths_from_scratchpad`
  - `PromptContextService` 5 delegation methods now point directly to `SummaryEngine`
  - `SummaryEngine` added to `core/engines/__init__.py` exports
  - zero external engine dependencies in `SummaryEngine` (imports only stdlib)
  - smoke test `scripts/summary_engine_smoke_test.py` — all 42 cases pass

Primary target:
- standardize carry-forward summaries and post-execution compression

Concrete goals:
- preserve:
  - verified result
  - constraints
  - unresolved risks
  - next actionable step
- reduce scattered post-turn carry-forward logic

Exit criteria:
- one clear owner for all scratchpad-level extraction logic ✓
- carry-forward pipeline explicit and authoritative ✓
- no duplicate `_is_generic_file_work_summary`, `_truncate_text`, or related helpers ✓
- zero external engine dependencies in `SummaryEngine` ✓
- no regression on full smoke test pack ✓

## 5. Per-Phase Workflow

Every phase should follow the same cycle:

1. define the repeated burden
2. identify current owners and duplication
3. define the contract
4. add focused regression coverage
5. route current behavior through the new owner
6. prove parity
7. remove old duplicate path
8. update docs/notes

## 6. Success Signals

Signs the redesign is working:
- similar tasks behave more consistently
- less prompt sprawl for repeated flows
- fewer one-off normalization/checker hacks
- smaller and more explicit context packets
- easier debugging because ownership is clearer
- targeted tests cover workflow families rather than isolated symptoms only

## 7. Failure Signals

Signs the redesign is going wrong:
- more wrappers without less duplication
- more feature switches instead of fewer paths
- engines with vague names and fuzzy scope
- rising test count but lower clarity
- verification becoming less central
- new abstractions that still require patching the same old places

## 8. Recommended Immediate Start

All 6 phases of the v1 engine redesign are complete. The frozen six-engine set is fully extracted and documented.

Possible next directions:
- harden planner boundary contracts (§3.1) — input/output contract is still informal ✓ done (PlannerBoundary implemented; typed schema validation at all LLM boundaries done — see TRIGGER_FLOW.md §13.5)
- wire `VerificationEngine` more deeply into the persona handoff path ✓ done (orc.last_verification flows through build_persona_runtime_pack())
- address the pre-existing `context_pack_engine_smoke_test` workspace-state dependency (`grocery_list.txt` must exist on disk for path normalization tests to pass) — still open

Do not start with:
- inventing new engines outside the frozen six (see post-v1 note below)
- a universal engine framework
- large directory shuffles
- replacing executor/orchestrator before contracts are clear

**Post-v1 engine additions:** The six-engine freeze applies to the v1 redesign scope. Planned architectural improvements beyond v1 are tracked in `docs/architecture/TRIGGER_FLOW.md` §13. One additional engine has been approved there: `ConversationCompressor` (`core/engines/conversation_compressor.py` — see §13.3). This does not violate the v1 freeze; it is a post-v1 addition. Any further new engines must be documented in TRIGGER_FLOW.md §13 before being coded.

## 9. Definition of Done For This Roadmap

This roadmap has served its purpose when:
- the next v1 work can be chosen by phase instead of improvisation
- each phase has a concrete goal and exit criteria
- future sessions can quickly recover why the redesign exists and how to continue it
