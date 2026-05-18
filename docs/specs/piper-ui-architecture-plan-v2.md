# Piper UI Architecture & Migration Plan
## v2.1 — Corrected (post-GPT review)

**Date:** 2026-05-14  
**Status:** Approved with corrections. Ready for Phase 0.  
**Base branch:** `feature/web-ui-bridge`, forked from `fix/guest-voice-name-disambiguation`

---

## Summary of Corrections from v2.0

| # | Issue | v2.0 (wrong) | v2.1 (corrected) |
|---|-------|-------------|-----------------|
| 1 | **Port collision** | `8765` | `8787` (8765 is TTS server) |
| 2 | **Queue consumer model** | "Both UIs can coexist reading same ui_queue" | "Alternate consumers, single-consumer queue. Fanout layer is future work if needed." |
| 3 | **Phase ordering** | Start with app.py branch | Start with adapter-only + smoke tests. app.py wiring comes last. |

---

## 1. The Three Corrections (Detailed)

### 1.1 Port 8787, not 8765

Port `8765` is already used by Piper's local TTS server (Kokoro ONNX runtime). Collision would cause silent failures that eat evenings.

```
PIPER_WEB_UI_PORT      = 8787   # HTTP + WebSocket
PIPER_WEB_UI_WS_PATH   = /ws   # ws://127.0.0.1:8787/ws
VITE_DEV_PORT          = 5173  # Development only (separate)
```

### 1.2 queue.Queue is Single-Consumer

A `queue.Queue.get()` **removes** the item. Two consumers racing on the same queue will steal messages from each other. The plan now uses an **alternate-consumer** model:

```
Mode A (PIPER_WEB_UI_ENABLED=false):
    backend ──► ui_queue ──► DearPyGui pump_ui_queue()

Mode B (PIPER_WEB_UI_ENABLED=true):
    backend ──► ui_queue ──► BridgeServer ──► WebSocket ──► React UI
```

**Not both simultaneously** in Phase 1. If dual-UI is needed later, add a `UiEventBus` fanout:

```
                    ┌──► DearPyGui queue
backend ──► UiEventBus ──┤
                    └──► Web bridge queue
```

This is deferred. The config flag selects one path at startup.

### 1.3 Boundaries First, Behavior Second

Implementation order (corrected):

1. **Phase 0** (read-only): Document the full UI event/action contract
2. **Phase 1** (adapter only): `web_ui/bridge/adapter.py` + deterministic smoke tests
3. **Phase 2** (bridge server): `web_ui/bridge/server.py` — no app.py changes
4. **Phase 3** (wiring): `app.py` + `controller.py` branch — last, not first
5. **Phase 4+** (React shell, live connection, controls — unchanged from v2.0)

---

## 2. Branch Strategy

```
feature/web-ui-bridge
    ↑
fix/guest-voice-name-disambiguation (commit 1414316)
    ↑
stabilize/voice-identity
    ↑
main
```

**Why base on the voice fix branch:**
- The UI migration must not reintroduce raw `[UI]` / `system:` leakage into chat
- Issue #13 (Ekin/Akin name disambiguation) is UI-facing — the new UI should inherit that fix
- Guest voice enrollment flows need correct name handling from day one

**Merge policy:**
- Do NOT merge to `main` until after PR #12 (SearchTopicResolver) lands and PR #4 (voice threshold) is properly tested
- The web UI branch is independent — it can be developed in parallel

---

## 3. Recommended Architecture (Corrected)

