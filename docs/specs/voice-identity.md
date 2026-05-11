# Voice Identity

Status: Active spec and consolidation point

This document is the single planning/reference surface for Piper voice identity.

Use it for:
- shipped voice-identity foundation summary
- active follow-up status
- remaining design work
- terminology and guardrails

Do not use older `ROADMAP.md` voice sections as separate authorities.
Those sections should point here.

## Purpose

Piper should identify who is speaking without relying on Persona narration or unsafe assumptions.

Voice identity exists to support:
- correct active-user selection
- admin/private-memory protection
- natural conversation without repeated "who is speaking?" friction
- safe recovery when recognition is wrong or uncertain

## Current Runtime Truth

The current repo already has significant voice-identity foundation and follow-up behavior in code.

Runtime components:
- [`core/voice_recognition.py`](../../core/voice_recognition.py)
- [`memory/user_runtime.py`](../../memory/user_runtime.py)
- [`tools/stt.py`](../../tools/stt.py)
- [`ui/controller_actions.py`](../../ui/controller_actions.py)
- [`core/orchestrator_phases.py`](../../core/orchestrator_phases.py)
- [`core/prompt_context.py`](../../core/prompt_context.py)

Validation surfaces:
- [`scripts/voice_identity_inference_smoke_test.py`](../../scripts/voice_identity_inference_smoke_test.py)
- [`scripts/voice_identity_drift_smoke_test.py`](../../scripts/voice_identity_drift_smoke_test.py)
- [`scripts/speaker_identity_correction_smoke_test.py`](../../scripts/speaker_identity_correction_smoke_test.py)

## Shipped Foundation

- Per-user runtime isolation is live through `ActiveUserRuntime`.
- Owner/admin protection is live through the `admin_baris` model and password/voice-gated access.
- Voice matching exists as a real runtime path, not just a roadmap idea.
- Persona receives explicit voice-identity context blocks and should not invent identity changes.
- Router-driven typed identity correction can override a mistaken voice guess.
- Voice drift handling exists so one strong sample does not immediately switch a known active user to another known speaker.

## Current Active Behavior

From code and recent notes, the active behavior is roughly:

- Piper starts public/unknown after restart until identity is re-established.
- STT can produce a voice-match decision.
- Voice decisions are score- and margin-gated.
- Admin unlock uses stricter thresholds than public speaker selection.
- A mistaken or low-confidence admin situation revokes private/admin access conservatively.
- Known-speaker drift requires repeated evidence instead of a single sample.
- A different known enrolled speaker must win `VOICE_DRIFT_CONFIRMATION_TURNS` consecutive turns before Piper switches away from the current known speaker.
- Repeated unresolved low-confidence turns are handled separately: Piper only drops from a known speaker to `unknown` after `VOICE_DRIFT_CONFIRMATION_TURNS` consecutive below-threshold turns that do not resolve to any known user.
- Explicit typed/router identity correction can move from a mistaken guess to the correct public profile.

This should be treated as the live design center unless code changes prove otherwise.

## Decision Model

The main decision object is `VoiceMatchDecision` in [`core/voice_recognition.py`](../../core/voice_recognition.py).

Important fields:
- `best_user`
- `best_score`
- `second_score`
- `margin`
- `best_is_admin`
- `threshold`
- `margin_threshold`
- `final_user`
- `decision`
- `reason`

This is the important shift:
- identity guess
- permission unlock

are related but not identical.

A strong candidate is not enough by itself to unlock admin/private context unless the stricter admin gates are satisfied.

## Threshold Model

The current smoke-test expectations show a calibrated threshold model with:
- admin score threshold
- admin margin threshold
- public score threshold
- public margin threshold
- low-confidence boundary
- first-turn inference threshold

These are validated in [`scripts/voice_identity_inference_smoke_test.py`](../../scripts/voice_identity_inference_smoke_test.py).

This threshold model should remain centralized in config/runtime code, not copied into Persona or scattered docs.

## Safety Rules

- Voice identity must not be treated as certain unless the runtime decision accepts it.
- Admin/private memory must be unlocked only from runtime-approved evidence.
- Persona narration is never identity authority.
- Router/typed correction must remain able to fix a mistaken voice guess.
- Unknown is acceptable and often preferable to false certainty.
- Revoking admin/private access on uncertainty is safer than leaking it.
- One strong sample from a different known voice must not immediately evict the current verified speaker.
- One below-threshold turn must not immediately drop a known speaker to `unknown`.

## Remaining Active Work

These are the main incomplete or still-active areas:

### 1. Spec consolidation

This file is the consolidation step.
Future voice-identity planning should be added here instead of re-creating overlapping roadmap sections.

### 2. Shipped-vs-unshipped cleanup

Older roadmap text still mixes:
- already-shipped foundation
- active stabilization
- future ideas

That should continue to be cleaned up so the repo clearly distinguishes:
- true runtime behavior
- active follow-up
- future design

### 3. Enrollment and profile lifecycle clarity

The runtime has passive enrollment/matching concepts, but the docs should stay precise about:
- what is already live
- what is partially live
- what still depends on local setup or future polish

### 4. Evidence and debugging hygiene

Voice identity now has meaningful debug/evidence surfaces.
Those should remain easy to inspect during live testing, especially for:
- threshold calibration
- drift behavior
- admin revocation/recovery
- router correction after mistaken inference

## Future Improvements

These belong to future work unless code proves they are already fully live:

- cleaner voice-profile management UX
- clearer enrollment state visibility
- explicit owner/public diagnostics in the UI
- richer evidence ledgers for real-world voice sessions
- broader manual validation across multiple real speakers/environments

## Deprecated Assumptions

Future agents should avoid these stale assumptions:

- "voice identity is only a roadmap idea"
- "admin voice can switch instantly on one strong sample"
- "typed identity should be handled only in Persona"
- "unknown is a failure state"

Those assumptions are unsafe or outdated relative to the current repo.

## Doc Placement Rules

- `AGENTS.md` defines doctrine and boundaries.
- `docs/WIP.md` tracks current voice-identity follow-up status.
- `docs/architecture/TRIGGER_FLOW.md` should hold shipped runtime truth when a behavior is stable enough to document there.
- `notes/coder-log.md` and `notes/known-good.md` hold implementation evidence and validated operational behavior.
- `docs/ROADMAP.md` should reference this file instead of keeping multiple competing voice-identity mini-specs.
