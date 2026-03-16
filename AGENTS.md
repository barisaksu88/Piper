
# PIPER AGENT ARCHITECTURE DOCTRINE
Version: 2.4
Status: Active

This file defines architectural boundaries and behavioral rules that **AI agents must respect when modifying this repository**.

---

# 1. PHILOSOPHY

Piper is an **Agentic Loop System**, not a simple chatbot.

Core execution model:

Route → Plan → Act → Speak

Route: classify intent and produce a mission plan  
Plan: determine next action within a stage  
Act: execute a restricted tool  
Speak: report outcome naturally to the user

Execution truthfulness outranks fluent narration.
The system must trust:

- structured tool results
- repository-backed state
- explicit verification

The system must not trust:

- planner narration
- persona narration
- printed success claims without evidence

---

# 2. SYSTEM LAYERS

The system is organized into three functional layers.

## Layer 1 — Orchestrator (Director)
File: core/orchestrator.py

Responsibilities:
- Accept user input
- Call Router to classify intent
- Dispatch tasks to Executor
- Call Persona to produce final response
- Maintain high-level state for a request

The Orchestrator decides **domains**, not the LLM.

## Layer 2 — Executor (Worker)
File: core/executor.py

Responsibilities:
- Execute stages from the task card
- Maintain Scratchpad memory
- Enforce tool restrictions
- Call Inspector to verify stage progress
- Return structured execution logs

Executor controls the **step loop**.

## Layer 3 — Prompt Builder (Architect)
File: core/prompting.py

Responsibilities:
- Format LLM inputs
- Assemble PromptContext
- Resolve domains into allowed tools
- Produce JSON payloads

Prompt construction must remain **pure**.
It must not directly access memory, weather, search, or filesystem.

---

# 3. LLM ROLES

The system defines logical roles implemented via prompts:

Router (Secretary)
Planner (Manager)
Inspector (Validator)
Persona (Speaker)

These are logical roles, not separate models.

---

# 4. EXECUTION LIFECYCLE

1. Routing Phase
Router classifies the user request.

Output JSON:
CHAT
SEARCH
TASK

TASK produces a Stage Card.

2. Execution Phase

Executor processes each stage.

For each step:

Planner decides next tool  
Tool executes  
Observation recorded in Scratchpad  
Verification classifies the result as VERIFIED, PARTIAL, or FAILED  
Inspector evaluates progress

3. Persona Phase

Persona reads the final outcome block and produces the user response.
Persona may only claim success that is supported by completed or verified execution state.

---

# 5. STAGE CARD STRUCTURE

A task is decomposed into stages.

Each Stage Card contains:

stage_goal
stage_type
success_condition

Stage types define the allowed tool domain.

Example domains:

FILE_WORK  
IMAGE_WORK  
MEMORY_WORK  
SEARCH_WORK

Router selects domain. Planner cannot change domains.

---

# 6. SCRATCHPAD STRUCTURE

Scratchpad is the persistent execution log.

Format:

=== STAGE START ===
STAGE_GOAL
STEP
THOUGHT
ACTION
OBSERVATION_KIND
OBSERVATION_TEXT
=== STAGE OUTCOME ===
RESULT

Scratchpad flows between stages.

For FILE_WORK and other verification-heavy stages, Scratchpad may also contain:

FILE_CHECKER_VERDICT  
FILE_CHECKER_REASON  
FILE_CHECKER_EVIDENCE

These verification entries are authoritative for stage completion.

---

# 7. TOOL DOMAIN SAFETY

The Router selects domains.
The Prompt Builder resolves domains to tool lists.

Example:

FILE_WORK → RUN_CODE  
IMAGE_WORK → CREATE_IMAGE, MODIFY_IMAGE

This prevents the Planner from invoking unrelated tools.

---

# 8. DATA CONTRACTS

All structured messages exchanged between components must eventually use explicit schemas.

Important contracts:

RouteDecision  
StageCard  
PlannerDecision  
ToolResult  
FileCheckDecision  
UiEvent

Future work should migrate these to typed models such as:

Python dataclasses  
TypedDict  
Pydantic models

Schemas prevent silent breakage between modules.

---

# 9. CODE ORGANIZATION GOALS

Piper should evolve toward:

