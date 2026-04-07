# Coder Log

## 2026-04-07

### Locked one TTS backend per utterance and restored strong-punctuation pauses for Quinn

Problem:
- after the Windows Quinn/Kokoro worker started working again, live Piper speech could still
  sound wrong in two ways:
  - the first sentence of a reply could come from the robotic Windows fallback, while later
    sentences in the same reply switched over to Quinn once the worker became ready
  - Quinn speech could flatten punctuation because multi-sentence text chunks were being sent to
    the pure Kokoro path as one synthesis unit

Root cause:
- backend choice was effectively per text chunk, not per utterance
- the TTS queue could therefore send an early chunk through system speech and later chunks
  through the Kokoro worker once it became ready
- `_split_3stage()` and the queueing path were still combining several sentences into one Kokoro
  job, so sentence-ending punctuation did not reliably become a pause in the Quinn path

Fix:
- `tools/tts.py`
  - added utterance-scoped backend locking inside `TTS`, separate from the existing epoch-based
    cancellation logic
  - Windows `_KokoroEngine.choose_reply_backend()` now picks one backend for the whole utterance:
    `onnx`, `torch`, or `system`
  - `_queue_text_job()` now segments Kokoro-bound utterances on strong punctuation / newlines
    before they reach the synth loop
  - the synth loop now respects the locked backend instead of letting later chunks switch paths

Validation:
- `python3 -m compileall tools/tts.py`
- `python3 scripts/event_speech_policy_smoke_test.py`
- focused inline TTS checks:
  - same utterance queued twice only called `choose_reply_backend()` once
  - Quinn-bound text now queues as:
    - `First sentence.`
    - `Second sentence!`
    - `Third: item.`

### Windows Quinn/Kokoro torch path restored after isolating the real native blockers

Problem:
- Piper still spoke only through the robotic Windows fallback even after the dedicated Kokoro
  worker process landed.
- `tts_debug.txt` showed the worker never became ready, and direct native torch probes hung
  during `import torch`.

Root cause:
- native Windows `torch` import was hanging inside Python's stdlib `platform` probes:
  - `platform.machine()` in `torch.__init__` line 244
  - `platform.system()` / `platform.uname()` in `torch.__init__` line 367
  - both routed through `_wmi_query()`, which was stalling on this machine
- after torch import was unblocked, the worker still failed to phonemize because `espeak-ng`
  IPA output was being decoded with the local ANSI code page instead of UTF-8
- `loguru` also expected the optional `win32_setctime` helper, which was missing from the
  Windows `.venv`

Fix:
- `tools/tts.py`
  - added `_patch_platform_for_windows_torch_import()` and applied it before Windows torch
    imports in the Kokoro torch path
  - switched Windows `espeak-ng` phonemization reads to
    `encoding="utf-8", errors="ignore"`
- `scripts/kokoro_torch_worker.py`
  - applied the same Windows torch-platform shim before importing torch
  - switched phonemizer reads to UTF-8
  - moved `loguru` output off stdout so the worker protocol stays JSON-clean
- `scripts/tts_windows_probe.py`
  - now imports `CFG`
  - applies the Windows torch-platform shim before the torch probe
  - treats the Windows torch path as a worker-readiness probe instead of assuming an ONNX
    `_kokoro` object exists
- `win32_setctime.py`
  - added a tiny repo-local no-op shim so `loguru` stops failing on a missing optional Windows
    helper package

Validation:
- `python3 -m compileall tools/tts.py scripts/kokoro_torch_worker.py scripts/tts_windows_probe.py win32_setctime.py`
- native Windows import probe with the shim:
  - `/.venv/Scripts/python.exe -c "... _patch_platform_for_windows_torch_import(); import torch ..."`
    returned `DONE 1.188 2.11.0+cpu`
- direct worker protocol probe:
  - `printf ... | /.venv/Scripts/python.exe scripts/kokoro_torch_worker.py`
    emitted `{"type":"ready"}` then `{"type":"result","ok":true,...}`
- native Windows probe:
  - `/.venv/Scripts/python.exe scripts/tts_windows_probe.py --engine torch --json`
    returned `worker_ready: true`

### Quinn punctuation/prosody restoration: moved the Windows torch worker onto Kokoro's English `KPipeline`

Problem:
- even after Quinn came back, speech still felt flatter than the older pushed runtime
- the strongest user signal was that punctuation inside a reply chunk no longer shaped prosody the
  way it used to

Root cause:
- the current Windows torch worker was bypassing Kokoro's text-aware `KPipeline`
- it manually called `espeak-ng` and fed raw IPA into `KModel`, which is enough to produce speech
  but not enough to recover the punctuation-aware English G2P / chunking behavior

Fix:
- installed the missing English Kokoro pipeline deps into the Windows `.venv`:
  - `misaki`
  - `num2words`
  - `spacy`
  - `en_core_web_sm` (auto-installed on first `KPipeline` init)
- `scripts/kokoro_torch_worker.py`
  - now constructs `KPipeline(lang_code='a'/'b', model=model)` and generates audio from text
    through the real English pipeline instead of the raw `espeak-ng` phoneme shortcut
  - still reuses the repo-local `af_bella` / `af_heart` voice packs
- `requirements.txt`
  - added `misaki`, `num2words`, and `spacy` so the Windows TTS stack stays reproducible

Validation:
- `python3 -m compileall scripts/kokoro_torch_worker.py tools/tts.py`
- direct worker probe:
  - `cat <<EOF | /.venv/Scripts/python.exe scripts/kokoro_torch_worker.py ... EOF`
    returned `{"type":"ready"}` then `{"type":"result","ok":true,...}`

### Quinn pause restoration: preserve `KPipeline` segment boundaries when merging worker audio

Problem:
- even after switching the Windows torch worker onto Kokoro's English `KPipeline`, long replies
  could still sound like punctuation and newlines were being ignored

Root cause:
- `KPipeline` was correctly splitting the text into multiple result chunks for sentence / newline
  boundaries
- but `scripts/kokoro_torch_worker.py` was concatenating those audio arrays directly with
  `np.concatenate(audios)` and inserting no silence at all between them
- that erased the audible boundary between chunks and made the speech sound like one continuous
  sentence

Fix:
- `scripts/kokoro_torch_worker.py`
  - added `_pause_samples_for_text()` to insert small pauses between consecutive `KPipeline`
    outputs
  - newline and stronger end punctuation now get longer inter-chunk gaps than commas / semicolons

Validation:
- `python3 -m compileall scripts/kokoro_torch_worker.py`
- direct worker probe with multi-sentence / newline text:
  - returned `{"type":"ready"}` then `{"type":"result","ok":true,...}`

## 2026-04-06

### Browser retrieval hardening: hub-page navigation + artifact download now verifies the real file

Problem:
- the new browser retrieval slice needed end-to-end coverage for a useful pattern:
  open a hub page, navigate to the artifact page, then download the intended file
- the first pass exposed several real issues:
  - `core/engines/summary.py` had an indentation error that blocked imports
  - the local browser download path could match a heading instead of a real download element
  - the WSL Playwright env was missing, and Chromium could not launch without rootless
    `libnspr4.so` / `libnss3.so`
  - Playwright `download` still used the generic selector helper, so text-only downloads could
    target the wrong element
  - the HTTP download fallback was too permissive and could treat `quarterly_reports.html` as the
    requested artifact
  - model variance could stall a valid browser stage if the planner tried `click` or `download`
    before an explicit `goto_url`

Fix:
- `core/engines/summary.py`
  - fixed the broken topic-section reply branch so browser verification code imports again
- `core/engines/computer_use_engine.py`
  - local downloads now prefer real download elements instead of the first text match
  - Playwright downloads now use the dedicated download selector helper
  - bare selectors like `quarterly-report-link` are normalized against current-page inventory
  - same-scope HTTP fallback now handles inline artifact links that do not emit a Playwright
    `download` event
  - obvious `.html` navigation links are rejected as non-artifacts instead of being saved as fake
    downloads
  - browser actions can now auto-open the stage `start_url` before the first non-`goto_url`
    action when no page is active yet
- `core/executor.py`
  - non-`goto_url` browser actions now inherit `start_url` from the stage metadata so the engine
    can perform that first-page bootstrap deterministically
- `scripts/computer_use_route_normalizer_smoke_test.py`
  - corrected the download-follow-up regression to use the localhost download hub context
- new harnesses:
  - `scripts/computer_use_navigation_download_harness_smoke_test.py`
  - `scripts/computer_use_playwright_localhost_navigation_download_harness_smoke_test.py`
  - `scripts/computer_use_playwright_localhost_download_followup_harness_smoke_test.py`
- WSL validation env:
  - recreated `.venv-wsl` with `--system-site-packages`
  - installed `playwright`
  - installed Chromium
  - downloaded and unpacked rootless `libnspr4` / `libnss3` into
    `.venv-wsl/playwright-libs/usr/lib/x86_64-linux-gnu`

Validation:
- `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts`
- `python3 scripts/computer_use_route_normalizer_smoke_test.py --json`
- `python3 scripts/followup_resolution_engine_smoke_test.py`
- `python3 scripts/computer_use_engine_smoke_test.py --json`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_engine_smoke_test.py --json`
- `python3 scripts/computer_use_navigation_download_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_navigation_download_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_download_followup_harness_smoke_test.py --json --timeout 120`
- `python3 scripts/computer_use_extract_download_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_extract_download_harness_smoke_test.py --json --timeout 120`
- `python3 scripts/file_edit_smoke_test.py --json`
- `python3 scripts/file_lookup_smoke_test.py --json`
- `python3 scripts/file_crud_smoke_test.py --json`
- `python3 scripts/code_session_smoke_test.py --json`
- `python3 scripts/file_chaos_test.py --json`
- `python3 scripts/summary_engine_smoke_test.py`
- `python3 scripts/context_pack_engine_smoke_test.py --json`

## 2026-03-31

### Browser follow-up routing: preserve recent page context without an explicit URL

Escalation: `mutation_no_effect` on a request to retrieve warranty / liability details
from the Python license docs page.

Root cause:
- a short follow-up like `retrieve those details for me` could lose the recent page URL
  and fall back to a router-produced `FILE_WORK` card
- executor then tried `RUN_CODE` inside `FILE_WORK`, which produced no workspace
  mutation and tripped the truthful no-effect rails

Fix:
- `core/browser_route_utils.py`
  - browser follow-up routing now falls back to recent non-system page context when
    runtime context is absent
  - recent retrieval offers can carry a `requested_topic` into the `COMPUTER_USE`
    stage metadata
- `core/routing/route_normalizer.py`
  - added a contextual browser-follow-up normalizer so these turns are corrected to
    `COMPUTER_USE` before execution
- `scripts/computer_use_route_normalizer_smoke_test.py`
  - regression coverage for the Python docs follow-up case

Validation:
- `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts`
- `python3 scripts/computer_use_route_normalizer_smoke_test.py --json`
- `python3 scripts/followup_resolution_engine_smoke_test.py`

### Browser follow-up drift fix: vague page continuations stay on the active docs page

The first browser follow-up fix kept `retrieve those details for me` out of `FILE_WORK`,
but manual logs showed a broader continuity drift on the Python docs pilot:

- `what else is there` could still be re-clarified by `RouteClarifier`
- `general info` could stay in `COMPUTER_USE` but over-constrain success and drift into a
  partial clarification
- `anything else?` needed to remain browser-scoped on the same page instead of dropping
  into generic chat

Fix:
- `core/browser_route_utils.py`
  - short browser follow-ups like `anything else?` now classify as page extraction in
    active browser context
  - short topical replies like `general info` now stay pinned to the current page URL and
    become generic body-extract routes with `requested_topic` carried as context instead
    of a brittle topic-specific success condition
  - recent assistant questions like `Which specific piece of information ...` are now
    treated as browser-topic prompts, so their short user replies stay in `COMPUTER_USE`
- `core/engines/route_clarity.py`
  - browser follow-up routes are now exempt from the ambiguity clarifier so valid
    `COMPUTER_USE` continuations are not replaced with `CHAT` pauses
- `scripts/computer_use_python_docs_followup_harness_smoke_test.py`
  - new live regression for:
    - open Python docs title
    - `what else is there`
    - `general info`
    - `anything else?`
  - asserts all turns stay `COMPUTER_USE`, verify, and never drift to
    `python.org/about/license`
- `scripts/followup_resolution_engine_smoke_test.py`
  - expanded with deterministic browser follow-up cases for:
    - `general info`
    - `anything else?`
    - `retrieve those details for me`

Validation:
- `python3 -m compileall app.py config.py core ui memory tools llm AGENTS scripts`
- `python3 scripts/followup_resolution_engine_smoke_test.py`
- `./.venv/Scripts/python.exe scripts/computer_use_python_docs_followup_harness_smoke_test.py --json --timeout 120`
- `./.venv/Scripts/python.exe scripts/computer_use_browser_followup_harness_smoke_test.py --json --timeout 120`
- `./.venv/Scripts/python.exe scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py --json --timeout 120`
- `./.venv/Scripts/python.exe scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120`
- `python3 scripts/file_edit_smoke_test.py --json`
- `python3 scripts/file_lookup_smoke_test.py --json`
- `python3 scripts/file_crud_smoke_test.py --json`

### Computer use v0 stabilization: per-turn Playwright suspension + widened live-site pilot

The widened live-browser pilot (`w3.org`, `docs.python.org`, `rfc-editor.org`) passed,
but the first bundled run exposed a real teardown bug after success:

- the harness printed the expected JSON
- then Playwright emitted an unhandled Node-side `EPIPE: broken pipe, write`

Root cause:
- `ComputerUseEngine` lives inside one long-lived `AgentBrain`
- each real turn runs `run_agent_loop()` on a short-lived worker thread
- Playwright objects were surviving past the end of the worker thread that created them
- the earlier app/harness teardown hook still tried to close the browser backend from a
  different thread, which was enough to leave noisy broken-pipe shutdowns behind

Fix:
- `core/engines/computer_use_engine.py`
  - added:
    - `suspend()` to close the live Playwright handles while preserving lightweight
      browser state (`current_url`, title, text preview, allowed domains, field values)
    - `shutdown()` to fully clear both handles and remembered browser state
- `core/agent.py`
  - added:
    - `suspend_runtime_sessions()`
    - `shutdown()`
- `ui/controller_actions.py`
  - `do_generate_stream()` now suspends browser runtime sessions in the same worker
    thread after each turn finishes
- `AGENTS/harness/session.py`
  - harness turn worker now suspends browser runtime sessions before returning to idle
  - harness close still performs full `agent_brain.shutdown()`
- `ui/controller.py` / `app.py`
  - real UI shutdown paths now also call `agent_brain.shutdown()`
- `config.py`
  - widened default live-browser pilot allowlist to:
    - `example.com`
    - `iana.org`
    - `apache.org`
    - `w3.org`
    - `python.org`
    - `rfc-editor.org`
    - `localhost`
    - `127.0.0.1`
- `scripts/computer_use_playwright_real_site_pilot_harness_smoke_test.py`
  - expanded real-site read-only validation to:
    - IANA title / heading
    - Apache title / heading
    - W3 title / heading
    - Python docs title / heading
    - RFC title
- `scripts/computer_use_playwright_w3_followup_harness_smoke_test.py`
  - new real-site follow-up regression:
    - turn 1: W3 heading
    - turn 2: `What's the title?`

Validation:
- `python3 -m compileall app.py core/agent.py core/engines/computer_use_engine.py ui/controller.py ui/controller_actions.py AGENTS/harness/session.py`
- `./.venv/Scripts/python.exe scripts/computer_use_playwright_real_site_pilot_harness_smoke_test.py --json --timeout 120`
- `./.venv/Scripts/python.exe scripts/computer_use_playwright_w3_followup_harness_smoke_test.py --json --timeout 120`
- `./.venv/Scripts/python.exe scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py --json --timeout 120`

Operational note:
- live Playwright harnesses that boot Piper/llama-server must be run sequentially, not in
  parallel; overlapping runs can kill each other's server boot and produce false failures
  (`Server crashed with code 15`)

## 2026-03-28

### Computer use v0 pilot expansion: two additional real read-only hosts

Expanded the live-site browser pilot one step beyond `example.com`, but only with hosts
that behaved cleanly through Piper's own `ComputerUseEngine` and stayed read-only.

Chosen hosts:
- `iana.org`
  - verified page: `https://iana.org/domains/reserved`
  - title and `h1` both resolve to `IANA-managed Reserved Domains`
- `apache.org`
  - verified page: `https://apache.org/licenses/LICENSE-2.0`
  - title resolves to `Apache License, Version 2.0 | Apache Software Foundation`
  - `h1` resolves to `Apache License, Version 2.0`

Fix / scope:
- `config.py`
  - expanded default `COMPUTER_USE_ALLOWED_HTTP_DOMAINS` to:
    - `example.com`
    - `iana.org`
    - `apache.org`
    - `localhost`
    - `127.0.0.1`
- `scripts/computer_use_route_normalizer_smoke_test.py`
  - added coverage for host+path browser requests like
    `iana.org/domains/reserved`
  - verifies start URL normalization and allowed-domain extraction
- `scripts/computer_use_playwright_real_site_pilot_harness_smoke_test.py`
  - new four-turn live-browser harness:
    - IANA title
    - IANA heading
    - Apache title
    - Apache heading
  - requires verified outcomes and rejects regressions back to `Systems indicate ...`

Validation:
- `python3 -m py_compile config.py scripts/computer_use_route_normalizer_smoke_test.py scripts/computer_use_playwright_real_site_pilot_harness_smoke_test.py`
- `python3 scripts/computer_use_route_normalizer_smoke_test.py --json`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_real_site_pilot_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120`
- `python3 -m compileall config.py core scripts`

### Computer use v0 polish: direct verified browser replies

The browser pilot was accurate, but the simple success replies still sounded too
mechanical in manual use (`Systems indicate ... is confirmed as ...`) even when the
runtime already had deterministic verified browser evidence.

Fix:
- `core/engines/summary.py`
  - added a dedicated renderer for `COMPUTER_USE_VERIFIED_RESULT` payloads
  - title / heading / extracted-text / status / download answers now collapse to direct
    natural sentences like:
    - `The page title at https://example.com/ is "Example Domain".`
    - `The main heading at https://example.com/ is "Example Domain".`
- `core/contracts.py` + `core/engines/context_pack.py`
  - added `verified_browser_answer` to `PersonaRuntimePack`
  - simple single-stage verified browser tasks now use a deterministic persona fast-path,
    parallel to the existing verified-file fast-path, instead of routing those easy cases
    through a looser LLM paraphrase
- `scripts/computer_use_playwright_example_title_harness_smoke_test.py`
- `scripts/computer_use_playwright_example_heading_harness_smoke_test.py`
- `scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py`
  - now fail if the reply regresses back to the old `Systems indicate ...` phrasing

Validation:
- `python3 -m py_compile core/contracts.py core/engines/summary.py core/engines/context_pack.py scripts/computer_use_playwright_example_title_harness_smoke_test.py scripts/computer_use_playwright_example_heading_harness_smoke_test.py scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_example_title_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_example_heading_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120`
- `python3 scripts/computer_use_harness_smoke_test.py --json --timeout 120`
- `python3 -m compileall core scripts`

### Computer use v0 stabilization: cross-turn Playwright thread reuse

Real app use exposed a browser-only failure that the earlier live-site harnesses missed:
the first turn (`Open example.com ... page title`) succeeded, but the second turn in the
same Piper session (`... main heading`) failed with:

- `Unexpected browser action failure: cannot switch to a different thread (which happens to have exited)`

Root cause:
- `AgentBrain` owns one long-lived `ComputerUseEngine`
- each harness/app turn runs `run_agent_loop()` in a fresh worker thread
- the engine was caching Playwright page/context/browser objects across turns
- the first fix tried to key ownership by `threading.get_ident()`, but Python was reusing
  thread ids for new short-lived worker threads in this environment, so the stale
  Playwright session still looked "same-thread" even when it was not

Fix:
- `core/engines/computer_use_engine.py`
  - added explicit Playwright session reset logic
  - browser session ownership now tracks the actual `threading.current_thread()` object,
    not just `get_ident()`
  - when a new worker thread reuses the engine, the old Playwright session is torn down
    and a fresh page is created on the new thread
  - non-`goto_url` follow-up actions can rehydrate the last verified browser URL after the
    reset so cross-turn browser continuity does not collapse to a blank page
- `scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py`
  - new regression harness that reproduces the exact manual failure shape:
    - turn 1: title lookup on `example.com`
    - turn 2: heading lookup on `example.com`
  - fails if the old thread-switch error appears in the assistant text or debug files

Validation:
- `python3 -m py_compile core/engines/computer_use_engine.py scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_example_title_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_example_heading_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120`
- `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_title_harness_smoke_test.py --json --timeout 120`
- `python3 scripts/computer_use_harness_smoke_test.py --json --timeout 120`
- `python3 -m compileall config.py core tools scripts`

### Computer use v0 stabilization: selector inventory + completion checker + multistep harnesses

The first browser slice routed correctly, but real multistep fixture runs exposed two
truthfulness gaps:
- local fixture browsing was too selector-fragile (`body`, `text=Next`, `button:has-text('next')`)
- `COMPUTER_USE` stages could be marked complete from one successful `BROWSER_OP` even when
  the stage still owed other requested browser outcomes (for example: extracted text but no
  download yet)

Fixes:
- `core/engines/computer_use_engine.py`
  - local fixture backend now supports:
    - tag selectors like `body`
    - richer selector matching through id / name / data-testid / href tokens
    - Playwright-style `:has-text(...)` selectors for local fixtures too
  - `capture_state` now returns a compact `element_inventory` so the planner can choose
    specific selectors instead of guessing generic `body` / `button`
  - local inventory now suppresses noisy container-only entries like bare `body`
- `core/routing/route_normalizer.py`
  - browser task cards now preserve compound intent instead of flattening to a single
    generic goal
  - custom download folders like `browser_downloads` are preserved exactly
  - stage metadata now carries browser requirement flags plus hints such as:
    - `expected_text` (`status`, `destination`)
    - `navigation_hint` (`next`)
- `core/engines/computer_use_verifier.py`
  - new deterministic stage checker for accumulated browser evidence
  - `COMPUTER_USE` no longer accepts planner completion unless the full requested browser
    outcomes are proven across the stage evidence
- `core/executor.py`
  - stages now accumulate browser evidence across successful `BROWSER_OP` steps
  - verified browser stages append `COMPUTER_USE_VERIFIED_RESULT:` scratchpad notes
  - completion is blocked honestly when browser evidence is only partial
- `core/engines/summary.py`
  - persona now prefers `COMPUTER_USE_VERIFIED_RESULT:` over raw last-step browser logs
    so replies cite the verified extracted/downloaded result instead of drifting to a title
- `tools/registry.py` / `data/prompts/manager.txt`
  - browser tool docs now explicitly teach `download`, `type_text`, `wait_for`, selector
    inventory reuse, and “use download instead of generic click” when the artifact link is
    already present

New / expanded coverage:
- `scripts/computer_use_engine_smoke_test.py`
  - now covers `body` extraction and `:has-text(...)` fallback navigation
- `scripts/computer_use_extract_download_harness_smoke_test.py`
  - full turn: extract status text + download artifact into `browser_downloads`
- `scripts/computer_use_form_navigation_harness_smoke_test.py`
  - full turn: fill email field + follow next-link + report destination text
- `scripts/computer_use_route_normalizer_smoke_test.py`
  - now covers compound browser intent and custom download-dir preservation

Validation:
- `python3 -m py_compile core/contracts.py core/executor.py core/engines/computer_use_engine.py core/engines/computer_use_verifier.py core/engines/summary.py core/routing/route_normalizer.py tools/registry.py scripts/computer_use_engine_smoke_test.py scripts/computer_use_route_normalizer_smoke_test.py scripts/computer_use_extract_download_harness_smoke_test.py scripts/computer_use_form_navigation_harness_smoke_test.py`
- `python3 scripts/computer_use_route_normalizer_smoke_test.py --json`
- `python3 scripts/computer_use_engine_smoke_test.py --json`
- `python3 scripts/computer_use_harness_smoke_test.py --json --timeout 120`
- `python3 scripts/computer_use_extract_download_harness_smoke_test.py --json --timeout 120`
- `python3 scripts/computer_use_form_navigation_harness_smoke_test.py --json --timeout 120`
- `python3 -m compileall core tools scripts`

### Computer use v0 first vertical slice: TASK-routed browser domain + local fixture harness

Started the browser-first computer-use implementation as a real stage domain instead of a
separate top-level route kind.

Scope landed:
- `core/contracts.py`:
  - added `ComputerUseRequest` and attached `computer_use` metadata to `StageCard`
- `tools/registry.py`:
  - added `BROWSER_OP` under new `COMPUTER_USE` domain
- `core/agent.py`:
  - added `BROWSER_OP` parsing/execution path via `ComputerUseEngine`
- `core/engines/computer_use_engine.py`:
  - new browser engine with structured actions for `goto_url`, `capture_state`,
    `extract_text`, `click`, `type_text`, `wait_for`, and `download`
  - supports local `file://` fixture pages deterministically now
  - optionally uses Playwright for live http/https browsing when the package/browsers exist
- `core/routing/route_normalizer.py`:
  - explicit browser-use requests with URLs now normalize to `TASK` cards whose stage type
    is `COMPUTER_USE` and allowed tool is `BROWSER_OP`
- `core/orchestrator_phases.py`:
  - explicit browser-use requests now outrank the ingested-document chat pre-LLM bypass;
    this fixed `file://...index.html in the browser` being swallowed as `DOC_FOCUS`
- `data/prompts/secretary.txt` and `data/prompts/manager.txt`:
  - added `COMPUTER_USE` routing/planner guidance
  - also corrected the stale manager rule that claimed task/event file locks do not exist
- `core/scratchpad_formatter.py` / `core/engines/summary.py`:
  - added structured `BROWSER_OP` observation carry-forward so persona prefers verified
    browser evidence like page title / extracted text over generic completion prose
- `scripts/fixtures/computer_use/*`:
  - local deterministic browser fixture pages and downloadable artifact
- new smoke coverage:
  - `scripts/computer_use_route_normalizer_smoke_test.py`
  - `scripts/computer_use_engine_smoke_test.py`
  - `scripts/computer_use_harness_smoke_test.py`

Validation:
- `python3 -m py_compile core/contracts.py tools/registry.py core/agent.py core/routing/route_normalizer.py core/orchestrator_phases.py core/engines/computer_use_engine.py core/engines/summary.py core/scratchpad_formatter.py scripts/computer_use_route_normalizer_smoke_test.py scripts/computer_use_engine_smoke_test.py scripts/computer_use_harness_smoke_test.py`
- `python3 scripts/computer_use_route_normalizer_smoke_test.py --json`
- `python3 scripts/computer_use_engine_smoke_test.py --json`
- `python3 scripts/computer_use_harness_smoke_test.py --json --timeout 120`

### Follow-up resolver history fix + faster edge harness batch

The new follow-up edge harness batch exposed a real route-layer leak: `phase_route()`
was passing the trimmed `router_history` into `FollowupResolutionEngine`. That tail is
fine for the Router LLM prompt, but it can drop the hidden `[LATEST_RUNTIME_CONTEXT]`
block once `[LAST_TURN_EXPLANATION_CONTEXT]` and a few visible turns crowd the window.

Observed bad symptom:
- short clarifications like `I mean the file.` after an `ACTIVE_TASK_DEPENDENCY` block
  could fall back to the raw router card and be interpreted like an override-style retry
  instead of a plain FILE_WORK clarification retry

Fix:
- `core/orchestrator_phases.py`: added `_build_followup_resolution_history()` and now
  pass that richer filtered history into `_resolve_followup_route_with_llm()`
- keep the Router LLM on the small trimmed history, but preserve the latest hidden runtime
  context for deterministic follow-up routing

Harness updates:
- `scripts/file_task_collision_clarification_smoke_test.py`: now fails if the clarify
  turn leaves an override trace in `planner_debug.txt` or narrates a fake stage-success
  like `successfully located`
- `scripts/file_event_mutex_smoke_test.py`,
  `scripts/file_append_constraints_smoke_test.py`,
  `scripts/file_rename_then_move_smoke_test.py`: relaxed brittle wording checks so the
  batch is state-based instead of persona-phrase-based

Revalidated individually after the fix:
- `python3 scripts/file_task_collision_clarification_smoke_test.py --json --timeout 120`
- `python3 scripts/file_event_mutex_smoke_test.py --json --keep-data-copy --timeout 120`
- `python3 scripts/file_append_constraints_smoke_test.py --json --keep-data-copy --timeout 120`
- `python3 scripts/file_rename_then_move_smoke_test.py --json --keep-data-copy --timeout 120`
- `python3 scripts/file_event_override_followup_smoke_test.py --json --timeout 120`
- `python3 scripts/file_event_close_then_delete_smoke_test.py --json --timeout 120`
- `python3 scripts/file_append_readback_undo_smoke_test.py --json --timeout 120`
- `python3 scripts/lookup_source_web_followup_smoke_test.py --json --timeout 120`
- `python3 scripts/lookup_source_workspace_then_web_flip_smoke_test.py --json --timeout 120`
- `python3 scripts/file_task_collision_mutex_smoke_test.py --json --timeout 120`
- `python3 scripts/file_work_state_isolation_smoke_test.py --json --timeout 120`
- `python3 -m compileall app.py config.py core ui memory tools llm AGENTS scripts`

### Dependency mutex alias matching from live logs

Live debug logs showed a real dependency miss:
- file target: `charlie.txt`
- active event name: `review Charly TXT`
- delete went through because `OperationalStateService.find_references()` only matched
  literal path-ish substrings in task/event text

Fix:
- `core/operational_state_service.py`: added normalized reference matching for
  task/event dependency scans, including file-extension-aware close matching for
  humanised filename mentions like `Charly TXT`
- `core/file_reference_matcher.py`: extracted the matching logic into one shared
  helper so path-alias semantics do not drift across layers
- `core/routing/route_normalizer.py`: workspace follow-up file-reference matching
  now uses the same shared matcher, so alias-style subjects like `charly txt`
  resolve against `charlie.txt` consistently
- `core/file_stage_policy.py`: targeted-read / targeted-lookup verification now
  uses the same matcher when checking whether observed file paths satisfy quoted
  stage targets
- `data/prompts/secretary.txt`: removed the stale rule claiming tasks/events are
  never file locks; router now says FILE_WORK still routes directly, but mutex
  enforcement happens during execution
- `scripts/file_event_alias_mutex_smoke_test.py`: new regression for the exact
  live pattern (`Create Charlie TXT` -> `Schedule ... review Charly TXT` -> `Delete charlie.txt`)

Revalidated:
- `python3 scripts/file_event_alias_mutex_smoke_test.py --json --timeout 120`
- `python3 scripts/file_event_mutex_smoke_test.py --json --timeout 120`
- `python3 scripts/file_task_collision_mutex_smoke_test.py --json --timeout 120`

## 2026-03-26

### Harness stabilization pass: terminal reroute guard + readback-stage classification

Additional edge-case sweeps found three real regressions plus one stale harness call.

Fixes:
- `core/scratchpad_formatter.py`: terminal missing-file detection now parses JSON-backed
  FILE_OP summaries correctly and treats both `target not found` and `source not found`
  as terminal explicit-target failures. This keeps `allow_persona_reroute=False` for
  honest missing-target FILE_WORK failures instead of letting persona emit a spurious
  retry pass.
- `core/executor.py`: `_stage_all_mutated_paths` now initializes in `StageExecutor.__init__`
  as well as per-stage reset, so helper-only executor flows used by smokes cannot crash
  before `run()` seeds that attribute.
- `core/file_stage_policy.py`: readback stages like `Read the updated exact contents...`
  now classify as non-mutating inspection/targeted-read stages even when the router emits
  `file_stage_kind: "UNKNOWN"` and the wording contains adjectives like `updated` or
  context like `after the requested removal`. Treat `"UNKNOWN"` as heuristic fallback,
  not as a hard explicit kind.
- `scripts/file_edit_already_satisfied_read_recovery_smoke_test.py`: updated the smoke to
  use the current executor helper (`_append_exact_file_read_note_if_available(stage)`)
  instead of the removed legacy helper name.
- `scripts/file_work_engine_smoke_test.py`: added regression coverage proving a pure
  readback stage classifies as inspection.
- `scripts/test_engines.py`: added regression coverage for JSON-backed missing-target and
  missing-source FILE_OP observations disabling persona reroutes.

Revalidated:
- `python3 scripts/file_edit_smoke_test.py --json`
- `python3 scripts/file_lookup_smoke_test.py --json`
- `python3 scripts/file_crud_smoke_test.py --json`
- `python3 scripts/missing_file_no_reroute_smoke_test.py --json`
- `python3 scripts/file_target_confirmation_smoke_test.py --json`
- `python3 scripts/file_edit_already_satisfied_read_recovery_smoke_test.py`
- `python3 scripts/file_edit_compound_followup_smoke_test.py --json`
- `python3 scripts/file_delete_followup_normalizer_smoke_test.py --json`
- `python3 scripts/file_work_engine_smoke_test.py`
- `python3 scripts/file_chaos_test.py --json`
- `python3 scripts/code_session_smoke_test.py --json`
- `python3 scripts/summary_engine_smoke_test.py`
- `python3 scripts/context_pack_engine_smoke_test.py`
- `python3 -m compileall app.py config.py core ui memory tools llm AGENTS scripts`

### Local pytest path restored

`pytest` was missing from the system interpreter and `pip` is PEP-668 locked here.
Important Windows-first reminder: do not recreate the repo-root `.venv` from WSL.
That replaces Piper's Windows runtime env with a Linux one and breaks PowerShell
launches with errors like `No Python at '/usr/bin\\python.exe'`.

Safe split:
- keep repo-root `.venv` as the Windows runtime environment
- if WSL-only tooling is needed, use a separate env such as `.venv-wsl`
- run WSL pytest from that separate env, not from repo-root `.venv`

Also fixed one stale engine test:
- `scripts/test_engines.py`: `test_build_runtime_note_falls_back_to_observation`
  now uses the current scratchpad contract where `=== STAGE N OUTCOME ===`,
  `RESULT: ...`, and `LAST_LOG: ...` live in a single entry.

Historical note:
- a prior WSL-only pytest run passed from a Linux env before the Windows `.venv`
  repair, but that env should not be recreated at repo root

### 2026-04-06: Added a pure Kokoro-torch fallback path for Windows TTS

Root cause:
- The repo-style voice selection (`quinn.style` -> `af_bella`) was still correct, but the native Windows `onnxruntime` / `kokoro_onnx` path remained unstable enough that Quinn could disappear behind the robotic system fallback.
- The strongest remaining signal was that Windows ONNX load/synth timing kept tripping the timeout rails, while the user explicitly wanted the original Kokoro voice back, not just "some speech."

Changes:
- `tools/tts.py`
  - added `_load_kokoro_torch_model_class()` to load the pure `kokoro` model modules without depending on the heavy `KPipeline` / `misaki` path
  - added `_KokoroTorchEngine`, which:
    - uses the official `kokoro` PyTorch model
    - reuses Piper's existing Windows `espeak-ng.exe` phonemization
    - downloads Kokoro weights/voice packs from `hexgrad/Kokoro-82M`
    - plays audio through the same WAV + `winsound` path as the ONNX backend
  - Windows `_KokoroEngine` now falls back in this order:
    1. Kokoro ONNX
    2. Kokoro PyTorch
    3. Windows system speech
  - if the pure Kokoro path also fails, Piper still lands on system speech instead of going silent again
- `config.py`
  - added `TTS_KOKORO_HF_REPO_ID = "hexgrad/Kokoro-82M"`
- `scripts/tts_windows_probe.py`
  - now supports probing the torch-backed Kokoro path separately from the ONNX path
- `scripts/run_tts_windows_probe.cmd`
  - added as a native Windows wrapper for the probe script

Environment:
- downloaded and unpacked `kokoro-0.9.4` and `loguru-0.7.3` into the current Windows `.venv` so the pure Kokoro model path is available for this runtime

Verification:
- `python3 -m compileall app.py config.py core ui memory tools llm scripts`

Caveat:
- the repo-side code is in place, but the final confirmation still needs a live Windows runtime pass to verify that Quinn is audibly back instead of falling through to system speech.

### 2026-04-06: Fixed the silent dead-end in the pure Kokoro fallback path

Problem:
- after adding the pure Kokoro-torch fallback, the user reported a worse failure mode: no Quinn voice and no robotic fallback either
- root cause was that the torch fallback had no timeout rails, so if it stalled during import/model load/first-run setup, Piper never reached Windows system speech
- the fallback also still depended on live Hugging Face downloads for the first model/voice fetch, which made first-run speech fragile

Fix:
- `tools/tts.py`
  - `_KokoroEngine._warm_fallbacks()` now runs the torch fallback under a bounded timeout before continuing to system speech
  - `_KokoroEngine._speak_via_fallbacks()` now runs the torch fallback under a bounded timeout, logs timeout/errors, and then falls through to Windows speech instead of hanging silently
  - added separate Windows fallback timeout logic:
    - background warm-up gets a longer allowance
    - foreground speech gets a shorter but still bounded allowance
  - `_KokoroTorchEngine` now prefers repo-local model/config/voice files under `models/kokoro/torch` before attempting any Hugging Face download
- `config.py`
  - added local pure-Kokoro file names:
    - `KOKORO_TORCH_SUBDIR = "torch"`
    - `KOKORO_TORCH_MODEL = "kokoro-v1_0.pth"`
    - `KOKORO_TORCH_CONFIG = "config.json"`

Local asset seed:
- downloaded into `models/kokoro/torch/`:
  - `config.json`
  - `kokoro-v1_0.pth`
  - `voices/af_bella.pt`
  - `voices/af_heart.pt`

Verification:
- `python3 -m compileall app.py config.py tools/tts.py scripts/tts_windows_probe.py`
- `python3 scripts/event_speech_policy_smoke_test.py`

Caveat:
- the only trustworthy confirmation for this fix is still a live Windows Piper restart and a spoken reply

### 2026-04-07: Made the pure Kokoro fallback background-warm instead of re-timing out every reply

Problem:
- the user could hear only the robotic Windows fallback, and it arrived after a long pause
- `tts_debug.txt` showed:
  - `KOKORO DISABLED: Kokoro load timed out on Windows`
  - `KOKORO TORCH FALLBACK TIMEOUT`
- that meant Piper was waiting through the ONNX timeout and then waiting through a second torch-fallback timeout before finally speaking

Fix:
- `tools/tts.py`
  - `_KokoroTorchEngine` now starts its model load on a background thread and exposes readiness via an event instead of blocking every call through `_load()`
  - `warm_up()` on Windows now kicks off that background load immediately
  - foreground speech waits only a short configurable window (`TTS_KOKORO_TORCH_READY_WAIT_S`, default `2.0s`) for Quinn to be ready
  - if Quinn is still warming, the torch backend raises quickly and Piper falls through to Windows speech without another long stall
  - once the torch backend finishes loading, later turns can use the real Kokoro voice without repeated timeout churn
  - added `tts_debug.txt` markers:
    - `KOKORO TORCH LOAD START`
    - `KOKORO TORCH READY`
    - `KOKORO TORCH LOAD ERROR: ...`
- `config.py`
  - added `TTS_KOKORO_TORCH_READY_WAIT_S = 2.0`
  - increased the outer voice-fallback timeout so real torch-backed synthesis has room to finish once the model is actually loaded

Verification:
- `python3 -m compileall app.py config.py tools/tts.py scripts/tts_windows_probe.py`
- `python3 scripts/event_speech_policy_smoke_test.py`

### 2026-04-07: Moved the pure Kokoro Windows path into a dedicated worker process

Root cause:
- the new stack dump showed the pure Kokoro fallback was not actually hanging in model init yet; it was stuck waiting on Python's import lock while trying to `import torch` inside the main Piper process
- trace from `tts_debug.txt`:
  - `KOKORO TORCH STEP: import torch`
  - stack dump ended in `importlib._bootstrap._lock_unlock_module ... acquire`
- so Quinn was blocked by in-process module import contention, not by voice selection or local asset lookup

Fix:
- added `scripts/kokoro_torch_worker.py`
  - dedicated subprocess that loads the local pure Kokoro model once
  - phonemizes via the existing Windows `espeak-ng.exe` path
  - synthesizes to a temp WAV and returns JSON status over stdout
- `tools/tts.py`
  - `_KokoroTorchEngine` on Windows now starts and warms a dedicated worker process instead of importing `torch` inside Piper
  - speech requests go to that worker over stdin/stdout, then Piper plays the returned WAV
  - this isolates Quinn from the main-process `torch` import lock / module contention

Verification:
- `python3 -m compileall tools/tts.py scripts/kokoro_torch_worker.py`
- `python3 scripts/event_speech_policy_smoke_test.py`

### Windows `.venv` repaired after WSL overwrite

The repo-root `.venv` was accidentally recreated from WSL during a harness pass,
which broke `python app.py` in PowerShell because `pyvenv.cfg` pointed at
`/usr/bin/python3.12`.

Repair:
- removed the WSL-created `.venv`
- recreated `.venv` with `C:\Program Files\Python312\python.exe -m venv C:\Projects\Piper\.venv`
- reinstalled `requirements.txt` from the Windows interpreter

Verified:
- `.venv/pyvenv.cfg` now points to `C:\Program Files\Python312\python.exe`
- `'.venv/Scripts/python.exe' -c "import dearpygui.dearpygui, requests, psutil, numpy; print('core_imports_ok')"`
- `'.venv/Scripts/python.exe' -c "import chromadb, sentence_transformers, faster_whisper, kokoro_onnx; print('ml_imports_ok')"`

### WSL harness boot bridge + missing-file truthfulness pass

Two real regressions surfaced while running isolated harnesses from WSL against the
Windows `llama-server.exe` path.

Fixes:
- `config.py` + `llm/boot.py`: when Piper is running under WSL but launching the
  Windows `llama-server.exe`, the runtime now:
  - converts model/mmproj args to Windows-native paths
  - binds the server on `0.0.0.0`
  - rewrites `CFG.LLAMA_SERVER_URL` to the Windows host gateway IP (for example
    `http://172.24.64.1:8080`) so WSL Python can actually reach `/health` and
    `/v1/chat/completions`
- `memory/brain.py`, `llm/boot.py`, `tools/tts.py`: missing optional deps
  (`chromadb`, `psutil`, `numpy`) now degrade gracefully enough for harness/runtime
  startup instead of crashing at import time.
- `core/engines/state_mutation.py`: task/event completion normalization now strips
  quoted literals before checking completion hints and ignores obvious FILE_WORK
  requests, which stops prompts like `Create a file called verify_test.txt with the
  content 'done'` from being hijacked into `COMPLETE_TASK`.
- `core/executor.py`: terminal missing explicit file targets now parse the requested
  path from `FILE_OP target not found: ...` summaries even when `requested_path` is
  absent, so existing-file edit stages stop honestly instead of rerouting to lookalike
  files.
- `core/orchestrator_phases.py`: the `Did you mean ...?` confirmation pause is now
  limited to explicit delete/remove cases; missing edit targets stay terminal honest
  failures.

Revalidated:
- `python3 scripts/file_edit_smoke_test.py --json`
- `python3 scripts/missing_file_no_reroute_smoke_test.py --json`
- `python3 scripts/file_target_confirmation_smoke_test.py --json`
- `python3 -m compileall app.py config.py core ui memory tools llm AGENTS scripts`

### Planner prompt budget fix for large exact file reads

Escalation `codex-repair-20260325-223223` was a real planner-context overflow, not a
llama-server regression. A `FILE_WORK` read of `notes/coder-log.md` stored a large
`FILE_READ_EXACT_CONTENT` block in scratchpad, and the next planner call replayed too much
of that exact text into the manager prompt for the active 8192-token llama.cpp context.

Fix:
- `core/prompt_builder.py`: planner prompt assembly now compacts `FILE_READ_EXACT_CONTENT`
  blocks before final scratchpad truncation. Code files keep a larger preview, non-code
  files keep a smaller preview, and oversized blocks are marked as planner-budget
  truncations instead of being replayed verbatim.
- `scripts/planner_boundary_smoke_test.py`: added a regression proving a large exact-read
  scratchpad no longer inflates the real manager prompt beyond a bounded size while still
  preserving the exact-read path marker.

## 2026-03-25

### FILE_WORK domain-escape rail for task/event helpers

Escalation `codex-repair-20260325-221018` showed a FILE_WORK stage redoing an already
completed event-close prerequisite by calling imagined task/event helpers from `RUN_CODE`
(`list_events()`, `close_event()`) after Stage 1 had already archived the blocking event.

Fix:
- `core/engines/file_work.py`: added `_check_run_code_task_event_escape()` to AST-scan
  FILE_WORK `RUN_CODE` payloads for task/event helper calls or store access and block them
  with a domain-boundary error.
- `core/executor.py`: applies the new rail before the existing RUN_CODE file-dependency
  mutex and aborts after 3 repeated escape attempts, matching other blocked-action rails.
- `scripts/file_work_engine_smoke_test.py`: added regression coverage for the blocked
  `list_events()/close_event()` pattern and a non-blocked pure file-rename script.

### Consolidation spin-loop: three remaining issues fixed

**Three issues found after previous spin-loop fix and fixed together:**

**1. Action-based repeat counter — `core/executor.py`**

`_decision_repeat_count` uses the full decision signature (thought + tool + proposal).
LLM thoughts can vary slightly between steps, resetting the counter and letting the loop
run to `max_steps`.  Added `_last_blocked_action` / `_blocked_action_count` instance
variables (initialised at both `__init__` and per-stage init).  All three violation
handlers now track repeats by action name alone (`planned_action or tool_tag`) — identical
regardless of thought variation.  Hard-abort fires at `_blocked_action_count >= 3` (third
blocked attempt of the same action).

**2. Inspection stage exits as SUCCESS after extension_inventory — `core/executor.py`**

When the planner overshoots an INSPECTION stage (runs `extension_inventory`, then tries
mutating actions), the stage hard-aborted with `success = False`.  The orchestrator
reported a failure even though the inspection goal was met.  Added a post-loop recovery
block (after the existing diagnosis-proposal recovery path, before the final `return`):

```python
if (
    not success
    and FileStagePolicy.stage_is_non_mutating_file_stage(stage)
    and isinstance(self._last_successful_tool_result, dict)
    and str(self._last_successful_tool_result.get("action", "")).lower() == "extension_inventory"
):
    return True, self.scratchpad
```

The planner overstepping into mutating actions in an inspection stage is a boundary
violation, not a task failure.  The stage now returns success so the orchestrator moves
to the consolidation stage cleanly.

**3. `exclude_files` prefix/glob matching — `tools/workspace_extension_actions.py`**

`consolidate_by_extension` only did exact name matching.  The planner passes `"keep_*"` for
"exclude files prefixed with keep_" but `sorted(["keep_*"])` matched nothing.  Fixed by
detecting entries ending in `*` in the exclusion-parsing loop and routing them to a new
`excluded_prefixes: list[str]` collection.  The per-file check now also tests
`any(src_name_lower.startswith(p) for p in excluded_prefixes)`, case-insensitively.

**Tests added (`scripts/test_engines.py` — `TestConsolidateExcludePrefix`, 3 tests):**
- Exact exclusion still works after the change
- `"keep_*"` excludes all `keep_`-prefixed files, leaves others
- Matching is case-insensitive (`Keep_upper.txt` excluded by `"keep_*"`)

109/109 engine smoke tests pass.

---

### Violation spin-loop hard-abort (`_decision_repeat_count` guard)

**Bug:** Even with escape hints injected, the planner ignored them and repeated the same
blocked action 9 times.  Root cause: the planner's THOUGHT is re-derived from scratch each
step (it always concluded `ensure_dirs` was needed to satisfy "destination folder is
identified") and the SYSTEM HINT, being soft, lost the argument every time.

**Fix — `core/executor.py` (all three stage-policy violation blocks):**

Added a hard-abort check after the hint is appended in each block:

```python
if self._decision_repeat_count >= 2:
    self.ui.put(("agent_log", "   -> ABORT: same action blocked 3+ times, forcing stage exit."))
    break
continue
```

`_decision_repeat_count` is already incremented at line ~387 before violation checks fire,
so by the third identical decision (count == 2) the stage loop breaks immediately instead
of continuing.  Applied to all three blocks: RUN_CODE non-mutating, FILE_OP non-mutating,
FILE_OP STRUCTURE_PREP.  The loop now terminates after at most 3 identical blocked steps
rather than running to `max_steps`.

106/106 engine smoke tests pass.

---

### ROUTER phrasing + STRUCTURE_PREP consolidation fix (post-retry testing)

**Two bugs found during live workspace-cleanup test and fixed:**

**1. "Shall I retry?" + [ROUTER] contradiction — `core/engines/summary.py`**

The FAILED instruction (line ~362) said "you may append [ROUTER] to trigger a fresh routing
pass" with no phrasing guidance.  The LLM responded with "Shall I retry?" while also appending
[ROUTER], which triggered the retry immediately — making the question nonsensical.

- Added an explicit phrasing rule to the instruction: when [ROUTER] is appended the persona
  must use declarative language ("Retrying now", "Initiating another pass") and must NOT ask
  for permission in the same message, because [ROUTER] fires the retry immediately.

**2. STRUCTURE_PREP guard blocking `consolidate_by_extension` — `core/executor.py`**

The workspace-cleanup skill stages: Inspection → Consolidation → Cleanup.  The Consolidation
stage was tagged `file_stage_kind: "STRUCTURE_PREP"` by the planner.  The STRUCTURE_PREP
guard's allowlist only contained `{"ensure_dir", "ensure_dirs", "read_text", "read_many",
"list_tree", "find_paths", "extension_inventory"}` — missing the two operations that ARE the
consolidation step.  The planner tried `consolidate_by_extension`, got SECURITY VIOLATION,
re-ran `extension_inventory` (the last allowed action it knew), and the stage failed/looped.

Additionally the STRUCTURE_PREP guard had no escape hint (unlike the INSPECTION guard fixed
in the previous session), so any other blocked action would also loop silently.

- Added `"consolidate_by_extension"` and `"delete_empty_dirs"` to the STRUCTURE_PREP allowlist
- Added escape hint to the STRUCTURE_PREP SECURITY VIOLATION block (mirrors the INSPECTION
  guard pattern added earlier today)

106/106 engine smoke tests pass.

---

### Persona verb + multi-stage fast-path fix (post-retry testing)

**Two remaining persona bugs confirmed during retry and fixed:**

**1. "Updated" verb hardcoded in fast-path — `core/engines/summary.py`**

`extract_verified_result` maps `write_text` → `"Updated …"` regardless of the `operation_label`
field the executor already writes into the `FILE_WORK_VERIFIED_RESULT` payload.  This function
feeds the `direct_answer` fast-path in `context_pack.py`, bypassing `extract_observation_detail`
where the earlier `operation_label` fix lived.

- Added `operation_label = data.get("operation_label")` extraction in `extract_verified_result`
- Verb now resolves to `"Created"` / `"Updated"` from the payload rather than being hardcoded
- Fallback stays `"Updated"` for payloads that pre-date the field

**2. Multi-stage fast-path showing only last stage — `core/engines/context_pack.py`**

`verified_file_work_answer` (from `extract_verified_result`) only covers the latest stage's
entries.  For multi-stage plans it fired as the `direct_answer`, silencing every stage except
the last.

- Suppressed the fast-path when `outcome_block` contains more than one `=== STAGE` marker
- For multi-stage turns the persona now receives the full outcome block via the LLM path,
  where `build_outcome_block`'s multi-stage instruction ("cover the whole task") applies

Two new tests added to `scripts/test_engines.py` covering `operation_label` "created" and
"updated" cases.  98/98 engine smoke tests pass.

---

### Planner spin-loop fix — non-mutating inspection stage escape hint (`core/executor.py`)

**Bug:** Extension-inventory test showed the planner looping 9+ times after a SECURITY
VIOLATION on a plain inspection stage.  The planner tried `consolidate_by_extension`, got
blocked, then pivoted to `write_json`, which was also blocked — repeating forever because no
escape hint was ever injected.

**Root cause:** The hint injection in the FILE_OP non-mutating guard (lines ~689) was gated:
```python
if FileStagePolicy.is_file_planning_stage(stage) or FileStagePolicy.stage_requires_user_approval(stage):
    hint = "SYSTEM HINT: Proposal/approval stages must not write files…"
```
For a plain `INSPECTION` stage both conditions are False → no hint → planner spins.

**Fix — `core/executor.py` (FILE_OP guard, lines ~689-701):**
- Moved `hint` assignment before the `if not self.scratchpad…` dedup check
- Added `else` branch: inspection stages get a distinct hint:
  `"SYSTEM HINT: This stage is inspection-only — no file writes are permitted. Return tool null with is_complete true and summarise your findings in the proposal field."`
- Every non-mutating SECURITY VIOLATION now unconditionally appends an escape hint

**Fix — `core/executor.py` (RUN_CODE guard, lines ~675-680):**
- Same pattern: added an escape hint after the SECURITY VIOLATION entry for the RUN_CODE
  non-mutating guard, which previously had no hint at all

**Tests added (`scripts/test_engines.py` — `TestExecutorNonMutatingHints`, 8 tests):**
- Verify `stage_is_non_mutating_file_stage` → True for INSPECTION stage
- Verify `is_file_planning_stage` → False for plain INSPECTION stage
- Verify `stage_requires_user_approval` → False for plain INSPECTION stage
- Verify `is_file_planning_stage` → True for planning/proposal stage
- Confirm planning-stage and inspection-stage hints are different strings
- Confirm both hint strings carry `is_complete true` and `proposal field` directives

106/106 engine smoke tests pass.

---

### R-6 bug-fix pass — three regression fixes (post-testing)

**Three bugs found during live testing and fixed:**

**1. R-6 RUN_CODE bypass — `core/engines/file_work.py` + `core/executor.py`**

The mutex check only fired for `FILE_OP` tools. If the planner deleted a file via
`RUN_CODE` (e.g. `os.remove("alpha.txt")`), `_check_active_dependency` was never reached.

- Added `FileWorkEngine._check_run_code_dependency(tool_tag, oss)` — scans Python code for
  `os.remove`, `os.unlink`, `shutil.rmtree`, `os.rename`, `shutil.move` and `Path.unlink/rename`
  patterns, extracts string-literal path arguments, then calls `find_references`
- Executor: added a parallel RUN_CODE mutex check block before the existing FILE_OP block
  (lines ~704); same fatal-return path on conflict

**2. Premature stage exit on single CREATED constraint — `core/engines/file_work.py`**

`derive_constraints()` auto-derived a `CREATED` constraint from each successful `write_text`
result (if exactly one file was created). This caused `evaluate_with_constraints()` to return
`VERIFIED` after the first write in a multi-file stage, exiting the stage before the remaining
files were written.

- Removed CREATED from auto-derivation. Only DELETED and MOVED are still auto-derived from
  tool results (they are unambiguous single-operation completions). CREATED constraints must now
  come from the planner explicitly (which R-5 ensures).
- Explicit stage-card constraints still take priority (unchanged).

**3. Only last file in LAST_LOG — `core/executor.py` + `core/engines/summary.py`**

`_append_verified_file_work_result_note` built its path list from `_last_successful_tool_result`
only, so multi-tool stages reported just the final operation. Also, the persona had no clean
signal for "created" vs "updated", guessing from the generic "Wrote text file: …" summary.

- Added `_stage_all_mutated_paths: list[str]` accumulator to `StageExecutor`, reset each stage.
  After every successful tool call the executor appends `created_files` + `updated_files` to it.
- `_append_verified_file_work_result_note` merges the accumulator into the paths list and
  adds an `operation_label` field ("created" / "updated" / "modified") to the payload.
- `SummaryEngine.extract_observation_detail` recognises `operation_label` and emits an explicit
  "Created: a.txt, b.txt. …" or "Updated: x.txt. …" line so the persona uses the correct verb.

All 96 engine smoke tests pass.

---

### R-5 Planner Schema Compliance + R-6 State Mutex

**Problem solved (R-5):** Planner could silently omit the `constraints` block even after R-5 shipped the schema — LLMs are stochastic. `derive_constraints()` is a strong fallback but cannot recover exclusion intent (only the Planner knows what was excluded). Silent fallthrough left the loop re-openable under long context.

**Problem solved (R-6):** File and Task domains operated without coordination. A DELETE or MOVE could proceed while an active Task referenced the target path, leaving a dangling reference with no warning.

**1. Schema compliance gate — `core/executor.py`**

- At both `is_complete` completion paths, for stages where `stage_requires_file_verification` is True, executor now checks that `constraints` is present in the Planner's decision
- First miss: injects schema reminder ("Re-emit your completion with `constraints` populated") and retries — `_constraints_reminder_sent` flag per stage prevents double-injection
- Second miss: logs `constraint_violation` to alerts via `StatsCollector.note_constraint_violation` and falls through to `derive_constraints()` as before
- `StageExecutor.__init__` gains `stats_collector` and `operational_state_service` optional params

**2. Constraint violation logging — `core/engines/stats_collector.py`**

- Added `note_constraint_violation(stage_goal, attempt)` — appends a timestamped line to the alerts file (same path as latency alerts)

**3. Prompt tightening — `data/prompts/manager.txt`**

- `constraints` field now labelled **required** for FILE_WORK completions
- Added explicit warning: "If you do not emit a constraints block, the verification step will fail and you will be asked to retry."
- Clarified when to use `[]` (no file op completed) vs omit (non-FILE_WORK stages only)

**4. Cross-domain dependency check — `core/operational_state_service.py`**

- Added `find_references(path)` — originally a case-insensitive substring scan over all active tasks and events; now backed by the shared matcher in `core/file_reference_matcher.py` so humanised filename mentions and close aliases also resolve consistently

**5. Fatal block in FileWorkEngine — `core/engines/file_work.py`**

- Added `_check_active_dependency(tool_tag, operational_state_service)` classmethod — extracts DELETE/MOVE target path(s) and calls `find_references`
- `should_block()` gains optional `operational_state_service` kwarg; dependency guard runs first (before content-edit gate), fires on RELOCATION stages too
- Returns `FileWorkBlock(blocked=True, fatal=True, reason="ACTIVE_TASK_DEPENDENCY: ...")`

**6. Fatal-block execution path — `core/contracts.py` + `core/executor.py`**

- Added `fatal: bool = False` to `FileWorkBlock` dataclass
- In executor, `_block.fatal` causes immediate stage stop (`return False, self.scratchpad`) instead of `continue` — persona then sees the dependency reason and reports it

**7. Orchestrator wiring — `core/orchestrator_phases.py`**

- Passes `stats_collector=orc.stats_collector` and `operational_state_service=orc.prompt_context.operational_state_service` to `StageExecutor`

**Smoke test:** 113 passed, 0 failed.

---

### R-5 Typed Success Constraints (Planner → Verifier boundary)

**Problem solved:** VerificationEngine was guessing at what the Planner meant by reading prose `success_condition` text and running heuristics against it. This is where fake-success lived — Planner claims done, Verifier can't reliably contradict, Persona reports success on a failed operation.

**1. `PlanConstraint` schema — `core/contracts.py`**

- Added `PlanConstraintType` Literal: `EXCLUSION | MOVED | DELETED | CREATED | MODIFIED | COUNT`
- Added `PlanConstraint` TypedDict with fields: `type`, `scope`, `path`, `from_path`, `to_path`, `pattern`, `directory`, `expected`, `expected_present`, `expected_absent`
- Added `constraints: List[PlanConstraint]` to `StageCard` (optional — falls back when absent)

**2. Constraint derivation — `core/engines/file_work.py`**

- Added `FileWorkEngine.derive_constraints(stage, tool_result=None)` static method
- Priority 1: returns explicit `stage["constraints"]` if router or planner set them
- Priority 2: derives MOVED / DELETED / CREATED from unambiguous single-operation tool results (`requested_moves`, `deleted_files`, `created_files`)
- Priority 3: returns `[]` — caller falls through to existing RULES → LLM path unchanged

**3. Constraint-first verification — `core/engines/verification.py`**

- Added `evaluate_with_constraints(constraints, workspace)` — iterates constraint list, dispatches to six `_check_*` helpers, returns `VerificationResult` or `None` when nothing evaluable
- Added six deterministic filesystem checkers: `_check_exclusion`, `_check_moved`, `_check_deleted`, `_check_created`, `_check_modified`, `_check_count`
- Modified `evaluate()`: constraint path runs first (no LLM call); falls through to existing RULES → LLM → STATE_CHECK path when no constraints derivable
- Checker path reported as `"RULES"` — structured constraint evidence surfaces in `evidence_summary`

**4. Planner instruction — `data/prompts/manager.txt`**

- Added `"constraints": []` field to the completion JSON block
- Added constraint-type reference with examples for all six types
- Planner instructed to populate constraints for FILE_WORK stages only; emit empty list if uncertain

**Smoke test:** 113 passed, 0 failed.

---

### Follow-up session: Bootstrap gating, knowledge bypass correction, architecture audit

**1. Bootstrap injection gated to session-start / style-change only**

- `core/orchestrator.py` — Added `_bootstrap_injected_for_style: str = ""` to `Orchestrator.__init__`. Tracks which style's bootstrap was last injected into history.
- `core/orchestrator_phases.py` → `phase_persona()` — Bootstrap now only fires when `orc.ss.name != orc._bootstrap_injected_for_style`. First turn injects and sets tracker. Subsequent turns skip. Style change re-injects and resets tracker. Previous design ("prime persona tone on every turn") wasted tokens and polluted the history view.

**2. Knowledge queries removed from readonly fast-path**

- `core/prompt_context.py` — `build_readonly_state_answer` now returns `""` when `query_kind == "knowledge"`. Hardcoded subject extraction + `"Your {subject} is {value}."` template was producing unnatural output ("Your which drink i like is coke."). Knowledge queries now fall through to normal routing → persona with `[WORLD STATE]` context, which handles them naturally. Operational state queries (tasks / events) retain the fast-path — their structured output is clean.

**3. Full architecture audit — items confirmed already implemented**

- **R-1 Context Arbitration** — `apply_context_arbitration()` called at both persona paths. `PERSONA_CONTEXT_ARBITRATION_TABLE` fully defined. `_tail_block_context_arbitration` registered. Fully live.
- **`[EXPLAIN_LAST_TURN]`** — fully implemented in `turn_explanation.py`, interceptor in `route_normalizer.py`, tail block registered, `phase_persona` handles the turn type, §13.11 already marked ✓ IMPLEMENTED.
- **Stale-date scan** — full grep of scripts confirmed no remaining critical stale-date assertions. Remaining hardcoded dates are inert fixture timestamps.

---

### Follow-up session: R-3 bootstrap injection, docs cleanup, stale-date test fixes

**1. R-3 Style Card Bootstrap Injection — implemented**

- `core/orchestrator_phases.py` → `phase_persona()`: bootstrap turns from `orc.ss.bootstrap` are now prepended to the in-memory history list after compression, before `build_persona_messages`. In-memory only — not persisted to chat, not seen by compressor. Skipped for `explain_last_turn` turns. 5 lines.
- Retired R-3 from ROADMAP.md → TRIGGER_FLOW.md §13.14.

**2. Turn Explanation (`[EXPLAIN_LAST_TURN]`) — docs fixed**

- Feature was already fully implemented: `_hook_upsert_last_turn_explanation_context` (snapshot on turn end), `_registered_explain_last_turn_interceptor` (route normalizer), `_tail_block_explain_last_turn` (tail block), `render_explain_last_turn_block` (renderer). All wired.
- TRIGGER_FLOW.md §13.11 status was wrong ("Planned. Not yet implemented.") — corrected with accurate description.

**3. Stale-date smoke tests — 4 files fixed**

- `scripts/knowledge_readonly_smoke_test.py`: event date `2026-03-24` → `today + 1 day` (dynamic); assertion updated.
- `scripts/state_mutation_engine_smoke_test.py`: mock date → `2027-06-15`; both event answer assertions updated; reminder user_msg changed from date-relative "the 25th" to ISO `2027-04-15`; reminder goal assertion updated.
- `scripts/state_domain_harness_smoke_test.py`: reminder user_msg → ISO `2027-04-15`; assertion `target_date == "2027-04-15"`.
- `scripts/followup_resolution_engine_smoke_test.py`: fixture event date → `2027-06-15`.

**Smoke test:** 113 passed, 0 failed.

---

### Architectural reliability pass — routing pipeline, follow-up resolution, fake success, context arbitration

Four structural problems fixed across a single session:

**1. Follow-up resolver: deterministic-primary (was LLM-primary)**

- `core/engines/followup_resolution.py` — `refine_with_llm` restructured so the deterministic path is tried first. LLM is only called for knowledge mutations (`contextual_remember_followup`, `ambiguous_memory_followup`) and affirmative-after-offer. All other cases (retry phrases, vague referrals with no assistant context, self-contained tasks) pass through without a second LLM call. Eliminates planner_errors caused by the resolver misrouting vague follow-ups like "Can you check?".

**2. Inspection stage fake success gap closed**

- `core/executor.py` — Both inspection stage completion paths (is_complete flag path and null tool path) now require `completion_handoff` length ≥ 40 chars instead of just non-empty. Trivial "Done." responses no longer pass the `stage_requires_analysis_report` verification gate.

**3. Pre-LLM bypass: operational state queries**

- `core/orchestrator_phases.py` — Added 5th pre-LLM bypass in `phase_route` using `build_readonly_state_answer`. Queries like "show me all upcoming events" are answered deterministically from event/task stores without touching the router LLM. Route forced to `CHAT`, answer delivered in persona. Mirrors the existing environment query bypass pattern.
- `docs/architecture/TRIGGER_FLOW.md` — Updated bypass diagram and description to include 5th bypass.

**4. Route clarifier: history-aware suppression**

- `core/engines/route_clarity.py` — `should_force_clarification` now accepts `recent_history` param. Suppresses clarification when any assistant turn exists in history (follow-up context is available). Added `_RETRY_HINT_RE` and `_RETRY_PREFIX_RE` for retry phrase detection. Removed hardcoded `_fallback_question` string entirely. `_build_clarification_route` takes optional `question: str = ""` — omits "Preferred clarification question" line when empty.

**5. R-1 Context Arbitration Policy — confirmed already implemented**

- `core/contracts.py` — `PERSONA_CONTEXT_ARBITRATION_TABLE` fully defined for 7 turn types.
- `core/engines/context_pack.py` — `apply_context_arbitration()` enforces the table; `_tail_block_context_arbitration` emits `[CONTEXT_ARBITRATION_RULE]` to persona each turn.
- `core/orchestrator_phases.py` — enforcement calls live at both persona paths (lines ~1159 and ~2028).
- Retired from ROADMAP.md → TRIGGER_FLOW.md §13.12.

**6. R-2 Style Card knowledge=false completeness fix**

- `core/engines/context_pack.py` — `vision_session_memory` injection gated behind `knowledge_enabled`.
- `core/orchestrator_phases.py` — `compress_history` call passes `existing_summary=""` when `knowledge_enabled=False`. `_hook_deferred_conversation_summary` returns early when `knowledge_enabled=False`, preventing cross-session summary growth during immersive style turns.
- Retired from ROADMAP.md → TRIGGER_FLOW.md §13.13.

**Smoke test:** 113 passed, 0 failed.

---

## 2026-03-23 - Explicit missing file target confirmation + harness slice fix

- Runtime fix:
  - explicit mutating file targets no longer silently treat an absent exact path as "already satisfied" when a close workspace candidate exists
  - `delete bob.txt` now pauses with a confirmation question like `Did you mean b.txt?`, then either proceeds on confirmation or leaves the workspace unchanged on cancellation
  - implemented via:
    - `core/file_target_confirmation.py`
    - `core/orchestrator_phases.py`
    - `core/routing/route_normalizer.py`
    - `core/file_stage_policy.py`
    - `core/executor.py`
- Harness note:
  - the first cancel-path smoke false-red was not a runtime failure; hidden-system-message removal during the turn could shrink the chat snapshot and make `AGENTS/harness/session.py` miss the just-written assistant reply
  - `send_text()` now falls back to slicing from the latest matching user turn when the raw `msg_start` slice contains no assistant message
- Added coverage:
  - `scripts/file_target_confirmation_smoke_test.py`
- Validation:
  - `python3 -m py_compile AGENTS/harness/session.py core/executor.py scripts/file_target_confirmation_smoke_test.py` — clean
  - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
  - `./.venv/Scripts/python.exe scripts/file_target_confirmation_smoke_test.py --json` — pass
  - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
  - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
  - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
  - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
  - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
  - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
  - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
  - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
  - `python3 scripts/summary_engine_smoke_test.py` — pass
  - `python3 scripts/context_pack_engine_smoke_test.py` — pass

## 2026-03-23 - Short task/event verification follow-up routing

- Runtime fix:
  - short immediate follow-ups like `check to confirm?`, `to confirm`, `verify`, or `double check` no longer fall into generic web/workspace lookup clarification when the live context is really a just-finished task/event thread
  - `FollowupResolutionEngine` now detects those short verification follow-ups, resolves them against recent task/event state, and prefers a readonly operational-state chat route such as `What events do I have scheduled?`
- Added coverage:
  - `scripts/route_boundary_smoke_test.py` now pins the exact `check to confirm?` case against hidden `[LATEST_RUNTIME_CONTEXT]` after an event-removal turn
- Validation:
  - `python3 -m py_compile core/engines/followup_resolution.py scripts/route_boundary_smoke_test.py` — clean
  - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
- 2026-03-20: Centralized the live environment query predicate into routing-owned shared code.
  - Problem:
    - the date/time/day bypass had the right layered behavior, but its shared predicate lived as a private method on `StateMutationEngine`
    - `phase_route()` and `route_normalizer.py` were reaching across boundaries into that private state-mutation implementation, which made ownership unclear in the trigger flow
  - Fix:
    - added `core/routing/environment_queries.py` with `looks_like_live_environment_query()`
    - switched `core/orchestrator_phases.py` to use the shared helper for the true pre-LLM bypass and the `phase_search()` safety net
    - switched `core/routing/route_normalizer.py` to use the same helper for post-router repair
    - switched `core/engines/state_mutation.py` readonly-state guard to the same helper and removed the duplicated private predicate
    - updated `docs/architecture/TRIGGER_FLOW.md` to describe the ownership correctly: shared predicate in routing, first bypass in `phase_route()`, safety-net guards later
  - Validation:
    - `python3 -m py_compile core/routing/environment_queries.py core/orchestrator_phases.py core/routing/route_normalizer.py core/engines/state_mutation.py` — clean
- 2026-03-20: Clarified route-phase UX for live environment bypasses so they no longer look like real Secretary/router turns.
  - Problem:
    - even when the environment-query bypass worked, `phase_route()` still set the top-bar mode to `ROUTING` and logged `SECRETARY (Routing)` before the bypass checks ran
    - that made direct date/time/day turns look as though the Router LLM had been invoked when it had not
    - the tightened smoke also exposed one predicate gap: `whats todays date` (no apostrophe) still fell through to the Router LLM even though it answered correctly
  - Fix:
    - `core/orchestrator_phases.py`: changed the phase start to a neutral `ROUTE CHECK` / `ANALYZING` state, and only emits `ROUTING` plus the `SECRETARY (Router LLM)` log immediately before the actual router prompt is built
    - bypass logs now say explicitly that the Secretary/router LLM was skipped
    - `core/routing/environment_queries.py`: broadened the live-environment predicate to accept `whats ...` as well as `what's ...`
    - `scripts/live_environment_chat_smoke_test.py`: now asserts not just “no search,” but also “no visible Routing... status” and “no Secretary/router log” for the live-environment path
  - Validation:
    - `python3 -m py_compile core/routing/environment_queries.py core/orchestrator_phases.py scripts/live_environment_chat_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-20: Added deterministic source clarification when a lookup request could mean either web search or workspace file lookup.
  - Problem:
    - route normalization would aggressively convert broad verbs like `search for`, `look for`, `find`, or `locate` into workspace FILE_WORK when the subject looked document-like, even when a fresh-session request could just as plausibly mean a web search
    - there was no dedicated route-layer rule for “ask the user instead of guessing” in unresolved web-vs-workspace lookup cases
  - Fix:
    - `core/routing/route_normalizer.py`: added an ambiguity guard that turns unresolved cross-source lookup requests into a clarification pause asking whether the user wants the web or workspace files
    - `core/routing/route_normalizer.py`: added deterministic next-turn resolution for short answers like `web` and `workspace files` by reading the prior clarification goal from `[LATEST_RUNTIME_CONTEXT]`
    - the new guard stays out of the way when the source is already explicit (`workspace`, `file`, `web`, `online`, etc.) or when recent runtime/file context already resolves the intent
  - Regressions added:
    - `scripts/route_boundary_smoke_test.py`: now covers unresolved ambiguity -> clarification, `workspace files` follow-up -> FILE_WORK, and file-context carry-forward -> no clarification
    - `scripts/lookup_source_disambiguation_smoke_test.py`: end-to-end harness smoke proving Piper asks `web or workspace?` for `Search for grocery.` and resolves `workspace files` to a real workspace lookup on the next turn
  - Validation:
    - `python3 -m py_compile core/routing/route_normalizer.py scripts/route_boundary_smoke_test.py scripts/lookup_source_disambiguation_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/lookup_source_disambiguation_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-20: Fixed explicit web-topic searches being hijacked by filename-style tokens inside the query.
  - Problem:
    - a clearly web-scoped request like `search for the latest news on llama.cpp performance benchmarks` still fell into workspace FILE_WORK
    - the underlying cause was `route_normalizer.py` treating `llama.cpp` as an explicit workspace file target before the web/news intent could win, which produced `No matching files found.`
  - Fix:
    - `core/routing/route_normalizer.py`: added `_normalize_explicit_web_search()` so explicit web/news/current lookup requests deterministically normalize to `SEARCH`
    - `core/routing/route_normalizer.py`: taught workspace lookup normalization to stand down when the request is explicitly web-scoped and does not also contain strong workspace wording
    - mixed web + strong workspace wording now routes to the existing clarification pause instead of guessing
  - Regressions added:
    - `scripts/route_boundary_smoke_test.py`: now asserts that the exact `llama.cpp performance benchmarks` phrase normalizes to `SEARCH` rather than FILE_WORK
    - `scripts/explicit_web_search_topic_smoke_test.py`: harness smoke proving the exact live phrase enters the async search path, sees the fake search query, and never says `No matching files found.`
  - Validation:
    - `python3 -m py_compile core/routing/route_normalizer.py scripts/route_boundary_smoke_test.py scripts/explicit_web_search_topic_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/lookup_source_disambiguation_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/explicit_web_search_topic_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-19: Added an end-to-end async search smoke and aligned the harness search lifecycle with the real controller.
  - Problem:
    - there was no direct harness regression proving the SEARCH -> background thread -> `search_result` -> reporter -> persona flow
    - the harness still passed no-op search state callbacks into `run_agent_loop()`, so an async search turn could appear idle before the reporter handoff finished
  - Fix:
    - `AGENTS/harness/session.py`: added harness-owned search-in-flight tracking and fed the real callbacks into `run_agent_loop()`
    - `AGENTS/harness/session.py`: `_wait_for_idle()` now stays active while a background search is in flight
    - `scripts/search_flow_smoke_test.py`: new deterministic harness smoke that stubs `tools.search.perform_search`, asserts `search_result` delivery, hidden search summary insertion, search-first-pass prompt logging, and a final reporter/persona reply grounded in the fake search data
  - Validation:
    - `python3 -m compileall AGENTS/harness/session.py scripts/search_flow_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
- 2026-03-19: Implemented the trigger-flow memory pre-fetch improvement target.
  - Problem:
    - first-pass persona/search turns still requested only 5 brain recall hits, which kept the pre-fetch window narrower than the current trigger-flow target
    - low-relevance brain hits had no first-pass distance filter, so widening recall would have risked more memory noise without an extra guard
  - Fix:
    - `core/engines/context_pack.py`: default first-pass `brain_limit` is now `9`, and recall hits with a `distance` field are filtered to `< 0.40`
    - `core/prompt_context.py`: service defaults now match the wider first-pass recall window
    - `core/orchestrator_phases.py`: `phase_persona()` and `phase_search()` now request 9 first-pass brain hits for normal turns while keeping the reduced live-screen path at 2
    - `scripts/context_pack_engine_smoke_test.py`: added coverage for `n_results=9` and low-relevance memory-hit filtering
    - `scripts/test_engines.py`: added focused regression coverage for the widened recall call and distance filter
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe -m pytest scripts/test_engines.py -k "build_persona_pack_calls_brain_recall or build_persona_pack_filters_low_relevance_brain_hits" -q` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
- 2026-03-19: Hardened the planner boundary contract and closed a FILE_OP read-shape regression exposed by the broad pack.
  - Problem:
    - the roadmap's planner-boundary target was only fully enforced inside Python dataclasses; the planner prompt still mainly saw the raw stage card instead of the normalized boundary contract
    - after surfacing that normalized contract in the planner prompt, the full `file_edit_smoke_test` caught a brittle FILE_OP failure: the planner issued `read_text` with a plural `paths` array after `find_paths` returned multiple grocery-list candidates, and the runtime rejected it before the stage could mutate the file
  - Fix:
    - `core/planner_boundary.py`: `validate_input()` now writes the normalized planner-boundary fields back into the stage card (`objective`, normalized `stage_type`, `active_targets`, `evidence_required`) so downstream consumers share one resolved contract
    - `core/prompt_builder.py`: added `_render_planner_boundary_block()` and wired `[PLANNER_BOUNDARY]` replacement into `build_planner_prompt(...)`
    - `core/executor.py`: planner calls now pass the validated `planner_input` into `PromptBuilder.build_planner_prompt(...)`, and the step directive remains a `user` message with the current Qwen template-compatible marker
    - `data/prompts/manager.txt`: added the normalized planner-contract section and an explicit single-`path` vs plural-`paths` FILE_OP read rule
    - `tools/registry.py`: added a concrete `read_text` syntax example plus explicit `read_text`/`read_many` argument-shape guidance to the generated FILE_WORK tool guide
    - `tools/workspace_file_actions.py`: FILE_OP dispatch now gracefully normalizes `read_text` + `paths` into `read_many`, and `read_many` + `path` into a one-item `paths` list, so harmless singular/plural read-shape drift does not brick the stage
    - `scripts/planner_boundary_smoke_test.py`: added regression coverage for stage write-back and rendered `[PLANNER_BOUNDARY]` prompt content
    - `scripts/redundant_code_read_guard_smoke_test.py`: updated the stale smoke to use `FileWorkEngine.should_block(...)` after the executor-owned helper removal
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/planner_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
- 2026-03-19: Wired typed verification deeper into the persona handoff and fixed the Qwen single-system persona ordering bug it exposed.
  - Problem:
    - the roadmap's verification-handoff target was only partially implemented: FILE_WORK could surface a typed `VerificationResult`, but the persona runtime contract dropped important fields (`recommendation`, `checker_path`) and mutation stages never populated `executor._last_verification` at all
    - once mutation verification started flowing through the same path, failed mutation turns exposed a Qwen prompt-shaping bug: `build_persona_messages()` still appended the final outcome as a trailing `system` message after the last `user` message, which violates the current llama.cpp single-system template contract
  - Fix:
    - `core/contracts.py`: extended `PersonaRuntimePack` with `verification_recommendation` and `verification_checker_path`
    - `core/engines/context_pack.py`: `build_persona_runtime_pack(...)` now preserves the full typed verification payload, uses `effective_success` to derive failure state, and `build_persona_directive_pack(...)` now emits authoritative `[VERIFICATION_RESULT]`, `[PARTIAL_VERIFICATION_RULE]`, and `[FAILED_VERIFICATION_RULE]` blocks so persona can report PARTIAL and FAILED outcomes honestly
    - `core/executor.py`: mutation stages (`TASK_EVENT_WORK`, `MEMORY_WORK`) now build a stage outcome pack and run `VerificationEngine.evaluate_mutation(...)`, storing the result in `_last_verification` so the orchestrator can hand the typed verdict to persona just like FILE_WORK
    - `core/prompting.py`: for single-system-message runtimes, persona now folds the final outcome block into the existing runtime system payload instead of appending a second trailing `system` message after the user content
    - `scripts/test_engines.py`: added focused coverage for typed verification fields in the runtime pack plus PARTIAL and FAILED directive rendering
    - `scripts/context_pack_engine_smoke_test.py`: added runtime and directive assertions for PARTIAL and FAILED verification handoff
    - `scripts/persona_system_event_role_smoke_test.py`: updated the stale system-role expectation to match the merged-runtime contract and keep the Qwen ordering fix covered
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/context_pack.py core/executor.py scripts/context_pack_engine_smoke_test.py scripts/test_engines.py` — clean
    - `./.venv/Scripts/python.exe -m pytest scripts/test_engines.py -k "evaluate_mutation or build_persona_runtime_pack_surfaces_typed_verification_fields or build_persona_directive_pack_includes_partial_verification_rule or build_persona_directive_pack_includes_failed_verification_rule" -q` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/state_mutation_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/persona_system_event_role_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe -X utf8 scripts/memory_state_harness_smoke_test.py --json --scenario memory_remove_direct --scenario memory_remove_already_absent > /tmp/piper_memory_state_harness.json` — pass (redirected to avoid Windows console encoding noise while printing JSON)
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
- 2026-03-19: Finished the remaining trigger-flow alignment by adding typed route-boundary validation and persona history compression with persistence.
  - Problem:
    - the route phase still relied on raw `parse_json_response()` plus ad hoc fallback behavior in `phase_route()`, `FollowupResolutionEngine`, and `RouteClarifier`, so malformed LLM output could silently drift into normalization instead of failing through one declared boundary rule
    - persona still hard-truncated history at `MODEL_MAX_TURNS`, permanently dropping older session context mid-conversation; once the summary became persisted, a separate leak appeared where `new session` / `clear` would have wiped chat memory but left the old summary file behind
  - Fix:
    - `core/route_boundary.py`: added `RouterBoundary`, `FollowupResolutionBoundary`, and `RouteClarifierBoundary` with `BoundaryValidationError` and one fallback rule owned per boundary
    - `core/contracts.py`: added `RouteClarifierResolution` as the typed clarifier output contract
    - `core/orchestrator_phases.py`: router output now validates through `RouterBoundary`, and the follow-up / clarifier wrapper helpers now catch structured boundary failures and apply the validator-owned fallback immediately
    - `core/engines/followup_resolution.py` and `core/engines/route_clarity.py`: replaced raw payload parsing with typed boundary validation before route refinement
    - `core/engines/conversation_compressor.py`: added the new engine that rolls dropped history into `orc.conversation_summary`, injects a hidden `[CONVERSATION SUMMARY]` message for persona, and re-summarizes when the budget ceiling is exceeded
    - `core/orchestrator.py`: loads `data/conversation_summary.json` on startup and persists updates when the compressor changes the summary
    - `core/orchestrator_phases.py`: `phase_persona()` now calls `ConversationCompressor` at the truncation point instead of tail-slicing history directly
    - `config.py`: added `CONVERSATION_SUMMARY_PATH`
    - `ui/controller_actions.py` and `AGENTS/harness/session.py`: `new session` / `clear` now also remove the persisted conversation summary so old context cannot leak into a deliberate fresh start
    - `docs/architecture/TRIGGER_FLOW.md`: updated the search-phase wording and marked 13.2, 13.3, 13.4, and 13.5 as implemented
  - Validation:
    - `python3 -m compileall core/contracts.py core/route_boundary.py core/engines/followup_resolution.py core/engines/route_clarity.py core/orchestrator_phases.py` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/followup_resolution_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/route_clarifier_smoke_test.py` — pass
    - `python3 -m compileall config.py core/engines/conversation_compressor.py core/orchestrator.py core/orchestrator_phases.py ui/controller_actions.py AGENTS/harness/session.py scripts/conversation_compressor_smoke_test.py scripts/route_boundary_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/conversation_compressor_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/persona_system_event_role_smoke_test.py` — pass
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
- 2026-03-19: Fixed the readonly knowledge fast path so live date/time questions fall back to persona instead of being misread as stored memory lookups.
  - Problem:
    - CHAT turns now check `build_readonly_state_answer()` before persona, which is correct for task/event/knowledge lookups
    - but `"What's today's date?"` matched the broad knowledge-query form and the possessive in `"today's"` made it look like a personal-fact query, so Piper answered `"I do not have a stored today's date."` instead of letting persona read `[ENVIRONMENT]`
  - Fix:
    - `core/engines/state_mutation.py`: `build_readonly_answer()` now short-circuits live date/time/day queries back to persona, and `_looks_like_live_environment_query()` captures the affected phrasing family
    - `scripts/knowledge_readonly_smoke_test.py`: added regression coverage proving `"What's today's date?"` and `"Do you remember today's date?"` no longer get answered by the readonly knowledge fast path
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py scripts/knowledge_readonly_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/knowledge_readonly_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/operational_state_readonly_smoke_test.py` — pass
- 2026-03-19: Fixed route-time misclassification for live date/time/day questions so they do not trigger background search and reporter.
  - Problem:
    - the readonly-state fix stopped `"What's today's date?"` from being answered as missing memory, but the router could still classify that same turn as `SEARCH`
    - once the route was `SEARCH`, Piper entered `SEARCH_FIRST_PASS`, queued a background search, and then auto-ran reporter on completion even though persona already had the answer in `[ENVIRONMENT]`
  - Fix:
    - `core/routing/route_normalizer.py`: added a narrow route-time normalization that converts `SEARCH` back to `CHAT` for live date/time/day queries, keeping this behavior aligned with the readonly-state guard
    - `scripts/route_boundary_smoke_test.py`: added regression coverage proving `"What's today's date?"` normalizes from router `SEARCH` to final `CHAT`
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/knowledge_readonly_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/operational_state_readonly_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
- 2026-03-19: Added an end-to-end harness smoke proving live date questions stay in CHAT and do not invoke search.
  - Problem:
    - after the route-time fix, the isolated harness answered `"whats todays date"` correctly, but the live app symptom could still be confused with stale runtime state because there was no dedicated end-to-end regression asserting zero search events for live date questions
  - Fix:
    - `scripts/live_environment_chat_smoke_test.py`: new harness smoke that asks for today's date, asserts a direct date-like reply, and fails if any search preview or `search_result` event appears
  - Validation:
    - `python3 -m compileall scripts/live_environment_chat_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
- 2026-03-19: Fixed short file-read follow-ups like `"What's in it?"` being over-clarified instead of continuing the active file lookup.
  - Problem:
    - route normalization could correctly rewrite a short pronoun follow-up into a targeted `FILE_WORK` read, but `RouteClarifier.should_force_clarification()` still treated the user text as too short and replaced the file-read route with a clarification pause
    - this broke natural follow-ups after file lookup results, especially bare questions like `"What's in it?"`
  - Fix:
    - `core/engines/route_clarity.py`: preserve targeted file lookup/read tasks by skipping clarification refinement when the current task already contains a targeted file-read or file-lookup stage
    - `scripts/file_lookup_smoke_test.py`: added the exact short follow-up turn `"What's in it?"` so the regression is covered end-to-end
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
- 2026-03-19: Fixed natural file-delete follow-ups being misrouted into durable-memory removal.
  - Problem:
    - bare delete phrasing like `Delete test_notes.txt` or natural follow-ups like `Delete test notes` could miss direct FILE_WORK normalization, then fall through to the broad `remove_knowledge` matcher
    - that caused planner/runtime to run `REMOVE_KNOWLEDGE` and feed persona a truthful but wrong `KNOWLEDGE ALREADY ABSENT` outcome for what should have been a workspace file delete
  - Fix:
    - `core/routing/route_normalizer.py`: added a workspace delete follow-up normalizer that resolves natural delete phrasing against the current explicit file target, recent file targets, and runtime `Relevant paths` before state-mutation routing runs
    - `core/routing/route_normalizer.py`: factored explicit file delete card construction so both direct path deletes and follow-up deletes share the same FILE_WORK route
    - `scripts/file_delete_followup_normalizer_smoke_test.py`: new end-to-end harness smoke covering `Create a file called test_notes.txt...` followed by `Delete test notes`
  - Validation:
    - `python3 -m py_compile core/routing/route_normalizer.py scripts/file_delete_followup_normalizer_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/file_delete_followup_normalizer_smoke_test.py --json` — pass (same-session follow-up and fresh-session delete)
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
- 2026-03-19: Fixed orphaned partial assistant bubbles surviving into the next turn and appearing as duplicate replies.
  - Problem:
    - the successful delete reply was being persisted only once, but the UI could still show a stale partial assistant bubble like `Removed` from an interrupted earlier stream
    - `memory.jsonl` proved the extra bare reply was not a real second committed turn, which pointed to chat/stream state rather than routing or persona truthfulness
    - `ChatState.upsert_streaming_assistant()` only updated the last message when it was an assistant; after an interrupted stream, the next user turn left that partial assistant in history and a later successful turn appended a new assistant bubble instead of replacing it
  - Fix:
    - `memory/chat_state.py`: track the active streaming assistant slot explicitly, update that slot across deltas even if later hidden/system messages are appended, and drop any orphaned in-progress assistant bubble before appending a new user turn
    - `core/pipeline.py`: finalize the tracked streaming assistant only on a real stream end
    - `ui/controller.py` and `AGENTS/harness/session.py`: pass the stream-finalization callback into `ChatPipeline`
    - `scripts/streaming_orphan_assistant_smoke_test.py`: added a focused regression that reproduces an interrupted `Removed` partial, starts a new user turn, and verifies only the final assistant reply remains visible/persisted
  - Validation:
    - `python3 -m py_compile memory/chat_state.py core/pipeline.py ui/controller.py AGENTS/harness/session.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/streaming_orphan_assistant_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
- 2026-03-19: Fixed literal file-edit phrasing being misrouted into task completion, and prevented mutating file stages from answering with stale exact-read text.
  - Problem:
    - a request like `Edit verify_test.txt - replace 'verified' with 'done' and also add a second line saying 'complete'` could miss the direct FILE_WORK normalizer
    - once that happened, the task/event completion heuristics grabbed the quoted `done` and `complete` tokens and misrouted the turn into `TASK_EVENT_WORK`
    - after the route fix, a second issue surfaced: the file changed correctly, but persona answered with the old exact-read content (`verified`) because the mutating stage was still being treated as a targeted read fast path
  - Fix:
    - `core/routing/route_normalizer.py`: added explicit compound file-edit normalization for edit requests that name a file target and combine quoted replace text with a second-line append instruction
    - `core/routing/route_normalizer.py`: built a dedicated FILE_WORK stage card for that pattern so the literal text content is kept out of task/event completion heuristics
    - `core/file_stage_policy.py`: prevented stages that require file verification from also being classified as targeted-read direct-answer stages
    - `scripts/file_edit_literal_completion_text_smoke_test.py`: added an end-to-end regression for the exact edit phrasing and asserted both final file contents and single-reply behavior
  - Validation:
    - `python3 -m py_compile core/routing/route_normalizer.py core/file_stage_policy.py scripts/file_edit_literal_completion_text_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/file_edit_literal_completion_text_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
- 2026-03-19: Fixed missing explicit file targets ending in verification-block failures instead of clean one-turn not-found replies.
  - Problem:
    - a turn like `Edit stent_file.txt, add a line saying 'test'` stopped creating the file after the earlier route fix, but it still failed with verification-escalation wording (`engineering support`, `unverified system error`) instead of a normal missing-file explanation
    - the root cause was the missing-target stop guard incorrectly treating the stage as create-capable because the stage card text contained negated guidance like `do not create a new file if it is missing`
    - that false positive let the planner keep looping into completion/proposal handling, where FILE_WORK verification rules converted the honest stop into a blocked-verification failure
  - Fix:
    - `core/file_stage_policy.py`: added `stage_may_create_missing_target()` and taught it to strip negated create/build language before looking for positive create intent
    - `core/executor.py` and `core/scratchpad_formatter.py`: switched missing-target classification to the shared helper so explicit existing-file edit cards are no longer treated as create-capable
    - `scripts/missing_file_no_reroute_smoke_test.py`: broadened the assertion to accept honest missing-file phrasing (`not found`, `does not exist`, `could not locate`) while still rejecting success claims and engineering-escalation wording
  - Validation:
    - `python3 -m py_compile core/file_stage_policy.py core/scratchpad_formatter.py core/executor.py scripts/missing_file_no_reroute_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/missing_file_no_reroute_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-19: Implemented TRIGGER_FLOW §13.6 structured stage intent for file/code stages.
  - Problem:
    - `file_stage_policy.py` was still re-deriving file-stage intent from English `stage_goal` / `success_condition` text on every policy call
    - that meant the same stage could be reclassified differently at different boundaries, and every new file-work edge case pushed more regex into the policy layer
  - Fix:
    - `core/contracts.py`: added optional `file_stage_kind` to `StageCard`
    - `core/routing/route_normalizer.py` + `core/orchestrator_phases.py`: added route-time `annotate_file_stage_kinds()` so TASK cards have `file_stage_kind` populated before skill selection and later phase logic
    - `core/planner_boundary.py`: backfills `file_stage_kind` during `validate_input()` when a FILE_WORK stage still arrives without it
    - `core/file_stage_policy.py`: added `stage_kind()` and switched policy methods to prefer structured `file_stage_kind` first, with the previous text/regex path preserved as backward-compatible fallback
    - `scripts/planner_boundary_smoke_test.py`: now asserts that `validate_input()` writes a valid `file_stage_kind` back into FILE_WORK stages
    - `scripts/file_stage_policy_smoke_test.py`: now verifies typed `INSPECTION`, `CONTENT_EDIT`, and `SCRIPT_LAUNCH` stages behave the same as the legacy text path
  - Regression caught during rollout:
    - the first field-first pass over-broadened `stage_is_structure_prep_stage()` by treating every `STRUCTURE_PREP` kind as folder-prep-only, which blocked the `consolidate_by_extension` action in the File Chaos flow
    - narrowed that method back to a field-first early-false + regex fallback shape so extension-consolidation stages keep their existing behavior while still benefiting from structured intent
  - Validation:
    - `python3 -m py_compile core/contracts.py core/routing/route_normalizer.py core/orchestrator_phases.py core/planner_boundary.py core/file_stage_policy.py scripts/planner_boundary_smoke_test.py scripts/file_stage_policy_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/planner_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/file_stage_policy_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/file_work_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-20: Added confidence-aware router handling for ambiguous web-vs-workspace lookup requests.
  - Problem:
    - the route normalizer treated broad lookup wording like `search for grocery` as deterministically ambiguous and always asked a clarification question unless hard context resolved it
    - that was safe, but it left no structured path for the Router/Secretary LLM to say "I am actually confident this means the web" or "this clearly means workspace files"
  - Fix:
    - `core/contracts.py`: extended `RouteDecision` with optional `source_scope`, `confidence`, and `question_if_uncertain`
    - `core/route_boundary.py`: `RouterBoundary.validate()` now accepts and validates those optional router-confidence fields
    - `data/prompts/secretary.txt`: documented the new lookup-source confidence contract and told Secretary to emit low-confidence clarification metadata for gray-zone lookup requests
    - `core/routing/route_normalizer.py`: ambiguous lookup routing now keeps the existing hard deterministic wins for explicit web/workspace cues, but in the gray zone it:
      - accepts high-confidence `web` router choices and keeps them as `SEARCH`
      - accepts high-confidence `workspace` router choices and keeps or canonicalizes them into `FILE_WORK`
      - converts low-confidence or unscoped lookup choices into the existing `web vs workspace` clarification pause
    - `scripts/route_boundary_smoke_test.py`: added explicit checks for router-confidence validation plus high-confidence web, high-confidence workspace, and low-confidence clarify behavior
  - Validation:
    - `python3 -m compileall core/contracts.py core/route_boundary.py core/routing/route_normalizer.py scripts/route_boundary_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/lookup_source_disambiguation_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/explicit_web_search_topic_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-20: Hardened live web search fallback and stripped unrelated local context from search first-pass replies.
  - Problem:
    - live search could return `Search Error: Zero results.` almost instantly for phrasing like `latest news on llama.cpp performance benchmarks`, even though a relaxed variant of the same query produced real articles
    - the first-pass search reply was built from the full persona context pack, so it could drift into unrelated `[WORLD STATE]`, `[OPERATIONAL STATE]`, or `[DOCUMENT MATCHES]` while the web search was still running
  - Fix:
    - `tools/search.py`: added multi-strategy DDGS fallback for news/current queries
      - try `news` on the original query
      - retry with a relaxed query when phrasing like `latest news on ...` collapses to zero results
      - fall back to `text` search after that
      - log each attempt so the UI activity stream makes it clear the search really ran
      - if snippets exist but deep-dive fetches fail, return snippet-only context instead of a hard error
    - `core/orchestrator_phases.py`: search first-pass now builds a lean persona pack with `knowledge_enabled=False`, `brain_limit=0`, and `document_limit=0`, plus a stronger `[SEARCH_FIRST_PASS_RULE]` telling persona to stay on-topic and ignore unrelated personal/workspace context
    - `scripts/search_tool_fallback_smoke_test.py`: verifies zero-result news phrasing falls back to a relaxed query and still returns deep-dive content
    - `scripts/search_flow_smoke_test.py`: now asserts the `SEARCH_FIRST_PASS` prompt block does not include `[WORLD STATE]`, `[OPERATIONAL STATE]`, or `[DOCUMENT MATCHES]`
  - Validation:
    - `python3 -m compileall tools/search.py core/orchestrator_phases.py scripts/search_flow_smoke_test.py scripts/search_tool_fallback_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/search_tool_fallback_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - live tool probe: `perform_search("latest news on llama.cpp performance benchmarks", ...)` now returns 5 candidate results after relaxing to `llama.cpp performance benchmarks`, with deep-dive fetches instead of immediate zero results
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-20: Isolated consecutive search turns so old search summaries and search chatter do not leak into the next search.
  - Problem:
    - a fresh search could still see the previous search's hidden summary and even the previous search's assistant reply, especially on the reporter/final persona turn
    - that let a new query such as `latest nvidia news` inherit the prior `llama.cpp` search context and biased both the waiting reply and the final summary prompt
  - Fix:
    - `core/prompting.py`:
      - dropped internal search transport messages from persona prompt history (`[SEARCH REPORT CONSUMED FOR ...]`, `Background search complete for ...`, and the internal reporter instruction)
      - reduced terminal search events to the latest `[SEARCH SUMMARY FOR ...]` only instead of carrying every historical search summary into runtime context
    - `core/orchestrator_phases.py`:
      - search first-pass now uses `_build_search_preview_history(...)`, which feeds persona only the current user search turn
      - reporter/final search persona turns now use `_build_search_report_history(...)`, which feeds persona only the latest current search summary plus the current user turn
      - tightened the first-pass rule so persona does not speculate that the search is quiet/empty before results arrive
    - `scripts/search_prompt_isolation_smoke_test.py`: verifies that a second search turn keeps only the latest search summary and drops the previous search summary plus consumed/internal search markers
  - Validation:
    - `python3 -m compileall core/prompting.py core/orchestrator_phases.py scripts/search_prompt_isolation_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/search_prompt_isolation_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-20: Fixed benchmark-style search phrases collapsing into `FILE_WORK`.
  - Problem:
    - bare benchmark lookups like `search for MLPerf Inference v5.0 benchmark results` and `search for llama.cpp benchmark results` were being routed as workspace filename lookups and answering `No matching files found.`
    - two route-normalizer shortcuts caused it:
      - dotted topic/version tokens such as `v5.0` and `llama.cpp` were being treated as explicit workspace-file signals during lookup-source disambiguation
      - workspace document subject extraction stopped at the first `.` and truncated topics like `llama.cpp` -> `llama`
    - once the first turn misrouted to `FILE_WORK`, retries inherited that bad `LATEST_RUNTIME_CONTEXT` and stayed on the file path
  - Fix:
    - `core/routing/route_normalizer.py`:
      - tightened `_FILE_TARGET_RE` so numeric version fragments such as `v5.0` no longer count as file targets
      - `_request_explicitly_scopes_lookup_to_workspace()` now requires actual workspace wording instead of any dotted token
      - `_extract_document_lookup_subject()` now captures full dotted subjects instead of stopping at the first period
      - `_normalize_workspace_document_lookup()` no longer rewrites a `SEARCH` route into workspace lookup unless there is real workspace scope or prior workspace context
      - ambiguity resolution and recent-target collection no longer let the current tentative file route validate itself as workspace context
    - `scripts/route_boundary_smoke_test.py`:
      - added coverage for explicit web benchmark queries staying `SEARCH`
      - added coverage for ambiguous benchmark queries clarifying `web vs workspace` instead of collapsing into `FILE_WORK`
    - `scripts/benchmark_search_routing_smoke_test.py`:
      - harness regression that confirms ambiguous benchmark phrases now ask for clarification instead of searching files
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/benchmark_search_routing_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/explicit_web_search_topic_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
- 2026-03-20: Fixed lookup-source clarification follow-ups dropping the original search subject.
  - Problem:
    - after an ambiguous lookup clarification like `Did you want me to search the web for "MLPerf Inference v5.0 benchmark results", or look for it in your workspace files?`, a natural reply like `web pls` was entering `SEARCH` with the literal follow-up text as the query
    - live logs showed `Search query: web pls` and `[SEARCH SUMMARY FOR 'web pls']`, which proved the original subject was being lost before the search tool even ran
    - the immediate bug was precedence: `_normalize_lookup_source_choice_followup()` was too strict for natural short replies, and even after broadening it, `_normalize_explicit_web_search()` still ran earlier and rewrote `web pls` into a generic search card
  - Fix:
    - `core/routing/route_normalizer.py`:
      - added `_classify_lookup_source_choice()` so scoped clarification replies can resolve `web`, `web pls`, `online`, `workspace files`, `my files`, and similar short natural replies
      - moved `_normalize_lookup_source_choice_followup()` ahead of `_normalize_explicit_web_search()` so an active lookup-source clarification thread wins before generic web-search normalization
      - preserved the original clarified subject when resolving the web branch, so the follow-up search query now becomes `MLPerf Inference v5.0 benchmark results` instead of the literal reply text
    - `scripts/route_boundary_smoke_test.py`:
      - added direct coverage for `web pls` resolving to `SEARCH` with the carried benchmark subject
    - `scripts/lookup_source_web_followup_smoke_test.py`:
      - added an end-to-end harness regression that confirms:
        - turn 1 asks `web vs workspace`
        - turn 2 `web pls` triggers real search
        - the fake search sees `MLPerf Inference v5.0 benchmark results`
        - the turn produces a search result event and hidden search summary
  - Validation:
    - `python3 -m compileall core/routing/route_normalizer.py scripts/route_boundary_smoke_test.py scripts/lookup_source_web_followup_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/lookup_source_disambiguation_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/lookup_source_web_followup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/benchmark_search_routing_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
- 2026-03-20: Tightened conversation-summary carry-forward labeling and noise cleanup.
  - Problem:
    - the hidden persona prompt block for compressed history could still look nearly verbatim for small histories, which is expected under the current no-extra-LLM policy, but the label `[CONVERSATION SUMMARY]` understated that it may omit detail
    - older persisted summaries could also carry low-value system/control lines such as `System: === New session` and `[SEARCH REPORT CONSUMED ...]`, which made the block look noisier and more literal than intended
  - Fix:
    - `core/engines/conversation_compressor.py`:
      - changed the injected hidden summary header to `[EARLIER CONVERSATION SUMMARY - MAY OMIT DETAIL]`
      - sanitize existing carried summary text before reinjecting it, so previously persisted junk system/control lines are cleaned on the next turn
      - drop system-role messages entirely from future transcript-style carry-forward material
      - remove low-value carry-forward lines such as session markers, search transport markers, runtime-context markers, and UI chatter when sanitizing summary text
      - keep backward compatibility by stripping both the old and new summary headers during summary normalization
    - `scripts/conversation_compressor_smoke_test.py`:
      - updated expected header coverage
      - added regression coverage proving persisted/system control noise is removed from both existing summaries and newly dropped transcript content
    - `docs/architecture/TRIGGER_FLOW.md`:
      - updated the documented hidden summary header and noted the system/control-line cleanup behavior
  - Validation:
    - `python3 -m compileall core/engines/conversation_compressor.py scripts/conversation_compressor_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/conversation_compressor_smoke_test.py` — pass
- 2026-03-22: Implemented turn stats collection and regression alerts from trigger-flow §13.7.
  - Problem:
    - regressions in routing/latency/search flow still required manual reading of `data/debug/*`, with no structured per-turn record or read-only UI summary
    - SEARCH turns span two orchestrator loops (preview, then reporter), so naive end-of-run logging would split one logical search into two records
  - Fix:
    - `core/engines/stats_collector.py`:
      - added append-only `data/stats.jsonl` recording, rolling 2-sigma outlier detection, and `data/debug/stats_alerts.log`
      - added pending SEARCH turn handoff so first-pass preview timing merges into the later reporter completion, using the cancel token when present and a shared-owner fallback for harness paths that do not provide one
      - added a read-only report builder for the UI stats tab
    - `core/orchestrator.py`:
      - instantiates `StatsCollector`, resumes/defer-records live turn state, records terminal turns once, skips cancelled turns, and records hard aborts as `ABORTED`
    - `core/orchestrator_phases.py`:
      - instruments route / manager / reporter / persona timings
      - records bypass type, search query, `[ROUTER]` reroute flag, and per-stage verification/timing snapshots
      - defers initial SEARCH preview records so reporter completion becomes the single stored SEARCH turn
    - `core/executor.py`:
      - exposes per-stage planner vs executor wall time for stats
    - `core/pipeline.py`:
      - captures completed stream/TTS timing metrics for persona/search timing summaries
    - `ui/layout.py`, `ui/controller.py`, `ui/controller_queue.py`:
      - added a read-only Stats tab that refreshes from `stats.jsonl` / `stats_alerts.log`
    - `AGENTS/harness/session.py`:
      - clears copied stats files in isolated runs and surfaces stats paths in harness dumps
    - `scripts/stats_collector_smoke_test.py`:
      - added direct collector coverage for append-only records, outlier alerts, and read-only report rendering
    - `scripts/live_environment_chat_smoke_test.py` and `scripts/search_flow_smoke_test.py`:
      - added end-to-end assertions that stats lines are written correctly for environment-query CHAT and merged async SEARCH turns
    - `docs/architecture/TRIGGER_FLOW.md`:
      - marked §13.7 implemented
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/stats_collector_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` hung in this environment during the broad sequential pack, so the general harness batch is only partially complete for this pass
- 2026-03-22: Validation rerun note for llama-backed harness smokes.
  - Observation:
    - a broad sequential smoke batch appeared to hang on file-oriented harness runs even after earlier focused stats/search checks were green
    - rerunning the same smokes one-by-one with an explicit Windows-side `llama-server` cleanup before each run cleared the false-red behavior
  - Working validation pattern:
    - run the Windows-backed harness smokes sequentially
    - before each rerun, stop any lingering `llama-server` Windows process
    - then launch the smoke fresh
  - Confirmed passes with this cleanup pattern:
    - `scripts/file_edit_smoke_test.py --json`
    - `scripts/file_lookup_smoke_test.py --json`
    - `scripts/file_crud_smoke_test.py --json`
    - `scripts/file_chaos_test.py --json`
- 2026-03-22: Implemented trigger-flow §13.9 undo / change journal.
  - Problem:
    - mutating FILE_WORK turns had no safety layer; a wrong write/move/delete had to be repaired manually
    - the spec called for a pre-LLM undo interceptor plus a rolling change journal, but runtime had no owner module or dedicated undo phase yet
  - Fix:
    - `core/engines/change_journal.py`:
      - added the journal owner for `data/change_journal.json`
      - captures pre-mutation path snapshots for supported reversible FILE_OP actions (`write_*`, `append_text`, `update_json`, `delete_*`, `move_*`, `copy_*`, `ensure_dir*`)
      - restores the latest recorded task by replaying snapshots in reverse operation order
      - marks the latest entry as undone after a verified restore instead of silently hopping backward to older tasks
      - retains the last 10 entries, matching the spec
    - `core/executor.py`:
      - snapshots supported FILE_OP mutations before execution
      - records only successful workspace-changing operations into the per-turn completed-change list
      - keeps `RUN_CODE` out of the journal in v1
    - `core/orchestrator.py`:
      - instantiates the change-journal owner
      - adds `UNDO` to the stage loop
      - tracks per-turn undo notice state and the latest recorded journal entry
    - `core/orchestrator_phases.py`:
      - `phase_route()` now short-circuits undo phrases through the pre-LLM interceptor path
      - `phase_manager()` records one journal entry per logical mutating task turn and enables the low-key undo notice only for successful mutating FILE_WORK turns
      - added `phase_undo()` which restores the latest journaled task and reports it through the normal FILE_WORK persona path
      - persona replies now append "You can say 'undo that'..." only when the current turn genuinely produced an undoable mutating FILE_WORK result
    - `core/routing/route_normalizer.py`:
      - added `detect_route_interceptor()` with undo phrase recognition
    - `config.py` / `AGENTS/harness/session.py`:
      - added `CFG.CHANGE_JOURNAL_PATH`
      - cleared copied `change_journal.json` in isolated harness runs so undo tests do not inherit stale history
    - `scripts/change_journal_smoke_test.py`:
      - added direct deterministic coverage for overwrite restore, parent-directory cleanup after undo, and undo-interceptor recognition
    - `scripts/undo_flow_smoke_test.py`:
      - added end-to-end harness coverage for create -> undo, including the user-facing undo availability notice and real workspace rollback
    - `docs/architecture/TRIGGER_FLOW.md`:
      - marked §13.9 implemented and updated the design notes to match the shipped snapshot-based journal/interceptor path
  - Validation:
    - `python3 -m py_compile config.py core/engines/change_journal.py core/executor.py core/orchestrator.py core/orchestrator_phases.py core/routing/route_normalizer.py AGENTS/harness/session.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/change_journal_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — first rerun hit a transient llama-server boot crash (`FATAL: Server crashed with code 15`); clean rerun passed
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
  - Reliable harness pattern for this pass:
    - Windows-backed smokes behaved more reliably when the cleanup step used `taskkill /F /IM llama-server.exe` before each run and the smoke itself was wrapped in `timeout`, instead of relying on one long shell loop

- 2026-03-22 - §13.7 feature hook registry refactor
  - Scope:
    - `core/routing/route_normalizer.py`:
      - extracted the route normalizer chain into `_NORMALIZER_REGISTRY`
      - extracted pre-route interceptors into `_ROUTE_INTERCEPTOR_REGISTRY` so undo/explain/reminder-style interceptors can self-register without editing the hotspot
      - preserved current normalization/interceptor order exactly
    - `core/engines/context_pack.py`:
      - extracted persona tail block assembly into `_TAIL_BLOCK_REGISTRY`
      - preserved current block order and direct-answer behavior
    - `core/orchestrator_phases.py`:
      - added hook registry helpers for `on_pre_route`, `on_task_verified`, and `on_turn_end`
      - moved pre-route user-turn bookkeeping, terminal task journaling, and persona turn-end finalization side effects behind registered hooks
      - kept execution flow and user-visible behavior unchanged
    - `docs/architecture/TRIGGER_FLOW.md`:
      - marked §13.7 implemented and updated the text to match the shipped registries
  - Validation:
    - `python3 -m py_compile core/routing/route_normalizer.py core/engines/context_pack.py core/orchestrator_phases.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/change_journal_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `python3 scripts/summary_engine_smoke_test.py` — pass
    - `python3 scripts/context_pack_engine_smoke_test.py` — pass

- 2026-03-22 - §13.8 stats audit and doc alignment
  - Findings:
    - `core/engines/stats_collector.py` was already live and matches the intended append-only stats design:
      - per-turn `stats.jsonl`
      - rolling outlier detection into `data/debug/stats_alerts.log`
      - deferred SEARCH first-pass -> reporter merge into one logical stats record
      - aborted turns recorded, cancelled turns skipped
    - UI already exposes Stats as a read-only main tab via `ui/controller.py` / `ui/layout.py`
    - remaining drift was in the spec text, not the runtime implementation
  - Changes:
    - marked §13.8 implemented in `docs/architecture/TRIGGER_FLOW.md`
    - updated the UI wording to reflect the shipped text/table-style stats report rather than overclaiming charts/colour-coded rows
    - clarified ownership: phases feed the collector during execution, `core/orchestrator.py` finalizes the terminal append

- 2026-03-22 - §13.10 proactive monitor / background reminders
  - Scope:
    - `core/feature_hooks.py`:
      - extracted the shared turn-hook registry out of `core/orchestrator_phases.py`
      - avoids a circular import when feature modules self-register turn-end hooks
    - `core/engines/proactive_monitor.py`:
      - added reminder parsing/storage helpers and the background `ProactiveMonitor`
      - self-registers the reminder-set pre-route interceptor, persona tail blocks, and proactive turn-end finalizer
      - added local fire-time formatting for typed reminder context
    - `core/orchestrator.py` / `core/orchestrator_phases.py`:
      - added `REMINDER_SET` phase dispatch
      - proactive hidden-trigger turns now short-circuit route directly to persona as synthetic user-invisible turns
      - skipped normal user-turn memory ingestion/consolidation hooks for proactive synthetic turns
    - `ui/controller.py` / `ui/controller_actions.py`:
      - added monitor lifecycle management, proactive idle gating, inflight reminder tracking, and synthetic reminder dispatch
      - reminder dispatch now uses the same active-operation rails as other background work and does not touch DPG widgets from the background thread
    - `core/prompting.py`:
      - strips raw `[PROACTIVE_TRIGGER]` transport messages from persona history so persona only sees the typed tail block, not the hidden JSON payload
    - `scripts/proactive_monitor_smoke_test.py`:
      - added focused coverage for reminder parsing, reminder-set interceptor registration, monitor deferral while busy, reminder scheduling through the harness, and a synthetic proactive reminder turn that marks the reminder fired
    - `docs/architecture/TRIGGER_FLOW.md`:
      - marked §13.10 implemented and updated the shipped ownership/runtime notes
  - Validation:
    - `python3 -m py_compile core/feature_hooks.py core/engines/proactive_monitor.py core/orchestrator_phases.py core/prompting.py ui/controller.py ui/controller_actions.py scripts/proactive_monitor_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/proactive_monitor_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/change_journal_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass
  - Operational note:
    - `file_lookup_smoke_test.py` is long-running because it executes 7 real turns; it needs wall time on the order of 90 seconds and should not be mistaken for a stall if no output appears immediately.

- 2026-03-22 - §13.11 turn explanation / "why did you do that"
  - Scope:
    - `core/turn_explanation.py`:
      - added the hidden last-turn explanation snapshot owner
      - added explicit/follow-up explanation phrase helpers
      - added snapshot render logic for `[EXPLAIN_LAST_TURN]`
    - `core/routing/route_normalizer.py`:
      - added the `EXPLAIN` pre-LLM interceptor
      - explanation follow-ups like `more detail` only bind when the last-turn snapshot is marked explanation-active
    - `core/orchestrator.py` / `core/orchestrator_phases.py`:
      - added `EXPLAIN` stage dispatch back into persona
      - persist the last completed turn as a hidden `[LAST_TURN_EXPLANATION_CONTEXT]` snapshot at turn end
      - explanation turns preserve/activate the prior snapshot instead of overwriting it with the explanation turn itself
      - explain turns now use a lean persona path and trim history to the most recent slice so the reply stays focused on the previous turn
      - moved fast-path turn finalization to after persona phase timing closes so the stored explanation snapshot sees the completed phase timings
    - `core/engines/context_pack.py` / `core/prompting.py`:
      - added `[EXPLAIN_LAST_TURN]` tail-block injection
      - strip the raw hidden explanation snapshot transport from persona history before the model sees it
    - `scripts/turn_explanation_smoke_test.py`:
      - added focused coverage for snapshot parse/render, explicit EXPLAIN interception, `more detail` follow-up interception, hidden snapshot persistence, and a live two-turn harness flow
    - `docs/architecture/TRIGGER_FLOW.md`:
      - marked §13.11 implemented and updated the ownership/runtime notes to reflect the hidden snapshot design
  - Validation:
    - `python3 -m py_compile core/turn_explanation.py core/routing/route_normalizer.py core/prompting.py core/engines/context_pack.py core/orchestrator.py core/orchestrator_phases.py scripts/turn_explanation_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/turn_explanation_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/proactive_monitor_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/change_journal_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass

- 2026-03-23 - §12 reporter constraint + §13.12 context arbitration policy
  - Scope:
    - `core/contracts.py`:
      - added typed `PersonaTurnType` / `PersonaArbitrationProfile`
      - added `PERSONA_CONTEXT_ARBITRATION_TABLE` covering `CHAT`, `TASK`, `DOC_FOCUS`, `SEARCH_FIRST_PASS`, `REPORTER`, `EXPLAIN`, and `PROACTIVE_TRIGGER`
    - `core/engines/context_pack.py` / `core/prompt_context.py`:
      - added `resolve_persona_turn_type()` and `apply_context_arbitration()`
      - added the registered `[CONTEXT_ARBITRATION_RULE]` tail block
      - reporter turns now explicitly suppress unrelated world/situational/document context while retaining `[SEARCH_REPORT_RULE]` and the search summary
    - `core/orchestrator_phases.py`:
      - `phase_search()` now applies the `SEARCH_FIRST_PASS` arbitration profile before prompt rendering
      - `phase_persona()` now applies the route-aware arbitration profile before prompt rendering, including `REPORTER`, `DOC_FOCUS`, `EXPLAIN`, and `PROACTIVE_TRIGGER`
      - control-tag stripping now removes echoed `[CONTEXT_ARBITRATION_RULE]`
    - `scripts/context_pack_engine_smoke_test.py` / `scripts/test_engines.py`:
      - added direct coverage for arbitration profiles and the new tail block
    - `scripts/search_flow_smoke_test.py`:
      - tightened prompt-debug extraction to read real phase content instead of stopping at the phase header divider
      - now detects actual block headers (for example `^[WORLD STATE]$`) instead of false positives from instruction text mentioning block names
    - `docs/architecture/TRIGGER_FLOW.md`:
      - marked §13.12 implemented
      - updated §12 to note that the reporter constraint is enforced by both `[SEARCH_REPORT_RULE]` and the `REPORTER` arbitration profile
  - Validation:
    - `python3 -m py_compile core/contracts.py core/engines/context_pack.py core/prompt_context.py core/orchestrator_phases.py scripts/context_pack_engine_smoke_test.py scripts/search_flow_smoke_test.py scripts/test_engines.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/context_pack_engine_smoke_test.py` — pass
    - `python3 scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe -m pytest scripts/test_engines.py -k "build_persona_directive_pack_includes_no_mutation_rule or build_persona_directive_pack_includes_search_rule" -q` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/live_environment_chat_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/proactive_monitor_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/turn_explanation_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass

- 2026-03-23 - search first-pass should not ask permission to continue
  - Scope:
    - `core/orchestrator_phases.py`:
      - tightened `[SEARCH_FIRST_PASS_RULE]` so persona is told the runtime will automatically deliver the completed search results on the same turn
      - explicitly forbids asking whether to proceed / continue / wait for permission once the search finishes
      - fallback text now says the results will come back automatically
    - `scripts/search_flow_smoke_test.py`:
      - added prompt-level coverage for the new auto-continue rule
      - added output-level coverage that the first assistant preview does not ask whether to proceed
  - Validation:
    - `python3 -m py_compile core/orchestrator_phases.py scripts/search_flow_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass

- 2026-03-23 - stop auto-appending undo reminder text to every FILE_WORK success
  - Scope:
    - `core/orchestrator_phases.py`:
      - made `_append_undo_notice_if_needed()` a no-op so normal success replies stop appending `You can say 'undo that' if you'd like to revert.`
      - undo interception and change-journal behavior remain intact; only the repeated narration was removed
    - `scripts/undo_flow_smoke_test.py`:
      - updated the create-turn assertion to validate successful file creation/edit wording instead of requiring the exact `undo that` phrase
  - Validation:
    - `python3 -m py_compile core/orchestrator_phases.py scripts/undo_flow_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass

- 2026-03-23 - compound create/delete/undo/redo file sequence must clarify missing details instead of inventing a file
  - Scope:
    - `core/routing/route_normalizer.py`:
      - repaired the compound file-sequence route path by importing the shared `_FILE_PATH_TOKEN` used by the existing recognizer
      - confirmed the vague request `create a file and then delete it and then undo it and then redo it` now normalizes to a single `CHAT` clarification stage that asks for filename + exact content instead of fabricating placeholder data
    - `core/engines/state_mutation.py`:
      - hardened `TASK_EVENT_WORK` / `MEMORY_WORK` outcome packing so `PROPOSAL:`-only details no longer count as successful stage results unless another explicit status override is present
    - `scripts/route_boundary_smoke_test.py`:
      - added coverage for the vague compound file sequence clarification route
    - `scripts/state_mutation_engine_smoke_test.py`:
      - added coverage that proposal-only task-event outcomes are `FAILED / INCOMPLETE`
  - Validation:
    - `python3 -m py_compile core/routing/route_normalizer.py core/engines/state_mutation.py scripts/route_boundary_smoke_test.py scripts/state_mutation_engine_smoke_test.py` — clean
    - `python3 scripts/state_mutation_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass

- 2026-03-23 - file-state corrections must acknowledge verified truth, and target corrections must undo mistaken file deletions
  - Scope:
    - `core/routing/route_normalizer.py`:
      - added a pre-LLM `FILE_STATE_CORRECTION_ACK` interceptor for follow-ups like `its final state should be non-existing i think` when the latest verified runtime context already proves the file is absent
      - added a pre-LLM `FILE_TARGET_CORRECTION` interceptor for follow-ups like `it was bob not b`, routed to `UNDO` so the mistaken file mutation is reverted before replying
      - anchored explicit delete cards and compound file-sequence cards with `active_targets`
      - widened target matching so bare name corrections like `b` still match a mistaken runtime target such as `b.txt`
    - `core/orchestrator_phases.py`:
      - added a deterministic fast-path acknowledgment reply for verified file-state corrections
      - added a narrow compound-sequence direct answer that speaks from the final change-journal state for the exact create/delete/undo/redo lifecycle pattern
      - rewrote `UNDO` success summaries for file-target corrections so persona reports the mistaken file restoration and whether the intended target was already absent
      - fixed early persona fast-path streaming so pre-router bypass replies emit `assistant_stream_start` before deltas instead of rendering as empty text in the harness/UI
    - `scripts/route_boundary_smoke_test.py`:
      - added coverage for the file-state correction interceptor and the `it was bob not b` wrong-target correction interceptor
    - `scripts/compound_file_sequence_truthfulness_smoke_test.py`:
      - added an end-to-end harness regression proving the compound lifecycle turn now reports the real final state (`bob` absent) and treats the next correction turn as non-mutating acknowledgment
    - `scripts/file_target_correction_undo_smoke_test.py`:
      - added an end-to-end harness regression that seeds a mistaken `b.txt` deletion and verifies `it was bob not b` restores `b.txt`, leaves `bob` absent, and marks the journal entry undone
  - Validation:
    - `python3 -m py_compile core/routing/route_normalizer.py core/orchestrator_phases.py scripts/route_boundary_smoke_test.py scripts/compound_file_sequence_truthfulness_smoke_test.py scripts/file_target_correction_undo_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/compound_file_sequence_truthfulness_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_target_correction_undo_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/undo_flow_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass

- 2026-03-23 - appointment-time follow-ups must stay in memory/chat context, and generic world-state listings must not count as exact-value retrieval
  - Scope:
    - `core/engines/followup_resolution.py`:
      - added a context-first memory-recall follow-up path so short confirmations/corrections after a memory-recall offer or runtime memory lookup resolve back to the prior substantive question instead of drifting into workspace/file routing
      - allowed this memory-recall recovery path to override a tentative `FILE_WORK` route when the live context clearly points to memory recall
    - `core/routing/route_normalizer.py`:
      - blocked generic `memory`, `records`, `world state`, and `operational logs` subjects from looking like workspace document targets
    - `core/engines/state_mutation.py`:
      - made `StageMutationEngine.build_outcome_pack()` stage-aware for `MEMORY_WORK`
      - marked `LIST_KNOWLEDGE` / `[WORLD STATE]` / `User Knowledge:` as `FAILED / INCOMPLETE` when the stage is asking for a specific remembered value like an appointment time, instead of treating the listing as successful exact retrieval
    - `core/scratchpad_formatter.py` and `core/executor.py`:
      - threaded the active stage into mutation outcome packing so typed verification and stats use the same stricter exact-value rule
    - `scripts/state_mutation_engine_smoke_test.py`:
      - added regression coverage proving a specific memory-value stage does not pass on a generic `[WORLD STATE]` listing
    - `scripts/route_boundary_smoke_test.py`:
      - added regression coverage for `do it to be sure` and `are you sure i said 9:30?`, both resolving back to `What time was the appointment?` rather than `FILE_WORK`
  - Validation:
    - `python3 -m py_compile core/engines/state_mutation.py core/scratchpad_formatter.py core/executor.py core/engines/followup_resolution.py scripts/state_mutation_engine_smoke_test.py scripts/route_boundary_smoke_test.py` — clean
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/state_mutation_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/search_flow_smoke_test.py --json` — pass
    - `python3 scripts/summary_engine_smoke_test.py` — pass
    - `python3 scripts/context_pack_engine_smoke_test.py` — pass

- 2026-03-24 - memory-recall follow-ups need to be context-first, not phrasing-first, or they can fall through into bogus `FILE_WORK` targets like `e.g`
  - Scope:
    - `core/engines/followup_resolution.py`:
      - broadened the memory-recall follow-up gate so short, underspecified recall continuations after a prior recall offer or live memory-lookup context resolve back to the previous substantive user question instead of requiring a narrow verification phrase
      - taught previous-user extraction to skip recall-control turns like `attempt a recall pls` and context wrappers like `i mean for the appointment`, so the recovered query walks back to the real anchor question
    - `core/routing/route_normalizer.py`:
      - added placeholder junk subjects like `eg` / `e g` to the generic lookup block list so router-invented filler cannot become a plausible workspace lookup target
    - `scripts/route_boundary_smoke_test.py`:
      - added regressions for `attempt a recall pls` and `i mean for the appointment`, both resolving back to `What time was the appointment?` even when a poisoned runtime context is trying to drag the turn into `FILE_WORK`
  - Validation:
    - `python3 -m py_compile core/engines/followup_resolution.py core/routing/route_normalizer.py scripts/route_boundary_smoke_test.py` — clean
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `python3 scripts/state_mutation_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass

- 2026-03-24 - appointment/event follow-up questions must not drift into `FILE_WORK` after a poisoned recall thread
  - Scope:
    - `core/engines/followup_resolution.py`:
      - added an event-detail follow-up path so short appointment/date/time follow-ups with live event context override a tentative `FILE_WORK` route and fall back to readonly event chat instead
      - kept this context-first and bounded: it only fires with active event context and refuses web/workspace/file-explicit turns
    - `core/operational_state_service.py`:
      - broadened readonly event detection so natural appointment phrasing like `When is my appointment tomorrow?` resolves through the operational-state answer path without needing the user to say `events` or `schedule`
    - `scripts/route_boundary_smoke_test.py`:
      - added regression coverage for `when is my appointment tomorrow i mean`, expecting recovery to `What events do I have scheduled tomorrow?` even after earlier bogus file-lookups
    - `scripts/operational_state_readonly_smoke_test.py`:
      - added regression coverage for natural appointment phrasing resolving to the scoped tomorrow event answer
  - Validation:
    - `python3 -m py_compile core/engines/followup_resolution.py core/operational_state_service.py scripts/route_boundary_smoke_test.py scripts/operational_state_readonly_smoke_test.py` — clean
    - `python3 scripts/operational_state_readonly_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass

- 2026-03-24 - events.json now stores optional time alongside date
  - Scope:
    - `memory/stores.py`:
      - `EventStore.add(name, date_str, time_str=None)` — stores `{"date": ..., "time": ...}` when time provided, plain string otherwise (backward compat)
      - `_parse_entry` static normalises either format to `(date_str, time_str|None)`
      - `upcoming()` returns `{"name", "date", "time?"}` dicts; sorts by date then time
      - `cleanup_old_events()` updated to use `_parse_entry`
    - `core/operational_state_service.py`:
      - `_format_event_label` helper emits `name on date at time` when time is present
      - `_render_event_answer` and `_render_event_countdown_answer` use it
    - `core/agent.py`:
      - `_extract_event_time` static parses `at HH:MM` / `at 3pm` / `at 9:15am` from the date phrase into normalised `HH:MM`
      - `exec_add_event` extracts and passes time to `event_store.add`; return message includes time when provided
      - `_resolve_event_date` strip regex made am/pm optional so bare 24h times (`at 14:00`) are also stripped from the date string
  - Validation:
    - `python -m py_compile memory/stores.py core/operational_state_service.py core/agent.py` — clean
    - `./.venv/Scripts/python.exe scripts/operational_state_readonly_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass

- 2026-03-24 - Add RESCHEDULE_EVENT tool; defer conversation summary past stream start
  - Root cause 1: no RESCHEDULE_EVENT tool existed — "i postponed the appointment to the 27th at 13:30" had no atomic handler; model was stuck with REMOVE + ADD or failing the stage
  - Root cause 2: compress_history (which calls LLM when candidate exceeds token budget) ran at phase_persona line 2015, before assistant_stream_start at line 2029 — blocking first reply token on long sessions
  - Scope:
    - `core/agent.py`:
      - `exec_reschedule_event(args)` — parses "Name to new date [at time]", fuzzy-matches existing entry, removes it and re-adds with new date+time atomically; reuses `_extract_event_time` and `_resolve_event_date`
    - `tools/registry.py`:
      - `RESCHEDULE_EVENT` ToolSpec added after ADD_EVENT with correct success_prefixes and syntax examples
    - `core/engines/state_mutation.py`:
      - Added `("Event rescheduled:", "EVENT RESCHEDULED", "event", "reschedule")` to `_TASK_EVENT_SUCCESS_PREFIXES`
    - `core/orchestrator_phases.py`:
      - `phase_persona`: changed compress_history call to `llm=None` — fast trim only, zero blocking before stream start
      - `_hook_deferred_conversation_summary`: new on_turn_end hook that runs full LLM summarization in a daemon thread after reply is delivered; saves result for the next turn
  - Trade-off: summary lags by one turn (computed after reply, available from next turn). Acceptable — better than blocking first token.
  - Validation:
    - `python -m py_compile core/agent.py tools/registry.py core/engines/state_mutation.py core/orchestrator_phases.py` — clean
    - `./.venv/Scripts/python.exe scripts/operational_state_readonly_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/route_boundary_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/state_mutation_engine_smoke_test.py` — pass
## 2026-03-15

- Phase 6: SummaryEngine extraction complete. All 6 frozen engines now done.
  - Contract: `docs/v1/SUMMARY_ENGINE.md` — defines 14 public methods across 7 ownership groups.
  - `core/engines/summary.py` (490 lines) implements the full public API:
    - `latest_stage_entries`, `extract_verified_result`, `extract_proposal`, `extract_exact_file_read`, `extract_file_lookup`, `extract_stage_status` — scratchpad extraction layer
    - `build_runtime_note` — carry-forward pipeline (priority: verified result → exact-read path → file-lookup brief → LAST_LOG → OBSERVATION_TEXT)
    - `build_outcome_block`, `select_outcome_detail`, `extract_observation_detail` — outcome formatting
    - `is_generic_file_work_summary`, `sanitize_note`, `truncate_scratchpad`, `truncate_text` — shared utilities
  - Duplication eliminated:
    - `ContextPackEngine`: 12 methods removed, all replaced with `SummaryEngine.*` call sites.
    - `ScratchpadFormatter`: 4 methods removed (`_truncate_text`, `_select_outcome_detail`, `_extract_observation_detail`, `_is_generic_file_work_summary`), replaced with `SummaryEngine.*` call sites.
    - `PromptBuilder`: 2 methods removed (`_truncate_scratchpad`, `_scratchpad_exact_read_paths`); truncation replaced with `SummaryEngine.truncate_scratchpad`; exact-read-path extraction replaced with `FileWorkEngine.exact_read_paths_from_scratchpad`.
    - `PromptContextService`: 5 delegation methods updated to point directly to `SummaryEngine` instead of `ContextPackEngine`.
  - Import safety: `SummaryEngine` depends only on stdlib (`re`, `json`). Zero engine-to-engine imports.
  - Smoke test: `scripts/summary_engine_smoke_test.py` — 42 cases, all pass.
  - Pre-existing failures not caused by Phase 6:
    - `context_pack_engine_smoke_test.py`: `grocery_list.txt` does not exist on disk — `_normalize_runtime_context_path` returns `""` for non-existent paths. Workspace-state dependency.
    - `code_edit_recovery_hint_smoke_test.py`, `redundant_code_read_guard_smoke_test.py`: `ImportError` on `StageExecutor` — LLM server dependency. Integration-level tests.

## 2026-03-15

- Fixed three live-session bugs diagnosed from debug log (user: "arrange all except fcom").

  **Bug A — `consolidate_by_extension` ignored exclusion constraints:**
  - Root cause: tool had no `exclude_files` / `exclude` parameter. Stage 2 ran `consolidate_by_extension` on the whole workspace and moved the FCOM PDF to `pdf/` despite the planner's stated success_condition saying "FCOM files excluded from movement".
  - Fix: added `exclude_files` (alias `exclude`) list parameter to `handle_consolidate_by_extension` in `tools/workspace_extension_actions.py`. Files are excluded by resolved path and by lowercase filename. Added a registry rule: "When the user wants to exclude specific files from consolidation, pass those filenames in the exclude_files list." Added a syntax example to `tools/registry.py`.

  **Bug B — Stage 3 verification loop (planner proposes completion, file checker blocks indefinitely):**
  - Root cause: Stage 3 goal was "Move all non-FCOM files to their designated folders" — a broad FILE_WORK stage that Stage 2 already completed. When Stage 3 ran, planner did `list_tree`, confirmed work was done, proposed COMPLETE. But `_accept_current_workspace_verification` → `verify_current_file_stage_state` returned None because the `stage_is_extension_file_reorg` guard was False (no explicit extension keywords in the stage text). The synthetic inventory STATE_CHECK path was never reached, so `_last_file_verdict` stayed `""` and completion was blocked forever. Stage 3 repeated until max steps.
  - Fix 1: Extended `stage_is_broad_file_reorg` `reorg_intent` regex in `core/file_stage_policy.py` to match `organi[sz]\w*` (covers "organized", "organizing") and `move\b.{0,40}\bto\b` / `move\b.{0,40}\binto\b` (covers "move files to folders" without requiring "into").
  - Fix 2: Extended guard in `file_checker.py` `verify_current_file_stage_state` to run the synthetic inventory STATE_CHECK for both `stage_is_extension_file_reorg` AND `stage_is_broad_file_reorg` stages.

  **Bug C — Persona asks "should I reroute?" while system simultaneously reroutes:**
  - Root cause: When a task failed hard enough to trigger engineering support (`latest_codex_escalation` set), the PERSONA phase ran with `[ENGINEERING_SUPPORT_RULE]` injected. Persona output included `[ROUTER]` tag (normal failure behavior) but phrased as an offer ("Should you wish to proceed...") without a literal `?`. `_wants_user_confirmation` returned False (no `?`), so the [ROUTER] tag fired and set `next_stage = ROUTE` at line 1235, rerouting immediately while the persona was just presenting the engineering brief.
  - Fix: In `orchestrator_phases.py`, added a guard at the top of the `elif outcome_failed:` branch: if `orc.latest_codex_escalation` is set, suppress the reroute and go to FINISHED. Engineering support is a "stop and report to user" state — the user should decide what to do next, not the [ROUTER] tag.

- Phase 4: VerificationEngine extraction complete.
  - `core/engines/verification.py` fully implemented: `should_verify()`, `evaluate()` (RULES→LLM→STATE_CHECK), `evaluate_mutation()`, `_run_checker()`, `_map_check_to_result()`.
  - `core/executor.py` wired: `self.verification_engine = VerificationEngine(file_checker=self.file_checker)` in `__init__`; inline 50-line verification block replaced with `should_verify()` + `evaluate()` call; `_last_file_verdict` kept in sync for downstream compat; `_last_verification: VerificationResult | None` added as the authoritative typed result.
  - Verification: `file_edit_smoke_test.py` 3/3, `file_crud_smoke_test.py` 6/6, `file_lookup_smoke_test.py` 6/6, `file_chaos_test.py` pass.

- Fixed route normalizer misclassifying `"Delete the file X from the workspace"` as remove-text-from-document.
  - Root cause: `DIRECT_FILE_REMOVE_TEXT_RE` fires before `DIRECT_FILE_DELETE_RE` in the normalizer. For input "Delete the file X from the workspace", the remove-text regex captures needle=`the file X`, subject=`workspace`. `_subject_looks_like_workspace_document("workspace")` returned True because `"workspace"` was not in `_GENERIC_LOOKUP_SUBJECTS`, so it built a content-edit stage instead of a delete stage.
  - Fix: added `"workspace"` to `_GENERIC_LOOKUP_SUBJECTS` in `route_normalizer.py`. `_clean_document_lookup_subject` now strips it to empty, blocking the wrong route. `DIRECT_FILE_DELETE_RE` then matches correctly.
  - Verification: `file_crud_smoke_test.py` 6/6 (delete turns now pass).

- Fixed knowledge removal false-positive "success" in persona after wrong key.
  - Root cause: `memory_remove_listing_confirms_absent()` auto-resolves when the target string is not literally found in the listing. With the wrong combined key, the literal check misses it but the fact IS in the listing under a different key format → auto-resolve fires with success=True → persona says "removed".
  - Fix: added word-overlap guard in `state_mutation.py`. If any significant word (>4 chars) from the target appears in the listing, the fact may be stored under a different key format — skip auto-resolve. True empty-listing case (genuinely absent fact) still triggers auto-resolve correctly.
  - Verification: `state_mutation_engine_smoke_test.py` passes.

- Fixed knowledge removal key mismatch (remove_knowledge target was wrong).
  - Root cause: LLM classifier had no instruction on what to use as `target` for `remove_knowledge`. It was hallucinating a combined key like "I slept the whole day and spent the whole day at the beach, knowledge" that doesn't match any stored attribute.
  - Stored attributes are canonicalized (e.g. `slept_the_whole_day_today`); rendered in memory_summary as "- Slept The Whole Day Today: slept the whole day today".
  - Fix: added two rules to the classifier prompt in `followup_resolution.py`:
    1. For `remove_knowledge`, set `target` to the exact attribute label from memory_summary (before the colon). Never invent a key.
    2. If multiple stored facts match, choose the single most specific one.
  - Verification: `state_mutation_engine_smoke_test.py`, `followup_resolution_engine_smoke_test.py`, `knowledge_route_normalizer_smoke_test.py` all pass.

- Loosened LLM classifier rule for store_knowledge in followup resolution.
  - Old rule "only when user explicitly means durable memory" was too conservative — local model returned clarify for "just remember that" after personal statements that don't follow "my X is Y" form.
  - New rules: 'just remember that' / 'remember that' after any personal statement → store_knowledge; personal statements include past events, experiences, preferences, things the user did/owns; clarify only when no identifiable statement exists in history.
  - Verification: `python scripts/followup_resolution_engine_smoke_test.py`

- Fixed "Just remember that" failing after first-person personal statements.
  - Root cause: `classify_contextual_remember_intent()` only handled "my X is Y" patterns. Statements like "I slept the whole day today." returned intent=none → CHAT → LLM clarification.
  - Fix: added `_FIRST_PERSON_STATEMENT_RE` and a fallback branch for "I [verb] [object]" statements. Predicate (up to 5 words) = subject; full predicate = value. Soft/ongoing markers excluded by `_looks_like_soft_subject()`.
  - Verification: inline classify test + `state_mutation_engine_smoke_test.py` + `followup_resolution_engine_smoke_test.py`

- Removed cross-engine detection dependency from `FollowupResolutionEngine`.
  - `should_resolve()` was calling `self.state_mutation_engine.looks_like_contextual_remember_followup()` and `looks_like_ambiguous_memory_followup()` — resolution-sensing work happening in the mutation engine.
  - Moved `_CONTEXTUAL_REMEMBER_RE` and `_AMBIGUOUS_MEMORY_FOLLOWUP_RE` patterns into `core/engines/followup_resolution.py`.
  - Added `looks_like_contextual_remember_followup()` and `looks_like_ambiguous_memory_followup()` as owned static methods on `FollowupResolutionEngine`.
  - `should_resolve()` now calls those local methods directly — no cross-engine call required.
  - `state_mutation_engine` reference in `__init__` kept for route-building delegation only.
  - Verification:
    - `python scripts/followup_resolution_engine_smoke_test.py`
    - `python scripts/state_mutation_engine_smoke_test.py`
    - `python scripts/skill_layer_smoke_test.py`
    - `python scripts/context_pack_engine_smoke_test.py`

- Added missing engine exports to `core/engines/__init__.py`.
  - `FollowupResolutionEngine`, `RouteClarifier`, and `VerificationEngine` were instantiated in `orchestrator_phases.py` but absent from `__init__.py`.
  - All five active engines now exported from the package.

- Defined `VerificationEngine` contract (Phase 4 prep).
  - `VerificationResult` dataclass with factory methods added to `core/engines/verification.py`.
  - Full contract and migration map documented in `docs/v1/VERIFICATION_ENGINE.md`.

## 2026-03-14

- Fixed a readonly fragment regression from the latest live `Any tasks?` session.
  - `Any tasks?` and `Any events?` were bypassing the deterministic readonly fast path because `core/operational_state_service.py` only recognized longer query forms like `what/show/list/do I have`.
  - That let persona see stale `[RETRIEVED MEMORY]` about `by bread` and freestyle against it instead of answering from `[OPERATIONAL STATE]`.
  - The readonly matcher now treats `any` as a valid query opener when the turn clearly targets tasks/events.
  - Verification:
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`

- Fixed two late regressions from the latest live logs.
  - `I've already done it, you may remove it.` was still being hijacked by the LLM memory-followup refiner and converted into `MEMORY_WORK` against `pending task to buy milk`, even though it was a task-completion update.
  - `core/engines/state_mutation.py` now refuses memory-followup refinement for completion-style turns unless they explicitly mention memory/knowledge/world-state scope.
  - The persona prompt still showed duplicate relevance-policy blocks because `core/prompt_builder.py` only checked for `[RELEVANCE DISCIPLINE]` and missed the markdown form `## RELEVANCE DISCIPLINE` from `data/prompts/instructions.txt`.
  - `core/prompt_builder.py` now treats either heading shape as already present, so the fallback block is not appended again.
  - Verification:
    - `python3 scripts/llm_memory_followup_refiner_smoke_test.py`
    - `python3 scripts/persona_relevance_policy_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`

- Fixed two remaining state-read/transient-state leaks from the late event sessions.
  - `core/operational_state_service.py` now treats date-scoped schedule queries like `what's on my schedules for tomorrow` and `Do I have an event for tomorrow?` as exact-date event reads instead of dumping the full upcoming-events list.
  - `memory/transient_state.py` now exposes `reconcile_operational_change(...)` so matching soft-intent entries are cleared when an explicit task/event is added, removed, or completed.
  - `core/agent.py` now calls that transient reconciliation hook on authoritative task/event mutations, and `app.py` / `harness/session.py` both pass the live `TransientStateManager` into `AgentBrain`.
  - Cleaned the stale live `intent:bike-loot-tomorrow` residue from `data/state/intent_state.json`.
  - Added / updated verification:
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/transient_state_manager_smoke_test.py`
    - `python3 scripts/agent_transient_reconcile_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`

## 2026-03-13

- Repaired Qwen persona payloads after llama-server started rejecting system-only persona requests with `No user query found in messages.`
  - Root cause: `core/prompting.py` folded the cleaned user/assistant history into `[CONVERSATION_TRANSCRIPT]` inside the first system prompt and then returned only that single `role="system"` message.
  - Final fix: keep the single leading system message for Qwen compatibility, but append the cleaned `user` / `assistant` turns after it so the payload still exposes a real user query to the parser.
  - Verification:
    - `python3 -m compileall core/prompting.py scripts/persona_system_event_role_smoke_test.py scripts/vision_prompt_hygiene_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/vision_prompt_hygiene_smoke_test.py`
- Re-fixed Qwen persona system-message ordering after a regression in `core/prompting.py`.
  - Root cause: the Qwen-compatible single-system path still emitted a second trailing `role="system"` message for `[NO_MUTATION_RULE]`, `[FINAL_STAGE_OUTCOME]`, and related runtime context.
  - Evidence: current persona escalations matched llama-server 400s with `System message must be at the beginning.`
  - Final fix: merge `[LATEST_RUNTIME_CONTEXT]` back into the first system prompt and keep only user/assistant messages afterward on the single-system path.
  - Updated `scripts/persona_system_event_role_smoke_test.py` to assert there is no trailing system message.
  - Verification:
    - `python3 -m compileall core/prompting.py scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`

## 2026-03-11

- Fixed persona runtime-event serialization for single-system-message models.
  - Root cause: `build_persona_messages(...)` was appending `[LATEST_SYSTEM_EVENT]` blocks as `role="user"` on the Qwen-compatible single-system path, which made debug logs and model context look like the user had authored system runtime rules such as `[NO_MUTATION_RULE]`.
  - Final fix: `core/prompting.py` now embeds terminal runtime-event blocks inside the merged system prompt under `[LATEST_RUNTIME_CONTEXT]` instead of serializing them as separate chat messages at all.
  - The merged system protocol explicitly says those blocks are authoritative system facts, not user claims or prior assistant narration.
  - Added `scripts/persona_system_event_role_smoke_test.py`.
  - Verification:
    - `python3 -m compileall core/prompting.py scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
- Added event-driven speech policy toggles and a deliberately noisy test mode.
  - Added `ui/event_speech.py` as the policy layer for event-notification TTS:
    - mode normalization/labels
    - event-to-speech mapping
    - `Off / Important / All / Noisy`
  - `ui/layout.py` now exposes an `Events:` combo in the main control row.
  - `ui/controller.py` now owns:
    - current event-speech mode
    - short dedupe window for repeated notifications
    - direct event-notification speech dispatch through the current style voice/speed
    - background one-line visual commentary for fresh image updates when `Events: Noisy` is active
  - `ui/controller_queue.py` now feeds selected UI/runtime events into that policy:
    - boot ready
    - engineering escalation
    - errors
    - status/dashboard activity
    - code-session launch/status
    - search completion
    - boot/agent logs in `Noisy`
    - short `vision_snapshot_note` events for fresh image/screen captures in `Noisy`
  - Added `scripts/event_speech_policy_smoke_test.py`.
  - Verification:
    - `python3 -m compileall ui scripts/event_speech_policy_smoke_test.py`
    - `python3 scripts/event_speech_policy_smoke_test.py`
    - `.venv\\Scripts\\python.exe` import probe for `ui.controller`, `ui.layout`, `ui.controller_actions`, `ui.controller_queue`
- Added separate live-vision session memory plus commentary-style vision notes.
  - Added `memory/vision_session.py` as an ephemeral rolling note buffer for active live-screen use.
  - `app.py` now instantiates that buffer once and shares it between the UI controller and `PromptContextService`.
  - `ui/layout.py` renames the main capture toggle from `SNAP` to `VISION`.
  - `ui/controller.py` now:
    - tracks vision-session activation through the live-screen toggle
    - generates short companion-style visual comments instead of descriptive screen summaries
    - includes recent visual comments in the note-generation prompt to reduce repetition
    - only allows speech on meaningfully changed visual comments
  - `core/prompt_context.py` and `core/prompt_builder.py` now expose those notes to persona under `[VISION SESSION NOTES]`.
  - Persona keeps normal recall/context while vision is active; the visual notes are an additional ephemeral stream, not a replacement for ordinary session continuity.
  - `ui/controller_queue.py` now logs every vision comment to the status pane, but only spoken visual remarks are stored in the separate vision-session memory.
  - Added `scripts/vision_session_memory_smoke_test.py`.
  - Verification:
    - `python3 -m compileall memory/vision_session.py core/contracts.py core/prompt_context.py core/prompt_builder.py ui app.py scripts/event_speech_policy_smoke_test.py scripts/vision_session_memory_smoke_test.py`
    - `python3 scripts/event_speech_policy_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts\\vision_session_memory_smoke_test.py`
    - `.venv\\Scripts\\python.exe` import probe for `app`, `ui.controller`, `ui.layout`, `ui.controller_actions`, `ui.controller_queue`, `ui.event_speech`, `core.prompt_context`
- Tightened vision/persona prompt hygiene after live movie-mode drift.
  - Root cause 1: the visual-commentary prompt did not explicitly frame screen captures as media/app content, so the model drifted into second-person webcam-style remarks such as `You look like...`.
  - Root cause 2: the assistant `Thinking...` placeholder was still eligible to enter persona history as if it were a real prior utterance.
  - `ui/vision_commentary.py` now centralizes vision-commentary prompt building and recent user-context extraction.
  - The prompt now explicitly says the capture may be a movie/video/game/app screen, not a webcam feed, and includes recent user context like `we are watching ironman`.
  - `memory/vision_session.py` now rejects viewer-assumption notes such as `You look...` from both speech gating and returned session notes.
  - `core/prompting.py` now strips exact assistant `Thinking...` placeholders from model history.
  - Added `scripts/vision_prompt_hygiene_smoke_test.py`.
  - Verification:
    - `python3 -m compileall ui/vision_commentary.py memory/vision_session.py core/prompting.py scripts/vision_prompt_hygiene_smoke_test.py`
    - `python3 scripts/vision_prompt_hygiene_smoke_test.py`
- Tightened visual-comment repetition control.
  - `ui/vision_commentary.py` now tells the model not to reuse any recent remark and to return `SKIP` when it has no fresh angle.
  - `ui/controller.py` now drops `SKIP` instead of forcing a recycled status/speech event.
  - `memory/vision_session.py` now compares a new remark against the whole recent spoken-vision buffer, not only the last spoken note.
  - Updated `scripts/vision_prompt_hygiene_smoke_test.py` and `scripts/vision_session_memory_smoke_test.py`.
  - Verification:
    - `python3 -m compileall ui/vision_commentary.py memory/vision_session.py ui/controller.py scripts/vision_prompt_hygiene_smoke_test.py scripts/vision_session_memory_smoke_test.py`
    - `python3 scripts/vision_prompt_hygiene_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts\\vision_session_memory_smoke_test.py`
- Normalized the world-model graph closer to the intended shape.
  - Same-name people can now stay distinct when the user clarifies they are different people:
    - `memory/world_model.py` now resolves relation targets with label-collision handling and can mint stable relation-scoped ids like `person:ekin_friend`
    - prompt guidance in `memory/world_model_prompts.py` now tells the extractor not to collapse different people that share the same label
  - Temporary workspace artifacts are now treated as temporary graph memory:
    - file-backed project nodes and `works_on` edges default to a transient expiry when no ttl is provided
    - startup normalization backfills expiry for existing workspace-artifact graph entries
  - The compatibility mirror now disambiguates duplicate labels when flattening world-model relationships, so `knowledge.json` can render keys like `Ekin (partner)` and `Ekin (friend)` instead of overwriting one with the other.
  - The rendered world-state block now shows incoming relation hints for same-name entities, so both `Ekin` nodes stay visible in prompt context when queried.
  - Live state normalized:
    - split `Ekin` into separate partner/friend nodes
    - set temporary expiry on `Catch the Stars`
  - Verification:
    - `python3 -m compileall memory core app.py`
    - `.venv\\Scripts\\python.exe` dummy-manager probe confirmed:
      - world-state render shows `Entity: Ekin [partner]` and `Entity: Ekin [friend]`
      - `knowledge.json` mirror contains distinct `Ekin (partner)` and `Ekin (friend)` keys
- Fixed a world-model hygiene bug that let transient correction chatter become durable profile memory.
  - Root cause: the async world-model refresh could still run on arbitrary recent chat, and the accepted attribute shape was too permissive.
  - Added `history_contains_world_model_candidate(...)` in `memory/knowledge_history.py` so durable world-model refresh now skips recent turns that do not look like real profile/project/entity disclosures.
  - Added `profile_fact_shape_is_allowed(...)` in `memory/knowledge_fact_rules.py` and applied it in `memory/world_model.py` for both live merges and legacy import, blocking meta keys like `user_corrected_*` and codelike values such as `foo = true`.
  - Added a startup scrub pass in `WorldModelManager` so malformed/meta memory entries are removed from `world_model.json` and mirrored `knowledge.json`.
  - Tightened `memory/world_model_prompts.py` to forbid storing assistant-mistake corrections, temporary filename/game references, or boolean-expression payloads as world-model facts.
  - Cleaned the live polluted state by removing the bogus `User Corrected Game Name` / `user_corrected_game_name` entry from `data/state/knowledge.json` and `data/state/world_model.json` (plus backups).
  - Verification:
    - `python3 -m compileall memory core app.py`
    - `.venv\\Scripts\\python.exe` probe:
      - `profile_fact_shape_is_allowed('user_corrected_game_name', \"'thousand bulls' or user_confused_about_game_name = true\") -> False`
      - `history_contains_world_model_candidate(['Not thousand bulls the less that game you said you made']) -> False`
- Hardened the generic code inspect/fix/run flow after repeated `catch_the_stars`-style failures.
  - Root causes:
    - diagnosis-only FILE_WORK stages could still be treated like targeted read/display stages
    - failed code rewrites did not feed enough real artifact state back into the next planner step
    - current-state code verification could accept overlapping-token corruption (`SCREEN_WIDTHH`)
    - vague code follow-ups could drop the known active script target and burn steps searching the workspace
    - plain `Run <script>.py.` follow-ups could inherit stale interactive-verification success criteria
  - Fixes:
    - `core/file_stage_policy.py`
      - diagnosis stages no longer count as direct-content-display stages
      - added semantic recovery hints for directional-control gaps and likely identifier typos
    - `core/executor.py`
      - failed code-edit checks now push current exact source plus stronger recovery hints back into scratchpad
      - diagnosis stages can complete from grounded read evidence instead of stalling on checker-only gates
    - `core/file_checker_rules.py`
      - current-state code verification now checks code-edit success with token-aware matching and directional-control logic
    - `core/route_normalizer.py`
      - vague code/game follow-ups now inherit the last explicit script target
      - plain run requests now normalize to launch-oriented success conditions
    - `core/scratchpad_formatter.py`
      - mutation observations now include snippet content so the planner can see malformed rewritten source
    - `core/prompt_builder.py`
      - code-edit override now pushes minimal grounded edits instead of full-file JSON blobs
  - Added regressions:
    - `scripts/code_edit_recovery_hint_smoke_test.py`
    - `scripts/code_edit_current_state_verifier_smoke_test.py`
    - `scripts/code_target_followup_normalizer_smoke_test.py`
    - `scripts/redundant_code_read_guard_smoke_test.py`
    - `scripts/code_repair_flow_smoke_test.py`
  - Verification:
    - `python3 scripts/code_edit_recovery_hint_smoke_test.py`
    - `python3 scripts/code_edit_current_state_verifier_smoke_test.py`
    - `python3 scripts/file_stage_policy_smoke_test.py`
    - `python3 scripts/code_target_followup_normalizer_smoke_test.py`
    - `.\.venv\Scripts\python.exe scripts\redundant_code_read_guard_smoke_test.py`
    - `.\.venv\Scripts\python.exe scripts\code_repair_flow_smoke_test.py --json --keep-data-copy`
  - Latest clean passing artifact:
    - `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-ppeiopdx\\data`
  - Follow-up observation:
    - one later rerun reached the successful run-stage outcome in the isolated logs but lingered during teardown; I terminated the rerun manually and confirmed no `llama-server` processes remained afterward.
- Fixed a task-clarification regression where `CHAT` stages inside `TASK` could fall through to a fake `RUN_CODE` success.
  - Added `core/stage_policy.py` so generic stage intent like `CHAT`, approval pauses, and user-input pauses are classified outside FILE_WORK-only logic.
  - `core/executor.py` now treats `CHAT` stages as proposal-only:
    - no runtime tools are exposed
    - planner must finish with `tool: null`, `is_complete: true`, and a `proposal`
    - the stage pauses as `AWAITING USER INPUT` instead of masquerading as completed execution
  - `core/orchestrator_phases.py` now carries that pause state through to persona and tells persona to ask for the missing details instead of narrating completion.
  - `core/scratchpad_formatter.py` now prefers verifier/proposal notes over the raw last step when building `[FINAL_STAGE_OUTCOME]`, so generic `RUN_CODE executed` no longer wins over stronger stage evidence.
  - `data/prompts/manager.txt` now explicitly tells the planner that `CHAT` stages are handoff pauses, not tool-execution stages.
  - Verification:
    - `python3 -m compileall core data/prompts app.py`
    - `.venv\\Scripts\\python.exe` stubbed executor check for the bad game-clarification case now ends as `PAUSED / AWAITING USER INPUT` with the proposal in `LAST_LOG`
- Added first-pass engineering-support sensing and Codex-brief generation.
  - `core/contracts.py` now defines `RuntimeSignal` and `EscalationDecision`.
  - `core/engineering_support.py` now owns:
    - signal normalization
    - automatic escalation heuristics
    - local Codex-brief JSONL writing
    - manual snapshot generation
  - `core/orchestrator.py` now owns the detector for each run and exposes `emit_runtime_signal(...)`.
  - `core/executor.py` now emits structured runtime signals for:
    - planner JSON failures
    - planner repetition loops
    - verification blocks
    - repeated FILE_CHECKER failures
    - true mutating file steps that succeed without changing workspace state
  - `core/orchestrator_phases.py` now emits route/search/persona runtime-error signals and tells persona to mention prepared engineering support when a task fails after an escalation brief is generated.
  - Manual `/codex [note]` snapshots now work from both the GUI and the harness, writing to `data/debug/codex_escalations.jsonl`.
  - Added `scripts/codex_escalation_smoke_test.py`.
  - Important integration fix during validation:
    - moved the new `ENGINEERING_SUPPORT_RULE` in `phase_persona` to after `outcome_failed` is defined
    - narrowed `mutation_no_effect` sensing so `find_paths` / `read_text` in a mutating stage do not falsely trigger escalation
  - Verification:
    - `python3 -m compileall core ui harness scripts app.py`
    - `.\.venv\Scripts\python.exe scripts/codex_escalation_smoke_test.py`
    - `.\.venv\Scripts\python.exe scripts/file_edit_smoke_test.py --json`
    - `.\.venv\Scripts\python.exe scripts/file_crud_smoke_test.py --json`
    - `.\.venv\Scripts\python.exe scripts/file_lookup_smoke_test.py --json`
  - Tightened `scripts/file_edit_smoke_test.py` so blank assistant replies no longer pass the first two turns just because the on-disk file happened to be correct.
- Repo Sweep Hard pass:
  - fixed embedded Code-tab session rerun/stop races in `core/code_session.py`, so superseded processes no longer leak stale output or duplicate inactive events
  - added `scripts/code_session_smoke_test.py` to verify prompt-without-newline output, stdin delivery, clean exit, and silent rerun
  - fixed successful `FILE_WORK` text-mutation reporting drift by adding structured verified-result scratchpad notes in `core/executor.py`
  - `core/orchestrator_phases.py` now bypasses persona for those verified mutation outcomes, which prevents stale retrieved memory from restating old file contents after a verified edit
  - cleaned small repo drift from the UI/status path:
    - removed dead status-widget queue branches in `ui/controller_queue.py`
    - stopped emitting the dead `status_widget_dashboard_mode` event from `core/orchestrator.py`
    - fixed the stale `ui/commands.py` module docstring and dropped one unused import from `ui/controller.py`
  - verification:
    - `python3 -m compileall core ui tools scripts app.py`
    - `.\.venv\Scripts\python.exe scripts/file_edit_smoke_test.py --json`
    - `.\.venv\Scripts\python.exe scripts/file_lookup_smoke_test.py --json`
    - `.\.venv\Scripts\python.exe scripts/file_crud_smoke_test.py --json`
    - `.\.venv\Scripts\python.exe scripts/code_session_smoke_test.py --json`
    - `.\.venv\Scripts\python.exe scripts/file_chaos_test.py --json`
- Added a one-click screen snapshot path for awareness before any continuous screen-share work.
  - `tools/screen_capture.py` captures the primary display through Windows PowerShell/System.Drawing and saves a downscaled JPEG under `data/workspace/images`.
  - Chat UI now has a `SNAP` button beside `MIC`; pressing it captures the current screen, updates `Visual Cortex`, and runs the existing local vision flow with a default screen-awareness prompt.
  - Verified the capture helper creates a real JPG and that `/vision "<captured-path>" Describe this screen briefly.` returns a valid desktop summary with the active 9B + mmproj runtime.
- Added a minimal `/vision "<image-path>" <question>` command that sends local images to the active multimodal `llama-server` path.
  - `llm/llm_server_client.py` now accepts multimodal message content parts and renders image placeholders instead of raw data URIs in debug prompt previews.
  - `tools/vision.py` resolves local image paths, encodes them as data URIs, and retries a few `llama.cpp`-compatible content-part variants to tolerate server format drift.
  - UI and harness command handling now treat `/vision` as a real assistant turn, so existing TTS, stop, status, and transcript flows still apply.
  - Verified with `scripts/piper_harness.py once "/vision data/workspace/images/sine_wave.png Describe this image briefly."` against the active 9B + mmproj runtime.
- Added dedicated ingested-document memory in `memory/documents.py`.
  - Stores ingested document metadata in `data/state/ingested_documents.json`.
  - Stores document vectors in a dedicated Chroma collection (`piper_documents`) under the shared `vector_store`.
  - Prompt context now appends retrieved document excerpts as `[INGESTED DOCUMENTS]`.
- Added explicit persona self-recall via `[RECALL: keywords]`.
  - Persona keeps live streaming; recall is handled before visible output when the reply begins with a recall tag.
  - `Thinking...` now appears in chat while route/plan/act work is in flight and is replaced on the first streamed assistant output.
- Added proper document ingest capability:
  - `.pdf` extraction via `pypdf`
  - `.docx` extraction via direct `word/document.xml` parsing without adding a Word-specific dependency
  - text-like file ingestion remains supported
- Added a Dear PyGui file-picker path for document ingest from the `Documents` tab, while keeping `/ingest <path>` as a command fallback.
- Added `ARCHITECTURE.md` as a current-state repo map that explicitly defers to `AGENTS.md` for doctrine.
- Added UI surfaces for the new state:
  - `Code` tab beside `Visual Cortex`
  - `Documents` tab between `Status` and `Monitor`
  - main controls now stay disabled during boot and while active work is running, while `Stop` remains enabled for cancellation
- Added `/ingest <path>` as the current document-ingest entrypoint.
- Verification:
  - `python3 -m compileall app.py config.py core ui memory tools`
  - `.venv\\Scripts\\python.exe -m compileall app.py config.py core ui memory tools`
  - `.venv\\Scripts\\python.exe` probe confirmed `DocumentMemoryManager` lists zero docs cleanly on the live state
  - `.venv\\Scripts\\python.exe` temporary ingest/recall probe succeeded, then the temporary `brief.txt` document record was removed from live state
  - isolated temp-data probe now ingests both a synthetic PDF and synthetic DOCX successfully and retrieves them through document recall

## 2026-03-10

- Added a separate `insta_agent/` Instagram-content PoC that borrows Piper's model/runtime surfaces without coupling to Piper's orchestrator or UI.
- WSL runtime note for Windows `llama-server.exe`:
  - model and mmproj arguments must be passed as Windows-style paths (`C:\...`), not `/mnt/...`
  - the server must bind `0.0.0.0` instead of `127.0.0.1`
  - the WSL client must call the Windows gateway IP from `ip route` rather than `127.0.0.1`
- The PoC now prefers a local non-streaming HTTP bridge because SSE streaming over the WSL-to-Windows gateway surfaced timeout/read quirks that were not worth carrying in the standalone prototype.

## 2026-03-09

- Added a repo-local note system so future coding passes can preserve working knowledge outside chat context.
- Fixed event normalization for direct dated appointment disclosures.
- Fixed event date resolution for phrases like `24th of March at 1 p.m.`.
- Added default expiry handling for transient knowledge such as `pending_*`.
- Changed retrieved memory rendering from raw dates to age labels.
- Fixed memory decay selection so recall applies decay over a larger candidate pool before trimming results.
- Unified active knowledge reads so expired entries are not exposed through direct knowledge tools.
- Fixed qwen persona chronology so the latest stage outcome is appended after the conversation transcript instead of being merged ahead of the user turn.
- Fixed malformed planner fallback parsing and `RUN_CODE` normalization so escaped newlines no longer get written literally into `temp_exec.py`.
- Expanded `FILE_WORK` rails so `FILE_OP` supports and verifies `list_tree`, `ensure_dirs`, `move_many`, `copy_many`, and `delete_many` as first-class structured actions.
- Added runtime enforcement for non-mutating file stages so inspection/planning stages do not mutate and simple directory walks no longer depend on `RUN_CODE`.
- Fixed two Windows-specific harness/runtime bugs in FILE_WORK:
  - `list_tree` short-path vs long-path mismatch under isolated Windows temp dirs
  - directory-only checker logic incorrectly reading later file requirements from stage context
- Revalidated the live harness:
  - structured file CRUD flow now passes end-to-end
  - open-ended folder organization can inspect, create folders, move files, and verify the final state
  - proposal-first organization leaves the workspace unchanged on the proposal turn
- Fixed a regression where persona `[ROUTER]` loopback was ignored after failed task outcomes; failed/incomplete outcomes can now re-enter routing again.
- Hardened planner JSON parsing so malformed planner replies that use `action` instead of `tool` are recovered instead of collapsing into an empty-tool retry.
- Widened FILE_WORK retry breathing room in the executor so partial progress is not sent to Inspector as early.
- Fixed another FILE_WORK regression where Inspector `FINISH` could be treated as stage success even though `FILE_CHECKER` never reached `VERIFIED`.
- Added visible raw-log reporting for non-VERIFIED FILE_CHECKER outcomes so retry failures are diagnosable without reading scratchpad internals.
- Added explicit no-op protection for `move_path` / `move_many` / `copy_path` / `copy_many` so self-moves and self-copies fail loudly instead of masquerading as progress.
- Tightened broad-scope `FILE_WORK` verification so `move_many` / `copy_many` only verify fully when they actually cover the stage scope; partial batches now stay `PARTIAL`.
- Added a runtime warning when qwen repeats identical `list_tree` calls on an unchanged root, so the scratchpad states clearly that repeated inventory is not progress.
- Added a pause rail for broad file reorganization loops: when the planner keeps inspection-looping without a reliable taxonomy, the stage now pauses for proposal/approval instead of blindly rerouting again.
- Lowered planner temperature to `0.0` and tightened the manager prompt so qwen emits shorter structured planner thoughts and is less likely to drift into malformed JSON.
- Hardened malformed planner JSON recovery so FILE_OP / RUN_CODE tool blocks can still be extracted when qwen breaks the outer JSON.
- Truncated planner/inspector scratchpad tails before prompt assembly to reduce qwen3.5 q6 context-overflow failures during long FILE_WORK retries.
- Replaced raw FILE_OP / RUN_CODE scratchpad JSON dumps with safe structured summaries so qwen no longer copies mid-truncated fake paths like `s...` into later file operations.
- Added targeted FILE_WORK lookup rails:
  - new `FILE_OP find_paths` action for exact missing-file/path discovery
  - targeted missing-file stages no longer auto-complete after a generic `list_tree`
  - runtime now emits explicit hints to switch from repeated inventory to `find_paths` after missing-source failures or repeated unchanged `list_tree`
- Fixed a FILE_WORK stage-classification bug where execution goals containing phrases like `according to the plan` were misclassified as planning-only and blocked from legitimate `ensure_dirs` / move actions.
- Added prompt guidance that if Piper is already operating in the workspace root, it must not invent or create a redundant top-level `Workspace` folder unless the user explicitly requests one.
- Added an explicit proposal handoff path for planning / approval stages:
  - planner may now complete with `tool: null`, `is_complete: true`, and a `proposal` field
  - executor stores that proposal in the scratchpad for persona instead of forcing the planner to write proposal text into workspace files
- Hardened malformed planner JSON recovery so `[FILE_OP] ... [/FILE_OP]` blocks survive even when qwen emits invalid outer JSON with raw newlines inside the `tool` field.
- Fixed `FILE_OP find_paths` glob handling:
  - imported `fnmatch` so glob mode no longer crashes
  - wildcard queries like `*.png` now also work in `mode: "basename"` as a forgiving fallback, because qwen often mixes the two
- Added controlled dependency self-healing for `FILE_WORK`:
  - explicit third-party import errors now temporarily unlock `INSTALL_PACKAGE` for the current stage
  - the planner prompt now includes `INSTALL_PACKAGE` docs only when that temporary unlock is active
  - stdlib modules like `fnmatch` are filtered out, so Piper does not try to `pip install` them
  - the activity log now shows when Piper is installing a package and tells the planner to retry the original action afterward
- Fixed another FILE_WORK runtime cluster:
  - malformed `[RUN_CODE]` blocks without a closing tag are now recovered instead of degrading into `Tag [RUN_CODE] requires an argument`
  - `list_tree` now returns top-level per-folder file counts, so one root scan carries more useful structure into planning
  - stage-classification regexes now recognize inflected mutation verbs like `moving`, `removing`, and `copying`, which prevents false folder-structure-only blocks during real execution stages
- Added deterministic no-progress handling in the executor:
  - repeated identical completion-like planner decisions after a successful inspection now auto-finish from the existing evidence
  - repeated identical empty-tool decisions now inject a stronger runtime error instead of silently burning steps
  - dashboard `Thinking:` lines are de-duplicated so the same planner thought does not spam the activity view
- Added generic extension-consolidation rails for FILE_WORK:
  - route normalizer now rewrites broad `group by extension / remove empty folders` requests into concrete inspect -> consolidate -> cleanup stages
  - `FILE_OP` now supports `extension_inventory`, `consolidate_by_extension`, and `delete_empty_dirs`
  - local FILE_CHECKER verification covers those actions from actual workspace state
- Fixed a completion deadlock in repeated FILE_WORK stages:
  - if the planner tries to complete an extension-consolidation stage without a fresh tool call, the executor now verifies the current workspace state directly instead of insisting on a verifier note from the current step only
- Revalidated against an isolated copy of the live `data/workspace`:
  - prompt: organize the workspace, group files by extension, avoid duplicates, delete empty folders
  - outcome: success
  - resulting workspace had one bucket per extension (`images`, `text_files`, `python_scripts`, `.json`) and no empty directories remained
- Observed real qwen3.5 q6 degradation versus qwen2.5 on long file-management retries:
  - `Context size has been exceeded` server errors
  - occasional dropped llama-server connections
  - less stable long-loop behavior even when the execution rails are improved
- 2026-03-09: Extracted WorkspaceToolRuntime from core/agent.py into tools/workspace_runtime.py. Verified with harness: simple task turn succeeded; extension-based workspace organization succeeded on isolated copy with no empty dirs remaining.
- 2026-03-09: Split FILE_WORK policy and checker logic out of core/executor.py into core/file_stage_policy.py and core/file_checker.py. Verified with harness: task add still works; extension-based workspace organization still succeeds with no empty dirs remaining.
- 2026-03-09: Extracted generic executor helper logic into core/executor_support.py and kept StageExecutor as the coordinator. Verified with harness: task add and extension-based workspace organization still succeeded.
- 2026-03-09: Added the named `File Chaos Test` regression surface in `scripts/file_chaos_test.py` plus a VS Code task. It seeds a deterministic messy workspace fixture, runs the natural-language extension-grouping request through the harness, and verifies the final filesystem state from disk.
- 2026-03-09: Adjusted the Dear PyGui shell for better UI readability and Windows behavior:
  - widened the default viewport to `1450x860`
  - widened the fixed right monitor/status pane and increased initial chat wrap width
  - replaced in-app batch relaunch with a restart exit-code contract (`85`) so `start_piper.bat` loops instead of falling into `pause`
  - added a Windows DWM viewport hook that requests immersive dark mode and dark caption colors after `show_viewport()`
  - mechanical verification passed via `compileall` and import checks; live Windows GUI behavior still needs user validation
- 2026-03-09: Refined the top status bar behavior:
  - added ANSI/control-character stripping before top-bar status rendering
  - split the top bar into a colored runtime mode plus grey metadata
  - runtime mode now recognizes dedicated states such as `THINKING`, `GENERATING`, `ROUTING`, `PLANNING`, and `ERROR`
  - planner steps now push structured `Stage x/y | Step n` metadata so stage/session/style context stays neutral while the mode color changes
  - mechanical verification passed via `compileall` and a small import test for status classification and ANSI stripping
- 2026-03-10: Added startup TTS warm-up to the boot sequence:
  - `BootManager` now starts the TTS warm-up thread immediately after server boot begins, so warm-up overlaps normal startup work instead of being appended after readiness
  - boot-time warm-up is still skipped on `resume_server()` so image-generation LLM resumes do not keep rewarming TTS
  - `TTS.warm_up()` is idempotent, starts worker threads if needed, and runs one dry synthesis without playing audio to absorb the first-use cold start
  - app startup now registers `Warming TTS engine...` as a boot-time task
  - mechanical verification passed via `compileall` and a dry import check with `TTSConfig(enabled=False)`
- 2026-03-10: Reorganized `data/` into owned subfolders and updated path owners:
  - `data/state` for live JSON/memory state
  - `data/debug` for runtime prompt/TTS/debug logs
  - `data/benchmarks/{results,logs,scripts}` for model comparison artifacts
  - `data/harness/{results,scripts}` plus `data/harness/_harness_prompt.txt`
  - `data/reference` for static reference material like `llama_b8241_help.txt`
  - added config path helpers/properties and updated runtime, harness, and benchmark scripts to use the new layout
  - mechanical verification passed via `compileall` plus import/path checks after the on-disk move
- 2026-03-09: Split core/prompting.py into a thin facade plus core/prompt_builder.py and core/scratchpad_formatter.py. Verified with harness: simple task turn still succeeds.
- 2026-03-09: The prompting split surfaced a real Windows path-alias bug in FILE_WORK regression runs (short-path vs long-path temp workspace aliases). Fixed canonical path checks in tools/file_ops.py, tools/workspace_runtime.py, and core/file_checker.py, then reran File Chaos Test successfully.
- 2026-03-09: Split memory/knowledge.py into a slimmer coordinator plus memory/knowledge_policy.py and memory/knowledge_prompts.py. Verified behavior with targeted policy probes (transient expiry, additive merge, grounding) and harness runs.
- 2026-03-09: Extracted basic UI rendering helpers into ui/controller_render.py so controller chat rendering and bounded log rendering no longer carry raw formatting logic inline.
- 2026-03-09: After the knowledge/UI split, the File Chaos Test still passed. Observed one runtime difference: the successful run reported a shorter Routing/Generating-only status trace instead of the earlier detailed Stage X / Step Y statuses, but final artifact state remained correct.
- 2026-03-10: Split core/route_normalizer.py into focused helpers:
  - core/route_patterns.py for regex policy
  - core/route_dates.py for date phrase extraction/resolution
  - core/route_subjects.py for event/task subject extraction and follow-up grounding
  - Verified with direct normalization probes, `py_compile`, harness task smoke, and File Chaos Test.
- 2026-03-10: Split the extension-consolidation subsystem out of tools/workspace_runtime.py into tools/workspace_extension_ops.py.
  - WorkspaceToolRuntime now delegates extension inventory, destination inference, and folder scoring to the new helper module.
  - Verified with `py_compile`, harness task smoke, and File Chaos Test.
- 2026-03-10: During File Chaos Test after the workspace-runtime split, observed one transient llama-server connection reset (`WinError 10054`) under qwen3.5-q8.
  - The task recovered via reroute and still completed successfully.
  - This is a runtime stability wrinkle worth watching; it did not corrupt state or fail the final artifact verification.
- 2026-03-10: Split FILE_OP runtime dispatch out of tools/workspace_runtime.py into tools/workspace_file_actions.py, then split that dispatcher into action-family modules:
  - tools/workspace_query_actions.py
  - tools/workspace_mutation_actions.py
  - tools/workspace_extension_actions.py
  - Verified with py_compile, harness task smoke, and repeated File Chaos runs.
  - One intermediate regression was caused by exec_file_op being dedented out of WorkspaceToolRuntime during the refactor; fixed immediately and revalidated.
- 2026-03-10: Split core/file_checker.py local rule logic into core/file_checker_rules.py.
  - FileWorkChecker now delegates local FILE_OP verification to LocalFileOpRuleChecker.
  - Verified with py_compile and harness/file-work regression surfaces.
- 2026-03-10: Split memory/knowledge_policy.py into:
  - memory/knowledge_history.py
  - memory/knowledge_fact_rules.py
  - knowledge_policy.py is now a small re-export surface.
  - Verified with targeted policy probe (expiry, additive merge, grounding) and harness smoke.
- 2026-03-10: Fixed a latent LlamaServerClient interface mismatch.
  - llm/llm_server_client.py now accepts optional cancel_token on generate()/generate_stream() to match orchestrator/executor usage.
  - This surfaced during harness validation after the refactor and was fixed before the final validation pass.
- 2026-03-10: File Chaos remains slightly q8-variant.
  - One seeded run failed with a non-deterministic FILE_WORK route/checker path.
  - Immediate rerun on the same test surface passed cleanly with correct artifact state.
  - Treat this as model/runtime variance, not a deterministic regression from the refactor.
- 2026-03-10: Hardened the UI stop button into a shared cancellation path.
  - Added a repo-wide cancellation token (`runtime_control.py`) and threaded it through UI generation, orchestrator phases, planner/inspector/file-checker LLM calls, background search, image generation, `RUN_CODE`, and bulk `FILE_OP` loops.
  - The stop button now cancels active work instead of only stopping TTS, and the top bar distinguishes `STOPPING` from final `CANCELED`.
  - Verified with `python3 -m compileall` plus smoke checks for pre-canceled LLM calls and mid-run interpreter cancellation.
- 2026-03-10: Fixed secretary/planner JSON recovery in `core/json_utils.py`.
  - Added missing malformed-JSON recovery helpers (`_append_missing_json_closers`, `_extract_object_field`).
  - This fixed the q9 task-completion regression where a truncated secretary card for `I bought the milk.` parsed into a planner-like fallback and produced `Route: None`.
  - Revalidated with a targeted q9 harness run saved at `data/benchmarks/results/task_event_completion_q9_targeted.json`.
  - Verified outcomes from agent logs and kept state:
    - `ADD_TASK` then `COMPLETE_TASK` for `buy milk`
    - `ADD_EVENT` then `COMPLETE_EVENT` for `dentist appointment`
    - `Add a task to buy milk tomorrow.` routed to `ADD_EVENT` and left `state/tasks.json` empty while adding `buy milk: 2026-03-11` to `state/events.json`
  - No lingering `llama-server.exe` remained after the run.
- 2026-03-10: Read-only task/event status questions now normalize to `CHAT` instead of entering `TASK` mode.
  - Implemented in `core/route_normalizer.py` with `READONLY_TASK_EVENT_QUERY_RE` in `core/route_patterns.py`.
  - Prompt guidance in `data/prompts/secretary.txt` now says persona should answer read-only task/event status questions from prompt context rather than creating a task card.
  - Verified with a live harness check saved at `data/benchmarks/results/route_status_query_chat_check.json`, where `What tasks and events do I have now?` produced `Secretary Raw: {"decision":"CHAT"}` and `Route: CHAT`.
- 2026-03-10: Targeted 4B-Q8 vs 9B-Q6 assessment compare.
  - `model_compare_targeted_q8_vs_q9.json`: 9B-Q6 passed the stale-context correction case (`Piper` -> `No, tomorrow is off.`) while 4B-Q8 did not.
  - `model_compare_event_followup_q8_vs_q9.json`: both models failed the neutral ambiguous event follow-up case, which currently looks like a system-level weakness more than a model separator.
  - `file_chaos_q8_assessment.json`: 4B-Q8 failed File Chaos, leaving empty directories and stopping on FILE_CHECKER_VERDICT gating.
  - `file_chaos_q9_assessment.json`: 9B-Q6 passed File Chaos cleanly with no empty directories or misplaced files.
  - Practical conclusion: 9B-Q6 is the better assessment model and is the better long-loop candidate right now; 4B-Q8 remains the snappier daily model but is less reliable on harder workflows.
- 2026-03-10: Clean harness rerun for small FILE_WORK is currently failing despite healthy boot/runtime.
  - Used `PiperHarness(isolated_data=True, keep_data_copy=True)` and explicitly cleared the isolated `state/memory.jsonl` before `start()`, because the harness otherwise loads recent memory from the copied data dir.
  - Repro task chain: create `text_files/harness_alpha.txt`, copy it into `text_files/harness_box/`, move it, read it, then delete the copy.
  - All five turns returned without timing out, but the workspace remained unchanged: no created source file, no copy, no moved file.
  - Clean artifacts were kept at `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-yrjzh4g2\data`.
  - Prompt/debug evidence shows three distinct failure classes:
    - checker deadlock on already-satisfied or completion-like states (`The stage goal is complete.` immediately followed by `FILE_WORK cannot complete until FILE_CHECKER_VERDICT is VERIFIED.`)
    - cross-stage grounding drift where the prompt says `text_files/harness_alpha.txt` exists but `copy_path` immediately fails with source not found
    - user-facing narration drift where a `read_text` not-found result is reported as an absolute-path / parent-path security violation
- 2026-03-10: Fixed the deterministic small FILE_WORK CRUD regression and codified it as a smoke test.
  - Added direct route normalization for single-path CRUD requests in `core/route_normalizer.py` / `core/route_patterns.py`, so create/copy/move/read/delete file requests no longer depend on the router inventing multi-stage directory scaffolding.
  - Sanitized FILE_WORK intent classification in `core/file_stage_policy.py` so filenames and folder names like `text_files` or `harness_alpha_moved.txt` no longer trip mutation/inspection heuristics by substring alone.
  - Extended current-state verification in `core/file_checker.py` / `core/file_checker_rules.py` beyond extension reorg, so already-satisfied file stages can verify from actual workspace state instead of looping on `FILE_CHECKER_VERDICT`.
  - Tightened copy verification to compare destination content against the source file when applicable.
  - Replaced the odd `PiperGen_00071_.png` FILE_OP syntax example with a neutral `logo.png` example in `tools/registry.py` to reduce prompt anchoring noise.
  - Tightened persona completion/failure handoff in `core/orchestrator_phases.py` so `LAST_LOG` is treated as the authoritative cause/evidence, including already-satisfied file states.
  - Added `scripts/file_crud_smoke_test.py` as the reusable isolated harness regression surface for this path.
  - Verification:
    - targeted local probes for route normalization, FILE_WORK intent classification, and current-state checker synthesis
    - clean isolated harness CRUD rerun passed end-to-end with kept artifacts at `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-y75wux6i\data`
    - reusable smoke script passed with `success: true` and kept artifacts at `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-r7ww5o6r\data`
    - targeted one-turn absent-delete rerun now says the file was already absent instead of hanging or reporting an invented path-security error; kept artifacts at `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-altx0va6\data`
- 2026-03-10: Fixed fuzzy workspace document lookup so existing files like `grocery_list.txt` are found and read across follow-up turns.
  - `core/route_normalizer.py` now upgrades document-like content questions and filename-mismatch follow-ups into explicit `FILE_WORK` cards, using recent history to recover subjects for messages like `What's in the file?` and `check again`.
  - `core/route_normalizer.py` now preserves explicit router file targets like `grocery_list.txt` and can recover them from recent history, so pronoun follow-ups such as `Yes, can you read what's in it?` no longer get rewritten into a bogus fuzzy lookup for the literal phrase `what's in it`.
  - `core/orchestrator_phases.py` now applies route normalization even when the secretary emits malformed JSON and would otherwise fall back to bare `CHAT`.
  - `tools/workspace_query_actions.py` now treats separator-normalized basename fragments as valid `find_paths` matches, so queries like `grocery list` match `grocery_list.txt`.
  - `core/file_stage_policy.py` now treats filename lookup/search stages as non-mutating inspection work, blocks `list_tree` from falsely satisfying targeted search/read stages, and matches read/search evidence against quoted lookup terms rather than only explicit filenames with extensions.
  - `core/executor.py` now accepts planner completion from existing non-mutating FILE_WORK evidence before any checker gate, which closes the lingering `find_paths succeeded but is_complete was still blocked` failure mode on lookup-only stages.
  - `core/orchestrator_phases.py` now classifies post-stage failure from structured scratchpad signals (`OBSERVATION_KIND: error`, `SYSTEM ERROR`, non-verified checker notes) instead of raw substring hits on the entire last entry. This fixes the false `Stage Failed/Errors` outcome when a successful lookup step mentions prior failure in the planner thought, such as `The previous search failed. I will retry.`.
  - `core/executor.py`, `data/prompts/manager.txt`, and `tools/registry.py` were updated to steer repeated lookup stages toward `find_paths` with partial filename queries.
  - Added `scripts/file_lookup_smoke_test.py` as the reusable isolated harness regression for this path.
  - Verification:
    - direct local probe: `normalize_route_decision({"decision":"CHAT"}, ...)` now maps the four grocery messages into `FILE_WORK`
    - direct local probe: `handle_find_paths(..., {"query":"grocery list","mode":"basename"})` now returns `grocery_list.txt`
    - direct local probe: a scratchpad step with thought text containing `previous search failed` plus a successful `find_paths` observation now yields `true_success = True`
    - direct local probe: an explicit router card for `grocery_list.txt` plus the follow-up `Yes, can you read what's in it?` now stays targeted on `grocery_list.txt` instead of being rewritten to `what's in it`
  - isolated harness lookup smoke passed with kept artifacts at `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-yfe_bo7h\data`
  - isolated CRUD smoke still passed after the lookup changes with kept artifacts at `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-gloygn8i\data`
- 2026-03-10: Hardened small `FILE_WORK` text-edit and exact-read behavior.
  - `core/scratchpad_formatter.py` now carries `FILE_OP read_text` / `read_many` content snippets into the planner scratchpad with larger observation limits, so edit stages can actually see what they just read.
  - `core/file_stage_policy.py` now derives stage intent from `stage_goal + success_condition` instead of polluting intent heuristics with historical `context`, which fixed read-only follow-ups being misclassified as mutating after prior edit turns.
  - `core/executor.py` now blocks repeated unchanged `read_text` loops in content-edit stages, appends deterministic `FILE_READ_EXACT_*` and `FILE_LOOKUP_MATCHES` notes for successful read/search stages, and no longer lets malformed tool parsing fall through to fake `Done.` success.
  - `core/agent.py` now recovers malformed inline `[FILE_OP ... [/FILE_OP]` and `[RUN_CODE ... [/RUN_CODE]` blocks that qwen sometimes emits.
  - `core/route_patterns.py` / `core/route_normalizer.py` now normalize direct text-edit requests like `Remove 'eggs' from the grocery list file.` and quoted replace requests into explicit `FILE_WORK` edit cards instead of trusting the router to invent a good edit stage.
  - `core/orchestrator_phases.py` now bypasses persona generation for successful exact file reads and targeted filename lookups, answering directly from authoritative scratchpad notes to avoid paraphrase drift and persona timeout failures on simple file turns.
  - Added `scripts/file_edit_smoke_test.py` as the deterministic isolated harness regression for single-file text edits followed by exact readback.
  - Verification:
    - `python3 -m compileall core tools scripts`
    - `.\.venv\Scripts\python.exe scripts\file_edit_smoke_test.py --json --keep-data-copy` -> passing artifact `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-s68411le\data`
    - `.\.venv\Scripts\python.exe scripts\file_lookup_smoke_test.py --json --keep-data-copy` -> passing artifact `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-4w0ok6pl\data`
    - `.\.venv\Scripts\python.exe scripts\file_crud_smoke_test.py --json --keep-data-copy` -> passing artifact `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-r6fm03_3\data`
- 2026-03-10: Hardened llama-server transport for long FILE_WORK runs and revalidated File Chaos on q9.
  - The recent chaos failures were not normal baseline behavior; earlier notes had q9 passing cleanly, but current runs were dying mid-stage with `cannot read from timed out object`, `WinError 10054`, and later `10061`.
  - `llm/llm_server_client.py` now uses a configurable stream read timeout (`CFG.LLAMA_SERVER_STREAM_READ_TIMEOUT_S`, default `30s`) instead of forcing a fragile `0.5s` low-level socket timeout during SSE reads.
  - `llm/boot.py` no longer launches `llama-server` behind an unread `stdout=PIPE`; server output is now written to `data/debug/llama_server.log`, avoiding a latent long-run Windows pipe/backpressure problem and preserving runtime logs for postmortems.
  - `app.py`, `harness/session.py`, and `config.py` were updated to carry the new stream-read timeout setting through both live runtime and isolated harness runs.
  - Verification:
    - `python3 -m compileall llm app.py harness config.py`
    - `.\.venv\Scripts\python.exe scripts\file_chaos_test.py --json --keep-data-copy` -> passing artifact `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-n2y3ybxj\data`
- 2026-03-10: Fixed the remaining false-failure path for already-satisfied mutating `FILE_WORK` stages.
  - Root cause 1: `verify_current_file_stage_state()` only understood explicit file paths from the stage card, so normalized document-reference stages like `grocery list` could not prove current-state success after a prior `find_paths` / `read_text`.
  - Root cause 2: persona exact-read/lookup bypass was reading notes from the whole scratchpad, which risked stale cross-stage file notes overriding later stage outcomes.
  - `core/file_checker.py` now accepts prior tool evidence when doing current-state verification and extracts candidate paths from `requested_path`, `requested_paths`, `files`, `matches`, and `evidence_files`.
  - `core/file_checker_rules.py` now lets `LocalFileOpRuleChecker` prioritize those candidate paths while synthesizing current-state checks.
  - `core/executor.py` now passes the last successful file tool result into current-state verification for completion acceptance and final recovery.
  - `core/orchestrator_phases.py` exact-read and lookup bypass now only inspect notes from the latest stage slice.
  - Direct probe now verifies the normalized grocery follow-up stage as `VERIFIED` with reason `Requested text is already absent, so the success condition is satisfied.`
  - Sequential isolated harness verification:
    - `.\.venv\Scripts\python.exe scripts\file_edit_smoke_test.py --json --keep-data-copy` -> passing artifact `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-jyx6sos1\data`
    - `.\.venv\Scripts\python.exe scripts\file_lookup_smoke_test.py --json --keep-data-copy` -> passing artifact `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-kmh3nere\data`
    - `.\.venv\Scripts\python.exe scripts\file_crud_smoke_test.py --json --keep-data-copy` -> passing artifact `C:\Users\HAWKGA~1\AppData\Local\Temp\piper-harness-jz982w68\data`
  - Note: earlier parallel reruns produced timeouts/blank turns under shared model load; sequential reruns were clean and are the authoritative signal.
- 2026-03-11: Added a supported `RUN_CODE` path for launching existing workspace Python scripts.
  - Problem: the planner tried to execute scripts with `subprocess.run([sys.executable, "script.py", ...])`, but the sandbox blocks `subprocess` and `sys` imports. For interactive scripts like `bulls_and_cows.py`, even a direct non-interactive run would not produce a playable session.
  - `tools/workspace_runtime.py` now recognizes two generalized script-launch forms:
    - `run_workspace_script("relative/path.py")`
    - common legacy `subprocess ... "relative/path.py"` launch snippets, which are auto-reinterpreted into the supported helper
  - On Windows runtime, the helper launches the script in a new console window and reports success if it remains alive past the short startup check. On non-Windows dev runtimes, quick non-interactive scripts still execute, but interactive live-console launch remains Windows-only.
  - `core/file_stage_policy.py` now classifies script-launch stages separately, suppresses file-checker gating for them, and emits a recovery hint telling the planner to use `run_workspace_script(...)` instead of importing `subprocess`.
  - `core/executor.py` now auto-finishes a script-launch stage after a successful `RUN_CODE` launch result.
  - `tools/registry.py` and `data/prompts/manager.txt` now document the helper so the planner has a first-class supported syntax for running existing workspace scripts.
  - Mechanical verification:
    - `python3 -m compileall core tools data/prompts`
    - direct policy probe: `Execute the bulls_and_cows.py script` now yields `stage_is_script_launch_stage=True` and `stage_requires_file_verification=False`
    - direct runtime probe: `run_workspace_script("hello_game.py")` returns `EXECUTED`
    - direct runtime probe: legacy `subprocess.run([sys.executable, "hello_game.py"])` is auto-reinterpreted and returns the same `EXECUTED` result
  - Caveat: the actual interactive console launch path was not live-tested from WSL; it is intended for the Windows Piper runtime.
- 2026-03-11: Converted the `Code` tab from a readonly artefact viewer into an embedded interactive process console.
  - Added `core/code_session.py` as a controller-owned subprocess session with piped stdin/stdout and char-by-char output pumping so prompts like `input("Enter guess: ")` appear immediately even without a trailing newline.
  - `ui/layout.py` now gives the `Code` tab its own status line, console output area, stdin input box, send button, clear button, and local stop button.
  - `ui/controller.py`, `ui/controller_actions.py`, and `ui/controller_queue.py` now own the session lifecycle, launch/focus the Code tab when a script-run request arrives, forward input to the child process, and return Piper to `IDLE` cleanly when the session exits or is stopped.
  - `core/executor.py` now emits a `code_session_launch` UI event when `RUN_CODE` returns the `run_workspace_script` action.
  - `tools/workspace_runtime.py` now requests `launch_mode=embedded_code_tab` instead of spawning an external console directly.
  - Mechanical verification:
    - `python3 -m compileall core tools ui app.py`
    - direct `EmbeddedCodeSession` probe with a temp script that prints `Welcome`, prompts `Enter guess: `, reads one line, echoes it, and exits
    - observed output: `$ python echo_game.py`, `Welcome`, `Enter guess: 1234`, `You said 1234`, `[Process exited with code 0]`
    - direct runtime probe confirms both `run_workspace_script("bulls_and_cows.py")` and legacy `subprocess.run([sys.executable, "bulls_and_cows.py"])` now resolve to `launch_mode=embedded_code_tab`
  - Residual caveat: this supports simple stdin/stdout console programs. Full-screen TUI/curses apps would still need a ConPTY-style terminal path.
- 2026-03-11: Fixed a follow-on routing/runtime bug for script-launch stages.
  - Symptom: a stage like `Locate and execute the Bulls and Cows game script` was still treated as non-mutating lookup work, so one successful `find_paths` auto-finished the stage and nothing launched in the `Code` tab.
  - `core/file_stage_policy.py` now excludes script-launch stages from non-mutating lookup classification and from targeted-lookup completion logic.
  - Successful `find_paths` in a script-launch stage now emits a deterministic hint: `run_workspace_script("matched_script.py")`.
  - `ui/controller_queue.py` now switches to the `Code` tab immediately when an embedded session launch event is received, before the process output starts arriving.
  - Direct probe result for the reproduced stage shape:
    - `stage_is_script_launch_stage=True`
    - `stage_is_non_mutating_file_stage=False`
    - `stage_requires_targeted_lookup=False`
    - recovery hint resolves to `run_workspace_script("bulls_and_cows.py")`
- 2026-03-11: Fixed the Documents-tab ingest action so it no longer feels dead after picking a file.
  - Root cause: the Dear PyGui picker callback ingested documents synchronously on the UI thread and provided almost no visible progress, so first-run embedding/model startup looked like a no-op.
  - `ui/controller_actions.py` now resolves picker payloads more defensively, starts document ingestion on a background thread, emits immediate `[UI] Ingesting ...` chat feedback, and posts final success/failure summaries back through the UI queue.
  - `ui/controller.py` and `ui/controller_queue.py` now track `document_ingest_active` so the `Ingest Document` button disables while an ingest is running and re-enables when it finishes.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall ui app.py core memory`
    - `.\\.venv\\Scripts\\python.exe` import probe for `app`
    - synthetic Dear PyGui selection payload probe covering `file_path_name`, `current_path + file_name`, and `selections`
- 2026-03-11: Fixed read-only Q&A over ingested documents so it no longer falls back into `FILE_WORK` against the raw PDF.
  - Symptom: questions like `What does the document say about RVSM checks?` were routed as `TASK`, then the executor tried `FILE_OP read_text` on the PDF and eventually timed out in `RUN_CODE`.
  - Root cause 1: routing had no deterministic rule for already-ingested document questions, so the Secretary was free to invent a `FILE_WORK` card even though persona already had `[INGESTED DOCUMENTS]` available.
  - Root cause 2: document prompt hits used the beginning of the full ingested text, which is poor for large manuals because page 1 dominates the prompt even when the query is about a later section.
  - `core/orchestrator_phases.py` now short-circuits read-only ingested-document questions to `CHAT` before the Secretary runs, logs that route decision explicitly, and adds a persona tail rule telling Piper to answer from document memory rather than narrating stale file-tool failures.
  - `memory/documents.py` now renders query-focused excerpts from the matched full-document text, with higher weight for explicit acronyms and reference terms, so a query like `RVSM checks` surfaces the RVSM section instead of generic early pages.
  - `data/prompts/secretary.txt` and `data/prompts/instructions.txt` now reinforce that ingested-document Q&A is a read-only chat path, not a file-operation task.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall core memory data/prompts app.py`
    - direct route probe: `_should_route_ingested_document_chat('What does the document say about RVSM checks?', [], docs) -> True`
    - direct excerpt probe now returns the `REDUCED VERTICAL SEPARATION MINIMUM - RVSM` section from the ingested FCOM
    - isolated harness turn now logs `Routed to CHAT via ingested document memory.` and answers without entering the executive loop
- 2026-03-11: Added a dedicated `DOCUMENT_FOCUS` pass for ingested-document Q&A so the persona prompt is cleaner and the activity pane shows source refs.
  - Trigger: after the route short-circuit marks an ingested-document read-only question, the orchestrator now runs a focused extraction pass before persona instead of sending raw multi-page excerpts straight into the final prompt.
  - `core/document_focus.py` adds a small LLM extraction helper that compresses the relevant document snippets into `relevant_info` plus references, driven by the new `data/prompts/document_focus.txt` template.
  - `core/orchestrator.py` / `core/orchestrator_phases.py` now include an internal `DOC_FOCUS` phase, store the focused context on the orchestrator, and log `Document source:` / `Document refs:` into the dashboard activity pane.
  - `core/contracts.py` and `core/prompt_builder.py` now support a `[DOCUMENT FOCUS]` block so persona can answer from the compact extracted context instead of the raw `[INGESTED DOCUMENTS]` dump on those turns.
  - `memory/documents.py` now exposes `extract_document_reference_labels(...)`, which distills broad refs like `Page 6749` and `Section PRO-SPO-50` for the activity pane without spamming narrower Ident-code variants.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall core memory data/prompts app.py`
    - isolated harness turn logs:
      - `--- PHASE 1.5: DOCUMENT FOCUS ---`
      - `Document source: A320 - FCOM - 03 DEC 2025.pdf`
      - `Document refs: Page 6749 | Section PRO-SPO-50`
    - isolated prompt-log probe confirms `PHASE: DOCUMENT_FOCUS` and a persona `[DOCUMENT FOCUS]` block are present for the RVSM query path
- 2026-03-11: Improved large-PDF ingested-document retrieval so concept queries land on the right pages more often.
  - Symptom: queries like `What is the wingspan?` or `What are the dimensions?` were either falling back to the first pages of the FCOM or hitting irrelevant matches, because only a whole-document vector existed and the lexical scorer had no structural penalties.
  - `memory/documents.py` now expands some query concepts (`wingspan` -> `principal dimensions`, etc.), normalizes joined words for matching (`wingspan` vs `wing span`), and reranks sections by document structure.
  - Preliminary pages such as table-of-contents and summary pages are now penalized, while content headings such as `PRINCIPAL DIMENSIONS` and `GENERAL ARRANGEMENT` are boosted.
  - I also explored page-chunk vector indexing, but for very large PDFs like the FCOM it is too expensive to build synchronously. The current runtime therefore keeps the whole-document vector path and uses stronger lexical page/section extraction for large manuals.
  - Mechanical verification:
    - direct recall probe for `What is the wingspan?` now surfaces pages `394`, `395`, and `397` in `DSC-20-20 PRINCIPAL DIMENSIONS` instead of the cover pages
    - direct recall probe for `What are the dimensions?` now surfaces the same `PRINCIPAL DIMENSIONS` section
  - Remaining limitation: some dimension/diagram pages still appear to be image-heavy enough that plain text extraction may omit the actual numeric table values. This is now a source-extraction limitation, not only a retrieval-ranking problem.
- 2026-03-11: Added PDF-page vision fallback for ingested-document Q&A and hardened routing/retrieval for mixed fact queries.
  - `core/document_focus.py` now renders the top matched PDF pages to temporary images and runs a multimodal extraction pass when text focus is empty or looks insufficient for a fact-style query (`wingspan`, `dimensions`, `clearance`, etc.).
  - The visual extractor now requires a visible label match for specific measurement questions and passes the matched label into `[DOCUMENT FOCUS]`, which prevents bare numbers from being reinterpreted incorrectly by persona.
  - `core/orchestrator_phases.py` now fail-closes document turns: read-only ingested-document questions answer only from `[DOCUMENT FOCUS]`, log `Document visual pages:` and `Document vision fallback used.`, and no longer fall back to raw `[INGESTED DOCUMENTS]` when focus extraction is empty.
  - `_should_route_ingested_document_chat(...)` now recognizes plain fact queries such as `What is the wingspan from the document?`, not only summary-style prompts.
  - `memory/documents.py` now merges chunk-vector hits with a whole-document lexical page scan instead of returning early on chunk results, and expands `rvsm` to `reduced vertical separation minimum` / `PRO-SPO-50`. This keeps `RVSM checks` on the RVSM pages instead of checklist noise.
  - Mechanical verification:
    - isolated harness turn: `What is the wingspan from the document?` -> `34.1 m / 111 ft 10 in`
    - isolated harness turn: `What does the document say about RVSM checks?` -> grounded procedural summary from pages `6749-6751`
    - dashboard activity now shows:
      - `Document refs: Page 394 | Section DSC-20-20`
      - `Document visual pages: Page 394 | Page 395 | Page 397`
      - `Document vision fallback used.`
- 2026-03-11: Reworked `SNAP` into a live screen toggle with a fixed rolling image instead of one-shot snapshot narration.
  - `tools/live_screen.py` now owns a background capture loop that overwrites `data/workspace/images/live_screen.jpg` on an interval, keeps only freshness/error state in memory, and never stores a text summary of the screen.
  - `tools/screen_capture.py` now supports atomic capture directly to a fixed target path so the current live frame is always replaced in place.
  - `ui/controller.py`, `ui/controller_actions.py`, and `ui/controller_queue.py` now treat `SNAP` like a toggle: `SNAP` -> `LIVE`, keep the button usable during normal turns, refresh the Visual Cortex preview from the fixed image, and show `Screen: LIVE` in the top bar while the loop is active.
  - `core/orchestrator.py` and `core/orchestrator_phases.py` now pass the current live screen image into normal multimodal route/persona turns when the frame is fresh. The attached-turn instruction explicitly says it is a current frame for this turn, not continuous vision.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall llm ui harness tools core memory app.py`
    - direct `LiveScreenSession` probe confirms `start()`, `current_image_path()`, and `stop()` all resolve to the fixed `live_screen.jpg` path
    - isolated harness probe with `live_screen=LiveScreenSession(...)` produced a normal assistant reply describing the current desktop from the attached live frame
- 2026-03-11: Tuned live screen capture and routing for visual text-reading prompts.
  - Raised the rolling screenshot clamp from `1280` to a configurable `SCREEN_CAPTURE_MAX_DIM` default of `1920`, which now writes a `1920x1080` probe on the current setup instead of the earlier smaller frame.
  - `data/prompts/secretary.txt` now explicitly says that reading text, filenames, labels, buttons, or tabs from the attached live screen is `CHAT`, not `FILE_WORK`.
  - `core/orchestrator_phases.py` now has a deterministic live-screen visual-query guard, so prompts like `Read the file name visible on the screen.` bypass router/task drift and go straight to persona chat handling.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall llm ui harness tools core memory app.py`
    - direct capture probe wrote `C:\\Projects\\Piper\\data\\workspace\\images\\live_screen_probe.jpg` at `1920x1080`
    - isolated harness turn with live screen enabled answered `The file name visible on the screen is \`Ground Crew.txt\`.` without leaking a failed file-task narration
- 2026-03-11: Added multimodal fallback resizing for live-screen turns.
  - Problem: `1920x1080` live frames worked in short `/vision` queries, but longer route/persona turns could intermittently fail in `llama-server` with `failed to process image`.
  - `tools/vision.py` now retries multimodal attachment requests with temporary smaller JPEGs (`1600`, then `1280`) when the original image attempt fails, instead of surfacing `Vision request failed` immediately.
  - This keeps the stored live screen image high-resolution while giving the inference path a graceful degradation path under prompt/image pressure.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall tools/vision.py core/orchestrator_phases.py ui/controller_actions.py app.py`
    - isolated live-screen orchestrator turn after the patch answered visible on-screen text normally instead of failing the turn
- 2026-03-11: Hardened persona against live-screen multimodal failures.
  - `core/prompt_context.py` now lets persona trim memory/document retrieval counts per turn.
  - `core/orchestrator_phases.py` now treats live-screen visual questions as a lighter persona shape: reduced memory recall and no ingested-document prompt stuffing, which cuts irrelevant prompt weight during image turns.
  - Persona now catches `VisionError` instead of letting it crash the whole orchestrator. For visual screen questions it degrades to a short retry message; for non-visual turns it can fall back to text-only generation.
  - `tools/vision.py` fallback resize ladder was extended to `1600`, `1280`, `1024`, `768`.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall core/orchestrator_phases.py core/prompt_context.py tools/vision.py`
    - isolated live-screen turn after the patch completed with an assistant reply instead of surfacing the previous giant `Orchestrator Error: Vision request failed ...` chain
- 2026-03-11: Promoted embedded code sessions into first-class UI runtime state and generalized pane autoscroll.
  - `ui/controller.py` now owns a shared autoscroll scheduler for scrollable child windows and tracks `code_session_meta` so the top bar can show `CODE SESSION | Code: <script>`.
  - `ui/layout.py` converted the boot log, status activity, monitor, documents, and code panes from multiline `input_text` widgets into scrollable child-window text views so Dear PyGui can reliably follow appended output.
  - `ui/controller_actions.py` now routes the main chat input into the running embedded process when a code session is active, which prevents short guesses like `1234` from going back through routing/persona.
  - `ui/controller_queue.py` / `ui/controller_status.py` now keep the right-pane views and code console pinned to the latest output and preserve `CODE SESSION` as the effective runtime mode while the process is live.
  - Mechanical verification:
    - `python3 -m compileall ui core app.py`
    - `.\\.venv\\Scripts\\python.exe` Dear PyGui probe confirmed:
      - child-window `horizontal_scrollbar=True`
      - `set_y_scroll(..., get_y_scroll_max(...))`
      - dynamic button relabeling and input hint updates
    - direct mode probe confirmed `_effective_runtime_mode(...)` returns `CODE SESSION` over idle while a code session is active
- 2026-03-11: Added live-screen runtime controls, active-window capture, and pointer-focused visual turns.
  - `ui/layout.py`, `ui/controller.py`, and `ui/controller_actions.py` now expose live-screen source and refresh-rate controls beside the `SNAP` toggle, with runtime options for `Display`, `Window`, and `Pointer` plus `2s / 5s / 10s / 15s`.
  - `tools/screen_capture.py` now supports capture modes for the display under the cursor, the current foreground window, and a pointer-centered crop, while preserving the existing atomic JPEG overwrite path and `SCREEN_CAPTURE_MAX_DIM` clamp.
  - `tools/live_screen.py` now tracks `mode`, `interval_s`, and a separate fixed-path pointer-focus image so the live loop and one-off pointer crops share the same session object.
  - `core/orchestrator.py` / `core/orchestrator_phases.py` now keep one resolved live-screen attachment per turn and switch to a pointer-centered crop when the user says things like `look here`, `this`, `that`, or `near my cursor` in an otherwise visual live-screen query.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall ui tools core app.py`
    - direct capture probe created:
      - `capture_display.jpg` at `1920x1080`
      - `capture_window.jpg` at `1625x1392`
      - `capture_pointer.jpg` at `1400x900`
- 2026-03-11: Hardened state persistence and trimmed always-on debug/path assumptions.
  - `memory/stores.py` now writes JSON stores atomically and maintains `.bak` companions. If a primary state file is corrupted but the backup is still valid, Piper archives the corrupt file and restores from backup instead of silently loading `{}` and overwriting state on the next save.
  - LLM HTTP payload dumps are now opt-in via `PIPER_DEBUG_LLM_HTTP_PAYLOADS=1`, and prompt/manager debug dumps are separately gated by `PIPER_DEBUG_LLM_PROMPTS=1` and `PIPER_DEBUG_MANAGER_PROMPTS=1`.
  - `llm/boot.py` no longer kills generic `llama-server` processes up front. It now checks for a healthy existing server first and only cleans up stale processes that match Piper's configured port/model/runtime shape.
  - `ui/controller_actions.py` now resolves Visual Cortex image previews through Piper's configured workspace and Comfy output directory instead of a hardcoded `F:` path.
  - `core/pipeline.py` now logs stream-time TTS failures into `tts_debug.txt` instead of swallowing them silently.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall app.py config.py core ui memory tools llm harness`
    - temp-store recovery probe restored a corrupted JSON file from its `.bak` and archived the bad copy as `*.corrupt_*.json`
    - `scripts/file_edit_smoke_test.py --json`
    - `scripts/code_session_smoke_test.py --json`
- 2026-03-11: Reduced helper-script bootstrap duplication.
  - Added `scripts/_bootstrap.py` so the harness/smoke/benchmark entrypoints share one repo-root bootstrap instead of repeating the same `ROOT_DIR` and `sys.path` block.
  - This stays within the existing boundary: runtime logic still lives in `core/`, `memory/`, `tools/`, `llm/`, and `ui/`; `scripts/` remains an entrypoint/regression surface.
  - One intermediate regression was caused by stripping a needed `Path` import from `code_session_smoke_test.py`; fixed immediately and revalidated.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall scripts`
    - `.\\.venv\\Scripts\\python.exe scripts\\piper_harness.py --help`
    - `.\\.venv\\Scripts\\python.exe scripts\\code_session_smoke_test.py --json`
- 2026-03-11: Refactored prompt/environment/state/command boundaries toward the repository doctrine.
  - Added `memory/state_owner.py` so shared `tasks.json`, `events.json`, and `knowledge.json` stores are constructed in one owner module and injected into app/harness/runtime services instead of being recreated ad hoc inside multiple `core` modules.
  - Added `core/instructions_loader.py` and `core/environment_service.py`, then rewired `core/prompt_context.py` into a service + pure assembler split. Prompt assembly now consumes loaded inputs instead of having `core/prompting.py` read instructions or shared state directly.
  - `core/prompting.py` is now a pure formatting module again; the old instruction-file loader and unused debug writer were removed from it.
  - Command parsing was moved down to `core/commands.py`, and `harness/session.py` no longer imports `ui.commands`.
  - `app.py`, `harness/session.py`, `core/orchestrator.py`, and `ui/controller_actions.py` now pass `PromptContextService` explicitly instead of letting the orchestrator assemble prompt/runtime dependencies for itself.
  - Mechanical verification:
    - `.\\.venv\\Scripts\\python.exe -m compileall app.py config.py core ui memory tools llm harness scripts`
    - `.\\.venv\\Scripts\\python.exe -c "import app; print('app_import_ok')"`
    - `.\\.venv\\Scripts\\python.exe scripts\\piper_harness.py --help`
    - `.\\.venv\\Scripts\\python.exe scripts\\code_session_smoke_test.py --json`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_edit_smoke_test.py --json`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_lookup_smoke_test.py --json`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_crud_smoke_test.py --json`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_chaos_test.py --json`
  - Important validation note:
    - the llama-server-backed harness smokes should be run sequentially on one machine; parallel harness boots can generate false negatives by racing the shared server lifecycle
- 2026-03-11: Built the first real Codex self-healing loop around the new engineering-support briefs.
  - Added `memory/codex_repair_store.py` as the single owner for:
    - `data/state/codex_repair_request.json`
    - `data/state/codex_repair_status.json`
    - `data/state/codex_recovery.json`
  - Added `core/codex_bridge.py` to:
    - load the latest escalation payload from `data/debug/codex_escalations.jsonl`
    - write a bounded repair request
    - spawn `scripts/codex_repair_worker.py`
    - poll worker status
    - hand recovery state back to Piper after restart
  - Added `scripts/codex_repair_worker.py`.
    - It calls the local `codex exec` CLI with a JSON schema contract, requires structured verification commands, reruns those commands itself, and only then writes `codex_recovery.json` plus `restart_requested`.
    - It also supports `--simulate fixed|blocked|no_fix` for deterministic testing without invoking the real Codex CLI.
  - Added `scripts/codex_repair_bridge_smoke_test.py`.
    - This validates the end-to-end control plane:
      - escalation log -> repair request
      - worker -> verified result
      - recovery payload -> restart_requested status
      - recovery consume -> resumed status
  - Wired the GUI controller into the bridge.
    - `ui/controller_queue.py` now turns `codex_escalation` events into repair requests.
    - `ui/controller.py` polls the repair status file, auto-restarts Piper when a verified repair is ready, and retries the interrupted user request once after boot.
  - Additional cleanup:
    - made `memory/__init__.py` lazy for `KnowledgeManager` so importing the lightweight repair store no longer drags in `chromadb`
    - harness isolated-data overlays now clear repair-state files to avoid stale self-heal jobs contaminating smokes
    - stale `queued/running/restart_requested` repair status is treated as expired after the repair timeout window instead of blocking all future repair attempts forever
  - Validation:
    - `python3 -m compileall config.py core ui memory harness scripts app.py`
    - `python3 scripts/codex_escalation_smoke_test.py`
    - `python3 scripts/codex_repair_bridge_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts\\file_edit_smoke_test.py --json`
- 2026-03-11: Replaced flat profile prompting with a graph-backed world model. `world_model.json` is now the source of truth for durable personal memory, `knowledge.json` is maintained as a compatibility mirror, tasks/events remain separate operational state, and persona prompt assembly now injects `[WORLD STATE]`, `[OPERATIONAL STATE]`, and `[ENVIRONMENT]` as distinct blocks.
- 2026-03-11: Tightened prompt-time context hygiene after the world-model rollout. `[WORLD STATE]` now suppresses transient/TTL-backed attributes unless the user query is about them, `[OPERATIONAL STATE]` defaults to near-term events and only surfaces distant events when query-relevant, document routing now catches plural-doc/follow-up phrasing more reliably, and persona no longer receives raw `[INGESTED DOCUMENTS]` excerpt dumps outside the focused `[DOCUMENT FOCUS]` path.
- 2026-03-11: Added a dedicated `[SITUATIONAL STATE]` prompt block sourced from active transient/TTL-backed world-model facts. Stable identity stays in `[WORLD STATE]`, while temporary user sentiment or hesitation remains available to persona for tone and planning.
- 2026-03-11: Reduced chat-stream jitter and hardened stop behavior for speech playback.
  - `ui/controller.py` now keeps a cached chat render and updates only the live assistant row during streaming deltas instead of rebuilding the entire transcript on each chunk.
  - `tools/tts.py` now exposes `is_busy()` and tracks synth/play activity, so the UI can keep the Stop button active while speech is still playing after generation finishes.
  - `core/pipeline.py` cancel handling now hard-stops TTS without calling `stream_end()` afterward, which prevents canceled turns from flushing a residual speech tail.
  - `ui/controller_actions.py` now reports `Speech stopped.` when Stop is used only to cut off TTS, and clears the chat-render cache on transcript clear.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile core\\pipeline.py ui\\controller.py ui\\controller_actions.py tools\\tts.py harness\\tts_probe.py`
    - direct pipeline probe confirmed cancel emits `stop` without a trailing `stream_end`
    - direct Dear PyGui probe confirmed the streaming assistant keeps the same widget across deltas
- 2026-03-11: Fixed router/persona context drift for follow-up turns after task execution.
  - Added a single upserted hidden system block `[LATEST_RUNTIME_CONTEXT]` in `memory/chat_state.py` so the latest authoritative runtime outcome can survive into the next route pass without showing in the chat transcript.
  - `core/orchestrator_phases.py` now builds that block from the previous task/search turn, including route type, task goal or search query, compact execution status, a short runtime note, and relevant paths.
  - The secretary/router now receives `[LATEST_RUNTIME_CONTEXT]` as an explicit extra system message, and route normalization also gets the enriched history instead of relying only on assistant narration.
  - `core/prompting.py` now strips `[LATEST_RUNTIME_CONTEXT]` from normal persona/model history, so the fix stays routing-focused instead of polluting conversation context.
  - `data/prompts/secretary.txt` was updated to mention `[DOCUMENT MATCHES]` / `[DOCUMENT FOCUS]` and to treat `[LATEST_RUNTIME_CONTEXT]` as authoritative for corrections, retries, and clarifications.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile memory\\chat_state.py core\\prompting.py core\\orchestrator_phases.py`
    - direct route probe confirmed the router prompt now includes `[LATEST_RUNTIME_CONTEXT]`
    - direct persona-message probe confirmed `[LATEST_RUNTIME_CONTEXT]` is excluded from normal persona history
- 2026-03-11: Reduced repeated file re-reads during FILE_WORK inspection/fix passes.
  - `core/executor.py` now appends exact file-read content into scratchpad immediately after successful single-file `FILE_OP read_text` calls, instead of waiting until stage completion.
  - small `read_many` payloads still qualify when they are narrow enough, but broader multi-file reads are kept compact to avoid blowing up planner context.
  - `core/prompt_builder.py` now gives file inspection/content-edit stages a larger planner scratchpad budget so the newly captured exact file contents remain visible to the planner.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile core\\executor.py core\\prompt_builder.py core\\file_stage_policy.py`
    - direct executor probe confirmed a `read_text` result produces `FILE_READ_EXACT_PATH` / `FILE_READ_EXACT_CONTENT` in scratchpad immediately
    - direct planner-prompt probe confirmed that exact content survives into the planner prompt
- 2026-03-11: Fixed the follow-up repair loop that appeared while retrying code-file edits such as `catch_the_stars.py`.
  - `core/orchestrator_phases.py` no longer sends `[LATEST_RUNTIME_CONTEXT]` as a second `system` role message to the secretary. It is now merged into the single leading secretary system prompt so Qwen/llama.cpp chat templates stop throwing `System message must be at the beginning`.
  - `core/orchestrator_phases.py` also suppresses `[ROUTER]` loopback if the latest secretary pass itself errored, which prevents a failed route from spinning persona back into ROUTE over and over.
  - `core/file_stage_policy.py`, `core/prompt_builder.py`, `data/prompts/manager.txt`, and `tools/registry.py` now steer existing code-file edit stages toward `RUN_CODE` after inspection instead of encouraging giant `FILE_OP write_text` JSON payloads.
  - `core/executor.py` now blocks fragile `FILE_OP write_text` attempts for inspected code-file edit stages and emits an explicit system error telling the planner to use `RUN_CODE` to read-modify-write the file.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile core\\orchestrator_phases.py core\\file_stage_policy.py core\\executor.py core\\prompt_builder.py tools\\registry.py`
    - direct secretary-system probe confirmed `[LATEST_RUNTIME_CONTEXT]` is merged once into a single system prompt
    - direct file-policy and executor probes confirmed `catch_the_stars.py` now produces a `RUN_CODE` hint and blocks code-file `FILE_OP write_text`
- 2026-03-11: Made `Code` tab changed-file previews less misleading.
  - `tools/workspace_runtime.py` now captures up to 6000 characters for changed-file text previews instead of 800 and marks genuinely clipped previews as `PREVIEW TRUNCATED`.
  - `core/executor.py` now labels that surface as a preview, and when clipping does occur it explicitly says the file on disk is longer.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile tools\\workspace_runtime.py core\\executor.py`
    - direct workspace-runtime probe confirmed `catch_the_stars.py` now shows its full 4517-character content with `truncated=False`
- 2026-03-11: Added a direct `Run File` control to the `Code` tab.
  - `ui/layout.py`, `ui/controller.py`, `ui/controller_actions.py`, and `ui/controller_queue.py` now expose a `Run File` button that launches the first visible `.py` path from the current code preview into the embedded code session.
  - The button is only enabled when Piper is boot-ready, there is no active operation/session, and the current preview actually shows a runnable script path.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile ui\\layout.py ui\\controller.py ui\\controller_actions.py ui\\controller_queue.py`
- 2026-03-11: Stopped interactive script verification loops from relaunching games repeatedly.
  - `core/route_normalizer.py` now rewrites `launch the game, then verify controls` cards into a `FILE_WORK` launch stage followed by a `CHAT` stage that asks the user to test the already-running app and report what happened.
  - `core/file_stage_policy.py` now recognizes interactive runtime verification stages separately from normal file verification stages.
  - `core/executor.py` and `core/orchestrator_phases.py` now support a user-input pause mode so that, if an interactive verification stage still reaches the executor, Piper pauses after the first successful launch and asks for user feedback instead of relaunching the script in a loop.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile core\\route_normalizer.py core\\file_stage_policy.py core\\executor.py core\\orchestrator_phases.py`
    - direct route-normalizer probe confirmed the second stage becomes `CHAT` for the `catch_the_stars.py` run-and-verify card shape
- 2026-03-11: Added a non-blocking engineering boot probe and cleaned the Codex repair worker path.
  - `llm/boot.py` now supports `background_boot_tasks`, and `app.py` uses that to run a Codex health probe while the server is booting.
  - `core/codex_bridge.py` now owns both `resolve_codex_executable()` and `probe_codex_support()`, so executable discovery is shared between the boot probe and repair worker.
  - `scripts/codex_repair_worker.py` now uses the shared resolver, keeps UTF-8 subprocess handling, and no longer depends on the removed local `_resolve_codex_executable()` helper.
  - Validation:
    - `python3 -m compileall config.py core/codex_bridge.py scripts/codex_repair_worker.py llm/boot.py app.py scripts/codex_boot_probe_smoke_test.py`
    - `python3 scripts/codex_boot_probe_smoke_test.py`
    - `python3 scripts/codex_repair_bridge_smoke_test.py`
- 2026-03-11: Fixed another Windows-only Codex repair worker encoding failure.
  - The worker could still die after starting `codex.exe` because printing captured Codex output through the worker's default `sys.stdout` used the local Windows `charmap` encoding.
  - `scripts/codex_repair_worker.py` now reconfigures `stdout`/`stderr` to UTF-8 when possible and routes captured Codex output through a byte-safe helper.
  - Validation:
    - `python3 -m compileall scripts/codex_repair_worker.py`
    - `python3 scripts/codex_repair_bridge_smoke_test.py`
    - direct probe with a strict `cp1252`-encoded `sys.stdout` confirmed `_emit_stdout('hello 🌐 world')` succeeds
- 2026-03-11: Added end-to-end UI coverage for the engineering self-heal loop.
  - `scripts/codex_ui_repair_smoke_test.py` exercises `PiperController.queue_codex_repair()`, `poll_codex_repair()`, and `resume_codex_recovery_if_needed()` against a real simulated repair worker.
  - It verifies the controller logs engineering status lines, requests restart when repair reaches `restart_requested`, and resubmits the interrupted user message after consuming recovery.
  - Operational note: this smoke must run under `.venv\\Scripts\\python.exe` because `ui.controller` imports `dearpygui`.
- 2026-03-11: Switched Windows engineering support to prefer the WSL Codex backend.
  - The real blocked repair log for `catch_the_stars.py` showed `codex.exe` could start but its local tool/shell invocations were failing with `%1 is not a valid Win32 application`.
  - `config.py` now resolves both the native Windows Codex path and the sibling WSL/Linux Codex path from the VS Code extension install, with `CODEX_PREFER_WSL` defaulting on for Windows.
  - `core/codex_bridge.py` now builds launch commands instead of assuming a single native executable, translating `--cd`, schema, and output paths into `/mnt/...` form when the WSL backend is used.
  - `scripts/codex_repair_worker.py` now uses the shared launch-command builder, so real repair jobs follow the same WSL-backed path as the boot probe.
  - Validation:
    - `.venv\\Scripts\\python.exe` confirmed `launch_prefix = ['...wsl.exe', '-e', '.../linux-x86_64/codex']`
    - real `probe_codex_support()` under the Windows runtime still returned `Engineering channel: ONLINE`
    - direct real Codex task via the new launch path successfully ran `/bin/bash -c pwd` in `/mnt/c/Projects/Piper`
- 2026-03-11: Restored the repeated-read planner loop rail for code/text edit stages.
  - `core/executor.py` had the `repeated_content_read` guard accidentally nested under the `list_tree` repeat branch after an unconditional `return`, so unchanged `read_text` retries in edit stages never emitted the intended `planner_repeat` signal or the stronger `RUN_CODE` correction rail.
  - Validation:
    - `.\\.venv\\Scripts\\python.exe -m py_compile core\\executor.py`
    - direct executor probe confirmed a repeated unchanged `read_text` on `catch_the_stars.py` now emits `planner_repeat` and appends the code-file `RUN_CODE` hint
    - `.\\.venv\\Scripts\\python.exe scripts\\file_edit_smoke_test.py --json`
- 2026-03-11: Hardened diagnosis-only `FILE_WORK` turns after the `catch_the_stars.py` raw-log loop.
  - Root causes from the kept raw logs:
    - diagnosis stages like `read and analyze ... identify why ...` were being treated as plain non-mutating inspection and could auto-finish after a single `read_text`
    - diagnosis stages could also trip the exact-read fast path and dump the file verbatim
    - persona was still seeing stale retrieved memory for `catch_the_stars.py`, which could override the just-read file
    - inspector/repeated-completion paths could finish a diagnosis stage without any explicit diagnosis summary in `proposal`
  - Runtime changes:
    - `core/file_stage_policy.py`
      - narrowed mutation detection so `movement` no longer looks like a `move*` mutation verb
      - widened code-edit detection for `modify the code / logic / handlers` phrasing
      - added `stage_requires_analysis_report()` and stopped treating a bare `read_text` as sufficient for diagnosis/report stages
      - narrowed `stage_requires_targeted_read()` so diagnosis/debug turns are no longer treated like `show me the exact contents`
    - `core/orchestrator_phases.py`
      - exact-read and file-lookup auto-replies now only trigger when the latest stage is actually a targeted read/lookup stage
      - persona suppresses retrieved memory/document hits for normal `FILE_WORK` turns so grounded file evidence wins
    - `core/scratchpad_formatter.py`
      - `PROPOSAL:` now outranks raw exact-read blobs when building `LAST_LOG` for FILE_WORK outcomes
    - `core/executor.py`
      - diagnosis/report stages now inject a post-read hint telling planner to put the diagnosis in `proposal`
      - explicit completion, repeated-completion fallback, and inspector finish now reject diagnosis-stage completion unless the current stage contains a `PROPOSAL:` diagnosis summary
  - Added regression surfaces:
    - `scripts/file_stage_policy_smoke_test.py`
      - covers diagnosis, modify, and interactive-runtime-verification stage shapes for `catch_the_stars`
    - `scripts/catch_the_stars_diagnosis_smoke_test.py`
      - isolated harness turn that asks Piper to diagnose the broken script without editing it
      - rejects engineering escalation, verification-block drift, verbatim code dumps, and the stale `IGHT // 2 - 50))` corruption story
  - Validation:
    - `python3 -m compileall core/file_stage_policy.py core/scratchpad_formatter.py core/orchestrator_phases.py core/executor.py scripts/file_stage_policy_smoke_test.py scripts/catch_the_stars_diagnosis_smoke_test.py`
    - `python3 scripts/file_stage_policy_smoke_test.py`
    - `.\\.venv\\Scripts\\python.exe scripts\\catch_the_stars_diagnosis_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-mnh58mic\\data`
- 2026-03-11: Fixed the `LAST_LOG` mismatch that was making persona talk about `50))` after a verified file write.
  - `core/scratchpad_formatter.py` now summarizes `FILE_WORK_VERIFIED_RESULT` from its structured JSON payload instead of taking the last 200 characters of the stored file content blob.
  - That prevents persona from receiving the tail of the rewritten source file as its authoritative outcome note.
  - `scripts/catch_the_stars_diagnosis_smoke_test.py` now closes the isolated harness in a `finally` block so an interrupted diagnosis smoke is less likely to strand a test `llama-server`.
  - Validation:
    - `python3 -m compileall core/scratchpad_formatter.py scripts/catch_the_stars_diagnosis_smoke_test.py`
- 2026-03-11: Added a proactive rail for redundant rereads in code edit stages.
  - Root cause: `read_text` could succeed and store the full exact source in scratchpad, but the planner still sometimes hallucinated that the file read was truncated and spent another step rereading the same unchanged code file before the existing reactive repeat rail kicked in.
  - `core/executor.py` now blocks a repeated `FILE_OP read_text/read_many` on the same unchanged code file before execution when exact current source is already present in scratchpad.
  - `core/prompt_builder.py` now adds an `EXACT_READ_READY` section for content-edit stages so the planner is told explicitly that the exact source is already available and must not be reread just because an observation preview looked truncated.
  - `core/file_stage_policy.py` also now treats `correct/fix/repair` phrasing as code/text mutation language for content-edit stage classification.
  - Added deterministic regression:
    - `scripts/redundant_code_read_guard_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/file_stage_policy.py core/prompt_builder.py core/executor.py scripts/redundant_code_read_guard_smoke_test.py scripts/file_stage_policy_smoke_test.py`
    - `.\\.venv\\Scripts\\python.exe scripts\\redundant_code_read_guard_smoke_test.py`
    - `python3 scripts\\file_stage_policy_smoke_test.py`
- 2026-03-11: Fixed vague code follow-up routing so Piper stops re-searching the workspace when the active script is already known.
  - Root cause: `core/route_normalizer.py` only recovered recent explicit file targets for document-style follow-ups. Vague code tasks like "inspect the input handler" or "fix the controls" were left targetless, so the planner started with `find_paths` / `list_tree` loops instead of reading the already-known script.
  - `core/route_normalizer.py` now normalizes code/game/script follow-up TASK cards onto the latest explicit code file target from recent history when the current card is vague and file-less.
  - The normalizer injects the explicit file into the task goal, FILE_WORK stage goals, and card context, and adds a direct "do not search the workspace for another file unless this read fails" instruction.
  - Added deterministic regression:
    - `scripts/code_target_followup_normalizer_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/route_normalizer.py scripts/code_target_followup_normalizer_smoke_test.py scripts/file_stage_policy_smoke_test.py`
    - `python3 scripts/code_target_followup_normalizer_smoke_test.py`
    - `python3 scripts/file_stage_policy_smoke_test.py`
- 2026-03-11: Reduced code-edit dead loops in `catch_the_stars`-style repair turns.
  - Live logs showed two separate failure modes:
    - older Stage 1 route drift wasted steps on blind `find_paths` / `list_tree` searches for "input" and "movement" instead of using the known script target
    - newer Stage 2 edit turns hit a self-inflicted loop after a blocked `RUN_CODE` edit (`Importing 'sys' is blocked.`), because runtime then hard-blocked a valid `FILE_OP write_text` fallback and the planner fell back into blocked rereads
  - `config.py`
    - added `EXECUTOR_MAX_STEPS` (env: `PIPER_EXECUTOR_MAX_STEPS`), default `12`
  - `core/executor.py`
    - stage step budget now comes from `CFG.EXECUTOR_MAX_STEPS` instead of a hard-coded `10`
    - code-file edit stages now allow a valid `FILE_OP write_text` fallback when rewriting the exact code file already read into scratchpad
    - redundant-read blocker now explicitly says one valid `FILE_OP write_text` is acceptable if the final source is already computed
  - `core/file_stage_policy.py`
    - blocked `sys` / `subprocess` imports during code-edit `RUN_CODE` now emit a specific recovery hint instead of only the generic failure
    - post-read code-edit hint now says to prefer `RUN_CODE`, but allows one valid `FILE_OP write_text` fallback
  - `core/prompt_builder.py`
    - `CODE_EDIT_OVERRIDE` now reflects the same preference/fallback rule so prompt guidance matches runtime behavior
  - Added deterministic regression:
    - `scripts/code_file_write_fallback_smoke_test.py`
  - Validation:
    - `python3 -m compileall config.py core/executor.py core/file_stage_policy.py core/prompt_builder.py scripts/code_file_write_fallback_smoke_test.py scripts/redundant_code_read_guard_smoke_test.py`
    - `.\\.venv\\Scripts\\python.exe scripts\\code_file_write_fallback_smoke_test.py`
    - `.\\.venv\\Scripts\\python.exe scripts\\redundant_code_read_guard_smoke_test.py`
- 2026-03-12: Repo Sweep Hard fixed two FILE_WORK verifier regressions that were breaking isolated CRUD and chaos harness runs.
  - Small CRUD regression:
    - Root cause: `core/file_checker_rules.py` extracted expected current-state text content from all quoted literals in the stage, so a context string like `The workspace root is '.'` could be hashed as the target content instead of `"alpha beta gamma"`.
    - Symptom: a successful `write_text` action verified correctly on the initial tool result but then got downgraded by current-state recovery, causing repeated create-file reroutes in `scripts/file_crud_smoke_test.py`.
    - Fix:
      - quoted text inference now prioritizes stage goal/success text over incidental context
      - non-content literals like `.` / `..` are ignored for text-state inference
      - added `scripts/file_checker_text_content_inference_smoke_test.py`
  - Extension reorg regression:
    - Root cause: current-state synthesis for broad extension reorg stages was allowed to interpret wording like `duplicate identical files` as generic copy semantics, which made `verify_current_file_stage_state()` fabricate a copy mismatch on `.json/misc_data.json` after a successful `consolidate_by_extension`.
    - Symptom: `scripts/file_chaos_test.py` failed after a correct consolidation step, then left empty directories behind because the stage never advanced to `delete_empty_dirs`.
    - Fix:
      - extension reorg stages now skip generic synthetic copy/move current-state inference and fall back to the dedicated extension-inventory verification path
      - generic copy inference now requires an actual copy-style verb instead of matching any `duplicate*` wording
      - `core/executor.py` no longer lets fallback current-state checks downgrade an already `VERIFIED` direct file-checker result; current-state only upgrades/recoveries false negatives now
      - added `scripts/extension_reorg_current_state_verifier_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/file_checker_rules.py core/executor.py scripts/file_checker_text_content_inference_smoke_test.py scripts/extension_reorg_current_state_verifier_smoke_test.py`
    - `python3 scripts/file_checker_text_content_inference_smoke_test.py`
    - `python3 scripts/extension_reorg_current_state_verifier_smoke_test.py`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_crud_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-0fmicem8\\data`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_edit_smoke_test.py --json`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_chaos_test.py --json`
  - Cleanup:
    - confirmed no stray `llama-server`, harness, or repair-worker processes remained after the sweep runs
- 2026-03-13: Added reversible skill-layer v1 behind `CFG.SKILL_LAYER_ENABLED` and cleaned the first routing regressions before leaving it enabled.
  - New pieces:
    - `core/skills/selector.py`
    - `core/skills/__init__.py`
    - `SkillDecision` contract in `core/contracts.py`
    - planner/persona active-skill injection in `core/prompt_builder.py` / `core/orchestrator_phases.py`
  - Regression 1:
    - naming-mismatch follow-up after a successful exact file read was being re-normalized from hidden `[LATEST_RUNTIME_CONTEXT]` text into a bogus subject like `exact contents of 'grocery_list`, then rereading the file
    - fixed by skipping hidden/system messages in recent document-subject extraction and by preferring an explicit-target filename recheck card for document search/naming follow-ups
    - added `scripts/document_lookup_followup_normalizer_smoke_test.py`
  - Regression 2:
    - `file_lookup` skill matching was too greedy and hijacked mutating file turns such as remove-text, copy, and move into lookup-only path searches
    - fixed by restricting `file_lookup` to non-mutating file stages only
    - strengthened `scripts/skill_layer_smoke_test.py` so content edits resolve to `file_edit` and simple path-copy turns stay unskilled
  - Validation:
    - `python3 -m compileall config.py core scripts`
    - `python3 scripts/document_lookup_followup_normalizer_smoke_test.py`
    - `python3 scripts/skill_layer_smoke_test.py`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_lookup_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-u9nb532c\\data`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_edit_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-fadajaj_\\data`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_crud_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-orngpmm8\\data`
  - Cleanup:
    - confirmed no stray `llama-server`, harness, or repair-worker processes remained after the skill-layer runs
- 2026-03-13: Fixed the `Read it back.` document follow-up bug in the route normalizer.
  - Root cause:
    - `_extract_document_lookup_subject()` was treating phrases like `read it back` as a literal document subject `it back`
    - because that current-turn subject was non-empty, routing never fell back to the real prior target/subject from history
  - Fix:
    - pronoun follow-up cleanup now strips trailing discourse tails like `back`, `again`, `for me`, `to me`, `out loud`, `aloud` when the subject begins with `it/this/that`
    - pronoun lookup blacklist now explicitly covers `it back` / `it again` / `this back` / `that back`
    - extended `scripts/document_lookup_followup_normalizer_smoke_test.py` to cover the no-explicit-path `Read it back.` case
    - extended `scripts/file_lookup_smoke_test.py` with a real `read_it_back` turn
  - Validation:
    - `python3 -m compileall core/route_normalizer.py scripts/document_lookup_followup_normalizer_smoke_test.py scripts/file_lookup_smoke_test.py`
    - `python3 scripts/document_lookup_followup_normalizer_smoke_test.py`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_lookup_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-uazaqihz\\data`
- 2026-03-13: Fixed the compound remove-then-read parser gap for direct FILE_WORK text-edit requests.
  - Failure shape from live logs:
    - `Remove bread from the grocery list and then read it back.` either fell through direct normalization entirely or captured the whole subject as `grocery list and then read it back`
    - older live builds then drifted into the old `it back` lookup bug and answered `No matching files found.`
    - the follow-up `Again` reply was wrong persona narration, and loopback correctly caught that repeated contradiction
  - Fix:
    - `DIRECT_FILE_REMOVE_TEXT_RE` now accepts unquoted removal text, not just quoted needles
    - `_split_file_followup_tail()` strips compound tails like `and then read it back`
    - remove-text card builders now optionally emit a second FILE_WORK stage to read the updated file back
    - added `scripts/file_edit_compound_followup_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/route_patterns.py core/route_normalizer.py scripts/file_edit_compound_followup_smoke_test.py`
    - direct route probe for:
      - `Remove bread from the grocery list and then read it back.`
      - `Remove 'bread' from the grocery list and then read it back.`
    - `.\\.venv\\Scripts\\python.exe scripts\\file_edit_compound_followup_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-ybjhjtyw\\data`
  - Cleanup:
    - confirmed the focused harness shut its local `llama-server` down cleanly after the run
- 2026-03-13: Created a lightweight source snapshot under `versions/piper_v0/` before the planned v1 engine/context-pack refactor pass.
  - Intent:
    - keep the live root as `Piper v1`
    - preserve a rollback/reference baseline without cloning heavy runtime assets
  - Included:
    - root code/config/docs
    - `core/`, `ui/`, `memory/`, `llm/`, `tools/`, `harness/`, `scripts/`, `notes/`
    - `data/prompts/`, `data/styles/`, `data/templates/`
  - Excluded:
    - `models/`, `runtime/`, `.venv*`, caches, and live `data/state|debug|workspace|vector_store|benchmarks|harness|reference`
- 2026-03-13: Added `PIPER_V1_ENGINE_BLUEPRINT.md` as the active source-of-truth for the v1 redesign.
  - Purpose:
    - preserve the original philosophy of the redesign
    - define the target stack
    - define migration workflow
    - define anti-sidetrack rules
    - keep `piper_v0` as rollback/reference while the live root becomes `v1`
- 2026-03-13: Added the v1 docs/checklists hub under `docs/`.
  - New files:
    - `docs/README.md`
    - `docs/V1_EXECUTION_ROADMAP.md`
    - `docs/checklists/V1_GUARDRAILS.md`
    - `docs/checklists/TRIAGE_MAP.md`
    - `docs/checklists/RELEASE_READINESS.md`
  - Purpose:
    - keep architecture work tied to a staged roadmap
    - give a fast "what file do I inspect first?" map
    - define a release/readiness checklist for real-use confidence
    - reduce drift by turning repeated architectural discipline into explicit checklists
  - Follow-up verification:
    - reviewed the older live `data/debug/llm_prompt_debug.txt` failure for `Remove bread from the grocery list and then read it back.`
    - that logged run did not actually remove `bread`; it routed into a bogus "locate or create grocery list containing bread" stage, wrote `bread\\nmilk\\neggs\\n`, then stage 2 only read that pre-removal state
    - reran the focused smoke on current code:
      - `.\\.venv\\Scripts\\python.exe scripts\\file_edit_compound_followup_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-paf4amdo\\data`
    - confirmed no stray `llama-server` remained after the rerun
- 2026-03-13: Started the real v1 extraction with `ContextPackEngine` as the first shared engine seam.
  - Wiring changes:
    - `core/engines/context_pack.py` is now the single owner for persona pack assembly and hidden runtime-context message rendering
    - `core/prompt_context.py` was reduced to a facade that delegates to `ContextPackEngine`
    - `core/orchestrator_phases.py` `phase_persona(...)` now works through explicit persona packs and pack overrides instead of mutating `PromptContext` via ad hoc `replace(...)` calls
    - `_build_latest_runtime_context_message(...)` now delegates to the shared context-pack service instead of assembling its own packet inline
  - Docs:
    - updated `docs/architecture/ARCHITECTURE.md` to name `ContextPackEngine` as the context assembly boundary
    - updated `docs/v1/EXECUTION_ROADMAP.md` Phase 1 status to `in progress`
  - Validation:
    - `python3 -m compileall core/engines/context_pack.py core/prompt_context.py core/orchestrator_phases.py scripts/context_pack_engine_smoke_test.py scripts/vision_session_memory_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/vision_session_memory_smoke_test.py`
  - Follow-up fix during validation:
    - `PromptContextService` now imports heavy memory modules only under `TYPE_CHECKING`, so lightweight context-pack smokes no longer fail by trying to import `chromadb`
- 2026-03-13: Continued Phase 1 by extracting scratchpad-to-persona carry-forward state into a typed runtime pack.
  - New contract:
    - `PersonaRuntimePack` in `core/contracts.py`
  - New ownership:
    - `core/engines/context_pack.py` now owns extraction of:
      - exact file read answers
      - file lookup answers
      - verified file-work summaries
      - latest stage proposal answers
      - persona outcome block / failed / paused flags
      - FILE_WORK report-rule gating
    - `core/orchestrator_phases.py` `phase_persona(...)` now consumes `orc.prompt_context.build_persona_runtime_pack(...)` instead of parsing scratchpad fragments directly
    - the manager success-recovery check for analysis-report stages now calls the shared prompt-context facade instead of local helper functions
  - Cleanup:
    - removed the old duplicated extraction helpers from `core/orchestrator_phases.py`
    - tightened `needs_file_work_report_rule` to key off the explicit latest stage card instead of requiring `STAGE_TYPE: FILE_WORK` text inside scratchpad
    - upgraded `_extract_latest_runtime_note(...)` in `ContextPackEngine` to keep parity with the richer old inline runtime-note behavior
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/context_pack.py core/prompt_context.py core/orchestrator_phases.py scripts/context_pack_engine_smoke_test.py scripts/vision_session_memory_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/vision_session_memory_smoke_test.py`
- 2026-03-13: Finished Phase 1 context packing by extracting persona directives and direct-answer fast-path policy.
  - New contract:
    - `PersonaDirectivePack` in `core/contracts.py`
  - New ownership:
    - `core/engines/context_pack.py` now builds persona tail-rule blocks (`NO_MUTATION_RULE`, document QA rule, search-report rule, active-skill block, engineering-support rule, FILE_WORK report rule)
    - `core/engines/context_pack.py` now selects direct persona fast-path answers from verified runtime state instead of leaving that selection inline inside `phase_persona(...)`
    - `core/orchestrator_phases.py` now uses `_finish_persona_fast_path(...)` / `_finalize_persona_turn(...)` helpers so the repeated finish/update/upsert sequence is not copied across direct-answer branches
  - Validation and parity:
    - `python3 -m compileall core/contracts.py core/engines/context_pack.py core/prompt_context.py core/orchestrator_phases.py scripts/context_pack_engine_smoke_test.py scripts/vision_session_memory_smoke_test.py scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/vision_session_memory_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts\\file_lookup_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-loewcsc4\\data`
    - `.venv\\Scripts\\python.exe scripts\\file_edit_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-c1o2g96x\\data`
  - Small adjacent fix:
    - updated `scripts/persona_system_event_role_smoke_test.py` to expect `2` messages, not `3`, because placeholder assistant `Thinking...` is intentionally filtered from the final persona prompt history
- 2026-03-13: Cleaned the repo structure before continuing v1 work.
  - Moved the first extracted engine to `core/engines/context_pack.py` so engine code has a single home.
  - Consolidated design/reference docs under:
    - `docs/architecture/`
    - `docs/v1/`
    - `docs/v1/checklists/`
  - Fixed stale doc links and `core/prompt_context.py` imports after the move.
  - Pruned stale `data/harness/` scratch artifacts while keeping the directory contract in place for future harness output ownership.
  - Validation:
    - `python3 -m compileall core/engines/context_pack.py core/prompt_context.py docs/v1 docs/architecture scripts/context_pack_engine_smoke_test.py scripts/vision_session_memory_smoke_test.py scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/vision_session_memory_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts\\file_lookup_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-rsc642ta\\data`
    - `.venv\\Scripts\\python.exe scripts\\file_edit_smoke_test.py --json --keep-data-copy`
      - passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-bhv55grm\\data`
    - final cleanup sweep: no active `llama-server`, harness, or repair-worker processes; no `__pycache__` directories left in the active tree
- 2026-03-13: Fixed a three-layer task/event correction failure chain.
  - Root causes:
    - direct first-person event assertions like `I already got an appointment` were being mistaken for completion commands in `core/route_normalizer.py`
    - persona could leak internal tail markers like `[ACTIVE_SKILL]` because final cleanup only stripped `[ROUTER]` / `[RECALL]`
    - inspector could terminate a stage without any successful tool or proposal evidence, producing false `RESULT: SUCCESS` after an `Event not found` failure
  - Fixes:
    - widened direct event assertion detection in `core/route_patterns.py` and forced those correction turns back to `CHAT` in `core/route_normalizer.py`
    - expanded `_strip_persona_control_tags(...)` in `core/orchestrator_phases.py` to remove internal runtime markers
    - added `_inspector_finish_has_stage_evidence()` in `core/executor.py` and blocked inspector completion when no success/proposal evidence exists
  - Validation:
    - `python3 -m compileall core/orchestrator_phases.py core/route_normalizer.py core/route_patterns.py core/executor.py scripts/task_event_correction_normalizer_smoke_test.py scripts/persona_control_tag_strip_smoke_test.py scripts/inspector_stage_evidence_guard_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts\\persona_control_tag_strip_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts\\inspector_stage_evidence_guard_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
- 2026-03-13: Tightened persona relevance discipline without muting Quinn mode.
  - Problem:
    - persona was over-consuming profile facts, upcoming events, and retrieved memory as if they were material that needed to be used, producing forced relevance chains
  - Fixes:
    - added an explicit `## RELEVANCE DISCIPLINE` section to `data/prompts/instructions.txt`
    - added a `[RELEVANCE DISCIPLINE]` block in `core/prompt_builder.py` whenever persona receives world/situational/operational state or retrieved memory
  - Expected effect:
    - Quinn can stay mean, but should default to one directly relevant contextual fact instead of chaining unrelated profile facts into the same riff
    - upcoming events should be mentioned only when the current turn or runtime outcome makes them genuinely relevant
  - Validation:
    - `python3 -m compileall core/prompt_builder.py scripts/persona_relevance_policy_smoke_test.py scripts/context_pack_engine_smoke_test.py scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/persona_relevance_policy_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
- 2026-03-13: Started v1 redesign Phase 2 with a first extracted `StateMutationEngine`.
  - Why:
    - task/event and memory stages were flattening into generic `SUCCESS` / `MEMORY UPDATED` outcomes, which made persona and runtime context lose the difference between corrections, inspections, real mutations, and not-found failures
    - direct calendar assertions like `Fix your calendar, I already got an appointment.` were still a pressure point because the routing distinction was scattered
  - First seam:
    - added `core/engines/state_mutation.py`
    - task/event follow-up completion classification now flows through `StateMutationEngine.classify_task_event_followup(...)`
    - `ScratchpadFormatter` now asks the engine for `TASK_EVENT_WORK` / `MEMORY_WORK` outcome packs instead of hardcoding generic domain-level result strings
    - `phase_manager(...)` now trusts `effective_success` from the outcome pack, so `Event not found` / `Key not found` style results can no longer be narrated as completed mutation stages
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/state_mutation.py core/scratchpad_formatter.py core/orchestrator_phases.py core/route_normalizer.py scripts/state_mutation_engine_smoke_test.py scripts/task_event_correction_normalizer_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
- 2026-03-13: Fixed reminder-style dated task/event requests that were being preserved as bogus completion cards.
  - Live failure from `llm_prompt_debug.txt`:
    - `Oh yeah, something important. My insurance company told me that my cars insurance will end on the 25th, so remind me to get a new yearly insurance for that.`
    - router/normalizer path preserved a bad `COMPLETE_TASK` card and looped on `Task not found: <entire sentence>`
  - Fixes:
    - added reminder-request normalization in `core/route_normalizer.py` so `remind me to ...` requests override bad task/event completion cards and become `ADD_EVENT` when a date is present, else `ADD_TASK`
    - added bare ordinal-day recognition (`on the 25th`) to `core/route_patterns.py`, `core/route_dates.py`, and `core/agent.py`
  - Validation:
    - `python3 -m compileall core/route_patterns.py core/route_dates.py core/agent.py core/route_normalizer.py scripts/reminder_event_normalizer_smoke_test.py scripts/task_event_correction_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
- 2026-03-13: Repaired direct/vague state-domain flows for knowledge memory and task/event follow-ups.
  - Failures found:
    - `Remember that my favorite drink is coffee.` was being misrouted into `COMPLETE_TASK`
    - `What do you know about my favorite drink?` could fall through to the task/event readonly answer and list events instead
    - vague follow-ups like `I did it.` or `I went to it.` could either stay `CHAT` or complete the literal target `it`
  - Fixes:
    - added explicit knowledge routing in `core/route_normalizer.py` for store/remove/query turns
    - added knowledge patterns in `core/route_patterns.py`
    - added deterministic readonly knowledge answers in `core/prompt_context.py`
    - tightened `OperationalStateService.build_readonly_answer(...)` so generic `what ...` questions do not get hijacked into task/event listings
    - added runtime-context-driven repair for vague `CHAT` follow-ups and for malformed `TASK` completion cards that target pronouns like `it`
  - Validation:
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `.venv\\Scripts\\python.exe -u - <<'PY' ... _task_vague_flow_eval/_event_vague_flow_eval ... PY`
      - `task_flow_vague_completion` passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-9khegcjf\\data`
      - `event_flow_vague_completion` passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-m8qgx_hc\\data`
- 2026-03-13: Continued Phase 2 by moving durable knowledge/world-state ownership under `StateMutationEngine`.
  - Changes:
    - added engine-owned `KnowledgeMutationIntent` and `StateReadonlyPack` contracts in `core/contracts.py`
    - moved knowledge store/remove/query parsing and route-card construction into `core/engines/state_mutation.py`
    - moved readonly state-answer resolution for knowledge vs task/event queries into `StateMutationEngine`, with `core/prompt_context.py` delegating to it
    - upgraded `MEMORY_WORK` outcome packaging so knowledge updates are marked with `state_owner=world_model` and `[WORLD STATE]` inspection output is recognized as a first-class success shape
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/state_mutation.py core/route_normalizer.py core/prompt_context.py scripts/state_mutation_engine_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/knowledge_readonly_smoke_test.py scripts/operational_state_readonly_smoke_test.py scripts/task_event_correction_normalizer_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `.venv\\Scripts\\python.exe -c "... _run_scenario(... knowledge direct/vague ...)"` passed
      - `knowledge_flow_direct` passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-1o1_a_zg\\data`
      - `knowledge_flow_vague_query` passing kept artifact: `C:\\Users\\HAWKGA~1\\AppData\\Local\\Temp\\piper-harness-wjuoppg4\\data`
- 2026-03-13: Fixed retry reconstruction for dated reminder/event requests.
  - Live failure seen in `data/debug/llm_prompt_debug.txt`:
    - initial insurance reminder request was misrouted into `COMPLETE_TASK`
    - follow-up `Try again.` rebuilt a vague retry stage and lost the original explicit dated reminder shape
    - the live app then narrated a bogus `Event scheduled: Car insurance renewal on 2025-12-25`, which did not persist into `data/state/events.json`
  - Fix:
    - added `_normalize_retry_from_latest_runtime_context(...)` in `core/route_normalizer.py`
    - bare retry utterances now reuse the previous explicit dated request from `[LATEST_RUNTIME_CONTEXT]` and pass back through reminder normalization instead of letting the model improvise the event payload
  - Validation:
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
      - direct request and bare `Try again.` both normalize to `ADD_EVENT` on `2026-03-25`
- 2026-03-13: Moved persona runtime/system-event block to the end of the prompt.
  - Change:
    - in `core/prompting.py`, Qwen/single-system persona assembly now emits `[LATEST_RUNTIME_CONTEXT]` as one terminal `system` message after the conversation transcript instead of embedding it inside the first merged system prompt
  - Reason:
    - latest outcome/instruction blocks behave better when they are truly last in the prompt
  - Validation:
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/vision_prompt_hygiene_smoke_test.py`
- 2026-03-13: Fixed readonly operational-state countdown questions being misanswered as full event listings.
  - Live failure seen in `data/debug/llm_prompt_debug.txt`:
    - `Tell me how many days my first upcoming event.` returned the generic `Upcoming events: ...` list
    - clarified follow-up `But that's not what I asked. How many days are left to my first upcoming event?` also fell back to the same list
  - Fix:
    - `core/operational_state_service.py` now detects event-countdown phrasing and renders a dedicated first-event countdown answer
    - readonly question detection no longer requires the query to begin with `what/show/list/tell me`, so clarification preambles still resolve through the readonly state path
  - Validation:
    - `python3 -m compileall core/operational_state_service.py scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
- 2026-03-13: Fixed self-heal boot restart loop caused by persisted `restart_requested` state.
  - Root cause:
    - `app.py` auto-restarts Piper when a repair requests restart and the app is not launched via `start_piper.bat`
    - on the next boot, the UI poller treated the persisted `codex_repair_status.json` state `restart_requested` as a fresh restart trigger before recovery handoff
  - Fix:
    - `ui/controller.py` now suppresses restart replay only when a recovery payload was already present at startup
    - `core/codex_bridge.py` suppresses boot replay for `restart_requested` statuses that already have matching recovery on disk
    - `consume_recovery()` now clears the stale repair request as part of handoff
  - Validation:
    - `python3 -m compileall core/codex_bridge.py ui/controller.py scripts/codex_ui_repair_smoke_test.py`
    - `.venv\\Scripts\\python.exe scripts/codex_ui_repair_smoke_test.py`
  - Cleanup:
    - cleared stale live `data/state/codex_repair_status.json` after confirming the loop source
- 2026-03-14: Re-fixed single-system persona runtime-context ordering and world-state rendered-fact removal.
  - Persona ordering:
    - for Qwen/single-system compatibility, `[LATEST_RUNTIME_CONTEXT]` had drifted back into the first system message, so stage outcomes were rendered above the recent conversation again
    - `core/prompting.py` now emits runtime context as a final assistant compatibility message while keeping the first system message as the only true system message
    - updated `scripts/persona_system_event_role_smoke_test.py` to assert the runtime block sits in the tail message, not the main system body
  - World-state removal:
    - planner naturally copied rendered lines like `works on: Catch the Stars` from `[WORLD STATE]`, but `memory/world_model.py` could only remove canonical keys or entity labels
    - `remove_fact()` now understands rendered `attribute: value` and `relation: label` lines, removes matching root facts or root relations, and prunes orphaned temporary workspace nodes
    - added `scripts/world_model_rendered_fact_removal_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/prompting.py memory/world_model.py scripts/persona_system_event_role_smoke_test.py scripts/world_model_rendered_fact_removal_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/world_model_rendered_fact_removal_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
- 2026-03-14: Added a speculative-request guard so first-person thinking-out-loud lines do not auto-promote into code/file tasks.
  - Root cause:
    - the router/runtime treated user text like `Maybe I should create a fuzzy words code` as an explicit `FILE_WORK` request
    - that produced a code-generation stage and tool attempts even though the user had not actually asked Piper to perform the work
  - Fix:
    - `core/route_normalizer.py` now downgrades speculative first-person task ideas to `CHAT` unless the user explicitly asked Piper to do the work
    - `core/route_patterns.py` now defines reusable speculative-vs-explicit request regexes
    - `data/prompts/secretary.txt` now tells the router to keep `maybe I should...` / `perhaps we should...` style thinking-out-loud lines conversational
    - added `scripts/speculative_task_idea_normalizer_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/route_patterns.py core/route_normalizer.py scripts/speculative_task_idea_normalizer_smoke_test.py`
    - `python3 scripts/speculative_task_idea_normalizer_smoke_test.py`
    - `python3 scripts/code_target_followup_normalizer_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
- 2026-03-14: Split temporary and soft-intent memory out of durable world-model ownership.
  - Architecture:
    - added `situational_state.json` for temporary user state
    - added `intent_state.json` for soft intentions / leanings
    - durable personal truth remains owned by `world_model.json`; `knowledge.json` stays a compatibility mirror
  - Runtime wiring:
    - `memory/transient_state.py` now ingests user turns into situational and intent owners
    - `core/orchestrator_phases.py` records the latest user turn into transient state at route time
    - `core/engines/context_pack.py` and `core/prompt_builder.py` now render `[SITUATIONAL STATE]` and `[INTENT STATE]` from the new owner instead of relying on transient facts living inside the world model
    - `memory/world_model.py` now drains legacy situational root attributes out of `world_model.json` so old temporary facts stop leaking into durable prompt state
  - Validation:
    - `python3 -m compileall app.py config.py core/contracts.py core/engines/context_pack.py core/prompt_context.py core/prompt_builder.py core/orchestrator_phases.py core/environment.py memory/stores.py memory/state_owner.py memory/world_model.py memory/transient_state.py scripts/transient_state_manager_smoke_test.py scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/transient_state_manager_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/world_model_rendered_fact_removal_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/persona_relevance_policy_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
  - Note:
    - `scripts/state_domain_harness_smoke_test.py` under `.venv` was started as an extra integration check but stalled during startup and was killed; it was not counted as proof.
- 2026-03-14: Fixed project/work-state memory removals that were drifting into task/event cleanup.
  - Root cause:
    - memory-removal phrasing like `I'm not really working on that project ... please remove it` was not recognized as durable-memory removal
    - the route then fell through into `TASK_EVENT_WORK`, where Piper tried `LIST_TASKS` / task cleanup instead of `REMOVE_KNOWLEDGE`
  - Fix:
    - `core/engines/state_mutation.py` now recognizes direct project/work-state negation as `remove_knowledge` with rendered facts like `works on: Catch the Stars`
    - `core/route_normalizer.py` now has a contextual fallback that can recover the latest rendered `works on:` fact from recent history for generic `remove it` follow-ups in project/work-state context
    - expanded `scripts/knowledge_route_normalizer_smoke_test.py` and `scripts/state_mutation_engine_smoke_test.py` to cover both direct and contextual project-removal cases
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py core/route_normalizer.py scripts/knowledge_route_normalizer_smoke_test.py scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
    - `python3 scripts/world_model_rendered_fact_removal_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
- 2026-03-14: Restored one-step state-owner reinterpretation when a mutating task/event stage only proves an empty readonly list.
  - Root cause:
    - after the state-engine split, `LIST_TASKS -> No pending tasks.` and `LIST_EVENTS -> No upcoming events.` still counted as successful `TASKS LISTED` / `EVENTS LISTED` outcomes
    - that meant clearly wrong state-owner resolutions never actually failed, so Piper had no reason to attempt a fresh route
  - Fix:
    - `core/engines/state_mutation.py` now marks empty readonly list evidence as `FAILED / INCOMPLETE` when the stage goal is mutating
    - those packs now carry a one-shot auto-reroute flag for likely state-owner mismatches
    - `core/orchestrator_phases.py` now persists the failed runtime context and triggers a single fresh routing pass instead of dropping straight into persona
    - added `scripts/state_owner_reroute_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/state_mutation.py core/orchestrator_phases.py scripts/state_mutation_engine_smoke_test.py scripts/state_owner_reroute_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/state_owner_reroute_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/task_event_correction_normalizer_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/knowledge_readonly_smoke_test.py`
- 2026-03-14: Restored stage-local self-recovery for failed durable-memory removals.
  - Root cause:
    - direct `MEMORY_WORK` remove stages were narrowed to `REMOVE_KNOWLEDGE` only
    - when `REMOVE_KNOWLEDGE` returned `Key not found: ...`, the planner had no inspection tool budget to recover by listing memory and retrying with the exact rendered fact
    - this made the user type a second message (`retry harder`) to unlock `LIST_KNOWLEDGE`, which is the opposite of agentic looping
  - Fix:
    - `core/engines/state_mutation.py` now grants remove-memory stages both `REMOVE_KNOWLEDGE` and `LIST_KNOWLEDGE`
    - `core/executor.py` now appends a specific recovery hint after failed `REMOVE_KNOWLEDGE`: list memory once, retry with the exact rendered key if present, or finish honestly if already absent
    - `core/executor.py` also hints after `LIST_KNOWLEDGE` in remove-memory stages so the planner can close the loop without user intervention
    - added `scripts/memory_remove_recovery_hint_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py core/executor.py scripts/knowledge_route_normalizer_smoke_test.py scripts/memory_remove_recovery_hint_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/memory_remove_recovery_hint_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/state_owner_reroute_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/knowledge_readonly_smoke_test.py`
- 2026-03-14: Restored auto-finish for FILE_WORK edit stages that are already satisfied after inspection.
  - Root cause:
    - edit stages like `remove the exact text "worms"` could read the correct file state and still fail
    - the planner anchored on the truncated `read_many` preview instead of the exact-read scratchpad note, then burned steps on repeated rereads and even a forbidden `RUN_CODE`
    - runtime already had enough evidence to certify `Requested text is already absent`, but only tried current-state verification on completion-like planner decisions or at final recovery
  - Fix:
    - `core/executor.py` now checks current-state verification immediately after a successful `FILE_OP read_text/read_many` in a mutating FILE_WORK stage
    - if the inspected on-disk state already satisfies the requested text/path constraint, the executor now auto-finishes the stage as verified instead of waiting for the planner to discover that
    - added `scripts/file_edit_already_satisfied_read_recovery_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/executor.py scripts/file_edit_already_satisfied_read_recovery_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/file_edit_already_satisfied_read_recovery_smoke_test.py`
    - `python3 scripts/file_checker_text_content_inference_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json --keep-data-copy --timeout 120`
- 2026-03-14: Durable-memory removal now self-recovers from `LIST_KNOWLEDGE`, and broad self-profile queries answer from current state instead of recent chat drift.
  - Root cause:
    - `MEMORY_WORK` remove stages could list world state, confirm the target fact was absent, and still let the planner wander into deleting an unrelated key.
    - broad readonly prompts like `Tell me everything you know about me.` were still vulnerable to recent chat/history bleed, so a just-removed project could get mentioned back to the user.
    - temporary `project:*` nodes with only workspace metadata could survive after `works_on` removal and keep leaking into prompt state.
  - Fix:
    - `core/engines/state_mutation.py` now extracts the remove target from the stage card and recognizes when `LIST_KNOWLEDGE` proves that target is already absent.
    - `core/executor.py` now auto-finishes `MEMORY_WORK` remove stages with `Knowledge already absent: ...` instead of waiting for the planner to improvise another deletion.
    - `memory/world_model.py` now treats nodes with only temporary workspace attributes (`file_name`, `path`, etc.) as orphaned, so they are pruned when the last `works_on` edge is removed.
    - `core/engines/state_mutation.py` now provides a readonly fast path for broad profile-summary questions, built from current world state plus current operational state instead of persona history.
    - `scripts/memory_state_harness_smoke_test.py` now checks the active `works_on` relation instead of grepping raw JSON for any stale project slug.
  - Validation:
    - `python3 -m compileall core/executor.py core/engines/state_mutation.py memory/world_model.py scripts/state_mutation_engine_smoke_test.py scripts/world_model_rendered_fact_removal_smoke_test.py scripts/memory_state_harness_smoke_test.py scripts/knowledge_readonly_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/world_model_rendered_fact_removal_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/memory_remove_recovery_hint_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/memory_state_harness_smoke_test.py --json --timeout 90`
    - `./.venv/Scripts/python.exe scripts/state_domain_harness_smoke_test.py --json`
- 2026-03-14: Stopped first-person activity lines from being hardened into durable world-model facts.
  - Root cause:
    - `Please remember that I'm working on improving piper, which is you.` was not mainly a world-model extractor mistake.
    - The Python-side durable-memory regex matched it first as `remember that <subject> is <value>`, producing:
      - subject: `I'm working on improving piper, which`
      - value: `you`
    - That promoted a transient/current-activity statement into durable memory before the LLM world-model layer even mattered.
  - Fix:
    - `core/engines/state_mutation.py` now treats `remember ...` plus first-person activity/state phrasing as transient context, not durable knowledge.
    - `core/route_normalizer.py` now forces those turns back to `CHAT` instead of letting bad router/task cards survive.
    - `memory/knowledge_fact_rules.py` now rejects suspicious first-person clause keys like `I'm working on ... , which` during world-model scrubbing.
    - Added `scripts/world_model_suspicious_fact_scrub_smoke_test.py`.
    - Cleaned the live repo state by reloading the world-model manager, which scrubbed the malformed durable key from `data/state/world_model.json` and synced `data/state/knowledge.json`.
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py core/route_normalizer.py memory/knowledge_fact_rules.py scripts/knowledge_route_normalizer_smoke_test.py scripts/transient_state_manager_smoke_test.py scripts/world_model_suspicious_fact_scrub_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/transient_state_manager_smoke_test.py`
    - `python3 scripts/world_model_suspicious_fact_scrub_smoke_test.py`
- 2026-03-14: Fixed contextual `remember that fact` turns being misrouted into stale task/event completion.
  - Root cause:
    - `Just remember that fact.` had no contextual memory-follow-up owner, so if the router drifted into `TASK`, the bad task/event card survived unchanged.
    - The exact failure in `data/debug/llm_prompt_debug.txt` was:
      - stage goal: `Mark the task 'Just remember that fact' as completed and archive it`
      - tool loop: `COMPLETE_TASK` -> `Task not found` -> repeated blocked `LIST_TASKS`
    - The prior user statement was also not being recognized strongly enough as transient focus/current-project context.
  - Fix:
    - `core/engines/state_mutation.py` now recognizes contextual `remember that fact/it/this` turns and can resolve the prior user assertion into either:
      - durable knowledge store for simple `my X is Y` facts
      - no durable mutation for transient/current-focus assertions
    - `core/route_normalizer.py` now applies that contextual remember normalization before retry/task-event follow-up logic.
    - `memory/transient_state.py` now captures `my biggest/main/current project/focus/priority is ...` as situational state.
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py core/route_normalizer.py memory/transient_state.py scripts/knowledge_route_normalizer_smoke_test.py scripts/transient_state_manager_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/transient_state_manager_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
- 2026-03-14: Added a bounded LLM refiner for ambiguous memory follow-ups after repeated regex-route failures.
  - Problem:
    - `Just remember that fact.` and `No, I can see it's not. Just remember it.` were still reaching stale `TASK_EVENT_WORK` cards in live logs.
    - Pure phrase normalization was not enough because users can express memory intent in too many ways for rescue regexes to stay reliable.
  - Fix:
    - `core/engines/state_mutation.py` now exposes a narrow LLM classifier for ambiguous memory-style follow-ups.
    - `core/orchestrator_phases.py` applies that classifier right after secretary normalization and before skill selection.
    - It only fires for ambiguous `remember/forget/remove it/that/fact` turns with nearby prior user context.
    - Durable/simple prior facts become `MEMORY_WORK`; transient/current-focus assertions stay `CHAT`.
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py core/orchestrator_phases.py memory/transient_state.py scripts/llm_memory_followup_refiner_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/transient_state_manager_smoke_test.py`
    - `python3 scripts/llm_memory_followup_refiner_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/transient_state_manager_smoke_test.py`
- 2026-03-14: Added a bounded LLM clarification step for ambiguous task routes that should pause and ask the user instead of executing.
  - Problem:
    - The router was still too eager to preserve `TASK` when the user turn was fragmentary or corrective but not concretely actionable.
    - Latest live example in `data/debug/llm_prompt_debug.txt`:
      - user: `A temporary tree.`
      - bad route: `Create a new temporary profile entry or update the existing profile to include the 'tree' information.`
    - This is not a better regex problem; it is a narrow ambiguity-resolution problem.
  - Fix:
    - Added `core/engines/route_clarity.py` with a bounded LLM classifier for ambiguous task-like turns.
    - `core/orchestrator_phases.py` now applies it after route normalization and before skill selection.
    - When the latest turn is too underspecified to execute safely, the route becomes a `TASK` card with a single `CHAT` clarification stage and no tools.
    - Explicit actionable requests like `Create a temporary tree file for me.` still stay on the task path.
  - Validation:
    - `python3 -m compileall core/engines/route_clarity.py core/orchestrator_phases.py scripts/route_clarifier_smoke_test.py scripts/llm_memory_followup_refiner_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/llm_memory_followup_refiner_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
- 2026-03-14: Hardened ambiguous-task clarification with a direct fragment guard and fixed the follow-up regression it introduced.
  - Problem:
    - The first bounded clarifier was still too soft for ultra-short fragments like `A temporary tree.` in live harness runs, which let the model keep a bogus `MEMORY_WORK` route and store it as world state.
    - After adding a hard fragment guard, the guard overfired on real task/event completion follow-ups such as `I bought the milk.` and `I went to it.`, causing clarification pauses instead of completion.
  - Fix:
    - `core/engines/route_clarity.py` now force-clarifies only very short no-action task fragments, and it explicitly exempts completion/progress statements matched by `COMPLETION_HINT_RE`.
    - `core/orchestrator_phases.py` now logs the generic message `Ambiguous task route converted into clarification pause.` because the route may be clarified by the hard guard or the bounded LLM.
    - Added `scripts/ambiguous_task_clarification_harness_smoke_test.py` to cover the exact live failure class with real conversation history.
  - Validation:
    - `python3 -m compileall core/engines/route_clarity.py core/orchestrator_phases.py scripts/route_clarifier_smoke_test.py scripts/ambiguous_task_clarification_harness_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/ambiguous_task_clarification_harness_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/state_domain_harness_smoke_test.py --json`
- 2026-03-14: Fixed the recurring Qwen persona prompt-shape regression and the task-delete follow-up collision.
  - Problem:
    - The Qwen-safe persona path had drifted into duplicating full conversation history: once inside `[CONVERSATION_TRANSCRIPT]` and again as normal chat messages after the system prompt.
    - `Please remove that from the tasks.` was also colliding with two wrong owners:
      - direct file-text removal on subject `tasks`
      - then the bounded LLM memory-followup refiner, which re-overrode a correct task-delete route back into `MEMORY_WORK`
    - The live result was false `knowledge already absent` narration while `tasks.json` still contained `buy milk`.
  - Fix:
    - `core/prompting.py` now uses the Qwen-safe shape `system`, `user`:
      - the first system message contains the transcript and the runtime tail at the bottom
      - only the latest user turn is sent as the actual chat message
      - older assistant/user turns are no longer duplicated after the system message
    - `core/route_normalizer.py` now:
      - blocks `remove ... from the tasks/events/calendar` from falling into knowledge removal
      - blocks subject-based direct file text removal from treating `tasks` / `events` / `calendar` like workspace documents
      - resolves singular `that` task/event deletion from the latest listed state, or asks for clarification if multiple items are visible
    - `core/engines/state_mutation.py` now keeps the LLM memory-followup refiner out of explicit task/event container requests.
    - Added focused regressions:
      - `scripts/task_delete_followup_normalizer_smoke_test.py`
      - `scripts/task_delete_followup_harness_smoke_test.py`
      - updated `scripts/persona_system_event_role_smoke_test.py`
      - updated `scripts/llm_memory_followup_refiner_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/prompting.py core/route_normalizer.py core/engines/state_mutation.py scripts/persona_system_event_role_smoke_test.py scripts/task_delete_followup_normalizer_smoke_test.py scripts/task_delete_followup_harness_smoke_test.py scripts/llm_memory_followup_refiner_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/task_delete_followup_normalizer_smoke_test.py`
    - `python3 scripts/llm_memory_followup_refiner_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/task_delete_followup_harness_smoke_test.py`
- 2026-03-14: Fixed duplicate persona policy blocks and instruction truncation leakage in the live prompt.
  - Problem:
    - The persona prompt was duplicating `[RELEVANCE DISCIPLINE]`: once inside `data/prompts/instructions.txt`, then again from `core/prompt_builder.py` whenever contextual memory/state blocks were present.
    - `core/instructions_loader.py` was also hard-truncating `instructions.txt` at 4000 chars and appending the literal marker `[TRUNCATED instructions.txt]` into the model-visible prompt.
    - The live log for `Done the shopping, remove them all.` showed both artifacts, which made the prompt look patched together and noisier than intended.
  - Fix:
    - `core/prompt_builder.py` now injects the fallback relevance block only if the loaded instructions do not already define `[RELEVANCE DISCIPLINE]`.
    - `core/instructions_loader.py` now uses a higher default cap and trims silently instead of emitting a truncation marker into the prompt.
    - Added focused regressions:
      - `scripts/persona_relevance_policy_smoke_test.py`
      - `scripts/instructions_loader_smoke_test.py`
  - Validation:
    - `python3 -m compileall core/instructions_loader.py core/prompt_builder.py scripts/persona_relevance_policy_smoke_test.py scripts/instructions_loader_smoke_test.py`
    - `python3 scripts/persona_relevance_policy_smoke_test.py`
    - `python3 scripts/instructions_loader_smoke_test.py`
- 2026-03-14: Fixed plural task follow-up routing for `remove them all` and the merged-subject regression.
  - Problem:
    - The live `Done the shopping, remove them all.` flow was still unstable even after the earlier single-target task fix.
    - Without a concrete plural follow-up normalizer, secretary could leave a vague shopping-removal stage in place, and task completion routing could collapse multiple visible tasks into one fake subject such as `by bread; buy milk; buy bread`.
    - The task-list subject parser only split on commas, but persona readbacks often format tasks with semicolons, so plural follow-ups could merge the whole visible list into one bogus task name.
    - `Please remove that from the tasks.` was also still too eager to clarify when a latest runtime target already existed.
  - Fix:
    - `core/route_normalizer.py` now:
      - resolves plural task/event follow-ups like `remove them all` into concrete task/event targets from the latest visible list or latest runtime subject
      - builds one concrete stage per resolved target instead of one vague aggregate stage
      - parses task lists split by commas or semicolons
      - prefers the latest resolved task/event candidate when generic `that/it` delete follow-ups occur
    - Added focused coverage in:
      - `scripts/vague_task_event_followup_normalizer_smoke_test.py`
      - `scripts/shopping_task_cleanup_harness_smoke_test.py`
    - The harness now clears copied task state before running so the scenario is deterministic instead of inheriting repo-local pending tasks.
  - Validation:
    - `python3 -m compileall core/route_normalizer.py scripts/vague_task_event_followup_normalizer_smoke_test.py scripts/shopping_task_cleanup_harness_smoke_test.py`
    - `python3 scripts/task_delete_followup_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/shopping_task_cleanup_harness_smoke_test.py`
- 2026-03-14: Removed the final-user duplication in the Qwen-safe persona prompt shape.
  - Problem:
    - In the single-system/Qwen path, the latest user turn was still included inside `[CONVERSATION_TRANSCRIPT]` and then repeated as the trailing `user` message.
    - That made the model see the most recent user input twice, once in the transcript and once at the very end of the prompt.
  - Fix:
    - `core/prompting.py` now renders the transcript only up to, but not including, the final user turn when a trailing `user` message is present.
    - `scripts/persona_system_event_role_smoke_test.py` now fails unless the latest user turn is absent from the system transcript and present only as the final `user` message.
  - Validation:
    - `python3 -m compileall core/prompting.py scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
- 2026-03-14: Restored Qwen persona compatibility while keeping latest runtime context terminal in transcript order.
  - Problem:
    - Collapsing the Qwen persona path to a single API `system` message made llama-server fail with `No user query found in messages.`
    - The earlier one-message fix was invalid for the current Qwen chat template even though it made the runtime block appear fully terminal.
    - The repo still needed the runtime/system block to feel session-recent instead of old header text.
  - Fix:
    - `core/prompting.py` now uses the Qwen-safe shape:
      - one real API `system` message
      - one real trailing API `user` message
      - transcript inside the system prompt stops before the final real user turn
      - when runtime context exists, the transcript appends:
        - a synthetic `ROLE: user` `[CURRENT_USER_TURN]` marker
        - then a final `ROLE: system` block containing `[MESSAGE_PROTOCOL]` and `[LATEST_RUNTIME_CONTEXT]`
    - This preserves template compatibility, avoids duplicating the actual final user text, and keeps runtime context terminal inside the transcript sequence.
    - `scripts/persona_system_event_role_smoke_test.py` now enforces that exact shape.
  - Validation:
    - `python3 -m compileall core/prompting.py scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `./.venv/Scripts/python.exe scripts/piper_harness.py once "Hello." --json`
- 2026-03-14: Fixed readonly task/event questions being stolen by the broad knowledge-query path.
  - Problem:
    - Prompts like `What's on my to-do list?` were matching the generic durable-memory regex (`what is/what's <subject>`), so persona replied with malformed knowledge-style lines such as `I do not have a stored on my to-do list.` even when `[OPERATIONAL STATE]` contained pending tasks.
    - The readonly fast path in `StateMutationEngine.build_readonly_answer()` was checking knowledge before operational task/event queries.
  - Fix:
    - `core/engines/state_mutation.py` now gives `READONLY_TASK_EVENT_QUERY_RE` precedence over durable-memory lookup inside readonly answer construction.
    - Added regression coverage so both the raw operational service and the prompt-context readonly path answer `What's on my to-do list?` as `Pending tasks: ...`.
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py scripts/knowledge_readonly_smoke_test.py scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
- 2026-03-14: Collapsed active state-route normalization into `StateMutationEngine` so state meaning stops being split across `route_normalizer`.
  - Problem:
    - State semantics were still being decided in too many places:
      - `route_normalizer` handled reminder conversion, task/event completion/delete follow-ups, contextual remember/remove, and retry reconstruction
      - `StateMutationEngine` separately handled readonly answers, mutation intent, and outcome truth
    - That let the same user sentence cross multiple interpreters before action, which is why the repo kept cycling through `Task not found`, `Event not found`, `Key not found`, and wrong-owner readonly answers.
  - Fix:
    - `core/engines/state_mutation.py` now exposes one engine-owned `normalize_route_decision(...)` entry point for active state-domain route rewrites.
    - The engine now owns:
      - durable knowledge store/remove/query route normalization
      - contextual remember/remove follow-ups
      - state-only retry reconstruction from `[LATEST_RUNTIME_CONTEXT]`
      - reminder-to-task/event conversion
      - task/event completion and delete follow-ups
      - plural task/event follow-ups like `remove them all`
      - state-query downgrades such as readonly task/event status questions staying `CHAT`
    - `core/route_normalizer.py` now delegates active state routing to that engine instead of running a separate chain of state rewrite helpers.
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py core/route_normalizer.py scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/task_delete_followup_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
  - Note:
    - Larger Windows harness scripts were unreliable during this pass because repeated full-runtime startups sometimes stalled or crashed before assertions ran. I did not count those unstable runs as proof.
- 2026-03-14: Fixed declarative task/event state contradictions falling through to persona riffing.
  - Problem:
    - Turns like `No tasks or events.` and `There should be events now.` were not treated as readonly/current-state checks because the fast path only recognized question-style queries.
    - That let persona answer from conversational momentum (`No pending tasks.` / recent task completion) even when `[OPERATIONAL STATE]` still contained upcoming events.
  - Fix:
    - `core/operational_state_service.py` now treats assertive/corrective task-event turns as current-state requests when they mention tasks/events and include state-claim language such as `no ...`, `there should be ...`, or `still have ...`.
    - Mixed task+event mentions now render both sides explicitly, e.g. `No pending tasks.` plus `Upcoming events: ...`, instead of silently dropping one side.
    - Added regression coverage for:
      - `No tasks or events.`
      - `There should be events now.`
  - Validation:
    - `python3 -m compileall core/operational_state_service.py scripts/operational_state_readonly_smoke_test.py scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/knowledge_readonly_smoke_test.py`
- 2026-03-14: Fixed natural completion follow-ups binding to the whole sentence instead of the live task/event target.
  - Problem:
    - Completion lines like `Cool, I forgot about those, thank you, but I washed my car already.` were routed into `TASK_EVENT_WORK`, but the completion target was the entire sentence instead of the active event.
    - Because completion cards allowed only `COMPLETE_TASK` or `COMPLETE_EVENT`, a bad first bind then dead-ended into tool-security violations instead of same-domain recovery.
  - Fix:
    - `core/engines/state_mutation.py` now scores recent live task/event candidates against the user’s completion wording and prefers the best matching active target over a suspicious raw sentence subject.
    - Runtime follow-up subject extraction is now domain-aware, so an `EVENT SCHEDULED` runtime block does not leak a fake task candidate.
    - Completion stages now allow one same-domain list tool (`LIST_TASKS` or `LIST_EVENTS`) for bounded recovery after a miss.
  - Validation:
    - `python3 -m compileall core/engines/state_mutation.py scripts/state_mutation_engine_smoke_test.py scripts/vague_task_event_followup_normalizer_smoke_test.py scripts/task_event_correction_normalizer_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
  - Note:
    - `scripts/state_domain_harness_smoke_test.py --json` under `.venv` stalled during full runtime startup in this pass, so I did not count that harness run as proof.
- 2026-03-14: Fixed proposal confirmations like `Yes, please.` being eaten by the ambiguous-task clarifier.
  - Problem:
    - After Piper proposed `Shall I schedule this activity for tomorrow instead?`, a clear assent like `Yes, please.` was still converted into a clarification pause.
    - The generic route clarifier only saw a short fragment with no action verb and ignored the assistant's immediately previous proposal.
  - Fix:
    - `core/engines/route_clarity.py` now detects affirmative confirmation of Piper's own scheduling proposal before the generic clarification path runs.
    - It reconstructs a concrete `ADD_EVENT` card from the assistant proposal plus recent runtime/user context.
  - Validation:
    - `python3 -m compileall core/engines/route_clarity.py scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/task_event_correction_normalizer_smoke_test.py`
- 2026-03-14: Replaced scattered vague follow-up guessing with an engine-owned LLM follow-up resolver.
  - Problem:
    - Repeated bugs like `Well, remove it.`, `I've already done it, you may remove it.`, `Any tasks left?`, and `Just remember that fact.` kept resurfacing because router heuristics, state mutation normalization, the older memory follow-up refiner, and persona fallback were all partially interpreting the same vague follow-up.
    - That let the system narrate task removal even when the live task still existed, or bounce a task follow-up into memory removal.
  - Fix:
    - Added `core/engines/followup_resolution.py` with a bounded LLM classifier that sees:
      - the latest user turn
      - recent session history
      - `[LATEST_RUNTIME_CONTEXT]`
      - current task/event snapshot
      - current memory summary
    - The resolver returns strict JSON and is now called directly from `phase_route` before the generic ambiguous-task clarifier.
    - It now owns ambiguous follow-up resolution for:
      - task delete / complete
      - event delete / complete
      - readonly task/event queries
      - explicit memory store / remove follow-ups
      - acknowledgement-only turns that should remain chat
    - `phase_persona` readonly fast-path now uses canonical `card.query`, so resolver-produced readonly decisions stay deterministic instead of drifting into persona.
    - Removed the unused orchestrator-level memory-followup helper once the new resolver path was in place.
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/followup_resolution.py core/orchestrator_phases.py scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/llm_memory_followup_refiner_smoke_test.py`
- 2026-03-14: Added a deterministic state override inside `FollowupResolutionEngine` for obvious pronoun follow-ups when the LLM still preserves a bad memory route.
  - Problem:
    - Live logs still showed `How many tasks do I have?` -> `Pending tasks: buy milk.` -> `Remove it.` becoming `MEMORY_WORK` with goal `Remove the user fact 'it' from memory`, then ending as `WORLD STATE LISTED`.
    - That proved the new resolver alone was still sometimes deferring to the already-bad memory card or a weak LLM answer.
  - Fix:
    - `core/engines/followup_resolution.py` now builds a deterministic fallback from:
      - the latest visible task/event answer in recent history
      - current operational snapshot
      - the user’s remove/complete intent
    - If the LLM returns no usable override, or keeps a generic `MEMORY_WORK` pronoun route like `remove the fact 'it'`, Python now snaps it to the obvious live task/event target instead.
    - Explicit memory scope (`memory`, `knowledge`, `world state`) still bypasses this override.
  - Validation:
    - `python3 -m compileall core/engines/followup_resolution.py scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/llm_memory_followup_refiner_smoke_test.py`
- 2026-03-14: Paused clarification stages now answer with the proposal text directly instead of letting persona hallucinate success.
  - Problem:
    - In the same `What tasks do I have?` -> `Remove it.` live transaction, once the system fell into a clarification pause, persona still produced a false success line like `the memory entry is now cleared` even though the stage outcome was `PAUSED / AWAITING USER INPUT`.
    - That poisoned the next follow-up because the visible assistant narration no longer matched the actual stage result.
  - Fix:
    - `PersonaRuntimePack` now carries `proposal_answer`.
    - `ContextPackEngine.build_persona_runtime_pack()` always extracts the latest `PROPOSAL:` text.
    - `ContextPackEngine.build_persona_directive_pack()` now uses that proposal as a deterministic `direct_answer` whenever the outcome is paused.
    - This forces clarification/user-input pauses to ask the exact missing-detail question instead of leaving it to the model.
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/context_pack.py scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
- 2026-03-14: Removed the obsolete memory-followup refiner path and added a lightweight persona output sanitizer.
  - Problem:
    - The old LLM memory-followup refiner logic still existed inside `StateMutationEngine` even after `FollowupResolutionEngine` became the active owner for ambiguous follow-ups, which kept the code path duplicated and misleading.
    - Persona wording still leaked low-value phrasing after otherwise-correct execution, including `upcoming tasks` for event removals, generic `Would you like...` closings, and stray operational/schedule paragraphs on casual chat turns like `I like cars.`
    - The first sanitizer smoke imported `core/orchestrator_phases.py`, which dragged in the full runtime and failed under plain `python3` because `psutil` is only present in the Windows runtime environment.
  - Fix:
    - Deleted the dead LLM memory-followup helper methods from `core/engines/state_mutation.py`; ambiguous follow-up resolution now belongs to `core/engines/followup_resolution.py`.
    - Added `core/persona_output.py` with a small deterministic sanitizer and moved `phase_persona` to call it after control-tag stripping.
    - The sanitizer now:
      - rewrites `upcoming tasks` -> `upcoming events`
      - strips forbidden trailing follow-up questions like `Would you like...`
      - removes the `systems indicate no further mutations were required` sentence
      - drops extra operational/schedule paragraphs from casual no-mutation chat replies when they are unrelated to the user turn
    - `scripts/persona_output_sanitizer_smoke_test.py` now imports the lightweight module directly, so it runs in the plain repo test env.
  - Validation:
    - `python3 -m compileall core/persona_output.py core/engines/state_mutation.py core/orchestrator_phases.py core/contracts.py core/engines/context_pack.py scripts/context_pack_engine_smoke_test.py scripts/followup_resolution_engine_smoke_test.py scripts/persona_output_sanitizer_smoke_test.py`
    - `python3 scripts/persona_output_sanitizer_smoke_test.py`
    - `python3 scripts/context_pack_engine_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
- 2026-03-14: Pruned the dead state-routing block from `core/route_normalizer.py`.
  - Problem:
    - `route_normalizer` still contained an older mini-engine for knowledge/task/event follow-ups, retries, reminders, and completion/delete routing even though active ownership had already moved into `StateMutationEngine` plus `FollowupResolutionEngine`.
    - That code was not on the live path anymore, but it duplicated logic and made future fixes riskier because the file still looked like it owned state semantics.
  - Fix:
    - Removed the obsolete route-normalizer helpers for:
      - knowledge follow-up routing
      - contextual remember/remove and retry replay
      - task/event delete/plural follow-ups
      - task/event completion/status/correction/reminder collapse
    - Trimmed the now-unused imports and regex constants at the top of `core/route_normalizer.py`.
    - Left the non-state route helpers in place: direct file work, workspace document lookup, code-target follow-up, and interactive runtime verification.
  - Validation:
    - `python3 -m compileall core/route_normalizer.py core/engines/followup_resolution.py core/engines/state_mutation.py scripts/followup_resolution_engine_smoke_test.py scripts/state_mutation_engine_smoke_test.py scripts/operational_state_readonly_smoke_test.py scripts/route_clarifier_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/reminder_event_normalizer_smoke_test.py scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
- 2026-03-14: Consolidated shared runtime-context history parsing into `core/runtime_context.py`.
  - Problem:
    - `extract_latest_runtime_context_fields()` existed three times across `FollowupResolutionEngine`, `StateMutationEngine`, and `RouteClarifier`.
    - `extract_previous_user_message()` existed twice with slightly different local copies.
    - This was not large dead code like the old route-normalizer block, but it was still duplicated infrastructure in exactly the engines now carrying the v1 state/follow-up ownership.
  - Fix:
    - Added `core/runtime_context.py` with:
      - `extract_latest_runtime_context_fields()`
      - `extract_previous_user_message()`
    - Rewired:
      - `core/engines/followup_resolution.py`
      - `core/engines/state_mutation.py`
      - `core/engines/route_clarity.py`
    - Removed the local copies and the per-file runtime-context regexes.
  - Validation:
    - `python3 -m compileall core/runtime_context.py core/engines/route_clarity.py core/engines/followup_resolution.py core/engines/state_mutation.py scripts/followup_resolution_engine_smoke_test.py scripts/state_mutation_engine_smoke_test.py scripts/operational_state_readonly_smoke_test.py scripts/route_clarifier_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/reminder_event_normalizer_smoke_test.py scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
- 2026-03-14: Consolidated task/event visible-target extraction into `core/task_event_context.py`.
  - Problem:
    - After the larger state-route cleanup, the remaining overlap was still real:
      - `FollowupResolutionEngine` had its own `extract_recent_visible_targets()` / task/event list parsing
      - `StateMutationEngine` had its own runtime follow-up subject extraction, visible-list parsing, and latest candidate extraction
    - That duplication sat directly inside the active state/follow-up ownership seam.
  - Fix:
    - Added `core/task_event_context.py` with shared helpers for:
      - parsing listed tasks/events from rendered text
      - extracting recent visible task/event targets from history
      - extracting runtime follow-up subjects from `[LATEST_RUNTIME_CONTEXT]`
      - extracting latest task/event candidates from recent history
    - Rewired `core/engines/followup_resolution.py` and `core/engines/state_mutation.py` to use the shared module.
    - Removed the local copies from both engines.
  - Validation:
    - `python3 -m compileall core/task_event_context.py core/engines/followup_resolution.py core/engines/state_mutation.py scripts/followup_resolution_engine_smoke_test.py scripts/state_mutation_engine_smoke_test.py scripts/operational_state_readonly_smoke_test.py scripts/route_clarifier_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/reminder_event_normalizer_smoke_test.py scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
- 2026-03-14: Heavy repo sweep after Phase 2 state/follow-up consolidation.
  - Removed:
    - Repo-side `__pycache__/` directories.
    - Repo-side `*.pyc` artifacts.
  - Kept on purpose:
    - `data/state/*.json.bak` companions, because `memory/stores.py` uses them as part of the JSON store recovery path rather than as disposable clutter.
    - Intentional empty harness directories under `data/harness/`.
  - Validation:
    - `python3 -m compileall core/runtime_context.py core/task_event_context.py core/engines/route_clarity.py core/engines/followup_resolution.py core/engines/state_mutation.py scripts/followup_resolution_engine_smoke_test.py scripts/state_mutation_engine_smoke_test.py scripts/operational_state_readonly_smoke_test.py scripts/route_clarifier_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/reminder_event_normalizer_smoke_test.py scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
- 2026-03-14: Re-asserted separation-of-concerns as an active repo-wide cleanup rule, not just a doctrine statement.
  - Added explicit v1 guardrails to keep:
    - state meaning/classification
    - mutation/execution
    - verification
    - prompt/rendering
    from collapsing back into mixed owners.
  - Future cleanup passes should treat duplicated semantic interpretation and shared-state multi-ownership as regressions, not merely style issues.
- 2026-03-14: Froze the `Piper v1` target around a fixed six-engine set.
  - `docs/v1/BLUEPRINT.md` and `docs/v1/EXECUTION_ROADMAP.md` now treat these as the only v1 engine targets unless the blueprint is revised first:
    - `ContextPackEngine`
    - `StateResolutionEngine`
    - `StateMutationEngine`
    - `VerificationEngine`
    - `FileWorkEngine`
    - `SummaryEngine`
  - Retrieval, patching, loop mechanics, and speech shaping are now documented as subordinate responsibilities for v1 rather than standalone engine targets.
  - This freeze exists to stop future sessions from discovering new architecture mid-bugfix.
- 2026-03-14: Added the planner boundary explicitly to the v1 docs.
  - `docs/v1/BLUEPRINT.md`, `docs/v1/EXECUTION_ROADMAP.md`, and `docs/v1/checklists/V1_GUARDRAILS.md` now document planner/executor separation as a contract:
    - route/workflow decides the job class
    - planner decides the next step inside the allowed stage
    - planner may not rewrite domains, invent success, or bypass verification
- 2026-03-14: Broad repo hygiene audit after the v1 freeze.
  - Removed clearly dead artifacts:
    - `data/prompts/tools.txt`
    - dead helper `build_profile_extraction_prompt()` from `memory/knowledge_prompts.py`
    - dead helper `render_chat_transcript()` from `ui/controller_render.py`
    - dead helper `render_domain_guide()` from `tools/registry.py`
    - dead unused model-message path from `core/prompting.py`:
      - `PromptBuildConfig`
      - `build_model_messages()`
      - `_inject_bootstrap_as_recent()`
  - Kept intentionally:
    - standalone `scripts/*.py` harness/utility entry points, because the audit found many are unreferenced as imports but still serve as direct manual/regression entry points
    - state-store `.json.bak` recovery companions under `data/state/`
  - Audit result:
    - no new active duplicate shared-state owners were found outside the intended runtime owners plus harness/test code
    - the only clearly dead prompt artifact in active v1 was `data/prompts/tools.txt`
  - Validation:
    - `python3 -m compileall core/prompting.py memory/knowledge_prompts.py ui/controller_render.py tools/registry.py scripts/persona_system_event_role_smoke_test.py scripts/persona_output_sanitizer_smoke_test.py scripts/followup_resolution_engine_smoke_test.py scripts/state_mutation_engine_smoke_test.py scripts/operational_state_readonly_smoke_test.py scripts/route_clarifier_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/reminder_event_normalizer_smoke_test.py scripts/vague_task_event_followup_normalizer_smoke_test.py scripts/instructions_loader_smoke_test.py`
    - `python3 scripts/persona_system_event_role_smoke_test.py`
    - `python3 scripts/persona_output_sanitizer_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/operational_state_readonly_smoke_test.py`
    - `python3 scripts/route_clarifier_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/instructions_loader_smoke_test.py`
  - Harness note:
    - `scripts/memory_state_harness_smoke_test.py --json --timeout 90` failed because the local llama server crashed during boot (`FATAL: Server crashed with code 15`)
    - `scripts/state_domain_harness_smoke_test.py --json` stalled during runtime startup and was not counted as proof
- 2026-03-14: Continued engine `#3` (`StateMutationEngine`) by making mutation stages carry structured mutation contracts.
  - Problem:
    - state mutation ownership was still too implicit because task/event/memory stages expressed the operation mostly through natural-language `stage_goal` / `success_condition` strings
    - `FollowupResolutionEngine` also still rebuilt its own task/event/memory mutation cards, which duplicated mutation-stage shape outside engine `#3`
  - Fix:
    - added structured `mutation` payloads to state stage cards via `core/contracts.py`
    - `core/engines/state_mutation.py` now stamps task/event/knowledge mutation stages with:
      - `state_owner`
      - `entity_kind`
      - `action`
      - `target`
      - `value` / `scheduled_date` where applicable
    - memory-removal target recovery now reads the structured mutation payload before falling back to English stage-text parsing
    - `core/engines/followup_resolution.py` now delegates task/event/memory mutation-card construction back into `StateMutationEngine`
  - Validation:
    - `python3 -m compileall core/contracts.py core/engines/state_mutation.py core/engines/followup_resolution.py scripts/state_mutation_engine_smoke_test.py scripts/followup_resolution_engine_smoke_test.py scripts/knowledge_route_normalizer_smoke_test.py scripts/reminder_event_normalizer_smoke_test.py scripts/vague_task_event_followup_normalizer_smoke_test.py`
    - `python3 scripts/state_mutation_engine_smoke_test.py`
    - `python3 scripts/followup_resolution_engine_smoke_test.py`
    - `python3 scripts/knowledge_route_normalizer_smoke_test.py`
    - `python3 scripts/reminder_event_normalizer_smoke_test.py`
    - `python3 scripts/vague_task_event_followup_normalizer_smoke_test.py`
- 2026-03-15: Fixed Bug B — `consolidate_by_extension` exclusion violations not caught by `LocalFileOpRuleChecker`.
  - Problem:
    - When a stage success_condition contained an exclusion clause ("except the FCOM", "leave out X", etc.), the file checker had no mechanism to detect that the excluded file had been moved anyway
    - `_check_consolidate_by_extension` only verified that destination folders existed and contained the expected extensions; it did not cross-reference exclusion constraints from stage text
    - This caused Stage 3 to loop 6+ times: planner proposed `[NO_TOOL_PROPOSAL]` thinking work was done, but verifier (correctly) rejected because FCOM had been moved in violation of the success_condition
  - Fix:
    - added `_EXCLUSION_CLAUSE_RE` regex at module level in `core/file_checker_rules.py` — detects exclusion verb phrases ("except", "excluding", "leave out", "skip", "ignore", "omit", "not including") and captures the keyword token using a non-greedy group with lookahead terminators
    - added `_exclusion_patterns_from_stage()` instance method that scans `self.stage_raw_text` and returns lowercase keyword tokens from all exclusion matches
    - `_check_consolidate_by_extension` now calls `_exclusion_patterns_from_stage()` before the workspace scan; if any pattern matches a filename in `created_files`, returns FAILED with the pattern name, violating path, and a hint to use `exclude_files`
  - Key lesson: the initial regex used `[A-Za-z0-9_\-. ]{2,40}` which was too greedy (included spaces); fixed to `(\w[\w.\-]{1,30}?)` with a lookahead boundary list so short tokens like "FCOM" are captured cleanly
  - Validation:
    - `python scripts/consolidate_exclusion_verifier_smoke_test.py` — all 3 cases pass (violation=FAILED, compliant=VERIFIED, no_exclusion=VERIFIED)
    - `python scripts/extension_reorg_current_state_verifier_smoke_test.py` — still VERIFIED (no regression)
    - `python scripts/file_stage_policy_smoke_test.py` — still passing (no regression)
- 2026-03-15: Fixed Bug C — persona asked "should I reroute?" while engineering escalation was already active.
  - Problem:
    - `_build_outcome_block` in `core/engines/context_pack.py` always emitted "you may append [ROUTER] to trigger a fresh routing pass" in the FAILED [INSTRUCTION]
    - When `latest_codex_escalation` was set, `[ENGINEERING_SUPPORT_RULE]` was injected into the runtime context, but the [ROUTER] permission was still visible to the persona
    - The persona saw both rules and hedged ("Should you wish to proceed, I can attempt a fresh routing pass"), which caused the router to fire in the same turn — persona and router collided
  - Fix:
    - threaded `escalation_active: bool = False` from `orchestrator_phases.py` through `PromptContextService.build_persona_runtime_pack` and `ContextPackEngine.build_persona_runtime_pack` to `_build_outcome_block`
    - when `escalation_active=True` and FAILED, the [INSTRUCTION] now reads "Do NOT append [ROUTER]. This turn must end here." instead of offering the router option
  - Validation:
    - `python -m compileall core/engines/context_pack.py core/prompt_context.py core/orchestrator_phases.py` — clean
    - no dedicated smoke test (requires live orchestrator run); verified by code inspection of the guard at `orchestrator_phases.py` line 1226
- 2026-03-15: Extracted FileWorkEngine (Phase 5) — removed file/code evidence-handling mechanics from executor loop.
  - Problem:
    - 9 evidence-handling methods + a 29-entry extension constant in `executor.py` had no business being in the step loop
    - path extraction logic was duplicated across 3 files: `executor._file_result_candidate_paths`, `file_checker._candidate_paths_from_evidence`, `file_stage_policy.tool_result_candidate_paths`
    - code extension set (`CODE_VIEW_EXTENSIONS`) was defined twice: once in `executor.py` (lines 37-65) and once as `_CODE_FILE_EXTENSIONS` in `file_stage_policy.py` (lines 33-61)
    - recovery hint generation lived in `FileStagePolicy.file_checker_recovery_hint` but was only called from executor; no single "evidence engine" owner
  - Fix:
    - created `core/file_extensions.py` — zero-dependency leaf module with the canonical `CODE_FILE_EXTENSIONS` frozenset
    - created `core/engines/file_work.py` — `FileWorkEngine` with 7 public static/class methods:
      - `candidate_paths(tool_result)` — superset path extractor replacing all 3 implementations
      - `exact_read_paths_from_scratchpad(scratchpad)` — replaces `executor._scratchpad_exact_read_paths`
      - `render_artifact_view(tool_result)` — replaces `executor._render_code_view` + `_maybe_emit_code_view`
      - `capture_exact_read(stage, tool_result, existing)` — combines `_should_capture_exact_file_read_for_planner` + `_append_exact_file_read_note_from_result`
      - `should_block(stage, tool_tag, exact_read_paths)` — combines two block guards; returns `FileWorkBlock` dataclass
      - `recovery_hint(stage, tool_result, file_check)` — moves from `FileStagePolicy.file_checker_recovery_hint`
      - `collect_evidence(stage, tool_result, existing)` — convenience combinator returning `FileWorkEvidence`
    - added `FileWorkEvidence`, `FileWorkBlock` dataclasses and `FileStageKind` Literal to `contracts.py`
    - `executor.py`: removed 9 methods + 29-line constant, imported `FileWorkEngine`, updated all call sites
    - `file_checker.py`: removed `_candidate_paths_from_evidence`, uses `FileWorkEngine.candidate_paths()`
    - `file_stage_policy.py`: `_CODE_FILE_EXTENSIONS` now imports from leaf module; `file_checker_recovery_hint` and `tool_result_candidate_paths` removed (logic in engine)
    - `FileWorkEngine` added to `core/engines/__init__.py` exports
  - Validation:
    - `python scripts/file_work_engine_smoke_test.py` — 28/28 cases pass
    - `python scripts/consolidate_exclusion_verifier_smoke_test.py` — still passing (no regression)
    - `python scripts/extension_reorg_current_state_verifier_smoke_test.py` — still VERIFIED
    - `python scripts/file_stage_policy_smoke_test.py` — still passing
- 2026-03-19: Started trigger-flow alignment with a shared search in-flight guard across UI, orchestrator, and reporter handoff.
  - Problem:
    - `phase_search()` launched the background search thread and returned immediately, but the shared "operation is still active" state depended on the thread retaining the cancel token after it started.
    - That left a timing-sensitive gap where the turn could finish before the background search retained ownership, and there was no route-level guard to stop a second SEARCH dispatch if one slipped through.
    - The follow-on `search_result` -> reporter loop had the same asynchronous handoff pattern.
  - Fix:
    - added shared search-in-flight state to `ui/controller.py` and folded it into `has_active_operations()`
    - threaded search-state callbacks through `run_agent_loop()` / `Orchestrator`
    - updated `phase_search()` to retain both the cancel token and search-in-flight state before spawning the background thread, then release them in thread cleanup
    - updated `phase_route()` to redirect duplicate SEARCH decisions to `PERSONA` with a deterministic search-in-flight notice
    - updated `phase_persona()` to fast-path that notice so the user gets a truthful "search already running" reply without another model round-trip
    - updated `handle_search_result()` to retain the cancel token before spawning the internal reporter turn thread
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness` — clean
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py --json` — pass
    - targeted in-memory route check: active search + router SEARCH decision now redirects to `CHAT` with `system_notice.kind=search_in_flight` and `next_stage=PERSONA`
    - observed but not addressed in this pass:
      - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` failed on `pronoun_followup`
      - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` failed on `copy_file` / `move_file`
- 2026-03-19: Finished the next trigger-flow alignment pass for FILE_WORK followups and CRUD truthfulness.
  - Problem:
    - document lookup followups could drift from the user’s fresh subject back to a stale hidden/runtime path hint, which caused the grocery-list pronoun thread to reread `text_files/grocery_list.txt`
    - FILE_WORK current-state verification for copy stages could falsely verify after a directory-prep action because synthetic copy checks let a preferred directory path override the real source path
    - deleting an already-absent file surfaced as a tool failure even when the requested end state was already satisfied
  - Fix:
    - `core/routing/route_normalizer.py`: current explicit lookup subjects now outrank stale hidden/runtime path hints for read/search followups
    - `core/skills/selector.py`: lookup-only skill rewrites now prefer stage target terms before recent path hints, so `"grocery"` stays `"grocery"` instead of being rewritten to `text_files/grocery_list.txt`
    - `core/file_checker_rules.py`: copy verification now requires the source to still exist and the source/destination types to match, which blocks bogus "directory prep means copy succeeded" verification
    - `tools/workspace_mutation_actions.py`: `delete_path` / `delete_many` are now idempotent and report already-absent targets as satisfied current state instead of returning an error
    - `core/executor.py`: persona/runtime summaries now preserve `tool_result.current_state_only`, so already-satisfied delete turns are narrated honestly instead of as fresh removals
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness` — clean
    - `./.venv/Scripts/python.exe scripts/document_lookup_followup_normalizer_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json --keep-data-copy` — pass
- 2026-03-19: Aligned the search turn with the trigger-flow first-pass response target.
  - Problem:
    - `phase_search()` still used a thin courtesy prompt, so the paid search-turn LLM call only said "I'm checking the web" instead of using Piper's normal persona context path for a useful first-pass answer.
    - reporter-followup guidance only said "answer from the completed search summary," which left the intended "extend/update the first response" behavior underspecified once the richer first-pass search reply is live.
  - Fix:
    - `core/orchestrator_phases.py`: `phase_search()` now builds the normal persona context pack, renders it through `PromptBuilder.build_persona_prompt()`, and routes the search first-pass reply through `build_persona_messages(...)` with a dedicated `[SEARCH_FIRST_PASS_RULE]`
    - `core/orchestrator_phases.py`: added deterministic fallback behavior so an empty/erroring first-pass reply collapses back to the old brief acknowledgement instead of blocking the background search flow
    - `core/orchestrator_phases.py`: search first-pass cleanup now strips the new control tag before sanitization
    - `core/engines/context_pack.py`: strengthened `[SEARCH_REPORT_RULE]` so the reporter-followup persona turn extends/refines the first-pass answer instead of restarting from scratch
    - `scripts/context_pack_engine_smoke_test.py`: added regression coverage for the stronger search report directive wording
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `./.venv/Scripts/python.exe scripts/code_session_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_edit_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_lookup_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_crud_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/file_chaos_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/summary_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/context_pack_engine_smoke_test.py` — pass

- 2026-03-26: Hardened edge-case FILE_WORK and follow-up truthfulness paths from harness repros.
  - Problem:
    - `FILE_OP append_text` silently accepted the planner's `text` field as empty content, so constrained append edits could no-op, fail verification, and fall into incomplete retry loops.
    - the follow-up resolver and route clarifier LLM paths were missing `CFG` imports, so boundary-validation branches could error instead of behaving deterministically under test.
    - event subject extraction left `"for to ..."` remnants after stripping relative dates, which polluted event titles and dependency messages.
    - dependency-blocked FILE_WORK turns could keep the workspace safe but still let persona freestyle a false-success narration on same-name task/file collisions.
    - route-boundary expectations had drifted: ambiguous lookup requests now intentionally clarify source even when the router claims high confidence.
  - Fix:
    - `tools/workspace_mutation_actions.py`: `append_text` now accepts either `content` or `text`, fails honestly when the target is missing, and records append/prefix hashes for verification.
    - `tools/workspace_file_actions.py` + `core/file_checker_rules.py`: wired `append_text` through the local checker so append operations verify directly instead of depending on fragile LLM-only evidence.
    - `core/engines/followup_resolution.py` and `core/engines/route_clarity.py`: restored `CFG` imports so the LLM refinement path works again.
    - `core/routing/route_subjects.py`: cleaned event-subject extraction after relative-date removal so `"Schedule an event for next Tuesday to review..."` becomes `review the file ...`.
    - `core/engines/context_pack.py`: added a deterministic direct-answer path for `ACTIVE_TASK_DEPENDENCY` / `ACTIVE_EVENT_DEPENDENCY` failures so persona cannot narrate a blocked delete/move as successful.
    - `scripts/route_boundary_smoke_test.py`: updated expectations to current lookup-source clarification rules and repaired the fake LLM stubs / validation-path coverage.
    - added new harnesses:
      - `scripts/file_append_constraints_smoke_test.py`
      - `scripts/file_event_mutex_smoke_test.py`
      - `scripts/file_task_collision_mutex_smoke_test.py`
      - `scripts/file_work_state_isolation_smoke_test.py`
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS scripts` — clean
    - `python3 scripts/context_pack_engine_smoke_test.py` — pass
    - `python3 scripts/route_boundary_smoke_test.py` — pass
    - `python3 scripts/file_append_constraints_smoke_test.py --json` — pass
    - `python3 scripts/file_event_mutex_smoke_test.py --json` — pass
    - `python3 scripts/file_task_collision_mutex_smoke_test.py --json` — pass
    - `python3 scripts/file_work_state_isolation_smoke_test.py --json` — pass
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass
    - `python3 scripts/missing_file_no_reroute_smoke_test.py --json` — pass
    - `python3 scripts/file_target_confirmation_smoke_test.py --json` — pass
    - `python3 scripts/file_multi_create_count_smoke_test.py --json` — pass
    - `python3 scripts/file_rename_then_move_smoke_test.py --json` — pass

- 2026-03-28: Stabilized `COMPUTER_USE` across both `file://` fixtures and localhost Playwright.
  - Problem:
    - the browser engine already returned `element_inventory`, but `core/scratchpad_formatter.py` stripped it out of `BROWSER_OP` observations, so the planner could not see selectors like `#status`, `#email`, or `#download-link` and fell back to `body` scraping / retry loops.
    - `core/engines/computer_use_verifier.py` treated form-fill selectors too literally (`#email` vs `[name='email']`) and did not credit verified DOM inventory text for destination/status-style prompts.
    - the local `file://` browser path did not carry refreshed `element_inventory` / `text_preview` / `field_values` after navigation, so post-click verification could read stale evidence from the prior page.
    - Playwright localhost validation in WSL needed its own env and rootless NSS/NSPR libs; do not recreate the repo `.venv` from WSL.
  - Fix:
    - `core/scratchpad_formatter.py`: preserved compact browser `element_inventory`, `field_values`, and `text_preview` in `BROWSER_OP` observations and raised the browser observation budget so selector evidence survives into the planner prompt.
    - `core/engines/computer_use_verifier.py`: added selector-alias matching for filled fields, allowed verified DOM inventory text to satisfy on-page text reporting when it matches the requested token, and removed unsafe fallback to unrelated inventory text when the requested token is absent.
    - `core/engines/computer_use_engine.py`: brought the local fixture backend up to parity with Playwright by returning current-page inventory/text/field state from `goto_url`, `extract_text`, `wait_for`, `click`, and `type_text`.
    - `.gitignore`: ignore `.venv-wsl/` so WSL Playwright validation stays separate from the Windows runtime env.
  - Validation:
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_engine_smoke_test.py --json` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_title_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_extract_download_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_form_navigation_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 scripts/computer_use_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 scripts/computer_use_extract_download_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 scripts/computer_use_form_navigation_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 -m compileall core tools scripts` — clean

- 2026-03-28: Promoted `COMPUTER_USE` to a small human-facing live-browser pilot.
  - Problem:
    - explicit browser routing only recognized fully qualified URLs, so normal human inputs like `open example.com in the browser` missed the `COMPUTER_USE` route.
    - there was no repo-level live-site safety gate yet; any explicit host could be attempted if Playwright was present.
    - heading-style read requests (`main heading`, `headline`) had no semantic selector hint, so the planner had to improvise.
    - the Windows runtime `.venv` did not yet have `playwright` or Chromium installed, so the code was ready before the shipped runtime was.
  - Fix:
    - `config.py`: added `COMPUTER_USE_ENABLED`, `COMPUTER_USE_HTTP_ENABLED`, and `COMPUTER_USE_ALLOWED_HTTP_DOMAINS` (default pilot allowlist: `example.com`, `localhost`, `127.0.0.1`).
    - `core/engines/computer_use_engine.py`: enforced the config-level live-site pilot allowlist at runtime and allowed semantic headings (`h1/h2/h3`) through browser element inventory so read-only live extraction is more transparent.
    - `core/routing/route_normalizer.py`: added bare-domain browser URL normalization (`example.com` -> `https://example.com`, localhost/IP -> `http://...`) and a heading semantic hint (`main heading` -> selector hint `h1`).
    - added new regression surfaces:
      - `scripts/computer_use_playwright_example_engine_smoke_test.py`
      - `scripts/computer_use_playwright_example_title_harness_smoke_test.py`
      - `scripts/computer_use_playwright_example_heading_harness_smoke_test.py`
      - `scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py`
    - installed `playwright` plus Chromium browser binaries into the Windows repo `.venv`, while keeping WSL browser validation in `.venv-wsl`.
  - Validation:
    - `python3 scripts/computer_use_route_normalizer_smoke_test.py --json` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_engine_smoke_test.py --json` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_example_engine_smoke_test.py --json` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_example_title_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_example_heading_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 scripts/computer_use_form_navigation_harness_smoke_test.py --json --timeout 120` — pass
    - `'/mnt/c/Projects/Piper/.venv/Scripts/python.exe' -c "from playwright.sync_api import sync_playwright; ... page.goto('https://example.com') ..."` — pass (`Example Domain`)
    - `python3 -m compileall core tools scripts` — clean

- 2026-03-28: Hardened human-facing `COMPUTER_USE` phrasing and browser follow-ups.
  - Problem:
    - live browser use felt brittle unless the user copied the seeded examples exactly.
    - looser prompts like `What's the title of example.com?` and `What's the main heading on example.com?` were only covered at normalization level, not in a real Playwright turn loop.
    - short browser follow-ups like `what else is there` now routed into `COMPUTER_USE`, but the verified payload was still collapsing generic page-text extracts back to the page title.
  - Fix:
    - `core/routing/route_normalizer.py`: accept URL + title/heading extract language as explicit browser work, and carry short browser follow-ups forward from `[LATEST_RUNTIME_CONTEXT]` using the previous verified page URL plus a `body` selector hint.
    - `core/engines/computer_use_verifier.py`: promote selector-hinted and generic verified extracts into `extracted_text` so summary/persona layers can speak from the actual page text instead of falling back to the title.
    - `core/engines/summary.py`: render generic verified browser extracts as direct page-text answers, with truncation, instead of misreporting the page title.
    - added regression surfaces:
      - `scripts/computer_use_browser_followup_harness_smoke_test.py`
      - `scripts/computer_use_playwright_example_alt_prompt_harness_smoke_test.py`
  - Validation:
    - `python3 scripts/computer_use_route_normalizer_smoke_test.py --json` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_browser_followup_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_example_alt_prompt_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_real_site_pilot_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 -m compileall core scripts` — clean

- 2026-03-31: Moved browser continuations into the real follow-up engine instead of keeping them in route normalization heuristics.
  - Problem:
    - browser follow-ups were behaving like follow-ups from the user's point of view, but the implementation still lived in `route_normalizer.py`.
    - that meant browser continuations were being solved by a small router-side phrase matcher instead of the repo's actual continuation owner, `FollowupResolutionEngine`.
    - the result was the wrong architectural pressure: adding more browser follow-up phrasings to normalization instead of resolving intent families from active browser runtime context.
  - Fix:
    - added `core/browser_route_utils.py` as the shared home for explicit browser-card construction and browser follow-up intent-family helpers.
    - `core/engines/followup_resolution.py` now owns active-page browser continuations by calling `build_browser_context_followup_route(...)` from deterministic fallback resolution.
    - `core/routing/route_normalizer.py` now only owns explicit first-turn browser requests and no longer registers browser-context follow-up normalization.
    - updated regression ownership:
      - `scripts/computer_use_route_normalizer_smoke_test.py` now covers explicit browser routing only
      - `scripts/followup_resolution_engine_smoke_test.py` now covers browser continuation resolution
      - `scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py` now uses a real second-turn follow-up (`What's the main heading?`) instead of another explicit URL prompt
  - Validation:
    - `python3 scripts/computer_use_route_normalizer_smoke_test.py --json` — pass
    - `python3 scripts/followup_resolution_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_browser_followup_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_playwright_example_alt_prompt_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 -m compileall app.py config.py core ui memory tools llm` — clean
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-03-31: Added topic-aware browser extraction so vague page follow-ups return relevant sections instead of generic body dumps.
  - Problem:
    - browser follow-up routing was fixed, but answer quality for prompts like `general info` was still poor.
    - `COMPUTER_USE` stage cards already carried `requested_topic`, but the browser engine and verifier were ignoring it, so verified replies often fell back to broad `body` text.
    - that made real follow-ups on pages like the Python docs stay on the right URL but still feel clumsy and low-signal.
  - Fix:
    - `core/executor.py`: inject active `COMPUTER_USE` stage metadata into `BROWSER_OP` calls at runtime, including `allowed_domains` for `goto_url` and `requested_topic` for `extract_text`.
    - `core/engines/computer_use_engine.py`: added deterministic topic-ranked extraction over ordered heading/text blocks for both local fixtures and Playwright pages; generic topics such as `general info` now prefer the first substantive section instead of page chrome.
    - `core/engines/computer_use_verifier.py`: browser verification now records topic-aware extract evidence and treats `requested_topic` extraction as a first-class verified outcome.
    - `core/engines/summary.py`: verified browser answers now render as `Here is the section about 'topic' ...`, which keeps follow-up runtime notes grounded in the active topic.
    - `core/browser_route_utils.py`: browser topic recovery now also understands phrasing like `section about '...'`, and topic-carrying browser follow-up cards keep topic-specific stage goals even when the selector hint is just `body`.
    - `data/prompts/manager.txt` and `tools/registry.py`: documented the structured `{"action":"extract_text","selector":"body","topic":"..."}` path for `COMPUTER_USE`.
    - added deterministic topic fixtures and harnesses:
      - `scripts/fixtures/computer_use/topic_sections.html`
      - `scripts/computer_use_playwright_localhost_topic_followup_harness_smoke_test.py`
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/computer_use_engine_smoke_test.py --json` — pass
    - `python3 scripts/computer_use_route_normalizer_smoke_test.py --json` — pass
    - `python3 scripts/followup_resolution_engine_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_playwright_localhost_topic_followup_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_browser_followup_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_python_docs_followup_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_playwright_example_two_turn_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_playwright_blocked_domain_harness_smoke_test.py --json --timeout 120` — pass
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass
    - `python3 scripts/code_session_smoke_test.py --json` — pass
    - `python3 scripts/file_chaos_test.py --json` — first run failed due an incomplete consolidation pass; immediate rerun passed, so treat this harness as still model-sensitive rather than browser-regressed
    - `python3 scripts/summary_engine_smoke_test.py` — pass
    - `python3 scripts/context_pack_engine_smoke_test.py --json` — pass

- 2026-04-03: Probed whether Qwen thinking can be enabled per inference on the active local runtime.
  - Context:
    - active stack is `Qwen_Qwen3.5-9B-Q6_K.gguf` on `llama-server` build `8241 (62b8143ad)`.
    - Piper currently defaults Qwen 3.5 to `--reasoning-budget 0` in `config.py`, and persona empty-output recovery appends ` /no_think` to the last user message.
  - Live findings:
    - with `--reasoning-budget 0`, suffixing ` /think` had no effect; direct `/v1/chat/completions` replies stayed answer-only with no `reasoning_content`.
    - with `--reasoning-budget -1`, plain prompts frequently failed with HTTP 500 `Failed to parse input ...` after the server generated hundreds of tokens.
    - with `--reasoning-budget -1`, suffix ` /think` produced parseable replies containing both final `content` and large `reasoning_content`.
    - with `--reasoning-budget -1`, suffix ` /no_think` also produced large `reasoning_content`; it did not actually suppress thinking.
    - prefix placement (`/think` or `/no_think` at the start of the user message) behaved worse and hit the same parser 500s.
  - Implication:
    - the current runtime does not support a clean per-phase “thinking on for planner, off for persona” design just by raising the server reasoning budget and toggling `/think` or `/no_think` per prompt.
    - if we revisit per-phase thinking later, treat it as a runtime/protocol experiment, not a prompt-only switch.

- 2026-04-05: Started the first multi-user foundation slice with a permanent owner/admin id for Baris.
  - Runtime model:
    - `admin_baris` is the canonical owner profile.
    - the admin profile stays on the legacy root `data/` silo for backward compatibility with Baris's existing memory and state.
    - standard users are created under `data/users/<user_id>/`.
  - Isolation now covers:
    - chat memory path
    - conversation summaries
    - tasks/events/knowledge/world-model/transient state
    - vector recall (`memory/brain.py` now caches per data dir)
    - ingested document indexes and document vector storage
    - per-user style filename in `data/users.json`
  - Manual control layer added before voice ID:
    - `/users`
    - `/user`
    - `/whoami`
    - `/user <name-or-id>`
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/user_runtime_smoke_test.py --json` — pass

- 2026-04-05: Fixed untimed reminder phrasing being misrouted as proactive reminders instead of task/event state.
  - Root cause:
    - the `REMINDER_SET` route interceptor in `core/engines/proactive_monitor.py` matched every `remind me to ...` request, even when the user gave no fire time.
    - that bypass skipped normal task/event routing, so `remind me to buy milk` became a chat-style reminder error instead of a task.
    - a follow-up like `set it as a task` could then stay on the wrong branch and the persona could narrate success without a real task mutation.
  - Fix:
    - timed reminders still use the proactive reminder path.
    - undated / untimed reminder phrasing now falls back to `TASK_EVENT_WORK` task creation.
    - dated reminder phrasing without a precise fire time now falls back to `TASK_EVENT_WORK` event creation.
    - explicit follow-ups like `set it as a task` now override prior reminder framing and create a real task.
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/proactive_monitor_smoke_test.py --json` — pass
    - `python3 scripts/reminder_task_fallback_smoke_test.py --json` — pass

- 2026-04-05: Fixed a silent Windows startup exit in the UI bootstrap.
  - Symptom:
    - `python app.py` returned immediately in PowerShell with no traceback and no window.
  - Root cause:
    - `PiperController.__init__()` called `refresh_active_user_meta()`, which reached `refresh_top_bar()` before `build_ui()` had created a Dear PyGui context.
    - on the Windows Dear PyGui path, calling `dpg.does_item_exist(...)` before `dpg.create_context()` caused a native hard exit instead of a Python exception.
  - Fix:
    - `ui/controller.py` now loads the active-user label during controller init without touching DPG, then performs the real top-bar refresh only after `build_ui()` completes.
  - Validation:
    - `python3 -m py_compile ui/controller.py app.py` — clean
    - `./.venv/Scripts/python.exe -u -c "import app; c=app.build_controller(); print('controller-ok')"` — pass
    - `./.venv/Scripts/python.exe -u - <<'PY' ... build_ui(...) ... print(dpg.is_dearpygui_running()) ... PY` — reports `True`

- 2026-04-05: Pivoted multi-user privacy toward owner unlock instead of equal privacy for every profile.
  - Product direction:
    - `admin_baris` is the only protected profile.
    - non-admin users remain per-user context silos, but they are not treated as security boundaries.
    - typed owner activation now requires a password once one has been configured.
  - Implementation:
    - `memory/user_runtime.py` now persists a small auth block in `data/users.json` with a PBKDF2-hashed admin password, public-speaker fallback metadata, and runtime-only admin unlock state.
    - typed `/user admin_baris` now pauses for a password instead of switching immediately when an admin password exists.
    - the next input is consumed as the password in both `ui/controller_actions.py` and `AGENTS/harness/session.py`; password attempts are not appended to chat history or persisted to memory.
    - `/adminpass <password>` sets or updates the owner password from an active Baris session.
    - per-user style persistence remains in `data/users.json`; once a user is re-identified, their saved style is reloaded with their memory/session.
    - on restart, a locked admin session falls back to a public-speaker state instead of auto-loading Baris's private memory; the current boot normalization now resolves that state to `unknown`.
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/user_runtime_smoke_test.py --json` — pass

- 2026-04-05: Added `unknown` speaker boot state plus admin world-memory mirroring for public speakers.
  - Product direction:
    - Piper should not assume the last public user is still present after restart.
    - Baris's privacy boundary is owner verification, while other speakers are convenience identities that still belong in Baris's world model.
  - Implementation:
    - `memory/user_runtime.py`
      - default public speaker is now `unknown` instead of `guest`.
      - boot normalizes the active public speaker to `unknown`, even if a different public user was active before shutdown.
      - typed self-identification like `I'm Max` / `My name is Max` can switch the active public speaker before the turn is processed.
      - typed `I'm Baris` now routes into the existing owner-password flow when an admin password is configured.
      - non-admin world-model saves now mirror stable facts into Baris's admin world graph under `person:<user_id>`.
      - explicit relation hints like `Baris's friend` create an admin-side relation edge from Baris to that person.
      - `[ACTIVE USER]` prompt context now tells persona to ask one short natural question when the speaker is still `unknown` or when Baris is missing a key relationship gap for a public speaker.
    - `memory/world_model.py`
      - added a graph-saved callback so profile-world updates can trigger Baris-memory mirroring.
    - `ui/controller_actions.py` and `AGENTS/harness/session.py`
      - typed identity hints are observed before a normal user turn is persisted, so silent public-user switching and owner-password prompting happen at the right boundary.
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/user_runtime_smoke_test.py --json` — pass

- 2026-04-05: Fixed false speaker creation from generic `I am ...` phrasing.
  - Symptom:
    - a line like `i am his friend, do you know baris?` could be parsed as a self-identification and create a bogus public user such as `his_friend`.
  - Root cause:
    - `_extract_self_identified_name()` accepted broad `i am ...` phrases, and `_clean_identity_name()` did not reject relation/descriptive noun phrases like `his friend`.
  - Fix:
    - `memory/user_runtime.py`
      - added guards for leading descriptor words (`his`, `her`, `their`, `the`, etc.) and relationship nouns (`friend`, `partner`, `mother`, `brother`, etc.) in the typed identity cleaner.
    - `scripts/user_runtime_smoke_test.py`
      - added the exact regression phrase `i am his friend, do you know baris?`
      - verifies the runtime stays on `unknown` and the harness does not silently switch speaker context from that line.
  - Validation:
    - `python3 -m compileall memory/user_runtime.py scripts/user_runtime_smoke_test.py ui/controller_actions.py AGENTS/harness/session.py` — clean
    - `python3 scripts/user_runtime_smoke_test.py --json` — pass

- 2026-04-05: Fixed question-like dialogue being stored as durable profile facts.
  - Symptom:
    - Max's public profile ended up with `personality_trait = "max, what is yours"`, which made persona ask bizarre follow-ups about a trait that was really just dialogue.
  - Root cause:
    - `profile_fact_shape_is_allowed()` rejected malformed keys, but it still allowed question/dialogue-shaped values to survive world-model refresh and legacy-knowledge mirroring.
  - Fix:
    - `memory/knowledge_fact_rules.py`
      - added `_QUESTIONISH_VALUE_RE` and now rejects question-style profile values such as:
        - direct questions
        - name-prefixed questions like `max, what is yours`
        - assistant-directed fragments like `do you ...`, `who are you`, `what is yours`
    - `scripts/world_model_suspicious_fact_scrub_smoke_test.py`
      - expanded to verify that question-shaped values are scrubbed from both `world_model.json` and `knowledge.json`
    - live state cleanup:
      - removed the poisoned `max, what is yours` trait from:
        - `data/users/max/state/world_model.json`
        - `data/users/max/state/knowledge.json`
        - mirrored admin copy in `data/state/world_model.json`
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/world_model_suspicious_fact_scrub_smoke_test.py` — pass
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-05: Converted `unknown` from a saved profile into a virtual speaker state.
  - Symptom:
    - `/users` showed `Unknown [unknown; unknown]` as if it were a normal profile, and restarting Piper could surface stale chat from the prior unknown session.
  - Root cause:
    - `memory/user_runtime.py` persisted `unknown` in `data/users.json`, listed it alongside real users, and bound startup chat history to the unknown silo like any other profile.
  - Fix:
    - `memory/user_runtime.py`
      - stopped persisting `unknown` in the registry
      - reserved `unknown` as a virtual profile resolved on demand
      - moved unknown session artifacts under `data/runtime/unknown`
      - clears the unknown runtime scratch on boot so transcript and summary do not survive restarts
      - prevents style persistence from recreating an `unknown` user record
    - `ui/controller_actions.py`
    - `AGENTS/harness/session.py`
      - `/users` now presents unknown as the current speaker state, not as a saved profile
    - `scripts/user_runtime_smoke_test.py`
      - now verifies `unknown` is absent from the saved registry and that stale unknown transcript/summary files are cleared on restart
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/user_runtime_smoke_test.py --json` — pass
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-06: Fixed Windows startup stalls caused by eager STT/audio imports before the GUI window existed.
  - Symptom:
    - `python app.py` could appear frozen with no Piper window for tens of seconds or even minutes, and sometimes the window only appeared long after launch.
  - Root cause:
    - `app.py` cannot show the DearPyGui window until `build_controller()` returns.
    - `ui/controller.py` imported `ui/controller_actions.py`, which eagerly imported `tools/stt.py`.
    - `tools/stt.py` imported `sounddevice` at module import time, and on the Windows runtime that import was slow enough to block startup before the viewport could be created.
  - Fix:
    - `tools/stt.py`
      - moved `sounddevice` and `faster_whisper` imports behind lazy loader helpers
      - STT dependencies now load only when microphone recording or transcription is actually used
    - `ui/controller_actions.py`
      - removed the module-level `get_stt_engine` import
      - microphone support is now imported inside `on_mic_toggle()`
  - Validation:
    - Windows timing probes:
      - `import app` ≈ 1.36s
      - `import ui.controller_actions` ≈ 1.13s
      - `import tools.stt` ≈ 0.16s
      - `app.build_controller()` ≈ 0.02s
    - `python3 -m compileall app.py config.py core ui memory tools llm` — clean
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-06: Fixed boot-screen stalls by making vector brain fallback-first and TTS warm-up non-blocking.
  - Symptom:
    - the Piper window opened, but boot could remain stuck at:
      - `Starting LLM Server...`
      - `Warming TTS engine...`
      - `Checking engineering channel...`
      - `Initializing Vector Brain...`
      - `Using existing LLM server.`
      - `Engineering channel: ONLINE`
    - `Brain Model Loaded.` and `System Ready.` never appeared.
  - Root cause:
    - on the Windows runtime, `PiperBrain(CFG.DATA_DIR)` was hanging for over 60 seconds while trying to build the Chroma + sentence-transformer backend.
    - boot was also waiting for `tts.warm_up()`, and the current `kokoro_onnx` import/load path can still take longer than 20 seconds.
  - Fix:
    - `memory/brain.py`
      - fallback memory now loads immediately and becomes usable right away
      - vector memory warm-up now starts in a daemon thread instead of blocking construction
      - `recall()` and `remember()` use fallback memory immediately while vector warm-up is pending
      - fallback entries are synced into Chroma later if the vector backend finishes warming
    - `llm/boot.py`
      - boot now treats fallback-backed brain readiness as sufficient to continue
      - boot log is explicit: `Brain Ready (fallback active; vector warm-up continues).`
    - `app.py`
      - TTS warm-up moved out of blocking post-boot tasks into background boot tasks
  - Validation:
    - Windows probes:
      - `get_brain(CFG.DATA_DIR)` ≈ 0.008s and returns with `vector_warmup_pending=True`
      - `brain.recall(...)` returns immediately via fallback memory
      - `boot_mgr.run_sequence()` now reaches `System Ready.` and returns in ≈ 0.017s
    - `python3 -m compileall app.py config.py core ui memory tools llm` — clean
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-06: Restored Windows TTS by adding a native system-speech fallback backend.
  - Symptom:
    - Piper replied normally again, but no voice played at all.
    - TTS requests stayed busy indefinitely, and older `tts_debug.txt` entries showed unstable Kokoro behavior (`model not found`, `need at least one array to concatenate`, access violation).
  - Root cause:
    - after boot stopped waiting on Kokoro, the actual TTS runtime was still trying to use the `kokoro_onnx` path, which remains unstable on this Windows machine.
    - the queue/stream pipeline was healthy, but the synth worker could hang inside Kokoro and never drain.
  - Fix:
    - `tools/tts.py`
      - added `_WindowsSystemSpeechEngine` using native `System.Speech.Synthesis.SpeechSynthesizer`
      - `TTSConfig.backend` now supports `auto`, `kokoro`, and Windows system speech (`system` / `sapi`)
      - on Windows, `auto` now prefers the native system-speech backend so Piper speaks reliably by default
      - text jobs can now bypass synth-to-samples and speak directly through the backend when supported
      - Windows fallback uses the real `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` path instead of relying on PATH resolution
    - `config.py`
      - added `TTS_BACKEND = "auto"`
    - `app.py`
      - passes `TTS_BACKEND` into `TTSConfig`
  - Validation:
    - Windows live probe:
      - engine selected: `_WindowsSystemSpeechEngine`
      - `warm_up()` completed
      - `speak('Piper speech fallback probe.')` completed
      - `tts.is_busy()` returned to `False`
    - `python3 -m compileall app.py config.py tools/tts.py` — clean
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-06: Added same-name speaker disambiguation for public identity selection.
  - Symptom:
    - if Baris knows two different people with the same display name, Piper could silently bind a typed identity like `I'm Ekin` to the first matching profile instead of asking who it was.
  - Root cause:
    - `memory/user_runtime.py` resolved self-identification and `/user <name>` by the first matching profile name, without consulting Baris's mirrored world relations (`friend`, `partner`, etc.).
  - Fix:
    - `memory/user_runtime.py`
      - added relation-aware identity candidate gathering from Baris's admin world graph
      - relation hints like `Baris's friend` / `Baris's partner` now select or create distinct same-name profiles safely
      - plain same-name identity claims now return an explicit clarification result instead of guessing
      - manual `/user <name>` now also refuses ambiguous same-name switches
    - `ui/controller_actions.py`
    - `AGENTS/harness/session.py`
      - clarification results are surfaced immediately to the user and the ambiguous line is not persisted as a normal user turn
    - `scripts/user_runtime_smoke_test.py`
      - added duplicate-name regression coverage for:
        - two public users with the same display name but different Baris relations
        - relation-guided resolution
        - plain-name clarification
        - manual `/user <name>` clarification
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/user_runtime_smoke_test.py --json` — pass
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-06: Fixed real-site browser download completion for semantic hints like `text version`.
  - Symptom:
    - `Open https://www.rfc-editor.org/rfc/rfc2606.html in the browser and download the text version into browser_downloads_real.` could fetch the file but still finish as `PARTIAL`, or loop/talk as if the artifact had not been saved yet.
  - Root cause:
    - `core/engines/computer_use_engine.py` only matched Playwright download targets by exact visible text, so terse links like `TEXT` were invisible to a hint like `text version`.
    - `core/engines/computer_use_verifier.py` still used raw token containment, so `rfc2606.txt` did not satisfy the hint `text version` even after the file existed.
    - `core/browser_route_utils.py` also over-marked pure download prompts as extract+download when the target phrase itself contained words like `text`.
  - Fix:
    - `core/browser_route_utils.py`
      - pure download requests no longer become extract stages just because the download target contains `text`.
    - `core/engines/computer_use_engine.py`
      - Playwright element inventory now captures visible body links/buttons instead of head/meta noise
      - download targeting now scores semantic hints against link text, hrefs, and file suffixes, so `text version` resolves to `.txt` / `TEXT`
      - workspace root is normalized to an absolute path so `saved_path` reporting is stable outside harnesses too
    - `core/engines/computer_use_verifier.py`
      - download verification now uses semantic artifact scoring instead of raw token overlap
      - checksum-like artifacts are de-prioritized unless the hint actually asks for a checksum
    - `scripts/computer_use_route_normalizer_smoke_test.py`
      - now asserts the RFC `text version` request is download-only, not extract+download
    - `scripts/computer_use_playwright_rfc_download_harness_smoke_test.py`
      - new real-site regression for the RFC 2606 text artifact path
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts` — clean
    - `python3 scripts/computer_use_route_normalizer_smoke_test.py --json` — pass
    - `python3 scripts/computer_use_engine_smoke_test.py --json` — pass
    - `python3 scripts/computer_use_navigation_download_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_localhost_navigation_download_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_real_site_pilot_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv-wsl/bin/python scripts/computer_use_playwright_rfc_download_harness_smoke_test.py --json --timeout 150` — pass
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-06: Finished the startup/TTS cleanup so the boot UI is visible again and style/TTS defaults are consistent.
  - Symptom:
    - Piper could become interactive so quickly that the boot screen never appeared at all.
    - Several UI action paths still loaded style/TTS defaults with hardcoded `af_heart` / `0.9`, so active-style or config changes were not applied consistently.
    - Kokoro playback also had a bad indentation in `_KokoroEngine.play()` that kept `sounddevice.play()` inside the exception branch.
  - Fix:
    - `ui/controller_queue.py`
      - added a deferred `boot_ready` handoff so the boot UI stays visible until `CFG.BOOT_SCREEN_MIN_VISIBLE_S` has elapsed before switching to the status group.
    - `ui/controller.py`
      - `load_style_state()` is the single config-aware style loader and now uses the current `CFG.TTS_SPEED` default (`0.85`) instead of the stale `0.9` fallback.
    - `ui/controller_actions.py`
      - active-user refresh, vision queries, new-session reset, and `/style` updates now all reuse `controller.load_style_state()` instead of their own hardcoded defaults.
    - `tools/tts.py`
      - `TTSConfig` defaults now match the runtime config (`af_heart`, `0.85`)
      - fixed `_KokoroEngine.play()` so it always calls `sounddevice.play()`
      - Windows system-speech fallback now caches installed voices and maps `af_*` / `bf_*` style hints to the closest available female/male system voice when possible
  - Validation:
    - `python3 -m compileall app.py config.py core ui memory tools llm` — clean
    - `./.venv/Scripts/python.exe - <<'PY' ... PY` boot-ready deferral + Windows voice-mapping probe — pass
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-06: Restored style-voice ownership for Windows TTS by removing `sounddevice` from the Kokoro path.
  - Symptom:
    - active styles like `quinn.style` loaded correctly (`af_bella` / `1.05`) but speech still sounded like the robotic Windows system voice because `TTS_BACKEND=auto` always selected `_WindowsSystemSpeechEngine`.
  - Root cause:
    - `tools/tts.py`
      - `_select_engine()` treated `auto` as "Windows system speech first" on Windows, so Kokoro voices were bypassed entirely.
      - `_KokoroEngine._load()` still imported `sounddevice`, and that import hangs on this Windows machine. That made the Kokoro path look unusable even though the actual style voice configuration was correct.
  - Fix:
    - `tools/tts.py`
      - `_KokoroEngine` no longer imports `sounddevice` during load
      - on Windows, Kokoro playback now writes a temporary WAV and plays it via `winsound`, so the style-voice path does not depend on `sounddevice` at all
      - `TTS._select_engine()` now makes `auto` prefer Kokoro when the Kokoro model files exist, and only falls back to system speech if Kokoro is not configured
    - `app.py`
      - aligned the last stale TTS speed fallback to `0.85`
  - Validation:
    - `python3 -m compileall app.py config.py tools/tts.py` — clean
    - `./.venv/Scripts/python.exe - <<'PY' ... print(type(tts.engine).__name__) ... PY` — `_KokoroEngine`
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

- 2026-04-07: Browser follow-up overviews are richer and stop misreporting section extracts as page headings.
  - Symptom:
    - broad browser follow-ups like `what else is there` stayed in `COMPUTER_USE`, but the reply was too shallow.
    - on some pages, a generic topic extract anchored at `h1` was narrated as if the whole extracted section were the page heading.
  - Root cause:
    - `core/browser_route_utils.py`
      - broad follow-ups without an explicit topic were routed to a plain page-text read, which left the summary path with too little structure to say anything beyond a short preview.
    - `core/engines/summary.py`
      - generic topic extracts coming back on selector `h1` could be mistaken for a real heading-only extract.
    - `core/engines/computer_use_verifier.py`
      - the verified browser payload was not carrying `element_inventory`, so summary could not mention other visible sections or links even when the engine had already captured them.
  - Fix:
    - `core/browser_route_utils.py`
      - broad browser continuations now map to the generic topic `general info` instead of a bare body read.
    - `core/engines/computer_use_verifier.py`
      - verified browser payloads now carry a compact `element_inventory`.
    - `core/engines/summary.py`
      - generic browser overviews use a longer preview and can mention other visible sections from the page inventory.
      - heading fast-path now ignores topic-backed `h1` extracts, so section summaries stop being mislabeled as page headings.
  - Validation:
    - `python3 -m compileall core/browser_route_utils.py core/engines/computer_use_verifier.py core/engines/summary.py`
    - `python3 scripts/computer_use_route_normalizer_smoke_test.py --json` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_browser_followup_harness_smoke_test.py --json --timeout 120` — pass
    - `./.venv/Scripts/python.exe scripts/computer_use_python_docs_followup_harness_smoke_test.py --json --timeout 120` — pass

- 2026-04-07: Restored the pushed-repo Quinn path on Windows by making ONNX text synthesis primary again.
  - Symptom:
    - Quinn/Kokoro was speaking, but the prosody still felt wrong: punctuation and newlines inside a spoken chunk were being flattened compared with the older pushed build.
  - Root cause:
    - `tools/tts.py`
      - the live Windows path had drifted away from the old repo behavior. It was preferring the newer Windows phoneme / fallback stack instead of the plain `kokoro_onnx.create(text, ...)` path that used to handle prosody from the original text.
      - background warm-up could also permanently mark Kokoro disabled on a conservative timeout, which pushed later replies onto torch/system fallback paths even when direct ONNX synthesis itself was healthy.
  - Fix:
    - `tools/tts.py`
      - `_KokoroEngine.synthesize()` now tries the plain ONNX text path first again, matching the old repo flow more closely.
      - the Windows CLI phoneme path remains only as a fallback if text synthesis explicitly fails.
      - `_KokoroEngine.warm_up()` no longer permanently demotes Kokoro just because a background warm-up used a conservative timeout.
      - `choose_reply_backend()` now logs when Quinn is staying on the ONNX path, which makes future regressions easier to spot in `tts_debug.txt`.
  - Validation:
    - `python3 -m compileall tools/tts.py scripts/kokoro_torch_worker.py scripts/tts_windows_probe.py` — clean
    - `python3 scripts/event_speech_policy_smoke_test.py` — pass
    - `./.venv/Scripts/python.exe scripts/tts_windows_probe.py --engine onnx --json` — pass
    - `./.venv/Scripts/python.exe - <<'PY' ... engine.warm_up(); print(engine.choose_reply_backend(...)) ... PY` — `{'loaded': True, 'disabled_reason': '', 'backend': 'onnx'}`

- 2026-04-06: Prevented the Windows Kokoro path from hanging speech forever.
  - Symptom:
    - after switching `auto` back to Kokoro, Piper could become completely silent: `_KokoroEngine` was selected, `tts.is_busy()` stayed `True`, and no synth/play error was logged.
  - Root cause:
    - the Windows Kokoro path was hanging inside the style-voice stack before any exception reached our worker rails.
    - `import onnxruntime` only behaved consistently on this machine when `ONNX_PROVIDER=CPUExecutionProvider` was set before import.
    - even after that, the Windows Kokoro `create()` path could still stall long enough that the TTS worker looked dead from Piper's point of view.
  - Fix:
    - `tools/tts.py`
      - `_KokoroEngine._load()` now forces `ONNX_PROVIDER=CPUExecutionProvider` on Windows before importing `kokoro_onnx`
      - Windows Kokoro now exposes `warm_up()` / `speak_text_blocking()` with a bounded timeout
      - if Kokoro synth times out, Piper marks Kokoro disabled for the current process, logs the reason, and falls back to the Windows system speech engine instead of staying silent forever
    - `config.py`
      - added `TTS_KOKORO_TIMEOUT_S` for the Windows timeout guard
  - Validation:
    - `python3 -m compileall app.py config.py tools/tts.py` — clean
    - `./.venv/Scripts/python.exe -u - <<'PY' ... tts.speak(...) ... PY` — `busy` returned to `False` instead of hanging forever
    - `data/debug/tts_debug.txt` recorded `KOKORO DISABLED: Kokoro synth timed out on Windows`
    - `python3 scripts/file_edit_smoke_test.py --json` — pass
    - `python3 scripts/file_lookup_smoke_test.py --json` — pass
    - `python3 scripts/file_crud_smoke_test.py --json` — pass

