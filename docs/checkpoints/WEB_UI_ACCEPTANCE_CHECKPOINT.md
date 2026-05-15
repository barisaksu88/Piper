# Web UI Acceptance Checkpoint — Phase 15D

> **Branch:** `feature/web-ui-bridge`
> **Date:** 2026-05-15
> **Status:** Usable and accepted for daily opt-in use. Not yet default.

---

## 1. Current Status

| Item | Value |
|---|---|
| Opt-in flag | `PIPER_WEB_UI_ENABLED=true` |
| Desktop window flag | `PIPER_WEB_UI_WINDOW=true` |
| Frontend build output | `web_ui/frontend/dist` |
| Backend served URL | `http://127.0.0.1:8787/` |
| WebSocket endpoint | `ws://127.0.0.1:8787/ws` |
| Vite dev URL | `http://127.0.0.1:3000` |
| Default UI | DearPyGui (unchanged) |

---

## 2. Launch Modes

### Browser Web UI (backend-served)
```powershell
cd web_ui/frontend
npm run build
cd C:\Projects\Piper
$env:PIPER_WEB_UI_ENABLED = "true"
python app.py
# Open http://127.0.0.1:8787/ in any browser
```

### Desktop Window (pywebview)
```powershell
cd web_ui/frontend
npm run build
cd C:\Projects\Piper
$env:PIPER_WEB_UI_ENABLED = "true"
$env:PIPER_WEB_UI_WINDOW = "true"
python app.py
# Piper desktop window opens automatically
```

### Vite Dev Mode (frontend developers)
```powershell
cd web_ui/frontend
npm run dev
# Open http://localhost:3000
# WebSocket still connects to ws://127.0.0.1:8787/ws
```

### DearPyGui Fallback
```powershell
# Unset Web UI flags
Remove-Item Env:\PIPER_WEB_UI_ENABLED -ErrorAction SilentlyContinue
Remove-Item Env:\PIPER_WEB_UI_WINDOW -ErrorAction SilentlyContinue
python app.py
```

---

## 3. Green Features (Accepted)

These features are verified working and accepted for daily use:

- [x] Desktop window opens (pywebview, no address bar)
- [x] WebSocket connects automatically
- [x] Typed chat works end-to-end
- [x] Streaming assistant reply works
- [x] Native MIC works (backend sounddevice → Faster-Whisper → voice identity)
- [x] Voice identity recognizes Baris
- [x] New Session clears chat and resets context
- [x] Stop button interrupts generation
- [x] Restart button restarts Piper cleanly
- [x] Code Session panel exists and functions
- [x] Documents panel exists and functions
- [x] Image/Vision panel exists and functions
- [x] System/Identity panel exists and functions
- [x] Activity & Logs panel exists and functions
- [x] Raw Events inspector exists and functions
- [x] Backend serves built frontend without Vite
- [x] Vite dev mode still works for frontend development
- [x] DearPyGui remains the default/fallback
- [x] Backend log noise reduced (Phase 15C.2)
- [x] Window closes and cleans up without traceback

---

## 4. Deferred / Not Accepted Yet

These items are explicitly out of scope for default readiness:

| Item | Status | Notes |
|---|---|---|
| Browser MediaRecorder mic upload | Quarantined | Available only behind `VITE_PIPER_EXPERIMENTAL_MIC_UPLOAD=true`. Native MIC is the accepted path. |
| TTS in browser/window | Not implemented | TTS still plays through native OS audio. No browser TTS integration. |
| Native OS packaging / installer | Not implemented | No `.exe` installer or Start Menu shortcut yet. |
| Web UI as default | Deferred | Requires 1+ week daily-use burn-in by Baris. |
| DearPyGui retirement | Deferred | Fallback must remain until Web UI parity is proven over time. |
| Long-term daily-use stability | Not proven | Needs sustained real-world use. |
| Deep visual polish / avatar | Deferred | No animated avatar, custom themes, or visual effects. |
| File picker / native dialog polish | Deferred | Document panel uses text path input; no native file picker. |
| Search result display panel | Not implemented | Search results flow through reporter turn only. |

---

## 5. Known Risks

| Risk | Mitigation |
|---|---|
| pywebview may behave differently on other Windows versions / Edge versions | Tested on current Windows 11 + Edge Chromium. pywebview falls back gracefully if unavailable. |
| Restart from inside desktop window requires user to close window manually | Documented limitation. Restart flag is set; re-exec happens after window closes. |
| Web UI pump loop runs in background thread only in window mode | Browser mode unchanged. Threading model is well-tested. |
| No native OS installer means launch still requires PowerShell/terminal | Acceptable for daily dev use. Packaging deferred to post-acceptance. |

