# Piper — Debug Protocol

Use this when Piper is behaving wrong and you need to find the root cause fast.
Hand this file to a coding agent at the start of a fix session.

---

## 1. First Stop: The Debug Files

Before touching any code, read these files in order:

### `data/debug/llm_prompt_debug.txt`
The most important file. Shows the exact prompts sent to the LLM for every phase.

What to look for:
- Is the correct date in `[ENVIRONMENT]`? (it should always be there)
- Is stale data appearing in `[INTENT STATE]` or `[SITUATIONAL STATE]`?
- Is `[DOCUMENT FOCUS]` or `[DOCUMENT MATCHES]` appearing when it shouldn't?
- Is the current user turn duplicated in the router history array?
- Is `"Thinking..."` appearing as an assistant entry in history?
- Is the planner directive sent as `ROLE: user` instead of `ROLE: system`?
- Is the right `[LATEST_RUNTIME_CONTEXT]` block present for task turns?

### `data/debug/llama_server.log`
Shows what the LLM server actually received and produced.

What to look for:
- `tokens in` / `tokens out` — if tokens out is near zero, the response is being swallowed
- `thinking = 0` confirms split-mode (reasoning goes to `reasoning_content`, not `content`)
- Cache hit % — very high similarity between consecutive calls may mean the prompt isn't changing

### `data/debug/llm_http_payload_debug.txt`
Raw HTTP payload sent to llama.cpp. Use this if the prompt debug file looks fine but the model is still misbehaving — the issue may be in serialization.

### `data/debug/llm_prompt_debug.txt` — PHASE labels
Each block is labeled with its phase: `SECRETARY`, `PERSONA`, `MANAGER`, `INSPECTOR`, `REPORTER`.
Search by phase to jump to the relevant call.

---

## 2. Symptom → Where to Look

| Symptom | Check first |
|---|---|
| No visible reply after input | `llama_server.log` tokens out; `stream_filter.py` behavior |
| Wrong date returned | `[ENVIRONMENT]` block in persona prompt; persona instructions |
| Stale intent surfacing | `[INTENT STATE]` TTL; `memory/stores.py` IntentStateStore |
| Hallucinated facts about user | `[WORLD STATE]` / `[RETRIEVED MEMORY]` in persona prompt |
| Aviation / document bias on unrelated turns | `[DOCUMENT MATCHES]` gating in `prompt_builder.py` |
| "Yes / Sure / Go ahead" not resolving | `FollowupResolutionEngine._AFFIRMATIVE_CONFIRM_RE`; prior assistant turn for offer phrase |
| Persona narrating success on partial/failed work | `orc.last_verification` wiring; `build_persona_runtime_pack()` |
| Router misclassifying CHAT vs TASK | Router history block; `phase_secretary` prompt |
| Planner picking wrong tool | Stage type / allowed tools in `PlannerBoundary.validate_input()` |
| Scratchpad not carrying forward | `SummaryEngine.build_runtime_note()` carry-forward pipeline |
| Memory written incorrectly | `StateMutationEngine`; `transient_state.py` `_try_ingest_disposition()` |
| Streaming stops mid-reply | `ChatPipeline._stream_active`; `TagScrubber`; `stream_thinking_filter` |

---

## 3. The Fix Prompt Template

When handing a fix to a coding agent, structure the prompt like this:

```
Piper fix session — [short description]

Symptom:
[What the user saw]

Root cause (from debug file):
[What the prompt / log showed — be specific, quote the relevant block]

File and line (if known):
[e.g. "core/orchestrator_phases.py around phase_secretary()"]

Fix required:
[Exact description of what needs to change]

Do not touch:
[Any files / behaviors that must stay the same]

After fixing:
- Run: python -m compileall app.py config.py core ui memory tools llm
- Run the relevant smoke test(s) from scripts/
- Update notes/known-issues.md or notes/coder-log.md if the fix closes a known issue
```

---

## 4. Scope Rules for Fix Sessions

- Fix one root cause at a time when possible. Bundling unrelated fixes makes regressions hard to attribute.
- If a fix touches `orchestrator_phases.py`, `executor.py`, or `prompt_builder.py`, always run the full smoke pack afterward.
- If a fix touches memory stores, check that existing state files are still readable after the change.
- If a fix touches the streaming pipeline, verify end-to-end with a live Piper session, not just unit tests.

**Minimum smoke pack for any non-trivial fix:**
```
python -m compileall app.py config.py core ui memory tools llm
scripts/file_edit_smoke_test.py --json
scripts/file_lookup_smoke_test.py --json
scripts/file_crud_smoke_test.py --json
```

**Full pack (after changes to orchestrator, executor, or prompt layers):**
- Everything above plus:
```
scripts/code_session_smoke_test.py --json
scripts/file_chaos_test.py --json
scripts/summary_engine_smoke_test.py --json
scripts/context_pack_engine_smoke_test.py --json
```

---

## 5. What the Debug Flag Does

Set `PIPER_DEBUG_STREAMING_PIPELINE=1` before launch to enable streaming diagnostics:

```
[PIPE-IN]      token entering stream_thinking_filter
[FILTER-OUT]   token exiting the filter
[QUEUE-PUT]    event placed on controller queue
[STREAM] START / delta / END   pipeline event trace
```

All of these are silent in normal operation. Turn on only when debugging streaming issues.

---

## 6. Known Failure Modes (Quick Reference)

**Split-mode thinking (llama.cpp with `thinking=0`)**
Reasoning tokens go to `reasoning_content`, not `content`. The stream filter handles this — first non-`<` character passes through immediately. If you see zero output but tokens are being generated, check `stream_thinking_filter` in `core/stream_filter.py`.

**"Thinking..." in model history**
Placeholder entries must be stripped from history before any model call. If you see `"Thinking..."` as an assistant entry in the router or persona history block in the prompt debug file, the stripping logic in `phase_secretary` is broken.

**Stale intent state**
Intent states expire after 2 days (`IntentStateStore.DEFAULT_TTL_SECONDS = 2 * 86400`). If an old intent is surfacing, check `memory/stores.py` → `load_active_entries()` TTL filter.

**Disposition traits stored as situational state**
Personality/disposition statements should route to `world_model.json` via `_try_ingest_disposition()` in `memory/transient_state.py`, not as short-lived situational entries. If you see a trait attached to a specific appointment or event, the disposition regex may need tuning.

**UTF-8 BOM encoding**
If a Python file causes `SyntaxError: invalid non-printable character U+FEFF`, it was saved with BOM encoding. Re-save as UTF-8 without BOM. Has historically affected `core/pipeline.py` and `llm/llm_server_client.py`.
