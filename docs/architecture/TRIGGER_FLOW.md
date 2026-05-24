# Piper — Trigger Flow Reference

Status: Active · Prescriptive
Authoritative doctrine: `AGENTS.md`
This document is the **optimized target spec** for Piper's runtime flow. Code must conform to this document. If the code and this document diverge, file a bug against the code.

> **Dual runtime:** Piper has two orchestrator implementations. The LangGraph runtime (`core/orchestrator_graph_builder.py`) is the default. The legacy `while`-loop runtime (`core/orchestrator_phases.py`) remains available via `PIPER_USE_LANGGRAPH_ORCHESTRATOR=false`. Both runtimes execute the same phase logic and produce identical outcomes — the LangGraph path adds checkpoint persistence, interrupt/resume, and visual debugging. This document describes both paths; graph-specific behaviour is called out explicitly.

---

## 1. Top-Level Turn Flow

```
User input
    │
    ▼
UI input path: controller.py → controller_actions.py
    │  controller_actions.py calls run_agent_loop()
    ▼
Orchestrator (orchestrator.py + orchestrator_phases.py)
    │  state-machine loop: orc.next_stage drives phase dispatch
    ▼
phase_route  ──────────────────────────────────────────────────────┐
    │                                                              │
    │  Pre-LLM bypass checks (no model call):                      │
    ├── pending search payload in history? ──────► next: REPORTER  │
    ├── proactive trigger? ──────────────────────► next: PERSONA   │
    ├── route interceptor (UNDO/REMINDER/EXPLAIN)? ─► interceptor  │
    ├── environment query (date/time/day)? ───────► next: PERSONA  │
    ├── operational state query (events/tasks)? ─► next: PERSONA   │
    ├── document chat heuristic? ────────────────► next: DOC_FOCUS │
    └── live screen visual query? ───────────────► next: PERSONA   │
    │                                                              │
    │  Router LLM call (if no pre-LLM match)                       │
    │  → normalize_route_decision()                                │
    │  → _resolve_followup_route_with_llm()                        │
    │  → _refine_ambiguous_task_route_with_llm()                   │
    │  → apply_route_skill_layer()                                 │
    │                                                              │
    ├── CHAT  ────────────────────────────────────► next: PERSONA  │
    ├── SEARCH ───────────────────────────────────► next: SEARCH   │
    └── TASK  ────────────────────────────────────► next: MANAGER  │
                                                                   │
    ┌──────────────────────────────────────────────────────────────┘
    │
    ├── UNDO: phase_undo ──────────────────────────► next: PERSONA
    │       (interceptor path — fires before router LLM when UNDO interceptor set)
    │
    ├── REMINDER_SET: phase_reminder_set ─────────► next: PERSONA
    │       (interceptor path — fires before router LLM when REMINDER_SET interceptor set)
    │
    ├── EXPLAIN: phase_explain ────────────────────► next: PERSONA
    │       (interceptor path — fires before router LLM when EXPLAIN interceptor set)
    │
    ├── REPORTER: phase_reporter ──────────────────► next: PERSONA
    │
    ├── DOC_FOCUS: phase_document_focus ───────────► next: PERSONA
    │
    ├── SEARCH: phase_search ──────────────────────► next: FINISHED
    │       (async — search thread completion auto-triggers internal reporter turn
    │        via controller_queue.py → controller_actions.py → run_agent_loop(),
    │        no user input required)
    │
    ├── MANAGER: phase_manager
    │       │  executor.run()  (stage loop — PlannerBoundary.validate_input()
    │       │                   runs at the start of each stage inside executor)
    │       ├──────────────────────────────────────► next: PERSONA
    │       └── auto_reroute on failed stage ──────► next: ROUTE
    │
    └── PERSONA: phase_persona
            ├── normal ────────────────────────────► next: FINISHED
            └── [ROUTER] self-correction ──────────► next: ROUTE
                (guarded — see §7)
```

---

### Runtime Ownership Note

Piper is Windows-first at runtime. The repo-root `.venv` is part of that Windows
runtime surface and must remain a Windows-created virtualenv.

- Safe: run Piper from PowerShell / `cmd.exe` using `C:\Projects\Piper\.venv`
- Safe: use WSL for analysis, repo reads, code edits, and harness work that does not
  replace the Windows runtime env
- Safe: if WSL needs its own Python packages, create a separate env such as
  `.venv-wsl`
- Unsafe: recreating repo-root `.venv` from WSL (`/usr/bin/python... -m venv .venv`)
  because it rewrites `.venv/pyvenv.cfg` to Linux paths and breaks PowerShell launches
  with `No Python at '/usr/bin\\python.exe'`

This is a runtime ownership rule, not part of the Route → Plan → Act → Speak turn
graph, but it is mandatory for keeping the shipped Windows entrypoint working.

---

## 1.5 LangGraph Runtime (when `USE_LANGGRAPH_ORCHESTRATOR=True`)

When the feature flag is set, `Orchestrator.run()` delegates to `_run_langgraph()` instead of the legacy `while` loop. The graph is compiled once per turn from `core/orchestrator_graph_builder.py` and invoked via `graph.invoke(state, config)`.

### Graph structure

```
START
  │
  ▼
ROUTE  ──[conditional]──► MANAGER       (if TASK)
  │                         │
  ├───────────────────────► SEARCH       (if SEARCH)
  ├───────────────────────► REPORTER     (pending search payload)
  ├───────────────────────► DOC_FOCUS    (document-chat bypass)
  ├───────────────────────► UNDO         (undo interceptor)
  ├───────────────────────► REMINDER_SET (reminder interceptor)
  ├───────────────────────► EXPLAIN      (explain interceptor)
  └───────────────────────► PERSONA      (if CHAT)
                            │
                            ▼
                          VERIFY
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
      AWAIT_INTERRUPT    MANAGER      PERSONA
              │          (resume)     (normal)
              └──────────► VERIFY ◄────┘
                            │
                            ▼
                          PERSONA
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
             END          ROUTE        MANAGER
                        (reroute)     (reroute)
```

**Nodes:**

| Node | File | Delegates to |
|---|---|---|
| `ROUTE` | `core/graph_nodes.py` → `route_node()` | `_run_route_core()` in `orchestrator_phases.py` |
| `DOC_FOCUS` | `core/graph_nodes.py` → `document_focus_node()` | `dispatch_stage("DOC_FOCUS")` |
| `SEARCH` | `core/graph_nodes.py` → `search_node()` | `dispatch_stage("SEARCH")` |
| `REPORTER` | `core/graph_nodes.py` → `reporter_node()` | `dispatch_stage("REPORTER")` |
| `MANAGER` | `core/graph_nodes.py` → `manager_node()` | `_run_manager_core()` in `orchestrator_phases.py` |
| `UNDO` | `core/graph_nodes.py` → `undo_node()` | `dispatch_stage("UNDO")` |
| `REMINDER_SET` | `core/graph_nodes.py` → `reminder_set_node()` | `dispatch_stage("REMINDER_SET")` |
| `EXPLAIN` | `core/graph_nodes.py` → `explain_node()` | `dispatch_stage("EXPLAIN")` |
| `VERIFY` | `core/graph_nodes.py` → `verify_node()` | Reads `orc.last_verification` + pending interrupt state |
| `AWAIT_INTERRUPT` | `core/graph_nodes.py` → `await_interrupt_node()` | Calls `langgraph.types.interrupt()`, applies resume helper |
| `PERSONA` | `core/graph_nodes.py` → `persona_node()` | `_run_persona_core()` in `orchestrator_phases.py` |

**Conditional edges:**

- `ROUTE → {DOC_FOCUS, SEARCH, REPORTER, MANAGER, UNDO, REMINDER_SET, EXPLAIN, PERSONA}` — based on `orc.next_stage` first, with `route_decision.decision` only as fallback
- `DOC_FOCUS / SEARCH / REPORTER / UNDO / REMINDER_SET / EXPLAIN → {ROUTE, DOC_FOCUS, SEARCH, REPORTER, MANAGER, UNDO, REMINDER_SET, EXPLAIN, PERSONA, END}` — based on `orc.next_stage`
- `VERIFY → {AWAIT_INTERRUPT, MANAGER, ROUTE, PERSONA}` — based on `interrupt_payload` + `orc.next_stage`
- `PERSONA → {END, ROUTE, MANAGER}` — based on `orc.next_stage` (mirrors legacy [ROUTER] / auto-reroute)

### State schema (`PiperState`)

```python
class PiperState(TypedDict, total=False):
    messages: Any
    stage: str
    route_decision: dict[str, Any] | None
    manager_result: dict[str, Any] | None
    verification_passed: bool
    pre_persona_output: str | None
    persona_output: str | None
    workspace_path: str
    interrupt_payload: dict[str, Any] | None
```

The orchestrator instance itself is **not** in state — it is passed via `config["configurable"]["orchestrator"]` at invocation time. State only holds the serializable subset needed for checkpointing.

### Checkpoints

Three checkpoint modes (via `PIPER_LANGGRAPH_CHECKPOINT_MODE`):

| Mode | Persistence | Use case |
|---|---|---|
| `sqlite` (default) | `data/state/langgraph_checkpoints.sqlite` | Production — durable, survives restart |
| `memory` | In-process only | Tests / ephemeral sessions |
| `none` | Disabled | Debugging / legacy fallback |

Per-thread pruning keeps the N most recent checkpoints (`PIPER_LANGGRAPH_CHECKPOINT_HISTORY_LIMIT`, default 500). Old checkpoints are pruned via SQL window function — one thread's flood cannot erase another thread's history.

### Interrupt / resume flow

1. `VERIFY` node detects `pending_stage_pause` or `pending_file_target_confirmation` on the orchestrator
2. `VERIFY` routing returns `AWAIT_INTERRUPT`
3. `await_interrupt_node()` calls `langgraph.types.interrupt(payload)` — graph execution pauses and state is checkpointed
4. UI layer (`controller_actions.py`) detects the interrupt, presents the question, and queues user input
5. On resume, `graph.invoke(state, config)` receives the user's reply as the `resume_value`
6. `await_interrupt_node()` applies the appropriate resume helper (`_apply_stage_approval_resume`, `_apply_file_target_resume`, or `_apply_user_input_resume`)
7. State is updated, `interrupt_payload` is cleared, and the graph loops back to `VERIFY` for re-evaluation

### Recovery records

On graph failure (exception mid-turn), a recovery record is written to `data/state/langgraph_recovery.json` if checkpoint mode is `sqlite`. The record contains thread ID, checkpoint ID, next node, stage trace, and error. Users can resume with `/graph resume` or discard with `/graph clear`.

Interrupt records are written to `data/state/langgraph_interrupt.json` when the graph pauses for user input. These are consumed on resume and cleared on completion.

### Visual debug traces

When `PIPER_DEBUG_LANGGRAPH_VISUALIZE=true`, the compiled graph is rendered to `data/debug/langgraph_visualization.png` (or `.md` fallback) on every turn. When `PIPER_DEBUG_LANGGRAPH_TRACE=true`, structured trace lines are appended to `data/debug/langgraph_trace.jsonl` with stage timings, route decisions, and checkpoint metadata.

### Feature-flag fallback

If LangGraph dependencies are missing or graph compilation fails, `_run_langgraph()` logs a fallback banner and falls back to the legacy `while` loop for that turn. The fallback is per-turn — the next turn will attempt LangGraph again.

---

## 2. Route Phase (phase_route)

**Triggered by:** every user turn, and on [ROUTER] / auto-reroute loops
**File:** `orchestrator_phases.py` → `phase_route()` (legacy) or `core/graph_nodes.py` → `route_node()` (LangGraph)
**LLM role:** Secretary / Router (prompt: `data/prompts/secretary.txt`)

> **LangGraph note:** `route_node()` delegates to `_run_route_core(orc)` — the same helper the legacy loop uses. The graph node additionally writes `route_decision` into `PiperState` for downstream conditional routing.

### User turn ingestion

Before routing, the orchestrator:
1. Extracts `orc.user_msg` from the most recent `role: user` entry in history
2. Calls `orc.prompt_context.record_user_turn()` — but only once per logical turn (skipped on re-route loops to prevent re-ingesting cleared state)

### Pre-LLM bypass checks (no model call):

