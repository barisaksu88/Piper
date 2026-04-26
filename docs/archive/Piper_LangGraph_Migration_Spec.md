# PIPER LANGGRAPH MIGRATION

## Implementation Specification

**Version 1.0 | April 2026**

---

## 1. Executive Summary

This document specifies the migration of Piper's agentic loop infrastructure to LangGraph. The current hand-rolled implementation suffers from reliability issues including flaky state management, poor crash recovery, and inconsistent loop behavior. LangGraph provides a battle-tested state machine framework with built-in checkpointing, interrupts, and durable execution patterns that directly address these pain points.

The migration is designed as a drop-in replacement for the loop orchestration layer while preserving all existing Piper domain logic. Your current ROUTE, MANAGER, VERIFY, and PERSONA nodes remain unchanged—they simply become nodes in a LangGraph state graph. This approach minimizes risk while gaining significant infrastructure improvements.

**Key outcomes:** reliable state transitions, crash recovery via checkpoints, human-in-the-loop interrupts, time-travel debugging, and production-ready retry/timeout patterns.

---

## 2. Current State Analysis

### 2.1 Existing Architecture

Piper currently uses a custom-built agentic loop with the following flow:

| Stage | Function |
|-------|----------|
| ROUTE | Determines intent and domain routing |
| MANAGER | Orchestrates tool calls and LLM interactions |
| VERIFY | Validates outputs against constraints |
| PERSONA | Applies voice and personality transformation |

This logic is sound, but the execution infrastructure around it is hand-rolled and prone to edge-case failures.

### 2.2 Identified Pain Points

| Issue | Description |
|-------|-------------|
| **Flaky state transitions** | Race conditions and edge cases in state management cause unpredictable loop behavior under load or during multi-step operations |
| **No crash recovery** | If Piper crashes mid-execution, all context is lost. Users must restart from the beginning |
| **Weak interrupt handling** | Human-in-the-loop patterns are bolted on rather than first-class, leading to awkward pause/resume semantics |
| **Ad-hoc retry logic** | Each node implements its own retry strategy inconsistently, making debugging and tuning difficult |
| **No execution visibility** | Debugging loop behavior requires manual logging; there is no structured way to inspect or replay state history |

### 2.3 What Works Well

The domain logic itself is solid and should be preserved:

- The **ROUTE** decision logic accurately classifies user intent
- The **MANAGER** tool orchestration handles complex multi-step workflows
- The **VERIFY** system catches output quality issues
- The **PERSONA** transformation produces consistent, high-quality voice output

These are Piper's differentiators and remain untouched by this migration.

---

## 3. Target Architecture

### 3.1 LangGraph Integration Model

LangGraph becomes the orchestration engine while Piper retains its identity as the assistant product.

**Mental model shift:** "Piper has a loop" → "Piper runs on LangGraph"

This separation allows Piper to focus on product-level concerns while LangGraph handles infrastructure-level reliability.

### 3.2 State Graph Design

The Piper agentic loop maps directly to a LangGraph state graph:

| Node | Function | State Changes |
|------|----------|---------------|
| `ENTRY` | Initialize state, validate input | Sets `user_input`, `session_id`, `timestamp` |
| `ROUTE` | Classify intent, determine domain | Sets `route_decision`, `target_domain` |
| `MANAGER` | Execute tools, orchestrate LLM calls | Sets `tool_calls`, `llm_response`, `intermediate_state` |
| `VERIFY` | Validate output quality | Sets `verified: boolean`, `issues: list`, `retry_count` |
| `PERSONA` | Apply voice and personality | Sets `final_output`, `persona_metadata` |
| `EXIT` | Persist final state, return response | Clears transient state, logs completion |

### 3.3 Edge Definitions

Edges define the valid transitions between nodes. Conditional edges enable branching logic:

| From | To | Condition |
|------|-----|-----------|
| `ENTRY` | `ROUTE` | Always |
| `ROUTE` | `MANAGER` | `route_decision != 'direct_response'` |
| `ROUTE` | `PERSONA` | `route_decision == 'direct_response'` |
| `MANAGER` | `VERIFY` | Always |
| `VERIFY` | `PERSONA` | `verified == true` |
| `VERIFY` | `MANAGER` | `verified == false AND retry_count < max_retries` |
| `VERIFY` | `EXIT` | `verified == false AND retry_count >= max_retries` |
| `PERSONA` | `EXIT` | Always |

