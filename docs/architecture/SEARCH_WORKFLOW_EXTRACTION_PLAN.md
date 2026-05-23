# Search Workflow Extraction Plan

> **Status:** Stage 02 — documentation + tests only. No runtime behavior changed.
> **Branch:** `audit/stage-02-search-workflow-inventory`
> **Owner:** core search lifecycle

This document is the pre-extraction blueprint for extracting `SearchWorkflowEngine` from `core/orchestrator_phases.py`. It records live-code truth as of the inventory date and prescribes safe boundaries for future extraction stages.

---

## 1. Current Search Lifecycle

The search workflow spans **three turns** and **five files**:

```
Turn 1 — User requests search
├─ ROUTE: route_normalizer detects explicit web-search intent
│   ├─ _registered_explicit_web_search → decision=SEARCH
│   └─ _registered_web_search_offer_affirmative_followup → decision=SEARCH (offer → yes followup)
├─ phase_search (orchestrator_phases.py)
│   ├─ Build first-pass persona preview (SEARCH_FIRST_PASS_RULE tail block)
│   ├─ Stream preview to user while search runs
│   ├─ Spawn background thread → perform_search(tools/search.py)
│   └─ next_stage = FINISHED (turn ends; search continues in background)
│
Turn 2 — Background search completes (async, no user input)
├─ perform_search returns data
├─ UI event "search_result" → handle_search_result (controller_actions.py)
│   ├─ Append hidden system message: [Background search complete for 'X'. Data: ...]
│   ├─ Append hidden system message: [The web search is complete. Summarize...]
│   └─ Trigger internal reporter turn → run_agent_loop
├─ ROUTE: _is_pending_search_payload detected in recent history
│   └─ Skip Secretary/router LLM; next_stage = REPORTER
├─ phase_reporter (orchestrator_phases.py)
│   ├─ Parse background search payload
│   ├─ On failure: build honest failure summary (no LLM)
│   ├─ On success: run reporter.txt LLM prompt to summarize findings
│   ├─ Replace payload + instruction messages with [SEARCH SUMMARY FOR 'X'] and [SEARCH REPORT CONSUMED FOR 'X']
│   └─ next_stage = PERSONA
├─ phase_persona (orchestrator_phases.py)
│   ├─ Context pack sees reporter_just_ran=True → SEARCH_REPORT_RULE tail block
│   ├─ Runtime context pack includes search_query, search_failed, search_error
│   └─ Persona speaks final answer extending/correcting the preview
```

**Key insight:** The workflow is inherently **multi-turn and async**. Any extraction must preserve the handoff between the background thread, UI queue, controller actions, and orchestrator phases. The engine cannot be a simple synchronous function.

---

## 2. Responsibilities Inside `phase_search`

| # | Responsibility | Current Location | Notes |
|---|---------------|------------------|-------|
| 2.1 | Live-environment query downgrade (search → CHAT) | `phase_search` | Safety guard; must not be lost |
| 2.2 | Stats routing (decision=SEARCH, query tracking) | `phase_search` via `stats_collector` | Owned by stats layer |
| 2.3 | Persona pack build + context arbitration for preview | `phase_search` via `prompt_context` | Reuses persona infrastructure |
| 2.4 | SEARCH_FIRST_PASS_RULE tail block assembly | `_build_search_first_pass_rule` (private helper) | Recency-sensitive logic |
| 2.5 | First-pass fallback text generation | `_build_search_first_pass_fallback` | Stripped query prefix |
| 2.6 | Preview history trimming | `_build_search_preview_history` | Minimal history for token economy |
| 2.7 | Stream preview answer (or emit fallback on recency) | `phase_search` | LLM call; cancel-aware |
| 2.8 | Cancel token retain/release for background thread | `phase_search` | Lifecycle hygiene |
| 2.9 | `search_in_flight` retain/release | `phase_search` | Prevents overlapping searches |
| 2.10 | Background thread spawn → `perform_search` | `phase_search` | Threading boundary |
| 2.11 | Error handling + UI event queuing for background result | `_do_search` closure inside `phase_search` | `search_result` event with error flag |
| 2.12 | Stats defer for deferred search turn | `stats_collector.defer_search_turn` | Stats layer concern |

