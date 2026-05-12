# Piper Config Reference

Status: Active reference

This document summarizes important Piper config settings, defaults, and risk notes from [`config.py`](../config.py).

Rules for this document:
- do not treat it as a substitute for reading `config.py`
- do not assume every setting is safe to change at runtime
- when a value is computed dynamically or depends on local files, it is marked accordingly

## How Config Works

- Base values live in [`config.py`](../config.py).
- Many settings can be overridden by environment variables such as `PIPER_*`.
- Runtime overrides may also be loaded from `data/state/config_override.json` through `LiveConfig.reload_if_stale()`.
- `config_override.json` intentionally accepts only scalar-style overrides.
- `ROOT_DIR`, `DATA_DIR`, `MEMORY_PATH`, and `LLAMA_SERVER_REASONING_BUDGET` are treated as restart-only and are not hot-reloaded through that override file.

## 1. Identity / Privacy / Voice Verification

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `VOICE_RECOGNITION_ENABLED` | `True` | Enables passive voice identity logic | Disabling forces identity back to non-voice paths; enabling without calibration can still misclassify speakers | Disable only when debugging or if voice identity is known broken on a machine | `python scripts/voice_identity_inference_smoke_test.py --json` |
| `VOICE_SIMILARITY_THRESHOLD_HIGH` | `0.74` | Public-speaker acceptance threshold | Too low increases false matches; too high keeps known public speakers stuck as unknown | Adjust only after real speaker evidence | `python scripts/voice_identity_inference_smoke_test.py --json` |
| `VOICE_SIMILARITY_THRESHOLD_LOW` | `0.58` | Low-confidence floor below which voice stays unknown | Too low risks weak matches being treated as plausible | Change only with calibration evidence | `python scripts/voice_identity_inference_smoke_test.py --json` |
| `VOICE_FIRST_TURN_INFER_THRESHOLD` | `0.74` | First-turn threshold while active speaker is unknown | Too low can let a wrong speaker claim identity too early | Change only with real first-turn speaker evidence | `python scripts/voice_identity_inference_smoke_test.py --json`, `python scripts/speaker_identity_correction_smoke_test.py --json` |
| `VOICE_ENROLLMENT_TURNS` | `5` | Number of turns collected for public voice enrollment | Too low may produce weak profiles; too high slows enrollment completion | Change only if enrollment quality is consistently poor | needs confirmation |
| `VOICE_ADMIN_ENROLLMENT_TURNS` | `10` | Number of turns collected for admin voice enrollment | Lowering weakens admin profile quality | Change only if admin enrollment is impractical and backed by evidence | needs confirmation |
| `VOICE_ADMIN_SIMILARITY_THRESHOLD` | `0.82` | Admin voice score threshold | Lowering increases risk of non-admin speaker unlocking admin context | Change only with strong real-world calibration evidence | `python scripts/voice_identity_inference_smoke_test.py --json` |
| `VOICE_ADMIN_MARGIN_THRESHOLD` | `0.14` | Required score margin for admin unlock | Lowering increases risk where two speakers score similarly | Change only with calibration evidence | `python scripts/voice_identity_inference_smoke_test.py --json` |
| `VOICE_PUBLIC_MARGIN_THRESHOLD` | `0.08` | Required score margin for public acceptance | Too low increases misassignment when two profiles are close | Change only with calibration evidence | `python scripts/voice_identity_inference_smoke_test.py --json` |
| `VOICE_DRIFT_CONFIRMATION_TURNS` | `3` | Consecutive turns required before switching a known speaker to another known speaker or unknown | Lowering makes drift more volatile; raising can slow correction | Change only if real drift handling is clearly too sticky or too eager | `python scripts/voice_identity_drift_smoke_test.py --json` |
| `VOICE_LOW_CONFIDENCE_ASK_AFTER` | `3` | Number of low-confidence turns before identity follow-up should be asked | Too low can annoy users; too high can leave identity unresolved too long | Change only if real interaction shows repeated friction | needs confirmation |

