# Piper UI Architecture & Migration Plan
## v2.0 — Modern Cockroachpit Interface

**Date:** 2026-05-14  
**Context:** Piper is a local/offline-first Python assistant with a Route → Plan → Act → Speak architecture. The current DearPyGui UI works but is visually limited. This plan proposes a modern UI inspired by the PIPER FACE reference image while preserving all backend behavior and keeping the old UI as a fallback until parity is proven.

---

## 1. Reference Image Analysis

The PIPER FACE reference shows a **dark cinematic assistant dashboard** with these zones:

| Zone | Description |
|------|-------------|
| **Top Bar** | "PIPER FACE" branding + logo, tab navigation (Chat, Memory, Tasks, System), WAKE/SLEEP pill buttons, settings icon, window controls |
| **Left Panel** | Conversation stream with user/Piper chat bubbles, timestamps, circular avatar for Piper messages |
| **Center** | Large cinematic Piper avatar/portrait — a professional figure at a dark desk with monitors. This is the "presence" area |
| **Center-Bottom** | Horizontal mode selector: Secretary / Scientist / Analyst / Engineer — Analyst is active |
| **Bottom Bar** | Voice control bar — "Voice" toggle, "Listening..." text, "Default Voice" dropdown, large mic button with animated waveform visualization |
| **Right Panel** | Three stacked cards: Memory (searchable list), Quick Actions (grid buttons), System Overview (CPU/Storage/Memory/Battery gauges) |
| **Footer Bar** | Model info (Piper v7 Local), Context (128k tokens), Privacy (100% Local), Local Time, Uptime |

**Key Design Principles from the Reference:**
- Dark, cinematic, not "I'm offline and broken" — status feels alive
- Piper has a **presence** (avatar portrait) — not just a chatbot
- System state is visible but **elegant** — gauges, not walls of text
- Log is secondary/collapsible — not front-and-center
- Mode selector is prominent — Piper adapts her persona
- Blue accent colors on near-black backgrounds
- Clean rounded panels, generous spacing

---

## 2. Frontend Stack Evaluation

### Option 1: Keep DearPyGui and Restyle

| Dimension | Assessment |
|-----------|-----------|
| **Visual Ceiling** | Medium. DearPyGui supports custom themes, but avatar display is static-image-only, rounded panels are limited, and CSS-grade styling is impossible. The cinematic look of the reference is not achievable. |
| **Implementation Difficulty** | Low. Already works. Just theme changes in `layout.py`. |
| **Impact on Backend** | None. Same code, different colors. |
| **Performance/GPU** | Low CPU overhead, no GPU required. |
| **Risk** | Very low. But also very limited — we'd still look like a 2005 IM client. |
| **Verdict** | **Short-term band-aid only.** Do this if we need a quick win, but don't expect the reference look. |

### Option 2: PySide6 / Qt / QML

| Dimension | Assessment |
|-----------|-----------|
| **Visual Ceiling** | High. QML can achieve almost any look. Custom shaders, animations, native performance. |
| **Implementation Difficulty** | High. Requires learning QML, rewriting all UI code, building a Qt bridge. The team is Python-heavy; QML is a new DSL. |
| **Impact on Backend** | Medium. New signal/slot bridge to replace `ui_queue`. Qt event loop integration. |
| **Performance/GPU** | Low GPU for QML (accelerated), medium CPU for widgets. |
| **Risk** | High. Qt deployment on Windows is heavy (100MB+). Licensing considerations. Steep learning curve. The team would be maintaining two entirely different UI paradigms (DearPyGui + Qt) during transition. |
| **Verdict** | **Overkill for this project.** The visual gains over a web UI don't justify the complexity. |

### Option 3: Local Web UI (React + Vite) + Python WebSocket Bridge

| Dimension | Assessment |
|-----------|-----------|
| **Visual Ceiling** | Very High. CSS, animations, canvas/WebGL for waveforms, component ecosystem. The reference image is easily achievable. |
| **Implementation Difficulty** | Medium. React is well-known. The bridge (Python `websockets` library) is ~100 lines. Vite builds a static bundle that Python serves locally. |
| **Impact on Backend** | **Low.** The existing `ui_queue` tuple system maps 1:1 to WebSocket messages. Just serialize and send. The backend changes are minimal — add a bridge module, don't touch the orchestrator. |
| **Performance/GPU** | Minimal GPU (browser compositing). Runs in any modern browser. Lightweight for v1 — no WebGL required. |
| **Risk** | Low-Medium. Requires a browser (Edge/Chrome is preinstalled on Windows). The bridge is the only new moving part. If the browser fails, the DearPyGui fallback still works. |
| **Verdict** | **Recommended.** Best visual ceiling for the effort. The bridge pattern is clean. The existing architecture already supports this — the `ui_queue` is practically a message bus. |

### Option 4: Tauri Wrapper Around Local Web UI

