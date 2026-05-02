# Tiered Smoke Suite

Status: Proposal spec

This document is the focused planning surface for a tiered smoke-test layout.

## Purpose

The current smoke runner is useful, but the suite still mixes:
- fast deterministic checks
- heavier harness-backed tests
- llama-backed or more volatile integration runs

The goal is a clearer tier system so the default path stays high-signal without hiding broader coverage.

## Design Goals

- keep a stable fast default tier
- make heavier tiers opt-in and explicit
- quarantine known-flaky or currently-red surfaces without pretending they do not exist

## Non-Goals

- deleting broader integration coverage
- building this while active browser computer-use churn is still high
- hiding test risk behind a minimal default run

## Planned Shape

- stable default tier for fast, reliable smoke tests
- opt-in extended tier for slower integration coverage
- quarantined tier for known-flaky or currently-red tests
- per-test timeout overrides and brief reason strings for exclusions

## Timing Rule

Do not prioritize this during active `computer use v0` stabilization.
Use the existing lightweight runner filter until the volatile harness tier is boring enough that classification will stick.

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
