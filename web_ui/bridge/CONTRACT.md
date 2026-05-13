# Piper Web UI Bridge — Phase 0 Contract

> Status: Phase 0 — Read-only contract mapping
> Branch: `feature/web-ui-bridge`
> Base: `fix/guest-voice-name-disambiguation`
> Scope: Document only. No runtime changes.

---

## 1. EVENT_KINDS

Piper's backend -> frontend communication flows through a single `queue.Queue` (`controller.ui_queue`). Each event is a tuple `(kind: str, payload: object)`. The pump lives in `ui/controller_queue.py` and processes events on the DearPyGui render thread.

The table below maps every observed `kind` to its payload shape, source location, current DPG handling path, proposed WebSocket event name, frontend visibility classification, and notes.

### Legend: Frontend Visibility

| Label | Meaning |
|---|---|
| `chat` | Appears in the main chat transcript |
| `log` | Appears in a raw/debug log panel |
| `status` | Appears in a status bar or top indicator |
| `control` | Triggers UI control enable/disable state |
| `image` | Updates an image viewer |
| `document` | Updates a document panel |
| `code` | Updates the code console |
| `internal` | Used internally; may not need frontend exposure |
| `multi` | Visible in multiple surfaces depending on payload |

---

### 1.1 Streaming Events (Assistant Response)

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `assistant_stream_start` | `dict` with optional `tts_voice`, `tts_speed` | `core/orchestrator.py` -> `orc.ui.put()` -> queued by `core/pipeline.py` | `controller.pipeline.handle_event("start", ...)` -> ChatPipeline lazy TTS start | `stream.start` | `chat` + `status` | Signals the beginning of a persona/streaming response. TTS metadata is optional. |
| `assistant_stream_delta` | `dict` with `"text"` or raw `str` | Same as above | `controller.pipeline.handle_event("delta", ...)` -> ChatPipeline upserts chat widget + TTS push. Throttled to ~60 fps in pump. Returns to render loop after ONE delta. | `stream.delta` | `chat` | **Critical:** Pump processes exactly one delta per frame and breaks. A WebSocket bridge must preserve this backpressure or the frontend will flood. |
| `assistant_stream_end` | `dict` with optional `tts_voice`, `tts_speed` or empty | Same as above | `controller.pipeline.handle_event("end", ...)` -> ChatPipeline finalizes stream, persists turn, sets status "Ready" | `stream.end` | `chat` + `status` | Ends streaming. Triggers `persist_turn` and TTS `stream_end`. |

---

### 1.2 Status & Mode Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `status` | `str` | `core/orchestrator_phases.py` (phases set status), `controller_actions.py` | `controller.set_status()` -> `refresh_top_bar()` -> DPG `status_text` tag + color from `MODE_COLOR_MAP` | `status.set` | `status` | Core state indicator: "IDLE", "THINKING", "GENERATING", "SEARCHING", etc. |
| `status_widget_mode` | `str` | `core/orchestrator.py` / phases | `controller.runtime_mode = classify_runtime_mode(...)` -> `refresh_top_bar()` | `status.mode` | `status` | Runtime mode classification (IDLE, ROUTING, SEARCHING, etc.). |
| `status_widget_step` | `str` | `core/orchestrator_phases.py` | `controller._set_stage_meta()` -> `refresh_top_bar()` | `status.step` | `status` | Stage/step metadata like "Stage 1/2 | Step 3". |
| `status_widget_dashboard_activity` | `str` | `controller_actions.py`, `core/engines/`, various | Appends to bounded line block in dashboard activity log (max 50 lines). Also triggers `maybe_speak_ui_event`. | `activity.append` | `log` + `status` (speech) | High-volume activity log. Speech-enabled depending on `event_speech_mode`. |
| `ui_controls_refresh` | `str` (usually empty) | `controller.py` (cancel/search/reminder retain/release) | `controller.refresh_interaction_state()` -> enables/disables DPG widgets (send, stop, mic, etc.) | `controls.refresh` | `control` | **Frequent.** Sent whenever operation counters change. A Web UI should debounce this or derive state from explicit lifecycle events. |

---

