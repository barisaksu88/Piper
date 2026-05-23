# Task Eval Harness

Status: Proposal spec

This document is the focused planning surface for a structured task-evaluation layer.

## Purpose

Piper already has smoke tests and harnesses, but it still needs a way to measure whether it got the right end-to-end result across real tasks.

The eval harness should score:
- route accuracy
- stage completion truthfulness
- checker alignment
- final answer quality relative to verified execution evidence

## Design Goals

- grade execution quality, not just crash resistance
- rely on structured runtime evidence
- separate results by domain so regressions stay diagnosable
- start deterministic and only widen later

## Non-Goals

- replacing smoke tests
- grading Persona polish ahead of verified outcomes
- starting with fragile live integrations as the default evaluation surface

## Architecture Guardrails

- score from `VERIFIED` / `PARTIAL` / `FAILED`, checker outputs, tool logs, and structured outcomes
- use deterministic fixtures/golden tasks first
- keep domain-specific reporting so failures are attributable

## Value

- a durable regression signal for whether Piper is actually getting better
- stronger protection against execution-quality drift
- evidence that complements smoke tests and manual review

## Likely File Surfaces

- `tests/eval/` or `AGENTS/harness/eval/`
- `scripts/` eval runners
- score-reporting docs and fixtures

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
