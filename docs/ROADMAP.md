# Piper — Roadmap

Status: Active · Prescriptive
This document owns all planned future work. Before Kimi Code touches any item, its spec must be written by Kimi Web (the web-based Kimi agent) here first (document-first doctrine).

This file is for planned work, priorities, and proposal-level specs.
If work is actively underway, track that status in `docs/WIP.md`.
If work is shipped, move the durable runtime truth to `docs/architecture/TRIGGER_FLOW.md`.

Build order reflects current priority. Re-order deliberately, not casually.
For high-risk interaction features, prefer the slice with the strongest verification surface first. In practice that means browser-first computer use before desktop automation, and pattern memory still stays last.

---

## Queued — ready to build (specs complete)

### R-4 User Pattern Memory

**Priority:** Low — build last. Delay until verification is trustworthy; pattern hints on fake-success turns would be noise.

**Problem:**

Piper stores explicit facts the user states but has no model of recurring behavioural patterns. Pattern recognition currently lives entirely with the user.

**Design:**

After each successfully completed TASK turn (`VERIFIED` outcome), a `PatternObserver` records a structural fingerprint to `data/pattern_log.json`. The fingerprint is derived from route kind + sorted `file_stage_kind` set + sorted tool set (e.g. `TASK:INSPECTION+CONTENT_EDIT:list_tree+write_text`) — stable across wording variation. If the same fingerprint appears 3+ times, it is promoted to `data/pattern_hints.json`.

Pattern hints are injected as a `[PATTERN HINTS]` block — soft suggestions, not directives. Surfaced at most once per session, only when plausible (same day of week, matching trigger words, or explicit user ask). One sentence, easy to ignore. Piper does not repeat if ignored.

**Files:** `core/engines/pattern_observer.py` (new); `orchestrator_phases.py` → `phase_persona()` (record on VERIFIED task); `core/engines/context_pack.py` (inject `[PATTERN HINTS]`); `data/pattern_log.json`; `data/pattern_hints.json`

---

## Backlog — needs spec before build

*These are approved ideas. Write the spec here before handing to Kimi Code.*
*Ordered by added value, highest first.*

---

**Release gate script**

Add a local release gate command that summarizes whether a branch is reviewable or blocked.

Candidate file: `scripts/release_gate.py`

Checks:
- current branch and whether high-risk WIP is on `main`
- dirty working tree status
- staged/private/runtime files
- required smoke/evidence commands for touched domains
- docs changed when behavior changed
- summary verdict: `SHIP`, `NEEDS EVIDENCE`, or `BLOCKED`

Why: prevents broad, weakly verified work from landing quietly.

---

**Repo hygiene checker**

Add a focused checker for accidental runtime/private/local files before commit.

Candidate file: `scripts/check_repo_hygiene.py`

Should flag:
- `data/debug/`
- runtime JSON/state files
- voice embeddings
- `.claude` or other agent-local state
- local scratch scripts
- model/cache artifacts
- unexpectedly large files

Why: keeps the repo clean as Piper grows more stateful.

---

**Evidence ledger convention**

Add `notes/evidence/` as the place to record what risky branches actually proved.

Example:
- `notes/evidence/voice-identity.md`

Each ledger should include:
- branch
- goal
- test command
- result
- manual test performed
- observed behavior
- unresolved risk
- reviewer verdict

Known issues say what is broken. Evidence ledgers say what has been proven.

---

**Config reference**

Create `docs/CONFIG_REFERENCE.md` to document environment flags, defaults, and risk notes.

Suggested groups:
- identity / privacy
- LangGraph
- executor limits
- voice / STT / TTS
- debug flags
- search / browser / computer-use
- memory / retrieval

Why: prevents bugs caused by forgotten flags, stale defaults, or mystery switches.

---

**Startup self-check**

Add a startup health check so Piper reports environment problems at boot.

Checks:
- Windows runtime `.venv` sanity
- required directories
- model paths
- llama server config
- voice dependencies available or cleanly disabled
- LangGraph checkpoint DB accessible
- unsafe runtime leftovers detected

Why: catches broken environment state before the user hits weird runtime behavior.

---

**Voice Recognition — Passive User Identification**

Active implementation follow-up is tracked in `docs/WIP.md`.
Primary spec: `docs/specs/voice-identity.md`

---

**Computer use v0 (browser-first)**