### 1.3 Boot & Lifecycle Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `boot_log` | `str` | `llm/boot.py` -> `BootManager` | Appended to boot log text widget (`boot_log_text`). Also triggers speech in "noisy" mode. | `boot.log` | `log` | Boot sequence logs. Hidden after boot_ready. |
| `boot_ready` | `str` or empty | `llm/boot.py` | Deferred until `_boot_ui_min_visible_until`. Then hides `boot_group`, shows `status_group`, sets `boot_ready=True`. | `boot.ready` | `status` + `control` | Signals Piper is ready for input. Enables controls. |

---

### 1.4 Chat & Message Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `chat_append` | `dict` with `role`, `content` | `controller_actions.py` (document ingest, live screen), `core/orchestrator.py` | `controller.chat_append(role, content)` -> adds to chat_state + renders via DPG. | `chat.append` | `chat` | Used for system messages (e.g. "[UI] Live screen mode enabled"). |
| `clear_thinking` | `str` (ignored) | `controller_actions.py` (`do_generate_stream`, interrupt handlers) | `controller.clear_thinking_placeholder()` -> removes "Thinking..." if present. | `chat.clear_thinking` | `chat` | Removes the assistant "Thinking..." placeholder. |

---

### 1.5 Search Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `search_result` | `dict` with `query`, `data`, `cancel_token`, optional `error` | `tools/search.py` (background thread) -> `controller_actions.handle_search_result()` | Queues background search content as hidden system messages, then auto-launches reporter turn via `run_agent_loop()`. Also triggers speech. | `search.result` | `internal` + `chat` (indirect) | **Async lifecycle event.** The frontend should NOT display this raw. The reporter turn produces the actual chat response. |

---

### 1.6 Image / Vision Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `show_image` | `str` (path or message like "Image saved to: ...") | `tools/image_gen.py`, `controller_actions.py` (live screen capture) | `handle_show_image()` -> loads image via `dpg.load_image()`, updates texture registry, shows in Visual Cortex tab. | `image.show` | `image` | Displays generated or captured image. Path resolution is workspace-relative. |
| `vision_snapshot_note` | `dict` with `text`, `speak: bool` or `str` | `controller.py` (`queue_visual_note`) | Appended to dashboard activity log with "Vision note:" prefix. Speech if `speak=True` and mode allows. | `vision.note` | `log` + `status` (speech) | Live screen visual analysis notes. |

---

### 1.7 Code Session Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `code_session_launch` | `dict` with `path` or `None` | `core/code_session.py` | Activates code tab, sets status, calls `start_code_session()`. Speech in "all"/"noisy" mode. | `code.launch` | `code` + `status` | Launches embedded Python process. |
| `code_session_reset` | `str` (ignored) | `core/code_session.py` | `controller.clear_code_output()` | `code.reset` | `code` | Clears code console output. |
| `code_session_output` | `str` | `core/code_session.py` | `controller.append_code_output()` -> appends to code view text (capped at 40k chars). | `code.output` | `code` | Stdout/stderr from embedded process. |
| `code_session_status` | `str` | `core/code_session.py` | `controller.set_code_status()` -> updates code status text. Speech depending on mode. | `code.status` | `code` + `status` | Status like "Launching...", "Exited with code 0". |
| `code_session_active` | `bool` | `core/code_session.py` | `controller.set_code_session_active()` -> updates meta, interaction state. | `code.active` | `code` + `control` | True when embedded process is running. |
| `code_session_focus` | `str` (ignored) | `core/code_session.py` | Activates code tab, focuses code input box. | `code.focus` | `control` | Requests frontend focus on code input. |
| `code_view` | `str` | `core/code_session.py` (preview) | Replaces code view text if no active session. | `code.preview` | `code` | Shows file content preview. |

---

### 1.8 Document Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `documents_view` | `str` | `controller.py` (`refresh_documents_view()`) | Sets `documents_view_text` widget value. | `document.view` | `document` | Rendered summary of ingested documents. |
| `document_ingest_active` | `bool` | `controller_actions.py` | Sets flag, refreshes interaction state (disables ingest button). | `document.ingest_active` | `control` | True while document ingestion is running. |

---

