# Known Issues

## Router / Model Behavior

- Qwen 3.5 Q6 still shows stale-context contamination in some routing cases.
- An unrelated user update can sometimes pull in older conversation context and produce the wrong task card.
- Current rails usually prevent bad state mutation, but the route itself can still be wrong.

## Memory

- Semantic dedup in vector memory refreshes metadata on very similar memories.
- That means repeated memories can become "new" again instead of aging out naturally.
- This may be desirable for reinforced truths, but it weakens strict memory aging.
- On the Windows runtime, Chroma + sentence-transformer vector warm-up can still be very slow.
- Piper now boots on fallback memory immediately, but vector warm-up may continue in the background for quite a while.
- PDF ingestion currently depends on text extraction from `pypdf`.
- Image-only or poorly encoded PDFs will still ingest weakly or return little text until an OCR path is added.

## Audio

- Kokoro ONNX on the Windows runtime can still time out and fall back.
- Piper now forces `ONNX_PROVIDER=CPUExecutionProvider` before loading `kokoro_onnx`, and if Kokoro ONNX still times out it falls back to a pure Kokoro-torch worker before using Windows system speech.
- The pure Kokoro-torch worker now bypasses the Windows `platform` WMI calls that were hanging `import torch`, and it decodes `espeak-ng` IPA output as UTF-8 instead of the local ANSI code page.
- Native Quinn probes are green again, but the live GUI path still needs one direct user confirmation after restart.
- If both Kokoro paths fail, Piper still falls back to Windows system speech instead of staying silent forever.

## Prompting

- Age labels help the model understand memory freshness, but they do not fully solve stale memory retrieval by themselves.
- If stale memories still intrude, retrieval thresholds or domain-aware recall gating may need tightening.
- On the current `Qwen3.5-9B-Q6_K` + `llama-server` b8241 path, enabling global reasoning (`--reasoning-budget -1`) is not a safe replacement for per-phase thinking. Plain reasoning-enabled prompts can 500 with `Failed to parse input`, and `/no_think` does not reliably suppress `reasoning_content`.

## FILE_WORK

- Open-ended file reorganization is much safer now, but proposal-first flows are still shallow.
- In approval-gated organization turns, Piper can inspect safely and pause without mutation, but the spoken proposal can still be too thin or generic.
- qwen3.5 q6 still tends to add low-value social follow-ups after successful file work, which can bury important artifact details.
- Diagnosis-only code/file inspections are lifecycle-safe now, but the spoken diagnosis can still be overly generic instead of naming the most concrete file-local bug.
- The current rails force an explicit diagnosis proposal and stop stale memory / exact-read contamination, but model quality still determines how specific that diagnosis is.
- On very large workspaces, qwen3.5 q6 still over-focuses on inventory generation and can waste steps re-listing or serializing file inventories instead of converging quickly to structured `move_many`.
- Broad organization requests can now pause honestly, but qwen3.5 q6 may still invent an overconfident plan description unless the pause handoff contains enough grounded structure.


## Engineering Support

- The external Codex repair loop now exists, but it depends on a working local `codex` CLI being reachable from Piper's runtime environment.
- The control plane and restart/resume path were validated mechanically plus with a simulated repair worker; the full live Windows GUI path with a real Codex patch job still needs live confirmation on the user's machine.