1. **Pending search payload** in recent history → skip router, jump to `REPORTER`
2. **Proactive trigger** (reminder fire-at reached) → force `CHAT`, jump to `PERSONA` — the trigger message becomes the user message and the proactive system notice is attached for persona context.
3. **Route interceptor** (keyword-based early exit) → jump to interceptor-specific stage. Interceptors include:
   - `UNDO` → `phase_undo` → `PERSONA`
   - `REMINDER_SET` → `phase_reminder_set` → `PERSONA`
   - `EXPLAIN` → `phase_explain` → `PERSONA`
   Interceptors are detected by `detect_route_interceptor()` in `core/routing/route_normalizer.py` before any LLM call.
4. **Environment query** (current date / time / day-of-week questions) → force `CHAT`, jump to `PERSONA` — answered directly from `[ENVIRONMENT]` block, never routed to `SEARCH`. The shared predicate lives in `core/routing/environment_queries.py`; `phase_route()` enforces the first true bypass, with `route_normalizer.py` and `phase_search()` acting as safety-net guards.
5. **Operational state query** (events, tasks, schedule reads) → force `CHAT`, jump to `PERSONA` — `prompt_context.build_readonly_state_answer()` is called deterministically; if it returns a non-empty answer the router LLM is skipped entirely and the answer is delivered via the `phase_persona` fast path. This prevents the LLM router from misclassifying read queries as `TASK` regardless of phrasing. Mutation requests (add/remove/reschedule) are excluded by `build_readonly_answer`'s own gate and fall through to normal routing.
6. **Document chat heuristic** matches user message + ingested documents → force `CHAT`, jump to `DOC_FOCUS`
7. **Live screen visual query** → force `CHAT`, jump to `PERSONA`

### Router history construction

The router receives a filtered history block:
- Current user turn **excluded** (passed separately — prevents duplication)
- `"Thinking..."` assistant placeholder entries **stripped**
- Limited to last 6 messages for token economy

### If no bypass — Router LLM call:

- Raw output: `RouteDecision` JSON → `CHAT` / `SEARCH` / `TASK` + optional StageCard + optional confidence fields (`source_scope`, `confidence`, `question_if_uncertain`)
- **Post-LLM normalization chain** (4 steps, in order):
  1. `normalize_route_decision()` — applies baseline route normalization rules including lookup-source disambiguation. Two cases:
     - **Explicit-scope request** (user text contains web/internet/online/latest/news/current OR workspace/file/folder keywords): the normalizer commits the route directly — web keywords → `SEARCH`, workspace keywords → `TASK FILE_WORK`. The router's `confidence` and `source_scope` fields are used only as tie-breakers when scope is already explicit.
     - **Ambiguous-scope request** (verb patterns like "search for X", "look for X", "find X" with no explicit scope marker): the normalizer **always** forces a web-vs-workspace clarification pause, regardless of the router's `confidence` field. The LLM over-assigns `confidence: high` for these patterns; the normalizer is the authoritative gate. The router's `question_if_uncertain` value is used as the clarification question when present.
     - Lookup-source **follow-up resolution** (`_normalize_lookup_source_choice_followup`): when the previous turn was a clarification pause, a short reply (≤ 6 normalised tokens, e.g. "web pls", "workspace files") is interpreted as a source choice and the original query is carried forward. A longer reply is treated as a new intent and falls through to normal routing.
     - Explicit browser requests with a host/URL stay here too. `normalize_route_decision()` owns first-turn `COMPUTER_USE` detection like `Open example.com in the browser...` or `What's the title of example.com?`
     - Validated by `RouterBoundary.validate()` in `core/route_boundary.py`.
  2. `_resolve_followup_route_with_llm()` — resolves ambiguous continuation routes (pronoun references, affirmative confirmations, short follow-ups, active browser-page continuations) via `FollowupResolutionEngine`
  3. `_refine_ambiguous_task_route_with_llm()` — converts vague/underspecified tasks into clarification pauses via `RouteClarifier`
  4. `apply_route_skill_layer()` — overlays skill selection when applicable

### Sets next stage:

- `CHAT` → `PERSONA`
- `SEARCH` → `SEARCH`
- `TASK` → `MANAGER`
- Parse failure → fallback to `CHAT` → `PERSONA`

---

## 3. Document Focus Phase (phase_document_focus)

**Triggered by:** `phase_route` pre-LLM document chat heuristic
**File:** `orchestrator_phases.py` → `phase_document_focus()`
**LLM role:** internal document condensation pass (not user-facing)

**Purpose:** condenses relevant ingested document excerpts into a focused block before persona. Keeps the persona prompt tight — only the relevant excerpt, not all document matches.

**Sets next stage:** `PERSONA`

---

## 4. Search Phase (phase_search)

**Triggered by:** router returning `SEARCH`
**File:** `orchestrator_phases.py` → `phase_search()`

### Search architecture

The search stack is intentionally split into separate responsibilities:

1. `core/routing/route_normalizer.py` decides whether the turn should become `SEARCH`
2. `core/search/topic_resolver.py` resolves the exact query that should be searched
3. `tools/search.py` selects the active backend adapter and performs retrieval
4. Reporter and Persona summarize the grounded results after retrieval finishes

That split matters for safety: the router decides intent, the topic resolver decides query shape, the backend decides retrieval, and the response layers must stay honest about what was actually found.

### Flow:

1. **Substantive first-pass response** — a context-backed LLM response is streamed to the user immediately while the search runs. It engages with what Piper already knows about the topic instead of only saying "checking the web".
2. **Background search thread** — `perform_search()` runs in a daemon thread, queues `("search_result", {...})` on completion
3. **Sets next stage:** `FINISHED` — the turn ends here

### SearXNG backend

SearXNG is Piper's local/self-hosted metasearch backend option. It is useful for local live testing because the manual search results in this branch have been better than the fallback backend, but it remains an optional local service rather than a core startup dependency.

Current config values documented for this branch:

- `SEARCH_BACKEND = "searxng"`
- `SEARXNG_URL = "http://127.0.0.1:8888"`
- `SEARXNG_TIMEOUT_S = 10.0`
- `SEARXNG_AUTO_START = True`
- `SEARXNG_STOP_ON_EXIT = True`
- `SEARXNG_DOCKER_CONTAINER = "piper-searxng"`
- `SEARXNG_DOCKER_IMAGE = "searxng/searxng:latest"`
- `SEARXNG_DOCKER_HOST_PORT = 8888`
- `SEARXNG_DOCKER_CONTAINER_PORT = 8080`
- `SEARXNG_DOCKER_CONFIG_DIR = ".local/searxng"`
- `SEARXNG_REQUIRE = False`

Lifecycle intent for this branch:

- If SearXNG is already reachable when Piper boots, Piper uses it but does not claim ownership
- If SearXNG is unreachable and `SEARXNG_AUTO_START` is true, Piper may start the Docker container
- If Piper started the container and `SEARXNG_STOP_ON_EXIT` is true, Piper may stop it on shutdown
- If the container was already running before Piper booted, Piper must not stop it
- Docker commands must not run during import or module load
- Docker or SearXNG failure must not crash unrelated chat or normal Piper startup

The implementation should fail gracefully if Docker is missing or SearXNG cannot start. A broken search backend should degrade search, not the rest of Piper.

Manual checks that match the docs:

- `docker --version`
- `docker info`
- `docker run --rm -d --name piper-searxng -p 8888:8080 -v C:\Projects\Piper\.local\searxng:/etc/searxng searxng/searxng:latest`
- `Invoke-RestMethod "http://127.0.0.1:8888/search?q=test&format=json"`
- `docker stop piper-searxng`

The local config file `.local/searxng/settings.yml` is generated/owned locally and must not be committed. JSON output must be enabled there:

```yaml
use_default_settings: true

server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "piper-local-test-only-change-me"
  limiter: false
  image_proxy: false

search:
  formats:
    - html
    - json
```

Troubleshooting notes:

- `docker : The term 'docker' is not recognized` means Docker Desktop is not installed or not on `PATH`
- `403 Forbidden` from `/search?q=test&format=json` usually means JSON output is not enabled or the config mount is missing
- A pre-existing container should be inspected with `docker ps -a` and stopped with `docker stop piper-searxng` before retrying
- Individual engines failing is not fatal if SearXNG still returns usable results

### Why async:

Search can take seconds. The async design avoids blocking the UI. When the search thread completes, `controller_actions.py` detects the queued `search_result` event and calls `run_agent_loop()` automatically — no user input required. This internal agent loop turn hits `phase_route`, which detects the pending search payload and jumps directly to `REPORTER`.

**Sets next stage:** `FINISHED`

---

## 5. Reporter Phase (phase_reporter)

**Triggered by:** `controller_actions.py` auto-launching an internal agent loop turn when the background search thread completes; `phase_route` detects the pending search payload and bypasses the router directly to `REPORTER`
**File:** `orchestrator_phases.py` → `phase_reporter()`
**LLM role:** Reporter (prompt: `data/prompts/reporter.txt`)

### Flow:

1. Extracts raw search data and query from the pending payload in history
2. Reporter LLM call summarizes the results
3. Replaces the raw payload in history with `[SEARCH SUMMARY FOR '{query}']`
4. Marks any search-reporter instruction messages as consumed
5. Stores `orc.latest_search_summary` as fallback for persona

**Sets next stage:** `PERSONA`

---

## 6. Manager Phase (phase_manager)

**Triggered by:** router returning `TASK`
**File:** `orchestrator_phases.py` → `phase_manager()` (legacy) or `core/graph_nodes.py` → `manager_node()` (LangGraph)
**Handles both planning and execution** — no separate phase_plan / phase_act split

> **LangGraph note:** `manager_node()` delegates to `_run_manager_core(orc)` — the same helper the legacy loop uses. The graph node additionally snapshots `verification_passed` into `PiperState` for `VERIFY` routing.

### Flow inside phase_manager:

```
For each stage in StageCard:
    │
    ├── executor.run()  ◄────────────────────────────────────────┐
    │   │                                                        │
    │   ├── PlannerBoundary.validate_input()  [start of stage]  │
    │   │       enforces 7 required PlannerInput fields          │
    │   │       injects parent objective into every stage        │
    │   │       resolves allowed_tools from registry             │
    │   │       fills declared_scope_root (protected)            │
    │   │       fills declared_exact_targets (protected)         │
    │   │       validation failure → return False immediately    │
    │   │                                                        │
    │   │   declared_scope_root and declared_exact_targets are   │
    │   │   protected fields — once set by the boundary, later   │
    │   │   layers (file_stage_policy, file_target_confirmation) │
    │   │   must not broaden or reinterpret them from prose.     │
    │   │                                                        │
    │   │  Per-step loop:                                        │
    │       │                                                    │
    │       ├── Stage budget guards (top of iteration)           │
    │       │       ├── wall-clock budget                        │
    │       │       │   `EXECUTOR_MAX_STAGE_RUNTIME_S`           │
    │       │       ├── action budget                            │
    │       │       │   `EXECUTOR_MAX_ACTIONS_PER_STAGE`         │
    │       │       └── existing step budget                     │
    │       │           `EXECUTOR_MAX_STEPS`                     │
    │       │                                                    │
    │       ├── Planner LLM call                                 │
    │       │       └── PlannerDecision → tool + args            │
    │       │                                                    │
    │       ├── Tool execution  (domain-restricted by stage_type)│
    │       │       ├── FILE_WORK       → FILE_OP / RUN_CODE     │
    │       │       │   All FILE_OP payloads pass through        │
    │       │       │   tools/file_ops.py (single contract owner)│
    │       │       │   before execution — action aliases, path  │
    │       │       │   aliases, field normalization all happen  │
    │       │       │   here and nowhere else.                   │
    │       │       │   FileWorkEngine.candidate_paths()         │
    │       │       │   FileWorkEngine.recovery_hint()           │
    │       │       ├── MEMORY_WORK     → knowledge/world stores │
    │       │       │   StateMutationEngine                      │
    │       │       ├── TASK_EVENT_WORK → StateMutationEngine    │
    │       │       └── IMAGE_WORK      → image_gen tools        │
    │       │                                                    │
    │       ├── Observation recorded in Scratchpad               │
    │       │       Budget exits append explicit scratchpad      │
    │       │       markers (`STAGE TIMEOUT`,                    │
    │       │       `ACTION BUDGET EXHAUSTED`) so persona        │
    │       │       reports the failure honestly.                │
    │       │       Timeout exits also record whether tool       │
    │       │       actions had already executed and whether     │
    │       │       workspace mutations were already applied.    │
    │       │                                                    │
    │       ├── VerificationEngine.should_verify()?              │
    │       │       ├── YES → VerificationEngine.evaluate()      │
    │       │       │         → VERIFIED / PARTIAL / FAILED      │
    │       │       │         → orc.last_verification set        │
    │       │       └── NO  → skip (CHAT, MEMORY_WORK stages)    │
    │       │                                                    │
    │       ├── Inspector LLM call                               │
    │       │       ├── continue ─────────────────────────────►  │
    │       │       ├── finish   → stage done                    │
    │       │       └── pause    → approval / user input required│
    │       │                                                    │
    │       └── SummaryEngine.build_runtime_note()               │
    │               carry-forward pipeline:                      │
    │               verified result → exact-read → file-lookup   │
    │               → LAST_LOG → OBSERVATION_TEXT                │
    │               → runtime_note injected into next stage      │
    │
    ├── If stage would fail + FILE_WORK: current-state recovery pass
    │       file_checker.verify_current_file_stage_state()
    │       → if VERIFIED: mark stage success, log "recovered from current state"
    │       → if not: proceed to failure path
    │
    ├── ScratchpadFormatter.build_outcome_pack()
    │       → effective_success determination
    │       → outcome_text appended to scratchpad
    │
    ├── On success + user_input/approval needed → pause (break)
    ├── On success → continue to next stage
    └── On failure:
            ├── auto_reroute flagged? → next: ROUTE (max 1 retry)
            └── otherwise → break
```

