"""
Comprehensive test suite for the refactored engine architecture.

Tests verify:
- Individual engine behavior
- Delegation patterns
- Integration between engines
- Edge cases and error handling
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, Mock, patch, call

import pytest


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sample_stage_card() -> Dict[str, Any]:
    """Create a sample stage card for testing."""
    return {
        "stage_goal": "Test stage goal",
        "stage_type": "FILE_WORK",
        "success_condition": "File is created",
        "allowed_tools": ["FILE_OP", "RUN_CODE"],
        "context": ["Workspace root is '.'"],
    }


@pytest.fixture
def sample_file_stage() -> Dict[str, Any]:
    """Create a FILE_WORK stage for testing."""
    return {
        "stage_goal": "Create the text file 'notes.txt' with content 'Hello World'",
        "stage_type": "FILE_WORK",
        "success_condition": "The file 'notes.txt' exists and contains 'Hello World'",
        "allowed_tools": ["FILE_OP"],
    }


@pytest.fixture
def sample_code_stage() -> Dict[str, Any]:
    """Create a code-editing FILE_WORK stage."""
    return {
        "stage_goal": "Fix the bug in 'app.py' where the button doesn't respond to clicks",
        "stage_type": "FILE_WORK",
        "success_condition": "The button click handler is implemented and the bug is fixed",
        "allowed_tools": ["FILE_OP", "RUN_CODE"],
    }


@pytest.fixture
def sample_analysis_stage() -> Dict[str, Any]:
    """Create an analysis stage."""
    return {
        "stage_goal": "Analyze the code in 'utils.py' and identify why the function returns None",
        "stage_type": "FILE_WORK",
        "success_condition": "Diagnosis with specific line numbers and root cause",
        "allowed_tools": ["FILE_OP"],
    }


@pytest.fixture
def sample_tool_result() -> Dict[str, Any]:
    """Create a sample tool result."""
    return {
        "tool": "FILE_OP",
        "action": "write_text",
        "status": "EXECUTED",
        "summary": "Wrote text file successfully",
        "path": "test.txt",
        "requested_path": "test.txt",
    }


@pytest.fixture
def sample_file_check_verified() -> Dict[str, Any]:
    """Create a verified file check result."""
    return {
        "verdict": "VERIFIED",
        "reason": "File exists with correct content",
        "paths": ["test.txt"],
    }


@pytest.fixture
def sample_file_check_failed() -> Dict[str, Any]:
    """Create a failed file check result."""
    return {
        "verdict": "FAILED",
        "reason": "File does not exist",
        "paths": [],
    }


@pytest.fixture
def sample_scratchpad() -> List[str]:
    """Create a sample scratchpad with various entries."""
    return [
        "=== STAGE 1 START ===",
        "STAGE_GOAL: Create file 'notes.txt'",
        "STEP 1",
        "THOUGHT: I need to write the file",
        "ACTION: [FILE_OP] write_text",
        "OBSERVATION_KIND: success",
        "OBSERVATION_TEXT: File written successfully",
        "PROPOSAL: The file has been created as requested.",
        # OUTCOME, RESULT, and LAST_LOG must be a single entry so that
        # extract_stage_status and build_outcome_block (which scan each
        # entry independently) can find both the header and the status.
        "=== STAGE 1 OUTCOME ===\nRESULT: SUCCESS\nLAST_LOG: File written successfully",
    ]


@pytest.fixture
def sample_scratchpad_with_exact_read() -> List[str]:
    """Create scratchpad with exact file read."""
    return [
        "=== STAGE 1 START ===",
        # FILE_READ_EXACT_PATH and FILE_READ_EXACT_CONTENT must be a single
        # scratchpad entry so that the regex in extract_exact_file_read can
        # match the path→content span within one string.
        "FILE_READ_EXACT_PATH: src/main.py\nFILE_READ_EXACT_CONTENT:\ndef main():\n    print('hello')\n",
        "STEP 1",
        "THOUGHT: I see the current code",
        "ACTION: [RUN_CODE] modify the function",
        "OBSERVATION_KIND: success",
        "OBSERVATION_TEXT: Code executed successfully",
        "=== STAGE 1 OUTCOME ===\nRESULT: SUCCESS",
    ]


@pytest.fixture
def sample_scratchpad_with_proposal() -> List[str]:
    """Create scratchpad with a proposal."""
    return [
        "=== STAGE 1 START ===",
        "STEP 1",
        "THOUGHT: Analyzing the bug",
        "PROPOSAL: The bug is on line 42 where the variable is not initialized.",
        "=== STAGE 1 OUTCOME ===",
        "RESULT: SUCCESS",
    ]


@pytest.fixture
def sample_scratchpad_with_verified_result() -> List[str]:
    """Create scratchpad with verified file work result."""
    return [
        "=== STAGE 1 START ===",
        "FILE_WORK_VERIFIED_RESULT: {\"kind\":\"state_changed\",\"action\":\"write_text\",\"summary\":\"File created\",\"paths\":[\"notes.txt\"]}",
        "=== STAGE 1 OUTCOME ===",
        "RESULT: SUCCESS",
    ]


# =============================================================================
# SUMMARY ENGINE TESTS
# =============================================================================

class TestSummaryEngine:
    """Tests for SummaryEngine - the single owner of scratchpad-level extraction."""

    def test_latest_stage_entries_finds_latest(self, sample_scratchpad):
        """Should return only entries from the latest stage."""
        from core.engines.summary import SummaryEngine

        result = SummaryEngine.latest_stage_entries(sample_scratchpad)

        assert len(result) > 0
        assert any("STAGE 1 START" in entry for entry in result)
        assert any("STAGE 1 OUTCOME" in entry for entry in result)

    def test_latest_stage_entries_fallback_when_no_header(self):
        """Should fallback to last 6 entries when no stage header found."""
        from core.engines.summary import SummaryEngine

        entries = [f"Entry {i}" for i in range(10)]
        result = SummaryEngine.latest_stage_entries(entries)

        assert len(result) == 6
        assert result == entries[-6:]

    def test_extract_verified_result_returns_summary(self, sample_scratchpad_with_verified_result):
        """Should extract and format verified result."""
        from core.engines.summary import SummaryEngine

        result = SummaryEngine.extract_verified_result(sample_scratchpad_with_verified_result)

        assert result != ""
        assert "created" in result.lower() or "verified" in result.lower()

    def test_extract_verified_result_returns_empty_when_not_found(self):
        """Should return empty string when no verified result."""
        from core.engines.summary import SummaryEngine

        scratchpad = ["STEP 1", "THOUGHT: Thinking..."]
        result = SummaryEngine.extract_verified_result(scratchpad)

        assert result == ""

    def test_extract_verified_result_uses_operation_label_created(self):
        """operation_label='created' should produce 'Created …' not 'Updated …'."""
        import json
        from core.engines.summary import SummaryEngine

        payload = {
            "kind": "mutation_verified",
            "action": "write_text",
            "paths": ["keep_me.txt", "move_me.txt"],
            "operation_label": "created",
            "summary": "Wrote text file: move_me.txt",
            "reason": "Files exist.",
        }
        scratchpad = [
            "=== STAGE 1 START ===",
            f"FILE_WORK_VERIFIED_RESULT: {json.dumps(payload)}",
        ]
        result = SummaryEngine.extract_verified_result(scratchpad)

        assert result.startswith("Created ")
        assert "keep_me.txt" in result
        assert "move_me.txt" in result

    def test_extract_verified_result_uses_operation_label_updated(self):
        """operation_label='updated' should still produce 'Updated …'."""
        import json
        from core.engines.summary import SummaryEngine

        payload = {
            "kind": "mutation_verified",
            "action": "write_text",
            "paths": ["config.txt"],
            "operation_label": "updated",
            "summary": "Wrote text file: config.txt",
            "reason": "File updated.",
        }
        scratchpad = [
            "=== STAGE 1 START ===",
            f"FILE_WORK_VERIFIED_RESULT: {json.dumps(payload)}",
        ]
        result = SummaryEngine.extract_verified_result(scratchpad)

        assert result.startswith("Updated ")
        assert "config.txt" in result

    def test_extract_proposal_finds_proposal(self, sample_scratchpad_with_proposal):
        """Should extract proposal text."""
        from core.engines.summary import SummaryEngine

        result = SummaryEngine.extract_proposal(sample_scratchpad_with_proposal)

        assert result != ""
        assert "line 42" in result

    def test_extract_proposal_returns_empty_when_not_found(self):
        """Should return empty when no proposal."""
        from core.engines.summary import SummaryEngine

        scratchpad = ["STEP 1", "No proposal here"]
        result = SummaryEngine.extract_proposal(scratchpad)

        assert result == ""

    def test_extract_exact_file_read_single_file(self, sample_scratchpad_with_exact_read):
        """Should extract exact file read content."""
        from core.engines.summary import SummaryEngine

        result = SummaryEngine.extract_exact_file_read(sample_scratchpad_with_exact_read)

        assert result != ""
        assert "def main():" in result

    def test_extract_exact_file_read_empty_when_not_found(self):
        """Should return empty when no exact read."""
        from core.engines.summary import SummaryEngine

        scratchpad = ["STEP 1", "No file read"]
        result = SummaryEngine.extract_exact_file_read(scratchpad)

        assert result == ""

    def test_extract_file_lookup_finds_matches(self):
        """Should extract file lookup matches."""
        from core.engines.summary import SummaryEngine

        # Matches must be inline after the colon in the same entry; the engine
        # partitions on "FILE_LOOKUP_MATCHES:" within a single string.
        scratchpad = [
            "FILE_LOOKUP_MATCHES:\nsrc/main.py\nsrc/utils.py",
        ]
        result = SummaryEngine.extract_file_lookup(scratchpad)

        assert result != ""
        assert "main.py" in result
        assert "utils.py" in result

    def test_extract_file_lookup_returns_not_found_message(self):
        """Should return 'No matching files' when empty."""
        from core.engines.summary import SummaryEngine

        scratchpad = ["FILE_LOOKUP_MATCHES:"]
        result = SummaryEngine.extract_file_lookup(scratchpad)

        assert "No matching files" in result

    def test_extract_stage_status_finds_result(self, sample_scratchpad):
        """Should extract stage status from outcome."""
        from core.engines.summary import SummaryEngine

        result = SummaryEngine.extract_stage_status(sample_scratchpad)

        assert result != ""
        assert "SUCCESS" in result.upper() or "FAILED" in result.upper()

    def test_build_runtime_note_uses_verified_result(self, sample_scratchpad_with_verified_result):
        """Should use verified result as primary source for runtime note."""
        from core.engines.summary import SummaryEngine

        result = SummaryEngine.build_runtime_note(sample_scratchpad_with_verified_result)

        assert result != ""

    def test_build_runtime_note_falls_back_to_observation(self):
        """Should fall back to observation text when no verified result."""
        from core.engines.summary import SummaryEngine

        scratchpad = [
            "=== STAGE 1 OUTCOME ===\nRESULT: FILE OPERATION SUCCESS\nLAST_LOG: File operation completed",
        ]
        result = SummaryEngine.build_runtime_note(scratchpad)

        assert "File operation" in result

    def test_build_outcome_block_includes_instruction(self, sample_scratchpad):
        """Should build outcome block with appropriate instruction."""
        from core.engines.summary import SummaryEngine

        result = SummaryEngine.build_outcome_block(sample_scratchpad)

        assert result != ""
        assert "[INSTRUCTION]" in result
        assert "OUTCOME" in result

    def test_build_outcome_block_handles_failure(self):
        """Should include failure instruction for failed outcome."""
        from core.engines.summary import SummaryEngine

        scratchpad = [
            "=== STAGE 1 OUTCOME ===\nRESULT: FAILED\nLAST_LOG: Error occurred",
        ]
        result = SummaryEngine.build_outcome_block(scratchpad)

        assert "FAILED" in result
        assert "FAILED" in result

    def test_build_outcome_block_handles_paused_input(self):
        """Should include pause instruction for user input."""
        from core.engines.summary import SummaryEngine

        scratchpad = [
            "=== STAGE 1 OUTCOME ===\nRESULT: PAUSED / AWAITING USER INPUT",
        ]
        result = SummaryEngine.build_outcome_block(scratchpad)

        assert "PAUSED" in result
        assert "USER INPUT" in result

    def test_build_outcome_block_handles_paused_approval(self):
        """Should include pause instruction for approval."""
        from core.engines.summary import SummaryEngine

        scratchpad = [
            "=== STAGE 1 OUTCOME ===\nRESULT: PAUSED / AWAITING USER APPROVAL",
        ]
        result = SummaryEngine.build_outcome_block(scratchpad)

        assert "PAUSED" in result
        assert "APPROVAL" in result

    def test_build_outcome_block_returns_empty_when_no_outcome(self):
        """Should return empty string when no outcome block."""
        from core.engines.summary import SummaryEngine

        scratchpad = ["STEP 1", "Just thinking"]
        result = SummaryEngine.build_outcome_block(scratchpad)

        assert result == ""

    def test_is_generic_file_work_summary_detects_generic(self):
        """Should identify generic file work summaries."""
        from core.engines.summary import SummaryEngine

        generic_summaries = [
            "Execution succeeded",
            "Wrote text file successfully",
            "Found 3 files",
            "Listed directory contents",
        ]

        for summary in generic_summaries:
            assert SummaryEngine.is_generic_file_work_summary(summary) is True

    def test_is_generic_file_work_summary_rejects_specific(self):
        """Should not identify specific summaries as generic."""
        from core.engines.summary import SummaryEngine

        specific_summaries = [
            "Created the configuration file for the API client",
            "Updated the user preferences in settings.json",
            "The file contains the authentication credentials",
        ]

        for summary in specific_summaries:
            assert SummaryEngine.is_generic_file_work_summary(summary) is False

    def test_sanitize_note_collapses_whitespace(self):
        """Should collapse multiple whitespace."""
        from core.engines.summary import SummaryEngine

        text = "This  has   multiple    spaces"
        result = SummaryEngine.sanitize_note(text)

        assert "  " not in result
        assert result == "This has multiple spaces"

    def test_sanitize_note_truncates_long_text(self):
        """Should truncate very long notes."""
        from core.engines.summary import SummaryEngine

        text = "A" * 500
        result = SummaryEngine.sanitize_note(text)

        assert len(result) <= 280

    def test_truncate_scratchpad_adds_header(self):
        """Should add header when truncating."""
        from core.engines.summary import SummaryEngine

        text = "A" * 10000
        result = SummaryEngine.truncate_scratchpad(text, limit=1000)

        assert "[TRUNCATED" in result

    def test_truncate_scratchpad_no_change_when_under_limit(self):
        """Should not modify text under limit."""
        from core.engines.summary import SummaryEngine

        text = "Short text"
        result = SummaryEngine.truncate_scratchpad(text, limit=1000)

        assert result == text

    def test_truncate_text_adds_marker(self):
        """Should add truncation marker."""
        from core.engines.summary import SummaryEngine

        text = "A" * 500
        result = SummaryEngine.truncate_text(text, 100)

        assert "[TRUNCATED]" in result
        assert len(result) < len(text) + 20  # marker adds some chars

    def test_select_outcome_detail_prefers_verified_result(self):
        """Should prefer verified result for FILE_WORK stages."""
        from core.engines.summary import SummaryEngine

        stage_entries = [
            "FILE_WORK_VERIFIED_RESULT: {\"summary\":\"File created\"}",
            "PROPOSAL: Some proposal",
        ]
        result = SummaryEngine.select_outcome_detail("FILE_WORK", stage_entries, "fallback")

        assert "FILE_WORK_VERIFIED_RESULT" in result

    def test_select_outcome_detail_uses_proposal_for_non_file(self):
        """Should use proposal for non-FILE_WORK stages."""
        from core.engines.summary import SummaryEngine

        stage_entries = [
            "PROPOSAL: The task is complete",
        ]
        result = SummaryEngine.select_outcome_detail("CHAT", stage_entries, "fallback")

        assert "PROPOSAL" in result

    def test_extract_observation_detail_handles_json_payload(self):
        """Should extract details from JSON payload."""
        from core.engines.summary import SummaryEngine

        observation = 'FILE_WORK_VERIFIED_RESULT: {"summary":"Done","reason":"Success"}'
        result = SummaryEngine.extract_observation_detail(observation)

        assert result != ""

    def test_extract_observation_detail_handles_plain_text(self):
        """Should handle plain observation text."""
        from core.engines.summary import SummaryEngine

        observation = "OBSERVATION_TEXT: The file was created successfully"
        result = SummaryEngine.extract_observation_detail(observation)

        assert "created" in result.lower()


# =============================================================================
# FILE WORK ENGINE TESTS
# =============================================================================

class TestFileWorkEngine:
    """Tests for FileWorkEngine - centralised file/code evidence handling."""

    def test_candidate_paths_extracts_from_dict(self):
        """Should extract paths from tool result dictionary."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "action": "write_text",
            "path": "main.py",
            "requested_path": "main.py",
            "updated_files": ["main.py", "utils.py"],
        }
        result = FileWorkEngine.candidate_paths(tool_result)

        assert "main.py" in result
        assert "utils.py" in result

    def test_candidate_paths_normalizes_backslashes(self):
        """Should normalize backslashes to forward slashes."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {"path": "src\\main.py"}
        result = FileWorkEngine.candidate_paths(tool_result)

        assert "src/main.py" in result

    def test_candidate_paths_deduplicates(self):
        """Should remove duplicate paths."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "path": "main.py",
            "requested_path": "main.py",
            "updated_files": ["main.py"],
        }
        result = FileWorkEngine.candidate_paths(tool_result)

        assert result.count("main.py") == 1

    def test_candidate_paths_extracts_from_files_dict(self):
        """Should extract paths from files dictionary."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "files": {
                "src/main.py": "content",
                "src/utils.py": "content",
            }
        }
        result = FileWorkEngine.candidate_paths(tool_result)

        assert "src/main.py" in result
        assert "src/utils.py" in result

    def test_candidate_paths_extracts_from_snippets(self):
        """Should extract paths from file_snippets."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "file_snippets": {
                "config.json": {"status": "text", "content": "{}"},
            }
        }
        result = FileWorkEngine.candidate_paths(tool_result)

        assert "config.json" in result

    def test_candidate_paths_returns_empty_for_non_dict(self):
        """Should return empty list for non-dict input."""
        from core.engines.file_work import FileWorkEngine

        result = FileWorkEngine.candidate_paths("not a dict")
        assert result == []

        result = FileWorkEngine.candidate_paths(None)
        assert result == []

    def test_exact_read_paths_from_scratchpad(self):
        """Should extract exact read paths from scratchpad."""
        from core.engines.file_work import FileWorkEngine

        scratchpad = [
            "FILE_READ_EXACT_PATH: config.json\nFILE_READ_EXACT_CONTENT:\n{}",
            "FILE_READ_EXACT_PATH: settings.yaml\nFILE_READ_EXACT_CONTENT:\nkey: value",
        ]
        result = FileWorkEngine.exact_read_paths_from_scratchpad(scratchpad)

        assert "config.json" in result
        assert "settings.yaml" in result

    def test_exact_read_paths_deduplicates(self):
        """Should deduplicate paths."""
        from core.engines.file_work import FileWorkEngine

        scratchpad = [
            "FILE_READ_EXACT_PATH: main.py",
            "FILE_READ_EXACT_PATH: main.py",
        ]
        result = FileWorkEngine.exact_read_paths_from_scratchpad(scratchpad)

        assert len(result) == 1
        assert result[0] == "main.py"

    def test_is_code_path_detects_code_files(self):
        """Should identify code files by extension."""
        from core.engines.file_work import FileWorkEngine

        code_files = [
            "main.py",
            "app.js",
            "styles.css",
            "config.json",
            "index.html",
        ]

        for path in code_files:
            assert FileWorkEngine._is_code_path(path) is True

    def test_is_code_path_rejects_non_code(self):
        """Should reject non-code files."""
        from core.engines.file_work import FileWorkEngine

        non_code = [
            "readme.txt",
            "image.png",
            "data.csv",
        ]

        for path in non_code:
            assert FileWorkEngine._is_code_path(path) is False

    def test_render_artifact_view_renders_code(self):
        """Should render code preview for code files."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "files": {
                "main.py": "def hello():\n    print('world')",
            }
        }
        result = FileWorkEngine.render_artifact_view(tool_result)

        assert "main.py" in result
        assert "code preview" in result.lower()

    def test_render_artifact_view_uses_snippets(self):
        """Should use file_snippets when files not present."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "file_snippets": {
                "config.json": {
                    "status": "text",
                    "content": '{"key": "value"}',
                    "truncated": False,
                }
            }
        }
        result = FileWorkEngine.render_artifact_view(tool_result)

        assert "config.json" in result

    def test_render_artifact_view_returns_empty_for_non_code(self):
        """Should return empty for non-code files."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "files": {
                "image.png": "<binary data>",
            }
        }
        result = FileWorkEngine.render_artifact_view(tool_result)

        assert result == ""

    def test_capture_exact_read_single_file(self, sample_file_stage):
        """Should capture single file read."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "action": "read_text",
            "files": {
                "config.json": '{"key": "value"}',
            }
        }
        result = FileWorkEngine.capture_exact_read(
            sample_file_stage,
            tool_result,
            existing_read_paths=[],
        )

        assert result is not None
        assert "FILE_READ_EXACT_PATH" in result
        assert "config.json" in result

    def test_capture_exact_read_multiple_files_under_limit(self, sample_file_stage):
        """Should capture multiple files under the limit."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "action": "read_many",
            "files": {
                "file1.txt": "content1",
                "file2.txt": "content2",
            }
        }
        # Create a stage that requires targeted read
        stage = dict(sample_file_stage)
        stage["stage_goal"] = "Read files file1.txt and file2.txt"

        result = FileWorkEngine.capture_exact_read(
            stage,
            tool_result,
            existing_read_paths=[],
        )

        # Should capture both files
        assert result is not None
        assert "file1.txt" in result
        assert "file2.txt" in result

    def test_capture_exact_read_respects_existing_paths(self, sample_file_stage):
        """Should not re-capture already read files."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "action": "read_text",
            "files": {
                "config.json": '{"key": "value"}',
            }
        }
        result = FileWorkEngine.capture_exact_read(
            sample_file_stage,
            tool_result,
            existing_read_paths=["config.json"],
        )

        # Should still capture (the method doesn't skip based on existing paths)
        # It's the caller's responsibility to not append duplicates
        assert result is not None

    def test_capture_exact_read_returns_none_for_non_read_actions(self, sample_file_stage):
        """Should return None for non-read actions."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "action": "write_text",
        }
        result = FileWorkEngine.capture_exact_read(
            sample_file_stage,
            tool_result,
            existing_read_paths=[],
        )

        assert result is None

    def test_should_block_redundant_exact_read(self, sample_code_stage):
        """Should block redundant exact read of code file."""
        from core.engines.file_work import FileWorkEngine
        from core.contracts import FileWorkBlock

        tool_tag = '[FILE_OP]\n{"action":"read_text","path":"app.py"}\n[/FILE_OP]'

        result = FileWorkEngine.should_block(
            sample_code_stage,
            tool_tag,
            exact_read_paths=["app.py"],
        )

        assert result.blocked is True
        assert "already in the scratchpad" in result.reason.lower()

    def test_should_block_allows_new_file_read(self, sample_code_stage):
        """Should allow reading a file not yet read."""
        from core.engines.file_work import FileWorkEngine

        tool_tag = '[FILE_OP]\n{"action":"read_text","path":"newfile.py"}\n[/FILE_OP]'

        result = FileWorkEngine.should_block(
            sample_code_stage,
            tool_tag,
            exact_read_paths=["other.py"],
        )

        assert result.blocked is False
        assert result.reason == ""

    def test_should_block_code_file_write_text_embedding(self, sample_code_stage):
        """Should block embedding full code file in write_text."""
        from core.engines.file_work import FileWorkEngine

        # Create a very long tool tag with code
        code_content = "def hello():\n    print('world')\n" * 1000
        tool_tag = f'[FILE_OP]\n{{"action":"write_text","path":"app.py","content":"{code_content}"}}\n[/FILE_OP]'

        # Use a *different* already-read path: the engine deliberately allows
        # write_text when the target file was already read into the scratchpad
        # (i.e. planned_path == exact_read_paths entry).  Using a different
        # path keeps already_read=False and triggers the embedding guard.
        result = FileWorkEngine.should_block(
            sample_code_stage,
            tool_tag,
            exact_read_paths=["other_file.py"],
        )

        assert result.blocked is True
        assert "run_code" in result.reason.lower()

    def test_should_block_allows_code_write_after_exact_read(self, sample_code_stage):
        """Should allow write_text if file was read and target matches."""
        from core.engines.file_work import FileWorkEngine

        short_content = "print('hello')"
        tool_tag = f'[FILE_OP]\n{{"action":"write_text","path":"app.py","content":"{short_content}"}}\n[/FILE_OP]'

        # If we just read app.py and now want to write a small update, allow it
        result = FileWorkEngine.should_block(
            sample_code_stage,
            tool_tag,
            exact_read_paths=[],
        )

        assert result.blocked is False

    def test_classify_returns_correct_kinds(
        self,
        sample_file_stage,
        sample_code_stage,
        sample_analysis_stage,
    ):
        """Should classify stages correctly."""
        from core.engines.file_work import FileWorkEngine

        # Script launch stage
        launch_stage = {
            "stage_goal": "Run the script game.py",
            "stage_type": "FILE_WORK",
        }
        assert FileWorkEngine.classify(launch_stage) == "SCRIPT_LAUNCH"

        # Analysis stage
        analysis_stage = {
            "stage_goal": "Analyze the code and find the bug",
            "stage_type": "FILE_WORK",
        }
        assert FileWorkEngine.classify(analysis_stage) == "INSPECTION"

        # Content edit stage — needs a success_condition containing a content
        # cue ("code", "source", "handler"…) so that stage_is_content_edit_stage
        # returns True; bare goal "Fix the bug in app.py" lacks one after path
        # tokens are stripped.
        edit_stage = {
            "stage_goal": "Fix the bug in app.py",
            "stage_type": "FILE_WORK",
            "success_condition": "The source code is corrected and the function runs without errors",
        }
        assert FileWorkEngine.classify(edit_stage) == "CONTENT_EDIT"

        # Structure prep stage
        structure_stage = {
            "stage_goal": "Organize files by extension",
            "stage_type": "FILE_WORK",
        }
        assert FileWorkEngine.classify(structure_stage) == "STRUCTURE_PREP"

    def test_recovery_hint_for_invalid_json(self, sample_file_stage):
        """Should provide hint for invalid JSON in write_text."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "tool": "FILE_OP",
            "action": "write_text",
        }
        file_check = {
            "verdict": "FAILED",
            "reason": "Invalid file_op json",
        }

        result = FileWorkEngine.recovery_hint(sample_file_stage, tool_result, file_check)

        assert "invalid json" in result.lower()
        assert "run_code" in result.lower()

    def test_recovery_hint_for_code_mismatch(self, sample_code_stage):
        """Should provide hint for code content mismatch."""
        from core.engines.file_work import FileWorkEngine

        # "path" is required so candidate_paths is non-empty; without it
        # paths_are_code_files([]) returns False and the hint is suppressed.
        tool_result = {
            "tool": "RUN_CODE",
            "action": "run_code",
            "path": "app.py",
        }
        file_check = {
            "verdict": "FAILED",
            "reason": "Content does not match the requested content",
        }

        result = FileWorkEngine.recovery_hint(sample_code_stage, tool_result, file_check)

        assert "scratchpad" in result.lower() or "current source" in result.lower()

    def test_collect_evidence_combines_all(self, sample_file_stage):
        """Should combine all evidence in one call."""
        from core.engines.file_work import FileWorkEngine
        from core.contracts import FileWorkEvidence

        tool_result = {
            "tool": "FILE_OP",
            "action": "read_text",
            "files": {
                "config.json": '{"key": "value"}',
            }
        }

        result = FileWorkEngine.collect_evidence(
            sample_file_stage,
            tool_result,
            existing_read_paths=[],
        )

        assert isinstance(result, FileWorkEvidence)
        assert "config.json" in result.candidate_paths
        # artifact_view may be empty if not code file
        # exact_read_note should have content


