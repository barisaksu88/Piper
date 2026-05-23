# Workspace Code Indexing

Status: Proposal spec

This document is the focused planning surface for structural workspace code indexing.

## Purpose

Piper can already find files by text similarity, but that does not provide a structural map of code.

Code indexing should let Piper reason about:
- where functions/classes are defined
- what calls what
- imports and references
- structural rename or lookup tasks

## Design Goals

- add structural understanding without replacing existing file/search tools
- improve code-navigation precision
- support exact code-reference questions with less blind file reading

## Non-Goals

- replacing all workspace search with indexing
- building this before there is proven user demand
- tying the feature to non-code users by default

## Value

- more precise code lookup
- safer refactor assistance
- better answers to structural questions like "where is this defined and what calls it?"

## Why It Stays Behind Computer Use

Browser-first computer use benefits every user.
Structural code indexing mainly benefits users who keep code in Piper's workspace.

## Promotion Trigger

Promote this work when real workspace-code usage shows that text search alone is not precise enough.

## Likely File Surfaces

- `core/indexing.py`
- workspace file watcher / incremental update path
- query interface for planner/runtime use

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