**Sets next stage:** `PERSONA` (or `ROUTE` on auto-reroute after failed stage)

---

## 7. Persona Phase (phase_persona)

**Triggered by:** all paths (CHAT, SEARCH/REPORTER, DOC_FOCUS, MANAGER)
**File:** `orchestrator_phases.py` → `phase_persona()` (legacy) or `core/graph_nodes.py` → `persona_node()` (LangGraph)
**LLM role:** Persona / Speaker (prompt: `data/prompts/instructions.txt`)

> **LangGraph note:** `persona_node()` delegates to `_run_persona_core(orc)`. After persona completes, the graph reads `orc.next_stage` and routes conditionally: `ROUTE` → loopback to `ROUTE` node; `MANAGER` → loopback to `MANAGER` node; `FINISHED` → `END`.

### Context assembly (ContextPackEngine + PromptContextService):

| Block | Source | When included |
|---|---|---|
| `[WORLD STATE]` | `WorldModelManager` | always |
| `[SITUATIONAL STATE]` | `TransientStateManager` | when entries exist |
| `[INTENT STATE]` | `IntentStateStore` | when non-expired entries exist (TTL: 2 days) |
| `[OPERATIONAL STATE]` | `tasks.json` + `events.json` | always |
| `[ENVIRONMENT]` | runtime time / weather / CPU+RAM | always |
| `[RETRIEVED MEMORY]` | `PiperBrain` vector recall | when relevant hits exist |
| `[DOCUMENT MATCHES]` | `DocumentMemoryManager` | only when grouped_matches non-empty AND distance < 0.35 |
| `[DOCUMENT FOCUS]` | `phase_document_focus` output | only when focus_text non-empty |
| `[LATEST_RUNTIME_CONTEXT]` | scratchpad + verification result | TASK turns only |
| `[LAST_TURN_EXPLANATION_CONTEXT]` | snapshot of prior turn for EXPLAIN route | EXPLAIN turns only |

### Turn-end hooks (`on_turn_end`):

After reply delivery, `fire_hooks("on_turn_end", orc)` runs a registered hook chain. These execute after the stream completes — they never block turn start. Registered hooks:

| Hook | What it does |
|---|---|
| `_hook_deferred_conversation_summary` | Async LLM summarization in daemon thread. If history exceeds `MODEL_MAX_TURNS`, compresses older turns into `orc.conversation_summary` → `data/conversation_summary.json` → injected as first hidden system message `[EARLIER CONVERSATION SUMMARY - MAY OMIT DETAIL]`. Skipped on synthetic turns and `knowledge=false` style cards. |
| `_hook_upsert_runtime_context` | Upserts `[LATEST_RUNTIME_CONTEXT]` hidden message with current scratchpad / path state. Removed on file-target-confirmation-cancelled notices. |
| `_hook_upsert_last_turn_explanation_context` | Upserts `[LAST_TURN_EXPLANATION_CONTEXT]` snapshot for use by next EXPLAIN turn. |
| `_hook_upsert_pending_file_target_confirmation` | Upserts or removes pending file target confirmation message. |
| `_hook_consolidate_recent_memory` | Async memory consolidation from recent messages. |
| `_hook_refresh_profile_knowledge` | Async profile knowledge update from recent messages. |

Skipped on synthetic user turns (`orc.synthetic_user_turn = True`).

### Fast paths (before LLM call):

1. **`direct_answer`** — persona directives contain a pre-computed answer → stream word-by-word, return immediately
2. **`build_readonly_state_answer()`** — for CHAT turns, checks if the query can be answered from state stores (tasks, events, knowledge) without an LLM call → stream and return

### Verification result → persona:

```
orc.last_verification (VerificationResult)
    │
    ▼
build_persona_runtime_pack()
    ├── VERIFIED  → outcome_failed=False  — standard FILE_WORK_REPORT_RULE
    ├── PARTIAL   → outcome_failed=True   — [PARTIAL_VERIFICATION_RULE]: name the evidence gap
    ├── FAILED    → outcome_failed=True   — failure directive
    └── None      → text inference fallback (CHAT / MEMORY_WORK only)
```

### Recall loop (up to 3 passes):

Persona can output `[RECALL: keywords]` to trigger a vector memory query:

1. Extract recall query from output
2. If recall is mid-sentence (tokens already streamed) → send fresh `stream_start` to wipe partial display
3. Query `PiperBrain.recall()` with extracted keywords (n_results=9; low-relevance hits filtered out before prompt assembly)
4. Append recall block to tail system content
5. Re-run persona LLM call with augmented context
6. Cap: 3 passes maximum, then ignore further `[RECALL:]` markers

### Empty output recovery (/no_think retry):

If persona returns zero content tokens (all output went to `reasoning_content` in split-mode), retry once with `/no_think` appended to the last user message. This is a Qwen3/3.5-specific workaround.

### [ROUTER] self-correction:

Persona can output `[ROUTER]` to request a re-route to `phase_route`. This is guarded by a cascade of conditions:

```
[ROUTER] in output?
    │
    ├── allow_persona_reroute == False?  (set in contracts.py on terminal failure)
    │       → IGNORE (truthfulness lock — persona reached a terminal honest failure,
    │                 re-routing would paper over it with a second attempt)
    │
    ├── latest_route_error exists?
    │       → IGNORE (don't retry a broken router)
    │
    ├── reporter_just_ran?
    │       → IGNORE (search cycle is complete)
    │
    ├── outcome_failed?
    │       ├── reply asks for user confirmation?
    │       │       → IGNORE (let user respond)
    │       ├── failed_task_router_retries >= 1?
    │       │       → IGNORE (retry cap reached)
    │       └── otherwise:
    │               → ACCEPT (increment retry counter, next: ROUTE)
    │
    ├── outcome_block exists AND not paused?
    │       → IGNORE (successful task — don't re-route)
    │
    └── default (CHAT with no outcome):
            → ACCEPT (loopback, next: ROUTE)
```

**Max re-routes per turn:** 1 (enforced by `failed_task_router_retries` counter)

> **LangGraph note:** The guard cascade lives in `_run_persona_core()` — behaviour is identical in both runtimes. In the graph, the same `orc.next_stage` mutation drives the `_persona_routing` conditional edge instead of a `while` loop condition.

### Output — streaming pipeline:

```
LLM server (llama.cpp, split-mode: reasoning_content skipped)
    │
    ▼
stream_thinking_filter()  [core/stream_filter.py]
    │  first non-"<" char → pass through immediately (no buffering)
    │  "<think>…</think>" present → strip block, yield remainder
    ▼
orc.ui.put("assistant_stream_start/delta/end")
    ▼
controller_queue.py  [delta print gated: CFG.DEBUG_STREAMING_PIPELINE]
    ▼
pipeline.handle_event(start / delta / end)
    ▼
ChatPipeline → TagScrubber → TTS (lazy start) → UI render
```

### Post-stream cleanup:

- `sanitize_persona_output()` strips control tags and validates output
- If reporter just ran and persona is empty, fall back to `latest_search_summary`
- If clean answer differs from raw output, replace in chat history

**Sets next stage:** `FINISHED` (or `ROUTE` via [ROUTER] guard cascade)

---

## 8. Follow-up Resolution (FollowupResolutionEngine)

**Triggered by:** `phase_route`, after the Router LLM call, as step 2 of the post-LLM normalization chain (`_resolve_followup_route_with_llm`)
**File:** `core/engines/followup_resolution.py`

**Important:** This engine runs at **route level**, not inside the executor loop. It refines the route decision before dispatch.

### Resolves:

- Pronoun references ("it", "that", "remove it")
- Affirmative confirmations of system-initiated offers ("Yes", "Yes please", "Go ahead", "Sure") — detected via `_AFFIRMATIVE_CONFIRM_RE` + `_OFFER_PHRASE_RE` on prior assistant turn
- Short readonly follow-ups ("Any tasks?")
- Ambiguous memory follow-ups
- Active browser-page follow-ups grounded in `[LATEST_RUNTIME_CONTEXT]` ("what else is there", "What's the title?", "What's the main heading?") by reconstructing a new `COMPUTER_USE` task card for the last verified page instead of relying on router heuristics

### Does NOT resolve:

- Novel requests
- Multi-step clarifications

---

## 9. Re-Route Entry Points

There are exactly **two** paths that loop back to `phase_route` mid-turn:

| Entry point | Trigger | Location | Guard |
|---|---|---|---|
| **[ROUTER] tag** | Persona outputs `[ROUTER]` in response | `phase_persona` lines 1333-1361 | 8-condition cascade (see §7) |
| **auto_reroute** | Failed stage with `outcome_pack.auto_reroute` flag | `phase_manager` lines 888-898 | `failed_task_router_retries < 1` |

Both share the same retry counter (`orc.failed_task_router_retries`), ensuring a combined maximum of 1 re-route per turn.

**Reset rule:** `orc.failed_task_router_retries` must be reset to `0` at the start of each new user turn (before `phase_route` runs). If it is not reset, a failed re-route from a previous turn will silently block legitimate re-routing in all subsequent turns.

> **LangGraph note:** In the graph runtime, these same two triggers mutate `orc.next_stage` to `"ROUTE"`, which the `_persona_routing` conditional edge reads. The graph then loops back to the `ROUTE` node instead of exiting to `END`. The retry counter and guard logic are unchanged — only the loop mechanism differs (`while` vs. conditional edge).

---

## 10. Memory Read / Write Map

### Reads (per turn, before persona)

| Store | When read | Block in persona prompt |
|---|---|---|
| `world_model.json` | always | `[WORLD STATE]` |
| `transient_state` | always | `[SITUATIONAL STATE]` |
| `intent_state` | always, TTL-filtered (2 days) | `[INTENT STATE]` |
| `tasks.json` + `events.json` | always | `[OPERATIONAL STATE]` |
| `PiperBrain` (Chroma) | vector query on user turn | `[RETRIEVED MEMORY]` |
| `piper_documents` (Chroma) | document similarity query (distance < 0.35) | `[DOCUMENT MATCHES]` / `[DOCUMENT FOCUS]` |

### Writes (during / after turn)

| Action | Writes to |
|---|---|
| User states a durable fact | `knowledge.json` via `StateMutationEngine` |
| User states a disposition/trait | `world_model.json` via `_try_ingest_disposition()` |
| Task add / complete / delete | `tasks.json` via `StateMutationEngine` |
| Event add / complete / delete | `events.json` via `StateMutationEngine` |
| Transient situation observed | `transient_state` (short TTL) |
| Action-oriented intent observed | `intent_state` (TTL: 2 days) |
| Turn ends | `memory.jsonl` (vector memory via `PiperBrain`) |

### Stale memory filters (applied during reads)

- **Date-claim memories** older than 1 day are stripped from `[RETRIEVED MEMORY]` (prevents stale "today is X" from overriding `[ENVIRONMENT]`)
- **Intent entries** older than 2 days are filtered out by `IntentStateStore.load_active_entries()`
- **Document hits** with cosine distance ≥ 0.35 are filtered out (prevents low-relevance document bleed)

---

## 11. Where New Logic Belongs

