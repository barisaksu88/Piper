# Code Cleanup Audit

## Phase 18A — Narrow, Safe Cleanup Pass

Date: 2026-05-22
Branch: `feature/web-ui-bridge`

---

### Phase 18A Summary

Goal: Remove only code that is proven unused. Clean unused imports and obvious dead helpers. No architecture refactoring.

---

### Removed Code

| File | Removed | Proof of Unused Status |
|------|---------|------------------------|
| `core/graph_nodes.py` | `Dict`, `List` from `typing` import | Repo-wide grep found zero uses outside the import line. Python 3.10+ native `dict`/`list` used throughout. |
| `core/routing/route_normalizer.py` | `looks_like_task_creation` import + empty `route_subjects` import block | Grep found only the import line in this file; actual usage is in `core/services/state_mutation.py` which imports directly from `route_subjects`. |
| `tools/search.py` | `import json`, `from datetime import datetime` | Grep found zero uses outside import lines. Module uses no JSON or datetime directly. |

---

### Confirmed Unused References

| Item | Status | Reason Kept |
|------|--------|-------------|
| `_normalize_extension_file_work()` | **Used** | Called by `_registered_file_work_extension_interceptor` at `core/routing/route_normalizer.py:729`. |
| `_proactive_monitor_registration` | **Used (side-effect)** | Imported for registration side effects (`# noqa: F401`). Removing would unregister proactive monitor. |
| `add_messages` in `core/graph_nodes.py` | **Used (fallback)** | Provides fallback when `langgraph` is not installed. Required for compatibility. |
| `requestFullscreen` in `ChatPanel.tsx` | **Only fullscreen behavior** | No `ImageModal` component exists in current branch. Would be feature add, not cleanup. |
| `requestFullscreen` in `VisionWorkspace.tsx` | **Intentional** | Dedicated workspace image viewer with its own fullscreen behavior. |

---

### Things Intentionally Not Removed

- **Legacy orchestrator and `core/orchestrator_phases.py`** were intentionally kept because LangGraph nodes still delegate into the existing phase logic and legacy fallback remains active insurance.
- **DearPyGui fallback** kept per hard constraints.
- **Browser mode** kept per hard constraints.
- **Native MIC/STT** kept per hard constraints.
- **`/style` functionality** kept per hard constraints.
- **Search/reporting** kept per hard constraints.
- **LangGraph fallback/legacy fallback** kept per hard constraints.
- **`docs/archive/`** not mass-deleted per hard constraints.
- **No broad `orchestrator_phases.py` split** performed in this phase.
- **No routing architecture rewrite** performed in this phase.

---

### Future Cleanup Phases

- **18B**: Consolidate duplicate routing/state/environment checks
- **18C**: Modularize `orchestrator_phases.py` by phase (after LangGraph is hardened)
- **18D**: Harden LangGraph as primary path
- **18E**: Consider legacy fallback retirement only after long daily-use burn-in

---

### Validation Results

```bash
python -m py_compile core/graph_nodes.py core/routing/route_normalizer.py tools/search.py
# Result: OK

python -m pytest web_ui/bridge/ -q
# Result: 261 passed

python scripts/search_deep_dive_timeout_smoke_test.py
# Result: 10 passed

python scripts/search_web_pump_smoke_test.py
# Result: 5 passed
```

---

### Frontend Status

Frontend files were **not touched** in this phase. No rebuild required.

---

### Data Files Status

- `data/users.json` — **untouched**
- `data/styles/active_style.txt` — **untouched**
