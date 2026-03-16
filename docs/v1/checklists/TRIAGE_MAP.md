# Piper Triage Map

Use this when something goes wrong and you need to know where to look first.

## 1. Routing Built the Wrong Task

Symptoms:
- stage goal is obviously wrong
- wrong file target
- literal pronoun/path fragments like `it back`
- wrong domain

Look first:
- [route_normalizer.py](../../../core/route_normalizer.py)
- [route_patterns.py](../../../core/route_patterns.py)
- [route_subjects.py](../../../core/route_subjects.py)
- [skills/selector.py](../../../core/skills/selector.py)

Logs:
- [llm_prompt_debug.txt](../../../data/debug/llm_prompt_debug.txt)
  - inspect the `SECRETARY` section

Relevant tests:
- [file_lookup_smoke_test.py](../../../scripts/file_lookup_smoke_test.py)
- [document_lookup_followup_normalizer_smoke_test.py](../../../scripts/document_lookup_followup_normalizer_smoke_test.py)
- [code_target_followup_normalizer_smoke_test.py](../../../scripts/code_target_followup_normalizer_smoke_test.py)

## 2. Planner Loops or Repeats the Same Read

Symptoms:
- repeated `read_text`
- repeated `find_paths`
- planner says file was truncated when it was not
- step budget burns without progress

Look first:
- [executor.py](../../../core/executor.py)
- [file_stage_policy.py](../../../core/file_stage_policy.py)
- [prompt_builder.py](../../../core/prompt_builder.py)

Logs:
- [llm_prompt_debug.txt](../../../data/debug/llm_prompt_debug.txt)
  - inspect `STAGE_*` sections and scratchpad history

Relevant tests:
- [redundant_code_read_guard_smoke_test.py](../../../scripts/redundant_code_read_guard_smoke_test.py)
- [file_stage_policy_smoke_test.py](../../../scripts/file_stage_policy_smoke_test.py)

## 3. File Edit Says Success But Reality Disagrees

Symptoms:
- file on disk differs from reported outcome
- repeated `already satisfied` lies
- stage says failed after correct mutation

Look first:
- [file_checker.py](../../../core/file_checker.py)
- [file_checker_rules.py](../../../core/file_checker_rules.py)
- [executor.py](../../../core/executor.py)
- [scratchpad_formatter.py](../../../core/scratchpad_formatter.py)

Logs:
- [llm_prompt_debug.txt](../../../data/debug/llm_prompt_debug.txt)
- recent file artifact under [data/workspace](../../../data/workspace)

Relevant tests:
- [file_edit_smoke_test.py](../../../scripts/file_edit_smoke_test.py)
- [file_edit_compound_followup_smoke_test.py](../../../scripts/file_edit_compound_followup_smoke_test.py)
- [file_checker_text_content_inference_smoke_test.py](../../../scripts/file_checker_text_content_inference_smoke_test.py)

## 4. CRUD or Path Ops Drift Into Lookup Logic

Symptoms:
- copy/move/delete turn says `No matching files found.`
- mutating file task gets turned into a path lookup only

Look first:
- [skills/selector.py](../../../core/skills/selector.py)
- [route_normalizer.py](../../../core/route_normalizer.py)
- [workspace_runtime.py](../../../tools/workspace_runtime.py)

Relevant tests:
- [file_crud_smoke_test.py](../../../scripts/file_crud_smoke_test.py)
- [skill_layer_smoke_test.py](../../../scripts/skill_layer_smoke_test.py)

## 5. Persona Says the Wrong Thing After a Correct Task

Symptoms:
- task succeeded but persona says failed
- persona invents a different reason than the logs
- stale failure is narrated after success

Look first:
- [orchestrator_phases.py](../../../core/orchestrator_phases.py)
- [prompting.py](../../../core/prompting.py)
- [scratchpad_formatter.py](../../../core/scratchpad_formatter.py)

Logs:
- [llm_prompt_debug.txt](../../../data/debug/llm_prompt_debug.txt)
  - compare `LAST_LOG`, `FINAL_STAGE_OUTCOME`, and persona input

Relevant tests:
- [persona_system_event_role_smoke_test.py](../../../scripts/persona_system_event_role_smoke_test.py)

## 6. Code Tab or Interactive Script Flow Is Broken

Symptoms:
- script says launched but nothing appears in `Code`
- input is not reaching the running script
- stop/rerun leaks old session state

Look first:
- [code_session.py](../../../core/code_session.py)
- [workspace_runtime.py](../../../tools/workspace_runtime.py)
- [controller.py](../../../ui/controller.py)
- [controller_queue.py](../../../ui/controller_queue.py)
- [controller_actions.py](../../../ui/controller_actions.py)

Relevant tests:
- [code_session_smoke_test.py](../../../scripts/code_session_smoke_test.py)

## 7. Vision Commentary or Event Speech Feels Wrong

Symptoms:
- repeated identical remarks
- Piper narrates the screen literally instead of commenting
- vision memory pollutes normal chat behavior

Look first:
- [vision_commentary.py](../../../ui/vision_commentary.py)
- [vision_session.py](../../../memory/vision_session.py)
- [event_speech.py](../../../ui/event_speech.py)
- [controller.py](../../../ui/controller.py)

Relevant tests:
- [vision_prompt_hygiene_smoke_test.py](../../../scripts/vision_prompt_hygiene_smoke_test.py)
- [vision_session_memory_smoke_test.py](../../../scripts/vision_session_memory_smoke_test.py)
- [event_speech_policy_smoke_test.py](../../../scripts/event_speech_policy_smoke_test.py)

## 8. Engineering Support Says It Triggered But Nothing Happens

Symptoms:
- support brief prepared but no repair outcome
- repair worker appears stuck
- boot probe green but repair path dead

Look first:
- [engineering_support.py](../../../core/engineering_support.py)
- [codex_bridge.py](../../../core/codex_bridge.py)
- [codex_repair_worker.py](../../../scripts/codex_repair_worker.py)
- [codex_repair_store.py](../../../memory/codex_repair_store.py)

State/log files:
- [codex_repair_status.json](../../../data/state/codex_repair_status.json)
- [codex_repair_worker.log](../../../data/debug/codex_repair_worker.log)
- [codex_escalations.jsonl](../../../data/debug/codex_escalations.jsonl)

Relevant tests:
- [codex_escalation_smoke_test.py](../../../scripts/codex_escalation_smoke_test.py)
- [codex_repair_bridge_smoke_test.py](../../../scripts/codex_repair_bridge_smoke_test.py)
- [codex_ui_repair_smoke_test.py](../../../scripts/codex_ui_repair_smoke_test.py)

## 9. Boot, Restart, or Server Lifecycle Is Wrong

Symptoms:
- startup hangs
- restart path closes but does not relaunch
- server disconnects or lingers after tests

Look first:
- [app.py](../../../app.py)
- [boot.py](../../../llm/boot.py)
- [llm_server_client.py](../../../llm/llm_server_client.py)
- [start_piper.bat](../../../start_piper.bat)

Logs:
- [llm_prompt_debug.txt](../../../data/debug/llm_prompt_debug.txt)

Useful checks:
- process sweep with `pgrep -af "llama-server|PiperHarness|codex_repair_worker"`

## 10. If You Are Unsure Where To Start

Start here:
- [llm_prompt_debug.txt](../../../data/debug/llm_prompt_debug.txt)
- then identify whether the failure started in:
  - route
  - plan
  - act
  - verify
  - persona

Then use the matching section above.