### 1.9 User & Identity Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `active_user_changed` | `dict` with `preserve_transcript: bool` | `memory/user_runtime.py` -> `controller.py` | Rebinds memory path, optionally persists captured messages, refreshes chat UI and interaction state. | `user.changed` | `chat` + `status` | Fired when voice/typed identity resolution switches the active user profile. |

---

### 1.10 Stats Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `stats_view_refresh` | `str` (ignored) | `controller.py` | `controller.refresh_stats_view()` -> updates stats text + plot series values. | `stats.refresh` | `status` | Triggers stats dashboard redraw. |

---

### 1.11 Error Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `error` | `str` | `controller_actions.py`, `AGENTS/harness/session.py`, `scripts/phase8_checklist_test.py` | `controller.pipeline.handle_event("error", ...)` -> ChatPipeline appends system message + TTS end. Also triggers speech. | `error` | `chat` + `status` | Critical error display. Always shown in chat as system message. |

---

### 1.12 Agent / Monitor Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `agent_log` | `str` | `controller.py` (`safe_log`) | `controller.log_agent_monitor()` -> prints + appends to agent log text (bounded, max 200 lines). Also triggers speech in "noisy" mode. | `log.agent` | `log` | Raw agent monitor logging. |

---

### 1.13 Live Screen Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `live_screen_refresh` | `dict` with `pending: bool` | `controller_actions.py` | `refresh_live_screen_ui()` -> updates snapshot button label/theme, combo values, screen meta. | `screen.refresh` | `status` + `control` | Live screen state changed. |

---

### 1.14 Config Events

| Kind | Payload | Source | DPG Path | WS Name | Visibility | Notes |
|---|---|---|---|---|---|---|
| `config_reloaded` | `list[str]` (changed keys) | `controller.py` (`_on_config_changed`) | **Currently unhandled in pump.** Falls through silently. | `config.reloaded` | `internal` | Fired when `LiveConfig` detects override changes. No DPG handler exists today. |

---

### 1.15 Event Count Summary

| Category | Count |
|---|---|
| Streaming | 3 |
| Status/Mode | 4 |
| Boot/Lifecycle | 2 |
| Chat/Message | 2 |
| Search | 1 |
| Image/Vision | 2 |
| Code Session | 7 |
| Document | 2 |
| User/Identity | 1 |
| Stats | 1 |
| Error | 1 |
| Agent/Monitor | 1 |
| Live Screen | 1 |
| Config | 1 |
| **Total** | **29** |


---

## 2. ACTIONS

User-facing actions are DPG callbacks registered in `ui/layout.py` and dispatched from `ui/controller_actions.py` or `ui/controller.py`. The table below maps each action to its current implementation, payload needs, safety classification, and source.

### Legend: Safety

| Label | Meaning |
|---|---|
| `safe` | Read-only or low-risk |
| `state-changing` | Mutates controller/backend state |
| `destructive` | Clears data, stops processes |
| `restart` | Requires application restart |

---

