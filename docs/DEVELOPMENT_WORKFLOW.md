# Piper Development Workflow

**Version:** 2.0  
**Status:** Active — multi-agent assisted development doctrine  
**Primary rule:** Local tests, harnesses, logs, and Git diffs outrank every model opinion.

---

## 1. Purpose

This document defines how Baris, GPT, Kimi Web, Kimi Code, and Codex coordinate Piper development.

New GPT sessions should read this file first.

The goal is controlled progress without model-driven scope creep, while still allowing agents to discover useful adjacent issues. Agents are encouraged to look broadly, but they must edit narrowly and report discoveries rather than silently implement them.

---

## 2. Agent Roles

| Agent | Primary Role | Use For | Do Not Use For |
|-------|-------------|---------|----------------|
| **Baris** | Product owner and final human authority | Choosing goals, running Piper locally, approving risky changes, deciding when to merge | Manually patching code that an agent should handle |
| **GPT** | Release captain, final reviewer, doctrine guardrail | Sequencing work, reviewing pushed diffs/PRs, checking doctrine alignment, writing strict prompts for Kimi Code or Codex, deciding whether work is SHIP / FIX / SPLIT / REVERT / NEEDS EVIDENCE / SCOUT | Uncontrolled direct implementation when Kimi Code/Codex are already assigned, or replacing test evidence with opinion |
| **Kimi Web** | Scout, broad repo analyst, alternate architect, second-opinion reviewer | Fast repo-wide scouting, cross-file discovery, alternative architecture proposals, checking whether GPT/Kimi Code missed likely files, project-management style analysis before implementation | Final merge authority, declaring work "done" without tests/diffs, or overriding GPT review/test evidence |
| **Kimi Code** | High-limit local implementer | Large coding tasks, refactors, local file edits, running tests, iterative implementation | Unsupervised architecture decisions, skipping phases, merging without review |
| **Codex** | Corrective debugger and final wiring specialist | Fixing what Kimi Code missed, high-risk flow/state/orchestrator bugs, subtle integration failures, final pass on fragile behavior | Routine bulk coding when Kimi Code can handle it |
| **Local Harnesses / Tests** | Truth source | Proving behavior, catching regressions, validating migration phases | Being ignored because a model says "looks good" |

---

## 3. Default Development Loop

1. Baris chooses a goal.
2. GPT converts it into a scoped task or prompt.
3. Optional: Kimi Web scouts the repo or critiques the approach when broad analysis is useful.
4. Kimi Code implements locally.
5. Baris runs tests/harnesses and inspects obvious behavior.
6. If Kimi Code misses wiring or creates subtle breakage, Codex performs corrective debugging.
7. Baris pushes branch/commit to GitHub.
8. GPT reviews the diff, docs, tests, scope, and any Kimi Web findings.
9. GPT decides one of:
   - **SHIP**
   - **FIX** with Kimi Code
   - **ESCALATE** to Codex
   - **SPLIT**
   - **REVERT**
   - **NEEDS EVIDENCE**
   - **SCOUT** with Kimi Web
10. Only after review does Baris merge or continue.

```
Baris goal
    │
    ▼
GPT → scoped task / prompt
    │
    ├─→ Kimi Web (optional scout / critique)
    │
    ▼
Kimi Code → local implementation
    │
    ▼
Baris → tests / harnesses / local check
    │
    ├─→ breakage? → Codex → corrective debug
    │
    ▼
Baris → push to GitHub
    │
    ▼
GPT → review diff + docs + tests + scope
    │
    ▼
SHIP / FIX / ESCALATE / SPLIT / REVERT / NEEDS EVIDENCE / SCOUT
    │
    ▼
Baris → merge or continue
```

---

## 4. Controlled Exploration Policy

**Explore broadly. Edit narrowly. Report discoveries.**

Piper development does not forbid agents from looking beyond the immediate task. Good agents often find adjacent failures, missing tests, stale docs, or better implementation paths. The rule is not "never go out of scope"; the rule is "never silently implement out-of-scope changes."

**Allowed:**
- Reading related files
- Tracing call chains
- Identifying adjacent bugs
- Proposing follow-up tasks
- Reporting better approaches
- Recommending additional tests/docs

**Not allowed without approval:**
- Editing unrelated files
- Changing behavior outside the task
- Deleting fallback paths
- Mixing cleanup with feature work
- Silently expanding the commit
- Auto-advancing to the next phase

**Required report section:**

```
Out-of-scope findings:
- finding
- evidence
- recommended follow-up
- whether it blocks current task
```

---

## 5. Branch and Commit Discipline