### 3.4 Checkpoint Strategy

LangGraph checkpoints enable crash recovery and time-travel debugging:

| Checkpointer | Use Case | Characteristics |
|--------------|----------|-----------------|
| `MemorySaver` | Development & testing | In-memory, fast, lost on restart |
| `SqliteSaver` | Single-instance production | Persistent, simple, no external dependencies |
| `PostgresSaver` | Distributed production | Shared across instances, supports horizontal scaling |

Checkpoints are saved after each node execution, allowing recovery from any point in the graph. The checkpoint includes:
- Complete state dictionary
- Current node position
- Execution metadata (timestamps, retry counts)

### 3.5 Interrupt Points

Human-in-the-loop is implemented via LangGraph interrupts.

**Strategic interrupt points** are placed at `VERIFY` node when user confirmation is required for:
- File modifications
- External API calls
- Financial transactions

**Interrupt flow:**
1. Graph pauses execution
2. State persisted to checkpoint
3. Control returned to caller with `requires_confirmation` flag
4. Caller collects user input
5. Execution resumes with checkpoint ID and confirmation response

---

## 4. Step-by-Step Implementation

### 4.1 Phase 1: Dependency Setup

#### Step 1.1: Install LangGraph

```bash
pip install langgraph langgraph-checkpoint-sqlite langgraph-checkpoint-postgres
```

#### Step 1.2: Verify Installation

```python
# test_langgraph_install.py
from langgraph.graph import StateGraph, END
from typing import TypedDict

class TestState(TypedDict):
    value: int

def increment(state: TestState) -> TestState:
    return {"value": state["value"] + 1}

graph = StateGraph(TestState)
graph.add_node("increment", increment)
graph.add_edge("increment", END)
graph.set_entry_point("increment")
app = graph.compile()

result = app.invoke({"value": 0})
assert result["value"] == 1
print("LangGraph installation verified successfully.")
```

---

### 4.2 Phase 2: Define State Schema

#### Step 2.1: Create PiperState TypedDict

```python
# piper/state.py
from typing import TypedDict, List, Optional, Any, Dict
from datetime import datetime

class PiperState(TypedDict):
    # Input
    user_input: str
    session_id: str
    timestamp: str
    
    # Routing
    route_decision: str  # 'direct_response', 'tool_workflow', 'clarification'
    target_domain: str   # 'assistant', 'dev', 'computer_use'
    route_confidence: float
    
    # Manager execution
    tool_calls: List[Dict[str, Any]]
    llm_response: str
    intermediate_state: Dict[str, Any]
    execution_log: List[str]
    
    # Verification
    verified: bool
    issues: List[str]
    retry_count: int
    max_retries: int
    
    # Persona
    final_output: str
    persona_metadata: Dict[str, Any]
    
    # Control flow
    requires_confirmation: bool
    confirmation_type: Optional[str]
    confirmation_response: Optional[bool]
    
    # Error handling
    error: Optional[str]
    error_node: Optional[str]
```

#### Step 2.2: Create State Factory

```python
# piper/state.py (continued)
import uuid

def create_initial_state(user_input: str, session_id: str = None) -> PiperState:
    return PiperState(
        user_input=user_input,
        session_id=session_id or str(uuid.uuid4()),
        timestamp=datetime.utcnow().isoformat(),
        route_decision="",
        target_domain="assistant",
        route_confidence=0.0,
        tool_calls=[],
        llm_response="",
        intermediate_state={},
        execution_log=[],
        verified=False,
        issues=[],
        retry_count=0,
        max_retries=3,
        final_output="",
        persona_metadata={},
        requires_confirmation=False,
        confirmation_type=None,
        confirmation_response=None,
        error=None,
        error_node=None
    )
```

---

### 4.3 Phase 3: Wrap Existing Nodes

#### Step 3.1: Create Node Wrappers

Each existing Piper function becomes a LangGraph node. The wrapper extracts needed values from state, calls the existing logic, and returns state updates.