```
┌─────────────────────────────────────────────────────────────────┐
│                      PIPER v2.0 ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Config: PIPER_WEB_UI_ENABLED = true                            │
│                                                                 │
│  ┌─────────────┐      WebSocket       ┌─────────────────────┐  │
│  │  React UI   │ ◄──────────────────► │   Python Bridge     │  │
│  │  (Vite)     │   ws://127.0.0.1:   │   (websockets lib)  │  │
│  │             │      8787/ws        │                     │  │
│  │  • Chat     │                     │  • Adapts ui_queue  │  │
│  │  • Avatar   │                     │    tuples to JSON   │  │
│  │  • Status   │                     │  • Serves static    │  │
│  │  • System   │                     │    build files      │  │
│  │  • Controls │                     │  • Receives actions │  │
│  └─────────────┘                     └──────────┬──────────┘  │
│                                                 │              │
│                        ui_queue (single-consumer)              │
│                                                 │              │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              PIPER BACKEND (unchanged)                  │  │
│  │  core/orchestrator.py  →  ui_queue.put((kind, payload)) │  │
│  │  core/pipeline.py      →  ChatPipeline                  │  │
│  │  memory/chat_state.py  →  message store                 │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ─────────────────────────────────────────────────────────────  │
│  Fallback (PIPER_WEB_UI_ENABLED = false):                       │
│                                                                 │
│  ┌──────────────┐                                               │
│  │ DearPyGui UI │◄── ui_queue (same queue, alternate consumer) │
│  │ (existing)   │                                               │
│  └──────────────┘                                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Folder Structure (Corrected)

```
Piper/
├── app.py                          # Entry point — wiring in Phase 3, NOT Phase 1
├── config.py                       # + PIPER_WEB_UI_ENABLED, PIPER_WEB_UI_PORT=8787
├── requirements.txt                # + websockets
│
├── ui/                             # EXISTING — DearPyGui UI (untouched until retirement)
│   ├── controller.py               # ← Phase 3: add run_web() method only
│   ├── layout.py
│   ├── controller_queue.py
│   ├── controller_actions.py
│   ├── controller_status.py
│   ├── controller_render.py
│   ├── event_speech.py
│   ├── vision_commentary.py
│   ├── commands.py
│   └── windowing.py
│
├── web_ui/                         # NEW — all phases below
│   ├── __init__.py
│   │
│   ├── bridge/                     # Phase 1-2: Python bridge (backend side)
│   │   ├── __init__.py
│   │   ├── adapter.py              # Phase 1: tuple-to-JSON + validation ONLY
│   │   ├── message_schema.py       # Phase 1: TypedDict/event contracts
│   │   ├── server.py               # Phase 2: WebSocket + HTTP server
│   │   └── test_adapter.py         # Phase 1: deterministic smoke tests
│   │
│   └── frontend/                   # Phase 4+: React application (frontend side)
│       ├── package.json
│       ├── vite.config.ts
│       ├── tsconfig.json
│       ├── index.html
│       │
│       ├── src/
│       │   ├── main.tsx
│       │   ├── App.tsx
│       │   ├── index.css
│       │   ├── types/
│       │   │   └── events.ts
│       │   ├── hooks/
│       │   │   ├── useWebSocket.ts
│       │   │   ├── useChat.ts
│       │   │   ├── useStatus.ts
│       │   │   └── useVoice.ts
│       │   ├── components/
│       │   │   ├── layout/
│       │   │   │   ├── TopBar.tsx
│       │   │   │   ├── BottomBar.tsx
│       │   │   │   └── CockpitShell.tsx
│       │   │   ├── chat/
│       │   │   │   ├── ChatPanel.tsx
│       │   │   │   ├── MessageBubble.tsx
│       │   │   │   ├── ChatInput.tsx
│       │   │   │   └── TypingIndicator.tsx
│       │   │   ├── avatar/
│       │   │   │   ├── AvatarCard.tsx
│       │   │   │   ├── ModeSelector.tsx
│       │   │   │   └── VoiceBar.tsx
│       │   │   ├── system/
│       │   │   │   ├── SystemPanel.tsx
│       │   │   │   ├── MemoryCard.tsx
│       │   │   │   ├── QuickActions.tsx
│       │   │   │   ├── SystemOverview.tsx
│       │   │   │   └── LogPanel.tsx
│       │   │   └── controls/
│       │   │       ├── WakeSleepButton.tsx
│       │   │       ├── StopButton.tsx
│       │   │       ├── CopyButton.tsx
│       │   │       └── SettingsButton.tsx
│       │   └── services/
│       │       └── wsClient.ts
│       │
│       └── public/
│           └── avatar/
│               ├── secretary.webp
│               ├── scientist.webp
│               ├── analyst.webp      # Default
│               └── casual.webp
│
├── core/                           # EXISTING — ZERO CHANGES
├── memory/                         # EXISTING — ZERO CHANGES
├── llm/                            # EXISTING — ZERO CHANGES
├── tools/                          # EXISTING — ZERO CHANGES
├── docs/
│   ├── AGENTS.md                   # Phase 0: read for doctrine
│   ├── DOCUMENTS_MAP.md            # Phase 0: read for file guide
│   ├── WIP.md                      # Phase 0: read for active work
│   ├── architecture/
│   │   └── TRIGGER_FLOW.md         # Phase 0: read for event flow
├── notes/
│   └── debug-protocol.md           # Phase 0: read for debug conventions
└── tests/
    └── web_ui/                     # Phase 1: adapter smoke tests