---

## 3. Responsibilities Inside `phase_reporter`

| # | Responsibility | Current Location | Notes |
|---|---------------|------------------|-------|
| 3.1 | Detect pending search payload in recent history | `_is_pending_search_payload` + `parse_background_search_content` | Contract parsing |
| 3.2 | Detect reporter instruction marker | `_is_search_reporter_instruction` | Contract parsing |
| 3.3 | Parse query/data/failed from payload | `parse_background_search_content` | Pure function; already in `search_contracts.py` |
| 3.4 | Stats reporter query tracking | `stats_collector.note_reporter_query` | Stats layer |
| 3.5 | Failure path: build honest failure summary | `_build_search_failure_summary` | No LLM; deterministic |
| 3.6 | Failure path: stats outcome=FAILED | `stats_collector.finalize_outcome` | Stats layer |
| 3.7 | Success path: load reporter.txt template | `phase_reporter` | File I/O |
| 3.8 | Success path: run reporter LLM summarization | `orc.llm.generate` | LLM call; cancel-aware |
| 3.9 | Success path: error fallback to raw data | `phase_reporter` exception handler | Graceful degradation |
| 3.10 | Replace hidden system messages with summary + consumed markers | `orc.chat.replace_last_system_message` | Chat state mutation |
| 3.11 | Set `latest_search_summary`, `latest_search_failed`, `latest_search_error`, `reporter_just_ran` on orc | `phase_reporter` | Orc state mutation |
| 3.12 | Stats end_phase("reporter") | `stats_collector` | Stats layer |

---

## 4. Responsibilities in Route Detection for Pending Search Payloads

| # | Responsibility | Current Location | Notes |
|---|---------------|------------------|-------|
| 4.1 | Detect SEARCH result in recent history | `_run_route_core` → `_is_pending_search_payload` | Pre-router short-circuit |
| 4.2 | Skip Secretary/router LLM | `_run_route_core` | Decision = SEARCH from context, not model |
| 4.3 | Set `orc.is_search_result = True` | `_run_route_core` | Flag used downstream |
| 4.4 | Route to REPORTER stage | `_run_route_core` → `next_stage = "REPORTER"` | Hard stage transition |
| 4.5 | Search-in-flight collision detection | `_run_route_core` or controller | If user sends another search while one is in flight, emit polite refusal |

---

## 5. Responsibility Ownership Decision Matrix

### 5.1 SearchWorkflowEngine-owned (registry + direct-call hybrid)

These behaviors are **tightly coupled to the search lifecycle** and benefit from being co-located in a single module with explicit state machine semantics.

| Responsibility | Rationale |
|---------------|-----------|
| First-pass rule assembly (2.4) | Search-specific prompt logic; pure function, testable |
| First-pass fallback text (2.5) | Search-specific prompt logic; pure function, testable |
| Preview history trimming (2.6) | Search-specific history shape; pure function, testable |
| Failure summary builder (3.5) | Search-specific honest-failure prose; pure function, testable |
| Search error normalization / summarization (3.5, 3.11-adjacent) | Reusable across search and general error paths |
| Payload parsing / instruction detection (3.1–3.3) | Already in `search_contracts.py`; engine should own the contract |
| Search lifecycle state machine (in-flight, pending result, completed) | Currently implicit in `orc` attributes; explicit state machine is safer |
| Reporter message replacement orchestration (3.10) | Encapsulates the chat-mutation contract |

### 5.2 Direct-call / Service-owned (remain in orchestrator/UI)

These behaviors are **cross-cutting concerns** that must not be hidden inside a search-specific engine.