| Dimension | Assessment |
|-----------|-----------|
| **Visual Ceiling** | Very High. Same as Option 3 since it's the same web UI, but with a native window chrome. |
| **Implementation Difficulty** | Medium-High. Tauri requires Rust toolchain. Adds build complexity. The value is a standalone `.exe` without a visible browser window. |
| **Impact on Backend** | Same as Option 3 — WebSocket bridge. |
| **Performance/GPU** | Tiny Rust binary (~600KB), minimal RAM. Better than running a full browser. |
| **Risk** | Medium. Rust toolchain is a new dependency. Tauri v2 is still maturing on Windows. If Tauri has issues, we fall back to "just open the browser." |
| **Verdict** | **Phase 2, not Phase 1.** Build the web UI first under Option 3. Wrap in Tauri later when the UI is stable. This de-risks the migration. |

### Summary

| Option | Visual Ceiling | Effort | Risk | Recommended |
|--------|---------------|--------|------|-------------|
| 1. DearPyGui restyle | Medium | Low | Very Low | Band-aid only |
| 2. PySide6/QML | Very High | Very High | High | No |
| **3. React + Vite + WebSocket** | **Very High** | **Medium** | **Low** | **Yes — start here** |
| 4. Tauri wrapper | Very High | Medium-High | Medium | Yes — after Option 3 |

---

## 3. Recommended Architecture

### 3.1 Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      PIPER v2.0 ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐      WebSocket       ┌─────────────────────┐  │
│  │  React UI   │ ◄──────────────────► │   Python Bridge     │  │
│  │  (Vite)     │   ws://localhost:   │   (websockets lib)  │  │
│  │             │      8765 (default) │                     │  │
│  │  • Chat     │                     │  • Adapts ui_queue  │  │
│  │  • Avatar   │                     │    tuples to JSON   │  │
│  │  • Status   │                     │  • Serves static    │  │
│  │  • System   │                     │    build files      │  │
│  │  • Controls │                     │  • Receives actions │  │
│  └─────────────┘                     └──────────┬──────────┘  │
│       ▲                                         │              │
│       │         (fallback)                      │              │
│       │         ┌──────────────┐               │              │
│       └─────────┤ DearPyGui UI │◄──────────────┘              │
│                 │ (existing)   │   same ui_queue              │
│                 └──────────────┘                              │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │                    PIPER BACKEND (unchanged)            │  │
│  │  core/orchestrator.py  →  ui_queue.put((kind, payload)) │  │
│  │  core/pipeline.py      →  ChatPipeline                  │  │
│  │  memory/chat_state.py  →  message store                 │  │
│  │  ...                                                     │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                               │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Key Design Decisions

1. **Dual UI Support**: Both DearPyGui and Web UI run off the same `ui_queue`. A config flag `PIPER_WEB_UI_ENABLED` selects which one starts. Both can coexist — the bridge just reads from `ui_queue` and forwards to WebSocket clients.

2. **Bridge is a thin adapter**: The bridge doesn't transform event semantics. It serializes `(kind, payload)` tuples as JSON WebSocket frames: `{"event": "assistant_stream_delta", "payload": {"text": "hello"}}`.

3. **Frontend is a dumb display**: No business logic in the frontend. It receives events and renders them. User actions (send, stop, mic toggle) are sent as action messages to the bridge, which calls existing controller methods.

4. **Static file serving**: The Vite-built frontend is served by Python's built-in `http.server` or `aiohttp`. No nginx, no external server required.

5. **No GPU requirement for v1**: Avatar is a static image. Waveform is CSS/Canvas animation, not WebGL. System gauges are SVG/CSS.

6. **Offline-first**: All assets are bundled. No CDN references. No external fonts — system fonts only or bundled woff2 files.

---

## 4. Folder Structure