- Prefer feature branches for non-trivial work.
- One conceptual change per commit.
- Do not mix architecture migration, feature work, and cleanup in one commit.
- Commit messages should be conventional and boring.
- Never rewrite or delete legacy fallback code during migrations until burn-in is complete.
- Before asking GPT for review, push the branch or provide the exact commit SHA.

---

## 6. Scope Control Rules

- Broad inspection is allowed.
- Narrow implementation is required.
- No opportunistic refactors without approval.
- No "while I'm here" edits.
- No phase skipping.
- No deleting old paths until feature-flag fallback and burn-in prove safety.
- If an agent changes files outside the task scope, it must justify why they were required.
- If tests were not run, the work is not done.
- If behavior changed but docs did not, the work is incomplete.

---

## 7. Evidence Required After Each Task

**Kimi Code or Codex must report:**
- Files changed
- Summary of changes
- Tests run
- Exact pass/fail output
- Known failures or skipped tests
- Commit SHA if committed
- Any behavior that could not be verified
- Out-of-scope findings, if any

**GPT review must check:**
- Diff scope
- Doctrine alignment
- Test evidence
- User-facing behavior risk
- Rollback path
- Whether docs need updates
- Whether Kimi Web scouting is needed before deciding

---

## 8. When to Use Codex

**Use Codex when:**
- Kimi Code gives repeated bad fixes on the same bug
- A change touches orchestration, routing, LangGraph, executor state, permissions, file safety, or verification
- Tests fail but the cause is not obvious
- The system works locally but behavior is subtly wrong
- Final wiring is needed after a broad Kimi Code implementation

**Do not use Codex for:**
- Routine docs
- Simple renames
- Bulk mechanical edits
- First-pass implementation when Kimi Code is available

---

## 9. When to Use Kimi Web

**Use Kimi Web when:**
- Broad repo scouting is useful
- GPT or Baris wants an independent second opinion
- You want alternative architecture proposals
- You want fast cross-file discovery before implementation
- You want project-manager-style analysis, risk assessment, or roadmap suggestions before GPT's final review

Kimi Web may suggest plans, identify missing files, or critique work. GPT remains the final reviewer/release captain before merge.

---

## 10. LangGraph / Orchestrator Migration Rules

- Phase-based only.
- Phase 0 golden harness must exist before risky migration.
- Golden corpus and semantic comparisons protect behavior.
- Legacy orchestrator fallback must remain until burn-in is complete.
- LangGraph and legacy runtimes must produce equivalent outcomes.
- Do not delete `orchestrator_phases.py` until explicit burn-in criteria are met.
- Migration changes must not be mixed with unrelated feature work.

Key files:
- `core/orchestrator.py`
- `core/orchestrator_phases.py`
- `core/orchestrator_graph_builder.py`
- `core/graph_nodes.py`
- `docs/architecture/TRIGGER_FLOW.md`

---

## 11. Doctrine Alignment

After significant changes, check:
- `AGENTS.md`
- `docs/architecture/TRIGGER_FLOW.md`
- `docs/ROADMAP.md`
- `docs/DEVELOPMENT_WORKFLOW.md`

- **TRIGGER_FLOW.md** describes runtime behavior.
- **AGENTS.md** describes doctrine/operating rules.
- **ROADMAP.md** tracks planned and shipped work.
- **DEVELOPMENT_WORKFLOW.md** describes how the human + model toolchain works.

---

## 12. Review Outcomes

GPT should label reviews as one of:

- **SHIP** — safe to merge
- **FIX** — targeted corrections needed
- **SPLIT** — change is too broad and must be broken up
- **REVERT** — unsafe or wrong direction
- **NEEDS EVIDENCE** — tests/logs/diff insufficient
- **SCOUT** — ask Kimi Web for broad analysis before deciding

---

## 13. New Session Onboarding

A new GPT session should be started with:
- Link to this file
- Current branch
- Latest commit SHA
- What changed since last review
- Test results if any
- Current goal

**Starter template:**

> "Read docs/DEVELOPMENT_WORKFLOW.md first. You are acting as Piper release captain, final reviewer, and doctrine guardrail. Current branch: `<branch>`. Latest commit: `<sha>`. Goal: `<goal>`. Review against AGENTS.md and docs/architecture/TRIGGER_FLOW.md where relevant. Use Kimi Web as scout/second-opinion analyst when broad repo analysis is useful."

---

## 14. Anti-Patterns

- **Model soup:** asking multiple agents to decide at once without a clear final reviewer
- **Implementation without tests**
- **Architecture rewrite hidden inside bugfix**
- **Docs updated without code reality**
- **Code changed without docs when behavior changes**
- **Trusting summaries over diffs**
- **Treating a green test as proof of full behavior if the test is too narrow**
- **Letting autonomous agents continue to the next phase without human/GPT review**
- **Silently implementing out-of-scope discoveries instead of reporting them**