```python
# piper/nodes.py
from piper.state import PiperState
from piper.existing_logic import (
    route_intent,      # Your existing ROUTE logic
    manage_workflow,   # Your existing MANAGER logic  
    verify_output,     # Your existing VERIFY logic
    apply_persona      # Your existing PERSONA logic
)

def entry_node(state: PiperState) -> dict:
    """Initialize and validate input."""
    if not state["user_input"]:
        return {"error": "Empty input", "error_node": "entry"}
    
    state["execution_log"].append(f"Entry: {state['user_input'][:50]}...")
    return {"execution_log": state["execution_log"]}

def route_node(state: PiperState) -> dict:
    """Wrap existing ROUTE logic."""
    try:
        result = route_intent(
            user_input=state["user_input"],
            context=state.get("intermediate_state", {})
        )
        return {
            "route_decision": result.decision,
            "target_domain": result.domain,
            "route_confidence": result.confidence,
            "execution_log": state["execution_log"] + [f"Route: {result.decision}"]
        }
    except Exception as e:
        return {"error": str(e), "error_node": "route"}

def manager_node(state: PiperState) -> dict:
    """Wrap existing MANAGER logic."""
    try:
        result = manage_workflow(
            user_input=state["user_input"],
            route_decision=state["route_decision"],
            target_domain=state["target_domain"],
            session_id=state["session_id"]
        )
        return {
            "tool_calls": result.tool_calls,
            "llm_response": result.response,
            "intermediate_state": result.intermediate_state,
            "execution_log": state["execution_log"] + [f"Manager: {len(result.tool_calls)} tools"]
        }
    except Exception as e:
        return {"error": str(e), "error_node": "manager"}

def verify_node(state: PiperState) -> dict:
    """Wrap existing VERIFY logic."""
    try:
        result = verify_output(
            response=state["llm_response"],
            tool_calls=state["tool_calls"],
            constraints=state.get("intermediate_state", {}).get("constraints", {})
        )
        
        updates = {
            "verified": result.passed,
            "issues": result.issues,
            "execution_log": state["execution_log"] + [f"Verify: {'PASS' if result.passed else 'FAIL'}"]
        }
        
        if not result.passed:
            updates["retry_count"] = state["retry_count"] + 1
            
        return updates
    except Exception as e:
        return {"error": str(e), "error_node": "verify"}

def persona_node(state: PiperState) -> dict:
    """Wrap existing PERSONA logic."""
    try:
        result = apply_persona(
            response=state["llm_response"],
            context=state.get("intermediate_state", {}),
            session_id=state["session_id"]
        )
        return {
            "final_output": result.output,
            "persona_metadata": result.metadata,
            "execution_log": state["execution_log"] + ["Persona: applied"]
        }
    except Exception as e:
        return {"error": str(e), "error_node": "persona"}

def exit_node(state: PiperState) -> dict:
    """Finalize and clean up."""
    state["execution_log"].append("Exit: complete")
    return {"execution_log": state["execution_log"]}
```

---

### 4.4 Phase 4: Build the Graph

#### Step 4.1: Create Graph Definition

Assemble the nodes and edges into a complete state graph.

```python
# piper/graph.py
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from piper.state import PiperState, create_initial_state
from piper.nodes import (
    entry_node, route_node, manager_node,
    verify_node, persona_node, exit_node
)
import sqlite3

def build_piper_graph(checkpointer_type: str = "memory"):
    """Build and compile the Piper state graph."""
    
    # Create the graph
    graph = StateGraph(PiperState)
    
    # Add nodes
    graph.add_node("entry", entry_node)
    graph.add_node("route", route_node)
    graph.add_node("manager", manager_node)
    graph.add_node("verify", verify_node)
    graph.add_node("persona", persona_node)
    graph.add_node("exit", exit_node)
    
    # Set entry point
    graph.set_entry_point("entry")
    
    # Add edges
    graph.add_edge("entry", "route")
    
    # Conditional routing after ROUTE
    def route_decision(state: PiperState) -> str:
        if state.get("error"):
            return "exit"
        if state["route_decision"] == "direct_response":
            return "persona"
        return "manager"
    
    graph.add_conditional_edges(
        "route",
        route_decision,
        {"manager": "manager", "persona": "persona", "exit": "exit"}
    )
    
    # Manager -> Verify
    graph.add_edge("manager", "verify")
    
    # Conditional after VERIFY
    def verify_decision(state: PiperState) -> str:
        if state.get("error"):
            return "exit"
        if state["verified"]:
            return "persona"
        if state["retry_count"] >= state["max_retries"]:
            return "exit"  # Max retries exceeded
        return "manager"  # Retry
    
    graph.add_conditional_edges(
        "verify",
        verify_decision,
        {"persona": "persona", "manager": "manager", "exit": "exit"}
    )
    
    # Persona -> Exit
    graph.add_edge("persona", "exit")
    
    # Configure checkpointer
    if checkpointer_type == "sqlite":
        conn = sqlite3.connect("piper_checkpoints.db", check_same_thread=False)
        checkpointer = SqliteSaver(conn)
    else:
        checkpointer = MemorySaver()
    
    # Compile with checkpointer
    app = graph.compile(checkpointer=checkpointer)
    
    return app

# Create singleton instance
_piper_app = None

def get_piper_app(checkpointer_type: str = "memory"):
    global _piper_app
    if _piper_app is None:
        _piper_app = build_piper_graph(checkpointer_type)
    return _piper_app
```

