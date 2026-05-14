# Piper Web UI Migration Guide

> **Version:** 1.2 (post-Phase 12.8)  
> **Branch:** `feature/web-ui-bridge`  
> **Base:** `fix/guest-voice-name-disambiguation` (commit `1414316`)  
> **Status:** Phase 12 complete. Manual checkpoint 2 passed.

---

## 1. Purpose

This guide is the canonical reference for Piper's Web UI migration. It reconciles:
- the original v2.0/v2.1 architecture plan
- the actual modified phase history used during implementation
- the remaining parity roadmap

### Key definitions
- **Web UI** means React/HTML/CSS rendering technology. The browser tab used during development is for debugging only.
- **Production target** remains a dedicated Piper desktop window (Tauri or pywebview wrapper — deferred until after parity).
- **DearPyGui** remains the default UI path. Web UI is strictly opt-in via `PIPER_WEB_UI_ENABLED`.
- **Parity** means the Web UI can do everything DearPyGui does today, without regressions.

### High-level goal
Build a thin WebSocket bridge (`web_ui/bridge/`) and a React frontend (`web_ui/frontend/`) that consume the same `ui_queue` tuples DearPyGui already uses. The backend (`core/`, `memory/`, `llm/`, `tools/`) requires **zero changes**.

---

## 2. Current Status

| Metric | Value |
|---|---|
| Branch | `feature/web-ui-bridge` |
| Base commit | `1414316` (`fix/guest-voice-name-disambiguation`) |
| Completed phase | **Phase 12** |
| Python tests | **213 / 213 passing** |
| Frontend typecheck | **Passing** |
| Frontend build | **Passing** (211 kB JS + 9.7 kB CSS) |
| Manual tested by Baris | **Checkpoint 2 passed** — all panels, chat, streaming, single-reply, no router leak |
| Next phase | **Phase 15 planning** — DearPyGui retirement criteria evaluation |

### What works today
- Bridge server (`BridgeServer`) forwards `ui_queue` events to WebSocket clients
- Adapter converts 30+ backend event kinds to JSON frames
- Frontend receives and renders: chat, streaming, status, activity/logs, boot sequence, raw event inspector
- Frontend sends actions: `send_message`, `stop`, `new_session`, `restart_piper`, `event_speech_mode`, `live_screen_mode`, `live_screen_interval`, `code_send`, `code_run`, `code_clear`, `document_picker_selected`, `document_picker_cancel`
- `chat.sync` sends visible transcript to each new WebSocket client
- Delta coalescing (~16 ms) and auto-scroll in chat panel
- Thinking placeholder lifecycle (`assistant` and `system` roles)
- DPG guard prevents native DearPyGui crash in Web mode
- **Code Session panel** (Phase 9): output, status, preview, stdin, run, send, clear
- **Document ingestion panel** (Phase 10): path input, selected paths list, ingest, cancel
- **Image / Vision panel** (Phase 11): `<img>` preview via safe static HTTP serving from `CFG.WORKSPACE_DIR`, vision notes, caption/path fallback
- **System / Identity panel** (Phase 12): user changed events, stats refresh, config reload log, controls refresh counter
- Safe static file serving with path traversal guards, extension whitelist, CORS headers
- **Web UI / WebView mic capture** (Phase 14B) — MediaRecorder, permission handling, abort/discard, backend STT + voice identity

### What does NOT work yet
- TTS in browser
- Search result display panel (raw inspector only)
- Desktop wrapper (Tauri/pywebview)
- DearPyGui retirement
- Settings mutation UI (config reload is read-only)

---

## 3. Original Plan vs Modified Plan

The original v2.1 plan proposed 7 phases over ~5 weeks. Live testing revealed safety issues that forced insertions and re-numbering. The table below maps original intent to what was actually built.

