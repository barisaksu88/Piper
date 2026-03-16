"""summary_engine_smoke_test.py

Verifies the SummaryEngine public API:

  1.  latest_stage_entries        — slices to last stage header; fallback to tail-6
  2.  extract_verified_result      — parses FILE_WORK_VERIFIED_RESULT JSON; state_already_satisfied
  3.  extract_proposal             — finds last PROPOSAL: payload
  4.  extract_exact_file_read      — single and multi-file FILE_READ_EXACT blocks
  5.  extract_file_lookup          — FILE_LOOKUP_MATCHES payload
  6.  extract_stage_status         — RESULT line from OUTCOME entry
  7.  build_runtime_note           — priority chain (verified → exact_path → lookup → LAST_LOG → OBSERVATION_TEXT)
  8.  build_outcome_block          — OUTCOME entry + [INSTRUCTION] directive
  9.  select_outcome_detail        — FILE_WORK_VERIFIED_RESULT > PROPOSAL > FILE_READ_EXACT_PATH
  10. extract_observation_detail   — FILE_WORK_VERIFIED_RESULT JSON; OBSERVATION_TEXT; tail
  11. is_generic_file_work_summary — generic prefixes return True; real text returns False
  12. sanitize_note                — collapse whitespace, cap at 280
  13. truncate_scratchpad          — tail-slice with header marker
  14. truncate_text                — tail-slice with [TRUNCATED] marker
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.summary import SummaryEngine  # noqa: E402


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SummaryEngineReport:
    # 1. latest_stage_entries
    entries_slices_to_latest_stage: bool
    entries_fallback_to_tail: bool

    # 2. extract_verified_result
    verified_write_action: bool
    verified_state_already_satisfied: bool
    verified_empty_on_miss: bool

    # 3. extract_proposal
    proposal_found: bool
    proposal_empty_on_miss: bool

    # 4. extract_exact_file_read
    exact_read_single: bool
    exact_read_multi: bool
    exact_read_empty_on_miss: bool

    # 5. extract_file_lookup
    lookup_found: bool
    lookup_empty_payload: bool
    lookup_empty_on_miss: bool

    # 6. extract_stage_status
    status_file_op_success: bool
    status_failed: bool
    status_paused: bool
    status_empty_on_miss: bool

    # 7. build_runtime_note
    note_from_verified: bool
    note_from_exact_path: bool
    note_from_last_log: bool
    note_from_observation_text: bool

    # 8. build_outcome_block
    block_success_instruction: bool
    block_failed_instruction: bool
    block_escalation_instruction: bool
    block_paused_input_instruction: bool
    block_empty_on_miss: bool

    # 9. select_outcome_detail
    detail_verified_result_priority: bool
    detail_proposal_priority: bool
    detail_fallback: bool

    # 10. extract_observation_detail
    obs_detail_verified_json: bool
    obs_detail_observation_text: bool
    obs_detail_tail: bool

    # 11. is_generic_file_work_summary
    generic_empty: bool
    generic_wrote_text: bool
    generic_real_text: bool

    # 12. sanitize_note
    sanitize_collapses_whitespace: bool
    sanitize_caps_at_280: bool

    # 13. truncate_scratchpad
    scratchpad_passthrough: bool
    scratchpad_truncated: bool

    # 14. truncate_text
    text_passthrough: bool
    text_truncated: bool

    success: bool


# ---------------------------------------------------------------------------
# 1. latest_stage_entries
# ---------------------------------------------------------------------------

def _test_latest_stage_entries() -> tuple[bool, bool]:
    old = "=== STAGE 1 START ===\nfirst stage entry"
    new_header = "=== STAGE 2 START ===\nsecond stage entry"
    new_step = "STEP 1\nTHOUGHT: x\nACTION: y\nOBSERVATION_KIND: info\nOBSERVATION_TEXT: z"
    scratchpad = [old, "old entry", new_header, new_step]
    entries = SummaryEngine.latest_stage_entries(scratchpad)
    ok_slices = new_header in entries and new_step in entries and old not in entries

    # fallback: no stage header → last 6
    no_header = ["a", "b", "c", "d", "e", "f", "g", "h"]
    fallback = SummaryEngine.latest_stage_entries(no_header)
    ok_fallback = fallback == ["c", "d", "e", "f", "g", "h"]

    return ok_slices, ok_fallback


# ---------------------------------------------------------------------------
# 2. extract_verified_result
# ---------------------------------------------------------------------------

def _test_extract_verified_result() -> tuple[bool, bool, bool]:
    payload_write = json.dumps({
        "action": "write_text",
        "summary": "Wrote text file",
        "reason": "The function foo was added",
        "paths": ["src/main.py"],
    })
    scratchpad_write = [
        "=== STAGE 1 START ===",
        f"FILE_WORK_VERIFIED_RESULT: {payload_write}",
    ]
    result_write = SummaryEngine.extract_verified_result(scratchpad_write)
    # write_text + label → "Updated src/main.py and verified the file change."
    ok_write = "Updated src/main.py" in result_write

    payload_satisfied = json.dumps({
        "kind": "state_already_satisfied",
        "paths": ["src/config.py"],
        "reason": "already present",
    })
    scratchpad_sat = [
        "=== STAGE 1 START ===",
        f"FILE_WORK_VERIFIED_RESULT: {payload_satisfied}",
    ]
    result_sat = SummaryEngine.extract_verified_result(scratchpad_sat)
    ok_sat = "already satisfied" in result_sat and "src/config.py" in result_sat

    ok_empty = SummaryEngine.extract_verified_result(["no verified result here"]) == ""

    return ok_write, ok_sat, ok_empty


# ---------------------------------------------------------------------------
# 3. extract_proposal
# ---------------------------------------------------------------------------

def _test_extract_proposal() -> tuple[bool, bool]:
    scratchpad = [
        "=== STAGE 1 START ===",
        "STEP 1\nTHOUGHT: done\nACTION: null\nOBSERVATION_KIND: info\nOBSERVATION_TEXT: ok\nPROPOSAL: The file now contains the fix.",
    ]
    proposal = SummaryEngine.extract_proposal(scratchpad)
    ok_found = "The file now contains the fix." in proposal

    ok_empty = SummaryEngine.extract_proposal(["no proposals here"]) == ""

    return ok_found, ok_empty


# ---------------------------------------------------------------------------
# 4. extract_exact_file_read
# ---------------------------------------------------------------------------

def _test_extract_exact_file_read() -> tuple[bool, bool, bool]:
    single = [
        "=== STAGE 1 START ===",
        "FILE_READ_EXACT_PATH: src/main.py\nFILE_READ_EXACT_CONTENT:\ndef foo(): pass",
    ]
    result_single = SummaryEngine.extract_exact_file_read(single)
    ok_single = "def foo(): pass" in result_single and "src/main.py" not in result_single

    multi = [
        "=== STAGE 1 START ===",
        (
            "FILE_READ_EXACT_PATH: src/a.py\nFILE_READ_EXACT_CONTENT:\nx = 1\n\n"
            "FILE_READ_EXACT_PATH: src/b.py\nFILE_READ_EXACT_CONTENT:\ny = 2"
        ),
    ]
    result_multi = SummaryEngine.extract_exact_file_read(multi)
    ok_multi = "src/a.py" in result_multi and "src/b.py" in result_multi and "x = 1" in result_multi

    ok_empty = SummaryEngine.extract_exact_file_read(["nothing here"]) == ""

    return ok_single, ok_multi, ok_empty


# ---------------------------------------------------------------------------
# 5. extract_file_lookup
# ---------------------------------------------------------------------------

def _test_extract_file_lookup() -> tuple[bool, bool, bool]:
    scratchpad = [
        "=== STAGE 1 START ===",
        "FILE_LOOKUP_MATCHES:\nsrc/main.py\nsrc/utils.py",
    ]
    result = SummaryEngine.extract_file_lookup(scratchpad)
    ok_found = "src/main.py" in result and "src/utils.py" in result

    empty_payload = [
        "=== STAGE 1 START ===",
        "FILE_LOOKUP_MATCHES:\n",
    ]
    ok_empty_payload = SummaryEngine.extract_file_lookup(empty_payload) == "No matching files found."

    ok_miss = SummaryEngine.extract_file_lookup(["no lookup here"]) == ""

    return ok_found, ok_empty_payload, ok_miss


# ---------------------------------------------------------------------------
# 6. extract_stage_status
# ---------------------------------------------------------------------------

def _test_extract_stage_status() -> tuple[bool, bool, bool, bool]:
    ok_success = SummaryEngine.extract_stage_status([
        "=== STAGE 1 START ===",
        "=== STAGE 1 OUTCOME ===\nRESULT: FILE OPERATION SUCCESS\nLAST_LOG: done",
    ]) == "FILE OPERATION SUCCESS"

    ok_failed = SummaryEngine.extract_stage_status([
        "=== STAGE 1 START ===",
        "=== STAGE 1 OUTCOME ===\nRESULT: FAILED / INCOMPLETE\nLAST_LOG: something broke",
    ]) == "FAILED / INCOMPLETE"

    ok_paused = SummaryEngine.extract_stage_status([
        "=== STAGE 1 START ===",
        "=== STAGE 1 OUTCOME ===\nRESULT: PAUSED / AWAITING USER INPUT\nLAST_LOG: need info",
    ]) == "PAUSED / AWAITING USER INPUT"

    ok_miss = SummaryEngine.extract_stage_status(["no outcome here"]) == ""

    return ok_success, ok_failed, ok_paused, ok_miss


# ---------------------------------------------------------------------------
# 7. build_runtime_note
# ---------------------------------------------------------------------------

def _test_build_runtime_note() -> tuple[bool, bool, bool, bool]:
    # 1. From verified result
    payload = json.dumps({"action": "write_text", "paths": ["src/x.py"], "summary": "Updated x.py", "reason": ""})
    note_verified = SummaryEngine.build_runtime_note([
        "=== STAGE 1 START ===",
        f"FILE_WORK_VERIFIED_RESULT: {payload}",
    ])
    ok_verified = "Updated" in note_verified or "src/x.py" in note_verified

    # 2. From exact read path (no verified result)
    note_path = SummaryEngine.build_runtime_note([
        "=== STAGE 1 START ===",
        "FILE_READ_EXACT_PATH: src/main.py\nFILE_READ_EXACT_CONTENT:\npass",
    ])
    ok_exact_path = "src/main.py" in note_path

    # 3. From LAST_LOG in OUTCOME
    note_log = SummaryEngine.build_runtime_note([
        "=== STAGE 1 START ===",
        "=== STAGE 1 OUTCOME ===\nRESULT: SUCCESS\nLAST_LOG: Deployment script ran without errors.",
    ])
    ok_last_log = "Deployment script ran without errors." in note_log

    # 4. From OBSERVATION_TEXT (no LAST_LOG, no verified, no exact path)
    note_obs = SummaryEngine.build_runtime_note([
        "=== STAGE 1 START ===",
        "STEP 1\nTHOUGHT: t\nACTION: a\nOBSERVATION_KIND: info\nOBSERVATION_TEXT: Build completed successfully.",
    ])
    ok_obs = "Build completed successfully." in note_obs

    return ok_verified, ok_exact_path, ok_last_log, ok_obs


# ---------------------------------------------------------------------------
# 8. build_outcome_block
# ---------------------------------------------------------------------------

def _test_build_outcome_block() -> tuple[bool, bool, bool, bool, bool]:
    success_outcome = "=== STAGE 1 OUTCOME ===\nRESULT: FILE OPERATION SUCCESS\nLAST_LOG: done"
    block_success = SummaryEngine.build_outcome_block(["=== STAGE 1 START ===", success_outcome])
    ok_success = "The task is complete" in block_success and "[INSTRUCTION]" in block_success

    failed_outcome = "=== STAGE 1 OUTCOME ===\nRESULT: FAILED / INCOMPLETE\nLAST_LOG: error"
    block_failed = SummaryEngine.build_outcome_block(["=== STAGE 1 START ===", failed_outcome])
    ok_failed = "FAILED" in block_failed and "LAST_LOG" in block_failed

    block_escalation = SummaryEngine.build_outcome_block(
        ["=== STAGE 1 START ===", failed_outcome],
        escalation_active=True,
    )
    ok_escalation = "engineering support has been briefed" in block_escalation

    paused_outcome = "=== STAGE 1 OUTCOME ===\nRESULT: PAUSED / AWAITING USER INPUT\nLAST_LOG: waiting"
    block_paused = SummaryEngine.build_outcome_block(["=== STAGE 1 START ===", paused_outcome])
    ok_paused = "paused pending user input" in block_paused

    ok_empty = SummaryEngine.build_outcome_block(["no outcome"]) == ""

    return ok_success, ok_failed, ok_escalation, ok_paused, ok_empty


# ---------------------------------------------------------------------------
# 9. select_outcome_detail
# ---------------------------------------------------------------------------

def _test_select_outcome_detail() -> tuple[bool, bool, bool]:
    verified_entry = "FILE_WORK_VERIFIED_RESULT: {}"
    proposal_entry = "STEP 1\nPROPOSAL: All done."
    exact_entry = "FILE_READ_EXACT_PATH: src/main.py\nFILE_READ_EXACT_CONTENT:\npass"
    fallback = "last observation"

    # FILE_WORK stage: verified result should win over proposal
    ok_verified = SummaryEngine.select_outcome_detail(
        "FILE_WORK",
        [proposal_entry, verified_entry],
        fallback,
    ) == verified_entry

    # No verified result: proposal wins
    ok_proposal = SummaryEngine.select_outcome_detail(
        "FILE_WORK",
        [exact_entry, proposal_entry],
        fallback,
    ) == proposal_entry

    # Nothing special → fallback
    ok_fallback = SummaryEngine.select_outcome_detail(
        "FILE_WORK",
        [],
        fallback,
    ) == fallback

    return ok_verified, ok_proposal, ok_fallback


# ---------------------------------------------------------------------------
# 10. extract_observation_detail
# ---------------------------------------------------------------------------

def _test_extract_observation_detail() -> tuple[bool, bool, bool]:
    # FILE_WORK_VERIFIED_RESULT JSON with real reason
    payload = json.dumps({"action": "write_text", "summary": "Wrote text file", "reason": "Added the foo function.", "paths": []})
    obs_verified = SummaryEngine.extract_observation_detail(f"FILE_WORK_VERIFIED_RESULT: {payload}")
    # Generic summary → reason returned
    ok_json = obs_verified == "Added the foo function."

    # OBSERVATION_TEXT prefix
    obs_text = SummaryEngine.extract_observation_detail(
        "STEP 1\nTHOUGHT: x\nACTION: y\nOBSERVATION_KIND: info\nOBSERVATION_TEXT: Script ran OK."
    )
    ok_obs = "Script ran OK." in obs_text

    # Plain tail
    long_text = "x" * 300
    obs_tail = SummaryEngine.extract_observation_detail(long_text)
    ok_tail = obs_tail == long_text[-200:]

    return ok_json, ok_obs, ok_tail


# ---------------------------------------------------------------------------
# 11. is_generic_file_work_summary
# ---------------------------------------------------------------------------

def _test_is_generic_file_work_summary() -> tuple[bool, bool, bool]:
    ok_empty = SummaryEngine.is_generic_file_work_summary("") is True
    ok_wrote = SummaryEngine.is_generic_file_work_summary("Wrote text file successfully") is True
    ok_real = SummaryEngine.is_generic_file_work_summary("Added the new foo function to main.py") is False
    return ok_empty, ok_wrote, ok_real


# ---------------------------------------------------------------------------
# 12. sanitize_note
# ---------------------------------------------------------------------------

def _test_sanitize_note() -> tuple[bool, bool]:
    ok_collapse = SummaryEngine.sanitize_note("  hello   world  ") == "hello world"
    long_text = "a" * 400
    ok_cap = len(SummaryEngine.sanitize_note(long_text)) == 280
    return ok_collapse, ok_cap


# ---------------------------------------------------------------------------
# 13. truncate_scratchpad
# ---------------------------------------------------------------------------

def _test_truncate_scratchpad() -> tuple[bool, bool]:
    short = "hello world"
    ok_passthrough = SummaryEngine.truncate_scratchpad(short, limit=100) == short

    long_text = "x" * 500
    result = SummaryEngine.truncate_scratchpad(long_text, limit=200)
    ok_truncated = (
        result.startswith("[TRUNCATED older scratchpad history]")
        and result.endswith("x" * 200)
    )
    return ok_passthrough, ok_truncated


# ---------------------------------------------------------------------------
# 14. truncate_text
# ---------------------------------------------------------------------------

def _test_truncate_text() -> tuple[bool, bool]:
    short = "hello"
    ok_passthrough = SummaryEngine.truncate_text(short, 100) == short

    long_text = "a" * 200
    result = SummaryEngine.truncate_text(long_text, 50)
    ok_truncated = result == "a" * 50 + "\n[TRUNCATED]"
    return ok_passthrough, ok_truncated


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_smoke() -> SummaryEngineReport:
    e1, e2 = _test_latest_stage_entries()
    v1, v2, v3 = _test_extract_verified_result()
    p1, p2 = _test_extract_proposal()
    x1, x2, x3 = _test_extract_exact_file_read()
    l1, l2, l3 = _test_extract_file_lookup()
    s1, s2, s3, s4 = _test_extract_stage_status()
    n1, n2, n3, n4 = _test_build_runtime_note()
    b1, b2, b3, b4, b5 = _test_build_outcome_block()
    d1, d2, d3 = _test_select_outcome_detail()
    o1, o2, o3 = _test_extract_observation_detail()
    g1, g2, g3 = _test_is_generic_file_work_summary()
    sn1, sn2 = _test_sanitize_note()
    ts1, ts2 = _test_truncate_scratchpad()
    tt1, tt2 = _test_truncate_text()

    success = all([
        e1, e2,
        v1, v2, v3,
        p1, p2,
        x1, x2, x3,
        l1, l2, l3,
        s1, s2, s3, s4,
        n1, n2, n3, n4,
        b1, b2, b3, b4, b5,
        d1, d2, d3,
        o1, o2, o3,
        g1, g2, g3,
        sn1, sn2,
        ts1, ts2,
        tt1, tt2,
    ])

    return SummaryEngineReport(
        entries_slices_to_latest_stage=e1,
        entries_fallback_to_tail=e2,
        verified_write_action=v1,
        verified_state_already_satisfied=v2,
        verified_empty_on_miss=v3,
        proposal_found=p1,
        proposal_empty_on_miss=p2,
        exact_read_single=x1,
        exact_read_multi=x2,
        exact_read_empty_on_miss=x3,
        lookup_found=l1,
        lookup_empty_payload=l2,
        lookup_empty_on_miss=l3,
        status_file_op_success=s1,
        status_failed=s2,
        status_paused=s3,
        status_empty_on_miss=s4,
        note_from_verified=n1,
        note_from_exact_path=n2,
        note_from_last_log=n3,
        note_from_observation_text=n4,
        block_success_instruction=b1,
        block_failed_instruction=b2,
        block_escalation_instruction=b3,
        block_paused_input_instruction=b4,
        block_empty_on_miss=b5,
        detail_verified_result_priority=d1,
        detail_proposal_priority=d2,
        detail_fallback=d3,
        obs_detail_verified_json=o1,
        obs_detail_observation_text=o2,
        obs_detail_tail=o3,
        generic_empty=g1,
        generic_wrote_text=g2,
        generic_real_text=g3,
        sanitize_collapses_whitespace=sn1,
        sanitize_caps_at_280=sn2,
        scratchpad_passthrough=ts1,
        scratchpad_truncated=ts2,
        text_passthrough=tt1,
        text_truncated=tt2,
        success=success,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Verify the SummaryEngine public API.")


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