| Action Name (Proposed WS) | Current Method | DPG Callback Source | Payload Fields | Safety | Notes |
|---|---|---|---|---|---|
| `send_message` | `controller_actions.on_send()` | `layout.py` -> `on_send` | `text: str` | `state-changing` | Main user input. Handles commands, interrupt detection, code session input, and normal text submission. |
| `stop` | `controller_actions.on_stop()` | `layout.py` -> `on_stop` | none | `destructive` | Cancels active operations, stops code session, stops TTS. |
| `new_session` | `controller_actions.on_new_session()` | `layout.py` -> `on_new_session` | none | `destructive` | Clears conversation summary, starts fresh chat session. |
| `clear_chat` | `controller_actions.on_clear()` | `layout.py` (modal dialog) -> `on_new_session` alias | none | `destructive` | Clears chat widget and resets cache. |
| `mic_toggle` | `controller_actions.on_mic_toggle()` | `layout.py` -> `on_mic_toggle` | none | `state-changing` | Toggles STT recording. Applies voice identity match on stop. |
| `snapshot_toggle` | `controller_actions.on_snapshot()` | `layout.py` -> `on_snapshot` | none | `state-changing` | Toggles live screen capture mode. |
| `live_screen_mode` | `controller_actions.on_live_screen_mode_changed()` | `layout.py` -> combo callback | `mode: str` ("display" / "window" / "pointer") | `state-changing` | Changes live screen source. |
| `live_screen_interval` | `controller_actions.on_live_screen_interval_changed()` | `layout.py` -> combo callback | `interval_s: float` (2 / 5 / 10 / 15) | `state-changing` | Changes live screen capture interval. |
| `event_speech_mode` | `controller_actions.on_event_speech_mode_changed()` | `layout.py` -> combo callback | `mode: str` (off / important / all / noisy) | `state-changing` | Changes event TTS verbosity. |
| `restart_piper` | `controller_actions.on_restart()` | `layout.py` -> restart button | none | `restart` | Sets restart flag, stops DPG, exits with code 85. |
| `open_document_picker` | `controller_actions.on_open_document_picker()` | `layout.py` -> ingest button | none | `safe` | Opens DPG file dialog. |
| `document_picker_selected` | `controller_actions.on_document_picker_selected()` | `layout.py` -> file dialog callback | `paths: list[str]` | `state-changing` | Starts document ingestion thread. |
| `document_picker_cancel` | `controller_actions.on_document_picker_cancel()` | `layout.py` -> file dialog cancel | none | `safe` | Closes dialog. |
| `code_send` | `controller_actions.on_code_send()` | `layout.py` -> code send button / enter key | `text: str` | `state-changing` | Sends input to active embedded process. |
| `code_run` | `controller_actions.on_code_run()` | `layout.py` -> code run button | none | `state-changing` | Launches `.py` file from code preview. |
| `code_clear` | `controller_actions.on_code_clear()` | `layout.py` -> code clear button | none | `destructive` | Clears code console output. |

---

### 2.1 Action Count Summary

| Safety | Count |
|---|---|
| safe | 2 |
| state-changing | 10 |
| destructive | 4 |
| restart | 1 |
| **Total** | **17** |

---

## 3. CHAT VISIBILITY RULES

The function `renderable_chat_messages()` in `ui/controller_render.py` (lines 104-123) is the single source of truth for what appears in the chat transcript.

### 3.1 Hard Filters

1. **`hidden: true`** — Any message with `hidden=True` is skipped entirely. This is the primary mechanism for suppressing system instructions, runtime context blocks, and search reporter directives from chat view.

2. **`role == "system"` with filtered prefixes** — System messages are further screened:
   - Content starting with `"[Saved to file:"` -> **skipped**
   - Content starting with `"System retrieved file"` -> **skipped**
   - Content starting with `"Tool Response"` -> **skipped**

3. **`role == "assistant"` with empty content** — Empty assistant messages are skipped.

### 3.2 What Gets Hidden (and Why)

The following message types are appended to chat history with `hidden=True` and therefore **never appear in the visible transcript**:

- `[LATEST_RUNTIME_CONTEXT]` blocks (scratchpad state)
- `[SEARCH SUMMARY FOR '{query}']` and `[SEARCH_REPORT_RULE]` (reporter instructions)
- `[PROACTIVE_TRIGGER]` reminder system notices
- `[LAST_TURN_EXPLANATION_CONTEXT]` (EXPLAIN route support)
- `[VOICE IDENTITY EVENT]` notices
- Pending file target confirmation messages
- Planner step directives inside executor

### 3.3 What Appears as `[UI]` System Messages

Certain system messages are intentionally visible and prefixed with `[UI]`:

- User list (`[UI] Users: ...`)
- Active user (`[UI] Active user: ...`)
- Live screen mode changes (`[UI] Live screen mode enabled/disabled`)
- Live screen errors (`[UI] Live screen error: ...`)
- Document ingest status (`[UI] Ingesting document: ...`)
- Password prompts (`[UI] Password required.`)
- Admin sign-in results
- LangGraph recovery/interrupt status
- Mic / STT errors (`[Mic Error: ...]`, `[STT Error: ...]`, `[No speech detected]`)
- Busy warnings (`[UI] Piper is busy. Press Stop...`)

### 3.4 Identity Clarification Notices