| Responsibility | Rationale |
|---------------|-----------|
| Live-environment downgrade (2.1) | Routing safety guard; belongs in route layer |
| Stats routing / phase tracking (2.2, 3.4, 3.6, 3.12) | StatsCollector is already a service; keep it there |
| Persona pack build + context arbitration (2.3) | PromptContextService / ContextPackEngine owns this |
| Stream preview answer (2.7) | Persona streaming infrastructure; not search-specific |
| Reporter LLM call (3.8) | Generic `llm.generate`; caller supplies cancel token |
| Cancel token lifecycle (2.8) | Orchestrator owns cancellation semantics |
| Background thread spawn (2.10) | Orchestrator / executor own threading policy |
| `search_result` UI event queuing (2.11) | UI boundary concern |
| Chat state mutation (3.10 raw calls) | ChatState repository owns persistence |
| Route detection for pending payload (4.1–4.4) | Router / route normalizer domain |
| Search-in-flight collision guard (4.5) | Router or controller boundary |

### 5.3 UI / Boundary-owned (remain in controller)

| Responsibility | Rationale |
|---------------|-----------|
| `handle_search_result` event handler | Controller owns UI event dispatch |
| Hidden message append + internal turn trigger | Controller owns turn lifecycle |
| `gen_lock` acquire for reporter turn | Controller owns operation serialization |

---

## 6. What Must Not Change During Extraction

1. **The three-turn lifecycle must remain intact.** First-pass preview → background search → reporter → persona final answer. No collapsing into a single synchronous call.
2. **The hidden system message contract must not change.** `build_background_search_content` format, reporter instruction markers, and `[SEARCH SUMMARY FOR ...]` / `[SEARCH REPORT CONSUMED FOR ...]` markers are consumed by downstream logic (context pack, history trimming, debug logging).
3. **The `search_contracts.py` wire format is the API.** Any engine must speak in terms of `BackgroundSearchPayload`, `is_background_search_payload`, `is_search_reporter_instruction`, etc.
4. **Stats tracking must remain externally visible.** `defer_search_turn`, `note_reporter_query`, `finalize_outcome` must still be called with the same semantics.
5. **Cancel token behavior must remain identical.** Background thread must release the token on both success and failure.
6. **Search-in-flight collision guard must remain.** A second search while one is running must be refused politely.
7. **Error path must stay honest.** On failure, no LLM call in reporter; deterministic failure summary; persona must say "verified web findings: none."
8. **Context arbitration profiles must stay aligned.** `SEARCH_FIRST_PASS` and `REPORTER` turn types in `PERSONA_CONTEXT_ARBITRATION_TABLE` must remain valid.
9. **Do not remove the legacy loop.** The LangGraph path is not yet proven for the search async pattern. Legacy `phase_search` / `phase_reporter` must remain callable until burn-in is complete.
10. **Do not touch `tools/search.py`.** `perform_search` is a tool, not an engine. Its interface stays unchanged.

---

## 7. Proposed SearchWorkflowEngine Boundaries

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SearchWorkflowEngine (proposed: core/engines/search_workflow.py)       │
│                                                                         │
│  Pure helpers (no I/O, no LLM):                                         │
│    - build_first_pass_rule(query, recency_hint=False) → str            │
│    - build_first_pass_fallback(query) → str                            │
│    - build_search_preview_history(user_msg, query) → list[dict]        │
│    - build_search_failure_summary(query, error_text) → str             │
│    - summarize_search_error_for_user(error_text) → str                 │
│    - build_search_in_flight_reply(active_query, requested_query) → str │
│                                                                         │
│  Contract helpers (pure, already in search_contracts.py):              │
│    - is_background_search_payload(content) → bool                      │
│    - is_search_reporter_instruction(content) → bool                    │
│    - parse_background_search_content(content) → BackgroundSearchPayload│
│    - build_background_search_content(...) → str                        │
│    - normalize_search_error(value) → str                               │
│                                                                         │
│  State machine (holds in-flight / pending / completed state):          │
│    - retain_in_flight(query) / release_in_flight()                     │
│    - can_accept_new_search() → bool                                    │
│    - current_in_flight_query() → str | None                            │
│                                                                         │
│  Reporter orchestration (called by phase_reporter):                    │
│    - prepare_reporter_turn(orc) → ReporterTurnContext                  │
│      (parses payload, decides failure vs success path,                 │
│       loads reporter template, returns structured context)              │
│    - finalize_reporter_turn(orc, summary, query, failed)               │
│      (sets orc attributes, replaces system messages)                    │
│                                                                         │
│  Registry hooks (future — not in this stage):                          │
│    - @register_hook("on_pre_route") → detect pending payload           │
│    - @register_tail_block → SEARCH_FIRST_PASS_RULE, SEARCH_REPORT_RULE │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Note:** The engine is a **hybrid** (per Stage 01 doctrine). It will expose direct-call helpers for the orchestrator to use, and in a future stage may register tail blocks and a pre-route hook to make the workflow more self-contained.