```

---

## 5. Corrected Migration Phases

### Phase 0: Read-Only Contract Map (No Code Changes)

**Goal:** Document every event kind, payload shape, visibility rule, and action callback. Produce the contract map that all subsequent phases depend on.

**Files to read (in order):**

| Order | File | What to extract |
|-------|------|-----------------|
| 1 | `AGENTS.md` | Piper doctrine, boundary rules, agent workflow |
| 2 | `docs/DOCUMENTS_MAP.md` | File organization guide |
| 3 | `docs/WIP.md` | Active work in progress |
| 4 | `docs/architecture/TRIGGER_FLOW.md` | Event trigger flow diagram |
| 5 | `notes/debug-protocol.md` | Debug logging conventions |
| 6 | `ui/controller_queue.py` | **ALL event kinds** — the complete ui_queue catalog |
| 7 | `ui/controller.py` | PiperController API surface, state fields, lifecycle |
| 8 | `ui/layout.py` | All DPG tags, visual zones, callback map |
| 9 | `ui/controller_actions.py` | All action handlers — maps 1:1 to incoming actions |
| 10 | `ui/controller_status.py` | Mode classification, color mapping, top_bar refresh |
| 11 | `ui/controller_render.py` | Message formatting, chat filtering, log line limiting |
| 12 | `app.py` | Entry point, dependency wiring, boot sequence |
| 13 | `config.py` | Config flags (add WEB_UI_ENABLED, WEB_UI_PORT) |
| 14 | `core/contracts.py` | OrchestratorConfig — the `ui` field is the queue |

**Deliverable:** A contract document containing:

```
1. EVENT_KINDS[] — every ui_queue event kind:
   - kind: string
   - payload_shape: observed Python type
   - visibility: "chat" | "log" | "status" | "internal" | "multi"
   - source_file: string
   - line_number: int
   - notes: string

2. ACTIONS[] — every user-facing action:
   - action_name: string
   - controller_method: string
   - source_file: string
   - payload_schema: {field: type}
   - notes: string

3. WEBSOCKET_SCHEMA — proposed JSON frame format per event

4. RISKS[] — places where code assumes DearPyGui tags directly
```

**Phase 0 output file:** `web_ui/bridge/CONTRACT.md`

---

### Phase 1: Adapter + Schema + Smoke Tests (No Server, No app.py)

**Goal:** Build the translation layer with deterministic tests. No WebSocket, no HTTP, no `app.py` changes.

**New files:**
- `web_ui/__init__.py`
- `web_ui/bridge/__init__.py`
- `web_ui/bridge/message_schema.py` — TypedDict for every event kind
- `web_ui/bridge/adapter.py` — `tuple_to_json()` and `json_to_action()`
- `web_ui/bridge/test_adapter.py` — pytest smoke tests

**adapter.py interface:**

```python
"""web_ui/bridge/adapter.py

Pure translation layer. No I/O, no WebSocket, no framework.
Deterministic: same input always produces same output.
"""

from typing import Any
import json

# Outgoing: backend tuple -> JSON frame for frontend
def ui_tuple_to_ws_frame(kind: str, payload: Any) -> str:
    """Convert a ui_queue (kind, payload) tuple to a JSON string.
    
    Raises ValueError if kind is unknown (forces explicit handling).
    """
    ...

# Incoming: frontend action -> controller method call
def parse_action_frame(raw_json: str) -> tuple[str, dict[str, Any]]:
    """Parse a WebSocket action message from frontend.
    
    Returns (action_name, payload_dict).
    Raises ValueError on invalid JSON or unknown action.
    """
    ...

# Validation
def is_known_event_kind(kind: str) -> bool:
    """Return True if this event kind has a defined schema."""
    ...