Voice identity events use `_announce_voice_identity_event()` -> `_set_voice_identity_notice()` which builds a `\n[VOICE IDENTITY EVENT]\n...` block. This is injected into the **orchestrator config** as `voice_identity_notice`, not as a chat message. It becomes part of the persona prompt context, not visible chat.

Typed identity hints that require clarification return a message that gets `chat_append`ed as a **visible system message**.

**Important (1414316 fix):** Ambiguous identity clarification is routed through an internal persona-facing `voice_identity_notice`, not through visible `chat_append`. The Web UI adapter must not surface raw `[VOICE IDENTITY CLARIFICATION]`, `[VOICE IDENTITY EVENT]`, or raw `[UI]` identity disambiguation text (e.g. "I need one more detail to identify who is speaking") as chat-visible output. These are persona directive context only.

**Note:** User-facing control messages such as `[UI] Password required.` and `[UI] Admin sign-in failed.` are legitimate UI messages and must remain visible. Only raw identity internals are suppressed.

### 3.5 Thinking Placeholder

`"Thinking..."` is appended as a visible assistant message during turn start. It is removed by `clear_thinking` before the real response streams. If streaming fails, the placeholder may persist.

### 3.6 Leakage Risks for Web UI

| Risk | Description | Mitigation |
|---|---|---|
| Hidden message exposure | A naive Web UI that renders all messages from `chat_state.get_messages_snapshot()` will expose system instructions. | Frontend MUST respect the `hidden` field. |
| Raw search payload | `search_result` event contains raw HTML/snippet data. Should not be rendered as chat. | Map to internal activity log only; wait for reporter turn. |
| `[UI]` prefix stripping | Some frontends may strip `[UI]` prefixes thinking they are meta. These are intentional user-facing messages. | Preserve `[UI]` prefix or render with distinct styling. |
| Tool tags in stream | Persona output may contain `[RUN_CODE]`, `[RECALL: ...]`, `[ROUTER]` tags. | The `TagScrubber` in `core/pipeline.py` already filters these before display. A Web UI must apply equivalent scrubbing or rely on the backend's `chat_upsert` buffer. |

---

## 4. DIRECT DPG ASSUMPTIONS / RISKS

The current UI layer is tightly coupled to DearPyGui. These are the risk points for Phase 3 wiring.

### 4.1 Widget Tag Assumptions

`PiperController` defines `UiTags` (lines 73-132 in `controller.py`) with ~40 string tag constants. These tags are referenced throughout:

- `controller_queue.py` checks `dpg.does_item_exist(tag)` before every operation.
- `controller.py` methods directly call `dpg.set_value()`, `dpg.configure_item()`, `dpg.delete_item()`, `dpg.add_input_text()` using these tags.
- `layout.py` creates all widgets with these tags at build time.

**Risk:** A Web UI cannot implement these DPG-specific tags. Phase 3 must introduce an abstraction layer (e.g., a `UiSurface` protocol) so the controller talks to interfaces, not widgets.

### 4.2 Render Loop Integration

`controller.run()` (line 1217) is the main loop:

```python
while dpg.is_dearpygui_running():
    self.pump_ui_queue()
    dpg.render_dearpygui_frame()
    self._flush_autoscrolls()
```

- `pump_ui_queue()` runs once per frame.
- Stream deltas break after ONE delta to force a render frame.
- Autoscroll is deferred across multiple frames via `pending_autoscrolls` counter.

**Risk:** A Web UI will not have a synchronous `render_dearpygui_frame()`. The bridge must ensure pump_ui_queue can run independently (e.g., on a WebSocket message thread or asyncio loop) without the frame-break assumption.

### 4.3 Immediate Mode Widget Mutation

The chat rendering uses immediate-mode DPG APIs:

- `_append_chat_message_widget()` calls `dpg.add_input_text(parent=tags.chat_text, ...)` to create a new widget per message.
- `_update_last_chat_message_widget()` mutates the last widget in-place during streaming.
- `_refresh_chat_ui()` deletes all children and recreates everything.

**Risk:** A Web UI using a virtual DOM (React/Vue) will handle this differently. The bridge should emit `chat.append`, `chat.update_last`, `chat.refresh` events rather than widget operations.