---

## 8. Proposed Public Methods (Phase 3+ implementation)

```python
class SearchWorkflowEngine:
    """Encapsulates search lifecycle helpers and state tracking.

    This is a hybrid module: direct-call helpers for orchestrator use,
    plus (future) registry-driven hooks for tail blocks and route detection.
    """

    # ── Pure helpers ──
    def build_first_pass_rule(self, query: str) -> str: ...
    def build_first_pass_fallback(self, query: str) -> str: ...
    def build_preview_history(self, user_msg: str, query: str) -> list[dict[str, str]]: ...
    def build_failure_summary(self, query: str, error_text: str) -> str: ...
    def summarize_error_for_user(self, error_text: str) -> str: ...
    def build_collision_reply(self, active_query: str, requested_query: str) -> str: ...

    # ── State machine ──
    def retain_in_flight(self, query: str) -> None: ...
    def release_in_flight(self) -> None: ...
    def is_in_flight(self) -> bool: ...
    def current_query(self) -> str | None: ...

    # ── Reporter turn orchestration ──
    def prepare_reporter_context(
        self,
        recent_history: list[dict],
    ) -> ReporterTurnContext: ...

    def finalize_reporter_turn(
        self,
        orc,
        *,
        summary: str,
        query: str,
        failed: bool,
        raw_payload_content: str,
        raw_instruction_content: str,
    ) -> None: ...
```

**What stays in orchestrator phases:**
- The actual `llm.generate` call for streaming the preview
- The actual `llm.generate` call for reporter summarization
- The `threading.Thread` spawn
- The `perform_search` tool invocation
- Stats collector calls
- Cancel token retain/release

---

## 9. Validation Checklist for Future Stages

### Stage 03 — Extraction (code move, no behavior change)
- [ ] `python -m compileall core` passes
- [ ] `python -m pytest tests/test_search_contracts.py -q` passes
- [ ] `scripts/search_error_contract_smoke_test.py --json` reports `success: true`
- [ ] `scripts/search_flow_smoke_test.py --json` reports `success: true`
- [ ] `scripts/search_thread_cleanup_smoke_test.py --json` reports `success: true`
- [ ] `scripts/search_tool_fallback_smoke_test.py --json` reports `success: true`
- [ ] `scripts/search_prompt_isolation_smoke_test.py --json` reports `success: true`
- [ ] No diff in `tools/search.py`
- [ ] No diff in `core/routing/route_normalizer.py` search route detection
- [ ] No diff in `ui/controller_actions.py` `handle_search_result`

### Stage 04 — Registry integration (optional, post-burn-in)
- [ ] `@register_tail_block` for `SEARCH_FIRST_PASS_RULE` and `SEARCH_REPORT_RULE`
- [ ] `@register_hook("on_pre_route")` for pending payload detection
- [ ] Legacy loop still callable and still passes smoke tests
- [ ] LangGraph path proven for at least 20 live search turns without regression

---

## 10. Manual Hands-On Test Checklist (Runtime Validation)

Run these manually in a live Piper session before declaring any extraction stage complete:

1. **Explicit search:** "Search the web for Python 3.14 release date"
   - Expect: first-pass preview streamed, then auto-delivered summary, then extended answer.
   - Expect: no "shall I proceed" or "would you like me to continue" in preview.

2. **Recency-sensitive search:** "What is the latest news on SpaceX Starship?"
   - Expect: preview is brief and defers factual claims.
   - Expect: no stale dates claimed from memory.