#### Step 4.2: Add Interrupt Support

Enable human-in-the-loop for sensitive operations.

```python
# piper/nodes.py (add to verify_node)

def verify_node(state: PiperState) -> dict:
    """Verify with interrupt for sensitive operations."""
    try:
        result = verify_output(
            response=state["llm_response"],
            tool_calls=state["tool_calls"],
            constraints=state.get("intermediate_state", {}).get("constraints", {})
        )
        
        updates = {
            "verified": result.passed,
            "issues": result.issues,
        }
        
        # Check for sensitive operations requiring confirmation
        sensitive_tools = {"file_write", "api_call", "execute_command"}
        called_sensitive = any(
            tc.get("tool") in sensitive_tools 
            for tc in state["tool_calls"]
        )
        
        if called_sensitive and not state.get("confirmation_response"):
            updates["requires_confirmation"] = True
            updates["confirmation_type"] = "sensitive_operation"
            # This will trigger an interrupt when compiled with interrupt_before
        
        if not result.passed:
            updates["retry_count"] = state["retry_count"] + 1
            
        return updates
    except Exception as e:
        return {"error": str(e), "error_node": "verify"}

# Update graph compilation for interrupts
def build_piper_graph_with_interrupts():
    graph = StateGraph(PiperState)
    # ... add nodes and edges as before ...
    
    app = graph.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["persona"]  # Pause before persona if confirmation needed
    )
    return app
```

---

### 4.5 Phase 5: Create Execution Interface

#### Step 5.1: Main Invocation Function

```python
# piper/executor.py
from piper.state import create_initial_state
from piper.graph import get_piper_app
from typing import Optional, Dict, Any

def run_piper(
    user_input: str,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Execute the Piper agentic loop.
    
    Args:
        user_input: The user's message
        session_id: Optional session identifier
        thread_id: Optional thread ID for conversation continuity
        config: Optional configuration overrides
    
    Returns:
        dict with final_output, execution_log, and metadata
    """
    app = get_piper_app()
    
    # Create initial state
    initial_state = create_initial_state(
        user_input=user_input,
        session_id=session_id
    )
    
    # Configure execution
    exec_config = {
        "configurable": {
            "thread_id": thread_id or session_id or "default"
        }
    }
    
    if config:
        exec_config.update(config)
    
    # Execute the graph
    result = app.invoke(initial_state, config=exec_config)
    
    return {
        "output": result.get("final_output", result.get("llm_response", "")),
        "verified": result.get("verified", False),
        "route_decision": result.get("route_decision"),
        "tool_calls": result.get("tool_calls", []),
        "execution_log": result.get("execution_log", []),
        "error": result.get("error"),
        "session_id": result.get("session_id")
    }
```

#### Step 5.2: Resume After Interrupt