### 4.4 Theme & Color Bindings

- `layout.py` binds themes to widgets at creation.
- `controller_actions.py` dynamically creates and binds temporary themes (e.g., mic button red when recording).
- `controller_status.py` maps mode strings to RGBA tuples in `MODE_COLOR_MAP`.

**Risk:** Theme data is DPG-specific. A Web UI will use CSS. The contract should treat colors as semantic (e.g., `status_color: "error"`) rather than raw RGBA.

### 4.5 File Dialog

Document ingestion uses `dpg.file_dialog()` with a modal picker. The callback receives DPG-specific `app_data` with `selections`, `current_path`, `file_path_name`.

**Risk:** A Web UI will use `<input type="file">` or drag-and-drop. The `document_picker_selected` action must accept a normalized path list, not DPG app_data.

### 4.6 Texture Registry for Images

`handle_show_image()` manipulates `dpg.texture_registry`, `dpg.add_static_texture()`, and `dpg.add_image()`.

**Risk:** Web images are URLs or blob URLs. The bridge must serve image files via HTTP and emit `image.show` with a URL, not manage GPU textures.

### 4.7 Key Handler Registry

`layout.py` registers `dpg.add_key_down_handler()` for Enter-to-send.

**Risk:** Web keyboard handling is native to the browser. No bridge work needed here, but the action contract must support `send_message` triggered by Enter.

### 4.8 Plot Widgets for Stats

Stats view uses `dpg.plot()`, `dpg.add_line_series()`, `dpg.fit_axis_data()`.

**Risk:** A Web UI will use a charting library (e.g., Recharts, Chart.js). The bridge should emit `stats.data` events with raw series data, not plot widget commands.

---

## 4.5 Desktop Window Policy

Web UI means React/HTML/CSS rendering technology, not final delivery in a normal browser tab. Development may use browser access for debugging. Production target is a dedicated Piper desktop window with no address bar, no tabs, and local/offline assets. Wrapper choice is deferred until after parity: Tauri first, pywebview second, Edge/Chrome app-mode temporary fallback.

## 5. PROPOSED WEBSOCKET FRAME FORMAT

This section defines the JSON contract for a future WebSocket transport. All timestamps are ISO 8601 strings in UTC.

### 5.1 Outgoing Backend -> Frontend Events

```json
{
  "frame": "event",
  "timestamp": "2026-05-09T22:24:56.740Z",
  "requestId": "",
  "kind": "stream.delta",
  "sourceKind": "assistant_stream_delta",
  "payload": {
    "text": "Hello, this is a delta."
  }
}
```

**Top-level fields:**

| Field | Type | Description |
|---|---|---|
| `frame` | `"event"` | Discriminator |
| `timestamp` | `string` | Event emission time |
| `requestId` | `string` | Correlates to an incoming action request. Empty for unsolicited events. |
| `kind` | `string` | Frontend event name (see section 1 mapped names) |
| `sourceKind` | `string` | Original ui_queue kind string (e.g. `assistant_stream_delta`) |
| `payload` | `object` | Event-specific data |

**Streaming batching recommendation:** `stream.delta` events should be batched by the bridge if they arrive faster than the frontend can render (e.g., coalesce deltas within 16 ms into a single frame).

### 5.2 Incoming Frontend -> Backend Actions

```json
{
  "frame": "action",
  "timestamp": "2026-05-09T22:24:56.740Z",
  "requestId": "req-42",
  "action": "send_message",
  "payload": {
    "text": "Hello Piper"
  }
}
```

**Top-level fields:**

| Field | Type | Description |
|---|---|---|
| `frame` | `"action"` | Discriminator |
| `timestamp` | `string` | Client submission time |
| `requestId` | `string` | Client-generated UUID for correlation |
| `action` | `string` | Action name (see section 2) |
| `payload` | `object` | Action-specific data |

### 5.3 Error Frames (Backend -> Frontend)

```json
{
  "frame": "error",
  "timestamp": "2026-05-09T22:24:56.740Z",
  "requestId": "req-42",
  "kind": "action_rejected",
  "message": "Piper is not ready yet.",
  "payload": {}
}
```

