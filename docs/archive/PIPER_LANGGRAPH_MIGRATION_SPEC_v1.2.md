# Piper LangGraph Migration Spec v1.2

**Status:** Architecture-approved. **BLOCKED on Phase 0 completion.**  
**Branch:** `main` (post-merge `c96337c`)  
**Goal:** Replace custom 2500-line orchestrator spaghetti with proven LangGraph state graph patterns while preserving Piper-native moat (safety, persona, permissions, voice)

---

## Philosophy

> Stop hand-rolling infrastructure. Adopt proven methods. Keep product-layer native. **Migrate with instruments, not blind faith. And enforce discipline — no skipping phases because "it looks fine."**

---

## GPT Safety Amendments (v1.1 → v1.2)

### Amendment 1: Semantic Comparison, Not Byte Match

**The trap:** "Exact match" on LLM-generated output is impossible. Timestamps, log order, temperature variance, and phrasing will diverge.

**The fix:** Define comparison rules per field:

| Field | Comparison Rule | Why |
|-------|----------------|-----|
| `route_decision` | **Exact string match** | Routing is deterministic code, not LLM output |
| `stage_transitions` | **Exact ordered match** | Sequence is code-controlled |
| `tool_calls` | **Exact match (args normalized)** | Tool selection and arguments are deterministic |
| `tool_results` | **Normalize then compare** | Strip timestamps, random IDs, temp paths before compare |
| `pre_persona_output` | **Exact match** | The structured content before persona wrapping |
| `persona_output` | **Skip comparison** or **semantic equivalence** | LLM phrasing variance is expected; compare only if using fixed seed |
| `workspace_state` | **Set equality** | File lists may differ in order |
| `checkpoint_id` | **Presence check only** | IDs are implementation-defined |

**Normalization rules for tool results:**
```python
def normalize_tool_result(result: dict) -> dict:
    """Strip non-deterministic fields before comparison."""
    result = copy.deepcopy(result)
    # Remove timestamps
    for key in ["timestamp", "created_at", "modified_at", "accessed_at"]:
        result.pop(key, None)
    # Remove random/temp IDs
    if "id" in result and looks_like_random_id(result["id"]):
        result["id"] = "<UUID>"
    # Normalize temp paths
    if "path" in result:
        result["path"] = normalize_temp_path(result["path"])
    return result
```

### Amendment 2: Persona Stage Is Non-Deterministic

**The trap:** Comparing raw persona output will fail every test because LLMs are not deterministic.

**The fix:** Golden corpus captures **pre-persona output** (the structured response before voice/persona wrapping) and compares that. Persona output is verified separately with:
- Fixed seed mode (`temperature=0`, `seed=42`) for testing, OR
- Semantic checks ("contains key facts from pre-persona output"), OR
- Manual spot-checking (not automated regression)

```python
class GoldenTurn:
    # ... existing fields ...
    pre_persona_output: str  # The structured content BEFORE persona/voice
    persona_output: str        # Captured for reference only, not auto-compared
```

### Amendment 3: Interrupt Test — Change Input on Resume

**The trap:** Only testing "pause → resume → finish" misses the real user behavior: changing their mind during the pause.

**The fix:** Add this golden case:

```python
def test_interrupt_with_changed_input():
    """User pauses at PERSONA, then changes their request before resuming."""
    # Start turn
    thread_id = "test_changed_input"
    config = {"configurable": {"thread_id": thread_id}}
    
    # Run until interrupt (before PERSONA)
    result = graph.invoke({"messages": [("human", "write a poem")]}, config)
    assert result["stage"] == "PERSONA_INTERRUPTED"
    
    # User changes mind: "actually write a haiku instead"
    # Resume with modified state
    new_state = {**result, "messages": result["messages"] + [("human", "actually write a haiku instead")]}
    final = graph.invoke(new_state, config)
    
    # Verify the resumed run used the NEW input
    assert "haiku" in final["pre_persona_output"].lower()
```

---

## Revised Migration Plan

### Phase 0: Golden Harness (1–2 days) — **DO NOT SKIP**

**Goal:** Build regression corpus before touching any orchestrator code.