| Original plan phase | Original intent | Actual modified phase(s) | Why it changed |
|---|---|---|---|
| Phase 0 — Contract Map | Read-only doc: map all events/actions | **Phase 0** | Unchanged. Produced `web_ui/bridge/CONTRACT.md`. |
| Phase 1 — Pure Adapter | `adapter.py` + `message_schema.py` + smoke tests | **Phase 1** + **Phase 1.1** | Phase 1.1 added: `sourceKind` field, strict unknown-event policy, leakage guard for voice-identity events, action parsing tests. These were discovered during first test run. |
| Phase 2 — Bridge Server | Standalone WebSocket/HTTP server | **Phase 2** | Unchanged core server. Added `ws_path` enforcement (`/ws` only) and `on_client_connect` callback (Phase 7) later. |
| Phase 3 — Runtime Wiring | Wire `app.py` + `controller.py` branch | **Phase 3** + **Phase 3.1** | Phase 3.1 added hardening: DPG-guarded dispatch audit, verified no unsafe DPG calls in Web mode, proved `pump_ui_queue_web` does not call DPG. Found `does_item_exist` hard-exit risk. |
| Phase 4 — React Shell | Scaffold Vite + React + layout | **Phase 4** | Simplified from full cockpit shell to functional chat + sidebar + controls. TypeScript compiles clean. |
| Phase 5 — Live Connection | Wire WebSocket, streaming, status | **Phase 5** | Required 3 live smoke fixes: `boot_ready` state drift, DPG `does_item_exist` crash, `chat_append` broadcast gap. Introduced `pump_ui_queue_web`, `bridge_queue`, `_state_synced` protocol. |
| Phase 6 — Controls | Wire all buttons + keyboard shortcuts | **Phase 6** | Became regression lock + parity baseline. Added 10 new tests and 16-row parity table in CONTRACT.md instead of finishing all controls. |
| Phase 7 — Validate + Retire | Parity checklist, flip default | **Phase 7** + **Phase 7.1** | Split into backend chat sync + frontend chat hardening (Phase 7), then thinking placeholder role fix (Phase 7.1). Retirement deferred far into the future. |

### Why the phase numbering diverged

Live testing on Windows revealed issues the original plan did not anticipate:

1. **ui_queue single-consumer problem** — `queue.Queue.get()` removes items. Having both DPG and Web bridge read from the same queue causes message theft. Fixed with `pump_ui_queue_web` + `bridge_queue` separation (Phase 5).
2. **boot_ready state drift** — Web mode never saw `boot_ready` because DPG's deferred-hide logic gated it. Fixed by making `boot_ready` forward in both modes (Phase 5).
3. **DPG hard-exit risk** — `run_web()` called DPG's `does_item_exist` directly, causing a native crash when no DPG context existed. Fixed with monkeypatch guard + restoration (Phase 3.1 / Phase 5).
4. **chat_append broadcast gap** — `chat_append` events were not reaching WebSocket clients because `ui_queue` was consumed by DPG pump. Fixed with bridge queue forwarding (Phase 5).
5. **Thinking placeholder role mismatch** — Piper creates "Thinking..." as an `assistant` message, but frontend only checked `role === "system"`. Fixed in Phase 7.1.

---

## 4. Completed Modified Phase History

### Phase 0 — Contract Map

**Goal:** Read-only documentation of every `ui_queue` event kind, payload shape, visibility rule, and action callback.

**Files read:** `AGENTS.md`, `docs/DOCUMENTS_MAP.md`, `docs/WIP.md`, `docs/architecture/TRIGGER_FLOW.md`, `notes/debug-protocol.md`, `ui/controller_queue.py`, `ui/controller.py`, `ui/layout.py`, `ui/controller_actions.py`, `ui/controller_status.py`, `ui/controller_render.py`, `app.py`, `config.py`, `core/contracts.py`.

**Deliverable:** `web_ui/bridge/CONTRACT.md`

**Proof:** Contract exists, committed as `docs(web-ui): map UI bridge contract`.

---

### Phase 1 — Pure Adapter

**Goal:** Build the translation layer with deterministic tests. No WebSocket, no HTTP, no `app.py` changes.

**New files:**
- `web_ui/__init__.py`
- `web_ui/bridge/__init__.py`
- `web_ui/bridge/message_schema.py` — dataclass schemas, `KNOWN_EVENT_KINDS`, `KNOWN_ACTION_NAMES`
- `web_ui/bridge/adapter.py` — `ui_tuple_to_ws_frame()`, `parse_action_frame()`, strict validation
- `web_ui/bridge/test_adapter.py` — pytest smoke tests (one per event kind)

**Key design:**
- `ui_tuple_to_ws_frame()` raises `ValueError` on unknown event kinds
- `parse_action_frame()` raises `ValueError` on unknown action names
- Every frame includes `timestamp`, `requestId`, `sourceKind`

**Proof:** `python -m pytest web_ui/bridge/test_adapter.py -v` — all pass.

---

### Phase 1.1 — Adapter Corrections

**Goal:** Harden adapter with `sourceKind` presence, leakage guard, and stricter validation.

**What changed:**
- Added `sourceKind` field to every outgoing frame (prevents silent schema drift)
- Added leakage prevention: voice-identity clarification events, `[UI]` system noise, and disambiguation messages are suppressed (`_suppressed: true`)
- `user_role` messages are never suppressed
- Added `is_known_event_kind()`, `get_frontend_event_name()`, `get_event_schema()` helpers
- Added action parsing tests for all 17 actions

