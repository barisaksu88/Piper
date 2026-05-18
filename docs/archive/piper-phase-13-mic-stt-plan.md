# Phase 13 Web UI Mic/STT Plan

> **Status:** Planning only. No implementation.  
> **Branch:** `feature/web-ui-bridge`  
> **HEAD:** `7b5a8e1f17a37de085fe2801b4c1ac4e2d96fea6`  
> **Base:** `fix/guest-voice-name-disambiguation` (`1414316`)  
> **Migration guide:** v1.2

---

## Verified Repository State

```
$ cd /tmp/piper-inspect
$ git checkout -b feature/web-ui-bridge FETCH_HEAD
$ git rev-parse HEAD
7b5a8e1f17a37de085fe2801b4c1ac4e2d96fea6
$ git log --oneline -5
7b5a8e1 docs(web-ui): mark checkpoint 2 passed, record 189 tests + regression notes
25892c9 fix(persona): ignore router marker after visible reply
3bc043c fix(web-ui): suppress noisy websockets handshake-error logs
2247dfc fix(web-ui): prevent duplicate assistant replies and router text leaks
$ python -m pytest web_ui/bridge/test_adapter.py -v --tb=short 2>&1 | tail -3
189 passed in 0.42s
```

### Files inspected

| # | File | Lines inspected | Purpose |
|---|------|-----------------|---------|
| 1 | `docs/specs/piper-web-ui-migration-guide.md` | 1-200+ | Phase history, status, roadmap |
| 2 | `web_ui/bridge/CONTRACT.md` | 1-200+ | Event/action contract map |
| 3 | `app.py` | 1-195 | Entry point, Web UI branch |
| 4 | `config.py` | 690-853 | Voice/STT/Web UI config flags |
| 5 | `ui/controller.py` | 1-1410 | PiperController full API |
| 6 | `ui/controller_queue.py` | 1-220 | Event pump (DPG + web) |
| 7 | `ui/controller_actions.py` | 1-560+ | `on_mic_toggle`, `_apply_voice_identity_match` |
| 8 | `web_ui/bridge/adapter.py` | 1-200+ | Frame translation |
| 9 | `web_ui/bridge/message_schema.py` | 1-100+ | KNOWN_EVENT_KINDS, KNOWN_ACTION_NAMES |
| 10 | `web_ui/bridge/server.py` | N/A (referenced) | BridgeServer |
| 11 | `web_ui/frontend/src/App.tsx` | 1-200+ | React app state machine |
| 12 | `web_ui/frontend/src/bridge.ts` | 1-117 | WebSocket client |
| 13 | `web_ui/frontend/src/types.ts` | 1-50 | TypeScript types |
| 14 | `tools/stt.py` | 1-300+ | `STTEngine` — Faster-Whisper, recording, voice match |
| 15 | `core/voice_recognition.py` | 1-200+ | `VoiceFingerprintEngine` — Resemblyzer |

---

## Current Native Mic/STT Path

```
[User clicks MIC button]
    │
    ▼
ui/controller_actions.py:on_mic_toggle(controller)
    │
    ├── controller.mic_state = "recording"
    ├── DPG: MIC button → STOP button (red)
    ├── controller.set_status("Listening...")
    ├── engine = get_stt_engine()          # tools/stt.py singleton
    ├── engine.set_active_voice_profile(
    │       profile.user_id,
    │       is_unknown=profile.is_unknown)
    └── engine.start_recording()           # sounddevice InputStream
                                               │
[User clicks STOP button]                      │
    │                                          ▼
    ▼                              Audio chunks accumulated in
reset_mic_ui(controller)           engine._audio_data[] via callback
    │
    ├── controller.set_status("Transcribing...")
    ├── engine.stop_recording()
    │       │
    │       ├── stream.stop(), stream.close()
    │       ├── Concatenate audio chunks (numpy)
    │       ├── RMS gate (skip if below min_rms)
    │       ├── Downsample to 16kHz if needed
    │       ├── faster_whisper.transcribe() → text
    │       └── voice recognition hook:
    │               ├── core.voice_recognition.extract_embedding()
    │               ├── engine.evaluate_match() or engine.match()
    │               └── engine._last_voice_match = (user, score, detail)
    │
    ├── _apply_voice_identity_match(controller, engine)
    │       ├── engine.consume_last_voice_match()
    │       ├── Voice drift tracker logic
    │       ├── Admin drift protection (revoke on drift)
    │       ├── user_runtime.switch_active_user() if needed
    │       └── _apply_active_user_switch() if user changed
    │
    ├── controller._pending_input_modality = "voice"
    ├── DPG: set input_box value to transcript text
    └── controller.on_send() → do_generate_stream() → run_agent_loop()
```

### Key source locations

| Step | File | Function | Line |
|------|------|----------|------|
| MIC toggle | `ui/controller_actions.py` | `on_mic_toggle()` | 492 |
| Recording start | `tools/stt.py` | `STTEngine.start_recording()` | 104 |
| Recording stop | `tools/stt.py` | `STTEngine.stop_recording()` | 174 |
| Voice embedding | `core/voice_recognition.py` | `VoiceFingerprintEngine.extract_embedding()` | 130 |
| Voice match | `core/voice_recognition.py` | `VoiceFingerprintEngine.evaluate_match()` | 186 |
| Identity apply | `ui/controller_actions.py` | `_apply_voice_identity_match()` | 284 |
| User switch | `ui/controller_actions.py` | `_apply_active_user_switch()` | 209 |
| Input modality | `ui/controller.py` | `build_orchestrator_config()` | ~870 |
| Generate stream | `ui/controller_actions.py` | `do_generate_stream()` | ~520 |