```
Piper/
├── app.py                          # Entry point (unchanged behavior)
├── config.py                       # + new PIPER_WEB_UI_ENABLED flag
├── requirements.txt                # + websockets, aiohttp (optional)
│
├── ui/                             # EXISTING — DearPyGui UI (stays)
│   ├── __init__.py
│   ├── controller.py               # ← add web_ui_enabled check
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
├── web_ui/                         # NEW — React frontend + bridge
│   ├── bridge/                     # Python WebSocket bridge
│   │   ├── __init__.py
│   │   ├── server.py               # WebSocket + HTTP file server
│   │   ├── adapter.py              # ui_queue ◄► WebSocket translation
│   │   └── message_schema.py       # TypedDict schemas for validation
│   │
│   └── frontend/                   # React + Vite application
│       ├── package.json
│       ├── vite.config.ts
│       ├── tsconfig.json
│       ├── index.html
│       │
│       ├── src/
│       │   ├── main.tsx            # Entry point
│       │   ├── App.tsx             # Root layout (3-zone cockpit)
│       │   ├── index.css           # Global styles + CSS variables
│       │   │
│       │   ├── types/
│       │   │   └── events.ts       # TypeScript event definitions
│       │   │
│       │   ├── hooks/
│       │   │   ├── useWebSocket.ts # WebSocket connection mgmt
│       │   │   ├── useChat.ts      # Chat message state
│       │   │   ├── useStatus.ts    # Status/system state
│       │   │   └── useVoice.ts     # Voice/mic state
│       │   │
│       │   ├── components/
│       │   │   ├── layout/
│       │   │   │   ├── TopBar.tsx         # PIPER FACE branding + tabs
│       │   │   │   ├── BottomBar.tsx      # Model/context/privacy/footer
│       │   │   │   └── CockpitShell.tsx   # 3-zone layout wrapper
│       │   │   │
│       │   │   ├── chat/
│       │   │   │   ├── ChatPanel.tsx      # Left panel container
│       │   │   │   ├── MessageBubble.tsx  # User/Piper bubbles
│       │   │   │   ├── ChatInput.tsx      # Text input + send
│       │   │   │   └── TypingIndicator.tsx # "Piper is thinking..."
│       │   │   │
│       │   │   ├── avatar/
│       │   │   │   ├── AvatarCard.tsx     # Center portrait card
│       │   │   │   ├── ModeSelector.tsx   # Secretary/Scientist/Analyst/Engineer
│       │   │   │   └── VoiceBar.tsx       # Bottom waveform + mic
│       │   │   │
│       │   │   ├── system/
│       │   │   │   ├── SystemPanel.tsx    # Right panel container
│       │   │   │   ├── MemoryCard.tsx     # Memory search + list
│       │   │   │   ├── QuickActions.tsx   # Grid buttons
│       │   │   │   ├── SystemOverview.tsx # CPU/Storage/Memory/Battery gauges
│       │   │   │   └── LogPanel.tsx       # Collapsible raw logs
│       │   │   │
│       │   │   └── controls/
│       │   │       ├── WakeSleepButton.tsx
│       │   │       ├── StopButton.tsx
│       │   │       ├── CopyButton.tsx
│       │   │       └── SettingsButton.tsx
│       │   │
│       │   └── services/
│       │       └── wsClient.ts      # WebSocket client + reconnect
│       │
│       └── public/
│           ├── avatar/              # Piper avatar images per mode
│           │   ├── secretary.jpg
│           │   ├── scientist.jpg
│           │   ├── analyst.jpg      # Default
│           │   └── casual.jpg
│           └── sounds/              # UI feedback sounds (optional v2+)
│
├── core/                           # EXISTING — no changes
├── memory/                         # EXISTING — no changes
├── llm/                            # EXISTING — no changes
├── tools/                          # EXISTING — no changes
├── data/                           # EXISTING — no changes
└── tests/                          # + web_ui tests
```

---

## 5. Backend/Frontend Bridge

### 5.1 Architecture

The bridge is a **separate Python module** that:
1. Reads from the existing `ui_queue` (same queue DearPyGui uses)
2. Forwards each `(kind, payload)` tuple to all connected WebSocket clients as JSON
3. Receives action messages from the frontend and calls existing controller methods
4. Serves the static Vite build files via HTTP

```
PiperController.ui_queue ──► BridgeServer ──► WebSocket clients
                                      ▲
                                      │
                         Action messages from frontend
```

### 5.2 Bridge Server (`web_ui/bridge/server.py`)

```python
"""WebSocket + HTTP bridge server.

Runs on localhost:8765 by default (configurable via PIPER_WEB_UI_PORT).
Serves static files from web_ui/frontend/dist/.
Forwards ui_queue events to all connected WebSocket clients.
Receives action messages and dispatches to controller callbacks.
"""
```

**Key design points:**
- Uses `websockets` library (pure Python, no extra deps beyond `requirements.txt` addition)
- HTTP file serving uses `aiohttp` OR Python's `http.server` (aiohttp preferred for async unification)
- Runs in a **separate thread** from the main Piper process, started by `app.py` if `PIPER_WEB_UI_ENABLED`
- Graceful shutdown: closes WebSocket, stops HTTP server
- Auto-reconnection: frontend reconnects with exponential backoff

### 5.3 Bridge Adapter (`web_ui/bridge/adapter.py`)

Translates between two worlds:

**Python ► Frontend (outgoing):**
```python
def ui_tuple_to_ws_frame(kind: str, payload: object) -> str:
    """Convert a ui_queue tuple to a JSON WebSocket frame."""
    return json.dumps({"event": kind, "payload": _serialize_payload(payload)})
```

**Frontend ► Python (incoming actions):**
```python
ACTION_HANDLERS = {
    "send_message": controller.submit_user_text,
    "stop": controller.on_stop,
    "new_session": controller.on_new_session,
    "mic_toggle": controller.on_mic_toggle,
    "snapshot": controller.on_snapshot,
    "live_screen_mode_change": controller.on_live_screen_mode_changed,
    "live_screen_interval_change": controller.on_live_screen_interval_changed,
    "event_speech_mode_change": controller.on_event_speech_mode_changed,
    "restart": controller.on_restart,
    "document_picker": controller.on_open_document_picker,
    "code_send": controller.on_code_send,
    "code_run": controller.on_code_run,
    "code_clear": controller.on_code_clear,
}
```

### 5.4 Connection to Existing Code

In `app.py`, instead of:
```python
controller.run()  # Starts DearPyGui loop
```

