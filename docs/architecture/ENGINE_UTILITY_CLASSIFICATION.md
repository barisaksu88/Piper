# Engine / Utility Classification

**Status:** Active reference  
**Scope:** Behavior-based classification of `core/engines/` modules

---

## Classification Rules

| Dimension | Lifecycle Engine | Direct-Call Utility |
|-----------|------------------|---------------------|
| Registry | Self-registers hooks or stages | No registry participation |
| Caller | Orchestrator discovers and invokes | Imported and called directly |
| State | May own persistent/runtime state | Stateless or pure helpers |
| Examples | `change_journal`, `proactive_monitor`, `stats_collector` | `search_workflow`, `summary`, `verification` |

---

## Current Classification

### Lifecycle Engines

- `change_journal.py` — persists file-change snapshots; orchestrator reads/writes via API
- `proactive_monitor.py` — schedules and fires proactive triggers
- `stats_collector.py` — accumulates turn-level timing and metrics

### Direct-Call Utilities

- `search_workflow.py` — **Utility** (direct-call, no registry). Pure helper/service methods for the search workflow lifecycle. Contains no LLM calls, no threading, no I/O, and no in-flight state management. May become hybrid later if tail blocks or route hooks are added.
- `summary.py` — direct-call summarization service
- `verification.py` — direct-call verification service
- `file_work.py` — direct-call file operation planner
- `followup_resolution.py` — direct-call follow-up resolution
- `route_clarity.py` — direct-call route clarification
- `state_mutation.py` — direct-call state mutation
- `conversation_compressor.py` — direct-call compression service
- `context_pack.py` — direct-call context pack builder
- `computer_use_engine.py` — direct-call computer-use orchestration
- `computer_use_verifier.py` — direct-call computer-use verification
- `rollback_engine.py` — direct-call rollback service

---

## Notes

- A module can migrate from Utility to Lifecycle if it later acquires registry hooks.
- The opposite migration (Lifecycle → Utility) is unlikely and should be discussed first.
- `AGENTS.md` remains the authority on architectural boundaries; this doc is a lookup reference.
