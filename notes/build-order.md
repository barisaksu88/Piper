# Piper Build Order

Status: Active planning note

This file is the release captain's working sequence. It is not runtime doctrine and does not replace `docs/ROADMAP.md`; it exists to keep near-term work ordered by dependency, risk, and verification value.

## Principle

Build the immune system before adding more muscles.

Piper is now capable enough that the main risk is not lack of features. The main risk is process drift: broad commits, weak evidence, dirty runtime files, and high-risk work continuing on `main` while still buggy.

## Current Critical Path

### 1. Stabilize voice identity

Branch: `stabilize/voice-identity`

Goal: make speaker identity safe enough for manual use before any more identity, privacy, or memory features are built.

Required evidence:
- unknown speaker does not activate admin
- Baris voice activates admin only above strict admin threshold
- unknown-to-identified transition preserves session memory correctly
- typed identity correction still works
- no persistent memory writes for unknown users

Unlocks:
- safer proactive behavior
- safer memory personalization
- safer natural voice mode

### 2. Release gate script

Candidate file: `scripts/release_gate.py`

Goal: provide one local command that summarizes whether a branch is reviewable or blocked.

Checks should include:
- current branch and whether high-risk WIP is on `main`
- dirty working tree status
- staged/private/runtime files
- required smoke/evidence commands for touched domains
- docs changed when behavior changed
- summary verdict: `SHIP`, `NEEDS EVIDENCE`, or `BLOCKED`

### 3. Repo hygiene checker

Candidate file: `scripts/check_repo_hygiene.py`

Goal: prevent accidental commits of runtime junk and private/local state.

Checks should flag:
- `data/debug/`
- runtime JSON/state files
- voice embeddings
- `.claude` or other agent-local state
- local scratch scripts
- model/cache artifacts
- unexpectedly large files

### 4. Evidence ledger convention

Candidate path: `notes/evidence/`

Goal: every risky branch gets a short evidence ledger that records what was actually proven.

Example:
- `notes/evidence/voice-identity.md`

Ledger fields:
- branch
- goal
- test command
- result
- manual test performed
- observed behavior
- unresolved risk
- reviewer verdict

Known issues say what is broken. Evidence ledgers say what has been proven.

### 5. Config reference

Candidate file: `docs/CONFIG_REFERENCE.md`

Goal: document environment flags, defaults, and risk notes so Piper's behavior is not controlled by mystery switches.

Suggested groups:
- identity / privacy
- LangGraph
- executor limits
- voice / STT / TTS
- debug flags
- search / browser / computer-use
- memory / retrieval

### 6. Startup self-check

Goal: Piper reports environment health at boot instead of failing mysteriously later.

Checks should include:
- Windows runtime `.venv` sanity
- required directories
- model paths
- llama server config
- voice dependencies available or cleanly disabled
- LangGraph checkpoint DB accessible
- unsafe runtime leftovers detected

### 7. Browser Computer Use v0 stabilization

Goal: stabilize browser-first computer use with deterministic proof before adding desktop automation.

Preferred order:
1. local fixture pages and harnesses
2. domain guard
3. structured `BROWSER_OP`
4. extraction-only tasks
5. form-fill tasks
6. download tasks
7. desktop expansion only after browser mode is boring

### 8. Task eval harness

Goal: measure whether Piper got the right result, not whether the persona sounded convincing.

Score from structured execution evidence:
- route accuracy
- stage completion truthfulness
- checker alignment
- final answer quality

### 9. Full-text search engine

Goal: complement vector recall with exact keyword and line retrieval.

Do after evals, because retrieval changes can alter behavior quietly.

### 10. Async background task queue

Goal: manage long-running work without freezing the chat loop.

Useful for:
- indexing
- ingestion
- long scripts
- large workspace operations

## Deferred Until Rails Are Stronger

- Morning Brief
- Workspace Tidy Suggestions
- Workspace Code Indexing
- Autonomous Tool Creation
- Desktop Computer Use
- Pattern Memory
- Avatar / UI polish

These are still useful. They are not first because they either depend on identity safety, execution evidence, or stronger release discipline.
