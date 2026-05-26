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
  - [ ] `npm run test` — passes
- [ ] Backend validation (if practical):
  - [ ] `python -m compileall app.py config.py core ui memory tools llm`
  - [ ] `python -m pytest tests/ --ignore=tests/golden` — passes

---

## 2. Boot

- [ ] Start Piper Web UI using the repo's normal command (e.g. `python app.py` or `start_piper.bat`)
- [ ] Confirm `boot.ready` event arrives and UI shows "connected" state
- [ ] Confirm no startup error appears in chat or System Drawer

---

## 3. Chat / Stream

- [ ] Send a normal chat message
- [ ] Confirm assistant stream appears
- [ ] Confirm no duplicate assistant bubble
- [ ] Confirm stream settles cleanly (no stuck "Thinking...")

---

## 4. Stop Behavior

- [ ] While idle: Stop button is disabled
- [ ] During generation: Stop button is enabled
- [ ] Click Stop
- [ ] Confirm Stop disables immediately
- [ ] Confirm no second Stop dispatch / no stuck generating state

---

## 5. Mic Behavior

- [ ] Start mic recording
- [ ] Stop recording
- [ ] Confirm "transcribing" state appears
- [ ] Confirm backend `mic.status idle` acknowledgement clears local transcribing state
- [ ] Confirm backend `mic.status error` shows error state (if testable)

---

## 6. Workspace File List

- [ ] Open workspace panel
- [ ] Confirm top-level files appear
- [ ] Confirm nested files appear (e.g. inside `src/`, `tests/`)
- [ ] Confirm nested labels display as workspace-relative, e.g. `src/main.py`
- [ ] Confirm unsupported files and directories do not appear

---

## 7. Workspace Read / Save

- [ ] Open a `.txt` or `.md` file from the workspace list
- [ ] Edit text content
- [ ] Click Save
- [ ] Confirm save succeeds and path remains inside workspace (no "Access denied")

---

## 8. Code Run

- [ ] Open a `.py` file from the workspace list
- [ ] Confirm path field contains the full file path
- [ ] Click Run
- [ ] Confirm no `FILE_OP paths must be relative to the workspace` error
- [ ] Confirm output appears in the code output pane
- [ ] Confirm stdin Send works for a running script
- [ ] Confirm code Stop works

---

## 9. Documents Rail

- [ ] Expand Documents card
- [ ] Add document paths manually (semicolon-separated)
- [ ] Click Ingest Selected
- [ ] Confirm `document_picker_selected` dispatch
- [ ] Click Cancel during active ingest
- [ ] Confirm `document_picker_cancel` dispatch
- [ ] Click Clear
- [ ] Confirm selected list empties

---

## 10. Errors / Logs

- [ ] Confirm System Drawer has separate Errors and Recent Events areas
- [ ] Confirm backend/action errors appear in Errors without breaking stream/chat state

---

## 11. Pass / Fail Notes

| Area | Pass / Fail | Notes |
|------|-------------|-------|
| Prep | | |
| Boot | | |
| Chat / Stream | | |
| Stop Behavior | | |
| Mic Behavior | | |
| Workspace File List | | |
| Workspace Read / Save | | |
| Code Run | | |
| Documents Rail | | |
| Errors / Logs | | |
