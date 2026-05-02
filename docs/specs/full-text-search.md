# Full-Text Search

Status: Proposal spec

This document is the focused planning surface for a local exact-match search layer.

## Purpose

Semantic recall is useful, but Piper also needs exact keyword retrieval for:
- commands
- snippets
- filenames
- exact previously seen lines

Full-text search should complement vector recall, not replace it.

## Design Goals

- provide exact-match retrieval alongside semantic memory
- keep one clear owner module
- stay compatible with the repo's data-hygiene rules
- make literal evidence distinguishable from semantic recall

## Non-Goals

- replacing vector recall
- scattering ad hoc text indexes across multiple layers
- unbounded ingestion or write growth

## Architecture Guardrails

- single owner under `memory/`
- bounded ingestion and write-path pruning/caps
- retrieval output should clearly distinguish literal hits from semantic matches

## Value

- exact retrieval where semantic recall is too fuzzy
- better support for command/snippet recall
- stronger evidence for literal matches

## Likely File Surfaces

- `memory/search_engine.py`
- `core/search_contracts.py`
- `core/engines/context_pack.py`
- retrieval plumbing in search/memory paths

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
