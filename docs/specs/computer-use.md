# Computer Use

Status: Active spec and consolidation point

This document is the single planning/reference surface for Piper computer use.

Use it for:
- browser-first computer-use design
- current shipped foundation summary
- active follow-up status
- verification expectations

Do not use scattered `ROADMAP.md` sections and notes as separate authorities.

## Purpose

Computer use lets Piper act inside a constrained browser session while staying inside the existing:

Route -> Plan -> Act -> Speak

shape.

The design goal is not "automation at any cost."
The design goal is:
- bounded scope
- structured actions
- deterministic verification
- honest stopping on uncertainty

## Current Runtime Truth

The repo already has a real browser-first `COMPUTER_USE` path and harness surface.

Primary runtime surfaces:
- [`core/engines/computer_use_engine.py`](../../core/engines/computer_use_engine.py)
- [`core/services/computer_use_verifier.py`](../../core/services/computer_use_verifier.py)
- [`core/executor.py`](../../core/executor.py)
- [`core/planner_boundary.py`](../../core/planner_boundary.py)
- [`core/prompt_builder.py`](../../core/prompt_builder.py)
- [`core/routing/route_normalizer.py`](../../core/routing/route_normalizer.py)
- [`tools/registry.py`](../../tools/registry.py)

Validation surface examples:
- [`scripts/computer_use_engine_smoke_test.py`](../../scripts/computer_use_engine_smoke_test.py)
- [`scripts/computer_use_harness_smoke_test.py`](../../scripts/computer_use_harness_smoke_test.py)
- [`scripts/computer_use_browser_followup_harness_smoke_test.py`](../../scripts/computer_use_browser_followup_harness_smoke_test.py)
- [`scripts/computer_use_extract_download_harness_smoke_test.py`](../../scripts/computer_use_extract_download_harness_smoke_test.py)
- [`scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py`](../../scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py)

## Shipped Foundation

- Browser-first computer use is a real `TASK` path, not only a future idea.
- The stage domain is `COMPUTER_USE`.
- The engine uses a Piper-owned browser/session model.
- Structured browser operations exist behind a constrained execution model.
- Routing and follow-up handling already support browser-focused turns.
- There is real deterministic harness coverage around routing, extraction, follow-up behavior, downloads, and blocked-domain behavior.

## Design Center

Computer use should remain:
- browser-first
- verification-first
- scope-bounded
- explicit about failures

It should not become:
- freeform desktop automation by default
- hidden browser hijacking
- narration-driven success

## Runtime Shape

Computer use stays inside the normal top-level turn flow:
- Router returns `TASK`
- stage type is `COMPUTER_USE`
- planner receives only the allowed computer-use surface
- executor runs browser actions
- verification decides what is actually true
- Persona reports only verified outcomes

This avoids inventing a parallel route system for browser work.

## Verification Model

Primary proof should be deterministic browser/runtime evidence such as:
- current URL
- origin/domain
- page title
- element presence or absence
- visibility/enabled state
- field value after interaction
- extracted text payload
- download path / artifact evidence

Vision can support recovery or ambiguity handling, but it should not replace deterministic browser proof as the main source of truth.

## Current Active Behavior

From code, notes, and harness surfaces, the active behavior already includes:
- browser-first `COMPUTER_USE` routing
- owned session/browser context behavior
- browser follow-up continuity
- blocked-domain handling
- extraction and download flows
- harness-backed local and live-site verification slices

This means future work should start from "stabilize and document the current system," not "design computer use from scratch again."

## Remaining Active Work

### 1. Shipped-vs-spec cleanup

Older roadmap text still reads partly like a future design even though substantial runtime behavior is already live.
Continue separating:
- already-shipped behavior
- active stabilization
- future expansion

### 2. Stability and evidence

Keep growing confidence through deterministic harnesses and known-good notes rather than broadening scope too early.

### 3. Trigger-flow promotion

As behavior stabilizes, ensure the relevant computer-use truth stays documented in:
- [`docs/architecture/TRIGGER_FLOW.md`](../architecture/TRIGGER_FLOW.md)
- [`docs/architecture/CAPABILITIES.md`](../architecture/CAPABILITIES.md)

### 4. Keep desktop expansion separate

Desktop/native automation is a later phase and should not blur the browser-first safety model.

## Future Improvements

These remain future-facing unless runtime/code proves otherwise:
- broader browser harness coverage
- cleaner user-facing browser controls/settings
- more resilient extraction strategies
- better artifact/download handling polish
- desktop computer-use expansion as a separate phase

## Deprecated Assumptions

Future agents should avoid these stale assumptions:
- "computer use is only a roadmap item"
- "computer use needs a new top-level route kind"
- "browser success can be narrated without deterministic evidence"
- "desktop and browser automation should be designed together from the start"

## Doc Placement Rules

- `AGENTS.md` defines doctrine and safety boundaries.
- `docs/WIP.md` tracks current computer-use follow-up status.
- `docs/architecture/TRIGGER_FLOW.md` holds shipped runtime truth.
- `docs/ROADMAP.md` should point here for the main computer-use design surface.
- notes and harness outputs remain evidence, not doctrine.
