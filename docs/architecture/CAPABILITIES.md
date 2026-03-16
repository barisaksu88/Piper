# Piper Capabilities

Lean summary of the current user-facing capability surface.

## Core Interaction

- Local-first agent with streamed replies.
- Routes each turn into `CHAT`, `SEARCH`, or `TASK`.
- Shows `Thinking...` while background work is running, then replaces it when streaming starts.
- Can stop active work and report from verified runtime state instead of bluffing.

## Memory and Personal Context

- Persistent chat history.
- Durable world-model memory with entities, attributes, and relationships about the user's life.
- Temporary situational state for active moods, hesitations, and other short-lived user context.
- Legacy flat knowledge remains mirrored for compatibility, but world-model memory is the primary durable profile layer.
- Vector memory recall for past conversational facts.
- Explicit self-recall via `[RECALL: keywords]` when Piper needs memory before answering.
- Tasks and events are kept in local state and surfaced in normal conversation.

## Tasks and Events

- Add, complete, and list undated tasks.
- Add, complete, and list dated events, reminders, appointments, and deadlines.
- Read-only task/event status questions stay conversational instead of entering task execution.

## Document Handling

- Ingest documents from the `Documents` tab or via `/ingest <path>`.
- Supports plain text, code, JSON, Markdown, PDF, and DOCX ingestion.
- Stores ingested documents in vector memory for later question answering.
- Uses focused document extraction so replies are based on only the relevant sections.
- Shows page/section refs in the activity pane for document answers.
- Uses PDF-page vision fallback for image-heavy/manual-style pages when plain text extraction is not enough.

## Workspace and Code

- Inspect, read, create, edit, move, copy, and delete files inside the workspace.
- Organize folders and files, including extension-based cleanup and consolidation.
- Perform file lookup from fuzzy natural-language references.
- Run existing workspace Python scripts.
- Route active script I/O into the embedded `Code` tab as an interactive session.
- Verify file-work outcomes from actual artifact state before reporting success.

## Vision and Images

- Create images.
- Edit images.
- Preview generated or selected images in `Visual Cortex`.
- Answer `/vision` questions about local image files.
- Use live screen context from `Display`, `Window`, or `Pointer` capture modes.
- Read visible on-screen text, buttons, labels, tabs, filenames, and general scene content.

## Search and Voice

- Run background web searches and summarize the results.
- Use microphone input.
- Speak replies through TTS.

## UI Surfaces

- Main tabs: `Chat`, `Visual Cortex`, `Code`.
- Right-side tabs: `Status`, `Documents`, `Monitor`.
- Embedded document ingest flow with a file picker.
- Embedded code console for running-script interaction.
- Activity pane for routing, document refs, and runtime progress.

## Current Limits

- Scanned or image-only PDFs are still weaker than text PDFs, though the visual fallback improves some cases.
- Document answers are intentionally source-bound; if the supplied pages do not support a claim, Piper should say it does not know.
