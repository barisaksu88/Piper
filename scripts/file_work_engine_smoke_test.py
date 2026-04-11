"""file_work_engine_smoke_test.py

Verifies the FileWorkEngine public API:

  1.  candidate_paths        — superset extraction from various tool result shapes
  2.  exact_read_paths_from_scratchpad — parses FILE_READ_EXACT_PATH entries
  3.  render_artifact_view   — returns code preview for .py/.ts files, empty for .pdf
  4.  capture_exact_read     — read_text captured, read_many budget respected
  5.  should_block           — redundant-read guard, write-text guard, RUN_CODE domain-escape guard
  6.  recovery_hint          — FAILED verdict returns hint; non-FAILED returns ""
  7.  classify               — maps stage types to FileStageKind constants
  8.  CODE_FILE_EXTENSIONS   — .py in set, .pdf not in set
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

from core.engines.file_work import FileWorkEngine  # noqa: E402


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FileWorkEngineReport:
    # 1. candidate_paths
    candidate_paths_basic: bool
    candidate_paths_moves: bool
    candidate_paths_files_dict: bool
    candidate_paths_dedup: bool

    # 2. exact_read_paths_from_scratchpad
    exact_read_paths_found: bool
    exact_read_paths_dedup: bool

    # 3. render_artifact_view
    render_py_file: bool
    render_pdf_empty: bool
    render_snippet: bool

    # 4. capture_exact_read
    capture_read_text: bool
    capture_read_many_small: bool
    capture_read_many_no_budget: bool
    capture_non_read_none: bool

    # 5. should_block
    block_redundant_read_code: bool
    block_redundant_read_plain: bool
    block_exact_target_read_many: bool
    block_write_text_code: bool
    block_run_code_task_event_escape: bool
    no_block_run_code_file_only: bool
    no_block_no_overlap: bool
    no_block_non_edit_stage: bool

    # 6. recovery_hint
    hint_invalid_json: bool
    hint_run_code_mismatch: bool
    hint_verified_empty: bool

    # 7. classify
    classify_inspection: bool
    classify_content_edit: bool
    classify_structure_prep: bool
    classify_script_launch: bool
    classify_readback_inspection: bool

    # 8. CODE_FILE_EXTENSIONS
    extensions_py_in: bool
    extensions_pdf_not_in: bool

    success: bool


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _stage(stage_type: str, goal: str = "", success_condition: str = "") -> dict:
    return {
        "stage_type": stage_type,
        "stage_goal": goal,
        "success_condition": success_condition,
        "allowed_tools": ["FILE_OP"],
        "context": [],
    }


# ---------------------------------------------------------------------------
# 1. candidate_paths
# ---------------------------------------------------------------------------

def _test_candidate_paths() -> tuple[bool, bool, bool, bool]:
    basic = FileWorkEngine.candidate_paths({
        "requested_path": "src/main.py",
        "path": "src/main.py",
        "matches": ["src/utils.py"],
        "evidence_files": ["src/tests/test_main.py"],
    })
    ok_basic = "src/main.py" in basic and "src/utils.py" in basic and "src/tests/test_main.py" in basic
    # dedup: requested_path and path are the same → should appear once
    ok_dedup = basic.count("src/main.py") == 1

    moves = FileWorkEngine.candidate_paths({
        "requested_moves": [{"src": "old/file.py", "dst": "new/file.py"}],
    })
    ok_moves = "old/file.py" in moves and "new/file.py" in moves

    files_dict = FileWorkEngine.candidate_paths({
        "files": {"src/app.py": "code here", "src/helper.ts": "more code"},
    })
    ok_files = "src/app.py" in files_dict and "src/helper.ts" in files_dict

    return ok_basic, ok_moves, ok_files, ok_dedup


# ---------------------------------------------------------------------------
# 2. exact_read_paths_from_scratchpad
# ---------------------------------------------------------------------------

def _test_exact_read_paths() -> tuple[bool, bool]:
    scratchpad = [
        "FILE_READ_EXACT_PATH: src/main.py\nFILE_READ_EXACT_CONTENT:\nprint('hello')",
        "Some other entry",
        "FILE_READ_EXACT_PATH: src/main.py\nFILE_READ_EXACT_CONTENT:\nprint('hello')",  # duplicate
        "FILE_READ_EXACT_PATH: src/utils.py\nFILE_READ_EXACT_CONTENT:\ndef foo(): pass",
    ]
    paths = FileWorkEngine.exact_read_paths_from_scratchpad(scratchpad)
    ok_found = "src/main.py" in paths and "src/utils.py" in paths
    ok_dedup = paths.count("src/main.py") == 1
    return ok_found, ok_dedup


# ---------------------------------------------------------------------------
# 3. render_artifact_view
# ---------------------------------------------------------------------------

def _test_render_artifact_view() -> tuple[bool, bool, bool]:
    py_result = {
        "files": {"src/game.py": "import pygame\n\nclass Game: pass\n"},
    }
    view = FileWorkEngine.render_artifact_view(py_result)
    ok_py = "src/game.py" in view and "import pygame" in view

    pdf_result = {
        "files": {"docs/manual.pdf": b"\x25\x50\x44\x46"},
    }
    view_pdf = FileWorkEngine.render_artifact_view(pdf_result)
    ok_pdf = view_pdf == ""

    snippet_result = {
        "file_snippets": {
            "src/app.ts": {
                "status": "text",
                "content": "const x = 1;",
                "truncated": False,
                "full_char_count": 12,
            }
        }
    }
    view_snip = FileWorkEngine.render_artifact_view(snippet_result)
    ok_snip = "src/app.ts" in view_snip and "const x = 1;" in view_snip

    return ok_py, ok_pdf, ok_snip


# ---------------------------------------------------------------------------
# 4. capture_exact_read
# ---------------------------------------------------------------------------

def _test_capture_exact_read() -> tuple[bool, bool, bool, bool]:
    edit_stage = _stage("FILE_WORK", "edit the file", "file is updated")
    edit_stage_typed = {**edit_stage, "stage_goal": "Edit source code in main.py"}

    # read_text → always capture
    rt = {"action": "read_text", "files": {"src/main.py": "x = 1\n"}}
    note = FileWorkEngine.capture_exact_read(edit_stage_typed, rt, [])
    ok_read_text = note is not None and "FILE_READ_EXACT_PATH: src/main.py" in note

    # read_many with 1 file → capture
    rm1 = {"action": "read_many", "files": {"src/main.py": "x = 1\n"}}
    note2 = FileWorkEngine.capture_exact_read(edit_stage_typed, rm1, [])
    ok_read_many_small = note2 is not None and "FILE_READ_EXACT_PATH: src/main.py" in note2

    # read_many with 3 files and no targeted_read/content_edit → do NOT capture
    # We use a plain stage_type that isn't content edit
    inspection_stage = _stage("FILE_WORK", "look at all files")
    rm3 = {
        "action": "read_many",
        "files": {
            "a.py": "a",
            "b.py": "b",
            "c.py": "c",
        },
    }
    note3 = FileWorkEngine.capture_exact_read(inspection_stage, rm3, [])
    # 3 files > EXACT_READ_MAX_FILES (2) and non-targeted inspection stage → None
    ok_no_budget = note3 is None

    # non-read action → None
    write_result = {"action": "write_text", "files": {"src/main.py": "new content"}}
    note4 = FileWorkEngine.capture_exact_read(edit_stage_typed, write_result, [])
    ok_non_read = note4 is None

    return ok_read_text, ok_read_many_small, ok_no_budget, ok_non_read


# ---------------------------------------------------------------------------
# 5. should_block
# ---------------------------------------------------------------------------

def _test_should_block() -> tuple[bool, bool, bool, bool, bool, bool, bool, bool]:
    content_edit_stage = _stage(
        "FILE_WORK",
        "Edit the source code to add a new function",
        "The file contains the new function",
    )
    # Override stage_goal so FileStagePolicy classifies it as content_edit
    content_edit_stage = {
        "stage_type": "FILE_WORK",
        "stage_goal": "Edit and rewrite the source code in main.py to add a function",
        "success_condition": "The edited main.py contains the new function definition",
        "allowed_tools": ["FILE_OP", "RUN_CODE"],
        "context": [],
    }

    non_edit_stage = _stage("FILE_WORK", "Look at all files in the workspace")

    # Guard 1: redundant read of a code file already in scratchpad
    # planned_file_op_path parses "path" key (not "requested_path")
    exact_paths = ["src/main.py"]
    tool_tag_read = '[FILE_OP: {"action":"read_text","path":"src/main.py"}]'
    block = FileWorkEngine.should_block(content_edit_stage, tool_tag_read, exact_paths)
    ok_block_code_read = block.blocked and "Exact current source" in block.reason

    # Guard 1: redundant read of a plain (non-code) file (.txt is not in CODE_FILE_EXTENSIONS)
    exact_paths_plain = ["data/notes.txt"]
    tool_tag_read_plain = '[FILE_OP: {"action":"read_text","path":"data/notes.txt"}]'
    block_plain = FileWorkEngine.should_block(content_edit_stage, tool_tag_read_plain, exact_paths_plain)
    ok_block_plain_read = block_plain.blocked and "file contents" in block_plain.reason

    exact_read_stage = _stage(
        "FILE_WORK",
        "Locate grocery_list.txt and read its exact contents.",
        "The exact contents of grocery_list.txt are reported.",
    )
    tool_tag_read_many = '[FILE_OP: {"action":"read_many","paths":["grocery_list.txt","text_files/grocery_list.txt"]}]'
    block_exact_read_many = FileWorkEngine.should_block(exact_read_stage, tool_tag_read_many, [])
    ok_block_exact_read_many = block_exact_read_many.blocked and "exact file 'grocery_list.txt'" in block_exact_read_many.reason

    # Guard 2: write_text on a code file when exact read paths exist
    tool_tag_write = '[FILE_OP: {"action":"write_text","path":"src/other.py","content":"x=1"}]'
    block_write = FileWorkEngine.should_block(content_edit_stage, tool_tag_write, ["src/main.py"])
    ok_block_write = block_write.blocked and "RUN_CODE" in block_write.reason

    run_code_escape = """[RUN_CODE]
