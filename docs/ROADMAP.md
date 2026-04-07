# Piper — Roadmap

Status: Active · Prescriptive
This document owns all planned future work. Before Codex touches any item, its spec must be written here first (document-first doctrine). Implemented items are retired to `docs/architecture/TRIGGER_FLOW.md §13`.

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

*These are approved ideas. Write the spec here before handing to Codex.*
*Ordered by added value, highest first.*

---

**Computer use v0 (browser-first)**

Piper can already see. This adds the action side for browser work first: click, type, scroll, navigate, extract, submit, and download in a controlled browser session.

**Why browser first:**

Browser work has the strongest verification surface Piper can get from an interactive environment: DOM presence, URL, title, field values, navigation completion, and download events. That makes it a good first slice for computer use under Piper's execution-truthfulness doctrine. Desktop automation remains a later expansion because it depends much more heavily on uncertain vision and coordinate targeting.

**Architecture:**

This is **not** a new top-level route decision. Piper's top-level turn flow remains `CHAT` / `SEARCH` / `TASK`.

Computer use enters through the normal `TASK -> MANAGER` path:
- router/normalizer produce a `TASK` card
- the stage domain is `COMPUTER_USE`
- the planner receives only computer-use tools
- executor dispatches those tool calls into a new `ComputerUseEngine`

That keeps the feature aligned with the current trigger flow instead of inventing a parallel route system.

**Owned browser session only:**

v0 should drive a dedicated Piper-owned Playwright browser context/profile. It does **not** attach to an arbitrary user browser window and does **not** auto-detect browser vs desktop targets.

Benefits:
- deterministic session ownership
- clearer scope control
- easier harnessing
- fewer surprising side effects in the user's personal browser

Desktop automation is a separate follow-on item, not part of the first build.

**Route and planning contract:**

- No new `RouteDecision.decision` value.
- Add `COMPUTER_USE` as a new `StageCard.stage_type` / tool domain.
- Secretary prompt and route normalizer learn to map clear website/browser-action requests into `TASK` cards containing `COMPUTER_USE` stages.
- Ambiguous requests must clarify **before** entering execution. If the target site, account context, or desired end state is unclear, the route should pause in a `CHAT` clarification stage first.

**Stage metadata:**

Add a typed contract in `core/contracts.py` for computer-use stages, e.g. `ComputerUseRequest`, carried on the stage card.

Minimum v0 fields:
- `backend`: fixed to `browser`
- `start_url`: optional seed URL
- `allowed_domains`: host allowlist for the stage
- `goal_kind`: one of `navigate`, `extract`, `form_fill`, `download`
- `download_dir`: optional workspace-relative destination for approved downloads

This metadata is authoritative runtime scope, not a persona hint.

**Tooling model:**

Prefer one structured tool for v0, e.g. `BROWSER_OP`, backed by `ComputerUseEngine`, rather than many ad hoc browser tools.

Representative actions:
- `open_page`
- `goto_url`
- `click`
- `type_text`
- `press_key`
- `select_option`
- `scroll`
- `wait_for`
- `extract_text`
- `download`
- `capture_state`

Tool arguments must be structured JSON, following the same doctrine as `FILE_OP`: deterministic actions, explicit selectors/targets, and machine-readable results.

**Selector strategy:**

Prefer deterministic selectors in this order:
1. role/name or label-based locator
2. stable test-id / id / name
3. visible text
4. CSS/XPath only as fallback

The engine should return which selector strategy actually matched so failures are diagnosable.

**Verification model:**

Computer use must not rely on LLM narration as proof.

Primary proof is deterministic browser evidence:
- current URL / origin
- page title
- DOM presence / absence
- element visibility / enabled state
- input value after type/fill
- extracted text payload
- download event + saved path

Vision is secondary:
- use existing screenshot + `tools/vision.py` flow only when DOM evidence is missing or ambiguous
- do not make screenshots the primary verification path for browser work

Each action result should include a structured verification block so executor can reason from evidence instead of free text.

**Failure behavior:**

Stop immediately on:
- scope drift to a domain outside `allowed_domains`
- element not found after bounded retries
- unexpected modal / popup / permission prompt
- navigation to login / MFA / CAPTCHA / payment flow the engine is not allowed to complete
- verification mismatch after an action