- clear separation of concerns
- single responsibility modules
- minimal cross-module coupling
- explicit data flow
- orchestration centralized in orchestrator/services
- file-backed state owned by repository classes

Avoid sideways imports across layers.

Preferred dependency direction:

ui → app → core → memory/tools/llm

Lower layers must not depend on higher layers.

---

# 10. PERSISTENCE RULES

Files representing shared state must have a single owner module.

Examples:

memory store  
chat history  
knowledge base  
workspace files

Direct multi-module file access is prohibited.

---

# 11. TOOL DEFINITIONS

Tool metadata should exist in a single source of truth.

Avoid duplication between:

ad hoc tool reference files  
prompt templates  
runtime registries

Prefer defining tools in Python modules and generating prompt text from them.

---

# 12. RUNTIME ENVIRONMENT

Piper runtime is **Windows-first**.

Entrypoints:

app.py  
start_piper.bat

WSL may be used for development and analysis but not full runtime execution unless explicitly requested.

Agents should avoid launching Windows GUI or audio systems inside WSL.

Current active local runtime model:

Qwen_Qwen3.5-9B-Q6_K.gguf

Current local runtime policy:

- Qwen3.5-9B-Q6_K is the active default
- qwen2.5-14b remains the fallback comparison baseline
- reasoning-budget should remain disabled for the current qwen3.5 llama.cpp path unless explicitly re-evaluated
- model optimism must be contained by verification rails, not by trusting narration

---

# 13. NON-GOALS

This architecture intentionally avoids:

fully autonomous self-modifying behavior  
unrestricted tool execution  
cross-layer imports  
tool calls directly from UI code  
monolithic orchestration logic

---

# 14. EXECUTION TRUTHFULNESS DOCTRINE

Natural-language success is never authoritative.

Rules:

- Do not accept "it worked" unless tool or state evidence proves it
- Do not accept persona phrasing as execution truth
- Do not mark a stage complete only because the model says it is complete
- Verification must be state-based or artifact-based whenever possible
- For direct state-change tools, success should be derived from explicit tool contracts
- For file and code tasks, success must be derived from post-action evidence

The system must distinguish:

- VERIFIED: requested final state is proven by evidence
- PARTIAL: some progress happened, but completion is not proven
- FAILED: execution failed, was blocked, or produced no meaningful evidence

PARTIAL is not success.
PARTIAL is a first-class execution state and must not be narrated as complete.

---

# 15. FILE_WORK DOCTRINE

FILE_WORK is the highest-risk domain for false success.

Rules:

- Prefer structured FILE_OP actions over RUN_CODE for direct workspace inspection and path operations
- Non-mutating FILE_WORK stages must stay non-mutating at runtime, not just by prompt convention
- Approval-first FILE_WORK turns must not mutate workspace state before the user confirms
- RUN_CODE must return structured execution evidence, not only freeform text
- Workspace diffs are required for mutation claims
- Printed output is not proof of file modification
- If the stage goal changes a file, the file itself must change
- After RUN_CODE, a checker layer must verify artifact state from evidence
- FILE_WORK stages must not complete until the checker returns VERIFIED
- If the checker returns PARTIAL, the planner may continue only if progress remains possible
- If the checker returns FAILED, the failure must remain visible to the system
- Local deterministic checker rules should verify FILE_OP path operations from real state whenever possible instead of delegating everything back to the model

Preferred FILE_WORK flow:

inspect  
modify  
verify  
stop

Preferred structured actions:

inspect → FILE_OP list_tree / read_text / read_many  
prepare dirs → FILE_OP ensure_dir / ensure_dirs  
reorganize → FILE_OP move_path / move_many  
copy → FILE_OP copy_path / copy_many  
delete → FILE_OP delete_path / delete_many

Do not allow repeated edits without artifact improvement.
Do not allow repeated verification of an unchanged artifact.

---

# 16. CHECKER LAYER

Checker layers exist to externalize truthfulness away from the model.

Required behavior:

- checker input must be schema-bound
- checker output must be schema-bound
- checker decisions must be stored in the execution log
- checker decisions must be available to the Inspector
- checker decisions must govern completion for code/file stages

Checkers are allowed to be conservative.
When uncertain, prefer PARTIAL or FAILED over VERIFIED.

