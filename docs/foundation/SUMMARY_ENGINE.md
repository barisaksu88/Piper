# SummaryEngine Contract

Status: Complete — extracted and frozen 2026-03-15
Date: 2026-03-15

Companion docs:
- [BLUEPRINT.md](BLUEPRINT.md)
- [EXECUTION_ROADMAP.md](EXECUTION_ROADMAP.md)
- [FILEWORK_ENGINE.md](FILEWORK_ENGINE.md)

---

## 1. Purpose

`SummaryEngine` is the single owner of the question:

> "What does the scratchpad say happened, and how should that be carried forward to the next turn?"

Right now that question is answered in at least three places:

- `core/engines/context_pack.py` — holds `latest_stage_entries`, all `extract_*` methods,
  `_build_outcome_block`, `_extract_latest_runtime_note`, `_extract_latest_stage_status`,
  `_extract_latest_exact_file_read_path`, `_extract_latest_file_lookup_brief`,
  `_is_generic_file_work_summary`, and `_sanitize_runtime_note` — 12 methods doing
  scratchpad-level extraction embedded inside a pack-building engine
- `core/scratchpad_formatter.py` — holds `format_outcome`, `build_outcome_pack`,
  `_select_outcome_detail`, `_extract_observation_detail`, `_is_generic_file_work_summary`
  (duplicate), and `_truncate_text` — outcome shaping logic mixed into a formatting class
- `core/prompt_builder.py` — holds `_truncate_scratchpad` and `_scratchpad_exact_read_paths`
  (duplicate of `FileWorkEngine.exact_read_paths_from_scratchpad`) — context sizing logic
  inside a prompt constructor

`SummaryEngine` extracts all of this into one explicit owner so the carry-forward pipeline
is auditable and testable independently of pack building, prompt construction, and formatting.

---

## 2. What SummaryEngine Owns

### A. Scratchpad slicing

```
latest_stage_entries(scratchpad)  ->  list[str]
```
Returns only the entries from the latest `=== STAGE N START ===` to the end.
Fallback: last 6 entries when no stage header is found.

Currently in: `ContextPackEngine.latest_stage_entries` (staticmethod).

---

### B. Stage evidence extraction

```
extract_verified_result(scratchpad)   ->  str    # FILE_WORK_VERIFIED_RESULT payload
extract_proposal(scratchpad)          ->  str    # PROPOSAL: payload
extract_exact_file_read(scratchpad)   ->  str    # FILE_READ_EXACT_PATH/CONTENT blocks
extract_file_lookup(scratchpad)       ->  str    # FILE_LOOKUP_MATCHES payload
```

All operate on `latest_stage_entries`. All return empty string when nothing is found.

Currently in: `ContextPackEngine.extract_verified_file_work_answer`,
`extract_latest_stage_proposal_answer`, `extract_exact_file_read_answer`,
`extract_file_lookup_answer`.

---

### C. Outcome status and runtime note

```
extract_stage_status(scratchpad)  ->  str   # RESULT line from latest OUTCOME entry
build_runtime_note(scratchpad)    ->  str   # carry-forward note for [LATEST_RUNTIME_CONTEXT]
```

`build_runtime_note` is the primary carry-forward builder. It checks in priority order:
1. `extract_verified_result` text
2. Last exact-read path label
3. File lookup brief
4. `LAST_LOG:` line from latest OUTCOME entry
5. `OBSERVATION_TEXT:` from last stage entry

Currently in: `ContextPackEngine._extract_latest_stage_status`,
`ContextPackEngine._extract_latest_runtime_note` (calls `_extract_latest_exact_file_read_path`,
`_extract_latest_file_lookup_brief`, plus inline scratchpad walks).

---

### D. Outcome block construction

```
build_outcome_block(scratchpad, *, escalation_active: bool = False)  ->  str
```

Finds the latest `OUTCOME` entry in the scratchpad and attaches the correct `[INSTRUCTION]`
directive for PAUSED / FAILED / SUCCESS states.

Currently in: `ContextPackEngine._build_outcome_block` (staticmethod).

---

### E. Outcome detail selection and extraction

```
select_outcome_detail(stage_type, stage_entries, fallback)  ->  str
extract_observation_detail(last_observation)                ->  str
```

`select_outcome_detail` chooses the most meaningful detail entry for `format_outcome`.
Priority: FILE_WORK_VERIFIED_RESULT → FILE_CHECKER_VERDICT → PROPOSAL → FILE_READ_EXACT_PATH.