def get_event_schema(kind: str) -> dict[str, Any]:
    """Return the JSON schema for an event kind."""
    ...
```

**test_adapter.py requirements:**

```python
"""web_ui/bridge/test_adapter.py

Deterministic smoke tests. Run with: python -m pytest web_ui/bridge/test_adapter.py
"""

# Every event kind from controller_queue.py gets a test:

def test_assistant_stream_delta():
    frame = ui_tuple_to_ws_frame("assistant_stream_delta", {"text": "hello"})
    parsed = json.loads(frame)
    assert parsed["event"] == "assistant_delta"      # renamed for frontend
    assert parsed["payload"]["text"] == "hello"
    assert "timestamp" in parsed

def test_status_widget_mode():
    frame = ui_tuple_to_ws_frame("status_widget_mode", "THINKING")
    parsed = json.loads(frame)
    assert parsed["event"] == "status_update"
    assert parsed["payload"]["mode"] == "THINKING"

def test_unknown_event_raises():
    with pytest.raises(ValueError):
        ui_tuple_to_ws_frame("totally_unknown_event", "data")

# Action parsing tests:

def test_parse_send_message():
    action, payload = parse_action_frame('{"action":"send_message","payload":{"text":"hi"}}')
    assert action == "send_message"
    assert payload["text"] == "hi"
```

**Phase 1 commit:** `feat(web-ui): add bridge adapter with smoke tests`

**Verification:**
```bash
python -m pytest web_ui/bridge/test_adapter.py -v
# All tests pass before any server or UI code exists
```

---

### Phase 2: Bridge Server (No app.py Changes)

**Goal:** Add the WebSocket + HTTP server that uses the adapter from Phase 1. Still no `app.py` changes — server is tested standalone with mock queue.

**New file:** `web_ui/bridge/server.py`

```python
"""web_ui/bridge/server.py

WebSocket + HTTP file server. Runs on localhost:8787.

Usage (standalone test):
    from queue import Queue
    from web_ui.bridge.server import BridgeServer
    
    mock_queue = Queue()
    server = BridgeServer(mock_queue, host="127.0.0.1", port=8787, static_dir="./web_ui/frontend/dist")
    server.start()  # Background thread
    
    # Inject test events:
    mock_queue.put(("status", "IDLE"))
    mock_queue.put(("assistant_stream_delta", {"text": "Hello"}))

Dependencies:
    - websockets (pip install websockets)
    - aiohttp (optional, for static file serving — can use stdlib http.server)
"""
```

**Key design points:**
- Uses `websockets` library (pure Python, single dependency)
- HTTP static file serving via `aiohttp` or `http.server` from stdlib
- Runs in a **daemon thread** — crash does not take down Piper
- Reads from `ui_queue` via `get_nowait()` in a loop, forwards to all connected WS clients
- Receives action messages from WS clients, puts them on an `action_queue` for the controller
- Graceful shutdown on SIGTERM

**Phase 2 commit:** `feat(web-ui): add bridge WebSocket/HTTP server`

**Verification:**
```bash
# Terminal 1: start server with mock queue
python -c "
from queue import Queue
from web_ui.bridge.server import BridgeServer
q = Queue()
srv = BridgeServer(q, port=8787)
srv.start()
import time; time.sleep(60)
"

# Terminal 2: connect with websocat
websocat ws://127.0.0.1:8787/ws