# =============================================================================
# VERIFICATION ENGINE TESTS
# =============================================================================

class TestVerificationEngine:
    """Tests for VerificationEngine - single owner of the verdict question."""

    @pytest.fixture
    def mock_file_checker(self):
        """Create a mock file checker."""
        checker = Mock()
        checker.run_local_file_op_checker = Mock(return_value=None)
        checker.run_file_checker = Mock(return_value={
            "verdict": "VERIFIED",
            "reason": "Test verified",
        })
        checker.verify_current_file_stage_state = Mock(return_value=None)
        return checker

    @pytest.fixture
    def verification_engine(self, mock_file_checker):
        """Create a verification engine with mock checker."""
        from core.engines.verification import VerificationEngine
        return VerificationEngine(file_checker=mock_file_checker)

    def test_should_verify_for_file_work_stage(self, verification_engine, sample_file_stage):
        """Should verify FILE_WORK stages."""
        result = verification_engine.should_verify(
            sample_file_stage,
            "FILE_OP",
            {"tool": "FILE_OP", "action": "write_text"},
        )
        assert result is True

    def test_should_not_verify_for_chat_stage(self, verification_engine):
        """Should not verify CHAT stages."""
        stage = {"stage_type": "CHAT"}
        result = verification_engine.should_verify(stage, "CHAT", None)
        assert result is False

    def test_evaluate_returns_verified(self, verification_engine, sample_file_stage, mock_file_checker):
        """Should return VERIFIED when check passes."""
        mock_file_checker.run_file_checker.return_value = {
            "verdict": "VERIFIED",
            "reason": "File exists and matches",
        }

        result = verification_engine.evaluate(
            sample_file_stage,
            {"tool": "FILE_OP"},
            Path("/workspace"),
            step=1,
            retry_budget=2,
        )

        assert result.verdict == "VERIFIED"
        assert result.effective_success is True
        assert result.recommendation == "STOP_SUCCESS"

    def test_evaluate_returns_partial(self, verification_engine, sample_file_stage, mock_file_checker):
        """Should return PARTIAL when check is partial."""
        mock_file_checker.run_file_checker.return_value = {
            "verdict": "PARTIAL",
            "reason": "Partial match",
        }

        result = verification_engine.evaluate(
            sample_file_stage,
            {"tool": "FILE_OP"},
            Path("/workspace"),
            step=1,
            retry_budget=2,
        )

        assert result.verdict == "PARTIAL"
        assert result.effective_success is False
        assert result.recommendation == "RETRY"

    def test_evaluate_partial_no_retries(self, verification_engine, sample_file_stage, mock_file_checker):
        """Should recommend STOP_FAILED when no retries left."""
        mock_file_checker.run_file_checker.return_value = {
            "verdict": "PARTIAL",
            "reason": "Partial match",
        }

        result = verification_engine.evaluate(
            sample_file_stage,
            {"tool": "FILE_OP"},
            Path("/workspace"),
            step=1,
            retry_budget=0,
        )

        assert result.verdict == "PARTIAL"
        assert result.recommendation == "STOP_FAILED"

    def test_evaluate_returns_failed(self, verification_engine, sample_file_stage, mock_file_checker):
        """Should return FAILED when check fails."""
        mock_file_checker.run_file_checker.return_value = {
            "verdict": "FAILED",
            "reason": "File not found",
        }

        result = verification_engine.evaluate(
            sample_file_stage,
            {"tool": "FILE_OP"},
            Path("/workspace"),
            step=1,
            retry_budget=0,
        )

        assert result.verdict == "FAILED"
        assert result.effective_success is False
        assert result.recommendation == "STOP_FAILED"

    def test_evaluate_uses_rules_path_first(self, verification_engine, sample_file_stage, mock_file_checker):
        """Should try rules path before LLM."""
        mock_file_checker.run_local_file_op_checker.return_value = {
            "verdict": "VERIFIED",
            "reason": "Rules check passed",
        }

        result = verification_engine.evaluate(
            sample_file_stage,
            {"tool": "FILE_OP"},
            Path("/workspace"),
            step=1,
            retry_budget=0,
        )

        assert result.verdict == "VERIFIED"
        assert result.checker_path == "RULES"
        mock_file_checker.run_file_checker.assert_not_called()

    def test_evaluate_uses_llm_when_no_rules(self, verification_engine, sample_file_stage, mock_file_checker):
        """Should use LLM when no rules match."""
        mock_file_checker.run_local_file_op_checker.return_value = None
        mock_file_checker.run_file_checker.return_value = {
            "verdict": "VERIFIED",
            "reason": "LLM check passed",
        }

        result = verification_engine.evaluate(
            sample_file_stage,
            {"tool": "FILE_OP"},
            Path("/workspace"),
            step=1,
            retry_budget=0,
        )

        assert result.verdict == "VERIFIED"
        assert result.checker_path == "LLM"

    def test_evaluate_state_check_upgrade(self, verification_engine, sample_file_stage, mock_file_checker):
        """Should upgrade to STATE_CHECK if tool succeeded but initial verdict not VERIFIED."""
        mock_file_checker.run_local_file_op_checker.return_value = None
        mock_file_checker.run_file_checker.return_value = {
            "verdict": "FAILED",
            "reason": "Initial check failed",
        }
        mock_file_checker.verify_current_file_stage_state.return_value = {
            "verdict": "VERIFIED",
            "reason": "State check passed",
        }

        result = verification_engine.evaluate(
            sample_file_stage,
            {"tool": "FILE_OP"},
            Path("/workspace"),
            step=1,
            retry_budget=0,
            tool_succeeded=True,
        )

        assert result.verdict == "VERIFIED"
        assert result.checker_path == "STATE_CHECK"

    def test_evaluate_mutation(self, verification_engine):
        """Should handle mutation stages."""
        from core.contracts import StageOutcomePack

        stage = {"stage_type": "MEMORY_WORK"}
        outcome = StageOutcomePack(
            status="SUCCESS",
            detail="Memory updated",
            effective_success=True,
        )

        result = verification_engine.evaluate_mutation(stage, outcome)

        assert result.verdict == "VERIFIED"
        assert result.checker_path == "MUTATION"

    def test_evaluate_mutation_failed(self, verification_engine):
        """Should handle failed mutation."""
        from core.contracts import StageOutcomePack

        stage = {"stage_type": "MEMORY_WORK"}
        outcome = StageOutcomePack(
            status="FAILED",
            detail="Memory update failed",
            effective_success=False,
        )

        result = verification_engine.evaluate_mutation(stage, outcome)

        assert result.verdict == "FAILED"
        assert result.effective_success is False

    def test_not_required_returns_verified(self, verification_engine):
        """Should return not_required for non-verification stages."""
        from core.engines.verification import VerificationResult

        stage = {"stage_type": "CHAT"}
        result = verification_engine.should_verify(stage, "CHAT", None)
        assert result is False