**Why:** First test run against real Piper runtime revealed that internal system messages were leaking into the frontend chat. The adapter needed to enforce the same filtering rules DPG already used.

**Proof:** `TestSourceKindPresence`, `TestLeakagePrevention`, `TestUnknownEventStrictness` classes in `test_adapter.py`.

---

### Phase 2 — Standalone Bridge Server

**Goal:** Add the WebSocket + HTTP server using the adapter from Phase 1. Still no `app.py` changes.

**New file:** `web_ui/bridge/server.py`

**Key design:**
- `websockets` library (pure Python, single dependency)
- Runs in a **daemon thread** — crash does not take down Piper
- Reads from `ui_queue` via `get_nowait()` loop, broadcasts to all connected WS clients
- Receives action frames from clients, places them on `action_queue`
- Serves static files from `web_ui/frontend/dist/`
- `ws_path` enforcement: only `/ws` accepted; other paths get HTTP 403
- `on_client_connect` callback (added Phase 7): sends initial sync frames per client

**Proof:** `python -m pytest web_ui/bridge/test_server.py -v` — all pass.

---

### Phase 3 — Opt-in Runtime Wiring

**Goal:** Connect the bridge to the actual Piper runtime.

**Files changed:**
- `config.py` — added `WEB_UI_ENABLED`, `WEB_UI_HOST`, `WEB_UI_PORT`, `WEB_UI_WS_PATH`
- `ui/controller.py` — added `run_web()` method
- `app.py` — branch: if `WEB_UI_ENABLED`, start bridge + call `run_web()`

**Key design:**
- Config flag selects ONE path at startup (alternate-consumer model)
- `run_web()` does everything `run()` does EXCEPT `build_ui()` and `dpg.render_dearpygui_frame()`
- Instead: `pump_ui_queue_web()` in a tight loop (~60 fps)

**Proof:** `python -m pytest web_ui/bridge/test_runtime_wiring.py::TestAppBranch -v`

---

### Phase 3.1 — Runtime Dispatch Hardening

**Goal:** Ensure Web mode never calls unsafe DPG functions.

**What changed:**
- Added `pump_ui_queue_web()` in `ui/controller_queue.py` — DPG-free state updates
- Verified every web action handler uses only controller methods, not DPG tags
- Proved `pump_ui_queue_web` does not call `dpg.does_item_exist`, `dpg.set_value`, etc.
- Added `_dpg_item_exists()` safe wrapper for optional DPG checks

**Why:** Live test showed that `controller._refresh_chat_ui()` called `dpg.does_item_exist`, which crashes when no DPG context exists. The Web mode needed a completely separate pump path.

**Proof:** `TestWebDispatchDpgSafety`, `TestWebDispatchNeverCallsPumpUiQueue` in `test_runtime_wiring.py`.

---

### Phase 4 — React Frontend Shell

**Goal:** Scaffold Vite + React + TypeScript. Build functional layout with live connection.

**Files created:**
- `web_ui/frontend/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`
- `web_ui/frontend/src/main.tsx`, `App.tsx`, `index.css`
- `web_ui/frontend/src/types.ts` — `BackendFrame`, `ChatMessage`, `ConnectionState`, `RawEvent`
- `web_ui/frontend/src/bridge.ts` — `PiperBridge` class with auto-reconnect

**Key design:**
- Single `App.tsx` with chat panel, status sidebar, activity/logs, raw event inspector, controls footer
- `PiperBridge` handles WebSocket lifecycle, JSON parsing, state change callbacks
- `handleFrame` switch dispatches events to React state
- No separate component files yet — everything in `App.tsx` for velocity

**Proof:** `npm run typecheck` and `npm run build` both pass.

---

### Phase 5 — Live Smoke Fixes

**Goal:** Make the live WebSocket connection actually work with a running Piper backend.

**Bugs found and fixed:**

#### Bug 1: `boot_ready` never true in Web mode
- **Symptom:** Frontend never received `boot.ready`, status stayed stuck
- **Root cause:** DPG's `refresh_top_bar()` gated `boot_ready` behind `_boot_ui_min_visible_until` and DPG tag existence checks
- **Fix:** `boot_ready` event now forwards unconditionally in both `pump_ui_queue()` and `pump_ui_queue_web()`

#### Bug 2: DPG `does_item_exist` hard-exit
- **Symptom:** `run_web()` crashed with native DearPyGui exception
- **Root cause:** `run_web()` called `build_ui()` which called `dpg.does_item_exist()` before DPG context existed
- **Fix:** Monkeypatch `dpg.does_item_exist = lambda _tag: False` during `run_web()`, restore in `finally`

