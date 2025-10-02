# scripts/core/transition_plan.py
# Ring‑0 Core: canonical transition plan (documentation only; no behavior).
# This file is the single source of truth for intended FSM transitions.
#
# Event payload contracts (from scripts/common/types.py):
#   WakeDetected → WakePayload
#   ASRResult    → ASRResultPayload
#   Speak        → SpeakPayload
#   StopSpeak    → None
#   Sleep        → None
#
# Planned transitions (phase-by-phase):
#
# T‑Core02 (flagged, implemented in router.py):
#   SLEEPING + WakeDetected  -> WAKING
#
# T‑Core03 (names only; to be implemented later):
#   WAKING   + ASRResult     -> LISTENING     # ASR ready; begin capturing commands
#   LISTENING+ ASRResult     -> THINKING      # final utterance received
#   THINKING + Speak         -> SPEAKING      # Core requests TTS (via Services)
#   SPEAKING + StopSpeak     -> LISTENING     # barge‑in
#   SPEAKING + Sleep         -> SLEEPING      # idle timeout or explicit sleep
#   * Any     + Sleep        -> SLEEPING      # universal transition (guarded)
#
# Notes:
# - Side effects (ASR/TTS start/stop) are NOT performed by Core; Core only emits events
#   and updates state. Services perform device actions when invoked by Core.
# - UI publishes events to Core; it never calls Services directly.
# - Services implement Protocols in scripts/services/base.py and never import UI.