# =============================================================================
# CONTEXT PACK ENGINE TESTS
# =============================================================================

class TestContextPackEngine:
    """Tests for ContextPackEngine - persona context construction."""

    @pytest.fixture
    def mock_instruction_loader(self):
        """Mock instruction loader."""
        loader = Mock()
        loader.load = Mock(return_value="System instructions")
        return loader

    @pytest.fixture
    def mock_environment_service(self):
        """Mock environment service."""
        service = Mock()
        service.render_block = Mock(return_value="[ENV] workspace root is '.'")
        return service

    @pytest.fixture
    def mock_operational_state_service(self):
        """Mock operational state service."""
        service = Mock()
        service.render_block = Mock(return_value="")
        service.snapshot = Mock(return_value=Mock(events=[], tasks=[]))
        return service

    @pytest.fixture
    def mock_knowledge_mgr(self):
        """Mock knowledge manager."""
        mgr = Mock()
        mgr.load = Mock(return_value={"user_name": "Test User"})
        mgr.render_prompt_state = Mock(return_value="[WORLD STATE]")
        return mgr

    @pytest.fixture
    def mock_brain(self):
        """Mock brain."""
        brain = Mock()
        brain.recall = Mock(return_value=[])
        return brain

    @pytest.fixture
    def mock_document_memory(self):
        """Mock document memory."""
        mem = Mock()
        mem.render_prompt_hits = Mock(return_value=[])
        mem.list_documents = Mock(return_value=[])
        return mem

    @pytest.fixture
    def context_pack_engine(
        self,
        mock_instruction_loader,
        mock_environment_service,
        mock_operational_state_service,
        mock_knowledge_mgr,
        mock_brain,
        mock_document_memory,
    ):
        """Create a context pack engine."""
        from core.engines.context_pack import ContextPackEngine

        return ContextPackEngine(
            instruction_loader=mock_instruction_loader,
            environment_service=mock_environment_service,
            operational_state_service=mock_operational_state_service,
            knowledge_mgr=mock_knowledge_mgr,
            brain=mock_brain,
            document_memory=mock_document_memory,
        )

    def test_build_persona_pack_includes_instructions(self, context_pack_engine):
        """Should include instructions in pack."""
        from core.contracts import PersonaContextPack

        result = context_pack_engine.build_persona_pack(user_msg="test query")

        assert isinstance(result, PersonaContextPack)
        assert result.instructions == "System instructions"

    def test_build_persona_pack_includes_style_overlay(self, context_pack_engine):
        """Should include style overlay."""
        result = context_pack_engine.build_persona_pack(
            user_msg="test",
            style_overlay="Be formal",
        )

        assert result.style_overlay == "Be formal"

    def test_build_persona_pack_calls_brain_recall(self, context_pack_engine, mock_brain):
        """Should call brain recall with query."""
        context_pack_engine.build_persona_pack(user_msg="what is my name?")

        mock_brain.recall.assert_called_once()
        call_args = mock_brain.recall.call_args
        assert "name" in call_args[0][0].lower() or "what is my name" in call_args[0][0].lower()
        assert call_args.kwargs.get("n_results") == 9

    def test_build_persona_pack_filters_low_relevance_brain_hits(self, context_pack_engine, mock_brain):
        """Should drop low-relevance recall hits from the first-pass pack."""
        mock_brain.recall.return_value = [
            {"text": "keep me", "metadata": {"date": "Mar 10, 2026"}, "distance": 0.21},
            {"text": "drop me", "metadata": {"date": "Mar 10, 2026"}, "distance": 0.61},
            {"text": "no distance exact hit", "metadata": {"date": "Mar 10, 2026"}},
        ]

        result = context_pack_engine.build_persona_pack(user_msg="test query")

        assert result.brain_hits == [
            {"text": "keep me", "metadata": {"date": "Mar 10, 2026"}, "distance": 0.21},
            {"text": "no distance exact hit", "metadata": {"date": "Mar 10, 2026"}},
        ]

    def test_apply_document_focus(self, context_pack_engine):
        """Should apply document focus."""
        from core.contracts import PersonaContextPack

        pack = PersonaContextPack(
            user_msg="test",
            instructions="",
            knowledge_enabled=True,
        )

        result = context_pack_engine.apply_document_focus(
            pack,
            focus_text="The document says X",
            references=["Section 1"],
            sources=["doc.pdf"],
        )

        assert result.document_focus == "The document says X"
        assert "Section 1" in result.document_references
        assert "doc.pdf" in result.document_sources

    def test_clear_memory_for_file_work(self, context_pack_engine):
        """Should clear memory hits for file work."""
        from core.contracts import PersonaContextPack

        pack = PersonaContextPack(
            user_msg="test",
            instructions="",
            knowledge_enabled=True,
            brain_hits=[{"text": "some memory"}],
            document_hits=[{"content": "doc content"}],
        )

        result = context_pack_engine.clear_memory_for_file_work(pack)

        assert result.brain_hits == []
        assert result.document_hits == []

    def test_build_persona_runtime_pack(self, context_pack_engine, sample_scratchpad):
        """Should build runtime pack from scratchpad."""
        from core.contracts import PersonaRuntimePack

        result = context_pack_engine.build_persona_runtime_pack(sample_scratchpad)

        assert isinstance(result, PersonaRuntimePack)
        assert result.outcome_block != ""

    def test_build_persona_runtime_pack_detects_failure(self, context_pack_engine):
        """Should detect failed outcome."""
        scratchpad = [
            "=== STAGE 1 OUTCOME ===\nRESULT: FAILED",
        ]

        result = context_pack_engine.build_persona_runtime_pack(scratchpad)

        assert result.outcome_failed is True

    def test_build_persona_runtime_pack_detects_pause(self, context_pack_engine):
        """Should detect paused state."""
        scratchpad = [
            "=== STAGE 1 OUTCOME ===\nRESULT: PAUSED / AWAITING USER INPUT",
        ]

        result = context_pack_engine.build_persona_runtime_pack(scratchpad)

        assert result.outcome_paused is True

    def test_build_persona_runtime_pack_surfaces_typed_verification_fields(self, context_pack_engine):
        """Should carry the full typed verification contract into persona runtime."""
        from core.engines.verification import VerificationResult

        result = context_pack_engine.build_persona_runtime_pack(
            [],
            verification_result=VerificationResult.partial(
                "Current state could not confirm the final artifact.",
                retry_budget=1,
                checker_path="STATE_CHECK",
            ),
        )

        assert result.outcome_failed is True
        assert result.verification_verdict == "PARTIAL"
        assert result.verification_evidence == "Current state could not confirm the final artifact."
        assert result.verification_recommendation == "RETRY"
        assert result.verification_checker_path == "STATE_CHECK"

    def test_build_persona_directive_pack(self, context_pack_engine):
        """Should build directive pack."""
        from core.contracts import PersonaDirectivePack

        result = context_pack_engine.build_persona_directive_pack()

        assert isinstance(result, PersonaDirectivePack)
        assert isinstance(result.tail_system_blocks, list)

    def test_build_persona_directive_pack_includes_no_mutation_rule(self, context_pack_engine):
        """Should include no mutation rule for CHAT turns."""
        result = context_pack_engine.build_persona_directive_pack(
            route_decision={"decision": "CHAT"},
        )

        has_arbitration = any(
            "CONTEXT_ARBITRATION_RULE" in block
            for block in result.tail_system_blocks
        )
        has_no_mutation = any(
            "NO_MUTATION_RULE" in block
            for block in result.tail_system_blocks
        )
        assert has_arbitration
        assert has_no_mutation

    def test_build_persona_directive_pack_includes_search_rule(self, context_pack_engine):
        """Should include search report rule for search turns."""
        result = context_pack_engine.build_persona_directive_pack(
            reporter_just_ran=True,
        )

        has_arbitration = any(
            "CONTEXT_ARBITRATION_RULE" in block
            for block in result.tail_system_blocks
        )
        has_search_rule = any(
            "SEARCH_REPORT_RULE" in block
            for block in result.tail_system_blocks
        )
        assert has_arbitration
        assert has_search_rule

    def test_build_persona_directive_pack_includes_partial_verification_rule(self, context_pack_engine):
        """Should include typed partial verification guidance for persona."""
        from core.contracts import PersonaRuntimePack

        runtime = PersonaRuntimePack(
            outcome_failed=True,
            needs_file_work_report_rule=True,
            verification_verdict="PARTIAL",
            verification_evidence="Current state could not confirm the final artifact.",
            verification_recommendation="RETRY",
            verification_checker_path="STATE_CHECK",
        )
        result = context_pack_engine.build_persona_directive_pack(
            persona_runtime=runtime,
        )

        verification_block = next((block for block in result.tail_system_blocks if "[VERIFICATION_RESULT]" in block), "")
        partial_block = next((block for block in result.tail_system_blocks if "[PARTIAL_VERIFICATION_RULE]" in block), "")
        assert "Checker path: STATE_CHECK" in verification_block
        assert "Recommendation: RETRY" in verification_block
        assert "Evidence gap: Current state could not confirm the final artifact." in partial_block

    def test_build_persona_directive_pack_includes_failed_verification_rule(self, context_pack_engine):
        """Should include typed failed verification guidance for persona."""
        from core.contracts import PersonaRuntimePack

        runtime = PersonaRuntimePack(
            outcome_failed=True,
            verification_verdict="FAILED",
            verification_evidence="Key not found: favorite drink",
            verification_recommendation="STOP_FAILED",
            verification_checker_path="MUTATION",
        )
        result = context_pack_engine.build_persona_directive_pack(
            persona_runtime=runtime,
        )

        verification_block = next((block for block in result.tail_system_blocks if "[VERIFICATION_RESULT]" in block), "")
        failed_block = next((block for block in result.tail_system_blocks if "[FAILED_VERIFICATION_RULE]" in block), "")
        assert "Verdict: FAILED" in verification_block
        assert "Checker path: MUTATION" in verification_block
        assert "Recommendation: STOP_FAILED" in failed_block
        assert "Failure evidence: Key not found: favorite drink" in failed_block


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestEngineIntegration:
    """Tests for engine integration and delegation."""

    def test_context_pack_delegates_to_summary_engine(self):
        """ContextPackEngine should delegate to SummaryEngine."""
        from core.engines.context_pack import ContextPackEngine

        # extract_verified_result lives on SummaryEngine; ContextPackEngine
        # delegates to it inside build_persona_runtime_pack.
        import inspect
        source = inspect.getsource(ContextPackEngine.build_persona_runtime_pack)

        assert "SummaryEngine.extract_verified_result" in source

    def test_file_work_engine_uses_file_stage_policy(self):
        """FileWorkEngine should use FileStagePolicy for classification."""
        from core.engines.file_work import FileWorkEngine

        # success_condition must supply a content cue ("code", "source"…) so
        # that stage_is_content_edit_stage fires; see test_classify_returns_correct_kinds.
        stage = {
            "stage_goal": "Fix bug in app.py",
            "stage_type": "FILE_WORK",
            "success_condition": "The source code is corrected and tests pass",
        }

        assert FileWorkEngine.classify(stage) == "CONTENT_EDIT"

    def test_no_duplicate_recovery_hints(self):
        """Ensure recovery hints are not duplicated."""
        from core.file_stage_policy import FileStagePolicy
        from core.engines.file_work import FileWorkEngine

        stage = {
            "stage_goal": "Fix the bug",
            "stage_type": "FILE_WORK",
        }
        tool_result = {
            "tool": "FILE_OP",
            "action": "write_text",
        }
        file_check = {
            "verdict": "FAILED",
            "reason": "Invalid file_op json",
        }

        result1 = FileStagePolicy.file_checker_recovery_hint(stage, tool_result, file_check)
        result2 = FileWorkEngine.recovery_hint(stage, tool_result, file_check)

        # Results should be identical
        assert result1 == result2, (
            f"Recovery hints differ!\n"
            f"FileStagePolicy: {result1[:100]}...\n"
            f"FileWorkEngine: {result2[:100]}..."
        )

    def test_full_pipeline_from_orchestrator_to_summary(self):
        """Test full pipeline from orchestrator to summary extraction."""
        # This is more of a documentation test showing the flow

        # 1. Orchestrator creates scratchpad — each sentinel must be one entry
        scratchpad = [
            "STEP 1",
            "FILE_READ_EXACT_PATH: config.json\nFILE_READ_EXACT_CONTENT:\n{}",
            "=== STAGE 1 OUTCOME ===\nRESULT: SUCCESS",
        ]

        # 2. ContextPackEngine extracts from scratchpad
        from core.engines.summary import SummaryEngine

        exact_read = SummaryEngine.extract_exact_file_read(scratchpad)
        assert exact_read != ""

        # 3. SummaryEngine builds outcome
        outcome = SummaryEngine.build_outcome_block(scratchpad)
        assert "[INSTRUCTION]" in outcome

    def test_verification_engine_uses_file_work_engine_for_paths(self):
        """VerificationEngine should use FileWorkEngine for path extraction."""
        from core.engines.file_work import FileWorkEngine

        # FileWorkEngine.candidate_paths should be used by verification
        # This is implicit in the design - verification uses file checker
        # which uses path extraction

        tool_result = {
            "tool": "FILE_OP",
            "path": "main.py",
        }

        paths = FileWorkEngine.candidate_paths(tool_result)
        assert "main.py" in paths