| If you need to... | It belongs in... |
|---|---|
| Change how a user turn is classified | `phase_route` prompt / `data/prompts/secretary.txt` |
| Add pre-LLM routing shortcuts | `phase_route()` pre-LLM bypass checks |
| Add a new stage type / domain | `AGENTS.md` + router prompt + `PlannerBoundary` tool resolver |
| Change what context persona sees | `ContextPackEngine` / `PromptContextService` |
| Change how persona narrates an outcome | `build_persona_runtime_pack()` in `core/prompt_context.py` + `core/engines/context_pack.py` |
| Add a new engine | `core/engines/` + `core/engines/__init__.py` + update `AGENTS.md` |
| Change verification logic | `VerificationEngine` in `core/services/verification.py` |
| Change file operation behavior | `FileWorkEngine` in `core/services/file_work.py` |
| Change state mutation (tasks/events/knowledge) | `StateMutationEngine` in `core/engines/state_mutation.py` |
| Change follow-up resolution patterns | `FollowupResolutionEngine` in `core/engines/followup_resolution.py` |
| Change memory read/write policy | `memory/stores.py` + `memory/transient_state.py` |
| Change prompt structure / rendering | `core/prompt_builder.py` + `data/prompts/` |
| Change streaming behavior | `core/stream_filter.py` + `core/pipeline.py` |
| Change UI event handling | `ui/controller_queue.py` |
| Add a re-route path | Update §9 of this document first, then implement |
| Change graph node behaviour | `core/graph_nodes.py` (delegates to `orchestrator_phases.py` helpers) |
| Change graph topology / edges | `core/orchestrator_graph_builder.py` |
| Change checkpoint / interrupt policy | `core/orchestrator_graph.py` |
| Change graph recovery commands | `core/orchestrator_graph.py` + `ui/controller_actions.py` |

---

## 12. What Must Never Happen

- Persona claiming success without a `VerificationResult` or explicit system outcome
- Planner choosing a tool outside its stage's allowed domain
- Lower layers importing from higher layers (`tools/` must not import from `ui/`)
- Routing or classification logic placed inside the executor loop
- Memory written from prompt construction code (`PromptBuilder` is render-only)
- `"Thinking..."` placeholder appearing in any history array sent to a model
- Current user turn duplicated in the router history block
- Planner step directive sent as `ROLE: user` where the model template supports system messages — **current exception:** Qwen template rejects a system-only payload so the live code uses `ROLE: user`; this rule becomes a hard constraint when the model or template changes
- More than 1 re-route per turn (enforced by shared retry counter)
- `[DOCUMENT FOCUS]` block injected when focus_text is empty
- `[DOCUMENT MATCHES]` block injected when grouped_matches is empty
- Intent state surfacing entries older than TTL (2 days)
- User turn re-ingested on [ROUTER] re-route loops
- New SEARCH dispatched while a search thread is already in flight (in-flight guard required)
- Validation failure at any LLM output boundary handled with ad hoc recovery logic — every boundary must map to exactly one defined fallback, declared in the validator, not scattered across calling code (see §13.5 fallback table)
- Reporter turn restarting the search topic as a new speaker — the reporter turn must extend, sharpen, or correct the first-pass answer, not re-introduce the topic from scratch. The only exception is when the first-pass answer was materially wrong; in that case the reporter must explicitly acknowledge the correction rather than quietly contradict. Piper must feel like one mind extending a thought, not two voices swapping turns. Enforced via `[SEARCH_REPORT_RULE]` in `core/engines/context_pack.py`.
- Router injecting a TASK_EVENT_WORK stage as a precondition for FILE_WORK based on task or event name matches to file paths — cross-domain dependency detection is handled exclusively by `FileWorkEngine._check_active_dependency` at executor level (§13.17). The router must never conflate calendar/task state with filesystem locks. When `_check_active_dependency` blocks an operation and persona reports the dependency to the user, a follow-up user message ("yes", "proceed", "ok") must be routed to FILE_WORK directly — not interpreted as authorization to auto-resolve the dependency via a TASK_EVENT_WORK stage. Enforced by staging rules 10–11 in `data/prompts/secretary.txt`.
- Repo-root `.venv` recreated from WSL — this replaces Piper's Windows runtime env
  with a Linux one, rewrites `.venv/pyvenv.cfg` to `/usr/bin/...`, and breaks
  PowerShell `python app.py` launches. Use a separate WSL env such as `.venv-wsl`
  instead.

---

## 13. Implemented Architectural Improvements

All items in this section are ✓ IMPLEMENTED and live in the codebase. Planned future work lives in `docs/ROADMAP.md`.

---

### 13.1 Search In-Flight Guard ✓ IMPLEMENTED

**Status:** Live in `orchestrator_phases.py`, `ui/controller.py`, and `ui/controller_actions.py`.

The guard prevents a second search thread from spawning while one is already running. When a SEARCH route is requested while a search is in flight, the request is redirected to PERSONA with a directive acknowledging the new query. The internal reporter handoff on search completion is also live via `controller_actions.py`.

**Async primitive note:** The live implementation uses ref-counted state in `controller.py`, not a plain boolean. A `threading.Event` (`is_set()` / `set()` / `clear()`) would be a cleaner primitive if the guard is ever refactored — low-priority tidy, not a current defect.

**UI input lock:** While a search is in flight, the UI disables the send button and input box. `controller.py` `has_active_operations()` (line 629) counts `_search_in_flight_count`; `controller.py` (line 657) disables the input controls when that returns true; `controller_actions.py` `on_send()` (line 439) also hard-blocks on active operations. This means back-to-back live search turns cannot be manually triggered — the harness can simulate the sequence but the live UI intentionally prevents it.

**Files:** `orchestrator_phases.py` → `phase_search()`, `phase_route()`; `ui/controller.py`; `ui/controller_actions.py`

---

### 13.2 Search Phase: Substantive First-Pass Response ✓ IMPLEMENTED

**Status:** Live in `orchestrator_phases.py`, `core/prompt_context.py`, and `core/engines/context_pack.py`.

The first SEARCH turn now builds the normal persona context pack and uses a search-specific first-pass directive, so Piper gives a useful immediate response while the background web search runs. On the follow-on internal reporter turn, `[SEARCH_REPORT_RULE]` tells persona to extend or refine that earlier answer rather than restart it from scratch.

**Files:** `orchestrator_phases.py` → `phase_search()`; `core/prompt_context.py`; `core/engines/context_pack.py`

---

### 13.3 Conversation History Compression ✓ IMPLEMENTED

**Status:** Live in `core/engines/conversation_compressor.py`, `orchestrator_phases.py`, and `core/orchestrator.py`.

Persona no longer hard-drops older history once `MODEL_MAX_TURNS` is exceeded. `ConversationCompressor` now rolls the dropped portion into `orc.conversation_summary`, persists it at `data/conversation_summary.json`, strips low-value system/control lines from that carry-forward block, and injects it back as a hidden `[EARLIER CONVERSATION SUMMARY - MAY OMIT DETAIL]` system message ahead of the live persona history window. When the accumulated summary exceeds budget, it is re-summarized instead of growing unbounded.

**Files:** `core/engines/conversation_compressor.py`; `orchestrator_phases.py` → `phase_persona()`; `core/orchestrator.py`; `data/conversation_summary.json`

---

### 13.4 Memory Pre-Fetch Improvement ✓ IMPLEMENTED

**Status:** Live in `core/engines/context_pack.py` and `core/prompt_context.py`.

First-pass persona/search recall now pulls 9 memory candidates and filters low-relevance hits (`distance < 0.40`) before prompt assembly. The `[RECALL:]` loop remains as the fallback for genuinely unexpected memory needs, but it is no longer the first place Piper has to look for slightly indirect memory matches.

**Files:** `core/engines/context_pack.py`; `core/prompt_context.py`

---

### 13.5 Typed Schema Validation at LLM Output Boundaries ✓ IMPLEMENTED

**Status:** Live in `core/route_boundary.py`, `core/orchestrator_phases.py`, `core/engines/followup_resolution.py`, and `core/services/route_clarity.py`.

Router, follow-up resolver, and route clarifier outputs now pass through explicit boundary validators before the phase code acts on them. Validation failures raise structured errors with one declared fallback per boundary:

| Boundary | Validator | Validation failure fallback |
|---|---|---|
| Router LLM output | `RouterBoundary.validate()` | `{"decision": "CHAT"}` |
| Follow-up resolver output | `FollowupResolutionBoundary.validate()` | `None` |
| Route clarifier output | `RouteClarifierBoundary.validate()` | `None` |
| Planner LLM output | `PlannerBoundary.validate_input()/normalize_output()` | stage failure path (unchanged) |
| Verification engine output | typed `VerificationResult` | `FAILED` semantics when evidence cannot verify |

The fallback rule now lives in the validator itself rather than in ad hoc parse glue inside the calling phase.

**Files:** `core/route_boundary.py`; `core/orchestrator_phases.py`; `core/engines/followup_resolution.py`; `core/services/route_clarity.py`

---

### 13.6 Structured Stage Intent for File/Code Stages ✓ IMPLEMENTED

**Status:** Live in `core/contracts.py`, `core/file_stage_policy.py`, and `core/orchestrator_phases.py` → `PlannerBoundary.normalize_output()`.

**Problem:**

`file_stage_policy.py` re-derives file-stage intent from English text (`stage_goal` + `success_condition`) on every verification call, using regex and keyword matching accumulated across many patches. `FileStageKind` already exists in `contracts.py` with the right classification values (`INSPECTION`, `CONTENT_EDIT`, `STRUCTURE_PREP`, `BROAD_REORG`, `SCRIPT_LAUNCH`, `DEPENDENCY_RECOVERY`, `UNKNOWN`), and `FileWorkEngine.classify()` produces it — but the result is never stored. It is re-derived from scratch each time policy checks run. This is the same class of debt that `StateMutationEngine` solved: the planner now emits structured `mutation` metadata on the `StageCard` instead of leaving mutation intent to be re-parsed from text.

**Design:**

`file_stage_kind: FileStageKind` is an optional field on `StageCard` (total=False TypedDict). `PlannerBoundary.normalize_output()` calls `FileWorkEngine.classify(stage)` for each FILE_WORK stage and writes the result onto the card at construction time. `file_stage_policy.py` reads `stage.get("file_stage_kind")` first; falls back to `stage_intent_text()` + regex only when the field is absent, preserving backward compatibility.

**Ownership rule:**

`FileWorkEngine.classify()` is the single authoritative classifier. Policy methods must not duplicate its logic.

**Remaining open work:** The fallback regex paths in `file_stage_policy.py` can now be pruned incrementally method by method, since the field-first path is confirmed stable by `file_stage_policy_smoke_test.py`. Regex accumulation is stopped; cleanup is low-priority tidy.

**Goal:** Each file stage's intent is classified exactly once, at construction time. `file_stage_policy.py` is a policy dispatcher (read kind → apply rule), not a classifier.

**Files:** `core/contracts.py`; `core/services/file_work.py`; `core/file_stage_policy.py`; `core/orchestrator_phases.py` → `PlannerBoundary.normalize_output()`

---

### 13.7 Feature Hook Registry ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

Three files are wiring hotspots that accumulate direct feature calls with every new §13 item: `route_normalizer.py` (already 12 sequential interceptors in `normalize_route_decision()`), `context_pack.py` (already 9 hardcoded tail blocks in `build_persona_directive_pack()`), and `orchestrator_phases.py` (already 294-line `phase_persona()` threaded with per-feature state flags). Adding §13.8–§13.12 directly would push all three past a maintainable size and make each future feature require editing 4–6 existing files.

**Design:**

Three targeted registries — one per hotspot. No broad restructuring. The rest of the repo is healthy.

**1. Normalizer interceptor registry (`route_normalizer.py`)**

Replace the explicit call sequence in `normalize_route_decision()` with a registered list:

```python
# Each interceptor is a callable: (decision, user_msg, history) -> RouteDecision | None
_NORMALIZER_REGISTRY: list[Callable] = []

def register_normalizer(fn: Callable) -> None:
    _NORMALIZER_REGISTRY.append(fn)
```

`normalize_route_decision()` iterates `_NORMALIZER_REGISTRY` and returns the first non-None result. Existing interceptors are registered at module load time.

The same hotspot now also exposes a small pre-route interceptor registry for `detect_route_interceptor()`:

