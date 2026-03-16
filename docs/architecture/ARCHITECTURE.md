# Piper Architecture

This document describes the current repository structure and runtime wiring.

`AGENTS.md` is the authoritative doctrine. If this file and `AGENTS.md` ever diverge, follow `AGENTS.md`.

## 1. System Shape

Piper is a Windows-first local agent system with a Dear PyGui shell, a llama.cpp-backed LLM runtime, persistent user memory, vector recall, workspace tooling, and image generation.

High-level execution flow:

1. UI accepts user input.
2. Core orchestrator routes the turn into `CHAT`, `SEARCH`, or `TASK`.
3. Task turns run through the stage executor with tool restrictions and verification.
4. Persona produces the user-facing reply from verified runtime state.

## 2. Runtime Layers

### UI Shell

Primary files:

- `app.py`
- `ui/controller.py`
- `ui/layout.py`
- `ui/controller_actions.py`
- `ui/controller_queue.py`
- `ui/controller_status.py`

Responsibilities:

- create the viewport and tabs
- manage input, stop, restart, mic, and document-ingest UI actions
- render chat, monitor logs, status, visual cortex output, code view, and ingested documents
- keep controls disabled during boot and active operations
- bridge UI events to the core orchestrator

### Orchestrator Layer

Primary files:

- `core/orchestrator.py`
- `core/orchestrator_phases.py`

Responsibilities:

- own the turn lifecycle
- call the router
- dispatch search and task flows
- collect stage scratchpad output
- hand final verified outcome to persona

This is the Director layer described in `AGENTS.md`.

### Execution Layer

Primary files:

- `core/executor.py`
- `core/executor_support.py`
- `core/file_stage_policy.py`
- `core/file_checker.py`
- `core/file_checker_rules.py`

Responsibilities:

- run one stage at a time
- enforce allowed tools per stage
- record scratchpad observations
- verify `FILE_WORK` outcomes from actual artifact state
- decide when to continue, stop, or pause for approval

This is the Worker layer described in `AGENTS.md`.

### Prompt Construction Layer

Primary files:

- `core/prompting.py`
- `core/prompt_builder.py`
- `core/prompt_context.py`
- `core/scratchpad_formatter.py`

Responsibilities:

- load instructions and prompt templates
- assemble prompt context from owned stores and runtime environment
- render planner, inspector, and persona prompts
- format scratchpad blocks for downstream phases

Important boundary:

- `PromptBuilder` is render-only.
- `ContextPackEngine` is the context assembly boundary that builds persona/runtime working sets before prompt rendering.
- `PromptContextService` is the integration facade that exposes that engine to the rest of the runtime.

## 3. Memory and State

### Chat and State Stores

Primary files:

- `memory/chat_state.py`
- `memory/stores.py`
- `memory/storage.py`

Owned state under `data/state`:

- `memory.jsonl`
- `knowledge.json`
- `world_model.json`
- `tasks.json`
- `events.json`
- `model_selection.json`
- `ingested_documents.json`

### Knowledge and Vector Memory

Primary files:

- `memory/knowledge.py`
- `memory/knowledge_prompts.py`
- `memory/brain.py`
- `memory/documents.py`

Responsibilities:

- `WorldModelManager` maintains a graph-backed life/world model in `world_model.json`
- active transient world-model entries can also be rendered as a separate situational-state prompt block for temporary user context
- `knowledge.json` remains as a derived compatibility mirror for legacy tooling and simple inspection
- `PiperBrain` stores conversational vector memories in Chroma collection `piper_memory`
- `DocumentMemoryManager` stores ingested document metadata plus document vectors in Chroma collection `piper_documents`

Vector store location:

- `data/vector_store`

Current document behavior:

- each ingested document is stored as a single vector document
- persona context appends up to five relevant ingested document excerpts
- those excerpts are query-focused snippets from the matched document text rather than always the beginning of the file
- read-only ingested-document questions can run an internal `DOCUMENT_FOCUS` pass that condenses those snippets before the final persona reply
- persona may request explicit vector-memory recall via `[RECALL: keywords]`

## 4. Tools and Capabilities

### Workspace and File Work

Primary files:

- `tools/workspace_runtime.py`
- `tools/workspace_file_actions.py`
- `tools/workspace_query_actions.py`
- `tools/workspace_mutation_actions.py`
- `tools/workspace_extension_actions.py`
- `tools/workspace_extension_ops.py`
- `tools/interpreter.py`
- `tools/registry.py`

Responsibilities:

- structured `FILE_OP` actions for direct workspace inspection and mutation
- guarded `RUN_CODE` execution inside the workspace
- extension-based organization helpers
- tool metadata as the single runtime source of truth

### Search, Image, Audio, and LLM

Primary files:

- `tools/search.py`
- `tools/image_gen.py`
- `tools/stt.py`
- `tools/tts.py`
- `llm/boot.py`
- `llm/llm_server_client.py`

Responsibilities:

- background web search and summarization
- ComfyUI-backed image generation and editing
- speech-to-text and text-to-speech
- local llama-server boot, pause/resume, and chat-completions transport

## 5. Request Flow

### Chat Turn

1. UI appends the user message.
2. UI shows `Thinking...` while the task runs in the background.
3. Orchestrator routes the turn.
4. If the turn is a read-only ingested-document question, an internal document-focus pass condenses the relevant excerpts before persona.
5. Persona streams the assistant reply.
6. The placeholder is replaced on the first streamed assistant tokens.

### Task Turn

1. Router returns a `TASK` card with stages.
2. Executor loops planner -> tool -> observation -> verification.
3. Inspector decides continue or finish.
4. Persona speaks only from verified or authoritative outcome state.

### Search Turn

1. Router returns `SEARCH`.
2. Search runs in the background.
3. Reporter summarizes results.
4. Persona gives the final user-facing answer.

## 6. UI Surfaces

Current top-level tabs:

- `Chat`
- `Visual Cortex`
- `Code`

Current right-side tabs inside `Chat`:

- `Status`
- `Documents`
- `Monitor`

## 7. Design Rules

- Execution truthfulness outranks fluent narration.
- File and code success must come from artifact evidence, not model claims.
- Tool domains are selected by routing/prompt policy, not by free-form model choice.
- Shared state files should have one owning module.
- Lower layers should not depend on higher layers.

## 8. Current Gaps

- PDF ingestion currently depends on `pypdf` text extraction and will be weak on scanned/image-only PDFs.
- DOCX ingestion currently extracts text directly from `word/document.xml`; it is sufficient for plain text content but not a full Word semantic parser.
- `ARCHITECTURE.md` is descriptive, not normative; repo doctrine remains in `AGENTS.md`.