`extract_observation_detail` peels the concise string from a raw observation entry
(handles FILE_WORK_VERIFIED_RESULT JSON payload, OBSERVATION_TEXT: prefix, and tail-200 fallback).

Currently in: `ScratchpadFormatter._select_outcome_detail`,
`ScratchpadFormatter._extract_observation_detail`.

---

### F. Generic summary detection

```
is_generic_file_work_summary(summary)  ->  bool
```

Returns `True` when a tool `summary` string is too generic (e.g. `"Wrote text file"`) to be
worth showing to the persona. Used as a suppression gate before showing the `reason` instead.

Currently in: **two places** —
`ContextPackEngine._is_generic_file_work_summary` and
`ScratchpadFormatter._is_generic_file_work_summary`. Identical implementations. One copy only.

---

### G. Text utility

```
sanitize_note(text)              ->  str   # single-line, max 280 chars
truncate_scratchpad(text, limit) ->  str   # tail-slice with header marker
truncate_text(text, limit)       ->  str   # tail-slice with [TRUNCATED] marker
```

`sanitize_note` → currently `ContextPackEngine._sanitize_runtime_note`.
`truncate_scratchpad` → currently `PromptBuilder._truncate_scratchpad`.
`truncate_text` → currently `ScratchpadFormatter._truncate_text`.

---

## 3. What SummaryEngine Does NOT Own

| Concern | Owner |
|---|---|
| Persona pack assembly | `ContextPackEngine` |
| Directive / tail-block selection | `ContextPackEngine.build_persona_directive_pack` |
| Runtime context rendering (message shape) | `ContextPackRenderer` |
| Planner / inspector prompt construction | `PromptBuilder` |
| Stage outcome formatting (`=== STAGE N OUTCOME ===`) | `ScratchpadFormatter` (delegates to engine for detail) |
| Observation stringification / field filtering | `ScratchpadFormatter._stringify_observation` (stays there) |
| Observation character limits by tool type | `ScratchpadFormatter._observation_limit` (stays there) |
| Stage header formatting | `ScratchpadFormatter.format_stage_header` (stays there) |
| Step formatting | `ScratchpadFormatter.format_step` (stays there) |
| LLM calls | None — SummaryEngine carries no LLM calls |
| File read / write | None |

---

## 4. Duplication Eliminated

| Duplicated item | Current locations | After extraction |
|---|---|---|
| `_is_generic_file_work_summary` | `ContextPackEngine` + `ScratchpadFormatter` | `SummaryEngine` only |
| `_scratchpad_exact_read_paths` | `PromptBuilder` + `FileWorkEngine` | `FileWorkEngine` only — `PromptBuilder` switches to `FileWorkEngine.exact_read_paths_from_scratchpad` |
| `_truncate_text` | `ScratchpadFormatter` only | `SummaryEngine` (both sites use it) |
| `_sanitize_runtime_note` | `ContextPackEngine` only | `SummaryEngine` only |

---

## 5. Public API Contract

```python
class SummaryEngine:
    """Single owner of scratchpad-level extraction and carry-forward compression.

    All public methods are static — no instance state.
    """

    # -- A. Scratchpad slicing ---------------------------------------------- #

    @staticmethod
    def latest_stage_entries(scratchpad: list[str]) -> list[str]: ...

    # -- B. Stage evidence extraction --------------------------------------- #

    @classmethod
    def extract_verified_result(cls, scratchpad: list[str]) -> str: ...

    @classmethod
    def extract_proposal(cls, scratchpad: list[str]) -> str: ...

    @classmethod
    def extract_exact_file_read(cls, scratchpad: list[str]) -> str: ...

    @classmethod
    def extract_file_lookup(cls, scratchpad: list[str]) -> str: ...

    # -- C. Outcome status and runtime note --------------------------------- #

    @classmethod
    def extract_stage_status(cls, scratchpad: list[str]) -> str: ...

    @classmethod
    def build_runtime_note(cls, scratchpad: list[str]) -> str: ...

    # -- D. Outcome block --------------------------------------------------- #

    @staticmethod
    def build_outcome_block(
        scratchpad: list[str],
        *,
        escalation_active: bool = False,
    ) -> str: ...

    # -- E. Outcome detail selection ---------------------------------------- #

    @staticmethod
    def select_outcome_detail(
        stage_type: str,
        stage_entries: list[str] | None,
        fallback: str,
    ) -> str: ...

    @staticmethod
    def extract_observation_detail(last_observation: str) -> str: ...

    # -- F. Generic summary detection --------------------------------------- #

    @staticmethod
    def is_generic_file_work_summary(summary: str) -> bool: ...

    # -- G. Text utility ---------------------------------------------------- #

    @staticmethod
    def sanitize_note(text: str) -> str: ...

    @staticmethod
    def truncate_scratchpad(text: str, *, limit: int) -> str: ...

    @staticmethod
    def truncate_text(text: str, limit: int) -> str: ...
```