---

## Current Web Typed-Input Path

```
Frontend input box
    │
    ▼
bridge.sendAction("send_message", {text})
    │
    ▼
ws://127.0.0.1:8787/ws  (BridgeServer)
    │
    ▼
action_queue.put(("send_message", {text}))
    │
    ▼
run_web() loop reads action_queue
    │
    ▼
_dispatch_web_action("send_message", payload)
    │
    ▼
controller.submit_user_text(text)
    ├── chat_append("user", text)
    ├── persist_turn("user", text)        # if not unknown/incognito
    ├── show_thinking_placeholder()
    └── threading.Thread(do_generate_stream)
                │
                ▼
        run_agent_loop(build_orchestrator_config())
            input_modality="typed" (default)
            voice_identity_notice="" (default)
```

### Current Web mic placeholder

`ui/controller.py:_dispatch_web_action()` line 1234:

```python
elif action_name == "mic_toggle":
    self.ui_queue.put(
        ("chat_append", {
            "role": "system",
            "content": "[UI] Microphone is not available in Web UI mode.",
        })
    )
```

This is what Phase 13+ replaces.

---

## Architecture Options

### Option A: Browser captures raw PCM16, streams chunks to backend via WebSocket binary frames

| Dimension | Assessment |
|-----------|-----------|
| **How** | `AudioContext` + `ScriptProcessorNode` / `AudioWorklet` captures real-time PCM16. Binary WebSocket frames stream chunks to backend. Backend accumulates in ring buffer. |
| **Offline/local** | Yes — all STT and voice identity on backend. |
| **Complexity** | **High.** Binary frame protocol, chunk ordering, reconnection mid-recording, endianness, sample rate negotiation, ring buffer management. |
| **Latency** | Low for recording start; transcription only at end (same as native). |
| **Audio format risks** | High — browsers output Float32 at 48kHz; need to convert to Int16 at 16kHz. Web Audio API has autoplay policy gotchas. |
| **Auth/identity** | Preserved — backend runs full pipeline. |
| **Testability** | Hard — binary frames, real-time timing. |
| **Safety rules** | Preserved. |
| **Verdict** | Overkill for v1. Native Piper is utterance-based, not streaming-ASR. |

### Option B: Browser records full utterance via MediaRecorder, sends as base64 WAV/WebM

| Dimension | Assessment |
|-----------|-----------|
| **How** | `MediaRecorder` API captures into Blob (WebM/OGG or WAV via polyfill). On stop, blob → `FileReader.readAsDataURL()` → base64 JSON action frame. Backend decodes → PCM16 → Faster-Whisper. |
| **Offline/local** | Yes — all STT and voice identity on backend. |
| **Complexity** | **Medium.** MediaRecorder is simple. Base64 decode on backend. Need audio format decoder (webm/opus → numpy). WAV would need WAV encoder on frontend or WAV polyfill. |
| **Latency** | Medium — full round-trip before transcription starts. Acceptable for utterance-based interaction. |
| **Audio format risks** | Medium — MediaRecorder outputs WebM/Opus natively on most browsers. Need Opus decoder on backend (`soundfile` or `ffmpeg`). Could use ` RecordRTC` WAV encoder for direct WAV output. |
| **Auth/identity** | Preserved — backend runs full pipeline. |
| **Testability** | Good — deterministic request/response. Can mock base64 audio. |
| **Safety rules** | Preserved. |
| **Verdict** | **Best for v1.** Matches native utterance model. Simple protocol. |

### Option C: Browser controls existing native mic (no browser audio capture)

| Dimension | Assessment |
|-----------|-----------|
| **How** | Frontend sends `mic_start` / `mic_stop` actions. Backend's existing `sounddevice` InputStream records. |
| **Offline/local** | Yes. |
| **Complexity** | Low for protocol. But: browser and native Python can't share the same mic hardware on all OSes. |
| **Latency** | Same as native. |
| **Audio format risks** | None — uses existing sounddevice path. |
| **Auth/identity** | Preserved. |
| **Testability** | Good for protocol; bad for hardware contention. |
| **Safety rules** | Preserved. |
| **Critical flaw** | **Hardware contention.** If the browser is the app (electron/tauri/pywebview), native mic works. If the browser is Chrome/Edge, Python's `sounddevice` and the browser may fight for the same mic. On Windows WASAPI exclusive mode makes this worse. |
| **Verdict** | **Not viable until desktop wrapper lands.** The whole point of Web UI is that Piper runs in the browser. |

### Option D: Browser-side Web Speech API

