# Piper Comment Audit
Generated: 2025-09-03T17:32:29
Scanned root: C:\Piper\scripts
Files: 122
Findings: 53


## C:/Piper/scripts/core/bridge.py
- **WARN** · `C:/Piper/scripts/core/bridge.py`:59 · *secrets_hint*
  - # ASR path: pull exactly one token from ASR if present and publish it.

## C:/Piper/scripts/entries/_app_gui_entry_impl.py
- **WARN** · `C:/Piper/scripts/entries/_app_gui_entry_impl.py`:489 · *deprecated_note*
  - from ui.panes import set_hb_text as __set  # legacy fallback

## C:/Piper/scripts/entries/_app_gui_entry_impl_backup.py
- **WARN** · `C:/Piper/scripts/entries/_app_gui_entry_impl_backup.py`:423 · *deprecated_note*
  - from ui.panes import set_hb_text as __set  # legacy fallback

## C:/Piper/scripts/entries/app_gui_entry.py
- **WARN** · `C:/Piper/scripts/entries/app_gui_entry.py`:6 · *deprecated_note*
  - from .app_gui_entry import run as run        # legacy fallback

## C:/Piper/scripts/services/asr_vosk.py
- **WARN** · `C:/Piper/scripts/services/asr_vosk.py`:93 · *secrets_hint*
  - # Stub path: if no recognizer, use scripted tokens

## C:/Piper/scripts/tests/old/test_core_bridge_mock.py
- **WARN** · `C:/Piper/scripts/tests/old/test_core_bridge_mock.py`:28 · *secrets_hint*
  - asr=MockASRSvc(["hello", ""])  # second token would be EOU if pulled

## C:/Piper/scripts/tests/test_poll_helper_with_mock.py
- **WARN** · `C:/Piper/scripts/tests/test_poll_helper_with_mock.py`:2 · *secrets_hint*
  - # Verifies poll_asr_once() forwards ASR tokens and advances FSM to THINKING.
- **WARN** · `C:/Piper/scripts/tests/test_poll_helper_with_mock.py`:37 · *secrets_hint*
  - # start(): publishes WakeDetected + first ASR token "hello" -> LISTENING

## C:/Piper/scripts/tests/test_vosk_stub_script_flow.py
- **WARN** · `C:/Piper/scripts/tests/test_vosk_stub_script_flow.py`:35 · *secrets_hint*
  - # start(): WakeDetected + first ASR token "hello" (non-empty) -> LISTENING
- **WARN** · `C:/Piper/scripts/tests/test_vosk_stub_script_flow.py`:39 · *secrets_hint*
  - # Next token from stub script is EOU "" -> publish -> THINKING

## C:/Piper/scripts/tools/apply_b04_21c_try_lone_fix.py
- **WARN** · `C:/Piper/scripts/tools/apply_b04_21c_try_lone_fix.py`:20 · *secrets_hint*
  - # that are NOT indented (top-level), and then a next top-level token (def/class/from/import/try/except/finally)

## C:/Piper/scripts/tools/audit_comments.py
- **WARN** · `C:/Piper/scripts/tools/audit_comments.py`:15 · *persona_touch_warning*
  - PERSONA_TOUCH_PAT = re.compile(r"#.*(personality\.py|persona|sarcasm level).*edit", re.I)
- **WARN** · `C:/Piper/scripts/tools/audit_comments.py`:16 · *secrets_hint*
  - SECRET_HINT_PAT = re.compile(r"#.*(token|api[_-]?key|password|secret|credential)", re.I)

## C:/Piper/scripts/ui/helpers/gui_loop.py
- **WARN** · `C:/Piper/scripts/ui/helpers/gui_loop.py`:10 · *deprecated_note*
  - # legacy namespace fallback
- **WARN** · `C:/Piper/scripts/ui/helpers/gui_loop.py`:48 · *deprecated_note*
  - from ui.panes import set_hb_text as _set  # legacy fallback

## C:/Piper/scripts/ui/pane_parts/logs_pane.py
- **WARN** · `C:/Piper/scripts/ui/pane_parts/logs_pane.py`:22 · *deprecated_note*
  - dpg.add_button(label="Copy Logs", callback=lambda: None)  # legacy callback stays in panes; guard below