#### Bug 3: `chat_append` not broadcast to WebSocket
- **Symptom:** User messages appeared in frontend locally but assistant responses never arrived
- **Root cause:** `BridgeServer` consumed `controller.ui_queue` directly, but DPG pump also consumed from the same queue (message theft)
- **Fix:** Introduced `bridge_queue` — `pump_ui_queue_web()` forwards events from `ui_queue` to `bridge_queue`, and `BridgeServer` consumes `bridge_queue`

**New concepts introduced:**
- `bridge_queue`: separate queue consumed by BridgeServer
- `_state_synced`: marker on `chat_append` events to prevent double-appending to `chat_state`
- `pump_ui_queue_web()`: DPG-free pump that forwards to bridge queue

**Proof:** `TestBootReadyWebState`, `TestDpgHardExitGuardLifecycle`, `TestBridgeQueueSeparation`, `TestChatAppendBroadcastContract` in `test_runtime_wiring.py`.

---

### Phase 6 — Regression Lock + Parity Baseline

**Goal:** Ensure Phase 5 fixes stay fixed. Document parity gaps.

**What changed:**
- Added 10 new regression tests across 7 test classes
- Updated `CONTRACT.md` with 16-row parity table (DearPyGui vs Web UI status)
- Locked test count at 140 (later 147 after Phase 7)

**New test classes:**
- `TestBootReadyWebState` — `boot_ready` forwards in Web mode
- `TestStateSyncedDuplicatePrevention` — `_state_synced` skips re-append
- `TestDpgHardExitGuardLifecycle` — monkeypatch restores on exit
- `TestBridgeQueueSeparation` — BridgeServer uses bridge_queue
- `TestChatAppendBroadcastContract` — `chat_append` emits `_state_synced`
- `TestDpgPumpCompatibility` — DPG pump still works without forward_queue
- `TestNonSyncedChatAppendWebState` — non-synced events still append

**Proof:** All 140 tests pass. Parity table in CONTRACT.md Section 11.2.

---

### Phase 7 — Chat Sync + Frontend Chat Hardening

**Goal:** On WebSocket connect, sync visible transcript. Harden chat UX.

**Backend changes:**
- Added `chat_sync` → `chat.sync` event mapping in `message_schema.py`
- Added `_normalize_chat_sync_payload()` in `adapter.py`
- `BridgeServer.__init__` accepts `on_client_connect` callback
- `run_web()` passes `_build_chat_sync_frames` callback to BridgeServer
- `_build_chat_sync_frames` calls `renderable_chat_messages()` to exclude hidden/system noise

**Frontend changes:**
- `chat.sync` handler: replaces transcript, dedupes, preserves local user messages and active streaming state
- Auto-scroll: `chatBoxRef` + `useEffect` on `messages`
- Stream delta coalescing: `pendingDeltasRef` + 16 ms flush timer
- Thinking placeholder: `stream.start` clears thinking, `chat.clear_thinking` filters placeholders

**New tests:**
- `TestChatSyncUsesRenderableMessages` — proves hidden messages excluded, correct payload shape

**Proof:** 147/147 tests pass. Frontend build passes.

---

### Phase 7.1 — Thinking Placeholder Role Fix

**Goal:** `isThinkingPlaceholder()` matched only `role === "system"`, but Piper creates the placeholder as an `assistant` message.

**Fix:**
```typescript
function isThinkingPlaceholder(m: ChatMessage): boolean {
  const text = m.content.trim();
  return (
    (m.role === "assistant" || m.role === "system") &&
    (text === "Thinking..." || text === "Thinking…" || text.startsWith("Thinking"))
  );
}
```

**Proof:** Frontend typecheck + build pass. No runtime changes.

---

## 5. Current Architecture

### Data flow (backend → frontend)

```
┌─────────────────────────────────────────────────────────────┐
│  Piper Backend                                                │
│  core/orchestrator.py → ui_queue.put((kind, payload))        │
│  controller.chat_append(role, content) → chat_state           │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  ui/controller_queue.py                                       │
│  pump_ui_queue_web(controller, forward_queue=bridge_queue)   │
│  • handles _state_synced marker                               │
│  • skips DPG calls entirely                                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  BridgeServer (daemon thread)                                 │
│  • consumes bridge_queue                                      │
│  • on_client_connect → sends chat.sync frame                  │
│  • adapter.ui_tuple_to_ws_frame(kind, payload) → JSON        │
│  • broadcasts to all WS clients                               │
└──────────────────────┬──────────────────────────────────────┘
                       │ ws://127.0.0.1:8787/ws
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  React Frontend (App.tsx)                                     │
│  • PiperBridge receives JSON frames                           │
│  • handleFrame() dispatches to React state                    │
│  • chat panel, status sidebar, activity/logs, inspector       │
└─────────────────────────────────────────────────────────────┘
```