---

# 17. REFACTORING PHILOSOPHY

Agents should prefer:

small safe refactors  
boundary improvements  
incremental migration

Large rewrites should be proposed first before implementation.

---

# 17A. REPO SWEEP SHORTHAND

The phrase `Repo Sweep` is reserved shorthand for repo-wide cleanup and consistency work.

Variants:

`Repo Sweep Light`
- apply the current repository rules and doctrine across the codebase
- fix obvious violations and low-risk inconsistencies repo-wide
- do not turn it into an open-ended autonomous campaign
- stop after the current pass and report what changed

`Repo Sweep Hard`
- perform the same repo-wide cleanup work, but continue iterating without asking for permission to keep going
- agents may touch any relevant file in the repository to finish the sweep cleanly
- agents should keep working until the targeted sweep is materially complete or a real blocker appears
- agents should validate behavior after each substantial pass using the strongest available regression surfaces
- minimum validation for a substantial sweep pass should include:
- `python -m compileall app.py config.py core ui memory tools llm harness scripts`
- `scripts/code_session_smoke_test.py --json`
- `scripts/file_edit_smoke_test.py --json`
- `scripts/file_lookup_smoke_test.py --json`
- `scripts/file_crud_smoke_test.py --json`
- `scripts/file_chaos_test.py --json`
- when these harness/model checks share one local llama-server lifecycle, run them sequentially rather than in parallel to avoid false failures from overlapping server boot/shutdown

Interpretation rules:

- `Repo Sweep Light` is a bounded pass
- `Repo Sweep Hard` is an autonomous multi-pass cleanup mode
- if the user says only `Repo Sweep`, default to `Repo Sweep Light`
- these phrases apply only to cleanup / consistency / refactor work, not to unrelated feature building unless the user says otherwise

---

# 18. PROJECT STRUCTURE

Piper/

app.py
config.py

core/
orchestrator.py
executor.py
prompting.py
agent.py
style.py
engines/
skills/

data/
prompts/
workspace/

docs/
architecture/
v1/

tools/
interpreter.py
image_gen.py
search.py

AGENTS.md
docs/architecture/ARCHITECTURE.md

---

# 19. UPDATE HISTORY

v2.5 — clarified `Repo Sweep Hard` validation expectations and codified the current compile/smoke regression pack, including sequential execution for llama-server-backed harness checks  
v2.4 — added `Repo Sweep Light` and `Repo Sweep Hard` shorthand for bounded vs autonomous repo-wide cleanup passes  
v2.3 — hardened FILE_WORK with structured FILE_OP path-operation rails, runtime enforcement for non-mutating stages, and deterministic local verification for directory and path operations  
v2.2 — activated qwen3.5 q6 as the default model, added execution truthfulness doctrine, PARTIAL vs VERIFIED semantics, and checker-layer rules for FILE_WORK  
v2.1 — clarified architecture boundaries, added schemas, added non-goals and agent rules  
v2.0 — introduced Director/Worker separation and stage execution model  
v1.5 — introduced persistent scratchpad  
v1.0 — initial agent architecture

---

# 20. CODER NOTE SYSTEM

The repository includes a repo-local note system for future coding passes.

Location:

notes/

Purpose:

- preserve validated runtime knowledge when chat context is compressed
- record known-good configurations and workflows
- record known issues, regressions, and model-specific failure modes
- record short implementation lessons that would otherwise be lost between sessions

These notes are operational memory for coders.
They are not architecture authority.
AGENTS.md remains the authoritative doctrine.

Agents are explicitly allowed to create, update, prune, and reorganize files inside notes/ without asking for separate permission when doing so improves future continuity.

Required note hygiene:

- keep notes concise and high-signal
- prefer short dated entries over long narratives
- remove or rewrite stale notes when the code changes
- distinguish clearly between known-good behavior and known issues
- do not duplicate large code snippets
- do not treat notes as proof; verify against code when stakes are high

Preferred files:

- notes/known-good.md
- notes/known-issues.md
- notes/coder-log.md

When an agent discovers:

- a recurring bug
- a model-specific weakness
- a runtime setup detail that must not be forgotten
- a fix that worked after repeated failures

it should update the relevant note file in the same turn if practical.