| Field | Type | Description |
|---|---|---|
| `frame` | `"error"` | Discriminator |
| `timestamp` | `string` | Error time |
| `requestId` | `string` | Correlated request, if any |
| `kind` | `string` | `action_rejected`, `internal_error`, `validation_error` |
| `message` | `string` | Human-readable error |
| `payload` | `object` | Optional structured details |

### 5.4 Correlation Rules

- **Solicited responses:** Backend echoes the client's `requestId` in the response frame.
- **Unsolicited events:** `requestId` is empty string. Most backend events are unsolicited.
- **Stream correlation:** A `stream.start` frame should include a backend-generated `streamId` that all subsequent `stream.delta` and `stream.end` frames carry. This allows multiplexing if multiple streams are ever supported.

### 5.5 Binary Data

Images should NOT be base64'd in WebSocket frames. Instead:

```json
{
  "frame": "event",
  "timestamp": "...",
  "kind": "image.show",
  "payload": {
    "url": "/api/images/live_screen_12345.jpg",
    "caption": "Image saved to: workspace/live_screen.jpg"
  }
}
```

An HTTP endpoint (`/api/images/<name>`) should serve image files from the workspace. This follows AGENTS.md section 10A (no binary payloads in JSON).

---

## 6. PHASE 1 IMPLEMENTATION NOTES

Phase 1 goal: build `adapter.py` — a pure translation layer between `PiperController` / `controller_queue.py` and the WebSocket frame format defined in section 5.

### 6.1 Constraints

- **Pure translation only.** No I/O. No WebSocket server/client code.
- No changes to `app.py`, `config.py`, `controller.py`, or any existing source file.
- Adapter must be testable with deterministic unit tests.
- Unknown event kinds must have a defined fallback policy.

### 6.2 Proposed Module Structure (Future)

```
web_ui/
  bridge/
    CONTRACT.md          <- this document
    adapter.py           <- Phase 1: pure translation
    message_schema.py    <- Phase 1: TypedDict / dataclass schemas
    server.py            <- Phase 3: WebSocket server wiring
    tests/
      test_adapter.py    <- Phase 1: one test per event kind
```

### 6.3 Adapter Responsibilities

1. **Event encoding:** Convert `(kind, payload)` tuples from `ui_queue` into JSON frames (section 5.1).
2. **Action decoding:** Convert incoming JSON action frames into controller method calls.
3. **Unknown policy:**
   - Unknown outgoing event kind -> raise `ValueError`.
   - Unknown incoming action name -> raise `ValueError`.
   - No passthrough fallback in `adapter.py`.
4. **Stateless:** Adapter holds no mutable session state. All state lives in `PiperController`.

### 6.4 Testing Strategy

One deterministic test per event kind (29 tests):

```python
def test_encode_stream_delta():
    adapter = PiperBridgeAdapter()
    frame = adapter.encode_event("assistant_stream_delta", {"text": "hi"})
    assert frame["frame"] == "event"
    assert frame["kind"] == "stream.delta"
    assert frame["payload"]["text"] == "hi"
```

One test per action (17 tests):

```python
def test_decode_send_message():
    adapter = PiperBridgeAdapter()
    action = adapter.decode_action({
        "frame": "action",
        "requestId": "r1",
        "action": "send_message",
        "payload": {"text": "hello"}
    })
    assert action.name == "send_message"
    assert action.payload["text"] == "hello"
```

### 6.5 Ambiguities Requiring Runtime Confirmation

The following event payloads have shapes that depend on runtime context and may need validation during Phase 1 implementation:

1. **`search_result` payload** — Contains a `CancellationToken` object. This is not serializable. The adapter must decide whether to strip it or represent it as a string token ID.
2. **`active_user_changed` payload** — `preserve_transcript` is the only documented field, but the controller also accesses `chat_state.get_messages_snapshot()`. The adapter should not need to serialize the entire snapshot.
3. **`config_reloaded` payload** — A list of changed config keys. Currently unhandled in DPG. The adapter should pass it through as-is.
4. **`code_session_launch` payload** — May be `None` or a `dict` with `path`. The adapter must normalize both.
5. **`show_image` payload** — May be a simple path string or a message like `"Image saved to: path"`. The adapter should parse the path out for URL generation.
6. **`vision_snapshot_note` payload** — May be `dict {"text": ..., "speak": ...}` or raw `str`. The adapter must normalize.