```python
# Each pre-route interceptor: (user_msg, history) -> dict | None
_ROUTE_INTERCEPTOR_REGISTRY: list[Callable] = []

def register_route_interceptor(fn: Callable) -> None:
    _ROUTE_INTERCEPTOR_REGISTRY.append(fn)
```

This is what UNDO / EXPLAIN / REMINDER_SET use for pre-LLM interception. New interceptors self-register instead of editing `route_normalizer.py`.

**2. Tail block builder registry (`context_pack.py`)**

Replace the hardcoded append sequence in `build_persona_directive_pack()` with a registered list:

```python
# Each builder: (TailBlockContext) -> str
_TAIL_BLOCK_REGISTRY: list[Callable] = []

def register_tail_block(fn: Callable) -> None:
    _TAIL_BLOCK_REGISTRY.append(fn)
```

`build_persona_directive_pack()` iterates `_TAIL_BLOCK_REGISTRY`, appends any non-None result. Existing blocks are registered at module load. New feature blocks self-register from their own engine file.

**3. Turn lifecycle hooks (`orchestrator_phases.py`)**

Replace direct per-feature calls at turn boundaries with a hook list:

```python
# hook_type: "on_turn_end" | "on_task_verified" | "on_pre_route"
_HOOKS: dict[str, list[Callable]] = defaultdict(list)

def register_hook(hook_type: str) -> Callable[[Callable], Callable]:
    ...
```

`phase_persona()` calls `fire_hooks("on_turn_end", orc)` at the terminal point. `phase_manager()` calls `fire_hooks("on_task_verified", orc, ...)` with terminal task context so registered features can preserve mixed-success and failed-turn behavior without editing `orchestrator_phases.py`. `phase_route()` calls `fire_hooks("on_pre_route", orc)` before the LLM call. New features (stats collector, pattern observer, change journal) register their own hooks — no edits to `orchestrator_phases.py` required.

**Migration:** Each existing direct call in the three hotspot functions is extracted into its own registered function. Behaviour is identical before and after migration — this is a refactor, not a feature change.

**Goal:** Adding a new §13 feature means creating an engine file and registering hooks/interceptors/blocks. It does not mean editing `orchestrator_phases.py`, `route_normalizer.py`, or `context_pack.py`.

**Files:** `core/routing/route_normalizer.py`; `core/engines/context_pack.py`; `core/orchestrator_phases.py`; all existing engine files (register their hooks at module load)

---

### 13.8 Statistics & Regression Detection ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

Regressions in live session behaviour (wrong route, slow turn, unexpected verification failure) are currently diagnosed by manually reading `data/debug/` logs after the fact. There is no structured record of what normal looks like, so spotting drift requires human pattern recognition across unstructured text.

**Design:**

A lightweight `StatsCollector` records timing and outcome data for every turn automatically. No opt-in, no configuration — it runs on every turn like the compressor.

Data captured per turn:
- Turn timestamp
- Route decision (`CHAT` / `SEARCH` / `TASK`) and which pre-LLM bypass fired (if any)
- Phase wall-clock times: route, planner, executor (per stage), persona, TTS
- Verification outcome per stage (`VERIFIED` / `PARTIAL` / `FAILED`)
- Token counts for router and persona LLM calls (if available from llama.cpp response)
- Whether [ROUTER] re-route fired

Data is appended to `data/stats.jsonl` as newline-delimited JSON (one JSON object per line, one line per turn). The file extension is `.jsonl` to make the format unambiguous. The file is never truncated — old entries age out naturally once a rolling window (e.g. last 500 turns) is applied at read time.

**Which turns get a record:** A stats record is written at the terminal point of every turn that reaches a user-facing outcome, regardless of success or failure. Specifically:

- Normal CHAT/TASK turns: record written at the end of `phase_persona()` whether the persona completed successfully, hit an error, or produced a [ROUTER] re-route.
- SEARCH turns: record written at the end of the reporter `phase_persona()` (the auto-triggered second turn), not the first-pass turn. The first-pass turn contributes its own phase timings to that same record.
- Turns that are aborted before persona (e.g. hard exception in planner): record written with whatever phase times were captured up to the abort point; outcome field set to `ABORTED`.
- Cancelled turns (user sends new input before persona completes): not recorded — incomplete data would skew latency baselines.

A **regression detector** runs at session startup and after each turn: computes rolling mean and standard deviation for each numeric field over the last N turns, flags any value outside 2σ as an outlier, and logs the flag to a dedicated `data/debug/stats_alerts.log`. The alert includes the field name, current value, expected range, and turn timestamp.

A **stats tab** in the UI reads `data/stats.jsonl` and renders a read-only report in the main tab area:
- Per-phase average and P95 latency (current implementation: text/table-style report; graphs can come later)
- Recent turn history with route/outcome/latency lines
- Any active outlier alerts at the top of the report

**Alert surfacing default:** Outlier alerts appear in the stats tab and `data/debug/stats_alerts.log` only. They do not surface in the main chat UI and Piper does not mention them conversationally unless the user explicitly asks (e.g. "are there any performance issues?"). Stats alerts are a developer/debugging signal, not a user-facing notification.

**Ownership:** `core/engines/stats_collector.py` owns data capture and the regression check. UI tab is a read-only consumer — it never writes to stats. `orchestrator_phases.py` feeds per-phase/per-stage stats state throughout execution, and `core/orchestrator.py` finalizes the append-only record at the terminal point of each turn.

**Files:** `core/engines/stats_collector.py` (new); `core/orchestrator.py` (instantiate); `orchestrator_phases.py` (record calls at all terminal points); `ui/` (stats tab, read-only); `data/stats.jsonl` (append-only store); `data/debug/stats_alerts.log`

---

### 13.9 Undo / Change Journal ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

Every mutating FILE_OP (write, edit, delete, move, rename) is permanent. If the executor makes an incorrect change — wrong file, wrong content, wrong scope — the only recovery is the user manually undoing it. This limits how aggressively the user can delegate file work to Piper.

**Design:**

Before any supported mutating FILE_OP fires, the executor snapshots the original workspace state for the affected paths into a per-turn change journal. "Undo last task" is then a real command that restores those snapshots in reverse operation order.

**Journal format** — one entry per mutating task turn:

```json
{
  "turn_id": "2026-03-22T14:30:00",
  "task_goal": "Rename report.txt to report_final.txt",
  "operations": [
    {
      "action": "move_path",
      "requested_paths": ["report.txt", "report_final.txt"],
      "snapshots": [
        {"path": "report.txt", "kind": "file", "...": "..."},
        {"path": "report_final.txt", "kind": "absent"}
      ]
    }
  ]
}
```

For `write` / `edit`, the pre-mutation file snapshot is captured. For `delete`, the deleted path snapshot is captured. For `move` / `rename` / `copy`, the destination snapshot is captured and `move` also snapshots the source so it can be restored. Missing parent directories created by the task are journaled too, so undo can remove them when they were introduced by the mutation.

Snapshot payload rules:
- Text files under the journal cap store inline `content` so ordinary edits remain meaningfully undoable.
- Binary extensions and oversized files do not store inline bytes. They are recorded as `snapshot_type: "metadata_only"` plus `size`, and oversized text snapshots also set `truncated: true`.
- Directories store structural state only (`kind: "directory"`), not recursive embedded file payloads.
- Legacy journal entries that still contain `bytes_b64` are tolerated at undo time, but they are not considered automatically restorable anymore; undo fails honestly instead of crashing.

Example text snapshot:

```json
{"path": "notes/todo.txt", "kind": "file", "size": 42, "content": "buy milk\ncall mom\n"}
```

Example metadata-only snapshot:

```json
{"path": "PiperGen_00025_.png", "kind": "file", "size": 312345, "snapshot_type": "metadata_only"}
```

**Edge cases:**
- **Overwrite:** if a `write` targets a file that already exists, the original content is captured before the overwrite fires. Undo restores the original content, not a blank file.
- **Partial batch failure:** the journal records only operations that were successfully executed. If a task had three mutations and the third failed, the journal entry contains two operations. Undo reverses only those two.
- **Mixed success/failure task:** "undo last task" reverses all journaled operations from that task entry — i.e. only the ones that actually ran. The failed stage left no journaled operation, so there is nothing to undo for it. Persona explains this clearly in the undo summary.
- **RUN_CODE exclusion:** `RUN_CODE` tool calls are excluded from the change journal in v1. Script side-effects are opaque — the journal cannot reliably capture or invert arbitrary subprocess execution. Undo does not apply to turns where `RUN_CODE` was the primary mutation vector; persona must say so if the user asks.

**Undo trigger:** The route normalizer recognises "undo", "undo that", "undo last task", "revert that" as an interceptor pattern and sets `next_stage = UNDO` directly — this is a pre-LLM normalizer intercept, not a new LLM-visible route kind. The LLM router never outputs `UNDO`; it is handled entirely in the pre-route interceptor helper (`detect_route_interceptor()`) before the Secretary call. `phase_route` dispatches `UNDO` to a dedicated `phase_undo()` handler that reads the most recent journal entry, restores the recorded snapshots in reverse order, confirms the result, and reports the outcome to persona for user-facing summary.

**Scope:** Reversible FILE_OP mutations only in v1: write/edit, delete, move/rename, copy, and directory-prep actions that created new directory state. RUN_CODE and StateMutationEngine changes are out of scope.

**Undo availability notice:** After a mutating FILE_WORK turn completes successfully, persona appends a brief, low-key notice that undo is available (e.g. "You can say 'undo that' if you'd like to revert."). This notice is only appended on successful mutating FILE_WORK turns — not on read-only tasks, failed tasks, CHAT turns, or SEARCH turns. It fires once per task, not on every stage.

**Journal retention:** Last 10 task turns retained. Older entries are dropped on append.

**Hook ownership:** The journal write hook self-registers from `core/engines/change_journal.py` via the feature-hook registry on `on_task_verified`. `orchestrator_phases.py` only fires the hook chain; it does not own the journal callback.

**Files:** `core/engines/change_journal.py` (owner, snapshot policy, `on_task_verified` hook); `core/executor.py` (pre-mutation capture hook); `core/orchestrator.py` (journal owner wiring); `core/orchestrator_phases.py` (`phase_undo()` and undo availability notice); `core/routing/route_normalizer.py` (UNDO interceptor recognition); `data/change_journal.json` (rolling store)

---

### 13.10 Proactive Monitor (Background Reminders) ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

Piper only acts when the user sends a message. Time-based reminders ("remind me in 30 minutes", "alert me before my dentist appointment") require the user to remember to ask — defeating the purpose.

**Shipped design:**

A `ProactiveMonitor` runs as a background thread alongside the existing UI event loop. It checks a scheduled reminders store on a short polling interval (currently the monitor default). When a reminder's fire time is reached and Piper is truly idle — boot ready, no active operation, no running code session, no TTS currently speaking, and no document-ingest/live-screen startup work pending — the monitor dispatches through `ui/controller_actions.py` with a synthetic user-invisible trigger message. The orchestrator routes this as `CHAT` → `PERSONA`, and persona's context pack includes a `[PROACTIVE_TRIGGER]` block describing what the reminder is. Persona speaks the reminder; the turn completes normally.

This reuses the same async auto-trigger pattern already proven by the search reporter handoff. No new orchestrator primitives are needed.

**Reminder setting:** The user sets reminders through normal conversation ("remind me to call the dentist in 20 minutes"). The route normalizer recognises reminder-setting intent as a pre-LLM interceptor — same mechanism as UNDO — and sets `next_stage = REMINDER_SET` directly without an LLM router call. `REMINDER_SET` is not a new LLM-visible route kind; it is a normalizer interceptor that dispatches to a lightweight `phase_reminder_set()` handler (no planner needed) that writes to `data/reminders.json` and confirms to persona. Relative times are resolved to absolute UTC timestamps at set time, using the `[ENVIRONMENT]` time block as the reference.

**Chat history:** The synthetic proactive trigger is not visible in chat history and is not appended as a user message. Persona's spoken reminder IS visible as a normal assistant turn. The reminder is marked `fired: true` in `data/reminders.json` after the persona turn completes. No memory entry is created unless the user explicitly asks Piper to remember something from the reminder context. The raw hidden trigger payload is stripped from persona prompt history; persona only sees the typed `[PROACTIVE_TRIGGER]` tail block.

**Reminder store format:**

```json
[
  {
    "id": "uuid",
    "fire_at": "2026-03-22T15:00:00Z",
    "message": "remind the user to call the dentist",
    "fired": false
  }
]
```