---

## 6. Migration Map

### 6.1 Methods moving FROM → TO

| Current file | Current name | New name in SummaryEngine |
|---|---|---|
| `ContextPackEngine` | `latest_stage_entries(scratchpad)` | `SummaryEngine.latest_stage_entries` |
| `ContextPackEngine` | `extract_verified_file_work_answer(scratchpad)` | `SummaryEngine.extract_verified_result` |
| `ContextPackEngine` | `extract_latest_stage_proposal_answer(scratchpad)` | `SummaryEngine.extract_proposal` |
| `ContextPackEngine` | `extract_exact_file_read_answer(scratchpad)` | `SummaryEngine.extract_exact_file_read` |
| `ContextPackEngine` | `extract_file_lookup_answer(scratchpad)` | `SummaryEngine.extract_file_lookup` |
| `ContextPackEngine` | `_extract_latest_stage_status(scratchpad)` | `SummaryEngine.extract_stage_status` |
| `ContextPackEngine` | `_extract_latest_runtime_note(scratchpad)` | `SummaryEngine.build_runtime_note` |
| `ContextPackEngine` | `_build_outcome_block(scratchpad, ...)` | `SummaryEngine.build_outcome_block` |
| `ContextPackEngine` | `_is_generic_file_work_summary(summary)` | `SummaryEngine.is_generic_file_work_summary` (**dedup**) |
| `ContextPackEngine` | `_sanitize_runtime_note(text)` | `SummaryEngine.sanitize_note` |
| `ContextPackEngine` | `_extract_latest_exact_file_read_path(scratchpad)` | private helper inside `build_runtime_note` |
| `ContextPackEngine` | `_extract_latest_file_lookup_brief(scratchpad)` | private helper inside `build_runtime_note` |
| `ScratchpadFormatter` | `_select_outcome_detail(stage_type, entries, fallback)` | `SummaryEngine.select_outcome_detail` |
| `ScratchpadFormatter` | `_extract_observation_detail(last_observation)` | `SummaryEngine.extract_observation_detail` |
| `ScratchpadFormatter` | `_is_generic_file_work_summary(summary)` | removed — use `SummaryEngine.is_generic_file_work_summary` (**dedup**) |
| `ScratchpadFormatter` | `_truncate_text(text, limit)` | `SummaryEngine.truncate_text` |
| `PromptBuilder` | `_truncate_scratchpad(text, *, limit)` | `SummaryEngine.truncate_scratchpad` |
| `PromptBuilder` | `_scratchpad_exact_read_paths(scratchpad_text)` | removed — use `FileWorkEngine.exact_read_paths_from_scratchpad` |

### 6.2 Call site changes after migration

| File | Old call | New call |
|---|---|---|
| `context_pack.py` | `cls.latest_stage_entries(sp)` | `SummaryEngine.latest_stage_entries(sp)` |
| `context_pack.py` | `cls.extract_verified_file_work_answer(sp)` | `SummaryEngine.extract_verified_result(sp)` |
| `context_pack.py` | `cls.extract_latest_stage_proposal_answer(sp)` | `SummaryEngine.extract_proposal(sp)` |
| `context_pack.py` | `cls.extract_exact_file_read_answer(sp)` | `SummaryEngine.extract_exact_file_read(sp)` |
| `context_pack.py` | `cls.extract_file_lookup_answer(sp)` | `SummaryEngine.extract_file_lookup(sp)` |
| `context_pack.py` | `cls._extract_latest_stage_status(sp)` | `SummaryEngine.extract_stage_status(sp)` |
| `context_pack.py` | `cls._extract_latest_runtime_note(sp)` | `SummaryEngine.build_runtime_note(sp)` |
| `context_pack.py` | `cls._build_outcome_block(sp, ...)` | `SummaryEngine.build_outcome_block(sp, ...)` |
| `context_pack.py` | `cls._sanitize_runtime_note(text)` | `SummaryEngine.sanitize_note(text)` |
| `context_pack.py` | `cls._is_generic_file_work_summary(s)` | `SummaryEngine.is_generic_file_work_summary(s)` |
| `scratchpad_formatter.py` | `ScratchpadFormatter._select_outcome_detail(...)` | `SummaryEngine.select_outcome_detail(...)` |
| `scratchpad_formatter.py` | `ScratchpadFormatter._extract_observation_detail(...)` | `SummaryEngine.extract_observation_detail(...)` |
| `scratchpad_formatter.py` | `ScratchpadFormatter._is_generic_file_work_summary(s)` | `SummaryEngine.is_generic_file_work_summary(s)` |
| `scratchpad_formatter.py` | `ScratchpadFormatter._truncate_text(text, limit)` | `SummaryEngine.truncate_text(text, limit)` |
| `prompt_builder.py` | `PromptBuilder._truncate_scratchpad(text, limit=...)` | `SummaryEngine.truncate_scratchpad(text, limit=...)` |
| `prompt_builder.py` | `PromptBuilder._scratchpad_exact_read_paths(text)` | `FileWorkEngine.exact_read_paths_from_scratchpad([text])` |