# Terminal 3: inject events
python -c "
import websocket, json
ws = websocket.create_connection('ws://127.0.0.1:8787/ws')
ws.send(json.dumps({'action': 'test_ping'}))
print(ws.recv())
"
```

---

### Phase 3: Wire app.py + controller.py (Bridge Integration)

**Goal:** Connect the bridge to the actual Piper runtime. This is the first time existing code changes.

**Changes to existing files (minimal, conservative):**

**`config.py`** — add two fields:
```python
WEB_UI_ENABLED: bool = _env_flag("PIPER_WEB_UI_ENABLED", False)
WEB_UI_PORT: int = int(os.environ.get("PIPER_WEB_UI_PORT", "8787"))
```

**`ui/controller.py`** — add one method:
```python
def run_web(self) -> int:
    """Run without DearPyGui — queue pumping only for WebSocket bridge.
    
    Does everything run() does EXCEPT:
    - No build_ui() call
    - No dpg.render_dearpygui_frame() loop
    - Instead: pump_ui_queue() in a tight loop
    """
    CFG.DATA_DIR.mkdir(parents=True, exist_ok=True)
    self.agent_brain.cleanup_old_events()
    self.load_memory_into_chat()
    self.knowledge_mgr.set_logger(self.safe_log)
    self.refresh_active_user_meta()
    self.proactive_monitor.start()
    self._boot_ui_min_visible_until = time.perf_counter() + float(
        getattr(CFG, "BOOT_SCREEN_MIN_VISIBLE_S", 0.75)
    )
    self._pending_boot_ready = False
    self._pending_boot_ready_payload = ""
    
    boot_thread = threading.Thread(target=self.boot_mgr.run_sequence, daemon=True)
    boot_thread.start()
    
    try:
        while not self.restart_requested:
            self.pump_ui_queue()
            tts_busy = self.is_tts_active()
            if tts_busy != self._last_tts_busy:
                self.refresh_interaction_state()
            time.sleep(0.016)  # ~60fps pump rate
    finally:
        self.proactive_monitor.stop()
        self.agent_brain.shutdown()
        self.code_session.shutdown()
    return RESTART_EXIT_CODE if self.restart_requested else 0
```

**`app.py`** — add branch at entry point:
```python
# At the end of app.py, replace the single controller.run() call with:

if CFG.WEB_UI_ENABLED:
    from web_ui.bridge.server import BridgeServer
    from web_ui.bridge.adapter import ui_tuple_to_ws_frame
    
    bridge = BridgeServer(
        ui_queue=controller.ui_queue,
        host="127.0.0.1",
        port=CFG.WEB_UI_PORT,
        static_dir=str(CFG.ROOT_DIR / "web_ui" / "frontend" / "dist"),
    )
    bridge.start()
    exit_code = controller.run_web()
    bridge.stop()
else:
    exit_code = controller.run()  # Existing DearPyGui path — UNCHANGED

sys.exit(exit_code)
```

**Phase 3 commit:** `feat(web-ui): wire bridge into app.py and controller`

**Verification:**
```bash
# Start with Web UI
PIPER_WEB_UI_ENABLED=1 python app.py

# Open browser
# http://127.0.0.1:8787 should serve the static frontend
# ws://127.0.0.1:8787/ws should accept WebSocket connections

# Verify DearPyGui still works
PIPER_WEB_UI_ENABLED=0 python app.py  # or just omit the env var
```

---

### Phase 4: React Shell with Mock Data

Same as v2.0 Phase 2. Scaffold the React application, build layout components with mock data. No live connection yet.

---

### Phase 5: Connect Live Stream

Same as v2.0 Phase 3. Wire WebSocket hook, connect events to state, streaming chat.

---

### Phase 6: Add Controls

Same as v2.0 Phase 4. Wire all buttons to send actions to backend.

---

### Phase 7: Validate + Retire DearPyGui

Same as v2.0 Phase 5. Parity checklist, then flip default.

---

## 6. Event Schema (Unchanged from v2.0)

See Section 6 of v2.0 plan. The schema itself was correct — the corrections are about:
- Port number (8787 not 8765)
- Consumer model (alternate, not simultaneous)
- Implementation order (adapter first, not app.py)

The 10 typed events and action mappings remain valid:
- `assistant_delta`
- `assistant_final`
- `user_message`
- `status_update`
- `log_event`
- `tool_event`
- `search_result`
- `listening_state`
- `tts_state`
- `auth_state`

---

## 7. Risks and Mitigation (Corrected)

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Port collision (8765)** | High (certain) | High | **Fixed:** use 8787. Verified against all known Piper services. |
| **Queue multi-consumer race** | High (if attempted) | High | **Fixed:** alternate-consumer model. Only one UI reads from ui_queue at a time. |
| **WebSocket connection unreliable** | Low | High | Auto-reconnect with exponential backoff. Frontend shows "Reconnecting...". |
| **Bridge crash takes down Piper** | Low | High | Bridge runs in daemon thread. `try/except` around all bridge calls in app.py. |
| **Frontend perf on long chats** | Medium | Medium | Virtualize chat list. Well-solved React pattern. |
| **Backend events leak into chat** | Medium | High | Existing `renderable_chat_messages()` filter. Frontend applies same filtering. |
| **DearPyGui + Web UI conflict** | None (alternate) | None | **Fixed:** they never run simultaneously. Config selects at startup. |
| **Two UI codebases to maintain** | High | Medium | Temporary. DearPyGui retired after parity. Same queue means behavior changes work for both. |

---

## 8. Corrected Implementation Order Summary

```
WEEK 1 — Phase 0: Contract Map (READ-ONLY)
├── Read all 14 files listed in Phase 0
├── Document every event kind, payload shape, visibility rule
├── Document every action/callback → controller method mapping
├── Identify DPG tag assumptions (risks)
└── Deliver: web_ui/bridge/CONTRACT.md