**Interruption default:**
- **Idle (no active operation):** reminder fires immediately — persona speaks it and it appears as a visible assistant message in chat.
- **Busy (active operation in flight):** monitor defers quietly until `has_active_operations()` returns false, then fires. No mid-task interruption under any circumstance.
- **Deferred reminder:** no indication is shown to the user that a reminder was deferred — it simply fires as soon as Piper is idle. If the delay would be unreasonable (e.g. still busy 5 minutes after fire time), this is left as a future policy decision — v1 defers indefinitely.

**Files:** `core/engines/proactive_monitor.py` (owner); `core/feature_hooks.py` (turn-end hook registry); `ui/controller.py` (monitor lifecycle + idle gate); `ui/controller_actions.py` (synthetic reminder dispatch); `core/routing/route_normalizer.py` (reminder-set intent recognition); `core/orchestrator_phases.py` (REMINDER_SET phase + PROACTIVE_TRIGGER route short-circuit); `core/prompting.py` (strip raw hidden trigger transport from persona history); `data/reminders.json`

---

### 13.11 Turn Explanation ("Why did you do that") ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

When Piper makes an unexpected routing or verification decision, diagnosing it requires opening `data/debug/router_debug.txt` or `persona_debug.txt` and reading raw LLM payloads. The information needed is already tracked in `orc` — it is just not surfaced conversationally.

**Design:**

The route normalizer recognises explanation-request phrases ("why did you do that", "how did you decide", "explain that", etc.) as a pre-LLM interceptor and sets `next_stage = EXPLAIN` directly — same mechanism as UNDO. After each turn, `_hook_upsert_last_turn_explanation_context` builds a snapshot from live `orc` state (route decision, route source, task goal, stages, verification verdict, phase timings, outcome) and persists it as a hidden system message in chat history via `orc.chat.upsert_hidden_system_message`. On an explain request, the interceptor extracts that snapshot and packs it into `system_notice.snapshot`. `phase_persona` routes to `explain_last_turn=True`, limiting history to the last 6 turns and disabling knowledge blocks. The tail block `_tail_block_explain_last_turn` renders `[EXPLAIN_LAST_TURN]` from the snapshot, instructing persona to give a 2–4 sentence plain-English explanation. Followup depth requests ("more detail", "why specifically") are also intercepted and trigger `detail_level=detailed`.

**Scope:** Explains the immediately preceding completed turn only.

**Files:** `core/turn_explanation.py` (snapshot builder, renderer, helpers); `core/routing/route_normalizer.py` (`_registered_explain_last_turn_interceptor`); `core/orchestrator_phases.py` (`_hook_upsert_last_turn_explanation_context`, `phase_persona` explain path); `core/engines/context_pack.py` (`_tail_block_explain_last_turn`)

---

### 13.12 Context Arbitration Policy (R-1) ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

The persona context pack assembled many competing blocks per turn with no written rule for which dominates. Every turn was a buffet. As block count grew this degraded persona sharpness.

**Design:**

A typed arbitration table (`PERSONA_CONTEXT_ARBITRATION_TABLE` in `core/contracts.py`) defines, per route / turn type, which blocks are **primary** (dominate attention), **secondary** (present but subordinate), and **suppressed** (omitted entirely). Seven turn types are covered: `CHAT`, `TASK`, `DOC_FOCUS`, `SEARCH_FIRST_PASS`, `REPORTER`, `EXPLAIN`, `PROACTIVE_TRIGGER`.

Enforcement runs in two places:
1. `apply_context_arbitration()` in `ContextPackEngine` strips suppressed blocks from the context pack before prompt assembly.
2. `_tail_block_context_arbitration` registered in the tail block registry emits a `[CONTEXT_ARBITRATION_RULE]` block to the persona naming primary/secondary/suppressed blocks for the current turn.

**Files:** `core/contracts.py` (`PERSONA_CONTEXT_ARBITRATION_TABLE`, `PersonaArbitrationProfile`, `PersonaTurnType`); `core/engines/context_pack.py` (`apply_context_arbitration()`, `render_context_arbitration_block()`, `_tail_block_context_arbitration`); `core/orchestrator_phases.py` (two enforcement call sites in `phase_search_first_pass` and `phase_persona`)

---

### 13.13 Style Card knowledge=false Completeness Fix (R-2) ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

`knowledge=false` in a style card was meant to be a full memory blackout for immersive styles (e.g. `storyteller`). Two sources leaked through regardless:

1. `vision_session_memory` — injected in `context_pack.py` with no `knowledge_enabled` check.
2. `conversation_summary` — injected via `compress_history` in `orchestrator_phases.py` independently of `orc.knowledge_enabled`. The deferred LLM summarization hook also ran unconditionally.

**Fix:**

- `context_pack.py`: `vision_notes` block gated behind `knowledge_enabled` flag.
- `orchestrator_phases.py`: `compress_history` call passes `existing_summary=""` when `knowledge_enabled=False`; deferred summary hook (`_hook_deferred_conversation_summary`) returns early when `knowledge_enabled=False` to prevent cross-session summary growth during immersive style turns.

**Files:** `core/engines/context_pack.py` (`build_persona_pack`); `core/orchestrator_phases.py` (`phase_persona` compress call, `_hook_deferred_conversation_summary`)

---

### 13.14 Style Card Bootstrap Injection (R-3) ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

Style cards support a `---BOOTSTRAP---` section containing example conversation turns meant to prime persona tone. `core/style.py` parsed these into a `bootstrap` tuple on `StyleSheet`. Nothing in the orchestrator ever read or injected them — they were silently discarded at runtime.

**Design:**

In `phase_persona()`, after history compression and before `build_persona_messages`, bootstrap turns from `orc.ss.bootstrap` are prepended to the in-memory history list. Injection is gated: only fires on session start (first turn, `_bootstrap_injected_for_style == ""`) or when the active style name changes. After injection, `orc._bootstrap_injected_for_style` is set to the current style name so subsequent turns skip re-injection. This is intentionally in-memory only — bootstrap turns are never written to `orc.chat` and never seen by the compressor. Skipped for `explain_last_turn` turns.

**Files:** `core/orchestrator.py` (`_bootstrap_injected_for_style` tracker); `core/orchestrator_phases.py` → `phase_persona()` (gated prepend after history compression)

---

### 13.15 Typed Success Constraints (R-5) ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

`VerificationEngine` evaluated stage success by reading prose `success_condition` text through heuristic matchers and an LLM file-checker call. There was no shared schema between the Planner and Verifier — the Verifier was guessing at what the Planner meant. This is the root of fake-success: the Planner claims done, the Verifier can't reliably contradict it, and Persona reports success on a failed operation.

**Design:**

`PlanConstraint` is a TypedDict with six constraint types — `EXCLUSION`, `MOVED`, `DELETED`, `CREATED`, `MODIFIED`, `COUNT` — each carrying the fields needed for deterministic filesystem evaluation. `StageCard` gains an optional `constraints: List[PlanConstraint]` field.

`FileWorkEngine.derive_constraints(stage, tool_result)` is the single derivation point:
1. Returns `stage["constraints"]` if explicitly set (router- or planner-emitted)
2. Derives MOVED / DELETED / CREATED from unambiguous single-operation tool results (`requested_moves`, `deleted_files`, `created_files`)
3. Returns `[]` — caller falls through to existing RULES → LLM path

`VerificationEngine.evaluate()` calls `derive_constraints` first. If constraints are found, `evaluate_with_constraints()` dispatches to six deterministic `_check_*` helpers that read actual filesystem state. A single failed constraint produces a specific, structured `FAILED` result. No LLM call. If no constraints are derivable, the existing RULES → LLM → STATE_CHECK path runs unchanged.

The planner prompt (`manager.txt`) instructs FILE_WORK stage completions to include a `"constraints"` field listing what was accomplished in structured form, with examples for all six types.

Prose `success_condition` is kept as fallback during migration. Once all plan types emit explicit constraints the prose-heuristic path will be removed.

**Files:** `core/contracts.py` (`PlanConstraintType`, `PlanConstraint`, `StageCard.constraints`); `core/services/file_work.py` (`FileWorkEngine.derive_constraints`); `core/services/verification.py` (`evaluate_with_constraints`, six `_check_*` methods, constraint-first path in `evaluate`); `data/prompts/manager.txt` (completion JSON + constraint-type reference)

---

### 13.16 Planner Schema Compliance (R-5 enforcement) ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

`PlanConstraint` closes the fake-success loop only if the Planner actually emits a `constraints` block. Under long context the Planner may revert to prose. `derive_constraints()` is a strong fallback but cannot recover exclusion intent. Silent fallthrough re-opens the loop.

**Design:**

1. `manager.txt` constraint language strengthened: `constraints` is now labelled **required** for FILE_WORK completions with an explicit warning that omitting it causes verification to fail.

2. `executor.py` schema compliance gate: at both `is_complete` paths, for stages where `stage_requires_file_verification` is True, the executor checks whether `constraints` is present. On first miss it injects a schema reminder and retries (`_constraints_reminder_sent` flag per stage). On second miss it logs a `constraint_violation` line via `StatsCollector.note_constraint_violation` and falls through to `derive_constraints()`.

**Files:** `data/prompts/manager.txt` (strengthened required language); `core/executor.py` (schema gate + retry + stats); `core/engines/stats_collector.py` (`note_constraint_violation`)

---

### 13.17 State Mutex — Cross-Domain Dependency Check (R-6) ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

A file could be deleted while an active Task held a reference to it. `phase_manager` dispatched file mutations without consulting `OperationalStateService`.

**Design:**

`OperationalStateService.find_references(path)` scans all active tasks and events for normalized file-reference matches on the given path. The matching semantics live in `core/file_reference_matcher.py`, so future changes to alias/typo/path matching should be made there instead of re-implementing them in `find_references()`. It still catches literal path/basename substrings, but also accepts extension-aware close matches for humanised filename mentions such as `review Charly TXT` matching `charlie.txt`. Returns a list of conflict dicts with a `kind` field.

`FileWorkEngine._check_active_dependency(tool_tag, operational_state_service)` extracts target path(s) from DELETE or MOVE tool tags and calls `find_references`. Returns a `FileWorkBlock(blocked=True, fatal=True, reason="ACTIVE_TASK_DEPENDENCY: ...")` on conflict.

`FileWorkEngine.should_block()` gains optional `operational_state_service`. The dependency guard runs first, before the content-edit gate, firing on RELOCATION stages too. `FileWorkBlock` gains a `fatal` field — when True the executor stops the stage immediately rather than retrying. The persona reports the dependency to the user; no automatic resolution.

**No automatic resolution:** When `_check_active_dependency` blocks an operation, the persona reports the dependency to the user and stops. The system never automatically closes or updates the referenced task/event. The user must decide: either manage the event/task themselves and retry the file operation, or explicitly ask Piper to do both in the same request. If the user follows up with a short affirmative ("yes", "proceed"), the router re-routes to FILE_WORK only — it does not create a TASK_EVENT_WORK precondition stage. See §12 and staging rules 10–11 in `data/prompts/secretary.txt`.

**Files:** `core/file_reference_matcher.py` (shared normalized file-reference matcher); `core/operational_state_service.py` (`find_references`); `core/services/file_work.py` (`_check_active_dependency`, extended `should_block`); `core/contracts.py` (`FileWorkBlock.fatal`); `core/executor.py` (fatal-block path, new init params); `core/orchestrator_phases.py` (wire params to StageExecutor); `data/prompts/secretary.txt` (staging rules 10–11 enforce no-auto-resolution at router level)

---

### 13.18 Bulk Mutation Rollback Manifests ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

Bulk FILE_OP tools (`consolidate_by_extension`, `move_many`, `copy_many`, `delete_many`) can touch many files in one operation. The existing single-op change journal (§13.9) snapshots file content per-path, but bulk operations are too wide to reconstruct after the fact. When the user says "undo that" after a large consolidation, the prior undo path fell back to planner guesswork and could loop.

**Design:**

After any supported bulk FILE_OP completes with `status=EXECUTED` and `workspace_changed=True`, the executor writes a rollback manifest to `data/rollback/rollback_<turn_id>.json`:

```json
{
  "turn_id": "...",
  "timestamp": "...",
  "action": "consolidate_by_extension",
  "committed": true,
  "rolled_back": false,
  "moves": [{"from": "report.txt", "to": "docs/report.txt"}, …],
  "deletions": [],
  "created_dirs": ["docs"]
}
```

