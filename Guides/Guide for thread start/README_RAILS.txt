
====================================================
RAILS · RERAIL — Solid Base (Behavior-Preserving)
====================================================
Phase A — Baseline & Safety (no code edits)
-------------------------------------------
A01 — Hard-restore baseline snapshot
  Cmd: Expand-Archive -Force "<KGB.zip>" "C:\Piper\scripts"
  Purge __pycache__, clear env flags (PIPER_UI_DEV_INPUT, etc.).
  Smoke: GUI/CLI launch cleanly; dev pane hidden by default.
  Tag: KGB-YYYY-MM-DD_A01_baseline_restored

A02 — Runbook check
  Ensure: two-terminal run (CLI → core.log, GUI tails it) documented in RUNBOOK.txt.
  Tag: KGB-YYYY-MM-DD_A02_runbook_ok

Phase B — UI Modularization (no behavior change)
------------------------------------------------
Goal: shrink oversized files; move code into component modules you already scaffolded.

B01 — Extract DPG boot
  Change: Move DearPyGui bootstrap + viewport sizing from entries/app_gui_entry.py to ui/dpg_app.py.
  Entry becomes a thin wrapper: parse flags → dpg_app.run(...).
  Smoke: GUI identical; logs/chat render; no layout change.
  Tag: KGB-YYYY-MM-DD_B01_dpg_boot_extract

B02 — Split Dev Pane (controls) to components
  Change: Move dev-only pane from ui/panes.py into ui/components/controls_pane.py.
  Keep gating by PIPER_UI_DEV_INPUT. Import from panes.py.
  Smoke: With flag=1, controls still work (input box, mood/state, injectors). With flag=0, hidden.
  Tag: KGB-YYYY-MM-DD_B02_controls_component

B03 — Split Chat/Logs/Status components
  Change: Move chat rendering into ui/components/chat_pane.py; logs tail into logs_pane.py;
         status indicators into status_pane.py. panes.py now composes these.
  Smoke: Chat wrap/copy/autoscroll and Logs tail/autoscroll behave exactly the same; status dot/labels intact.
  Tag: KGB-YYYY-MM-DD_B03_panes_components

B04 — Trim panes.py and entry size check
  Change: Ensure entries/app_gui_entry.py < 150 lines; ui/panes.py ~80–120.
  Add DEV NOTE at file heads with size targets.
  Smoke: Launch ok; no diff in behavior.
  Tag: KGB-YYYY-MM-DD_B04_sizes_ok

Phase C — Prompt/Format Seam in Core (RR01/RR02)
------------------------------------------------
C01 — Prompt seam unify
  Change: core/core_machine.py uses services/cli_prompt.current_prompt() for prompts.
  Smoke: CLI prompt identical; no duplication of prompt logic in entry.
  Tag: KGB-YYYY-MM-DD_C01_prompt_seam

C02 — _say() formatting path
  Change: Route all speak/print through cli_prompt.format_line().
  Smoke: Output lines remain identical in content; style consolidated.
  Tag: KGB-YYYY-MM-DD_C02_say_format

Phase D — Adapter Hygiene (Legacy quarantine)
---------------------------------------------
D01 — Move legacy adapters
  Change: Move services/asr_vosk.py and services/wake_porcupine.py → services/old/.
  Smoke: Imports resolve to modular adapters (services/asr/vosk_adapter.py, services/wake/porcupine_adapter.py). App runs.
  Tag: KGB-YYYY-MM-DD_D01_legacy_quarantine

D02 — Canonical imports
  Change: Ensure entries/core import only modular adapters. Add __init__.py if needed.
  Smoke: `python -c "import services.asr.vosk_adapter"` works.
  Tag: KGB-YYYY-MM-DD_D02_canonical_imports

Phase E — Core Helpers (Paths & Logbus)
---------------------------------------
E01 — core/paths.py
  Change: Add root(), run_dir(), snapshots_dir(), library_dir() with Windows-safe joins.
  Replace ad-hoc path building in entries/ui/services with paths.*.
  Smoke: Logs and snapshots land where expected; no broken paths.
  Tag: KGB-YYYY-MM-DD_E01_paths_helper

E02 — core/logbus.py
  Change: Add event(name, **kv) and state(prev, next) that print compact lines, replacing ad-hoc prints.
  GUI still reads same file; we keep content format stable.
  Smoke: Single concise line per transition; no spam.
  Tag: KGB-YYYY-MM-DD_E02_logbus_seed

Phase F — Naming Risks & Stubs
------------------------------
F01 — Rename common/logging.py → common/log_utils.py
  Change: Update imports; avoid shadowing stdlib logging.
  Smoke: App runs; “import logging” elsewhere still points to stdlib.
  Tag: KGB-YYYY-MM-DD_F01_logutils

F02 — TTS manager stub fence
  Change: Add TODO + interface in services/tts/tts_manager.py; ensure all callers still use speak_once for baseline.
  Smoke: No change to audio behavior; barge-in unaffected.
  Tag: KGB-YYYY-MM-DD_F02_tts_stub_ready

Phase G — Tests & Linters (RR08/RR09)
-------------------------------------
G01 — Import smoke tests
  Change: Add tests/test_imports.py covering: core paths/logbus, adapters, ui components, entries.
  Smoke: pytest runs; imports pass/skips ok.
  Tag: KGB-YYYY-MM-DD_G01_import_tests

G02 — Linter configs
  Change: ruff.toml and pyproject.toml (black line-length=100). No code reformat yet.
  Smoke: runtime unaffected; CI-lite ready.
  Tag: KGB-YYYY-MM-DD_G02_linters

Phase H — Cleanup & Closure
---------------------------
H01 — Remove dead placeholders
  Change: Delete empty component files replaced by real modules; move any “old” to /old/ folders.
  Smoke: repo imports still pass; GUI/CLI run.
  Tag: KGB-YYYY-MM-DD_H01_dead_cleanup

H02 — Size & structure audit
  Check: entries/app_gui_entry.py ≤ 150; ui/panes.py ≤ 120; no services import legacy; paths/logbus in use.
  Tag: KGB-YYYY-MM-DD_H02_structure_ok

H03 — Wrap & snapshot
  Change: Add README_FIRST snapshots note; produce final KGB zip.
  Tag: KGB-YYYY-MM-DD_RERAIL_SOLIDBASE_WRAP

====================================================
Definition of Done
====================================================
- No oversized monoliths; UI split into components; entry thin.
- Legacy adapters quarantined; canonical imports in place.
- Core prompt and format seams unified; paths/logbus helpers present.
- No module name collisions; stubs fenced with clear interfaces.
- Tests/linter configs present; all smokes pass.
- Final KGB snapshot created and recorded.