---

## 6. Manual Acceptance Checklist

Use this checklist when Baris does the final acceptance run:

### 6.1 Desktop Window Smoke
```powershell
cd C:\Projects\Piper\web_ui\frontend
npm run build
cd C:\Projects\Piper
$env:PIPER_WEB_UI_ENABLED = "true"
$env:PIPER_WEB_UI_WINDOW = "true"
python app.py
```

- [ ] "Piper" window opens within 5 seconds
- [ ] No address bar visible
- [ ] Status badge shows "connected"
- [ ] Boot logs appear in Activity & Logs panel
- [ ] `boot.ready` appears, status shows "IDLE"

### 6.2 Chat Test
- [ ] Type "hello" and send
- [ ] Response streams character-by-character
- [ ] Exactly one assistant bubble appears (no double reply)
- [ ] No `[ROUTER]` or `[RECALL:...]` visible in chat
- [ ] Stop button interrupts generation
- [ ] New Session clears chat
- [ ] Restart exits cleanly (no orphan processes)

### 6.3 Native MIC Test
- [ ] Click MIC button
- [ ] Backend logs "Recording from device..."
- [ ] UI changes to STOP / "Listening..."
- [ ] Speak a short phrase
- [ ] Click STOP
- [ ] UI changes to "Transcribing..."
- [ ] Transcript appears once in chat
- [ ] Assistant replies once
- [ ] Voice identity recognizes Baris (or asks to enroll if new)

### 6.4 Panel Sanity Check
- [ ] Code Session panel shows status
- [ ] Documents panel accepts path input
- [ ] Image/Vision panel displays generated images (if any)
- [ ] System/Identity panel shows stats
- [ ] Raw Events inspector shows backend frames

### 6.5 Cleanup
- [ ] Close desktop window
- [ ] Backend exits without traceback
- [ ] No lingering `python.exe` or `llama-server.exe` processes

### 6.6 Fallback Verification
- [ ] Unset `PIPER_WEB_UI_ENABLED`
- [ ] `python app.py` opens DearPyGui as before
- [ ] DearPyGui chat, MIC, and panels work normally

---

## 7. Default-Readiness Criteria

Before `WEB_UI_ENABLED` can default to `true`, the following must be met:

1. **Daily-use burn-in:** Baris uses Web UI (window or browser mode) as primary UI for at least 1 week.
2. **No critical regressions:** Chat, MIC, New Session, Restart, and voice identity behave reliably.
3. **DearPyGui fallback preserved:** `PIPER_WEB_UI_ENABLED=false` must always work.
4. **Clean startup/shutdown:** No hangs, orphan processes, or traceback on exit.
5. **Manual checklist passes:** Every item in Section 6 passes end-to-end.
6. **Docs updated:** Migration guide and acceptance checkpoint reflect final state.
7. **Rollback instructions tested:** All three launch modes (browser, window, DPG) verified working.

---

## 8. DearPyGui Retirement Criteria

DearPyGui is **not** retired. It remains the default until:

1. All default-readiness criteria are met.
2. A Windows desktop wrapper or installer is available (Phase 14+).
3. Baris explicitly approves the switch.
4. Rollback to DearPyGui is documented and tested.

Even after retirement, DearPyGui code stays in `ui/` as an emergency fallback.

---

## 9. Rollback / Fallback Instructions

### To Browser Web UI
```powershell
$env:PIPER_WEB_UI_ENABLED = "true"
# PIPER_WEB_UI_WINDOW unset or false
python app.py
```

### To Desktop Web UI
```powershell
$env:PIPER_WEB_UI_ENABLED = "true"
$env:PIPER_WEB_UI_WINDOW = "true"
python app.py
```

### To DearPyGui
```powershell
Remove-Item Env:\PIPER_WEB_UI_ENABLED -ErrorAction SilentlyContinue
Remove-Item Env:\PIPER_WEB_UI_WINDOW -ErrorAction SilentlyContinue
python app.py
```

---

## 10. Source of Truth

| Document | Role |
|---|---|
| This checkpoint | Acceptance state, criteria, checklist |
| `docs/specs/piper-web-ui-migration-guide.md` | Phase history, architecture narrative |
| `web_ui/bridge/CONTRACT.md` | Event/action frame contracts |
| `AGENTS.md` | Repository doctrine and architectural boundaries |

---

*Checkpoint created: Phase 15D — 2026-05-15*