---

## 7. Import Safety

`SummaryEngine` must remain a **zero-external-engine-dependency** leaf module:

- **Allowed imports**: `re`, `json`, stdlib only + `core.contracts` for type hints
- **Must NOT import**: `ContextPackEngine`, `ScratchpadFormatter`, `FileWorkEngine`,
  `FileStagePolicy`, `StateMutationEngine`, `PromptBuilder`
- `ContextPackEngine` imports `SummaryEngine` (one-directional, no cycle)
- `ScratchpadFormatter` imports `SummaryEngine` (one-directional, no cycle)
- `PromptBuilder` imports `SummaryEngine` (one-directional, no cycle)
- `core/engines/__init__.py` exports `SummaryEngine`

---

## 8. New Contract Type (if needed)

No new `contracts.py` type is required for Phase 6. The engine returns plain Python scalars
(`str`, `list[str]`, `bool`) from all public methods. `StageOutcomePack` (already in
`contracts.py`) is the only structured type touched by downstream callers.

If a `ScratchpadSnapshot` dataclass proves useful after extraction, it can be added then.

---

## 9. Migration Sequence

1. **Create** `core/services/summary.py` with the full `SummaryEngine` implementation,
   porting all methods listed in §6.1, with no behaviour change.
2. **Add** `SummaryEngine` export to `core/engines/__init__.py`.
3. **Update `context_pack.py`**:
   - Add `from core.services.summary import SummaryEngine` import
   - Replace all `cls.*` scratchpad extraction calls with `SummaryEngine.*` equivalents
   - Remove the 12 methods that moved (keep `build_runtime_context_pack`,
     `build_persona_runtime_pack`, `build_persona_directive_pack`, `build_persona_pack`,
     `apply_document_focus`, `clear_memory_for_file_work`, `to_prompt_context`,
     `render_runtime_context_message`, `_collect_runtime_context_paths`, and
     `_render_persona_active_skill_block` — those stay in `ContextPackEngine`)
4. **Update `scratchpad_formatter.py`**:
   - Add `from core.services.summary import SummaryEngine` import
   - Replace `_select_outcome_detail`, `_extract_observation_detail`,
     `_is_generic_file_work_summary`, `_truncate_text` with `SummaryEngine.*` calls
   - Remove the four private methods from `ScratchpadFormatter`
5. **Update `prompt_builder.py`**:
   - Replace `_truncate_scratchpad` with `SummaryEngine.truncate_scratchpad`
   - Replace `_scratchpad_exact_read_paths` with `FileWorkEngine.exact_read_paths_from_scratchpad`
   - Remove both private methods from `PromptBuilder`
6. **Write smoke test** at `scripts/summary_engine_smoke_test.py` covering all 14 public methods.
7. **Run full regression pack** — `file_work_engine_smoke_test.py`, `file_stage_policy_smoke_test.py`,
   `consolidation_exclusion_smoke_test.py`, `extension_reorg_smoke_test.py`.
8. **Update** `docs/v1/EXECUTION_ROADMAP.md` Phase 6 status → DONE.
9. **Update** `notes/coder-log.md` with Phase 6 entry.
10. **Update** `memory/MEMORY.md` — mark SummaryEngine done, note all 6 engines complete.
