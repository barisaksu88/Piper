# Notebook-Aware File Tools

Status: Proposal spec

This document is the focused planning surface for notebook-aware workspace tooling.

## Purpose

Piper can already inspect and edit ordinary code files, but data-science and research workflows often live in `.ipynb` notebooks.

Notebook-aware tooling should let Piper:
- inspect cells structurally
- edit notebooks with explicit intent
- rerun notebooks for verification where appropriate
- keep notebook outputs/evidence manageable

## Design Goals

- prefer structured notebook actions over opaque blob handling
- maintain verification-friendly execution evidence
- stay consistent with existing `FILE_WORK` doctrine

## Non-Goals

- treating notebooks like unstructured binary blobs
- shoving large rich outputs wholesale into JSON logs
- mutating notebook content during read-only inspection turns

## Architecture Guardrails

- structured notebook actions or an explicit notebook work surface
- data-hygiene compliance for large/binary-rich outputs
- non-mutating notebook inspection must stay non-mutating at runtime

## Value

- better support for research and reproducible analysis workflows
- stronger notebook inspection/editing than plain text approximations

## Likely File Surfaces

- `tools/registry.py`
- `tools/workspace_runtime.py` or notebook-specific runtime modules
- `core/file_stage_policy.py`
- possible `FILE_OP` notebook actions or a `NOTEBOOK_WORK` domain

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
