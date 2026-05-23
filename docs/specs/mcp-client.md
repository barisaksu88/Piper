# MCP Client Support

Status: Proposal spec

This document is the focused planning surface for MCP client support.

## Purpose

Piper should be able to use external Model Context Protocol servers without turning every new integration into a bespoke first-party tool implementation.

The goal is a thin compatibility layer that lets external tool descriptors enter Piper's existing planning and execution model safely.

## Design Goals

- normalize remote tools into Piper's existing tool/domain contracts
- preserve Router-selected domains and Prompt Builder allowed-tool resolution
- keep results schema-bound and checker-friendly
- extend Piper without bypassing first-party safety rails

## Non-Goals

- allowing arbitrary remote tool execution outside Piper's safety model
- replacing local-first tools as the default path
- introducing freeform or narration-only tool contracts

## Architecture Guardrails

- MCP tools still enter through Router-selected stage domains
- tool metadata must normalize into the same contract family Piper already expects from `tools/registry.py`
- remote results must be machine-readable enough for verification/checker flows
- external capability should be an extension layer, not a side door around doctrine

## Value

- plugin-style extensibility
- easier access to local MCP servers and specialized tools
- less bespoke integration code for each new external capability

## Likely File Surfaces

- `tools/mcp_client.py`
- `tools/registry.py`
- `core/planner_boundary.py`
- `core/prompt_builder.py`
- `core/executor.py`

## Open Questions

- how much metadata adaptation happens at registration time vs runtime
- how remote tool errors should be normalized into Piper result contracts
- how checker compatibility should be enforced for non-file, non-browser remote tools

## Doc Placement Rules

- `AGENTS.md` remains the doctrine authority.
- `docs/ROADMAP.md` should point here rather than carrying the whole concept inline.
- if MCP work becomes active, `docs/WIP.md` should carry live branch status.