## 2. LangGraph / Orchestrator

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `LANGGRAPH_RUNTIME_ENABLED` | `False` | Enables the dedicated LangGraph runtime path | Can change how resume/graph commands run; use carefully with existing recovery state | Change only when testing the dedicated runtime path | `python scripts/orchestrator_graph_smoke_test.py --json`, `python scripts/piper_graph_smoke_test.py --json` |
| `USE_LANGGRAPH_ORCHESTRATOR` | `True` | Makes LangGraph the default orchestrator path | Turning this off reverts to legacy loop; leaving it on requires graph parity confidence | Change only for fallback/debugging or controlled comparisons | `python scripts/orchestrator_graph_smoke_test.py --json`, `python scripts/piper_graph_smoke_test.py --json` |
| `LANGGRAPH_CHECKPOINT_MODE` | `sqlite` | Selects checkpoint backend (`sqlite`, `memory`, or `none`) | `none` removes durable recovery; `memory` is not restart-safe | Change only intentionally for tests or debugging | `python scripts/langgraph_checkpoint_recovery_smoke_test.py --json` |
| `LANGGRAPH_TRACE_HISTORY_LIMIT` | `500` | Max trace history retained | Too high increases debug-data growth | Change if trace files grow too much | needs confirmation |
| `LANGGRAPH_CHECKPOINT_HISTORY_LIMIT` | `500` | Max checkpoints retained per thread | Too low can prune useful recovery history; too high grows state DB | Change if checkpoint storage needs tuning | `python scripts/langgraph_checkpoint_recovery_smoke_test.py --json` |
| `LANGGRAPH_CHECKPOINT_PATH` | dynamic; default `data/state/langgraph_checkpoints.sqlite` | Checkpoint DB path | Bad paths break checkpointing or split history across locations | Override only for tests, alternate runtime state, or relocation | `python scripts/langgraph_checkpoint_recovery_smoke_test.py --json` |
| `LANGGRAPH_RECOVERY_PATH` | dynamic; default `data/state/langgraph_recovery.json` | Recovery record path | Wrong path can hide or split recovery records | Override only for isolated tests | `python scripts/langgraph_recovery_command_smoke_test.py --json` |
| `LANGGRAPH_INTERRUPT_PATH` | dynamic; default `data/state/langgraph_interrupt.json` | Interrupt record path | Wrong path can break resume/clear flows | Override only for isolated tests | `python scripts/langgraph_interrupt_smoke_test.py --json` |

## 3. Executor Limits / Stage Control

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `EXECUTOR_MAX_STEPS` | `12` | Maximum planner loop steps per stage | Too low can cut off legitimate work; too high increases runaway loops | Change only if stages routinely stop too early or spin too long | `python scripts/executor_budget_smoke_test.py --json` |
| `EXECUTOR_MAX_STAGE_RUNTIME_S` | `120` | Per-stage wall-clock limit | Too low can abort normal work; too high can hide stalls | Change only if stage runtime budget is clearly wrong | `python scripts/executor_budget_smoke_test.py --json` |
| `EXECUTOR_MAX_ACTIONS_PER_STAGE` | `15` | Hard cap on actions per stage | Too low blocks valid long stages; too high reduces anti-loop protection | Change only with evidence from real executor behavior | `python scripts/executor_budget_smoke_test.py --json` |
| `SKILL_LAYER_ENABLED` | `True` | Enables skill-layer behavior | Disabling may remove route/planner guidance unexpectedly | Change only for controlled debugging | needs confirmation |
| `MODEL_MAX_TURNS` | `10` | Caps conversation turns in some runtime/model contexts | Raising can increase context drift | Change only if context carryover is too short and drift remains acceptable | needs confirmation |