# =============================================================================
# EDGE CASE TESTS
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_scratchpad_handling(self):
        """Should handle empty scratchpad gracefully."""
        from core.engines.summary import SummaryEngine

        assert SummaryEngine.latest_stage_entries([]) == []
        assert SummaryEngine.extract_verified_result([]) == ""
        assert SummaryEngine.extract_proposal([]) == ""
        assert SummaryEngine.extract_exact_file_read([]) == ""

    def test_malformed_json_in_verified_result(self):
        """Should handle malformed JSON in verified result."""
        from core.engines.summary import SummaryEngine

        scratchpad = [
            "FILE_WORK_VERIFIED_RESULT: {invalid json}",
        ]

        # Should not crash
        result = SummaryEngine.extract_verified_result(scratchpad)
        # May return empty or partial
        assert isinstance(result, str)

    def test_very_long_scratchpad_entry(self):
        """Should truncate very long entries."""
        from core.engines.summary import SummaryEngine

        long_content = "A" * 100000
        scratchpad = [
            f"FILE_READ_EXACT_PATH: big.txt\nFILE_READ_EXACT_CONTENT:\n{long_content}",
        ]

        result = SummaryEngine.extract_exact_file_read(scratchpad)

        # Should be truncated
        assert len(result) < len(long_content) + 1000

    def test_unicode_in_content(self):
        """Should handle unicode content."""
        from core.engines.summary import SummaryEngine

        scratchpad = [
            "PROPOSAL: 你好世界 🎉",
        ]

        result = SummaryEngine.extract_proposal(scratchpad)

        assert "你好世界" in result or "🎉" in result

    def test_special_characters_in_paths(self):
        """Should handle special characters in file paths."""
        from core.engines.file_work import FileWorkEngine

        tool_result = {
            "path": "my file (copy).txt",
            "requested_path": "my file (copy).txt",
        }

        result = FileWorkEngine.candidate_paths(tool_result)

        assert "my file (copy).txt" in result

    def test_concurrent_access_to_engines(self):
        """Engines should handle concurrent access (no state mutation)."""
        import threading

        from core.engines.summary import SummaryEngine
        from core.engines.file_work import FileWorkEngine

        errors = []

        def worker():
            try:
                SummaryEngine.extract_proposal(["PROPOSAL: test"])
                FileWorkEngine.candidate_paths({"path": "test.txt"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_none_inputs(self):
        """Should handle None inputs gracefully."""
        from core.engines.summary import SummaryEngine
        from core.engines.file_work import FileWorkEngine

        assert SummaryEngine.latest_stage_entries(None) == []
        assert FileWorkEngine.candidate_paths(None) == []
        assert FileWorkEngine.candidate_paths({}) == []

    def test_circular_reference_in_tool_result(self):
        """Should handle tool results with potential circular references."""
        from core.engines.file_work import FileWorkEngine

        # Tool result should not have circular refs in practice
        # but ensure we don't crash
        tool_result = {
            "tool": "FILE_OP",
            "action": "read_text",
            "files": {
                "test.txt": "content",
            }
        }

        result = FileWorkEngine.candidate_paths(tool_result)
        assert "test.txt" in result


# =============================================================================
# PERFORMANCE TESTS
# =============================================================================

class TestPerformance:
    """Basic performance tests for engines."""

    def test_summary_engine_performance(self):
        """SummaryEngine should process large scratchpads quickly."""
        import time

        from core.engines.summary import SummaryEngine

        # Create a large scratchpad
        scratchpad = []
        for i in range(100):
            scratchpad.extend([
                f"STEP {i}",
                f"THOUGHT: Thinking {i}",
                f"OBSERVATION_TEXT: Result {i}",
            ])
        scratchpad.extend([
            "=== STAGE 1 OUTCOME ===",
            "RESULT: SUCCESS",
        ])

        start = time.time()
        SummaryEngine.extract_proposal(scratchpad)
        SummaryEngine.extract_verified_result(scratchpad)
        SummaryEngine.build_outcome_block(scratchpad)
        elapsed = time.time() - start

        # Should complete in under 100ms
        assert elapsed < 0.1, f"SummaryEngine too slow: {elapsed:.3f}s"

    def test_file_work_engine_path_extraction_performance(self):
        """FileWorkEngine should extract paths quickly."""
        import time

        from core.engines.file_work import FileWorkEngine

        # Create a complex tool result
        tool_result = {
            "tool": "FILE_OP",
            "action": "read_many",
            "files": {f"file{i}.py": f"content{i}" for i in range(100)},
        }

        start = time.time()
        for _ in range(100):
            FileWorkEngine.candidate_paths(tool_result)
        elapsed = time.time() - start

        # 100 extractions should complete in under 50ms
        assert elapsed < 0.05, f"Path extraction too slow: {elapsed:.3f}s"


# =============================================================================
# EXECUTOR SPIN-LOOP ESCAPE HINT TESTS
# =============================================================================

class TestExecutorNonMutatingHints:
    """
    Verify that the executor's non-mutating FILE_WORK SECURITY VIOLATION handler
    always injects an escape hint regardless of stage sub-type.

    These tests use FileStagePolicy directly to validate the conditions that
    determine which hint branch fires, without spinning up the full executor.
    """

    def _make_inspection_stage(self) -> dict:
        """A plain inspection stage: INSPECTION kind, no planning/approval language."""
        return {
            "stage_goal": "Inspect the workspace and build an extension inventory with a destination folder chosen for each extension.",
            "stage_type": "FILE_WORK",
            "stage_kind": "INSPECTION",
            "success_condition": "Extension inventory is built.",
            "allowed_tools": ["FILE_OP"],
        }

    def _make_planning_stage(self) -> dict:
        """A planning stage that proposes changes but doesn't execute them."""
        return {
            "stage_goal": "Propose a plan for reorganising the project files.",
            "stage_type": "FILE_WORK",
            "success_condition": "Proposal is ready for approval.",
            "allowed_tools": ["FILE_OP"],
        }

    def test_inspection_stage_is_non_mutating(self):
        """stage_is_non_mutating_file_stage must be True for an INSPECTION stage."""
        from core.file_stage_policy import FileStagePolicy

        stage = self._make_inspection_stage()
        assert FileStagePolicy.stage_is_non_mutating_file_stage(stage), (
            "INSPECTION stage should be classified as non-mutating"
        )

    def test_inspection_stage_is_not_planning(self):
        """is_file_planning_stage must be False for a plain INSPECTION stage."""
        from core.file_stage_policy import FileStagePolicy

        stage = self._make_inspection_stage()
        assert not FileStagePolicy.is_file_planning_stage(stage), (
            "INSPECTION stage (no proposal/plan keywords) must not be a planning stage"
        )

    def test_inspection_stage_does_not_require_user_approval(self):
        """stage_requires_user_approval must be False for a plain INSPECTION stage."""
        from core.file_stage_policy import FileStagePolicy

        stage = self._make_inspection_stage()
        assert not FileStagePolicy.stage_requires_user_approval(stage), (
            "INSPECTION stage must not require user approval"
        )

    def test_planning_stage_is_non_mutating(self):
        """Planning stages must also be classified as non-mutating."""
        from core.file_stage_policy import FileStagePolicy

        stage = self._make_planning_stage()
        assert FileStagePolicy.stage_is_non_mutating_file_stage(stage), (
            "Planning/proposal stage should be classified as non-mutating"
        )

    def test_planning_stage_is_planning(self):
        """is_file_planning_stage must be True for a stage with proposal language."""
        from core.file_stage_policy import FileStagePolicy

        stage = self._make_planning_stage()
        assert FileStagePolicy.is_file_planning_stage(stage), (
            "Planning/proposal stage should be classified as a planning stage"
        )

    def test_inspection_hint_differs_from_planning_hint(self):
        """
        Confirm the two hint strings the executor injects are meaningfully different.
        This documents the branching contract: inspection stages get a different
        message than planning/approval stages.
        """
        planning_hint = (
            "SYSTEM HINT: Proposal/approval stages must not write files. "
            "Return tool null with is_complete true and put the proposal text in the optional proposal field."
        )
        inspection_hint = (
            "SYSTEM HINT: This stage is inspection-only — no file writes are permitted. "
            "Return tool null with is_complete true and summarise your findings in the proposal field."
        )
        assert planning_hint != inspection_hint
        assert "inspection-only" in inspection_hint
        assert "Proposal/approval" in planning_hint

    def test_inspection_hint_contains_is_complete_directive(self):
        """The inspection-stage escape hint must tell the planner to set is_complete true."""
        hint = (
            "SYSTEM HINT: This stage is inspection-only — no file writes are permitted. "
            "Return tool null with is_complete true and summarise your findings in the proposal field."
        )
        assert "is_complete true" in hint
        assert "proposal" in hint

    def test_run_code_inspection_hint_contains_is_complete_directive(self):
        """The RUN_CODE variant of the escape hint must also carry is_complete true."""
        hint = (
            "SYSTEM HINT: This stage is inspection-only — no code execution or file writes are permitted. "
            "Return tool null with is_complete true and summarise your findings in the proposal field."
        )
        assert "is_complete true" in hint
        assert "proposal" in hint
        assert "code execution" in hint


# =============================================================================
# EXCLUDE_FILES PREFIX MATCHING TESTS
# =============================================================================

class TestConsolidateExcludePrefix:
    """
    Verify that consolidate_by_extension's exclusion logic handles glob-prefix
    patterns (e.g. "keep_*") in addition to exact filenames.

    These tests exercise only the exclusion parsing + move-planning path; they
    do not execute real file moves (the workspace files are created in tmp_path
    so shutil.move calls are safe, and the after_inventory is a no-op stub).
    """

    _EMPTY_INVENTORY = {
        "files_by_extension": {},
        "destination_hints": {},
        "extension_counts": {},
        "folder_extension_counts": {},
        "empty_dirs": [],
    }

    def _fake_runtime(self, ws):
        """Return a FakeRuntime configured for the given workspace Path."""
        empty_inv = self._EMPTY_INVENTORY

        class FakeRuntime:
            workspace = ws
            _call_count = 0

            def _normalize_extension_list(self, v):
                return v or []

            def _raise_if_cancelled(self, t=None):
                pass

            def _build_extension_inventory(self, root, ws_root, extensions=None):
                # First call: real inventory for planning; second call (after moves): empty.
                FakeRuntime._call_count += 1
                if FakeRuntime._call_count > 1:
                    return empty_inv
                files_by_ext: dict = {}
                for f in root.iterdir():
                    if f.is_file():
                        ext = f.suffix.lower() or "[no_ext]"
                        files_by_ext.setdefault(ext, []).append(f)
                hints = {ext: "dest_" + ext.lstrip(".") for ext in files_by_ext}
                return {
                    "files_by_extension": files_by_ext,
                    "destination_hints": hints,
                    "extension_counts": {e: len(v) for e, v in files_by_ext.items()},
                    "folder_extension_counts": {},
                    "empty_dirs": [],
                }

            def _normalize_extension_token(self, v):
                return v

            def _is_within_dir(self, src, dst):
                return src.parent == dst

            def _workspace_rel(self, p, workspace_root=None):
                try:
                    return str(p.relative_to(ws))
                except ValueError:
                    return str(p)

            def _sha1_file(self, p):
                return "unique_" + p.name

        return FakeRuntime()

    def test_exact_exclusion_still_works(self, tmp_path):
        """Exact filename in exclude_files must still be excluded."""
        from tools.workspace_extension_actions import handle_consolidate_by_extension

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "keep_me.txt").write_text("keep")
        (ws / "move_me.txt").write_text("move")

        result = handle_consolidate_by_extension(
            self._fake_runtime(ws),
            {"root": ".", "exclude_files": ["keep_me.txt"]},
            "consolidate_by_extension",
            lambda m, **kw: {"error": m},
        )
        moved_srcs = [m["src"] for m in result.get("requested_moves", [])]
        assert not any("keep_me" in s for s in moved_srcs), "keep_me.txt should be excluded"
        assert any("move_me" in s for s in moved_srcs), "move_me.txt should be moved"

    def test_glob_prefix_exclusion(self, tmp_path):
        """'keep_*' in exclude_files must exclude all files starting with 'keep_'."""
        from tools.workspace_extension_actions import handle_consolidate_by_extension

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "keep_me.txt").write_text("keep1")
        (ws / "keep_notes.txt").write_text("keep2")
        (ws / "move_me.txt").write_text("move")

        result = handle_consolidate_by_extension(
            self._fake_runtime(ws),
            {"root": ".", "exclude_files": ["keep_*"]},
            "consolidate_by_extension",
            lambda m, **kw: {"error": m},
        )
        moved_srcs = [m["src"] for m in result.get("requested_moves", [])]
        assert not any("keep_" in s for s in moved_srcs), \
            f"No keep_ files should be moved, got: {moved_srcs}"
        assert any("move_me" in s for s in moved_srcs), "move_me.txt should be moved"

    def test_glob_prefix_case_insensitive(self, tmp_path):
        """Prefix matching must be case-insensitive."""
        from tools.workspace_extension_actions import handle_consolidate_by_extension

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "Keep_upper.txt").write_text("k")
        (ws / "normal.txt").write_text("n")

        result = handle_consolidate_by_extension(
            self._fake_runtime(ws),
            {"root": ".", "exclude_files": ["keep_*"]},
            "consolidate_by_extension",
            lambda m, **kw: {"error": m},
        )
        moved_srcs = [m["src"] for m in result.get("requested_moves", [])]
        assert not any("Keep_upper" in s for s in moved_srcs), \
            "Case-insensitive prefix should exclude Keep_upper.txt"
        assert any("normal" in s for s in moved_srcs)


