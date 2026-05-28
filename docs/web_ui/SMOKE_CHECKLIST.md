# Web UI Smoke Checklist

Manual smoke-test checklist for the current Web UI stabilization pass.
Run this after any frontend or backend Web UI boundary change.

> DearPyGui mode is maintenance-only and not covered by this checklist.

---

## 1. Prep

- [ ] Checkout latest `main`
- [ ] Install frontend deps if needed: `cd web_ui/frontend && npm install`
- [ ] Frontend validation:
  - [ ] `npm run typecheck` — passes
  - [ ] `npm run build` — passes
  - [ ] `npm run test` — passes (65 tests)
- [ ] Backend validation (if practical):
  - [ ] `python -m compileall app.py config.py core ui memory tools llm`
  - [ ] `python -m pytest tests/ --ignore=tests/golden --ignore=tests/test_proactive_monitor.py -q` — passes

---

## 2. Launch

### Default — Desktop Window (pywebview)
```powershell
python app.py
```
- [ ] Web UI rebuilds on boot by default (`PIPER_WEB_UI_REBUILD_ON_BOOT=true`)
- [ ] Standalone "Piper" window opens automatically (`PIPER_WEB_UI_WINDOW=true`)
- [ ] No address bar visible
- [ ] `boot.ready` event arrives and UI shows "connected" state
- [ ] No startup error appears in chat or System Drawer
- [ ] App does not require browser refresh/restart tricks after boot

### Browser mode fallback
```powershell
$env:PIPER_WEB_UI_ENABLED = "true"
$env:PIPER_WEB_UI_WINDOW = "false"
python app.py
# Open http://127.0.0.1:8787/
```
- [ ] Backend-served frontend loads in browser
- [ ] WebSocket connects, status badge shows "connected"

---

## 3. Chat / Stream

- [ ] Send a normal chat message
- [ ] Confirm assistant stream appears
- [ ] Confirm no duplicate assistant bubble
- [ ] Confirm stream settles cleanly (no stuck "Thinking...")
- [ ] Confirm no `[ROUTER]` or `[RECALL:...]` visible in chat

---

## 4. Stop Behavior

- [ ] While idle: Stop button is **disabled**
- [ ] During generation: Stop button is **enabled**
- [ ] While TTS is synthesizing/playing (after stream ended): Stop button is **enabled**
- [ ] Click Stop
- [ ] Confirm Stop disables immediately
- [ ] Confirm top GENERATING pill clears
- [ ] Confirm no second Stop dispatch / no stuck generating state
- [ ] Late stream deltas after Stop must **not** create a second assistant bubble
- [ ] Text and mic messages after Stop must clear stream suppression and work normally

**Failure note:** If late deltas create a second bubble, check `App.tsx` stream suppression logic and `clearStreamSuppression` on new user send.

---

## 5. Mic Behavior

### Default native mic (backend STT)
- [ ] Click MIC button
- [ ] Backend logs show native STT activity (e.g., `Recording from device...`)
- [ ] Confirm **not** seeing `Web mic: action received` (that means experimental upload path is active)
- [ ] Stop recording
- [ ] Confirm "transcribing" state appears
- [ ] Confirm backend `mic.status idle` acknowledgement clears local transcribing state
- [ ] Confirm transcript appears once in chat (no duplicates)
- [ ] Confirm voice identity recognizes the speaker (or asks to enroll if new)

### Experimental mic upload (quarantined)
Only test if explicitly enabled:
```powershell
$env:VITE_PIPER_EXPERIMENTAL_MIC_UPLOAD = "true"
```
- [ ] Browser MediaRecorder upload path is **experimental only**
- [ ] Default mic smoke should show native STT logs, **not** `Web mic: action received`

**Failure note:** If the MIC button triggers browser upload instead of native backend mic, check that `VITE_PIPER_EXPERIMENTAL_MIC_UPLOAD` is **not** set.

---

## 6. Workspace File List

- [ ] Open workspace panel
- [ ] Confirm top-level files appear
- [ ] Confirm **nested** files appear (e.g., inside `src/`, `tests/`)
- [ ] Confirm nested labels display as workspace-relative, e.g., `src/main.py`
- [ ] Confirm unsupported files and directories do not appear