| Dimension | Assessment |
|-----------|-----------|
| **How** | `webkitSpeechRecognition` or `SpeechRecognition` in browser. |
| **Offline/local** | **No.** Chromium's speech recognition requires Google cloud servers unless running a custom speech-dispatcher (which Piper does not). |
| **Auth/identity** | **Bypassed.** Voice identity runs on backend. If browser does STT, the audio never reaches Piper's voice recognition engine. |
| **Verdict** | **Rejected per user constraints.** Not local/offline. Bypasses voice identity. |

### Summary

| Option | Offline | Complexity | Latency | Identity | Desktop Wrapper Needed | Verdict |
|--------|---------|-----------|---------|----------|----------------------|---------|
| A (streaming PCM) | Yes | High | Low | Preserved | No | Future optimization |
| **B (utterance base64)** | **Yes** | **Medium** | **Medium** | **Preserved** | **No** | **Recommended for v1** |
| C (native mic control) | Yes | Low | Low | Preserved | Yes (hardware contention) | After desktop wrapper |
| D (Web Speech API) | **No** | Low | Low | **Bypassed** | No | **Rejected** |

---

## Recommended Phase 14 Architecture

**Option B (utterance-based): Backend owns STT + voice identity. Browser captures audio via MediaRecorder and sends complete utterance as base64.**

### High-level flow

```
[User clicks MIC button in Web UI]
    │
    ▼
Frontend: MediaRecorder.start()
    ├── Request mic permission (if not granted)
    ├── Show "Listening..." state locally
    └── Disable MIC button, show STOP button

[User clicks STOP button]
    │
    ▼
Frontend: MediaRecorder.stop()
    ├── Blob created (audio/webm)
    ├── FileReader.readAsDataURL(blob) → "data:audio/webm;base64,..."
    └── bridge.sendAction("mic_transcript", {
            audio: "base64...",
            format: "webm",
            sample_rate_hint: 48000
        })

[Backend receives]
    │
    ▼
BridgeServer → action_queue
    │
    ▼
run_web() loop
    │
    ▼
_dispatch_web_action("mic_transcript", payload)
    │
    ▼
_handle_mic_transcript(payload)
    ├── Decode base64 → bytes
    ├── Decode WebM/Opus → PCM16 numpy array (soundfile or ffmpeg)
    ├── STTEngine.transcribe_buffer(audio_np, sr)
    │       ├── Faster-Whisper transcribe → text
    │       └── Voice recognition: extract_embedding + match
    ├── _apply_voice_identity_match(controller, engine)
    │       ├── User switch if needed
    │       └── Admin drift protection
    ├── controller._pending_input_modality = "voice"
    └── if text: controller.submit_user_text(text)
                   └── do_generate_stream() → run_agent_loop()
```

### Why this preserves Piper's safety model

1. **No identity bypass** — voice recognition runs on backend with same Resemblyzer engine
2. **No auth rule changes** — `_apply_voice_identity_match()` is called identically to native path
3. **No wake/sleep changes** — mic_state tracked on frontend; backend doesn't need wake-word logic
4. **No cloud APIs** — all processing local
5. **Same modality tracking** — `_pending_input_modality = "voice"` set before `submit_user_text()`
6. **Same transcript path** — transcript enters via `submit_user_text()` → `chat_append("user", text)` → `persist_turn()` — identical to typed input path after STT

---

## Required Bridge Protocol Changes

### New action: `mic_transcript`

```typescript
// Frontend → Backend
interface MicTranscriptAction {
  frame: "action";
  action: "mic_transcript";
  payload: {
    audio: string;           // base64-encoded audio data (without data: URI prefix)
    format: "webm" | "wav";  // Audio container format
    sample_rate_hint: number; // Original capture sample rate (e.g. 48000)
  };
  requestId: string;
  timestamp: string;
}
```

### New events: mic status

```typescript
// Backend → Frontend
interface MicStatusEvent {
  frame: "event";
  kind: "mic.status";
  sourceKind: "mic.status";
  payload: {
    state: "idle" | "listening" | "transcribing" | "error";
    error?: string;          // Present only when state="error"
  };
  timestamp: string;
  requestId: string;
}

// Deprecated placeholder replacement
// When mic_transcript action arrives:
//   1. Emit mic.status {state: "transcribing"}
//   2. Run STT
//   3. On success: mic.status {state: "idle"}
//   4. On error:   mic.status {state: "error", error: "..."}
//   5. Chat append "user" message with transcript (via normal chat.append flow)
```

### No binary frames

For v1, all audio is base64 inside JSON action frames. Binary frames are deferred to a future optimization phase.

### Files to change

| File | Change |
|------|--------|
| `web_ui/bridge/message_schema.py` | Add `"mic_transcript"` to `KNOWN_ACTION_NAMES` |
| `web_ui/bridge/adapter.py` | Add `mic_transcript` action parsing to `parse_action_frame()` (if any payload normalization needed) |
| `web_ui/bridge/test_adapter.py` | Add test for `mic_transcript` action parsing |

---

## Required Frontend Changes

### New state

```typescript
// In App.tsx state
type MicState = "idle" | "requesting_permission" | "listening" | "transcribing" | "error";

const [micState, setMicState] = useState<MicState>("idle");
const [micError, setMicError] = useState<string>("");
const mediaRecorderRef = useRef<MediaRecorder | null>(null);
const audioChunksRef = useRef<Blob[]>([]);
```

### New handlers