### Data flow (frontend → backend)

```
React controls → sendAction(action, payload)
→ PiperBridge.send() → WebSocket → BridgeServer
→ action_queue → PiperController._dispatch_web_action()
→ controller method (submit_user_text, on_stop, etc.)
```

### Key safety mechanisms

| Mechanism | Purpose | Location |
|---|---|---|
| `bridge_queue` | Prevents `ui_queue` double-consumption | `controller_queue.py`, `server.py` |
| `_state_synced` | Prevents duplicate `chat_state.append` | `controller.py`, `controller_queue.py` |
| DPG guard | Prevents native DPG crash in Web mode | `controller.py` (`run_web()`) |
| `ws_path` enforcement | Only `/ws` accepted; blocks probe traffic | `server.py` |
| Unknown-event strictness | Adapter raises on unmapped events | `adapter.py` |
| Leakage guard | Suppresses voice-identity/system noise | `adapter.py` |
| Delta coalescing | Batches rapid stream deltas (~16 ms) | `App.tsx` |
| Auto-reconnect | Frontend reconnects after disconnect | `bridge.ts` (3 s timer) |

### DearPyGui path (unchanged)

When `PIPER_WEB_UI_ENABLED=false` (default):
```
ui_queue → pump_ui_queue() → DPG widgets
```

`run()` is called instead of `run_web()`. No bridge thread starts. All existing behavior is preserved.

---

## 6. Event and Action Contract

**Authoritative low-level contract:** `web_ui/bridge/CONTRACT.md`

Do not duplicate CONTRACT.md here. The migration guide is the roadmap; CONTRACT.md is the frame-format reference.

### Summary

| Dimension | Count |
|---|---|
| Backend event kinds | 30+ |
| Frontend actions | 17 |
| Unknown event policy | Strict — adapter raises `ValueError` |
| Unknown action policy | Strict — parser raises `ValueError` |
| `sourceKind` | Preserved on every outgoing frame |
| Hidden/system leakage | Blocked by adapter suppression rules |
| Chat visibility | `renderable_chat_messages()` filter enforced backend + frontend |

### Event categories

- **Streaming:** `stream.start`, `stream.delta`, `stream.end`
- **Chat:** `chat.append`, `chat.sync`, `chat.clear_thinking`
- **Status:** `status.set`, `status.mode`, `status.step`
- **Activity/Log:** `activity.append`, `boot.log`, `boot.ready`, `log.agent`
- **System:** `error`, `ui_controls_refresh`, `config_reloaded`
- **Code:** `code.launch`, `code.reset`, `code.output`, `code.status`, `code.active`, `code.focus`, `code.preview`
- **Documents:** `document.view`, `document.ingest_active`
- **Image/Vision:** `image.show`, `vision.note`
- **System/Identity:** `stats_view_refresh`, `active_user_changed`, `config_reloaded`, `ui_controls_refresh`
- **Future (not wired to frontend yet):** `search_result`, `live_screen_refresh`

### Action categories

- **Chat controls:** `send_message`, `stop`, `new_session`, `clear_chat`
- **System:** `restart_piper`, `event_speech_mode`
- **Screen:** `live_screen_mode`, `live_screen_interval`
- **Code:** `code_send`, `code_run`, `code_clear`
- **Documents:** `document_picker_selected`, `document_picker_cancel`
- **Future (not wired):** `mic_toggle`, `snapshot`

---

## 7. Manual Testing Policy

**Baris should not manually test every phase.** Manual testing begins only when instructed after a stable checkpoint.

### Manual test checkpoint 1 (completed)
**Trigger:** After Phase 8 guide consolidation.

**Result:** Chat, streaming, status, New Session, Restart verified by Baris.

### Manual test checkpoint 2 (passed)
**Trigger:** After Phase 12.5–12.8 (this document). All sidebar panels are now wired.

**Retest coverage:**
- Chat basics (connect, send, stream, stop, New Session, Restart)
- Turkish character rendering correct
- Single assistant reply for normal chat prompts (no double bubble)
- No visible `[ROUTER]` or `[RECALL:...]` in chat
- Code Session, Document Ingestion, Image/Vision, System/Identity panels functional
- Sidebar layout acceptable

