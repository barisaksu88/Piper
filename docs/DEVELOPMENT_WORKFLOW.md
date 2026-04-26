# Piper Development Workflow

**Version:** 1.0  
**Date:** 2026-04-27  
**Status:** Active — reflects current tooling reality

---

## Toolchain

| Agent | Role | Access | When to Use |
|-------|------|--------|-------------|
| **Kimi (Web)** | Architect, reviewer, spec writer | GitHub, web search, file analysis | Any task requiring cross-file reasoning, repo-wide analysis, or planning |
| **Kimi Code (VS)** | Local implementation, daily driver | Local filesystem, terminal, IDE | Applying patches, running tests, refactoring, inline edits |
| **Codex** | Overflow specialist | Cloud API, metered | When Kimi Code is stuck, or for complex algorithm generation |

**Dropped:** GLM (redundant with Kimi web), Kimi Claw (unreliable).

---

## The 3-Phase Loop

### Phase 1: Intent → Spec (Kimi Web)

**You:** State your intention in plain language.  
*Example: "I want to remove the Codex engineering support module and harden FILE_WORK truthfulness."*

**Kimi Web does:**
1. Inspects the relevant files on GitHub (or you upload them)
2. Writes a surgical spec containing:
   - **Files to touch** (with paths)
   - **Current code → Desired code** (with exact context strings or line-number ranges)
   - **Files to delete** (if any)
   - **Guardrails:** what NOT to touch (scope boundaries)
   - **Test verification commands**

**Output:** A single copy-pasteable prompt block for Kimi Code.

---

### Phase 2: Local Implementation (Kimi Code)

**You:** Paste the prompt block into Kimi Code (VS) verbatim. Do not paraphrase.

**Kimi Code does:**
1. Reads the specified local files
2. Applies the exact changes from the spec
3. Runs the specified tests
4. Reports back: pass/fail per test, plus the final diff or commit SHA

**You:** Copy-paste Kimi Code's output (test results + diff summary) back to Kimi Web.

---

### Phase 3: Review & Ship (Kimi Web)

**Kimi Web does:**
1. Reviews the diff for correctness, completeness, and scope creep
2. Flags any issues (e.g., "This hunk adds behavior not in the spec")
3. If clean: says **"ship it"**
4. If issues: writes a correction prompt for Kimi Code (back to Phase 2)

**You:** Commit and push when Kimi Web approves.

---

## Communication Rules

### Between You and Kimi Web
- **Intent only.** Do not describe implementation details unless you have a specific preference.
- **Share results, not interpretations.** Paste raw test output and diffs. Kimi Web will interpret.
- **Upload files if GitHub is behind.** If your local branch has un-pushed changes, zip and upload the relevant files.

### Between You and Kimi Code
- **Copy-paste only.** Kimi Code receives prompts written by Kimi Web. Do not rephrase.
- **Report back verbatim.** When Kimi Code finishes, copy its full output (diff + test results) back to Kimi Web.

### Codex Overflow Rule
Use Codex only when:
1. Kimi Code gives 3 consecutive unsatisfactory answers on the same bug
2. You need a complex algorithm written from scratch (e.g., "implement a streaming JSON parser")
3. You have spare API credits and want a second opinion

**Default:** Kimi Code handles everything. Codex is the exception.

---

## Prompt Format (What Kimi Web Produces)

Every prompt for Kimi Code follows this template:

```
TASK: [One-line summary]

FILE: [path/to/file.py]
EDIT 1 (~line N, in function_name):
Change:
    [exact old code block]
To:
    [exact new code block]

EDIT 2 (~line N, in function_name):
Change:
    [exact old code block]
To:
    [exact new code block]

GUARDRAIL: [What not to touch]

COMMIT:
git add [files]
git commit -m "[conventional commit message]"
git push origin [branch]

TESTS TO RUN:
[exact commands]
```

---

## Scope Discipline

| Violation | Example |
|-----------|---------|
| **Scope creep** | Adding a new chat-behavior feature inside a commit titled "fix error handling" |
| **Doc drift** | Implementing a feature but not updating TRIGGER_FLOW.md or AGENTS.md |
| **Test gap** | Shipping code without running the smoke tests listed in the spec |

**Prevention:** Kimi Web's guardrails block scope creep. If Kimi Code adds unrequested behavior, Kimi Web flags it during Phase 3 review.

---

## Onboarding a New Session

If you (or a future collaborator) start a new session with Kimi Web:

1. **State the current branch:** *"Working on `langgraph-runtime` branch, Piper repo at github.com/barisaksu88/Piper"*
2. **State the current tooling:** *"Using Kimi Code for local implementation, Codex for overflow only."*
3. **Share the relevant commit:** *"Latest commit is `b2bd9a2` — Codex removal + truthfulness hardening."*

Kimi Web will read this document (if you link it) and immediately understand the workflow.

---

## Doc Alignment Checklist

After any significant feature ships, Kimi Web updates:
- [ ] `docs/architecture/TRIGGER_FLOW.md` — add §13 entry for new runtime behavior
- [ ] `AGENTS.md` — if behavior doctrine changes
- [ ] `docs/ROADMAP.md` — mark item as shipped, move to §13 in TRIGGER_FLOW
- [ ] This document (`DEVELOPMENT_WORKFLOW.md`) — if the workflow itself changes

---

## Current Known State (Auto-updated per session)

**Branch:** `main`  
**Latest commit:** `c96337c` — Archive superseded Three-Agent Flow document  
**Previous:** `ad10070` — Remove stale Codex references; prune ENGINEERING_SUPPORT_RULE  
**Rollback tag:** `v1.0-stable-pre-langgraph` on origin  
**Status:** Post-hygiene complete. Agentic spine rebuilt. Ready for new feature work.