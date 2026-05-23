# Evidence Ledgers

This folder holds branch-specific evidence ledgers for risky work.

Use an evidence ledger when:
- a branch changes high-risk runtime behavior
- GPT review needs proof of what was actually tested
- a branch is `NEEDS_EVIDENCE` rather than immediately `SHIP`
- manual validation matters in addition to smoke/harness checks

Evidence ledgers are not:
- architecture docs
- doctrine
- known-issues lists
- roadmap/spec files

## What An Evidence Ledger Records

An evidence ledger records what was actually proven on a branch from:
- local commands
- harness output
- manual tests
- Git diffs

It must not rely on model opinion as proof.

## Required Fields

Each evidence ledger should include:
- branch
- PR link if available
- goal
- risk area
- commands run
- result summary
- manual tests performed
- unresolved risks
- reviewer verdict
- date
- latest commit SHA

## How It Differs From Known Issues

- `notes/known-issues.md` records what is currently broken or risky in the repo generally.
- `notes/evidence/*.md` records what a specific branch actually proved.

Known issues answer:
- what is still wrong?

Evidence ledgers answer:
- what did this branch prove locally, and what still requires manual confidence?

## Relationship To Local Gates

Evidence ledgers complement:
- `python scripts/check_repo_hygiene.py --json`
- `python scripts/release_gate.py --json`

Those scripts provide a branch-status snapshot.
The evidence ledger captures the concrete test and manual proof behind that snapshot, especially when the release gate says `NEEDS_EVIDENCE`.

## Rule

If a branch is high-risk, do not rely on "looks good" or model approval alone.
Capture the local evidence here before asking for final review.