The manifest is written post-execution (the full recipe is only known after `consolidate_by_extension` runs). At most 5 manifests are kept on disk; older ones are pruned on each write.

The manifest path is stored on the change-journal entry for that turn (`rollback_manifests` field). On "undo", `phase_undo()` checks the latest journal entry first: if it holds an uncommitted manifest path, `invert_manifest()` replays each move in reverse order and removes any empty auto-created directories. The manifest is marked `rolled_back=True` on success. A second undo attempt is refused (guard check). If no manifest path is present the existing single-op snapshot undo runs as before.

**Limitations (v1):** Only the most recent bulk operation is reversible via manifest. `delete_many` deletions are recorded but cannot be restored (no content snapshot); `invert_manifest` reports them as non-recoverable. `RUN_CODE` operations are excluded entirely.

**Files:** `core/engines/rollback_engine.py` (new — `record_manifest`, `invert_manifest`, `is_bulk_action`, `_prune_old_manifests`); `core/executor.py` (post-bulk-op manifest write, `completed_rollback_manifests` list, `_current_turn_id` propagated from phase); `core/engines/change_journal.py` (`rollback_manifests` field on entry, `mark_entry_undone` method); `core/orchestrator_phases.py` (manifest collection, hook args, `phase_undo` manifest-first path); `data/rollback/` (manifest store); `scripts/bulk_rollback_manifest_smoke_test.py` (11-case smoke test)

### 13.19 Execution Budget — Wall-Clock and Action-Count Stage Limits ✓ IMPLEMENTED

**Status:** Implemented.

**Problem:**

The executor's only stage limit was `EXECUTOR_MAX_STEPS = 12`. No wall-clock timeout existed. A hung LLM call or a planner loop issuing repeated "continue" actions could burn unbounded time and inference tokens, leaving the workspace in a partial state with no clear signal.

**Design:**

Two new env-overridable budget constants in `config.py`: `EXECUTOR_MAX_STAGE_RUNTIME_S` (wall-clock) and `EXECUTOR_MAX_ACTIONS_PER_STAGE` (action count). Both are checked at the top of each step iteration — after control returns from an LLM call but never mid-stream, so in-flight inference is never interrupted.

On budget exhaustion the executor appends an explicit scratchpad marker (`STAGE TIMEOUT` or `ACTION BUDGET EXHAUSTED`) and records mutation state: whether no tool had yet executed, whether tool actions had already run, and whether known workspace mutations had already occurred. A timeout after a real file move is reported with that context, not as a clean no-op failure.

`TIMEOUT` is a distinct terminal outcome in `stats_collector.py` — not folded into generic `FAILED`. `step_count`, `action_count`, `timeout_hit`, and `action_budget_hit` are recorded per stage. The signal flows through `orchestrator_phases.py`, `scratchpad_formatter.py`, and `context_pack.py` so persona receives accurate failure context.

**Files:** `config.py` (`EXECUTOR_MAX_STAGE_RUNTIME_S`, `EXECUTOR_MAX_ACTIONS_PER_STAGE`); `core/executor.py` (budget guards, mutation-state annotation); `core/engines/stats_collector.py` (`TIMEOUT` outcome, new per-stage fields); `core/orchestrator_phases.py` (timeout signal forwarding); `core/scratchpad_formatter.py` (TIMEOUT in stage status); `core/engines/context_pack.py` (TIMEOUT as failed execution outcome); `scripts/executor_budget_smoke_test.py` (timeout-before-action, timeout-after-action, action-budget exhaustion cases)

---

### 13.20 Data Hygiene Rules (Pre-roadmap #3) ✓ IMPLEMENTED

**Status:** Implemented.

- `AGENTS.md` now includes §10A: binary payload prohibition, unbounded-file prohibition, and write-path rotation as a doctrine rule.
- `stats.jsonl` now prunes to `history_limit` lines on write, using a temp-file plus atomic rename instead of leaving disk growth unbounded.
- No `bytes_b64` write paths remain in the codebase. The only surviving reference is the legacy undo read handler in `change_journal.py`, which skips old entries safely.
- The repo sweep also capped other JSONL write surfaces discovered during the audit: persisted chat memory and agent escalation logs now prune on write too.

**Files:** `AGENTS.md` (§10A doctrine); `memory/storage.py` (shared JSONL tail-prune helper); `core/engines/stats_collector.py` (write-side rotation); `memory/chat_state.py` (bounded `memory.jsonl` writes)

---

### 13.21 Hook Extraction (Pre-roadmap #4) ✓ IMPLEMENTED

**Status:** Implemented.

- All remaining feature hooks now self-register in their owning modules instead of living in `orchestrator_phases.py`.
- `orchestrator_phases.py` contains zero `@register_hook` decorators. It only fires hook events.
- The live hook inventory in this repo is broader than the earlier task sheet implied: the extracted hooks now live in `memory/world_model.py`, `core/engines/conversation_compressor.py`, `core/engines/context_pack.py`, `core/file_target_confirmation.py`, `core/turn_explanation.py`, `core/engines/stats_collector.py`, and `core/prompt_context.py`, alongside the already-existing `change_journal.py` and `proactive_monitor.py` hooks.
- `core.feature_hooks.list_hooks()` now exposes the active registry for validation.

**Files:** `core/orchestrator_phases.py` (decorators removed); `core/feature_hooks.py` (`list_hooks()`); `memory/world_model.py`; `core/engines/conversation_compressor.py`; `core/engines/context_pack.py`; `core/file_target_confirmation.py`; `core/turn_explanation.py`; `core/engines/stats_collector.py`; `core/prompt_context.py`

---

### 13.22 Structured Logging (Pre-roadmap #5) ✓ IMPLEMENTED

**Status:** Implemented.

- `print()` calls were replaced with the Python logging module across `core/`, `memory/`, `tools/`, and `llm/`.
- The old streaming debug print guards were removed from `core/`; debug level is now the filter.
- `config.py` now exposes `LOG_LEVEL` via `PIPER_LOG_LEVEL` (default `INFO`).
- `app.py` configures `logging.basicConfig()` before the main Piper imports so module loggers have a consistent root configuration from startup.

**Files:** `app.py` (root logging config); `config.py` (`LOG_LEVEL`); `memory/brain.py`; `tools/image_gen.py`; `llm/boot.py`; `core/orchestrator_phases.py`; `core/agent.py`; `tools/stt.py`; `core/style.py`; `core/debug_tools.py`; `tools/search.py`; `core/pipeline.py`; `core/environment_service.py`; `core/environment.py`; `memory/stores.py`; `memory/chat_state.py`

---

### 13.23 Doc Sync — §2 Bypass Order ✓ IMPLEMENTED

**Status:** Implemented.

- §2 pre-LLM bypass checks reordered to match actual `phase_route()` code order.
- Added missing steps: proactive trigger (step 2) and route interceptor (step 3, covering UNDO / REMINDER_SET / EXPLAIN).
- §1 top-level flow diagram updated to match.
- Code was already correct; this was a documentation-only fix.

**Files:** `docs/architecture/TRIGGER_FLOW.md` (§1 diagram, §2 bypass list)

---

### 13.24 Test Visibility ✓ IMPLEMENTED

**Status:** Implemented.

- Unified smoke test runner: `scripts/run_smoke_tests.py`
- Discovers all `*_smoke_test.py` files in `scripts/` automatically. Non-test files (harnesses, workers, fixtures) are excluded by the glob pattern.
- Supports `--category`, `--list`, `--fail-fast`, `--verbose`, `--timeout`, and positional fnmatch patterns for targeted runs.
- Categorises tests by filename prefix (FILE_WORK, COMPUTER_USE, ROUTING, PERSONA, etc.) with GENERAL as the catch-all.
- `--skip-harness` excludes any test whose filename contains `harness`, giving a fast default signal without the LLM-backed integration tests. Rationale recorded in `notes/coder-log.md`.
- Full tiered classification (smoke / extended / quarantined) deferred to a later roadmap slot after the computer-use suite stabilises.

**Files:** `scripts/run_smoke_tests.py` (new); `notes/coder-log.md` (rationale entry)

---

### 13.25 Orchestrator Dependency Injection ✓ IMPLEMENTED

**Status:** Implemented.

- `OrchestratorConfig` frozen dataclass holds all constructor dependencies, grouped into logical clusters (LLM + memory, chat + style, pipeline + UI, tools, search-state callables, paths).
- `Orchestrator.__init__()` accepts a single `OrchestratorConfig` parameter. All `orc.X` attribute names are unchanged — zero downstream changes in `orchestrator_phases.py`.
- `run_agent_loop()` accepts a single `OrchestratorConfig` parameter.
- `PiperController.build_orchestrator_config()` assembles the config from controller state. `conversation_summary_path` resolution is inlined directly (`self.user_runtime.current_conversation_summary_path()`) to avoid a circular import with `controller_actions.py`.
- All three UI call sites in `controller_actions.py` replaced with `controller.build_orchestrator_config()`. As a side effect, the search-reporter and proactive-reminder call sites now consistently receive the user-specific summary path rather than falling back to the global default.
- `AGENTS/harness/session.py` updated (missed by the original task sheet — caught during implementation).

**Files:** `core/orchestrator.py` (`OrchestratorConfig` dataclass, simplified `__init__`, simplified `run_agent_loop()`); `ui/controller.py` (`build_orchestrator_config()`); `ui/controller_actions.py` (three call sites); `AGENTS/harness/session.py` (harness caller updated)

---

### 13.26 Config Hot-Reload (Roadmap S-4) ✓ IMPLEMENTED

**Status:** Implemented.

- `CFG` is now a live `LiveConfig` proxy over the frozen `Config`, so existing `from config import CFG` call sites continue to work while runtime-safe fields can change between turns.
- `LiveConfig.reload_if_stale()` watches `data/state/config_override.json`, accepts only scalar overrides, enforces a 10 KB size guard, ignores restart-only keys (`ROOT_DIR`, `DATA_DIR`, `MEMORY_PATH`, `LLAMA_SERVER_REASONING_BUDGET`), and reverts overrides when the file is deleted.
- `LlamaServerClient.reconnect()` hot-swaps HTTP client settings under the request lock, and `PiperController` subscribes to config changes so updated LLM client settings and `LOG_LEVEL` take effect without a process restart.
- `Orchestrator.run()` checks for stale overrides at the start of each turn and logs the reloaded keys so config reload remains explicit and honest in the runtime flow.
- Public `Config` class attrs such as `MODEL_PATH` and `MMPROJ_PATH` are mirrored alongside dataclass fields, so legacy `CFG.X` access and override/revert behavior stay compatible.

**Files:** `config.py` (`LiveConfig`, override watcher, revert path); `llm/llm_server_client.py` (`reconnect()`); `ui/controller.py` (config change subscriber + LLM/log-level refresh); `core/orchestrator.py` (turn-start reload check)

---

### 13.27 LangGraph Orchestrator Migration ✓ IMPLEMENTED

**Status:** Implemented. Migration spec phases 0–6 are code-complete and live. Phase 7 (repo indexing) is deferred. Phase 8 (burn-in) is in progress — automated tests pass, real-world edge cases validated. The LangGraph runtime is the default; legacy loop available via `PIPER_USE_LANGGRAPH_ORCHESTRATOR=false`.

**Problem:**

The legacy `while`-loop orchestrator in `orchestrator_phases.py` grew to ~2500 lines of phase-dispatch spaghetti. Adding checkpointing, interrupt/resume, and visual debugging would have required hand-rolling infrastructure that already exists in proven libraries.

**Design:**

Adopt LangGraph `StateGraph` as an alternative runtime behind a feature flag. The graph delegates to the same phase helpers (`_run_route_core`, `_run_manager_core`, `_run_persona_core`) so behaviour is identical between runtimes. Only the loop mechanism changes: `while next_stage != "FINISHED"` becomes a compiled graph with conditional edges.

**Nodes extracted (`core/graph_nodes.py`):**

| Node | Phase | Delegates to |
|---|---|---|
| `route_node` | Phase 1 | `_run_route_core(orc)` |
| `manager_node` | Phase 2 | `_run_manager_core(orc)` |
| `verify_node` | Phase 3 | Reads verification + interrupt state |
| `persona_node` | Phase 3 | `_run_persona_core(orc)` |
| `await_interrupt_node` | Phase 5 | `langgraph.types.interrupt()` + resume helpers |

**Graph builder (`core/orchestrator_graph_builder.py`):**