It becomes:
```python
if CFG.WEB_UI_ENABLED:
    from web_ui.bridge.server import BridgeServer
    bridge = BridgeServer(controller, host="127.0.0.1", port=CFG.WEB_UI_PORT)
    bridge.start()  # Starts in background thread
    controller.run_web()  # No GUI loop, just queue pumping
else:
    controller.run()  # Existing DearPyGui path — unchanged
```

`controller.run_web()` is a new method that does everything `run()` does EXCEPT:
- No `build_ui()` call
- No `dpg.render_dearpygui_frame()` loop
- Instead: `while running: pump_ui_queue(); time.sleep(0.016)`

---

## 6. Event Schema

### 6.1 Outgoing Events (Backend ► Frontend)

These map directly to the existing `ui_queue` tuple kinds used in `controller_queue.py`. Each is serialized as:
```json
{"event": "<kind>", "payload": <serialized_payload>, "timestamp": "2026-05-14T10:43:00Z"}
```

#### `assistant_delta`
```typescript
interface AssistantDeltaEvent {
  event: "assistant_delta";
  payload: {
    text: string;           // Incremental text chunk
    messageId: string;      // Unique ID for this assistant turn
  };
  timestamp: string;
}
```
**Source:** `assistant_stream_delta` in controller_queue.py  
**UI Effect:** Append text to the current Piper message bubble, streaming character-by-character.

#### `assistant_final`
```typescript
interface AssistantFinalEvent {
  event: "assistant_final";
  payload: {
    text: string;           // Complete final text
    messageId: string;      // Same ID as the delta stream
    ttsVoice?: string;      // Voice used for TTS
    ttsSpeed?: number;      // Speed used for TTS
  };
  timestamp: string;
}
```
**Source:** `assistant_stream_end` in controller_queue.py  
**UI Effect:** Mark message as complete, enable copy button, trigger TTS if enabled.

#### `user_message`
```typescript
interface UserMessageEvent {
  event: "user_message";
  payload: {
    role: "user";
    content: string;
    timestamp: string;
  };
}
```
**Source:** `chat_append` in controller_queue.py (filtered for role="user")  
**UI Effect:** Add user message bubble to chat panel.

#### `status_update`
```typescript
interface StatusUpdateEvent {
  event: "status_update";
  payload: {
    mode: string;           // "IDLE" | "ROUTING" | "THINKING" | "GENERATING" | ...
    label: string;          // Human-readable status text
    color: string;          // Hex color for the status indicator
    stageMeta?: string;     // "Stage 1/3 | Step 4" — optional
  };
  timestamp: string;
}
```
**Source:** `status`, `status_widget_mode`, `status_widget_step` in controller_queue.py  
**UI Effect:** Update the status pill in the top bar, update the avatar's "mood" indicator.

#### `log_event`
```typescript
interface LogEvent {
  event: "log_event";
  payload: {
    level: "info" | "warning" | "error";
    source: "agent" | "boot" | "system";
    message: string;
  };
  timestamp: string;
}
```
**Source:** `agent_log`, `boot_log` in controller_queue.py  
**UI Effect:** Append to the collapsible log panel (not the chat). Color-coded by level.

#### `tool_event`
```typescript
interface ToolEvent {
  event: "tool_event";
  payload: {
    tool: string;           // Tool name
    status: "started" | "completed" | "failed";
    detail?: string;        // Human-readable description
  };
  timestamp: string;
}
```
**Source:** Derived from dashboard_activity messages that match tool patterns  
**UI Effect:** Show a brief toast or inline indicator — NOT a chat message. E.g., a small pill that says "Searching..." then disappears.

#### `search_result`
```typescript
interface SearchResultEvent {
  event: "search_result";
  payload: {
    query: string;
    results: Array<{
      title: string;
      url: string;
      snippet: string;
    }>;
    summary?: string;       // Optional synthesized summary
  };
  timestamp: string;
}
```
**Source:** `search_result` in controller_queue.py  
**UI Effect:** Render a collapsible search results card in the chat stream — NOT raw text. User can expand/collapse.

#### `listening_state`
```typescript
interface ListeningStateEvent {
  event: "listening_state";
  payload: {
    state: "idle" | "listening" | "transcribing" | "processing";
    confidence?: number;    // Voice recognition confidence
    userLabel?: string;     // Recognized user name
  };
  timestamp: string;
}
```
**Source:** Mic state changes in controller_actions.py (`on_mic_toggle`)  
**UI Effect:** Animate the voice bar waveform, show "Listening..." text, change mic button state.

#### `tts_state`
```typescript
interface TTSStateEvent {
  event: "tts_state";
  payload: {
    speaking: boolean;
    voice?: string;
    textPreview?: string;   // First 50 chars of what's being spoken
  };
  timestamp: string;
}
```
**Source:** `is_tts_active()` polling in controller.py  
**UI Effect:** Subtle indicator (e.g., avatar lips animate or a "speaking" pulse).

#### `auth_state`
```typescript
interface AuthStateEvent {
  event: "auth_state";
  payload: {
    userLabel: string;
    isKnown: boolean;       // Known user vs. unknown/guest
    isAdmin: boolean;
    enrollmentNeeded?: boolean;
  };
  timestamp: string;
}
```
**Source:** `active_user_changed` in controller_queue.py  
**UI Effect:** Update user display in top bar, show enrollment prompt if needed.