3. **Search failure simulation:** (block network or force 403)
   - Expect: honest failure message, no hallucinated results.
   - Expect: "verified web findings: none." in persona output.

4. **Overlapping search collision:** Send a second search while first is in flight.
   - Expect: polite refusal, no duplicate background threads.

5. **Search offer followup:** Assistant offers to search → user says "yes please"
   - Expect: route normalizer intercepts, SEARCH decision, full workflow runs.

6. **Reporter error fallback:** Force reporter LLM to fail (e.g., unplug model)
   - Expect: raw search data passed through to persona without crashing.

7. **Cancel during search:** Press Stop while background search is running.
   - Expect: search thread terminates cleanly, cancel token released, no zombie threads.

8. **Context arbitration verification:** Check persona debug logs.
   - Expect: SEARCH_FIRST_PASS has [WORLD STATE] but not [OPERATIONAL STATE].
   - Expect: REPORTER/PERSONA has [SEARCH_REPORT_RULE] but not [WORLD STATE].

---

## 11. Live-Code Findings That Shaped This Plan

1. **There are no unit tests for `search_contracts.py`.** The only search tests are integration-level smoke tests in `scripts/` that require a full harness. This stage adds `tests/test_search_contracts.py` to close the gap.

2. **`parse_background_search_content` has fallback parsing for mixed markers.** If a failure-prefix payload contains `"Data:\n"` instead of `"Error:\n"`, it falls back to splitting on `"Data:\n"`. This is defensive and must be preserved.

3. **`is_search_error_result` is called in two places:** (a) inside `_do_search` closure in `phase_search` to decide whether to raise, and (b) inside `parse_background_search_content` to set `failed=True` when the data itself starts with `"Search Error:"`. Both must stay consistent.

4. **`_build_search_failure_summary` and `_summarize_search_error_for_user` are separate.** The former builds the hidden system summary; the latter builds user-facing failure prose. They must not be merged.

5. **`phase_reporter` replaces **two** system messages:** the raw payload and the reporter instruction. It does not simply append. This replacement contract is relied on by history trimming in `_build_search_report_history`.

6. **`_build_search_report_history` only keeps the latest `[SEARCH SUMMARY FOR ...]`** and the current user message. This trimming is critical for token economy during the persona turn.

7. **The search workflow is the only place where `defer_search_turn` is used.** StatsCollector uses this to merge the background search timing into the original turn stats. Any extraction must preserve this call site.

8. **`handle_search_result` in `controller_actions.py` slices data to 16,000 chars** before building the background search content. This is a hard UI/chat limit, not a search contract concern. The engine must not assume unlimited payload size.

---

## 12. Risks and TODOs

| Risk | Mitigation | Owner |
|------|-----------|-------|
| Moving too much into the engine makes it a "god object" | Strict boundary: no LLM calls, no thread spawn, no stats, no UI events inside engine | Future extraction stage |
| Registry migration for tail blocks breaks context pack ordering | Register with explicit priority; compare before/after persona debug logs | Future registry stage |
| Legacy loop removal before LangGraph burn-in | **Hard rule:** keep legacy loop until 20+ live search turns pass in LangGraph mode | Architecture review |
| Private helpers in `orchestrator_phases.py` are hard to test without extraction | This stage documents them; Stage 03 extracts the pure ones into the engine | Stage 03 |
| `search_contracts.py` currently has no tests | **Closed in this stage** via `tests/test_search_contracts.py` | This stage |
| Controller-level search-in-flight guard may be duplicated in router | Verify single source of truth before extraction | Future audit |

---

## 13. Compatibility-Shim Rule (from Stage 01 doctrine)

If `SearchWorkflowEngine` is created in a new file, the old private helpers in `orchestrator_phases.py` must remain as **thin wrappers** calling the new engine for at least one full release cycle. This ensures:
- Existing debug scripts that monkey-patch `orchestrator_phases._build_search_first_pass_rule` continue to work.
- Smoke tests that patch `phase_search` internals have a migration path.
- Rollback is possible by reverting the wrapper-to-engine import.

---

*End of plan. No runtime code was changed in this stage.*