Active implementation follow-up is tracked in `docs/WIP.md`.
Primary spec: `docs/specs/computer-use.md`

---

**Autonomous Tool Creation — Piper writes scripts for herself**
Primary spec: `docs/specs/autonomous-scripting.md`

---

**Workspace Code Indexing — structural understanding of files**
Primary spec: `docs/specs/workspace-code-indexing.md`

---

**Morning Brief — proactive daily summary**

Piper gives the user a daily summary when they first open her. No prompting needed. Uses existing proactive monitor infrastructure to trigger on session start.

Content:
- Tasks due today / overdue
- Upcoming events from reminders
- Reminders that fired overnight
- New files in workspace since last session
- Quick suggestion based on priority

Example: "You have 3 overdue tasks and a meeting in 2 hours. Want me to prioritize?"

Why: Shifts Piper from reactive tool to proactive assistant.

Effort: ~1 day. Uses existing task/event/reminder + file infrastructure.

Files (tentative): `core/engines/morning_brief.py`; `core/orchestrator_phases.py` hook on session start; `core/engines/context_pack.py` brief block injection

---

**Workspace Tidy Suggestions — proactive housekeeping**

Piper periodically scans the workspace and offers to help with digital clutter.

Patterns:
- Downloads folder has N unorganized files → "Sort by type?"
- Files with 'backup' in name older than 30 days → "Delete old backups?"
- Duplicate filenames in multiple folders → "Consolidate?"
- Empty directories or temp files left behind → "Clean up?"

Piper asks permission before acting. All operations are undoable via change journal.

Why: Housekeeping users never get around to. Piper notices what you'd miss.

Effort: ~1–2 days. Uses existing file tools + undo system.

Files (tentative): `core/engines/workspace_tidy.py` (scanner + suggestion builder); `core/engines/proactive_monitor.py` integration for periodic scans

---

**Natural Voice Mode — hands-free improvement**

Improve the hands-free voice experience beyond current TTS/STT.

Features:
- Wake word ("Hey Piper") instead of button press
- Piper can interrupt herself if user speaks over her (barge-in)
- Better noise filtering / understanding in non-ideal environments
- Voice-only mode for simple tasks (no screen interaction needed)

Why: True hands-free operation while cooking, driving, walking.

Effort: ~2–3 days. Uses existing TTS/STT pipeline + event loop changes.

Files (tentative): `tools/wake_word.py`; `core/pipeline.py` barge-in support; `tools/stt.py` noise profile improvement

---

**User Identity & World Model Restoration**

Active identity follow-up is tracked in `docs/WIP.md`.
Primary spec: `docs/specs/voice-identity.md`

---

**Bulk mutation rollback manifests** — *Implemented as §13.18. See `docs/architecture/TRIGGER_FLOW.md §13.18` and `core/engines/rollback_engine.py`.*

---

**Engine directory audit and lifecycle cleanup**
Primary spec: `docs/specs/engine-directory-audit.md`

---

**Tiered smoke-suite audit (after computer use v0 stabilizes)**
Primary spec: `docs/specs/tiered-smoke-suite.md`

---

**Desktop computer use expansion (phase 2)**
Follow-on to `docs/specs/computer-use.md`

---

**Voice-based user identification + owner-private speaker separation**

Foundation is largely shipped; any remaining active follow-up belongs in `docs/WIP.md`.
Primary spec: `docs/specs/voice-identity.md`

---

**MCP client support**
Primary spec: `docs/specs/mcp-client.md`

---

**Task eval harness (`inspect-ai`-style)**
Primary spec: `docs/specs/task-eval-harness.md`

---

**Full-text search engine**
Primary spec: `docs/specs/full-text-search.md`

---

**Async task queue for background work**
Primary spec: `docs/specs/async-task-queue.md`

---

**Notebook-aware file tools**
Primary spec: `docs/specs/notebook-aware-file-tools.md`

---

**Active image selection (cortex tab)**

The cortex/vision tab already auto-updates the active image in certain scenarios. Add a manual "choose file" control in the cortex tab so the user can explicitly designate any image as the current visual reference, overriding the auto-selected one. The chosen file becomes the active cortex image for the session until changed or cleared.

---

**Avatar**

UI persona visualisation placed at the bottom right of the status tab. The status pane is currently too long — split it so the lower portion hosts the avatar. Pure polish, no capability impact. Implement after the status tab split is done as a UI layout task.

---