## 4. Voice / STT / TTS

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `TTS_ENABLED` | `True` | Enables speech output | Disabling changes user experience and some runtime assumptions | Disable only for silent/debug sessions | `python scripts/tts_windows_probe.py --json` if available locally; needs confirmation |
| `TTS_BACKEND` | `auto` | Backend selection for TTS | `auto` can choose different backends per machine; forcing backend can expose missing dependencies | Change when diagnosing backend-specific issues | `python scripts/tts_windows_probe.py --json` if available locally; needs confirmation |
| `TTS_VOICE` | `af_heart` | Default TTS voice | Changing voice can hide style/config bugs versus expected default | Change for style or voice preference only | needs confirmation |
| `TTS_SPEED` | `0.85` | Default TTS speed | Extreme values reduce intelligibility | Change for voice comfort, not as a bug workaround | needs confirmation |
| `TTS_KOKORO_TIMEOUT_S` | `8.0` | Timeout for Kokoro generation | Too low can cause false failures; too high can stall TTS fallback | Change only when TTS timing is proven wrong | needs confirmation |
| `TTS_KOKORO_TORCH_READY_WAIT_S` | `2.0` | Wait budget for Kokoro torch worker readiness | Too low can fail startup readiness | Change only if worker readiness is consistently mistimed | needs confirmation |
| `TTS_KOKORO_HF_REPO_ID` | `hexgrad/Kokoro-82M` | Hugging Face repo source for Kokoro torch assets | Wrong repo ID breaks model asset lookup | Change only when intentionally switching asset source | `python scripts/kokoro_torch_worker.py` usage paths; needs confirmation |
| `TTS_LANG` | `en-us` | TTS language code | Wrong value can mismatch voice model expectations | Change only intentionally for language support | needs confirmation |
| `BOOT_SCREEN_MIN_VISIBLE_S` | `0.75` | Minimum boot screen visibility time | Mostly UX; too low can cause flicker, too high delays interaction | Change only for startup UX tuning | needs confirmation |
| `LIVE_SCREEN_INTERVAL_S` | `10.0` | Live-screen capture interval | Too low increases overhead; too high makes screen context stale | Change only for live-vision tuning | needs confirmation |
| `LIVE_SCREEN_SOURCE_MODE` | `display` | Default live-screen source mode | Wrong source mode can make live vision seem broken | Change only for capture-mode preference/testing | needs confirmation |
| `LIVE_SCREEN_MAX_STALE_S` | `30.0` | Max age for live-screen context | Too high risks stale visual context | Change only if stale-screen behavior is clearly wrong | needs confirmation |
| `SCREEN_CAPTURE_MAX_DIM` | `1920` | Capture max dimension | Lowering reduces detail; raising increases size/cost | Change only for performance or detail tuning | needs confirmation |

## 5. Debug / Logging

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `LOG_LEVEL` | `INFO` | General log verbosity | Raising to debug/noisy levels can flood logs; lowering can hide useful context | Change when debugging or reducing verbosity intentionally | needs confirmation |
| `DEBUG_LLM_HTTP_PAYLOADS` | `False` | Full raw LLM HTTP payload logging | Can expose prompt/content details and create noisy debug artifacts | Enable only during targeted serialization/debug work | inspect `data/debug/llm_http_payload_debug.txt` |
| `DEBUG_LLM_PROMPTS` | `True` | Prompt debug logging | Useful, but still writes prompt content to debug files | Disable only for focused perf/noise reasons | inspect `data/debug/llm_prompt_debug.txt` and per-layer debug files |
| `DEBUG_MANAGER_PROMPTS` | `False` | Extra manager prompt logging | Adds noise during routine work | Enable only for manager/planner debugging | inspect `data/debug/manager_debug.txt` |
| `DEBUG_LANGGRAPH_TRACE` | `True` | Structured LangGraph trace logging | Produces ongoing trace output under debug dir | Disable for noise reduction only if not actively debugging graph behavior | `python scripts/orchestrator_graph_smoke_test.py --json` |
| `DEBUG_LANGGRAPH_VISUALIZE` | `False` | Graph visualization output | Extra debug artifact generation | Enable only when inspecting graph structure | check `data/debug/langgraph_visualization.*`; needs confirmation |
| `DEBUG_STREAMING_PIPELINE` | `False` | Per-token streaming diagnostics | Very noisy; not for routine use | Enable only for streaming regressions | `notes/debug-protocol.md`, live debug session |