## C:/Piper/scripts/common/config.py
- **INFO** · `C:/Piper/scripts/common/config.py`:6 · *hardcoded_path_hint*
  - # Robust import (works from C:\Piper and from C:\Piper\scripts)

## C:/Piper/scripts/common/types.py
- **INFO** · `C:/Piper/scripts/common/types.py`:7 · *hardcoded_path_hint*
  - # Robust imports for both launch styles (C:\Piper and C:\Piper\scripts)

## C:/Piper/scripts/core/bridge.py
- **INFO** · `C:/Piper/scripts/core/bridge.py`:7 · *hardcoded_path_hint*
  - # Robust imports (work from C:\Piper and C:\Piper\scripts)

## C:/Piper/scripts/core/core_app.py
- **INFO** · `C:/Piper/scripts/core/core_app.py`:7 · *hardcoded_path_hint*
  - # Robust imports (works from C:\Piper and C:\Piper\scripts)

## C:/Piper/scripts/core/core_commands.py
- **INFO** · `C:/Piper/scripts/core/core_commands.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\core\core_commands.py

## C:/Piper/scripts/core/core_machine.py
- **INFO** · `C:/Piper/scripts/core/core_machine.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\core\core_machine.py
- **INFO** · `C:/Piper/scripts/core/core_machine.py`:17 · *hardcoded_path_hint*
  - # in C:\Piper\scripts\core\core_machine.py near the top

## C:/Piper/scripts/core/events.py
- **INFO** · `C:/Piper/scripts/core/events.py`:9 · *hardcoded_path_hint*
  - # Robust imports (works from C:\Piper and C:\Piper\scripts)

## C:/Piper/scripts/core/transition_plan.py
- **INFO** · `C:/Piper/scripts/core/transition_plan.py`:1 · *long_comment_block*
  - 29 consecutive comment lines — consider compressing/moving to README

## C:/Piper/scripts/entries/_app_gui_entry_impl.py
- **INFO** · `C:/Piper/scripts/entries/_app_gui_entry_impl.py`:27 · *hardcoded_path_hint*
  - from scripts.ui.theme import apply_theme_if_enabled   # running from C:\Piper
- **INFO** · `C:/Piper/scripts/entries/_app_gui_entry_impl.py`:29 · *hardcoded_path_hint*
  - from ui.theme import apply_theme_if_enabled           # running from C:\Piper\scripts

## C:/Piper/scripts/entries/_app_gui_entry_impl_backup.py
- **INFO** · `C:/Piper/scripts/entries/_app_gui_entry_impl_backup.py`:27 · *hardcoded_path_hint*
  - from scripts.ui.theme import apply_theme_if_enabled   # running from C:\Piper
- **INFO** · `C:/Piper/scripts/entries/_app_gui_entry_impl_backup.py`:29 · *hardcoded_path_hint*
  - from ui.theme import apply_theme_if_enabled           # running from C:\Piper\scripts

## C:/Piper/scripts/entries/app_cli_entry.py
- **INFO** · `C:/Piper/scripts/entries/app_cli_entry.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\entries\app_cli_entry.py
- **INFO** · `C:/Piper/scripts/entries/app_cli_entry.py`:68 · *hardcoded_path_hint*
  - from scripts.core.core_commands import core_banner as _cb  # when launched from C:\Piper
- **INFO** · `C:/Piper/scripts/entries/app_cli_entry.py`:70 · *hardcoded_path_hint*
  - from core.core_commands import core_banner as _cb          # when launched from C:\Piper\scripts

## C:/Piper/scripts/make_snapshot.py
- **INFO** · `C:/Piper/scripts/make_snapshot.py`:19 · *hardcoded_path_hint*
  - root = Path(__file__).resolve().parents[1]   # -> C:\Piper

## C:/Piper/scripts/personality.py
- **INFO** · `C:/Piper/scripts/personality.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\personality.py

## C:/Piper/scripts/services/asr/vosk_adapter.py
- **INFO** · `C:/Piper/scripts/services/asr/vosk_adapter.py`:9 · *hardcoded_path_hint*
  - # Robust import of shared paths (works from C:\Piper and C:\Piper\scripts)
