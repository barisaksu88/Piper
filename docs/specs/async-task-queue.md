# Async Task Queue

Status: Proposal spec

This document is the focused planning surface for background task queue support.

## Purpose

Long-running operations such as indexing, document ingestion, or extended code execution should not freeze the chat loop.

The queue should let Piper:
- enqueue background work
- surface progress and status
- allow continued interaction where safe

## Design Goals

- make long jobs manageable without bypassing core safety rails
- keep progress visible to the UI
- preserve cancellation, failure, and partial completion as real states

## Non-Goals

- a back door around Router, Executor, or checker behavior
- silent background mutation without status visibility
- narration-driven job completion

## Architecture Guardrails

- queueing is a scheduling layer, not a replacement for the turn/executor model
- background jobs must expose explicit progress/status events
- cancellation and failure must remain first-class runtime outcomes

## Value

- large jobs stop blocking the whole interaction loop
- background work becomes inspectable and manageable
- users can continue interacting while long tasks proceed

## Likely File Surfaces

- `core/task_queue.py`
- `core/orchestrator.py`
- `ui/controller_queue.py`
- background worker / progress plumbing

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