from workspace import list_events, close_event
events = list_events()
for event in events:
    if "keep_me.txt" in event.get("name", ""):
        close_event(event["id"])
        break
[/RUN_CODE]"""
    block_escape = FileWorkEngine._check_run_code_task_event_escape(run_code_escape)
    ok_block_escape = block_escape.blocked and "TASK_EVENT_WORK" in block_escape.reason

    run_code_file_only = """[RUN_CODE]
from pathlib import Path
Path("keep_me.txt").rename("archive/beta.txt")
[/RUN_CODE]"""
    block_file_only = FileWorkEngine._check_run_code_task_event_escape(run_code_file_only)
    ok_no_block_file_only = not block_file_only.blocked

    # No block: different file not in exact_paths
    tool_tag_new = '[FILE_OP: {"action":"read_text","path":"src/new_file.py"}]'
    block_new = FileWorkEngine.should_block(content_edit_stage, tool_tag_new, ["src/main.py"])
    ok_no_block = not block_new.blocked

    # No block: non-content-edit stage
    block_non_edit = FileWorkEngine.should_block(non_edit_stage, tool_tag_read, exact_paths)
    ok_no_block_non_edit = not block_non_edit.blocked

    return (
        ok_block_code_read,
        ok_block_plain_read,
        ok_block_exact_read_many,
        ok_block_write,
        ok_block_escape,
        ok_no_block_file_only,
        ok_no_block,
        ok_no_block_non_edit,
    )


# ---------------------------------------------------------------------------
# 6. recovery_hint
# ---------------------------------------------------------------------------

def _test_recovery_hint() -> tuple[bool, bool, bool]:
    fw_stage = {
        "stage_type": "FILE_WORK",
        "stage_goal": "Edit and rewrite the source code in main.py to fix the bug",
        "success_condition": "The edited source code contains the fix",
        "allowed_tools": ["FILE_OP", "RUN_CODE"],
        "context": [],
    }

    # invalid JSON in write_text
    tool_json_error = {"tool": "FILE_OP", "action": "write_text"}
    check_json_error = {"verdict": "FAILED", "reason": "invalid file_op json payload detected"}
    hint = FileWorkEngine.recovery_hint(fw_stage, tool_json_error, check_json_error)
    ok_json = "invalid JSON" in hint

    # RUN_CODE content mismatch on a code file
    tool_run_code = {"tool": "RUN_CODE", "action": "run_python", "evidence_files": ["src/main.py"]}
    check_mismatch = {
        "verdict": "FAILED",
        "reason": "content does not match the requested content — expected_present_texts not satisfied",
        "evidence_files": ["src/main.py"],
    }
    hint2 = FileWorkEngine.recovery_hint(fw_stage, tool_run_code, check_mismatch)
    ok_mismatch = "baseline" in hint2.lower()

    # VERIFIED → empty hint
    check_ok = {"verdict": "VERIFIED", "reason": "all checks pass"}
    hint3 = FileWorkEngine.recovery_hint(fw_stage, tool_run_code, check_ok)
    ok_verified = hint3 == ""

    return ok_json, ok_mismatch, ok_verified


# ---------------------------------------------------------------------------
# 7. classify
# ---------------------------------------------------------------------------

def _test_classify() -> tuple[bool, bool, bool, bool, bool]:
    inspection = _stage("FILE_WORK", "Read and inspect all files to diagnose the issue", "diagnosis complete")
    ok_inspection = FileWorkEngine.classify(inspection) == "INSPECTION"

    content_edit = {
        "stage_type": "FILE_WORK",
        "stage_goal": "Edit the source code in main.py to fix the off-by-one error",
        "success_condition": "The code contains the corrected loop logic",
        "allowed_tools": ["FILE_OP", "RUN_CODE"],
        "context": [],
    }
    ok_content_edit = FileWorkEngine.classify(content_edit) == "CONTENT_EDIT"

    structure = _stage(
        "FILE_WORK",
        "Create the required folder structure",
        "All destination directories exist",
    )
    ok_structure = FileWorkEngine.classify(structure) in {"STRUCTURE_PREP", "UNKNOWN"}

    script = {
        "stage_type": "FILE_WORK",
        "stage_goal": "Launch the game script to verify it runs without error",
        "success_condition": "The script starts without crashing",
        "allowed_tools": ["RUN_CODE"],
        "context": [],
    }
    ok_script = FileWorkEngine.classify(script) in {"SCRIPT_LAUNCH", "UNKNOWN"}

    readback = _stage(
        "FILE_WORK",
        'Read the updated exact contents of the workspace file matching "grocery list".',
        'A matching file is identified and its exact updated contents are read once after the requested removal.',
    )
    ok_readback = FileWorkEngine.classify(readback) == "INSPECTION"

    return ok_inspection, ok_content_edit, ok_structure, ok_script, ok_readback


# ---------------------------------------------------------------------------
# 8. CODE_FILE_EXTENSIONS
# ---------------------------------------------------------------------------

def _test_extensions() -> tuple[bool, bool]:
    ok_py = ".py" in FileWorkEngine.CODE_FILE_EXTENSIONS
    ok_pdf = ".pdf" not in FileWorkEngine.CODE_FILE_EXTENSIONS
    return ok_py, ok_pdf


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_smoke() -> FileWorkEngineReport:
    c1, c2, c3, c4 = _test_candidate_paths()
    e1, e2 = _test_exact_read_paths()
    r1, r2, r3 = _test_render_artifact_view()
    cap1, cap2, cap3, cap4 = _test_capture_exact_read()
    b1, b2, b3, b4, b5, b6, b7, b8 = _test_should_block()
    h1, h2, h3 = _test_recovery_hint()
    cl1, cl2, cl3, cl4, cl5 = _test_classify()
    ex1, ex2 = _test_extensions()

    success = all([
        c1, c2, c3, c4,
        e1, e2,
        r1, r2, r3,
        cap1, cap2, cap3, cap4,
        b1, b2, b3, b4, b5, b6, b7, b8,
        h1, h2, h3,
        cl1, cl2, cl3, cl4, cl5,
        ex1, ex2,
    ])

    return FileWorkEngineReport(
        candidate_paths_basic=c1,
        candidate_paths_moves=c2,
        candidate_paths_files_dict=c3,
        candidate_paths_dedup=c4,
        exact_read_paths_found=e1,
        exact_read_paths_dedup=e2,
        render_py_file=r1,
        render_pdf_empty=r2,
        render_snippet=r3,
        capture_read_text=cap1,
        capture_read_many_small=cap2,
        capture_read_many_no_budget=cap3,
        capture_non_read_none=cap4,
        block_redundant_read_code=b1,
        block_redundant_read_plain=b2,
        block_exact_target_read_many=b3,
        block_write_text_code=b4,
        block_run_code_task_event_escape=b5,
        no_block_run_code_file_only=b6,
        no_block_no_overlap=b7,
        no_block_non_edit_stage=b8,
        hint_invalid_json=h1,
        hint_run_code_mismatch=h2,
        hint_verified_empty=h3,
        classify_inspection=cl1,
        classify_content_edit=cl2,
        classify_structure_prep=cl3,
        classify_script_launch=cl4,
        classify_readback_inspection=cl5,
        extensions_py_in=ex1,
        extensions_pdf_not_in=ex2,
        success=success,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify the FileWorkEngine public API."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