- **INFO** · `C:/Piper/scripts/services/asr/vosk_adapter.py`:13 · *hardcoded_path_hint*
  - from common.paths import VOSK_MODEL, vosk_model_dir  # when CWD=C:\Piper\scripts

## C:/Piper/scripts/services/persona_adapter.py
- **INFO** · `C:/Piper/scripts/services/persona_adapter.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\services\persona_adapter.py

## C:/Piper/scripts/services/tts/speak_once.py
- **INFO** · `C:/Piper/scripts/services/tts/speak_once.py`:12 · *hardcoded_path_hint*
  - ROOT = Path(__file__).resolve().parents[3]  # -> C:\Piper

## C:/Piper/scripts/services/wake/porcupine_adapter.py
- **INFO** · `C:/Piper/scripts/services/wake/porcupine_adapter.py`:12 · *hardcoded_path_hint*
  - from common.paths import PORCUPINE_PPN  # when launched from C:\Piper\scripts

## C:/Piper/scripts/tests/old/test_event_queue.py
- **INFO** · `C:/Piper/scripts/tests/old/test_event_queue.py`:9 · *hardcoded_path_hint*
  - from core.event_queue import EventQueue  # cwd=C:\Piper\scripts

## C:/Piper/scripts/tests/old/test_flag_visibility.py
- **INFO** · `C:/Piper/scripts/tests/old/test_flag_visibility.py`:33 · *hardcoded_path_hint*
  - import entries.app_wake_entry as entry  # cwd = C:\Piper\scripts

## C:/Piper/scripts/tests/old/test_input_forwarder_eou.py
- **INFO** · `C:/Piper/scripts/tests/old/test_input_forwarder_eou.py`:27 · *hardcoded_path_hint*
  - import entries.app_wake_entry as entry  # cwd=C:\Piper\scripts

## C:/Piper/scripts/tests/old/test_timers_idle.py
- **INFO** · `C:/Piper/scripts/tests/old/test_timers_idle.py`:10 · *hardcoded_path_hint*
  - from core.timers import IdleTimer  # cwd=C:\Piper\scripts

## C:/Piper/scripts/tools/apply_b04_13b_refresh_core_split.py
- **INFO** · `C:/Piper/scripts/tools/apply_b04_13b_refresh_core_split.py`:37 · *commented_out_code*
  - # try without explicit return annotation

## C:/Piper/scripts/tools/audit_comments.py
- **INFO** · `C:/Piper/scripts/tools/audit_comments.py`:14 · *noisy_debug_note*
  - NOISY_DEBUG_PAT = re.compile(r"#.*(print\(|logger\.debug|verbose log)", re.I)
- **INFO** · `C:/Piper/scripts/tools/audit_comments.py`:23 · *hardcoded_path_hint*
  - HARDCODE_PAT = re.compile(r"#.*(C:\\Piper|G:\\My Drive|Dropbox|\\Users\\|\.gguf|\.pt|\.ckpt)", re.I)

## C:/Piper/scripts/tools/b04_splitter.py
- **INFO** · `C:/Piper/scripts/tools/b04_splitter.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\tools\b04_splitter.py
- **INFO** · `C:/Piper/scripts/tools/b04_splitter.py`:18 · *hardcoded_path_hint*
  - ROOT = Path(__file__).resolve().parents[1]  # C:\Piper\scripts

## C:/Piper/scripts/ui/dev_tools.py
- **INFO** · `C:/Piper/scripts/ui/dev_tools.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\ui\dev_tools.py

## C:/Piper/scripts/ui/heartbeat.py
- **INFO** · `C:/Piper/scripts/ui/heartbeat.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\ui\heartbeat.py

## C:/Piper/scripts/ui/ipc_child.py
- **INFO** · `C:/Piper/scripts/ui/ipc_child.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\ui\ipc_child.py

## C:/Piper/scripts/ui/tailer.py
- **INFO** · `C:/Piper/scripts/ui/tailer.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\ui\tailer.py

## C:/Piper/scripts/ui/theme.py
- **INFO** · `C:/Piper/scripts/ui/theme.py`:1 · *hardcoded_path_hint*
  - # C:\Piper\scripts\ui\theme.py
