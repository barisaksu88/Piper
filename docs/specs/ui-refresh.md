# Modern UI Refresh

Status: Future spec / parked UI direction

This document captures the planned Piper UI redesign direction so it does not get lost while higher-risk runtime work continues.

This is not active WIP yet. Track active implementation in `docs/WIP.md` only when work actually starts.

## Purpose

Piper's current DearPyGui interface is functional but visually limited. The long-term goal is a more motivating, polished assistant surface without stealing GPU budget from the local LLM or destabilizing the core Route → Plan → Act → Speak runtime.

The redesign should improve:
- readability of conversation and system state
- visual motivation for continued Piper development
- separation of chat, logs, status, documents, vision/cortex, and controls
- avatar presence without making the UI feel like a toy
- user trust by showing what Piper is doing and what she has access to

## Design Direction

Target style:
- modern desktop assistant interface
- richer chat layout than DearPyGui can comfortably provide
- clear left/right or multi-panel structure
- visually distinct user, Piper, system, and debug/status content
- status surfaces that feel alive, not like repeated `offline` labels
- optional Piper avatar panel that can be hidden or paused

The UI should feel like Piper is a local assistant with real runtime state, not a chatbot window taped to logs.

## Likely Framework Direction

DearPyGui can remain for the current UI until the runtime is boring.

For the modern UI, prefer a framework with stronger layout and styling support, such as:
- PySide6 / Qt
- Qt Quick / QML if richer animation is justified
- another local desktop UI stack only if it keeps Piper Windows-first and local-first

Do not start by trying to force advanced chat bubbles, responsive panels, animated avatar surfaces, or rich styling into DearPyGui if the framework itself is the bottleneck.

## Avatar Direction

The avatar is optional and should not become a runtime dependency.

Desired behavior:
- visible in a dedicated panel, likely bottom-right or a status/assistant panel
- toggleable from the UI
- can be paused automatically under load
- can start as a still portrait or lightweight animated surface
- later can evolve toward a realistic 3D / generated-avatar pipeline

Avatar style direction already preferred:
- adult woman in her 20s
- messy long blonde hair
- teal-green eyes by default
- slim/curvy but not exaggerated
- pretty/cute with a freckled nose
- expressive but not distracting

Mode-aware presentation:
- secretary mode for task/admin organization
- analyst/scientist mode for technical analysis
- other visual modes later if useful

Important: avatar mode is presentation only. It must not change runtime permissions, routing, memory, or safety behavior.

## GPU / Performance Constraints

Piper runs local LLM inference on an RTX 4070 SUPER-class GPU. The UI must not compete heavily with the model.

Rules:
- default avatar implementation should be lightweight
- expensive rendering should pause when LLM/tool work is active
- GPU-heavy avatar/comfy/rendering features must be optional
- UI must remain usable if avatar is disabled
- do not make visual polish block core assistant function

## Scope Boundaries

This spec is about the shell/interface, not Piper's core intelligence.

Keep Piper-native:
- persona
- memory policy
- permissions/access tiers
- voice and TTS/STT pipeline
- high-level routing
- tool safety

The UI may display those states, but must not own them.

## Candidate UI Layout

Possible high-level layout:

- left/center: conversation
- right: active status, identity/access tier, mode, current task, logs summary
- tabbed lower or side surfaces:
  - documents
  - cortex/vision
  - tools/activity
  - settings
- avatar/status panel:
  - compact portrait or animated avatar
  - current mode/context shown subtly
  - no redundant `offline` noise

## Useful First Slice

Do not begin with the full cinematic UI.

First practical slice:
1. create a prototype shell separate from the current runtime UI
2. render static mock data first
3. prove layout, chat styling, avatar placeholder, and status surfaces
4. only then connect to Piper's existing controller/events
5. keep DearPyGui UI available until the new shell is proven

## Non-Goals For First Build

- no full 3D avatar engine in v0
- no ComfyUI runtime dependency in v0
- no rewriting core controller logic just for visuals
- no breaking current UI while experimenting
- no GPU-heavy rendering by default
- no cloud UI services

## Validation

Minimum proof before replacing the current UI:
- Piper boots on Windows
- chat send/receive works
- streaming response renders correctly
- TTS still works
- mic/STT still works or is cleanly disabled
- logs/status remain visible
- active user/access tier display is correct
- long replies scroll correctly
- UI stays responsive during LLM generation
- avatar disabled mode works

## Placement Rules

- Active implementation status belongs in `docs/WIP.md` only after the UI branch starts.
- Durable runtime/UI behavior should eventually be documented in `docs/architecture/CAPABILITIES.md` or a UI architecture doc if needed.
- This spec remains the planning reference until implementation starts.
