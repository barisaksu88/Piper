# Engine Registry Inventory

## Purpose

The engine registry inventory is a backend observability utility that reports the current state of Piper's three main engine registries:

1. **Route interceptors** — functions that short-circuit the router before the Secretary LLM is invoked.
2. **Feature hooks** — lifecycle callbacks registered by engine modules (e.g. `on_turn_end`, `on_task_verified`).
3. **Persona tail blocks** — builder functions that append directive text to the Persona system prompt.

This script exists so that future refactors (such as memory insertion or hook relocation) can be reviewed against a reliable, version-controlled inventory. It does not change runtime behavior, migrate hooks, or modify registrations.

## Running the script

```bash
# Readable text output
python scripts/engine_registry_inventory.py

# JSON output
python scripts/engine_registry_inventory.py --json

# Write JSON to a file
python scripts/engine_registry_inventory.py --json --output registry.json
```

## Requirements

- Python 3.12+
- Piper source tree available
- No Piper app boot, UI, LLM server, model load, or web server required
- Runs in under one second

## What it reports

For each registered function the script emits:

| Field | Description |
|-------|-------------|
| `registry_type` | `route_interceptor`, `feature_hook`, or `tail_block` |
| `index` | Registration order (list position) |
| `module` | Python module the function is defined in |
| `function_name` | `__name__` of the function |
| `qualname` | `__qualname__` of the function |
| `signature` | `inspect.signature(...)` string if available |

## Registries covered

### Route interceptors

Source: `core.routing.route_normalizer._ROUTE_INTERCEPTOR_REGISTRY`

Interceptors are evaluated in registration order. The first interceptor that returns a non-`None` result wins and skips the Secretary/router LLM entirely.

Known interceptors at time of writing:
- `environment_query` — live date/time questions
- `operational_state_answer` — task/event status lookups
- `proactive_monitor` — proactive reminder triggers
- `destructive_prompt_injection` — injection guard
- `undo`, `explain_last_turn`, `file_target_correction`, `file_state_correction_ack`, `pending_file_target_confirmation`

### Feature hooks

Source: `core.feature_hooks.list_hooks()` (backed by `_HOOKS`)

Hooks fire at specific lifecycle points. Known hook types:
- `on_pre_route`
- `on_turn_end` (includes `core.engines.memory_insertion` hooks for memory consolidation and profile refresh)
- `on_task_verified`

### Tail blocks

Source: `core.engines.tail_block_registry._TAIL_BLOCK_REGISTRY`

Tail blocks append rules and context to the Persona system prompt. They run in registration order and each receives a `TailBlockContext`.

Known tail blocks at time of writing:
- `no_mutation_rule`
- `context_arbitration`
- `document_qa_rule`
- `search_report_rule`
- `explain_last_turn`
- `active_skill`
- `verification_result`
- `file_work_report`
- `failed_verification`
- `failed_outcome_no_verification`
- `workspace_boundary`
- `proactive_trigger`
- `reminder_set_result`

## Registration path

The inventory exercises the normal backend registration path by importing `core.orchestrator`.  This import chain now explicitly includes:

- `core.engines.*` registrations (proactive monitor, change journal, conversation compressor, stats collector, environment query, operational state answer, memory insertion)
- `core.prompt_context` registration (`on_pre_route` hook)

Because all registrations happen through the orchestrator import chain, the inventory does not depend on special one-off imports for completeness.

## Limitations

- The inventory reflects the state **after** import-time side effects have run. If a module is imported conditionally in production, the inventory may differ unless the same import chain is exercised.
- Additional conditional imports in `app.py` could in theory register more items, but none are known at time of writing.
- No file is written unless `--output` is passed.

## Registry idempotency

All three registries are idempotent by `module + qualname`:

- `register_hook` — does not append a duplicate function (same `__module__` + `__qualname__`) to the same hook type.
- `register_route_interceptor` — does not append a duplicate interceptor.
- `register_tail_block` — does not append a duplicate tail block builder.

Duplicate imports or reloads will not create duplicate entries. Registration order remains first-registration order.

## Testing

```bash
python -m pytest tests/test_engine_registry_inventory.py -q
```

Tests verify:
- inventory builds without booting Piper
- expected interceptors are present exactly once
- expected hooks and tail blocks are present
- signatures are captured
- no duplicate interceptor qualnames exist