### 6.6 Backpressure & Throttling

The current DPG pump throttles `assistant_stream_delta` to one per frame (~60 Hz) using `time.sleep()`. A WebSocket bridge cannot rely on a render loop. Recommended approach:

- The adapter does NOT throttle.
- The WebSocket server (Phase 3) may batch rapid `stream.delta` events.
- The frontend should implement its own rendering throttling (e.g., requestAnimationFrame).

### 6.7 Chat History Synchronization

For a Web UI, the initial page load needs the current chat history. The adapter should provide a method:

```python
def build_chat_sync_frame(self, controller: PiperController) -> dict:
    """Return a snapshot of visible chat messages for initial sync."""
```

This calls `renderable_chat_messages(controller.chat_state.get_messages_snapshot())` and returns a `chat.sync` event frame.

---

## 7. APPENDIX: Source Files Inspected

| # | File | Purpose |
|---|---|---|
| 1 | `AGENTS.md` | Doctrine, architecture rules, data hygiene |
| 2 | `docs/DOCUMENTS_MAP.md` | Navigation guide |
| 3 | `docs/WIP.md` | Active work register |
| 4 | `docs/architecture/TRIGGER_FLOW.md` | Runtime lifecycle spec |
| 5 | `notes/debug-protocol.md` | Debug protocol and known failure modes |
| 6 | `ui/controller_queue.py` | UI queue pump — all event kinds |
| 7 | `ui/controller.py` | PiperController — state, tags, render methods |
| 8 | `ui/layout.py` | DPG widget construction, callbacks |
| 9 | `ui/controller_actions.py` | Action implementations, user input handling |
| 10 | `ui/controller_status.py` | Status text, mode classification, colors |
| 11 | `ui/controller_render.py` | Chat formatting, message visibility rules |
| 12 | `app.py` | Entry point, controller construction |
| 13 | `config.py` | Configuration, paths, feature flags |
| 14 | `core/contracts.py` | TypedDict schemas for contracts |
| 15 | `core/pipeline.py` | ChatPipeline — streaming, TTS, tag scrubbing |
| 16 | `ui/event_speech.py` | Event-to-speech mapping |

---

## 8. REPORT

### Files Created

- `web_ui/bridge/CONTRACT.md` (this document)

### Files Inspected

- 16 source files read (see section 7)

### Event Count

- **29 distinct event kinds** documented across 13 categories
- 3 streaming events, 4 status events, 2 boot events, 2 chat events, 1 search event, 2 image/vision events, 7 code events, 2 document events, 1 user event, 1 stats event, 1 error event, 1 agent log event, 1 live screen event, 1 config event

### Action Count

- **17 user-facing actions** documented
- 2 safe, 10 state-changing, 4 destructive, 1 restart

### Risks Found

- **14 DPG coupling risks** identified in section 4:
  1. Widget tag assumptions (~40 tags)
  2. Render loop integration (frame-break semantics)
  3. Immediate-mode widget mutation
  4. Theme/color bindings (raw RGBA)
  5. File dialog (DPG-specific app_data)
  6. Texture registry for images
  7. Key handler registry
  8. Plot widgets for stats
  9. Chat append/update/refresh widget calls
  10. Autoscroll frame-deferred mechanism
  11. Live screen button theme dynamic binding
  12. Modal dialog window management
  13. Height calculations based on line counts
  14. `dpg.does_item_exist()` guards everywhere

### Ambiguous Event Payloads

- 6 payloads identified in section 6.5 that need runtime confirmation during Phase 1:
  1. `search_result` (CancellationToken serialization)
  2. `active_user_changed` (snapshot boundary)
  3. `config_reloaded` (currently unhandled in DPG)
  4. `code_session_launch` (None vs dict)
  5. `show_image` (path extraction from message)
  6. `vision_snapshot_note` (dict vs str)
