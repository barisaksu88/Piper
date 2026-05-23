# Search Workflow Helper Extraction Plan

**Status:** Completed on mainline (`audit/search-workflow-engine-mainline`)
**Scope:** Extract pure helpers from `core/orchestrator_phases.py` into `core/engines/search_workflow.py`

---

## What Was Extracted

`core/engines/search_workflow.py` contains a direct-call utility class `SearchWorkflowEngine` with the following pure helpers:

- `build_search_failure_summary(query, error_text)`
- `summarize_search_error_for_user(error_text)`
- `build_search_in_flight_reply(active_query, requested_query)`
- `build_search_first_pass_rule(query)`
- `build_search_first_pass_fallback(query)`
- `build_search_preview_history(user_msg, query)` — includes source-choice rewrite logic
- `build_search_report_history(history, user_msg)` — includes summary query regex extraction

Plus higher-level context builders:

- `prepare_reporter_context(recent_history)` → `SearchReporterContext`
- `prepare_preview_context(user_msg, query)` → `SearchPreviewContext`

## Mainline-Specific Behavior Preserved

The extraction preserves all current mainline behavior that evolved since the original audit branch:

1. **Externally-verifiable-facts sentence** in `build_search_first_pass_rule`
2. **Source-choice rewrite** in `build_search_preview_history` via `_SEARCH_PREVIEW_SOURCE_CHOICE_WORDS` / `_SEARCH_PREVIEW_SOURCE_WORDS`
3. **Summary query regex extraction** in `build_search_report_history` via `_SEARCH_SUMMARY_QUERY_RE`
4. **Search trace logging** remains in `phase_search` (not extracted)
5. **`orc.latest_search_query = query`** remains in `phase_reporter` (not extracted)

## Compatibility Wrappers

`core/orchestrator_phases.py` retains thin wrappers delegating to `_SEARCH_WORKFLOW_ENGINE`:

- `_build_search_failure_summary`
- `_summarize_search_error_for_user`
- `_build_search_in_flight_reply`
- `_build_search_first_pass_rule`
- `_build_search_first_pass_fallback`
- `_build_search_preview_history`
- `_build_search_report_history`

## What Remains in Orchestrator

The following runtime concerns were intentionally **not** extracted:

- LLM streaming (`_stream_or_capture_persona_answer_text_only`)
- Threading / background search (`_do_search`)
- Stats collection
- UI events (`orc.ui.put`)
- Cancel tokens
- `search_in_flight` state
- `perform_search` call
- `_search_trace` closure
- Chat mutation (`orc.chat.replace_last_assistant_content`)
- `orc.latest_search_query` assignment

## Tests

- `tests/test_search_contracts.py` — wire-format contract tests
- `tests/test_search_workflow_engine.py` — 108 pure-helper tests

Run with:
```bash
python -m pytest tests/test_search_contracts.py tests/test_search_workflow_engine.py -v
```

## Classification

Per `AGENTS.md` and `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md`:
- `SearchWorkflowEngine` is a **Utility** (direct-call, no registry)
- May become hybrid later if tail blocks / route hooks are added