---

## 7. Workspace Read / Save

- [ ] Open a `.txt` or `.md` file from the workspace list
- [ ] Edit text content
- [ ] Click Save
- [ ] Confirm save succeeds and path remains inside workspace (no "Access denied")

---

## 8. Workspace Images

- [ ] Confirm nested workspace files are listed (including image files)
- [ ] Click an image file in the workspace list
- [ ] Confirm image opens via workspace-relative `/workspace/...` URL
- [ ] Confirm paths with **backslashes** and **spaces** work correctly
- [ ] Confirm image preview renders in the Image/Vision panel

**Failure note:** If images 404, check `BridgeServer` static file serving and path traversal guards.

---

## 9. Code Run

- [ ] Open a `.py` file from the workspace list
- [ ] Confirm path field contains the full file path
- [ ] Click Run
- [ ] Confirm no `FILE_OP paths must be relative to the workspace` error
- [ ] Confirm output appears in the code output pane
- [ ] Confirm stdin Send works for a running script
- [ ] Confirm code Stop works

---

## 10. Documents Rail

- [ ] Expand Documents card
- [ ] Add document paths manually (semicolon-separated)
- [ ] Click Ingest Selected
- [ ] Confirm `document_picker_selected` dispatch
- [ ] Click Cancel during active ingest
- [ ] Confirm `document_picker_cancel` dispatch
- [ ] Click Clear
- [ ] Confirm selected list empties

---

## 11. Search / Route Regressions

- [ ] Conversational prompts like **"show me the planets"** or **"read about space"** route to `CHAT`, **not** `FILE_WORK`
- [ ] Workspace followups like **"Read it back"** after file context should still be `FILE_WORK`
- [ ] Confirm no `looks_like_live_environment_query` NameError in backend logs

**Failure note:** If casual prompts get routed to file lookups, check `core/routing/route_normalizer.py` `_normalize_workspace_document_lookup` guards `SEARCH` and `CHAT` decisions.

---

## 12. Recall / Streaming Regression

- [ ] Trigger a recall-triggered second pass (e.g., ask a follow-up that triggers recall)
- [ ] Confirm the second pass does **not** duplicate or append stale assistant text
- [ ] Confirm exactly one assistant bubble per turn

**Failure note:** If stale text appends, check `TagScrubber` and `renderable_chat_messages` last-line filter.

---

## 13. TTS Streaming Behavior

- [ ] Send a message that produces a TTS reply
- [ ] Confirm TTS starts speaking quickly (first chunk fast-start)
- [ ] Confirm minimal pause between first and second spoken chunk
- [ ] Confirm later chunks sound natural (larger, preserving emotional continuity)

**Current 3-phase chunker thresholds:**

| Phase | Setting | Value |
|---|---|---|
| First chunk | `first_complete_min_chars` | 8 |
| First chunk | `first_force_chars` | 80 |
| Second chunk | `second_min_chars` | 100 |
| Second chunk | `second_force_chars` | 150 |
| Later chunks | `later_min_chars` | 280 |
| Safety | `max_chars` | 320 |

**Failure note:** If TTS is silent for too long at start, check `_StreamChunker` phase 0 search logic. If there's a pause after the first chunk, check phase 1 (`second_min_chars` / `second_force_chars`).

---

## 14. Errors / Logs

- [ ] Confirm System Drawer has separate Errors and Recent Events areas
- [ ] Confirm backend/action errors appear in Errors without breaking stream/chat state

---

## 15. Pass / Fail Notes

| Area | Pass / Fail | Notes |
|------|-------------|-------|
| Prep | | |
| Launch | | |
| Chat / Stream | | |
| Stop Behavior | | |
| Mic Behavior | | |
| Workspace File List | | |
| Workspace Read / Save | | |
| Workspace Images | | |
| Code Run | | |
| Documents Rail | | |
| Search / Route | | |
| Recall / Streaming | | |
| TTS Streaming | | |
| Errors / Logs | | |
