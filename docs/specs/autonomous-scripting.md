# Autonomous Scripting

Status: Proposal spec

This document is the focused planning surface for autonomous script creation.

## Purpose

Piper should eventually recognize when a task is too large, too repetitive, or too precise for manual file-by-file handling, and choose to write a small script to complete it more effectively.

This is not the same as ordinary `RUN_CODE`.

The key difference is:
- `RUN_CODE` today is an explicit execution tool inside a user-requested task
- autonomous scripting would add planner/runtime recognition that script creation is the right method before the user asks for code directly

## Design Goals

- use scripting as a means, not as a user-facing product surface
- keep execution sandboxed and verifiable
- disclose what Piper did conversationally
- fall back to manual methods when script generation/execution is unsafe or fails

## Non-Goals

- silent unrestricted script execution
- networked autonomous coding
- bypassing Router, Executor, or verification rails
- replacing existing structured tools when they are already sufficient

## Candidate Use Cases

- bulk file renaming or filtering
- EXIF-driven photo organization
- batch summarization of many notes/documents
- high-volume structured extraction from a workspace folder
- repetitive transforms that are too slow or error-prone by hand

## Safety Model

- workspace-jail or equivalent sandbox only
- no arbitrary system access
- no unrestricted network access
- artifact/output validation before reporting success
- clear disclosure to the user that Piper used a generated helper script

## Why Deferred

- requires robust recognition of when scripting is actually warranted
- increases context and debugging pressure
- needs very clear validation rules to avoid false-success narration
- should follow repeated real user pain, not just theoretical convenience

## Trigger For Promotion

Promote this work only after repeated concrete evidence that existing tools are too slow or awkward for common user tasks.

## Likely File Surfaces

- `core/autonomous_scripts.py`
- `core/engines/script_library.py`
- executor/planner integration for generated helper scripts

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if this work becomes active, `docs/WIP.md` should carry live branch status.