WEEK 1-2 — Phase 1: Adapter + Smoke Tests
├── Write message_schema.py (TypedDict for all events)
├── Write adapter.py (tuple_to_json + parse_action)
├── Write test_adapter.py (pytest, one test per event kind)
├── Verify: python -m pytest web_ui/bridge/test_adapter.py -v
└── Commit: adapter only, no server, no app.py changes

WEEK 2 — Phase 2: Bridge Server
├── Write server.py (WebSocket + HTTP, port 8787)
├── Test standalone with mock queue + websocat
├── Verify: ws://127.0.0.1:8787/ws accepts connections
└── Commit: server only, still no app.py changes

WEEK 2 — Phase 3: Wire app.py + controller
├── Add WEB_UI_ENABLED + WEB_UI_PORT to config.py
├── Add run_web() method to controller.py
├── Add branch in app.py
├── Test: PIPER_WEB_UI_ENABLED=1 python app.py
├── Verify: DearPyGui still works with =0
└── Commit: wiring

WEEK 3 — Phase 4: React Shell
├── Scaffold Vite + React + TypeScript
├── Build layout components (TopBar, ChatPanel, AvatarCard, SystemPanel, BottomBar)
├── CSS design system (dark cinematic)
├── Mock data for all components
└── Verify: npm run dev shows full layout

WEEK 3-4 — Phase 5: Live Connection
├── Wire useWebSocket hook (connects to ws://127.0.0.1:8787/ws)
├── Event dispatching to state hooks
├── Streaming chat with assistant_delta
├── Status bar + log panel live updates
└── Verify: real Piper backend drives the UI

WEEK 4 — Phase 6: Controls
├── All action buttons wired to backend
├── Keyboard shortcuts (Enter, Escape)
├── Error toasts, loading states
└── Verify: every control works

WEEK 5 — Phase 7: Validate + Retire
├── Daily use, parity checklist
├── Flip WEB_UI_ENABLED default to True
├── Document PIPER_WEB_UI_ENABLED=0 for legacy fallback
└── Archive DearPyGui retirement plan
```

---

## 9. The Next Move

**Do not start implementation yet.**

The next action is the **Phase 0 prompt**: read the 14 files and produce the UI event/action contract map. This is a read-only exercise — zero code changes.

Files for Phase 0 (in priority order):
1. `AGENTS.md`
2. `docs/DOCUMENTS_MAP.md`
3. `docs/WIP.md`
4. `docs/architecture/TRIGGER_FLOW.md`
5. `notes/debug-protocol.md`
6. `ui/controller_queue.py`
7. `ui/controller.py`
8. `ui/layout.py`
9. `ui/controller_actions.py`
10. `ui/controller_status.py`
11. `ui/controller_render.py`
12. `app.py`
13. `config.py`
14. `core/contracts.py`

**Base branch:** `feature/web-ui-bridge` ← `fix/guest-voice-name-disambiguation` (commit 1414316)

**Deliverable:** `web_ui/bridge/CONTRACT.md`

---

## 10. Appendix: Port Allocation Reference

| Port | Service | Status |
|------|---------|--------|
| 8080 | llama.cpp server (LLM) | Fixed |
| 8765 | TTS server (Kokoro ONNX) | **RESERVED — do not use** |
| 8787 | **Piper Web UI** | **New allocation** |
| 5173 | Vite dev server (development only) | Standard |
