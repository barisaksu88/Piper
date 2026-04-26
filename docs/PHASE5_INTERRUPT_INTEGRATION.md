# Phase 5 — Interrupt Integration

## What Changed

Three files updated + one new test file. All changes are additive behind the existing `USE_LANGGRAPH_ORCHESTRATOR` feature flag (default `False`).

---

### 1. `core/graph_nodes.py`

**Added `await_interrupt_node`** — wraps `langgraph.types.interrupt()` to pause graph execution when user approval is required.

**Modified `verify_node`** — after MANAGER runs, `verify_node` now checks the orchestrator for:
- `pending_file_target_confirmation` → emits `missing_file_target_confirmation` interrupt
- `pending_stage_pause` → emits `stage_approval_pause` or `stage_user_input_pause` interrupt

If no pending approval, `interrupt_payload` stays `None` and the graph flows directly to PERSONA.

**Resume helpers** — `_apply_*_resume()` functions translate the user's post-interrupt response back into state the orchestrator understands (confirmed target path, approval boolean, user text).

---

### 2. `core/orchestrator_graph_builder.py`

**Added `AWAIT_INTERRUPT` node** to the graph.

**New conditional edge from VERIFY:**
```
VERIFY → interrupt_payload present  → AWAIT_INTERRUPT
VERIFY → interrupt_payload is None  → PERSONA
```

**Loop back from AWAIT_INTERRUPT:**
```
AWAIT_INTERRUPT → VERIFY
```
This ensures after the user responds, the graph re-runs VERIFY so the orchestrator can re-evaluate with the confirmed/denied state applied.

**Unchanged:** ROUTE → MANAGER / PERSONA conditional, PERSONA → END.

---

### 3. `core/orchestrator.py` — `_run_langgraph()`

**Resume support added:**
- Loads `langgraph_interrupt_record` at start of turn
- If `thread_id` matches and `langgraph_resume_value` is set, sends `Command(resume=...)` instead of fresh state
- Clears interrupt/recovery records on clean completion after resume

**Interrupt handling added:**
- After `graph.invoke()`, checks `_result_has_interrupt(result)`
- Calls `_record_pending_interrupt(...)` to persist the pause state to disk
- UI shows "Paused for approval." instead of crashing or spinning

**Error paths unchanged:** `OperationCancelled` re-raised, general exceptions recorded + raised.

---

## How to Apply

1. **Copy the files** into `core/` (backup your originals first):
   ```bash
   cp core/graph_nodes.py core/graph_nodes.py.bak
   cp core/orchestrator_graph_builder.py core/orchestrator_graph_builder.py.bak
   cp core/orchestrator.py core/orchestrator.py.bak
   ```

2. **Apply the Phase 5 replacements.**
   - `graph_nodes.py` → full replacement (adds `await_interrupt_node` + resume helpers)
   - `orchestrator_graph_builder.py` → full replacement (adds AWAIT_INTERRUPT node + edges)
   - `orchestrator.py` → replace only the `_run_langgraph()` method body (lines ~278–327 in current main)

3. **Run smoke tests:**
   ```bash
   python -m pytest tests/test_graph_interrupt_path.py -v
   ```

4. **Run existing harness (must still pass):**
   ```bash
   python -m compileall app.py config.py core ui memory tools llm AGENTS/harness scripts
   scripts/code_session_smoke_test.py --json
   scripts/file_edit_smoke_test.py --json
   ```

5. **Enable for a single turn to test live:**
   ```python
   # In config.py or env:
   USE_LANGGRAPH_ORCHESTRATOR = True
   ```

---

## Interrupt Flow (End-to-End)

```
User: "Write a file foo.txt with content bar"

ROUTE → decision: TASK
MANAGER → StageExecutor runs FILE_WORK stage
VERIFY → detects pending_stage_pause (approval needed)
       → sets interrupt_payload = {"kind": "stage_approval_pause", ...}
       → graph routes to AWAIT_INTERRUPT
AWAIT_INTERRUPT → calls interrupt(payload)
                → graph PAUSED, checkpoint saved
                → _record_pending_interrupt writes disk record
                → UI shows "Paused for approval."

[User clicks "Approve" in UI]

_run_langgraph() next turn:
  → sees matching thread_id in interrupt record
  → sees langgraph_resume_value = True
  → sends Command(resume=True) to graph

AWAIT_INTERRUPT (resumed) → resume_value = True
                          → applies approval to state
                          → routes back to VERIFY

VERIFY (second pass) → confirmation applied
                     → interrupt_payload cleared
                     → routes to PERSONA

PERSONA → "Done. I've written foo.txt with bar."
```

---

## Known Limitations / Phase 6+ Work

| Item | Status |
|------|--------|
| Interrupt UI integration (buttons, prompts) | Needs UI layer hook; out of scope for core graph |
| Timeout on pending interrupts | Not implemented; user must approve/deny manually |
| Retry after denial | Currently loops VERIFY→AWAIT_INTERRUPT indefinitely if user keeps denying. Need max-deny cap. |
| Checkpoint visualization / time-travel | Spec'd but not wired |

---

## Golden Corpus Note

Interrupt turns are inherently non-deterministic (user input required). Do **not** add them to the `corpus_graph_mode/` regression pack. Keep the golden corpus focused on fully-automated CHAT/SEARCH/TASK paths. Add a separate `corpus_graph_mode_interrupts/` folder if you want interrupt regression tests.