```typescript
const handleMicToggle = useCallback(async () => {
  if (micState === "idle") {
    // Start recording
    try {
      setMicState("requesting_permission");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      audioChunksRef.current = [];

      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) audioChunksRef.current.push(ev.data);
      };

      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const reader = new FileReader();
        reader.onloadend = () => {
          const base64 = String(reader.result).split(",")[1]; // Strip data:audio/webm;base64, prefix
          bridgeRef.current?.sendAction("mic_transcript", {
            audio: base64,
            format: "webm",
            sample_rate_hint: 48000,
          });
        };
        reader.readAsDataURL(blob);
        stream.getTracks().forEach(t => t.stop());
      };

      mediaRecorderRef.current = recorder;
      recorder.start(100); // 100ms timeslice for ondataavailable
      setMicState("listening");
    } catch (err) {
      setMicState("error");
      setMicError(String(err));
    }
  } else if (micState === "listening") {
    // Stop recording
    mediaRecorderRef.current?.stop();
    setMicState("transcribing");
  }
}, [micState]);
```

### Event handler addition

```typescript
// In handleFrame switch:
case "mic.status": {
  const p = payload as { state: MicState; error?: string };
  setMicState(p.state);
  if (p.error) setMicError(p.error);
  break;
}
```

### UI rendering

```typescript
// Mic button shows state:
// idle       → "MIC" button (enabled)
// listening  → "STOP" button (red, pulsing indicator)
// transcribing → "..." (disabled, spinner)
// error      → "MIC" button (red outline, hover shows error)

<button
  onClick={handleMicToggle}
  disabled={micState === "transcribing" || micState === "requesting_permission"}
  className={micState === "listening" ? "btn-stop" : micState === "error" ? "btn-error" : "btn-mic"}
>
  {micState === "listening" ? "STOP" :
   micState === "transcribing" ? "..." :
   micState === "error" ? "ERR" : "MIC"}
</button>
```

### Stop / New Session / Restart interactions