#### Additional Events (from existing queue kinds)

| Event | Source | UI Effect |
|-------|--------|-----------|
| `boot_log` | controller_queue.py | System boot log panel |
| `boot_ready` | controller_queue.py | Hide boot panel, enable controls |
| `ui_controls_refresh` | controller_queue.py | Re-evaluate button enable/disable states |
| `clear_thinking` | controller_queue.py | Remove typing indicator |
| `document_ingest_active` | controller_queue.py | Show/hide document ingestion spinner |
| `live_screen_refresh` | controller_queue.py | Update live screen status |
| `show_image` | controller_queue.py | Display image in chat |
| `code_session_launch` | controller_queue.py | Activate code tab/panel |
| `code_session_output` | controller_queue.py | Append to code console |
| `code_session_status` | controller_queue.py | Update code status text |
| `code_session_active` | controller_queue.py | Enable/disable code controls |
| `documents_view` | controller_queue.py | Update documents list |
| `stats_view_refresh` | controller_queue.py | Refresh statistics charts |
| `vision_snapshot_note` | controller_queue.py | Show vision analysis note |
| `config_reloaded` | controller.py | Show config change toast |

### 6.2 Incoming Actions (Frontend ► Backend)

```typescript
interface ActionMessage {
  action: string;
  payload?: Record<string, unknown>;
  requestId: string;      // For correlation if needed
}
```

| Action | Payload | Handler |
|--------|---------|---------|
| `send_message` | `{ text: string }` | `controller.submit_user_text()` |
| `stop` | `{}` | `controller.on_stop()` |
| `new_session` | `{}` | `controller.on_new_session()` |
| `mic_toggle` | `{}` | `controller.on_mic_toggle()` |
| `snapshot` | `{}` | `controller.on_snapshot()` |
| `live_screen_mode_change` | `{ mode: string }` | `controller.on_live_screen_mode_changed()` |
| `live_screen_interval_change` | `{ interval: string }` | `controller.on_live_screen_interval_changed()` |
| `event_speech_mode_change` | `{ mode: string }` | `controller.on_event_speech_mode_changed()` |
| `restart` | `{}` | `controller.on_restart()` |
| `document_picker` | `{}` | `controller.on_open_document_picker()` |
| `code_send` | `{ text: string }` | `controller.on_code_send()` |
| `code_run` | `{}` | `controller.on_code_run()` |
| `code_clear` | `{}` | `controller.on_code_clear()` |
| `clear_logs` | `{}` | Clear log buffer in bridge |
| `copy_conversation` | `{}` | Serialize chat to clipboard |
| `set_avatar_mode` | `{ mode: "secretary" \| "scientist" \| "analyst" \| "casual" }` | Future: style manager |

---

## 7. Migration Phases

### Phase 0: Read Current UI/Controller Files
**Goal:** Understand every event kind, every callback, every tag.  
**Files to inspect:**
1. `ui/controller.py` — The main controller class, all methods and state
2. `ui/layout.py` — All DPG tags, layout structure, callbacks map
3. `ui/controller_queue.py` — All `ui_queue` event kinds and their handling
4. `ui/controller_actions.py` — All action handlers that the UI calls
5. `ui/controller_status.py` — Status mode classification, color mapping
6. `ui/controller_render.py` — Chat message formatting, text wrapping
7. `core/contracts.py` — OrchestratorConfig (the `ui` field)
8. `app.py` — Entry point, dependency wiring
9. `config.py` — Config flags (add PIPER_WEB_UI_ENABLED)

**Deliverable:** Document every event kind, every tag, every callback. Map them to the new schema.

---

### Phase 1: Create Bridge Without Changing Behavior
**Goal:** Build the WebSocket bridge that reads from `ui_queue` and forwards to WebSocket clients. DearPyGui remains the default UI. No visual changes.

**New files:**
- `web_ui/__init__.py`
- `web_ui/bridge/__init__.py`
- `web_ui/bridge/server.py` — WebSocket + HTTP server
- `web_ui/bridge/adapter.py` — Tuple-to-JSON translation
- `web_ui/bridge/message_schema.py` — TypedDict schemas

**Changes to existing files:**
- `config.py`: Add `WEB_UI_ENABLED: bool = False`, `WEB_UI_PORT: int = 8765`
- `ui/controller.py`: Add `run_web()` method (queue pump loop without DPG)
- `app.py`: Add branch: if `WEB_UI_ENABLED`, start bridge + call `run_web()`

**Verification:**
- Start Piper with `PIPER_WEB_UI_ENABLED=1`
- Connect with a simple WebSocket test client (`websocat` or browser console)
- Verify all `ui_queue` events appear as JSON frames
- Verify DearPyGui still works with `PIPER_WEB_UI_ENABLED=0`

**Risk Mitigation:** Bridge is isolated — if it crashes, DearPyGui path is untouched.

---