```python
# piper/executor.py (continued)

def check_pending_confirmation(thread_id: str) -> Optional[Dict[str, Any]]:
    """Check if there's a pending confirmation for a thread."""
    app = get_piper_app()
    
    # Get current state
    state = app.get_state({"configurable": {"thread_id": thread_id}})
    
    if state and state.values.get("requires_confirmation"):
        return {
            "requires_confirmation": True,
            "confirmation_type": state.values.get("confirmation_type"),
            "tool_calls": state.values.get("tool_calls", []),
            "pending_output": state.values.get("llm_response", "")
        }
    
    return None

def confirm_and_resume(
    thread_id: str,
    confirmed: bool,
    feedback: Optional[str] = None
) -> Dict[str, Any]:
    """
    Provide confirmation and resume execution.
    
    Args:
        thread_id: The thread to resume
        confirmed: True to proceed, False to abort
        feedback: Optional user feedback
    
    Returns:
        dict with final output
    """
    app = get_piper_app()
    
    # Get current state
    current_state = app.get_state({"configurable": {"thread_id": thread_id}})
    
    if not current_state:
        return {"error": "No pending state found for thread"}
    
    # Update state with confirmation
    app.update_state(
        {"configurable": {"thread_id": thread_id}},
        {
            "confirmation_response": confirmed,
            "requires_confirmation": False,
            "verified": confirmed  # If rejected, will trigger retry
        }
    )
    
    if not confirmed:
        # Mark as not verified to trigger retry or exit
        app.update_state(
            {"configurable": {"thread_id": thread_id}},
            {"verified": False, "issues": ["User rejected the action"]}
        )
    
    # Resume execution
    result = app.invoke(
        None,  # No new input, resume from checkpoint
        config={"configurable": {"thread_id": thread_id}}
    )
    
    return {
        "output": result.get("final_output", ""),
        "confirmed": confirmed,
        "execution_log": result.get("execution_log", [])
    }
```

---

### 4.6 Phase 6: Integrate with Piper Core

#### Step 6.1: Replace Old Loop Entry Point

Update Piper's main entry to use the new LangGraph-based executor.

```python
# piper/main.py (or equivalent)

# OLD CODE (to be deprecated):
# def process_user_input(user_input, context):
#     state = initialize_state(user_input, context)
#     state = route(state)
#     state = manager(state)
#     state = verify(state)
#     state = persona(state)
#     return state

# NEW CODE:
from piper.executor import run_piper, check_pending_confirmation, confirm_and_resume

def process_user_input(user_input: str, session_id: str = None) -> dict:
    """Main entry point for Piper - now powered by LangGraph."""
    return run_piper(
        user_input=user_input,
        session_id=session_id
    )

def handle_user_message(user_input: str, session_id: str) -> dict:
    """Handle incoming user message with conversation continuity."""
    
    # First check for pending confirmations
    pending = check_pending_confirmation(session_id)
    if pending:
        # This is a response to a confirmation request
        confirmed = parse_confirmation(user_input)
        return confirm_and_resume(session_id, confirmed, user_input)
    
    # Normal message flow
    return process_user_input(user_input, session_id)
```

---

## 5. Testing Strategy

### 5.1 Unit Tests

Each node wrapper should have comprehensive unit tests verifying state transformations in isolation.

```python
# tests/test_nodes.py
import pytest
from piper.state import create_initial_state
from piper.nodes import route_node, verify_node

def test_route_node_sets_decision():
    state = create_initial_state(user_input="What is the weather?")
    result = route_node(state)
    
    assert "route_decision" in result
    assert result["route_decision"] in ["direct_response", "tool_workflow", "clarification"]
    assert "execution_log" in result

def test_verify_node_passes_clean_output():
    state = create_initial_state(user_input="Hello")
    state["llm_response"] = "Hi there!"
    state["tool_calls"] = []
    
    result = verify_node(state)
    assert result["verified"] == True

def test_verify_node_retries_on_failure():
    state = create_initial_state(user_input="Do something")
    state["llm_response"] = ""  # Empty response should fail
    state["tool_calls"] = []
    state["retry_count"] = 0
    
    result = verify_node(state)
    assert result["verified"] == False
    assert result["retry_count"] == 1
```

### 5.2 Integration Tests

Test the complete graph execution with various input scenarios.

```python
# tests/test_graph.py
import pytest
from piper.graph import build_piper_graph
from piper.state import create_initial_state

@pytest.fixture
def piper_app():
    return build_piper_graph(checkpointer_type="memory")

def test_full_execution_happy_path(piper_app):
    state = create_initial_state(user_input="Hello, how are you?")
    result = piper_app.invoke(state)
    
    assert result.get("final_output") is not None
    assert result.get("error") is None
    assert "Entry" in str(result.get("execution_log", []))

def test_tool_workflow_execution(piper_app):
    state = create_initial_state(user_input="What time is it?")
    result = piper_app.invoke(state)
    
    assert result.get("route_decision") == "tool_workflow"
    assert len(result.get("tool_calls", [])) > 0

def test_retry_on_verification_failure(piper_app):
    # This test requires mocking the manager to return invalid output
    # and verify to reject it
    pass
```

### 5.3 Checkpoint Recovery Tests