- `ROUTE → {MANAGER, PERSONA}` conditional on `route_decision.decision`
- `MANAGER → VERIFY` fixed edge
- `VERIFY → {AWAIT_INTERRUPT, MANAGER, ROUTE, PERSONA}` conditional on `interrupt_payload` + `orc.next_stage`
- `AWAIT_INTERRUPT → VERIFY` fixed edge (loop back for re-evaluation)
- `PERSONA → {END, ROUTE, MANAGER}` conditional on `orc.next_stage`

**Checkpoint integration (`core/orchestrator_graph.py`):**

- SQLite (default), in-memory, or disabled checkpoint modes
- Per-thread pruning with configurable history limit
- Recovery records on failure (`/graph resume`, `/graph clear`)
- Interrupt records on pause (`/graph resume` with user reply)
- State serialization / deserialization helpers (`snapshot_orchestrator_state`, `restore_orchestrator_state`)

**Visual debug (`orchestrator_graph_builder.py` → `save_piper_graph_visualization`):**

Renders compiled graph to PNG/Mermaid on every turn when `PIPER_DEBUG_LANGGRAPH_VISUALIZE=true`.

**Trace logging (`core/orchestrator_graph.py` → `_write_graph_trace`):**

Appends structured trace lines to `data/debug/langgraph_trace.jsonl` with stage timings, route decisions, and checkpoint metadata when `PIPER_DEBUG_LANGGRAPH_TRACE=true`.

**Feature flag:**

- `PIPER_USE_LANGGRAPH_ORCHESTRATOR` — enables graph runtime
- `PIPER_LANGGRAPH_CHECKPOINT_MODE` — `sqlite` / `memory` / `none`
- `PIPER_LANGGRAPH_CHECKPOINT_PATH` — SQLite file location
- `PIPER_LANGGRAPH_CHECKPOINT_HISTORY_LIMIT` — per-thread checkpoint retention
- `PIPER_DEBUG_LANGGRAPH_TRACE` — structured trace logging
- `PIPER_DEBUG_LANGGRAPH_VISUALIZE` — graph PNG/Mermaid output

**Validation:**

- `scripts/phase8_checklist_test.py` — 10/10 automated checklist tests pass (chat, search, file creation/edit, approval approve/deny, change-mind, memory, complex task, edge case)
- `scripts/langgraph_interrupt_smoke_test.py` — interrupt/resume roundtrip
- `scripts/langgraph_checkpoint_recovery_smoke_test.py` — checkpoint save/restore
- `scripts/langgraph_recovery_command_smoke_test.py` — `/graph resume` and `/graph clear`
- `scripts/orchestrator_graph_smoke_test.py` — graph compilation and basic invocation

**Files:** `core/graph_nodes.py` (node implementations); `core/orchestrator_graph_builder.py` (graph builder + visual debug); `core/orchestrator_graph.py` (checkpoint runtime, recovery, interrupt handling, trace logging); `core/orchestrator.py` (feature flag dispatch, `_run_langgraph()`); `config.py` (LangGraph env flags)

---

## Appendix: Engine vs. Utility

### What Is an Engine

An **engine** self-registers behavior via the hook or interceptor registry (`@register_hook`, `@register_tail_block`, `@register_route_interceptor`) so that orchestrator/executor/prompt layers invoke it indirectly through the registry, not by direct import and explicit call. Adding or removing an engine does not require editing `orchestrator_phases.py`, `route_normalizer.py`, or `prompt_context.py`.

A **utility** is imported and called directly by the orchestrator, executor, or prompt layer. It is tightly coupled to its caller.

**Important nuance:** Self-registering engines still need their module imported somewhere so decorators execute (usually in `core/engines/__init__.py` or via lazy import). The key distinction is **no feature-specific call sites** in orchestrator/route/prompt layers.

**Hybrid components:** A component may act as both an engine and a utility. In such cases, only the registry-driven behavior is considered "engine behavior"; direct calls remain utility behavior.

### Components Using the Registry (Live)

These files register hooks, tail blocks, or route interceptors at module load time:

| Component | Registration Type | File |
|-----------|----------------|------|
| ChangeJournal | `@register_hook("on_task_verified")` | `core/engines/change_journal.py` |
| ConversationCompressor | `@register_hook` | `core/engines/conversation_compressor.py` |
| ContextPackEngine | `@register_tail_block` | `core/engines/context_pack.py` |
| ProactiveMonitor | `@register_hook`, `@register_tail_block`, `@register_route_interceptor` | `core/engines/proactive_monitor.py` |
| StatsCollector | `@register_hook("on_pre_route")` | `core/engines/stats_collector.py` |
| File target confirmation | `@register_hook` | `core/engines/file_target_confirmation.py` |
| Turn explanation | `@register_hook` | `core/engines/turn_explanation.py` |
| Prompt context hooks | `@register_hook` | `core/prompt_context.py` |

**Note:** Some of these components also expose direct-call functionality; classification is based on how behavior is integrated, not file location.

### Directly-Called Utilities (Live)

These are imported and called explicitly by orchestrator/executor/prompt layers:

| Utility | Called From | File |
|---------|-------------|------|
| SummaryEngine | `orchestrator_phases.py` | `core/services/summary.py` |
| VerificationEngine | `orchestrator_phases.py` | `core/services/verification.py` |
| FileWorkEngine | `executor.py`, `file_stage_policy.py` | `core/services/file_work.py` |
| FollowupResolutionEngine | `orchestrator_phases.py` | `core/engines/followup_resolution.py` |
| RouteClarifier | `route_normalizer.py` | `core/services/route_clarity.py` |
| StateMutationEngine | `orchestrator_phases.py` | `core/engines/state_mutation.py` |
| ComputerUseEngine | `executor.py`, `tools/` | `core/engines/computer_use_engine.py` |
| ComputerUseVerifier | `executor.py` | `core/engines/computer_use_verifier.py` |
| RollbackEngine | `executor.py`, `orchestrator_phases.py` | `core/engines/rollback_engine.py` |

**Note:** Some of these live in `core/engines/` for historical reasons but function as utilities. Cleanup target: Phase 8+.

### Future Features: Use the Registry

New features that trigger on lifecycle events should self-register from day one. This requires:

1. A hook or interceptor exists in `core/feature_hooks.py` or `core/engines/context_pack.py`
2. The new component registers against it
3. No feature-specific call sites are added to orchestrator/executor/prompt code

If the needed hook does not exist, it must be added at the **hook system boundary** (central registry + fire point), not as a feature-specific call site. This prevents drift:

```python
# WRONG — do not do this
if feature_x:
    do_thing()

# RIGHT — add to registry, fire from existing hook point
@register_hook("on_turn_end")
def _feature_x_behavior(orc):
    do_thing()
```

---

## 14. TTS Pipeline

Documents the current text-to-speech flow from persona output to audio playback. This is not a staged change — it describes the live system as built.

### Overview

The TTS pipeline is fully non-blocking. Synthesis and playback run on background threads; the UI event pump and LLM generation are never stalled waiting for audio.

```
phase_persona() — persona text ready
    │  emits word-by-word stream deltas via orc.ui.put()
    ▼
controller_queue.py  [UI event pump, 60fps-throttled]
    │  assistant_stream_start / assistant_stream_delta / assistant_stream_end
    ▼
core/pipeline.py — ChatPipeline.handle_event()
    │  tag scrubbing (TagScrubber — strips [TOOL_NAME] commands)
    │  number cleaning (_clean_numbers_for_tts — decimals → "point", strip commas)
    │  stage direction parsing (StageDirectionProcessor — *action* markers)
    │  → text fragments   → tts.stream_push(text)
    │  → SFX markers      → tts.stream_flush() then tts.play_wav(path)
    ▼
tools/tts.py — TTS singleton (get_tts())
    │  lazy start: tts.stream_start() deferred until first real content delta
    │  (skips long thinking blocks; no wasted synthesis on <think> output)
    ▼
_StreamChunker — accumulates text, emits utterances to _job_q
    │  sentence-end regex: r"(?:(?<!\d)[.!?]|\n)"
    │  first utterance: min 20 chars, max 300 chars
    │  subsequent: min 300 chars, max 300 chars
    │  safety valve: force-split at 300 chars on whitespace if no boundary found
    │  newlines treated as hard stops (fixes list/bullet reading)
    ▼
_job_q  (synthesis queue — strict FIFO, epoch-tagged)
    │  items: ("text", text, voice, speed, backend) | ("sfx", filepath)
    ▼
_synth_loop worker thread
    │  backend chain: Kokoro ONNX → Kokoro Torch → Windows SAPI
    │  synthesizes text → numpy audio array
    │  for SFX: loads WAV, mono-converts, applies volume boost, clips to [-1, 1]
    │  discards stale jobs (epoch mismatch — stop() was called)
    ▼
_audio_q  (playback queue — epoch-tagged)
    ▼
_play_loop worker thread
    │  blocks until audio finishes playing
    ▼
audio out
```

### Lazy TTS start

`tts.stream_start()` is not called on `assistant_stream_start`. It is deferred to the **first real content delta** that passes through `ChatPipeline`. This means long `<think>` blocks (which `stream_thinking_filter` strips before they reach the pipeline) never trigger TTS initialization, and the first word a user hears is always real persona content.

### Text splitting

**Streaming path** (`_StreamChunker`, used during live persona output):
- Accumulates raw text fragments pushed by `stream_push()`
- Emits an utterance when a sentence boundary is found and the minimum size is met
- First utterance fires early (min 20 chars) to minimize perceived latency
- Subsequent utterances batch to ≥300 chars for smoother synthesis

**Non-streaming path** (`_split_3stage`, used by direct `speak()` calls):
- Splits on `[.!?;]\s+|\n+` into sentence-sized chunks (≤260 chars each)
- Packs sentences into 3 logical pipeline chunks: fast-start (~100 chars), normal (~500 chars), remainder
- Three-chunk design allows synthesis of chunk 2 to overlap with playback of chunk 1

### Stage direction processing

`*action*` markers in persona output are intercepted by `StageDirectionProcessor` in `ChatPipeline` before text reaches the TTS engine:

| Marker pattern | Behaviour |
|---|---|
| `*sigh*`, `*laugh*`, etc. | Flush current text utterance, then play matching `data/sfx/*.wav` |
| `*softly*`, `*sternly*`, etc. | Prepend semantic text cue before next utterance |
| `*smirk*`, `*pause*` | Insert "… " pause token into text stream |

SFX playback is ordered relative to the surrounding text via the shared `_job_q` (SFX jobs sit in-queue between the text jobs that surround them).

### Backend chain

| Priority | Backend | Class | Notes |
|---|---|---|---|
| 1 (primary) | Kokoro ONNX | `_KokoroEngine` | `kokoro-v1.0.onnx` + `voices-v1.0.bin`; lazy model load |
| 2 (fallback) | Kokoro Torch | `_KokoroTorchEngine` | Subprocess worker; HF hub `hexgrad/Kokoro-82M`; Windows probe patch |
| 3 (last resort) | Windows SAPI | `_WindowsSystemSpeechEngine` | `pyttsx3`; system-installed voices only |

Backend is selected per synthesis job; if the primary engine raises an exception the synth worker steps down the chain automatically.

### Epoch-based cancellation

Each `stop()` call increments `_epoch`. The synthesis worker checks epoch before pushing to `_audio_q`; any job whose epoch is older than the current value is silently discarded. This ensures a hard stop (e.g. user interrupts mid-sentence) drains cleanly without deadlock.

### Configuration keys

`config.py`: `TTS_ENABLED`, `TTS_BACKEND` (`"auto"`), `TTS_VOICE` (`"af_heart"`), `TTS_SPEED` (`0.85`), `TTS_KOKORO_TIMEOUT_S`, `TTS_KOKORO_TORCH_READY_WAIT_S`, `TTS_KOKORO_HF_REPO_ID`, `KOKORO_DIR`, `KOKORO_MODEL`, `KOKORO_VOICES`.

### Key files

`tools/tts.py` — TTS singleton, all three backend engines, `_StreamChunker`, `_split_3stage`, synth/play workers, epoch tracking; `core/pipeline.py` — `ChatPipeline`, `TagScrubber`, `StageDirectionProcessor`, number cleaning; `ui/controller_queue.py` — UI event pump, 60fps delta throttle; `core/orchestrator_phases.py` — persona stream emission; `app.py` — singleton init (`get_tts(TTSConfig(...))`); `scripts/tts_windows_probe.py` — latency benchmark tool