**Tasks:**
1. Create `tests/golden/record_piper_turns.py`
2. Run 10–20 real Piper sessions covering diverse cases
3. Store `tests/golden/corpus/turn_001.json` through `turn_020.json`
4. Write `tests/golden/compare_turns.py` with **semantic comparison rules** (not byte match)
5. Add `pre_persona_output` capture to every turn
6. Add normalization for tool results
7. Commit this harness to `main` FIRST

**Golden corpus cases:**
- Simple chat (no tools)
- File read (within workspace)
- File write (within workspace)
- File request (outside workspace — should jail)
- Code generation
- Ambiguous input (should trigger clarification)
- Multi-turn conversation (memory test)
- Search/internet request (permission gate)
- Interrupt roundtrip (pause → resume)
- **Interrupt with changed input (pause → edit → resume)** ← Amendment 3

**Tag:** `v1.1-golden-harness`

---

### Phase 1: Extract ROUTE Node (1 day)

**New file:** `core/graph_nodes.py`

```python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class PiperState(TypedDict):
    """LangGraph state schema — mirrors current orchestrator state."""
    messages: Annotated[list, add_messages]
    stage: str
    route_decision: str | None
    manager_result: dict | None
    verification_passed: bool
    pre_persona_output: str | None  # Structured content before persona wrapping
    persona_output: str | None        # Final voiced output (non-deterministic)
    workspace_path: str

def route_node(state: PiperState) -> PiperState:
    """ROUTE stage — decide which domain handles the user input."""
    # ... existing ROUTE logic from orchestrator_phases.py
    return {**state, "stage": "ROUTE", "route_decision": decision}
```

**Verification:**
- Run `compare_turns.py` with **semantic rules**
- Assert: `route_decision` exact match for all 20 turns
- If mismatch: debug, fix, re-run until 100% match

**Commit:** `feat(graph): extract route_node with golden corpus verification`
**Tag:** `v1.2-route-extracted`

---

### Phase 2: Extract MANAGER Node (1 day)

```python
def manager_node(state: PiperState) -> PiperState:
    """MANAGER stage — execute the decided route."""
    return {**state, "stage": "MANAGER", "manager_result": result}
```

**Verification:** Compare tool calls + normalized tool results against golden corpus.

**Commit:** `feat(graph): extract manager_node`
**Tag:** `v1.3-manager-extracted`

---

### Phase 3: Extract VERIFY + PERSONA Nodes (1 day)

```python
def verify_node(state: PiperState) -> PiperState:
    return {**state, "stage": "VERIFY", "verification_passed": passed}

def persona_node(state: PiperState) -> PiperState:
    """PERSONA stage — apply voice/persona to pre_persona_output."""
    return {**state, "stage": "PERSONA", "persona_output": voiced_result}
```

**Verification:**
- `verification_passed`: exact match
- `pre_persona_output`: exact match (not persona_output — see Amendment 2)

**Commit:** `feat(graph): extract verify_node and persona_node`
**Tag:** `v1.4-all-nodes-extracted`

---

### Phase 4: Build Graph Behind Feature Flag (1 day)

**New file:** `core/orchestrator_graph_builder.py`

```python
from langgraph.graph import StateGraph, END
from core.graph_nodes import route_node, manager_node, verify_node, persona_node, PiperState

def verification_router(state: PiperState) -> str:
    if state["verification_passed"]:
        return "PERSONA"
    return "MANAGER"

def build_piper_graph() -> StateGraph:
    builder = StateGraph(PiperState)
    builder.add_node("ROUTE", route_node)
    builder.add_node("MANAGER", manager_node)
    builder.add_node("VERIFY", verify_node)
    builder.add_node("PERSONA", persona_node)
    builder.set_entry_point("ROUTE")
    builder.add_edge("ROUTE", "MANAGER")
    builder.add_edge("MANAGER", "VERIFY")
    builder.add_conditional_edges("VERIFY", verification_router, {"PERSONA": "PERSONA", "MANAGER": "MANAGER"})
    builder.add_edge("PERSONA", END)
    return builder.compile(checkpointer=...)
```

**Feature flag in `core/orchestrator.py`:**
```python
USE_LANGGRAPH_ORCHESTRATOR: bool = _env_flag("PIPER_USE_LANGGRAPH_ORCHESTRATOR", False)

if CFG.USE_LANGGRAPH_ORCHESTRATOR:
    from core.orchestrator_graph_builder import build_piper_graph
    graph = build_piper_graph()
    result = graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
else:
    from core.orchestrator_phases import run_phase_loop
    result = run_phase_loop(state)
```