### Phase 2: Build Static UI Shell with Mocked Data
**Goal:** Create the React frontend structure matching the reference image. No live connection yet — all data is mocked.

**New files (all in `web_ui/frontend/src/`):**
- `main.tsx`, `App.tsx`, `index.css` — Entry + global styles
- `types/events.ts` — TypeScript interfaces for all events
- `hooks/useWebSocket.ts` — WebSocket hook (connects to ws://localhost:8765)
- `hooks/useChat.ts`, `hooks/useStatus.ts`, `hooks/useVoice.ts` — State hooks
- `services/wsClient.ts` — WebSocket client with reconnect
- All components in `components/*/`: empty shells with correct layout

**CSS Design System** (dark cinematic theme):
```css
:root {
  --bg-primary: #0d1117;       /* Main background */
  --bg-panel: #161b22;         /* Card/panel background */
  --bg-elevated: #1c2128;      /* Elevated elements */
  --border-subtle: #21262d;    /* Panel borders */
  --border-active: #30363d;    /* Active/hover borders */
  --text-primary: #e6edf3;     /* Primary text */
  --text-secondary: #8b949e;   /* Secondary text */
  --accent-blue: #58a6ff;      /* Primary accent */
  --accent-blue-dim: #388bfd;  /* Hover accent */
  --accent-green: #3fb950;     /* Success/idle */
  --accent-amber: #d29922;     /* Warning/thinking */
  --accent-red: #f85149;       /* Error/stop */
  --avatar-glow: rgba(88, 166, 255, 0.15);  /* Subtle avatar glow */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
}
```

**Layout Structure:**
```
┌─────────────────────────────────────────────────────────┐
│  TopBar (branding + tabs + wake/sleep + controls)       │
├──────────────────────────┬──────────────────────────────┤
│                          │                              │
│   ChatPanel              │   AvatarCard                 │
│   (conversation)         │   (portrait)                 │
│                          │                              │
│                          ├──────────────────────────────┤
│                          │   ModeSelector               │
│                          │   (Secretary/Scientist/...)   │
│                          ├──────────────────────────────┤
│                          │   VoiceBar                   │
│                          │   (waveform + mic)           │
├──────────────────────────┴──────────────────────────────┤
│   SystemPanel (right sidebar - overlay or adjacent)     │
│   ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐  │
│   │ MemoryCard  │ │ QuickActions│ │ SystemOverview  │  │
│   └─────────────┘ └─────────────┘ └─────────────────┘  │
│   ┌─────────────────────────────────────────────────┐   │
│   │ LogPanel (collapsible, default collapsed)       │   │
│   └─────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│  BottomBar (model + context + privacy + time + uptime)  │
└─────────────────────────────────────────────────────────┘
```

**Verification:**
- `npm run dev` shows the full layout with mock data
- Responds to window resize
- Toggle log panel open/close
- Mode selector changes highlight
- No connection to backend yet

---

### Phase 3: Connect Live Conversation/Status/Log Stream
**Goal:** Wire the frontend to the bridge. Real events flow from backend ► bridge ► frontend.

**Integration points:**
1. `useWebSocket.ts` connects to `ws://localhost:8765`
2. Incoming events dispatched to: `useChat.ts`, `useStatus.ts`, `useVoice.ts`
3. Chat messages render live with streaming deltas
4. Status bar updates in real-time
5. Log panel receives `agent_log` and `boot_log` events

**Changes:**
- `hooks/useWebSocket.ts`: Implement real connection with auto-reconnect
- `hooks/useChat.ts`: Handle `assistant_delta`, `assistant_final`, `user_message`, `chat_append`
- `hooks/useStatus.ts`: Handle `status_update`, `boot_ready`, `ui_controls_refresh`
- `components/chat/MessageBubble.tsx`: Implement streaming text animation
- `components/system/LogPanel.tsx`: Handle `log_event`, render color-coded lines

**Verification:**
- Start Piper with `PIPER_WEB_UI_ENABLED=1`
- Open browser to `http://localhost:8765`
- Send a message — should see streaming response in chat
- Stop button works
- Status changes reflect backend state
- Logs appear in log panel

**Risk Mitigation:** If the WebSocket connection drops, the frontend shows "Reconnecting..." and retries. Backend is unaffected.

---

### Phase 4: Add Controls
**Goal:** Wire all user-facing controls to send actions to the backend.

**Controls to wire:**
| Control | Action |
|---------|--------|
| Send button | `send_message` |
| Stop button | `stop` |
| Mic button | `mic_toggle` |
| Wake/Sleep pills | `mic_toggle` (wake) / `stop` (sleep) |
| Copy conversation | `copy_conversation` |
| Copy logs | Client-side: serialize log buffer to clipboard |
| Clear session | `new_session` |
| Clear logs | `clear_logs` |
| Snapshot | `snapshot` |
| Live screen mode | `live_screen_mode_change` |
| Live screen interval | `live_screen_interval_change` |
| Event speech mode | `event_speech_mode_change` |
| Restart | `restart` |
| Document picker | `document_picker` |
| Code console send/run/clear/stop | `code_send` / `code_run` / `code_clear` / `stop` |
| Avatar mode selector | `set_avatar_mode` (future: style change) |

**Changes:**
- Each control component dispatches its action via `wsClient.sendAction()`
- Bridge receives action, calls corresponding controller method
- Controller does exactly what it does now — no new code paths

**Verification:**
- Every control triggers the correct backend behavior
- Frontend state updates reflect control results
- Error handling: failed actions show toast

---

### Phase 5: Retire DearPyGui Only After Parity
**Goal:** Confirm the web UI has 100% feature parity with DearPyGui, then make it the default.

**Parity Checklist:**
- [ ] Chat messaging (send, receive, stream)
- [ ] Stop active work
- [ ] New/clear session
- [ ] MIC toggle
- [ ] Vision snapshot
- [ ] Live screen controls
- [ ] Event speech mode
- [ ] Document picker/ingestion
- [ ] Code console (send, run, clear, stop)
- [ ] Statistics display
- [ ] Boot sequence display
- [ ] Log panel
- [ ] Settings/config
- [ ] Restart
- [ ] Proactive reminders
- [ ] User switching / voice recognition
- [ ] Image generation display
- [ ] Keyboard shortcuts (Enter to send, Escape to stop)

**Retirement criteria:**
- All checklist items pass
- 1 week of daily use without issues
- No regression in backend behavior

**Transition:**
1. Change `WEB_UI_ENABLED` default from `False` to `True`
2. Keep DearPyGui code in `ui/` for emergency fallback
3. Document `PIPER_WEB_UI_ENABLED=0` to use legacy UI

---

## 8. Risks and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **WebSocket connection is unreliable** | Low | High | Auto-reconnect with exponential backoff. Frontend shows "Reconnecting..." state. Bridge runs on localhost so network issues are minimal. |
| **Frontend performance degrades with long chats** | Medium | Medium | Virtualize chat list (only render visible messages). Paginate log panel. These are well-solved React patterns. |
| **Backend events leak into chat** | Medium | High | The existing `renderable_chat_messages()` filter in `controller_render.py` already handles this. The frontend applies the same filtering. Tool events render as toasts, not chat messages. |
| **Bridge crashes, takes Piper down** | Low | High | Bridge runs in a **daemon thread**. If it crashes, Piper continues. Controller catches and logs bridge exceptions. |
| **DearPyGui and Web UI conflict** | Low | Medium | They never run simultaneously — `WEB_UI_ENABLED` flag selects one path. The `ui_queue` is thread-safe (it's a `queue.Queue`). |
| **Avatar images are large, slow to load** | Low | Low | Images are local static files. Optimize to WebP. Total size < 2MB for all modes. |
| **Browser compatibility issues** | Low | Low | Target Chromium-based browsers (Edge/Chrome). Use modern but stable CSS. No experimental features. |
| **Increased startup time** | Low | Low | Bridge starts in ~50ms. Frontend loads from localhost (no network). Total overhead < 200ms. |
| **TTS/audio integration in browser** | Medium | Medium | Keep TTS in Python. The browser is just the display. Audio plays via existing Python sounddevice path. Future: could add Web Audio API for UI sounds. |
| **Two UI codebases to maintain** | High | Medium | This is temporary. Once DearPyGui is retired, `ui/` can be archived. Until then, changes to controller behavior automatically work for both (same `ui_queue`). |

---

## 9. Exact Files Codex Should Inspect First

When beginning implementation, read these files in order:

### 9.1 Understand the UI-Backend Contract (read first)

| Priority | File | What to look for |
|----------|------|-----------------|
| **1** | `ui/controller_queue.py` | **ALL event kinds** emitted via `ui_queue`. This is the complete event catalog. ~20 kinds defined here. |
| **2** | `ui/controller.py` | `PiperController` class — the full API surface. Constructor dependencies, all public methods, state fields (`boot_ready`, `runtime_mode`, `cancel_tokens`, etc.). The `run()` method lifecycle. |
| **3** | `ui/layout.py` | All DPG tags (TAG_* constants), layout structure, callback map passed to `build_ui()`. This defines the visual zones. |
| **4** | `ui/controller_actions.py` | All action handlers — what happens when user clicks buttons. Maps 1:1 to incoming action messages. |

### 9.2 Understand the Backend Connection

| Priority | File | What to look for |
|----------|------|-----------------|
| **5** | `core/contracts.py` | `OrchestratorConfig` — the `ui` field is the `ui_queue`. This is how the backend emits events. |
| **6** | `core/pipeline.py` | `ChatPipeline` — how streaming works, `handle_event()` method, TTS integration. |
| **7** | `app.py` | How the controller is constructed, dependency wiring, the main entry point. |

### 9.3 Understand State and Formatting

| Priority | File | What to look for |
|----------|------|-----------------|
| **8** | `ui/controller_status.py` | `classify_runtime_mode()` — maps status text to mode enum. `MODE_COLOR_MAP` — color per mode. `refresh_top_bar()` — status display logic. |
| **9** | `ui/controller_render.py` | `format_chat_message_block()` — how messages are formatted. `renderable_chat_messages()` — which messages are chat-visible vs. filtered out. `append_bounded_line_block()` — log line limiting. |
| **10** | `config.py` | All config flags. Add `WEB_UI_ENABLED` and `WEB_UI_PORT` here. |

### 9.4 Reference (read for context, not implementation)

| Priority | File | What to look for |
|----------|------|-----------------|
| 11 | `memory/chat_state.py` | How messages are stored, `append()`, `upsert_streaming_assistant()`, `get_messages_snapshot()`. |
| 12 | `ui/event_speech.py` | TTS event speech mapping — what gets spoken for each event kind. |
| 13 | `core/orchestrator.py` | How the orchestrator uses `ui` (the queue) to emit events. Search for `ui_queue.put` or `ui.put`. |

---

## 10. Quick Reference: Event Kind Mapping

The existing `ui_queue` event kinds (`controller_queue.py`) map to the new schema as follows:

| Existing Kind (`controller_queue.py`) | New Event Name | Payload Shape |
|--------------------------------------|----------------|---------------|
| `assistant_stream_delta` | `assistant_delta` | `{"text": string}` |
| `assistant_stream_start` | `assistant_start` | `{"tts_voice"?: string, "tts_speed"?: number}` |
| `assistant_stream_end` | `assistant_final` | `{}` |
| `boot_log` | `log_event` | `{"level": "info", "source": "boot", "message": string}` |
| `boot_ready` | `status_update` | `{"mode": "IDLE", "label": "Ready"}` |
| `status` | `status_update` | `{"mode": string, "label": string}` |
| `status_widget_mode` | `status_update` | `{"mode": string}` |
| `status_widget_step` | `status_update` | `{"stageMeta": string}` |
| `status_widget_dashboard_activity` | `log_event` | `{"level": "info", "source": "system", "message": string}` |
| `agent_log` | `log_event` | `{"level": "info", "source": "agent", "message": string}` |
| `error` | `log_event` | `{"level": "error", "source": "system", "message": string}` |
| `chat_append` | `user_message` | `{"role": "user", "content": string}` (filtered) |
| `clear_thinking` | — (internal) | Remove typing indicator |
| `show_image` | `show_image` | `{"path": string}` |
| `search_result` | `search_result` | `{"query": string, "results": [...]}` |
| `vision_snapshot_note` | `log_event` | `{"level": "info", "source": "vision", "message": string}` |
| `code_session_*` | `code_*` | Various |
| `documents_view` | `documents_update` | `{"content": string}` |
| `stats_view_refresh` | `stats_refresh` | Trigger stats re-fetch |
| `ui_controls_refresh` | `controls_refresh` | Re-evaluate button states |
| `active_user_changed` | `auth_state` | `{"userLabel": string}` |
| `document_ingest_active` | `document_ingest` | `{"active": boolean}` |
| `live_screen_refresh` | `live_screen` | `{"pending": boolean}` |
| `config_reloaded` | `config_reload` | `{"changedKeys": string[]}` |

---

## 11. Implementation Order Summary

```
WEEK 1 — Phase 0 + Phase 1
├── Day 1-2: Read all 13 files above. Document every event kind.
├── Day 3-4: Build bridge (server.py, adapter.py, message_schema.py)
├── Day 5:   Wire bridge into app.py + controller.py. Test with ws client.

WEEK 2 — Phase 2
├── Day 1-2: Scaffold React project (Vite, TypeScript, folder structure)
├── Day 3-4: Build layout shell (TopBar, ChatPanel, AvatarCard, SystemPanel, BottomBar)
├── Day 5:   CSS theme + mock data + ModeSelector + VoiceBar visual

WEEK 3 — Phase 3
├── Day 1-2: WebSocket hook + event dispatching to state hooks
├── Day 3:   Wire chat streaming (MessageBubble + assistant_delta handling)
├── Day 4:   Status bar + log panel live connection
├── Day 5:   Boot sequence + all non-control events

WEEK 4 — Phase 4
├── Day 1-2: All action buttons (send, stop, mic, wake/sleep, copy)
├── Day 3:   Document picker + code console controls
├── Day 4:   Settings + remaining controls
├── Day 5:   Polish: keyboard shortcuts, error toasts, loading states

WEEK 5 — Phase 5 (validation)
├── Day 1-5: Daily use, parity checklist, bug fixes
└──         Flip WEB_UI_ENABLED default to True
```

---

## 12. Conclusion

**The architecture is simple because Piper already has the right abstraction:** the `ui_queue` is a message bus. The web UI is just another consumer of that bus.

**The risk is low because:**
- The existing DearPyGui UI stays untouched as a fallback
- The backend (core/, memory/, llm/, tools/) requires **zero changes**
- The bridge is additive — it's a new module, not a rewrite
- Each phase has clear verification criteria

**The visual ceiling is high because:**
- React + CSS can achieve the reference image precisely
- Component-based architecture supports future avatar modes
- The design system is extensible

**Start with Phase 0: read the 13 files listed in Section 9.1. Then build the bridge (Phase 1). The rest follows.**
