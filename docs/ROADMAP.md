# Piper — Roadmap

Status: Active · Prescriptive
This document owns all planned future work. Before Kimi Code touches any item, its spec must be written by Kimi Web (the web-based Kimi agent) here first (document-first doctrine).

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

**Computer use v0 (browser-first)** *(WIP — implementation in progress, not yet complete)*

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

**Autonomous Tool Creation — Piper writes scripts for herself**

Piper should be able to recognize when a task is too large, too slow, or too precise to handle with her existing hand tools (file ops, search, individual edits), and choose to write a small throwaway script to solve it instead — without the user asking or even knowing code is involved.

The script is a means, not an end. The user never sees the code unless they ask. Piper writes it, runs it in a sandbox, validates the output, reports the result conversationally, and can save the script for similar future tasks.

**Examples:**

- "I have a folder with 500 photos. Keep only the ones from 2023, rename them by date."
  - Current: Piper opens each photo one by one, checks manually, renames slowly
  - Target: Piper writes a script that reads EXIF metadata from all 500 photos at once, filters for 2023, renames them in 3 seconds

- "Summarize all my meeting notes from last month"
  - Current: Piper reads each note file sequentially, tries to hold everything in context
  - Target: Piper writes a script that reads all notes, extracts key points, builds a structured summary without missing anything

**Why this is different from current RUN_CODE:**

Current RUN_CODE only fires when the planner explicitly decides to run code as part of a user-requested task. The user must be aware code is running. What's missing is autonomous recognition — Piper deciding on her own that code would help, without user prompting.

**Safety model (v1):**

- Sandbox execution (workspace-jailed, no system access, no network)
- Script output validated before being used or reported
- Piper discloses what she did conversationally ("I wrote a small script to sort those files for you — want to see it?")
- Fallback to manual methods on script failure

**Why deferred:**

- Requires autonomous inefficiency recognition (when is the current approach too slow?)
- Context window pressure — write + debug + validate uses significant context
- Safety story needs careful design before autonomous execution is trusted
- Collect concrete user pain points first (tasks that felt too slow with current Piper)

**Trigger condition for prioritization:** When 3+ concrete user tasks are reported as "took too long" or "Piper should have been able to do this automatically."

**Files (tentative):** `core/autonomous_scripts.py` (script writer + sandbox runner + validator); `core/engines/script_library.py` (saved reusable scripts); executor integration for self-written scripts

---

**Workspace Code Indexing — structural understanding of files**

When Piper searches your workspace, she currently finds files by text similarity ("files that mention password"). She doesn't understand code structure — where a function is defined, what calls it, what arguments it takes.

Indexing adds a structural map of your workspace: every function, class, variable, import, and call relationship. This lets Piper answer precise questions about code without reading every file.

**Examples:**

- "Where is the login function defined and what calls it?"
  - Without indexing: Piper searches for "login", finds 8 files, reads them all, guesses
  - With indexing: Piper answers immediately — `login()` is defined in `auth.py:23`, called by `routes.py:45` and `test_auth.py:12`

- "Rename get_user to fetch_user everywhere"
  - Without indexing: Piper does text search, misses indirect references
  - With indexing: Piper finds every exact call site, every import, every reference

**Why browser-first computer use comes first:**

Indexing is useful only if you keep code in Piper's workspace. Computer use (browser automation) benefits every user regardless of whether they write code.

**Trigger condition for prioritization:** When the user actively writes or maintains code in Piper's workspace and reports that text search isn't precise enough.

**Files (tentative):** `core/indexing.py` (scanner + parser + index builder); workspace file watcher for incremental updates; query interface for Piper's planner

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

**Bulk mutation rollback manifests** — *Implemented as §13.18. See `docs/architecture/TRIGGER_FLOW.md §13.18` and `core/engines/rollback_engine.py`.*

---

**Tiered smoke-suite audit (after computer use v0 stabilizes)**

The current unified smoke runner is useful, but the suite still mixes fast deterministic checks with heavier harness-backed and llama-backed tests. After browser computer use is stable and boring, add a real tier system so default runs stay high-signal without hiding the broader integration surface.

**Deferred on purpose:**
- do **not** build this during active `computer use v0` churn
- first rely on the lightweight runner filter (`--skip-harness`) for a fast default path
- only do the full audit once the volatile harness tier has settled enough that classification will stick

**Planned shape (needs full spec before build):**
- stable default tier for fast, reliable smoke tests
- opt-in extended tier for slower integration coverage
- quarantined tier for known-flaky or currently-red tests
- per-test timeout overrides and short reason strings for quarantined exclusions

---

**Desktop computer use expansion (phase 2)**

After browser-only computer use is stable and boring, extend `ComputerUseEngine` with a desktop backend for native apps.

**Architecture:** Add a second backend behind the same engine interface, but keep desktop activation explicit. Do not rely on implicit browser-vs-desktop detection at first. The task must say or strongly imply native app / desktop interaction.

- **Desktop backend** — pyautogui or equivalent coordinate/input driver.
- **Perception** — vision-backed target finding plus screenshot verification after every action.
- **Safety bar** — higher than browser work. Desktop automation should stop on any uncertain target match, window mismatch, or unexpected dialog.

**Files (when specced):** `core/engines/computer_use_engine.py` (desktop backend integration); desktop action driver module(s) (new); `core/orchestrator_phases.py`; `ui/settings.py`; dedicated desktop computer-use harnesses

---

**Voice-based user identification + owner-private speaker separation** *(foundation shipped — voice embedding follow-up remains)*

