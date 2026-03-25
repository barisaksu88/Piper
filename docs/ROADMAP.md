# Piper — Roadmap

Status: Active · Prescriptive
This document owns all planned future work. Before Codex touches any item, its spec must be written here first (document-first doctrine). Implemented items are retired to `docs/architecture/TRIGGER_FLOW.md §13`.

Build order reflects current priority. Re-order deliberately, not casually.

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

*These are approved ideas. Write the spec here before handing to Codex.*
*Ordered by added value, highest first.*

---

**Computer use (environment interaction)**

Piper can already see. This adds the action side: click, drag, type, scroll, navigate — on both browser and desktop.

**Architecture:** One `ComputerUseEngine` with two backends unified behind a single action interface. The orchestrator hands it a task and waits for a result, same pattern as all other engines.

- **Browser backend** — Playwright. DOM-aware, reliable element targeting, handles navigation and form interaction cleanly.
- **Desktop backend** — pyautogui. Coordinate-based, relies on vision to locate targets. Needs tighter verification between steps.

The engine detects context (is the target a browser window or a native app?) and routes to the appropriate backend automatically.

**Perception loop:** Each action is followed by a screenshot fed through the existing Qwen vision pipeline to verify the result before the next action. Same vision model, same pipeline — already the perception primitive in cortex. No changes needed on the vision side.

**Guards (first line of safety, no confirmation dialogs to start):**
- Per-task scope: the planner specifies which app, window, or domain is in-scope. Actions outside that scope are blocked at the engine level, not asked about.
- Failure behaviour: if the engine cannot verify expected state after an action (wrong page, element not found, unexpected dialog), it stops and reports to the user rather than continuing blind.
- A master enable/disable toggle in settings for when computer use should be off entirely.

**Trigger:** Natural language command. Ambiguous intent is resolved the same way any other route is — via the confidence-aware router asking before routing, not mid-task. Once the task is routed to `COMPUTER_USE` it executes without interruption unless a guard fires.

**New route kind:** `COMPUTER_USE` — added to the router cascade above the terminal lock.

**Files (when specced):** `core/engines/computer_use_engine.py` (new — action loop, both backends, scope guard enforcement); `core/contracts.py` (COMPUTER_USE route kind); `core/route_normalizer.py` (route to COMPUTER_USE); `core/orchestrator_phases.py` (hand off to engine); `ui/settings.py` (master toggle)

---

**Voice-based user identification + multi-user separation** *(merged)*

Speaker embedding runs as a separate inference process alongside Whisper (e.g. `pyannote.audio`). On each turn, the detected voice is matched against registered profiles in `data/users.json`.

Anyone present is part of the owner's world. Piper always tries to learn about whoever is speaking — building or updating their world state entry, connecting dots from context even if the person has never been explicitly mentioned. Not recognising a voice is not a reason to ignore a person; it is a reason to start learning about them.

The only distinction is what Piper *surfaces back*:

| Voice match | What Piper does |
|---|---|
| Primary user (owner) | Full access — all memory, all context blocks, all routes |
| Anyone else (known or new) | Actively learns: creates or updates their world state entry, connects available context to fill in the picture. No access to owner's private memory. |

If a voice is unrecognised and the person hasn't been mentioned before, Piper treats them as a new person to learn about — not a guest to wall off. Over time, repeated presence builds a richer entry. Voice ID is one parameter in their profile (`voice_embedding_path`); identity can also be established by name or introduction.

Each entry in `users.json` includes: `user_id`, `name`, `voice_embedding_path` (optional until registered), and a path to their memory silo.

Foundation: `knowledge_enabled` gating from R-2. The `user_id` filter is the natural extension of that mechanism.

**Files (when specced):** `core/engines/voice_id.py` (new — embedding inference + match); `data/users.json`; `core/orchestrator.py` (set access tier pre-phase); `core/engines/context_pack.py` (filter memory blocks by `user_id`)

---

**File operation rollback (undo)**

Bulk mutations — consolidation, batch moves, renames — currently have no inverse. If the user asks Piper to undo such an operation there is nothing to work from; the planner has no record of what moved where, and loops trying to invent one.

**Design:**

Before any `consolidate_by_extension`, `move_many`, `move_path`, `copy_many`, or `delete_many` executes, the executor writes a rollback manifest to `data/rollback/rollback_<timestamp>.json`:

```json
{
  "timestamp": "…",
  "action": "consolidate_by_extension",
  "moves": [{"from": "root/photo.png", "to": "images/photo.png"}, …],
  "deletions": []
}
```

The manifest is written *before* the action so a crash mid-run leaves a usable record. On success it is marked `committed: true`. At most the last N manifests are kept (configurable, default 5).

When the user says "undo" or "reverse that", the router resolves to a `ROLLBACK` task kind. The executor reads the most recent committed manifest and replays each move/deletion in reverse — moves are inverted, deleted files cannot be recovered (logged as unrecoverable). The manifest is marked `rolled_back: true` on completion.

Limitations accepted at first build: only the most recent operation is undoable; deletions are unrecoverable; no UI to browse history.

**Files (when specced):** `core/executor.py` (write manifest pre-mutation, mark committed post-success); `core/engines/rollback_engine.py` (new — read manifest, invert moves); `core/contracts.py` (ROLLBACK route kind); `core/route_normalizer.py` (route "undo/reverse/rollback" to ROLLBACK); `data/rollback/` (manifest store)

---

**Active image selection (cortex tab)**

The cortex/vision tab already auto-updates the active image in certain scenarios. Add a manual "choose file" control in the cortex tab so the user can explicitly designate any image as the current visual reference, overriding the auto-selected one. The chosen file becomes the active cortex image for the session until changed or cleared.

---

**Avatar**

UI persona visualisation placed at the bottom right of the status tab. The status pane is currently too long — split it so the lower portion hosts the avatar. Pure polish, no capability impact. Implement after the status tab split is done as a UI layout task.

---