**What NOT to test yet:**
- TTS in browser
- Desktop wrapper (Tauri/pywebview)
- Settings mutation (config is read-only in Web UI)
- File picker native dialog (document panel uses path text input)

### Regression notes

**Phase 12.6 — Web stream lifecycle & internal marker scrubbing**
- Bug: Web UI showed raw `assistant_stream_delta` text while DPG showed scrubbed text from `chat_state`. `[ROUTER]` and `[RECALL:...]` leaked into visible chat.
- Fix: `TagScrubber` strips `[ROUTER]` / `[RECALL:...]`; `pump_ui_queue_web` forwards **clean** deltas; `App.tsx` `stream.start` replaces existing streaming bubble; `renderable_chat_messages` last-line filter excludes internal markers.

**Phase 12.7 — Router loopback after visible reply**
- Bug: Persona emitted visible answer + hidden `[ROUTER]` → backend looped to `ROUTE` → second assistant reply for same user turn.
- Fix: `_should_ignore_router_after_visible_reply(clean_answer, router_requested)` guard in `_run_persona_core`. If `clean_answer.strip()` is truthy, `[ROUTER]` is ignored and turn finishes. Pure `[ROUTER]` / empty visible answer still allows loopback.

### Manual test checklist — Checkpoint 2

**1. Start backend:**
```powershell
$env:PIPER_WEB_UI_ENABLED = "true"
python app.py
```

**2. Start frontend (separate terminal):**
```powershell
cd web_ui/frontend
npm run dev
```

**3. Open:**
```
http://localhost:3000
```

**4. Chat test:**
- [ ] DearPyGui does **not** open
- [ ] Web UI connects (badge shows "connected")
- [ ] Boot logs appear in sidebar Activity & Logs panel
- [ ] `boot.ready` appears, status shows "IDLE"
- [ ] Send one simple message (e.g., "hello")
- [ ] Response streams character-by-character
- [ ] "Thinking..." placeholder appears then disappears
- [ ] Browser refresh restores transcript through `chat.sync`
- [ ] Stop button interrupts generation
- [ ] New Session clears chat
- [ ] Restart exits cleanly (no orphan llama-server processes)

**5. New Session summary reset test:**
- [ ] Have a conversation (3+ turns)
- [ ] Click New Session
- [ ] Chat clears
- [ ] Send a new message
- [ ] Piper should not hallucinate context from the previous session

**6. Code Session panel (light test):**
- [ ] Panel shows "idle" status
- [ ] Enter a Python script path in the path input
- [ ] Click Run
- [ ] Output appears in the panel
- [ ] Click Clear resets output

**7. Document Ingestion panel (light test):**
- [ ] Enter a file path in the path input (e.g., `C:\temp\test.txt`)
- [ ] Click Add
- [ ] Path appears in the selected list
- [ ] Click Ingest Selected
- [ ] Status shows "Ingesting..." then returns to "Idle"
- [ ] Click Clear empties the selected list

**8. Image / Vision panel (test if image exists):**
- [ ] If Piper generates an image, the panel shows the image preview
- [ ] If the image fails to load, caption and path are shown as fallback
- [ ] Vision notes appear below the image when live screen is active
- [ ] Click Clear Notes empties vision notes

**9. System / Identity panel (observation only):**
- [ ] Panel shows Identity, Stats, Controls Refresh, Config Reloads
- [ ] When config changes, a new entry appears in Config Reloads with timestamp
- [ ] Controls Refresh counter increments when UI controls change
- [ ] Click Clear Stats and Clear Config Log work

**10. Raw Events inspector:**
- [ ] Every backend event appears in the Raw Events panel
- [ ] Events are collapsible (click to expand payload JSON)

**11. Cleanup:**
- [ ] Close browser tab
- [ ] Stop frontend dev server (Ctrl+C in frontend terminal)
- [ ] Stop backend (Ctrl+C in backend terminal)
- [ ] Verify no lingering `python.exe` or `llama-server.exe` processes

**If any checklist item fails:**
1. Capture the symptom
2. Check `notes/debug-protocol.md` for symptom-to-file lookup
3. File a bug with: phase, symptom, reproduction steps, expected vs actual

---

## 8. Remaining Roadmap

### Phase 9 — Code Session Panel ✅ COMPLETE

**Delivered:** Frontend-only panel with output, status, preview, stdin, run/send/clear controls.

**Files touched:**
- `web_ui/frontend/src/App.tsx`
- `web_ui/frontend/src/styles.css`

---

### Phase 10 — Document Ingestion ✅ COMPLETE

