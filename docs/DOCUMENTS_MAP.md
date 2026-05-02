# Piper Documents Map

This is the fast navigation guide for humans and coding agents.

`AGENTS.md` remains the top-level doctrine.
Use this file as the shortest path from "what am I trying to do?" to "what should I read next?"

## Start Here

1. [AGENTS.md](../AGENTS.md)
   - authoritative doctrine
   - execution truthfulness, layer boundaries, FILE_WORK rules

2. [docs/architecture/TRIGGER_FLOW.md](architecture/TRIGGER_FLOW.md)
   - where runtime logic belongs
   - read this before adding behavior to orchestrator, executor, routing, or persona flow

3. [docs/architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md)
   - current codebase structure
   - useful when you need to find the owning module quickly

4. [docs/architecture/CAPABILITIES.md](architecture/CAPABILITIES.md)
   - current user-facing behavior
   - best for "can Piper do this already?"

## If You Need To...

- Understand architecture doctrine:
  [AGENTS.md](../AGENTS.md)

- Place a change in the right runtime layer:
  [docs/architecture/TRIGGER_FLOW.md](architecture/TRIGGER_FLOW.md)

- Find the file/module that owns a behavior:
  [docs/architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md)

- Check what Piper currently promises users:
  [docs/architecture/CAPABILITIES.md](architecture/CAPABILITIES.md)

- See planned work and intentional future changes:
  [docs/ROADMAP.md](ROADMAP.md)

- Check what is actively being worked on right now:
  [docs/WIP.md](WIP.md)
  This is the active branch-status surface; prefer it over interpreting `ROADMAP.md` as live progress.

- Understand voice identity without reading overlapping roadmap sections:
  [docs/specs/voice-identity.md](specs/voice-identity.md)

- Understand computer use without reading a giant roadmap block:
  [docs/specs/computer-use.md](specs/computer-use.md)

- Understand LangGraph/default-runtime stabilization without starting from the old migration spec:
  [docs/specs/langgraph-stabilization.md](specs/langgraph-stabilization.md)

- Understand the autonomous script-generation idea without reading a roadmap blob:
  [docs/specs/autonomous-scripting.md](specs/autonomous-scripting.md)

- Understand MCP client support planning:
  [docs/specs/mcp-client.md](specs/mcp-client.md)

- Understand workspace structural indexing planning:
  [docs/specs/workspace-code-indexing.md](specs/workspace-code-indexing.md)

- Understand the task eval harness plan:
  [docs/specs/task-eval-harness.md](specs/task-eval-harness.md)

- Understand the async task queue plan:
  [docs/specs/async-task-queue.md](specs/async-task-queue.md)

- Understand full-text search planning:
  [docs/specs/full-text-search.md](specs/full-text-search.md)

- Understand engine directory cleanup planning:
  [docs/specs/engine-directory-audit.md](specs/engine-directory-audit.md)

- Understand the tiered smoke-suite plan:
  [docs/specs/tiered-smoke-suite.md](specs/tiered-smoke-suite.md)

- Understand notebook-aware file-tool planning:
  [docs/specs/notebook-aware-file-tools.md](specs/notebook-aware-file-tools.md)

- Understand development workflow:
  [docs/DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)

- Debug a broken behavior:
  [notes/debug-protocol.md](../notes/debug-protocol.md)
  [docs/foundation/checklists/TRIAGE_MAP.md](foundation/checklists/TRIAGE_MAP.md)

- Check current known-good behavior:
  [notes/known-good.md](../notes/known-good.md)

- Check active problems or regressions:
  [notes/known-issues.md](../notes/known-issues.md)

- Read short implementation history from real sessions:
  [notes/coder-log.md](../notes/coder-log.md)

- Review the verification/file-work foundation:
  [docs/foundation/VERIFICATION_ENGINE.md](foundation/VERIFICATION_ENGINE.md)
  [docs/foundation/FILEWORK_ENGINE.md](foundation/FILEWORK_ENGINE.md)

- Review older foundation/spec material for context only:
  [docs/foundation/BLUEPRINT.md](foundation/BLUEPRINT.md)
  [docs/foundation/EXECUTION_ROADMAP.md](foundation/EXECUTION_ROADMAP.md)
  [docs/foundation/SUMMARY_ENGINE.md](foundation/SUMMARY_ENGINE.md)
  [docs/foundation/GLM_ADVICE.md](foundation/GLM_ADVICE.md)

## Status Guide

- Doctrine:
  active rules that must be followed
  source: [AGENTS.md](../AGENTS.md)

- Architecture:
  live runtime spec and current structure
  source: [docs/architecture/](architecture/)

- Roadmap:
  future intended work
  source: [docs/ROADMAP.md](ROADMAP.md)

- WIP:
  active implementation work that is not shipped truth yet
  source: [docs/WIP.md](WIP.md)

- Notes:
  operational memory, debugging lessons, validated behavior
  source: [notes/](../notes/)

- Archive:
  superseded material kept for reference only
  source: [docs/archive/](archive/)

## Red Flags

- Do not treat `notes/` as doctrine.
- Do not treat `docs/archive/` as current direction.
- Do not add new behavior before checking [docs/architecture/TRIGGER_FLOW.md](architecture/TRIGGER_FLOW.md).
- If two docs disagree, prefer `AGENTS.md`, then `docs/architecture/TRIGGER_FLOW.md`, then current code.