The engine reports the stop honestly and returns control to persona. It does not continue blind and does not improvise new scope.

**Safety and non-goals for v0:**

Allowed:
- information retrieval from websites
- form filling where the final submit action is within declared scope
- downloading artifacts into a workspace-controlled folder

Out of scope for v0:
- desktop/native app control
- purchases / checkout / payment submission
- password-manager interaction
- CAPTCHA solving
- MFA/2FA completion
- arbitrary browser-profile hijacking
- silent background automation across unrelated sites/tabs

If a task hits one of those boundaries, Piper pauses and explains why.

**User-visible behavior:**

- `COMPUTER_USE` respects the existing Route -> Plan -> Act -> Speak shape.
- Persona reports what was verified, not what was attempted.
- When extraction succeeds, the answer should foreground the extracted result, not the click-by-click transcript.
- When a task pauses, persona should explain the concrete blocker and the exact next thing the user would need to do.

**Harness-first rollout:**

Do not ship browser computer use without deterministic regression coverage.

Minimum harness surface:
- navigation + URL verification
- click + DOM-state verification
- type/fill + field-value verification
- extraction from a known test page
- out-of-scope domain block
- unexpected dialog / login wall stop
- download verification into a test workspace directory
- follow-up correction after a paused browser task

Use local static fixture pages or a tightly controlled local test app first. Avoid making real external websites the primary regression surface.

**Implementation order:**

1. Add contracts + stage domain + tool registry entries.
2. Add route normalization / secretary prompt support for explicit browser-use requests.
3. Implement `ComputerUseEngine` with Playwright and structured `BROWSER_OP` results.
4. Add executor handling + verification plumbing for `COMPUTER_USE`.
5. Add harnesses and fixture pages.
6. Add the UI settings master toggle.
7. Only after all of the above are stable, consider desktop expansion.

**Files (when specced):**

- `core/contracts.py` (`ComputerUseRequest`, `COMPUTER_USE` stage domain, typed tool result shape)
- `tools/registry.py` (`BROWSER_OP` tool spec and domain guidance)
- `core/routing/route_normalizer.py` (explicit browser-use normalization / clarification rules)
- `data/prompts/secretary.txt` (browser-use routing guidance)
- `core/planner_boundary.py` (allowed-tool resolution for `COMPUTER_USE`)
- `core/prompt_builder.py` (stage guide rendering for computer-use stages)
- `core/executor.py` (`COMPUTER_USE` execution + verification path)
- `core/engines/computer_use_engine.py` (new — Playwright action loop, scope guards, structured verification)
- `tools/vision.py` / `tools/live_screen.py` / `tools/screen_capture.py` (reuse as secondary perception path only where needed)
- `ui/settings.py` (master enable/disable toggle)
- `scripts/` browser computer-use harnesses + local fixture pages

---

**Bulk mutation rollback manifests** *(extends existing undo; does not replace it)*

The existing undo system in `docs/architecture/TRIGGER_FLOW.md §13.9` already handles supported single-operation `FILE_OP` mutations by snapshotting pre-mutation state into the change journal.

The remaining gap is bulk mutation tools whose effective scope is too wide to reconstruct after the fact. Operations like `consolidate_by_extension`, `move_many`, `copy_many`, and `delete_many` can touch many files at once; when the user says "undo that", the current journal does not retain a full reversible recipe for the whole batch, so the undo path falls back to planner guesswork and can loop.

**Design:**

Before any supported bulk mutation tool (`consolidate_by_extension`, `move_many`, `copy_many`, `delete_many`) executes, the executor writes a rollback manifest to `data/rollback/rollback_<timestamp>.json`:

```json
{
  "timestamp": "…",
  "action": "consolidate_by_extension",
  "moves": [{"from": "root/photo.png", "to": "images/photo.png"}, …],
  "deletions": []
}
```

The manifest is written *before* the action so a crash mid-run leaves a usable record. On success it is marked `committed: true`. At most the last N manifests are kept (configurable, default 5).

The manifest is linked from the normal change-journal entry for that task, rather than replacing the journal. When the user says "undo" or "reverse that", the existing `UNDO` interceptor / `phase_undo()` flow stays in charge. If the latest journal entry points at a committed bulk manifest, the undo path reads that manifest and replays each move/deletion in reverse. The manifest is marked `rolled_back: true` on completion.