**Delivered:** Path text input, Add/Ingest/Cancel controls, selected paths list, ingest status.

**Files touched:**
- `web_ui/frontend/src/App.tsx`
- `web_ui/frontend/src/styles.css`

---

### Phase 11 — Image / Vision Display ✅ COMPLETE

**Delivered:** Safe static file serving (`GET /workspace/<filename>`) with traversal guards, extension whitelist, CORS. `<img>` preview with caption/path fallback. Vision notes panel.

**Files touched:**
- `web_ui/bridge/server.py`
- `web_ui/bridge/adapter.py`
- `web_ui/frontend/src/App.tsx`
- `web_ui/frontend/src/styles.css`

---

### Phase 12 — Stats / Settings / Identity Surface ✅ COMPLETE

**Delivered:** Identity status, stats refresh, config reload log with timestamps, controls refresh counter. All read-only observation.

**Files touched:**
- `web_ui/frontend/src/App.tsx`
- `web_ui/frontend/src/styles.css`

---

### Phase 14A — Backend Mic Audio Submission Foundation ✅ COMPLETE

**Delivered:** Backend-only action `mic_audio_submit`, local audio decoder (`tools/audio_decode.py`), `STTEngine.transcribe_buffer()`, `mic.status` event, payload validation, config guards.

**Not delivered:** Frontend MediaRecorder capture (Phase 14B). No Web UI mic button exists yet.

**Architecture:**
- Web UI / WebView records one complete utterance (future Phase 14B).
- Frontend sends base64 audio via `mic_audio_submit` action.
- Backend decodes with ffmpeg (WebM/Opus → 16 kHz mono WAV).
- Backend runs existing offline Faster-Whisper STT via `transcribe_buffer()`.
- Backend runs existing voice identity/auth path unchanged.
- Final transcript enters Piper as a voice input via `submit_user_text()`.

**Security:**
- Max decoded size: `PIPER_WEB_MIC_MAX_DECODED_BYTES` (default 10 MiB).
- Max duration: `PIPER_WEB_MIC_MAX_SECONDS` (default 60 s).
- Temp files only; cleaned up in `finally`.
- No base64 audio logged; only sizes and status.
- Backend busy guard rejects audio while Piper is generating.

**Files touched:**
- `tools/audio_decode.py` — new local audio decoder
- `tools/stt.py` — `transcribe_buffer()`, refactored shared helpers
- `ui/controller.py` — `_handle_web_mic_audio_submit()`, dispatch branch
- `web_ui/bridge/message_schema.py` — `mic_audio_submit`, `mic_status`
- `web_ui/bridge/adapter.py` — `mic.status` normalizer
- `config.py` — `WEB_MIC_MAX_DECODED_BYTES`, `WEB_MIC_MAX_SECONDS`

**Tests added:** Action parsing, event normalization, audio decode (mock ffmpeg), controller dispatch (mock STT), config defaults.

---

### Phase 14B — Frontend MediaRecorder / WebView Mic Capture ✅ COMPLETE

**Delivered:** Frontend MediaRecorder API for utterance capture (WebM/Opus), mic button with state machine, abort/discard behavior, `mic.status` event handling.

**Files touched:**
- `web_ui/frontend/src/App.tsx` — mic state machine, MediaRecorder lifecycle, base64 encode, abort helpers
- `web_ui/frontend/src/styles.css` — mic button pulse animation, error state, status text

**Behavior:**
- MIC button: idle → requesting_permission → listening (STOP) → transcribing → idle/error
- Permission denied: shows user-safe error message
- Global Stop / New Session / Restart / disconnect: aborts active recording and discards audio
- Backend `mic.status` drives frontend mic state (transcribing → idle/error)
- Transcript appears through normal backend `chat.append` path (no duplicate frontend injection)
- No Web Speech API or cloud STT used

---

### Phase 13 — Mic / STT Browser Integration

> **Superseded by Phase 14A + 14B.** The original Phase 13 planning doc recommended an utterance-based backend stream path. Phase 14A implements the backend half; Phase 14B will implement the frontend half.

---

### Phase 14 — Desktop Wrapper

**Scope:**
- **Tauri first** (Rust-based, small binary)
- **pywebview second** (Python-native, simpler build)
- Browser app-mode fallback only for temporary debugging

**Files likely touched:**
- New top-level directory `desktop/` or `tauri/`
- Build scripts for `.exe` generation

**Constraint:** Do not start before Web UI parity is proven. The wrapper is just a chrome-less browser.

---

### Phase 15 — DearPyGui Retirement Decision