**Verification:** Golden corpus passes through graph identically (per semantic rules).

**Commit:** `feat(graph): add LangGraph orchestrator behind feature flag`
**Tag:** `v1.5-graph-wired`

---

### Phase 5: Interrupt / Checkpoint Integration (1 day)

```python
graph = builder.compile(
    checkpointer=sqlite_saver,
    interrupt_before=["PERSONA"],
)
```

**Verification:**
1. Golden interrupt case: pause → resume → finish
2. **Amendment 3 case:** pause → change input → resume → verify new input used

**Commit:** `feat(graph): LangGraph interrupt integration`
**Tag:** `v1.6-interrupt-integrated`

---

### Phase 6: Visual Debug Traces (0.5 day)

```python
def save_piper_graph_visualization(graph, path: Path):
    try:
        png = graph.get_graph().draw_mermaid_png()
        path.write_bytes(png)
    except Exception:
        text = graph.get_graph().draw_mermaid()
        path.write_text(text)
```

**Config:** `DEBUG_LANGGRAPH_VISUALIZE: bool`

**Commit:** `feat(graph): add visual graph debugging`
**Tag:** `v1.7-visual-debug`

---

### Phase 7: Repo Indexing — Steal Pattern (2–3 days)

**New file:** `core/indexing.py`

**Commit:** `feat(tools): add workspace semantic indexing`
**Tag:** `v1.8-indexing-added`

---

### Phase 8: Burn-In and Old Orchestrator Deletion (2+ weeks)

**Requirements to flip default:**
- [ ] `USE_LANGGRAPH_ORCHESTRATOR=True` in your local config for 2+ weeks
- [ ] Zero golden corpus divergences (per semantic rules)
- [ ] Zero user-reported routing regressions
- [ ] All smoke tests pass

**Then:** Change default to `True`.

**After 1 more week with no issues:**
```bash
git rm core/orchestrator_phases.py
git commit -m "refactor: delete legacy orchestrator_phases.py after burn-in"
```

**Tag:** `v2.0-langgraph-default`

---

## Enforcing Discipline With Kimi Code

**The risk:** Kimi Code (or any agentic model) will want to "just implement everything quickly 😄" and skip phases.

**The fix:** Strict prompt engineering. Every Phase N prompt must include:

```text
DISCIPLINE RULES:
1. ONLY implement the phase listed in the TASK header. Do NOT touch code for other phases.
2. Do NOT add "nice to have" features. No visualization, no indexing, no extra nodes.
3. The verification section is MANDATORY, not optional. If verification fails, STOP and report.
4. Do NOT delete, rename, or modify any file not explicitly listed in the FILES TO EDIT section.
5. If you think something should be done differently, ASK before doing it. Do not improvise.
6. After verification, commit with the EXACT commit message provided. Do not embellish.
```

**Additional enforcement:**
- **Small prompt scope:** Each prompt is one phase, one node, one file
- **Explicit guardrails:** "Do NOT add X, Y, Z" in every prompt
- **Mandatory report back:** Kimi Code must show verification output before claiming success
- **No auto-advance:** You (the human) must review Phase N results before I write Phase N+1 prompt

---

## Immediate Next Step

**Phase 0: Golden Harness**

Create `tests/golden/record_piper_turns.py`. Run it against current `main` to capture 10–20 real Piper sessions with **pre_persona_output** and **normalized tool results**. Do NOT touch `orchestrator_phases.py` until this harness exists and the corpus is committed.

When the harness is done, paste the commit SHA here and I'll write the Phase 1 prompt.

---

## Approved By

- **Architect:** Kimi Web (system reasoning)
- **Safety Reviewer:** GPT (second opinion — Phase 0, feature flags, burn-in, semantic comparison)
- **Implementer:** Kimi Code (VS Code agent) — pending, with strict discipline prompts
- **Rollback:** Tagged at every phase, feature flag protects all the way to Phase 8

**Status:** Architecture-approved. **BLOCKED on Phase 0 completion before implementation begins.**