## 6. Search / Browser / Computer Use

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `COMPUTER_USE_ENABLED` | `True` | Master browser computer-use enable | Disabling changes route/tool behavior for browser tasks | Change only to disable the feature intentionally | `python scripts/computer_use_harness_smoke_test.py --json` |
| `COMPUTER_USE_HTTP_ENABLED` | `True` | Enables HTTP fallback/related browser HTTP behavior | Disabling may break validated artifact/download fallback flows | Change only when isolating HTTP fallback issues | `python scripts/computer_use_extract_download_harness_smoke_test.py --json` |
| `COMPUTER_USE_ALLOWED_HTTP_DOMAINS` | `example.com, iana.org, apache.org, w3.org, python.org, rfc-editor.org, localhost, 127.0.0.1` | Default allowlist for HTTP/browser-use scope | Bad allowlist weakens safety or blocks intended sites | Change only intentionally and with scope awareness | `python scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json` |
| `SEARCH_BLACKLIST` | `["zhihu.com", "baidu.com", "weibo.com"]` | Domains excluded from search sourcing | Removing entries can degrade result quality; adding too many can starve search | Change only with search-quality evidence | search flow smokes; needs confirmation |
| `SEARCH_URL_FETCH_TIMEOUT_S` | `20.0` | Per-URL fetch timeout | Too low causes false fetch failures; too high slows search | Change only if search fetch timing is demonstrably wrong | `python scripts/search_flow_smoke_test.py --json` if available locally; needs confirmation |
| `SEARCH_MIN_CONTENT_LENGTH` | `100` | Minimum fetched content size to keep | Too low admits junk; too high drops valid pages | Change only with search-content quality evidence | needs confirmation |
| `SEARCH_MAX_RESULTS` | `8` | Search result count cap | Raising increases cost/noise; lowering may miss good sources | Change only with search-quality evidence | needs confirmation |
| `SEARCH_SNIPPETS_LIMIT` | `3` | Snippet count surfaced into search flow | Larger values can bloat prompt/context | Change only if preview quality is too weak | needs confirmation |
| `SEARCH_DEEP_DIVE_LINKS_LIMIT` | `6` | Number of links selected for deeper content retrieval | Higher values increase latency and noise | Change only with search-quality evidence | needs confirmation |
| `SEARCH_CONTENT_SLICE_LENGTH` | `1500` | Content slice size for fetched pages | Too high wastes context; too low truncates useful evidence | Change only with context/search evidence | needs confirmation |

## 7. Memory / Retrieval

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `DATA_DIR` | `ROOT_DIR / "data"` | Root of runtime state | Moving this without planning can split or hide live state | Change only intentionally for alternate runtime roots | `python scripts/check_repo_hygiene.py --json` plus manual state inspection |
| `MEMORY_PATH` | `DATA_DIR/state/memory.jsonl` | Chat memory log path | Wrong path can split conversation history | Change only intentionally; restart-sensitive | `python scripts/user_runtime_smoke_test.py --json` |
| `VECTOR_STORE_DIR` | `DATA_DIR/vector_store` | Shared vector store location | Path changes can strand embeddings/history | Change only intentionally | `python scripts/user_runtime_smoke_test.py --json` |
| `KNOWLEDGE_PATH` | `DATA_DIR/state/knowledge.json` | Legacy durable knowledge mirror | Wrong path can split compatibility knowledge state | Change only intentionally | `python scripts/user_runtime_smoke_test.py --json` |
| `WORLD_MODEL_PATH` | `DATA_DIR/state/world_model.json` | World model state path | Wrong path can break identity/world state continuity | Change only intentionally | `python scripts/user_runtime_smoke_test.py --json` |
| `INGESTED_DOCUMENTS_PATH` | `DATA_DIR/state/ingested_documents.json` | Ingested document index metadata path | Wrong path can hide document memory state | Change only intentionally | document/user-runtime validation; needs confirmation |
| `CONVERSATION_SUMMARY_PATH` | `DATA_DIR/conversation_summary.json` | Conversation summary storage | Wrong path can break summary continuity | Change only intentionally | needs confirmation |

## 8. Paths / Models / Runtime Files