| Action | Mic behavior |
|--------|-------------|
| `stop` | If `micState === "listening"`, call `mediaRecorder.stop()` first, then discard the audio (don't send). Set `micState = "idle"`. |
| `new_session` | Same as `stop` — abort recording, discard audio. |
| `restart_piper` | Same as `stop`. Bridge disconnect handles cleanup. |
| Reconnect | If `micState === "listening"` during reconnect, audio chunks are still in memory. On reconnect, send `mic_transcript`. If reconnect fails, discard. |

### Permission handling

- First click triggers `navigator.mediaDevices.getUserMedia({ audio: true })`
- Browser shows native permission prompt
- If denied: `micState = "error"`, `micError = "Microphone permission denied"`
- If granted: proceed to recording
- On subsequent clicks: permission is cached by browser (no re-prompt)

### Files to change

| File | Change |
|------|--------|
| `web_ui/frontend/src/App.tsx` | Add `micState`, `micError`, `handleMicToggle`, MediaRecorder refs, mic UI rendering |
| `web_ui/frontend/src/types.ts` | Add `MicState` type (optional) |

---

## Required Backend Changes

### 1. `web_ui/bridge/message_schema.py`

```python
KNOWN_ACTION_NAMES: set[str] = {
    "send_message",
    "stop",
    "new_session",
    "clear_chat",
    "mic_toggle",          # Keep for backward compat (placeholder)
    "mic_transcript",      # NEW — replaces mic_toggle for real audio
    # ... rest unchanged
}
```

### 2. `web_ui/bridge/adapter.py`

No payload normalization needed for `mic_transcript` — base64 string passes through as-is. `parse_action_frame()` will accept it automatically once added to `KNOWN_ACTION_NAMES`.

Add test:
```python
def test_parse_mic_transcript_action(self) -> None:
    frame = json.dumps({"action": "mic_transcript", "payload": {
        "audio": "base64testdata", "format": "webm", "sample_rate_hint": 48000
    }})
    name, payload = parse_action_frame(frame)
    assert name == "mic_transcript"
    assert payload["audio"] == "base64testdata"
    assert payload["format"] == "webm"
```

### 3. `ui/controller.py` — `_dispatch_web_action`

Replace the `mic_toggle` placeholder with `mic_transcript` handling:

```python
elif action_name == "mic_toggle":
    # Keep the placeholder for frontend compat during transition
    self.ui_queue.put(("chat_append", {
        "role": "system",
        "content": "[UI] Use the microphone button in the Web UI toolbar.",
    }))

elif action_name == "mic_transcript":
    self._handle_mic_transcript(payload)
```

Add `_handle_mic_transcript` method:

```python
def _handle_mic_transcript(self, payload: dict) -> None:
    """Handle audio transcript from Web UI microphone.

    Pipeline:
    1. Decode base64 audio
    2. Decode WebM/Opus to PCM16
    3. Run STT + voice identity
    4. Inject transcript as user message if text exists
    """
    import base64
    import tempfile
    import numpy as np

    self.ui_queue.put(("mic.status", {"state": "transcribing"}))

    try:
        audio_b64 = str(payload.get("audio", "")).strip()
        audio_format = str(payload.get("format", "webm")).strip().lower()

        if not audio_b64:
            self.ui_queue.put(("mic.status", {
                "state": "error",
                "error": "Empty audio payload",
            }))
            return

        # Decode base64
        try:
            audio_bytes = base64.b64decode(audio_b64, validate=True)
        except Exception:
            self.ui_queue.put(("mic.status", {
                "state": "error",
                "error": "Invalid base64 audio data",
            }))
            return

        # Decode audio to numpy PCM16
        audio_np: np.ndarray | None = None
        sample_rate: int = 16000

        try:
            import soundfile as sf
            with tempfile.NamedTemporaryFile(suffix=f".{audio_format}", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            audio_np, file_sr = sf.read(tmp_path, dtype="float32")
            sample_rate = int(file_sr)
            Path(tmp_path).unlink(missing_ok=True)
        except ImportError:
            self.ui_queue.put(("mic.status", {
                "state": "error",
                "error": "soundfile not installed; cannot decode audio",
            }))
            return
        except Exception as exc:
            self.ui_queue.put(("mic.status", {
                "state": "error",
                "error": f"Audio decode failed: {exc}",
            }))
            return

        # Run STT
        from tools.stt import get_stt_engine
        engine = get_stt_engine()

        # Set active voice profile
        try:
            profile = self.user_runtime.active_profile()
            if hasattr(engine, "set_active_voice_profile"):
                engine.set_active_voice_profile(
                    profile.user_id,
                    is_unknown=getattr(profile, "is_unknown", False),
                )
        except Exception:
            pass

        # Transcribe
        transcript = ""
        try:
            # Need to add this method to STTEngine (see below)
            transcript = engine.transcribe_buffer(audio_np, sample_rate)
        except Exception as exc:
            self.ui_queue.put(("mic.status", {
                "state": "error",
                "error": f"Transcription failed: {exc}",
            }))
            return

        # Apply voice identity
        try:
            from ui.controller_actions import _apply_voice_identity_match
            _apply_voice_identity_match(self, engine)
        except Exception:
            pass

        self.ui_queue.put(("mic.status", {"state": "idle"}))

        # Set voice modality and submit
        if transcript:
            self._pending_input_modality = "voice"
            self.submit_user_text(transcript)
        else:
            self.ui_queue.put(("chat_append", {
                "role": "system",
                "content": "[No speech detected]",
            }))

    except Exception as exc:
        self.ui_queue.put(("mic.status", {
            "state": "error",
            "error": f"Unexpected error: {exc}",
        }))
```

### 4. `tools/stt.py` — add `transcribe_buffer` method

```python
def transcribe_buffer(self, audio_data: "np.ndarray", sample_rate: int = 16000) -> str:
    """Transcribe pre-recorded audio buffer (non-interactive).

    Used by Web UI mic path where audio is captured by the browser
    and decoded to PCM16 before reaching the STT engine.

    Also runs voice recognition if enabled.
    """
    import numpy as np

    if audio_data.size == 0:
        return ""

    # Ensure float32 mono
    audio_float = np.asarray(audio_data, dtype=np.float32)
    if audio_float.ndim > 1:
        audio_float = audio_float.mean(axis=1)

    # Downsample to 16kHz if needed
    source_sr = int(sample_rate)
    if source_sr != 16000:
        source_idx = np.arange(audio_float.shape[0], dtype=np.float32)
        target_len = max(1, int(round(audio_float.shape[0] * 16000 / source_sr)))
        target_idx = np.linspace(0, audio_float.shape[0] - 1, num=target_len, dtype=np.float32)
        audio_float = np.interp(target_idx, source_idx, audio_float).astype(np.float32)

    # Store for voice recognition hook
    self._last_audio_samples = audio_float.copy()

    # Transcribe
    try:
        self._load_model()
        segments, info = self.model.transcribe(
            audio_float,
            beam_size=5,
            language="en",
            condition_on_previous_text=False,
        )
        text = "".join(seg.text for seg in segments).strip()
    except Exception as exc:
        _LOG.warning("[STT] Transcription error: %s", exc)
        return ""

    # Voice recognition hook (same as stop_recording)
    try:
        from core.voice_recognition import get_voice_engine
        from config import CFG

        if CFG.VOICE_RECOGNITION_ENABLED:
            voice_engine = get_voice_engine()
            if voice_engine.available():
                embedding = voice_engine.extract_embedding(self._last_audio_samples)
                if embedding is not None:
                    current_user_id = str(getattr(self, "_active_voice_user_id", "") or "").strip()
                    current_is_unknown = bool(getattr(self, "_active_voice_user_unknown", True))

                    if (current_user_id and not current_is_unknown
                            and voice_engine.is_enrolling(current_user_id)):
                        completed = voice_engine.add_enrollment_sample(current_user_id, embedding)
                        _log_voice_debug(f"enrollment_sample user={current_user_id} completed={completed}")
                    elif hasattr(voice_engine, "evaluate_match"):
                        try:
                            decision = voice_engine.evaluate_match(embedding, first_turn=current_is_unknown)
                        except TypeError:
                            decision = voice_engine.evaluate_match(embedding)
                        mode = "unknown_eval" if current_is_unknown else "strict_eval"
                        _log_voice_debug(_voice_decision_debug_line(
                            mode=mode,
                            active_user=current_user_id or "unknown",
                            decision=decision,
                        ))
                        self._last_voice_match = (
                            getattr(decision, "final_user", "") or None,
                            float(getattr(decision, "best_score", 0.0) or 0.0),
                            {
                                "best_user": str(getattr(decision, "best_user", "") or ""),
                                "best_score": float(getattr(decision, "best_score", 0.0) or 0.0),
                                "second_score": float(getattr(decision, "second_score", 0.0) or 0.0),
                                "margin": float(getattr(decision, "margin", 0.0) or 0.0),
                                "best_is_admin": bool(getattr(decision, "best_is_admin", False)),
                                "threshold": float(getattr(decision, "threshold", 0.0) or 0.0),
                                "margin_threshold": float(getattr(decision, "margin_threshold", 0.0) or 0.0),
                                "final_decision": str(getattr(decision, "decision", "") or ""),
                                "reason": str(getattr(decision, "reason", "") or ""),
                            },
                        )
    except Exception as exc:
        _log_voice_debug(f"voice_recognition error: {type(exc).__name__}: {exc}")

    return text
```

**Note on audio format:** `soundfile` can read WebM/Opus if `libsndfile` is built with Ogg/Opus support, which is the case on most modern Linux but **not guaranteed on Windows**. If `soundfile` fails on Windows, add a fallback to:
1. Save the file and call `ffmpeg -i input.webm -ar 16000 -ac 1 -f wav -` to stdout → read with `soundfile`
2. Or add `pydub` dependency with `ffmpeg` binary bundled in Piper's `scripts/` or `models/` directory

This is a runtime adaptation detail, not an architecture change.

### Files to change — backend

| File | Change |
|------|--------|
| `web_ui/bridge/message_schema.py` | Add `"mic_transcript"` to `KNOWN_ACTION_NAMES` |
| `web_ui/bridge/adapter.py` | No code change needed; add test |
| `web_ui/bridge/test_adapter.py` | Add `mic_transcript` action parsing test |
| `ui/controller.py` | Replace `mic_toggle` placeholder with `mic_transcript` dispatch; add `_handle_mic_transcript()` |
| `tools/stt.py` | Add `transcribe_buffer(audio_data, sample_rate)` method |
| `requirements.txt` | Add `soundfile` (if not already present) |

---

## Auth / Voice Identity / Safety Notes

### What stays exactly the same

| Concern | Native behavior | Web mic behavior | Verdict |
|---------|----------------|------------------|---------|
| **Voice recognition engine** | Resemblyzer (`core/voice_recognition.py`) | Same engine, same embeddings | Preserved |
| **Enrollment** | Automatic after identity known | Same — enrollment triggered by backend | Preserved |
| **Drift tracking** | `_voice_drift_tracker` on controller | Same controller state | Preserved |
| **Admin drift protection** | Admin revoked on voice drift after N turns | Same logic in `_apply_voice_identity_match` | Preserved |
| **Unknown user handling** | Unknown sessions not persisted | Same — `persist_turn` checks `is_unknown` | Preserved |
| **Input modality** | `"voice"` sent to orchestrator | Same — `_pending_input_modality = "voice"` | Preserved |
| **Voice identity notice** | Injected into orchestrator context | Same — `_apply_voice_identity_match` sets `_pending_voice_identity_notice` | Preserved |
| **Access tier** | Admin/public/unknown | Same — derived from `user_runtime.is_admin_unlocked()` | Preserved |

### What the frontend must NOT do

- **Must NOT** do speech-to-text in the browser (rejects Option D)
- **Must NOT** send raw PCM chunks in v1 (deferred to future)
- **Must NOT** bypass voice identity by sending pre-transcribed text
- **Must NOT** expose the transcript before backend confirms it (no "live transcript preview")
- **Must NOT** cache microphone permission state across sessions (browser handles this)

### What the backend must NOT do

- **Must NOT** remove or change `_apply_voice_identity_match()`
- **Must NOT** change `submit_user_text()` signature or behavior
- **Must NOT** change `build_orchestrator_config()` voice identity handling
- **Must NOT** use cloud speech APIs
- **Must NOT** store audio files permanently (temp files only, cleaned up)

---

## Test Plan

### 1. Adapter action parsing

```python
# web_ui/bridge/test_adapter.py
class TestMicTranscriptAction:
    def test_valid_webm(self):
        frame = json.dumps({"action": "mic_transcript", "payload": {
            "audio": "GkXfo59ChoEBQveBAULygQRC84EIQoKIbW90aW9u", "format": "webm", "sample_rate_hint": 48000
        }})
        name, payload = parse_action_frame(frame)
        assert name == "mic_transcript"
        assert payload["format"] == "webm"
        assert payload["sample_rate_hint"] == 48000

    def test_invalid_action_rejected(self):
        # mic_toggle is still known (placeholder), but mic_transcript replaces it
        frame = json.dumps({"action": "mic_transcript", "payload": {}})
        name, _ = parse_action_frame(frame)
        assert name == "mic_transcript"
```

### 2. Bridge protocol

```python
# web_ui/bridge/test_server.py (or new test file)
# Verify mic_transcript action is accepted and forwarded to action_queue
```

### 3. Controller dispatch

```python
# New: tests/web_ui/test_mic_dispatch.py
class TestMicTranscriptDispatch:
    def test_empty_audio_returns_error(self, mock_controller):
        # _handle_mic_transcript with empty audio → mic.status error

    def test_invalid_base64_returns_error(self, mock_controller):
        # _handle_mic_transcript with bad base64 → mic.status error

    def test_transcribe_buffer_called(self, mock_controller, monkeypatch):
        # Mock STTEngine.transcribe_buffer to return "hello"
        # Verify submit_user_text called with "hello"
        # Verify _pending_input_modality == "voice"

    def test_voice_identity_applied(self, mock_controller, monkeypatch):
        # Mock _apply_voice_identity_match
        # Verify it is called after transcription

    def test_no_speech_shows_system_message(self, mock_controller, monkeypatch):
        # Mock transcribe_buffer to return ""
        # Verify chat_append "[No speech detected]" queued
```

### 4. Audio state machine

```python
class TestMicStateMachine:
    def test_idle_to_listening(self):
        # mic_state idle → recording → "Listening..." status

    def test_listening_to_transcribing(self):
        # stop → transcribing → "Transcribing..." status

    def test_transcribing_to_idle(self):
        # success → idle

    def test_transcribing_to_error(self):
        # failure → error + error message
```

### 5. No DPG regression

```python
# Existing test: TestWebDispatchNeverCallsPumpUiQueue
# Verify _handle_mic_transcript never calls dpg.*
```

### 6. No cloud dependency

```python
# Verify no imports of google.cloud, azure.cognitiveservices, etc.
# Verify STTEngine still uses faster_whisper locally
```

### 7. No duplicate user messages

```python
# Verify submit_user_text is called exactly once per mic_transcript
# Verify chat_append "user" is called exactly once
```

### 8. Transcript sanitization

```python
# Verify [ROUTER], [RECALL:...] markers do not appear in mic transcripts
# (adapter already handles this for stream deltas; mic uses submit_user_text which goes through chat_append — no marker injection risk)
```

### 9. Frontend typecheck/build

```bash
cd web_ui/frontend && npm run build
# Must succeed with 0 errors
# micState integration must not increase bundle size significantly
```

### 10. Manual smoke checklist

| # | Check | Expected |
|---|-------|----------|
| 1 | Start Piper with `PIPER_WEB_UI_ENABLED=1` | Boot succeeds |
| 2 | Open `http://127.0.0.1:8787` | Page loads, chat visible |
| 3 | Click MIC button | Browser permission prompt |
| 4 | Grant permission | Button changes to STOP, "Listening..." appears |
| 5 | Speak, then click STOP | Status changes to "Transcribing..." then IDLE |
| 6 | Transcript appears in chat | User message with spoken text |
| 7 | Assistant responds | Streaming response appears |
| 8 | Voice identity check | If enrolled user speaks, user switch fires correctly |
| 9 | Unknown speaker | If unknown speaks, remains unknown; transcript appears |
| 10 | No speech | "[No speech detected]" system message |
| 11 | Click STOP during assistant response | Assistant stops; mic status stays idle |
| 12 | New session during listening | Recording aborts; mic resets to idle |
| 13 | DearPyGui mode | `PIPER_WEB_UI_ENABLED=0` — native MIC still works as before |

---

## Non-Goals

These are explicitly out of scope for Phase 14:

| Item | Reason | Deferred to |
|------|--------|-------------|
| Real-time streaming ASR (chunk-based) | Overkill for v1; native Piper is utterance-based | Phase 15+ |
| Binary WebSocket frames | Base64 works; binary is optimization | Phase 15+ |
| Web Speech API fallback | Cloud-dependent; violates offline constraint | Never |
| TTS in browser | TTS stays in Python (`tools/tts.py` on port 8765) | Phase 16+ |
| Wake-word detection in browser | Wake/sleep is a backend concept; no wake word in Web UI v1 | Phase 15+ |
| Desktop wrapper (Tauri/pywebview) | Deferred per migration guide | After parity |
| DearPyGui retirement | Only after full parity proven | Far future |
| Audio file persistence | Temp files only; no recording storage | Never |
| Multi-language STT | English only (same as native) | If native adds it |
| Noise cancellation in browser | Backend STT handles noise; no frontend processing | Never |

---

## Open Questions

| # | Question | Impact | Recommendation |
|---|----------|--------|----------------|
| 1 | **Does `soundfile` read WebM/Opus on Windows?** | If not, need ffmpeg fallback | Test on target Windows machine. Add `pydub` + bundled `ffmpeg.exe` fallback if needed. |
| 2 | **MediaRecorder browser compatibility?** | `audio/webm` is supported in Chromium, Firefox, Safari | Test all target browsers. Add WAV encoder fallback (RecordRTC) if needed. |
| 3 | **Audio file size for typical utterance?** | Affects memory pressure and WS frame size | Estimate: 5s at 48kHz mono Opus ≈ 15-30KB base64 ≈ 20-40KB JSON. Well within WS limits. |
| 4 | **Should the frontend show transcript preview before sending?** | UX question | No for v1 — same as native (no preview). Backend returns transcript via normal chat.append flow. |
| 5 | **What happens if user speaks while assistant is generating?** | Race condition | `submit_user_text` checks `has_active_operations()` and returns early. Frontend should disable MIC when `status != "IDLE"`. |
| 6 | **Should the MIC button reflect backend `mic.status` events?** | State sync | Yes — frontend drives its own state but also listens to `mic.status` events for backend-initiated resets (e.g., error). |

---

## Open Questions — Resolution Path

Before Phase 14 implementation begins, resolve:

1. **Audio format decision:** WebM (browser default) vs. WAV (simpler backend decode). WebM is preferred because it uses native MediaRecorder without additional frontend libraries. The backend decode question (Q1) determines if a WAV encoder is needed on the frontend.

2. **MIC button availability during generation:** Should MIC be disabled when `status != "IDLE"`? The native code checks `boot_ready && !has_active_operations()` in `on_mic_toggle`. The Web UI should mirror this: MIC button disabled when status is not IDLE.

3. **Recording duration limit:** Should there be a max recording time (e.g., 30s) before auto-stop? Native Piper has no limit but relies on the user clicking STOP. Same model for Web UI v1.

---

## Phase 14 Commit Plan

```
commit 1: feat(web-ui): add mic_transcript to action schema and adapter tests
  - message_schema.py: + mic_transcript
  - test_adapter.py: + mic_transcript parsing test

commit 2: feat(stt): add transcribe_buffer method for pre-recorded audio
  - tools/stt.py: + transcribe_buffer()
  - requirements.txt: + soundfile (if needed)

commit 3: feat(web-ui): add backend mic_transcript handler with voice identity
  - ui/controller.py: _handle_mic_transcript(), _dispatch_web_action update
  - mic.status event emission

commit 4: feat(web-ui): add frontend MediaRecorder mic capture
  - App.tsx: micState, handleMicToggle, MediaRecorder lifecycle
  - bridge.ts: no changes (sendAction already supports mic_transcript payload)

commit 5: test(web-ui): add mic_transcript integration tests
  - test_mic_dispatch.py
  - test_mic_state_machine.py

commit 6: docs(web-ui): update migration guide for mic/STT integration
  - docs/specs/piper-web-ui-migration-guide.md
  - web_ui/bridge/CONTRACT.md: + mic.status event, mic_transcript action
```

---

## Cited File Paths and Functions

| Reference | File | Function/Class | Line |
|-----------|------|---------------|------|
| Native MIC toggle | `ui/controller_actions.py` | `on_mic_toggle()` | 492 |
| STT engine | `tools/stt.py` | `STTEngine.start_recording()` | 104 |
| STT stop | `tools/stt.py` | `STTEngine.stop_recording()` | 174 |
| STT singleton | `tools/stt.py` | `get_stt_engine()` | ~300 |
| Voice recognition | `core/voice_recognition.py` | `VoiceFingerprintEngine` | 37 |
| Voice embedding | `core/voice_recognition.py` | `extract_embedding()` | 130 |
| Voice match eval | `core/voice_recognition.py` | `evaluate_match()` | 186 |
| Identity apply | `ui/controller_actions.py` | `_apply_voice_identity_match()` | 284 |
| User switch | `ui/controller_actions.py` | `_apply_active_user_switch()` | 209 |
| Drift tracker | `ui/controller_actions.py` | `_voice_drift_tracker()` | 92 |
| Web dispatch | `ui/controller.py` | `_dispatch_web_action()` | 1224 |
| Web runtime | `ui/controller.py` | `run_web()` | 1332 |
| Submit text | `ui/controller.py` | `submit_user_text()` | 1140 |
| Generate stream | `ui/controller_actions.py` | `do_generate_stream()` | ~520 |
| Orchestrator config | `ui/controller.py` | `build_orchestrator_config()` | ~870 |
| Event pump DPG | `ui/controller_queue.py` | `pump_ui_queue()` | 31 |
| Event pump Web | `ui/controller_queue.py` | `pump_ui_queue_web()` | (see controller_queue.py) |
| Adapter | `web_ui/bridge/adapter.py` | `parse_action_frame()` | (in adapter.py) |
| Action schema | `web_ui/bridge/message_schema.py` | `KNOWN_ACTION_NAMES` | ~78 |
| Event schema | `web_ui/bridge/message_schema.py` | `KNOWN_EVENT_KINDS` | ~24 |
| Bridge client | `web_ui/frontend/src/bridge.ts` | `PiperBridge.sendAction()` | 84 |
| Frontend app | `web_ui/frontend/src/App.tsx` | `App()` | 23 |
| Frontend types | `web_ui/frontend/src/types.ts` | `ActionFrame` | ~19 |
| Config Web UI | `config.py` | `WEB_UI_ENABLED` | 843 |
| Config voice | `config.py` | `VOICE_RECOGNITION_ENABLED` | 690 |
| Config STT | `config.py` | `PIPER_STT_MIN_RMS` | (env var) |
| Config enrollment | `config.py` | `VOICE_ENROLLMENT_TURNS` | 694 |
| Entry point | `app.py` | `main()` | 178 |
| Migration guide | `docs/specs/piper-web-ui-migration-guide.md` | (document) | 1 |
| Contract | `web_ui/bridge/CONTRACT.md` | (document) | 1 |