This is complementary to the shipped single-op undo:
- single-path `write` / `edit` / `delete` / `move` / `rename` / `copy` stay owned by the existing change journal
- bulk file transforms gain a manifest-backed reversible recipe so "undo that" remains mechanical instead of inferential

Limitations accepted at first build: only the most recent bulk operation is undoable; deletions are unrecoverable; no UI to browse history.

**Files (when specced):** `core/executor.py` (write manifest pre-mutation, mark committed post-success, link it to the journal entry); `core/engines/change_journal.py` (store manifest reference on the task entry); `core/engines/rollback_engine.py` (new — read manifest, invert the batch mechanically); `core/orchestrator_phases.py` (`phase_undo()` uses the manifest path when present); `data/rollback/` (manifest store)

---

**Desktop computer use expansion (phase 2)**

After browser-only computer use is stable and boring, extend `ComputerUseEngine` with a desktop backend for native apps.

**Architecture:** Add a second backend behind the same engine interface, but keep desktop activation explicit. Do not rely on implicit browser-vs-desktop detection at first. The task must say or strongly imply native app / desktop interaction.

- **Desktop backend** — pyautogui or equivalent coordinate/input driver.
- **Perception** — vision-backed target finding plus screenshot verification after every action.
- **Safety bar** — higher than browser work. Desktop automation should stop on any uncertain target match, window mismatch, or unexpected dialog.

**Files (when specced):** `core/engines/computer_use_engine.py` (desktop backend integration); desktop action driver module(s) (new); `core/orchestrator_phases.py`; `ui/settings.py`; dedicated desktop computer-use harnesses

---

**Voice-based user identification + owner-private speaker separation** *(merged foundation; voice unlock follow-up remains)*

Speaker embedding runs as a separate inference process alongside Whisper (e.g. `pyannote.audio`). On each turn, the detected voice is matched against registered profiles in `data/users.json`.

The current merged foundation now assumes a local owner-first model:
- `admin_baris` is the only protected profile.
- everyone else is a public identified speaker, not a separate privacy boundary.
- after restart, Piper starts in `unknown` speaker mode until identity is established again.

Anyone present is part of the owner's world. Piper always tries to learn about whoever is speaking — building or updating their world state entry, and mirroring stable person facts into Baris's world model even if the person has never been explicitly mentioned before. Not recognising a voice is not a reason to ignore a person; it is a reason to start learning about them.

The only distinction is what Piper *surfaces back*:

| Voice match | What Piper does |
|---|---|
| Verified owner (`admin_baris`) | Unlock full private memory, owner configuration, and private context blocks |
| Public speaker (known or new) | Use that speaker's own silo for their active session/style, mirror durable person facts into Baris's world model, and do not reveal owner-private memory |
| Unknown speaker | Stay public, assume no identity yet, and ask one short natural question to learn who is speaking |

If a voice is unrecognised and the person hasn't been mentioned before, Piper treats them as a new person to learn about — not a guest to wall off. Over time, repeated presence builds a richer entry. Voice ID is one parameter in their profile (`voice_embedding_path`); identity can also be established by name or introduction.

Each entry in `users.json` includes: `user_id`, `name`, `voice_embedding_path` (optional until registered), a path to their memory silo, and their persisted style card. Typed owner activation is password-gated when voice verification is not present.

Foundation: `knowledge_enabled` gating from R-2. The `user_id` filter is the natural extension of that mechanism.

**Files (when specced):** `core/engines/voice_id.py` (new — embedding inference + owner match); `data/users.json`; `core/orchestrator.py` (set access tier pre-phase); `core/engines/context_pack.py` (filter memory blocks by `user_id`)

---

**Active image selection (cortex tab)**

The cortex/vision tab already auto-updates the active image in certain scenarios. Add a manual "choose file" control in the cortex tab so the user can explicitly designate any image as the current visual reference, overriding the auto-selected one. The chosen file becomes the active cortex image for the session until changed or cleared.

---

**Avatar**

UI persona visualisation placed at the bottom right of the status tab. The status pane is currently too long — split it so the lower portion hosts the avatar. Pure polish, no capability impact. Implement after the status tab split is done as a UI layout task.

---