| Setting | Default | Controls | Risk note | When to change | Validation |
|---|---|---|---|---|---|
| `ROOT_DIR` | dynamic repo root from `config.py` location | Base repo/runtime root | Wrong root breaks nearly everything | Do not override casually | `python -m compileall app.py config.py core ui memory tools llm` |
| `MODELS_DIR` | `ROOT_DIR / "models"` | Base models folder | Wrong path breaks model discovery | Change only if repo layout changes | needs confirmation |
| `LLAMA_SERVER_EXE` | dynamic; prefers `PIPER_LLAMA_SERVER_EXE`, then `runtime/llama.cpp/llama-server.exe`, then hardcoded Windows fallback, then repo root fallback | Llama server executable path | Wrong path means Piper cannot boot or connect the local runtime as expected | Change only when switching llama builds intentionally | benchmark/launch checks; needs confirmation |
| `LLAMA_SERVER_URL` | dynamic from `PIPER_LLAMA_SERVER_URL`, defaulting to `http://127.0.0.1:8080` with WSL/Windows host rewriting when needed | URL Piper uses to talk to llama server | Wrong value breaks model calls or WSL bridging | Change only when server address really differs | `python scripts/orchestrator_graph_smoke_test.py --json`, live boot |
| `LLAMA_SERVER_BIND_HOST` | dynamic; usually `127.0.0.1` or `0.0.0.0` depending on runtime/exe context | Host binding exposed to llama server startup path | Bad host can break WSL/Windows interop or overexpose the service | Change only if runtime topology demands it | needs confirmation |
| `LLAMA_SERVER_CTX_SIZE` | `8192` | Llama context window size | Too low truncates work; too high may hit performance or memory ceilings | Change only with model/runtime evidence | live runtime + compile/smoke pack; needs confirmation |
| `LLAMA_SERVER_GPU_LAYERS` | `99` | GPU layer offload count | Wrong value hurts performance or compatibility | Change only for hardware/runtime tuning | needs confirmation |
| `LLAMA_SERVER_REASONING_BUDGET` | dynamic; defaults to `0` for Qwen 3.5 model names and `-1` otherwise | Reasoning budget passed to llama runtime | Changing can materially alter behavior and cost/latency | Change only intentionally and treat as restart-sensitive | model/runtime comparison evidence; needs confirmation |
| `MODEL_PATH` | dynamic; prefers `PIPER_MODEL_PATH`, then selected model, then preferred local model fallback | Active GGUF model path | Wrong model path changes behavior dramatically | Change only intentionally with validation | `python -m compileall ...` plus branch-specific smoke pack |
| `MMPROJ_PATH` | dynamic; `None` unless a matching multimodal projector is found/required | Multimodal projector path | Wrong path breaks multimodal usage or silently disables it | Change only intentionally when using multimodal models | multimodal/manual validation; needs confirmation |
| `COMFY_DIR` | dynamic; hardcoded Windows path if present, else `ROOT_DIR / "ComfyUI"` | ComfyUI runtime path | Wrong path breaks image generation/editing | Change only when image runtime location differs | image runtime/manual validation; needs confirmation |
| `KOKORO_DIR` | dynamic; hardcoded Windows path if present, else `ROOT_DIR / "models" / "kokoro"` | Kokoro model directory | Wrong path breaks TTS model loading | Change only when TTS assets live elsewhere | `python scripts/tts_windows_probe.py --json` if available locally; needs confirmation |
| `KOKORO_MODEL` | `kokoro-v1.0.onnx` | ONNX model filename | Wrong file name breaks TTS load | Change only when asset naming differs | needs confirmation |
| `KOKORO_VOICES` | `voices-v1.0.bin` | Kokoro voice data filename | Wrong file name breaks TTS voices | Change only when asset naming differs | needs confirmation |

## Practical Review Commands

Use these when config-sensitive behavior changed:

```powershell
git status --short
python -m compileall app.py config.py core ui memory tools llm
python scripts/check_repo_hygiene.py --json
python scripts/release_gate.py --json
```

For the current voice-identity/runtime branch class, also use:

```powershell
python scripts/voice_identity_inference_smoke_test.py --json
python scripts/user_runtime_smoke_test.py --json
python scripts/voice_identity_drift_smoke_test.py --json
python scripts/orchestrator_graph_smoke_test.py --json
python scripts/piper_graph_smoke_test.py --json
```
