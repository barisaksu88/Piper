---
**ARCHIVED:** Superseded by `docs/DEVELOPMENT_WORKFLOW.md` as of 2026-04-27.
The Three-Agent Flow (GLM + Codex + Claude) is no longer the active development model.
---

You pick next roadmap item
        │
        ▼
┌──────────────────────────────────────────────┐
│  PHASE 1: SPEC  (GLM)                        │
│                                              │
│  • Write the full ROADMAP spec entry         │
│  • Cross-referenced against TRIGGER_FLOW.md  │
│  • Edge cases surfaced from doctrine         │
│  • Generate it as a document you drop        │
│    into docs/                                │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  PHASE 2: LOCAL PREFLIGHT  (Claude)          │
│                                              │
│  • You give Claude the spec + tell it to     │
│    read the actual local files               │
│  • Claude validates the spec against the     │
│    REAL codebase (not GitHub, which may      │
│    be behind your local state)               │
│  • Claude flags: "spec says modify X but     │
│    X was refactored 3 days ago, here's the   │
│    new location"                             │
│  • Claude prepares the context package for   │
│    Codex — which files to read, which        │
│    sections of AGENTS.md matter, what the    │
│    harness expectations are                  │
│  • Output: a Codex-ready prompt with         │
│    scoped file list + constraints            │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  PHASE 3: IMPLEMENT  (Codex)                 │
│                                              │
│  • Codex works from the scoped prompt        │
│  • Writes code, runs harnesses locally       │
│  • Iterates until green                      │
│  • You commit when satisfied                 │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  PHASE 4: LOCAL SMOKE TEST  (Claude)         │
│                                              │
│  • Claude runs the full harness pack         │
│    (the one from AGENTS.md §17A)             │
│  • Claude checks for regressions in          │
│    areas the spec didn't explicitly touch    │
│  • Quick doctrinal check: does the           │
│    implementation violate any §12 rules?     │
│  • If issues: fix or back to Phase 3         │
│  • If clean: you push to GitHub              │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  PHASE 5: DEEP AUDIT  (GLM)                  │
│                                              │
│  • Re-pull the repo from GitHub              │
│  • Full alignment check: spec vs code vs     │
│    TRIGGER_FLOW.md                           │
│  • Cross-file cascade analysis               │
│  • Check things Claude might miss at         │
│    local scope: does this change create      │
│    a new cross-layer import? Does it add     │
│    a new re-route path not in §9?            │
│  • Output: alignment report                  │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  PHASE 6: RETIRE  (GLM + You)                │
│                                              │
│  • If aligned: GLM drafts the §13 retirement │
│    entry for TRIGGER_FLOW.md                 │
│  • Update the ROADMAP (mark as shipped)      │
│  • If diverged: specific notes back to       │
│    Phase 3 with exact files/lines            │
└──────────────────────────────────────────────┘