# Piper Release Readiness Checklist

Use this to decide whether Piper is ready for real daily use after a patch set or architecture phase.

## 1. Boot and Runtime

- Piper boots cleanly.
- The local LLM server becomes healthy.
- No stale repair-status noise appears at startup.
- Engineering boot probe reports sane state.
- Restart path works.
- Stop path works.

## 2. Core Interaction

- Normal chat still responds correctly.
- Search turns still route and summarize correctly.
- Simple task turns still route into `TASK`.
- Persona does not contradict verified outcomes.
- Loopback handling does not hide real failures.

## 3. File Work

- Direct file lookup works.
- Pronoun follow-up lookup works.
- Naming-mismatch follow-up works.
- Direct file read works.
- File edit works.
- Repeated already-satisfied file edit works.
- Copy/move/delete CRUD still work.
- Compound file requests like remove-then-read work.

Suggested automated coverage:
- [file_lookup_smoke_test.py](../../../scripts/file_lookup_smoke_test.py)
- [file_edit_smoke_test.py](../../../scripts/file_edit_smoke_test.py)
- [file_edit_compound_followup_smoke_test.py](../../../scripts/file_edit_compound_followup_smoke_test.py)
- [file_crud_smoke_test.py](../../../scripts/file_crud_smoke_test.py)

## 4. Code and Runtime Interaction

- Code-target follow-ups bind to the correct file.
- Code repair flow still works.
- Embedded `Code` tab session still launches.
- Interactive script input/output still behaves.
- Script-run turns do not fall back to bogus lookup-only behavior.

Suggested automated coverage:
- [code_target_followup_normalizer_smoke_test.py](../../../scripts/code_target_followup_normalizer_smoke_test.py)
- [code_repair_flow_smoke_test.py](../../../scripts/code_repair_flow_smoke_test.py)
- [code_session_smoke_test.py](../../../scripts/code_session_smoke_test.py)

## 5. Verification and Truthfulness

- `FILE_WORK` still requires verified state for true mutation success.
- Current-state verification does not fabricate failures.
- Persona uses current verified outcome instead of stale narration.
- `NO_MUTATION_RULE` is respected.

Suggested automated coverage:
- [file_checker_text_content_inference_smoke_test.py](../../../scripts/file_checker_text_content_inference_smoke_test.py)
- [extension_reorg_current_state_verifier_smoke_test.py](../../../scripts/extension_reorg_current_state_verifier_smoke_test.py)

## 6. Vision and Speech

- Vision commentary remains commentary, not literal screen narration spam.
- Vision session notes do not contaminate normal memory.
- Event speech policy still works.
- Noisy mode still dedupes repeated remarks.

Suggested automated coverage:
- [vision_prompt_hygiene_smoke_test.py](../../../scripts/vision_prompt_hygiene_smoke_test.py)
- [vision_session_memory_smoke_test.py](../../../scripts/vision_session_memory_smoke_test.py)
- [event_speech_policy_smoke_test.py](../../../scripts/event_speech_policy_smoke_test.py)

## 7. Engineering Support

- Escalation briefs can still be prepared.
- Repair bridge path still works.
- UI repair lifecycle still works.
- Worker leaves clean status instead of hanging.

## 8. Cleanliness

- `python3 -m compileall` passes for touched modules.
- Relevant focused smokes pass.
- No stray `llama-server`, harness, or repair-worker processes are left running.
- Notes are updated if behavior changed materially.

## Ready Means

Piper is ready for use when:
- the relevant automated coverage passes
- the touched workflow families behave correctly end-to-end
- the runtime is clean afterward