**Criteria for retirement:**
1. All Phase 9–12 features work without regressions
2. 1+ week of daily use by Baris without issues
3. Manual test checklist passes 100%
4. Windows desktop wrapper (Phase 14) produces stable `.exe`

**Transition plan:**
1. Change `WEB_UI_ENABLED` default from `False` to `True`
2. Keep DearPyGui code in `ui/` as emergency fallback
3. Document `PIPER_WEB_UI_ENABLED=false` for legacy mode
4. Archive DPG retirement plan in `docs/archive/`

**Do not retire DearPyGui before Phase 15.**

---

## 9. Non-goals Until Later

The following are explicitly out of scope until their respective phases:

| Non-goal | Why deferred | Target phase |
|---|---|---|
| Tauri / pywebview wrapper | Need stable Web UI first | 14 |
| Avatar / persona visual | Not needed for functional parity | Post-15 |
| Cloud dependency | Piper is offline-first by design | Never |
| TTS port 8765 conflict | Already reserved; bridge uses 8787 | N/A |
| DearPyGui removal | Fallback must remain until parity proven | 15 |
| Web UI as default | Must be opt-in until Baris accepts it | 15 |
| Browser mic/STT | Implemented via backend STT + voice identity (Phases 14A–14B) | 14B |
| File picker native dialog | Needs Web-safe path handling | 10 |
| WebGL / canvas animations | Visual polish, not functional | Post-15 |

---

## 10. Validation Commands

Run these before every commit to `feature/web-ui-bridge`:

### Python
```bash
# Syntax check
python -m compileall web_ui ui app.py config.py

# Targeted bridge tests (fast)
python -m pytest web_ui/bridge/test_adapter.py web_ui/bridge/test_server.py web_ui/bridge/test_runtime_wiring.py -v

# Full bridge test suite (slower)
python -m pytest web_ui/bridge/ -q
```

### Frontend
```bash
cd web_ui/frontend
npm run typecheck
npm run build
```

### Pre-push checklist
- [ ] `python -m compileall` passes
- [ ] All 213 bridge tests pass
- [ ] `npm run typecheck` passes
- [ ] `npm run build` passes
- [ ] No changes to `data/users.json` unless intentionally part of the PR
- [ ] No generated files (`dist/`, `node_modules/`) committed

---

## 11. Source of Truth

| Document | Role |
|---|---|
| **This migration guide** (`docs/specs/piper-web-ui-migration-guide.md`) | High-level roadmap, phase history, remaining work |
| **`web_ui/bridge/CONTRACT.md`** | Authoritative low-level event/action contract (frame formats, payload shapes, parity table) |
| **Tests** (`web_ui/bridge/test_*.py`) | Proof source — behavior is defined by passing tests |
| **`AGENTS.md`** | Repository doctrine — architectural boundaries all agents must respect |
| **`notes/debug-protocol.md`** | Operational debugging guide for live issues |
| **DearPyGui** | Fallback UI until Web UI parity is verified and accepted |

If this guide and CONTRACT.md conflict, **tests win**. The code is the final authority.

---

## 12. Document History

| Date | Change |
|---|---|
| 2026-05-14 | v2.0 original architecture plan (`docs/archive/piper-ui-architecture-plan.md`) |
| 2026-05-14 | v2.1 corrected plan (`docs/specs/piper-ui-architecture-plan-v2.md`) — port 8787, alternate consumer, adapter-first order |
| 2026-05-09 | Phase 8 — This migration guide created to reconcile original plan with modified phase history |
| 2026-05-09 | Phase 12 complete — System/Identity panel, image serving, document ingestion, code session |
| 2026-05-09 | Phase 12.5 — Manual checkpoint 2 prep: layout audit, CSS safety fix, updated checklist |
| 2026-05-09 | Phase 12.6 — Fixed duplicate assistant replies and `[ROUTER]` / `[RECALL:...]` leak into visible chat |
| 2026-05-09 | Phase 12.7 — Fixed backend router loopback after visible reply; pure router still routes |
| 2026-05-09 | Phase 12.8 — Checkpoint 2 marked passed; docs updated; next: Phase 13 planning |
| 2026-05-09 | Phase 14A — Backend mic audio submission foundation: decoder, transcribe_buffer, mic_audio_submit action, mic.status event, tests |
| 2026-05-09 | Phase 14A.1 — Enforce WEB_MIC_MAX_SECONDS duration guard; remove unused sample_rate_hint parsing |
| 2026-05-09 | Phase 14B — Frontend MediaRecorder mic capture: idle/listening/transcribing/error state machine, abort/discard, mic.status wired |