Speaker embedding runs as a separate inference process alongside Whisper (e.g. `pyannote.audio`). On each turn, the detected voice is matched against registered profiles in `data/users.json`.

**Foundation shipped:** `memory/user_runtime.py` (ActiveUserRuntime, per-user isolated brain/knowledge/memory/style/conversation summary), `data/users.json` + `data/users/` per-user state directories, `/users` / `/user` / `/adminpass` UI commands, typed identity hint observer, and PBKDF2 admin password gate are all live. The remaining work is the voice embedding inference path itself.

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

**MCP client support**

Piper should be able to call external Model Context Protocol servers without turning every integration into a bespoke in-repo tool. Add a thin MCP client layer that resolves external tool descriptors into Piper's existing `ToolSpec` / domain model so the Executor can use them without caring whether a tool is local or remote.

**Architecture guardrails:**
- MCP tools must still enter through Router-selected stage domains and Prompt Builder allowed-tool resolution.
- External tool metadata should normalize into the same structured contracts Piper already expects from `tools/registry.py`.
- Remote tool results must be schema-bound and checker-friendly; freeform success narration is not enough.
- Keep Piper local-first: MCP extends the tool surface, but does not replace first-party tools or bypass safety rails.

**Value-add:** plugin-style extensibility for local MCP servers, remote sandboxes, and proprietary internal tools without hand-authoring a new Piper integration each time.

**Files (when specced):** `tools/mcp_client.py` (new); `tools/registry.py`; `core/planner_boundary.py`; `core/prompt_builder.py`; `core/executor.py`

---

**Task eval harness (`inspect-ai`-style)**

Piper has smoke tests and harnesses, but it still needs a structured task-evaluation layer that measures whether the system got the *right result* across real end-to-end turns. Add an eval suite that scores route accuracy, stage completion truthfulness, checker alignment, and final answer quality across versions.

**Architecture guardrails:**
- Grade from structured execution evidence (`VERIFIED` / `PARTIAL` / `FAILED`, checker outputs, tool logs), not persona polish.
- Start with deterministic fixtures and golden tasks before widening to heavier live integrations.
- Report regressions by domain (`FILE_WORK`, `SEARCH_WORK`, `COMPUTER_USE`, etc.) so failures stay diagnosable.

**Value-add:** a durable regression signal for execution quality, not just crash resistance, plus a way to track whether Piper is actually improving or quietly degrading.

**Files (when specced):** `tests/eval/` or `AGENTS/harness/eval/`; `scripts/` eval runners; score-reporting docs and fixtures

---

**Full-text search engine**

Chroma-backed semantic recall is useful, but Piper also needs exact keyword retrieval for commands, snippets, filenames, and previously seen lines. Add a local full-text index that complements vector recall instead of replacing it.

**Architecture guardrails:**
- Give the index a single owner module under `memory/` rather than scattering ad hoc search logic across the stack.
- Keep ingestion bounded and write-path-capped in the same spirit as the repo's data-hygiene rules.
- Surface exact-match evidence as a separate retrieval source so the model can distinguish semantic recall from literal hits.

**Value-add:** semantic recall can find "deployment notes"; full-text recall can find the exact `docker run -p 8080:8080` line from weeks ago.

**Files (when specced):** `memory/search_engine.py` (new); `core/search_contracts.py`; `core/engines/context_pack.py`; retrieval plumbing in the search/memory path

---

**Async task queue for background work**

Long-running operations such as large indexing jobs, document ingestion, or extended code execution should not freeze the chat loop. Add an internal task queue so Piper can enqueue background work, surface progress, and let the user keep interacting while the job runs.

**Architecture guardrails:**
- Queueing is a scheduling layer, not a bypass around Router, Executor, or checker rails.
- Background jobs need explicit status/progress events visible to the UI.
- Cancellation, failure, and partial completion must stay first-class states rather than getting narrated away.

**Value-add:** "index my whole Documents folder" or "run this long script" becomes a managed background task instead of a turn-blocking operation.

**Files (when specced):** `core/task_queue.py` (new); `core/orchestrator.py`; `ui/controller_queue.py`; background worker / progress event plumbing

---

**Notebook-aware file tools**

Piper can already inspect and edit plain code files, but data-science workflows often live in `.ipynb` notebooks. Add notebook-aware read/write/execute support so Piper can inspect cells, rerun notebooks for verification, and produce reproducible notebook artifacts.

**Architecture guardrails:**
- Prefer structured notebook actions with explicit outputs and execution evidence rather than treating notebooks as opaque blobs.
- Respect the repo's data-hygiene rules: large binary-rich outputs should not be shoved wholesale into JSON logs.
- Keep non-mutating notebook inspection stages non-mutating at runtime, just like existing `FILE_WORK` doctrine.

**Value-add:** Piper becomes much more useful for research, experimentation, and reproducible analysis workflows that currently sit outside normal file tooling.

**Files (when specced):** extend `FILE_OP` with notebook actions or add `NOTEBOOK_WORK`; `tools/registry.py`; `tools/workspace_runtime.py` or notebook-specific runtime module(s); `core/file_stage_policy.py`

---

**Active image selection (cortex tab)**

The cortex/vision tab already auto-updates the active image in certain scenarios. Add a manual "choose file" control in the cortex tab so the user can explicitly designate any image as the current visual reference, overriding the auto-selected one. The chosen file becomes the active cortex image for the session until changed or cleared.

---

**Avatar**

UI persona visualisation placed at the bottom right of the status tab. The status pane is currently too long — split it so the lower portion hosts the avatar. Pure polish, no capability impact. Implement after the status tab split is done as a UI layout task.

---