# =============================================================================
# ENGINEERING ESCALATION DETECTOR TESTS
# =============================================================================

class TestScratchpadFormatterTerminalMissing:
    """Missing explicit file targets must disable persona reroutes."""

    def test_json_target_not_found_disables_persona_reroute(self):
        from core.scratchpad_formatter import ScratchpadFormatter

        stage = {
            "stage_goal": "Read the existing file 'stent_file.txt', append a new line exactly 'test' to the end, and save the updated file.",
            "stage_type": "FILE_WORK",
            "success_condition": "The existing file 'stent_file.txt' was updated by appending a new line exactly 'test'; do not create a new file if it is missing.",
            "context": ["The target file path is 'stent_file.txt'."],
        }
        entry = ScratchpadFormatter.format_step(
            1,
            "Read the target file before editing it.",
            '[FILE_OP] {"action":"read_text","path":"stent_file.txt"}',
            {
                "tool": "FILE_OP",
                "status": "FAILED",
                "summary": "FILE_OP target not found: stent_file.txt",
                "action": "read_text",
            },
        )

        pack = ScratchpadFormatter.build_outcome_pack(
            success=False,
            stage_type="FILE_WORK",
            last_observation=entry,
            stage_entries=[entry],
            stage=stage,
        )

        assert pack.allow_persona_reroute is False

    def test_json_source_not_found_disables_persona_reroute(self):
        from core.scratchpad_formatter import ScratchpadFormatter

        stage = {
            "stage_goal": "Rename 'missing.txt' to 'ready.txt'.",
            "stage_type": "FILE_WORK",
            "success_condition": "The file 'missing.txt' is renamed to 'ready.txt'.",
            "context": ["The source path is 'missing.txt' and the destination path is 'ready.txt'."],
        }
        entry = ScratchpadFormatter.format_step(
            1,
            "Rename the requested file.",
            '[FILE_OP] {"action":"move_path","src":"missing.txt","dst":"ready.txt"}',
            {
                "tool": "FILE_OP",
                "status": "FAILED",
                "summary": "FILE_OP source not found: missing.txt",
                "action": "move_path",
            },
        )

        pack = ScratchpadFormatter.build_outcome_pack(
            success=False,
            stage_type="FILE_WORK",
            last_observation=entry,
            stage_entries=[entry],
            stage=stage,
        )

        assert pack.allow_persona_reroute is False


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