Verify crash recovery works correctly.

```python
# tests/test_recovery.py
from piper.graph import build_piper_graph
from piper.state import create_initial_state

def test_state_recovery_after_crash():
    """Simulate crash and recovery."""
    # Use SQLite for persistence
    app = build_piper_graph(checkpointer_type="sqlite")
    
    thread_id = "test-recovery-thread"
    config = {"configurable": {"thread_id": thread_id}}
    
    # Start execution
    state = create_initial_state(user_input="Long running task")
    result = app.invoke(state, config=config)
    
    # Get checkpoint
    saved_state = app.get_state(config)
    assert saved_state is not None
    assert saved_state.values.get("user_input") == "Long running task"
    
    # Simulate new instance (crash recovery)
    new_app = build_piper_graph(checkpointer_type="sqlite")
    recovered_state = new_app.get_state(config)
    
    assert recovered_state.values.get("user_input") == "Long running task"
```

---

## 6. Migration Checklist

Complete this checklist during migration to ensure nothing is missed.

### 6.1 Pre-Migration

- [ ] All existing Piper tests pass
- [ ] Current agentic loop behavior documented
- [ ] Rollback plan created
- [ ] LangGraph dependencies installed and verified

### 6.2 State Schema

- [ ] `PiperState` TypedDict includes all necessary fields
- [ ] State factory function creates valid initial state
- [ ] State schema versioned for future migrations

### 6.3 Node Wrappers

- [ ] `entry_node` validates input correctly
- [ ] `route_node` wraps existing ROUTE logic
- [ ] `manager_node` wraps existing MANAGER logic
- [ ] `verify_node` wraps existing VERIFY logic
- [ ] `persona_node` wraps existing PERSONA logic
- [ ] `exit_node` cleans up properly
- [ ] All nodes return state updates (not full state)
- [ ] Error handling returns `error` + `error_node`

### 6.4 Graph Construction

- [ ] All nodes added to graph
- [ ] Entry point set correctly
- [ ] All edges defined
- [ ] Conditional edges use correct predicates
- [ ] Graph compiles without errors

### 6.5 Checkpointing

- [ ] `MemorySaver` works for development
- [ ] `SqliteSaver` works for single-instance production
- [ ] Checkpoint includes complete state
- [ ] Recovery from checkpoint works

### 6.6 Interrupts

- [ ] Sensitive operations trigger confirmation
- [ ] Interrupt pauses execution correctly
- [ ] Resume with confirmation proceeds
- [ ] Resume with rejection triggers retry or exit

### 6.7 Integration

- [ ] Main entry point uses new executor
- [ ] Conversation continuity works
- [ ] Error states handled gracefully
- [ ] Old loop code deprecated but accessible

### 6.8 Testing

- [ ] Unit tests for all node wrappers
- [ ] Integration tests for complete flow
- [ ] Checkpoint recovery tests
- [ ] Interrupt flow tests
- [ ] Performance benchmarks match or exceed old loop

### 6.9 Deployment

- [ ] Staged rollout plan defined
- [ ] Monitoring for LangGraph-specific metrics
- [ ] Rollback procedure tested
- [ ] Documentation updated

---

## 7. Rollback Plan

If critical issues are discovered:

1. **Restore old loop entry point** in `main.py`
2. **Revert to previous git commit**
3. **Redeploy old version**

The old loop code should be kept in the codebase as "deprecated" for at least one release cycle before removal to ensure quick rollback capability.

---

## 8. Future Enhancements

After successful migration, consider these enhancements enabled by LangGraph infrastructure:

| Enhancement | Description |
|-------------|-------------|
| **Time-travel debugging UI** | Replay and inspect past executions |
| **Parallel node execution** | Run independent operations concurrently |
| **Streaming output** | Stream long-running responses |
| **Distributed execution** | LangGraph Cloud for horizontal scaling |
| **LangSmith integration** | Observability and tracing |

---

## 9. Conclusion

This migration replaces Piper's hand-rolled agentic loop with LangGraph's battle-tested state machine infrastructure.

**Key benefits:**
- Reliable state transitions
- Crash recovery via checkpoints
- Human-in-the-loop interrupts
- Production-ready retry patterns

**The migration preserves all existing Piper domain logic while significantly improving infrastructure reliability.**

Follow the step-by-step implementation guide, complete the checklist items, and maintain the rollback plan until the new system proves stable in production.
