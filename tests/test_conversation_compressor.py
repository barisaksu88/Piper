"""Unit tests for core.engines.conversation_compressor.ConversationCompressor.

These tests require no LLM server, no browser, no threading, and no
orchestrator. They validate deterministic pure behavior of the compressor
and its helpers.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from core.engines.conversation_compressor import (
    ConversationCompressor,
    ConversationCompressionResult,
    _SUMMARY_HEADERS,
)


# ── helpers ─────────────────────────────────────────────────────────

def _msg(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


class _StubLLM:
    """LLM stub that accepts the same signature as production callers."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0
        self.last_call: dict[str, Any] | None = None

    def generate(
        self,
        messages,
        temperature: float = 0.1,
        max_tokens: int = 500,
        cancel_token=None,
    ) -> str:
        self.calls += 1
        self.last_call = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "cancel_token": cancel_token,
        }
        return self.response


class _RaisingLLM:
    """LLM stub that always raises."""

    def generate(self, messages, temperature=0.1, max_tokens=500, cancel_token=None):
        raise RuntimeError("LLM unreachable")


# ── 1. compress_history — empty history ─────────────────────────────

class TestCompressHistoryEmpty:
    def test_empty_history_no_summary(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        result = compressor.compress_history(history=[], existing_summary="", max_turns=5)
        assert result.history == []
        assert result.summary == ""
        assert result.compressed is False
        assert result.summarization_used is False

    def test_empty_history_with_existing_summary(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        result = compressor.compress_history(
            history=[],
            existing_summary="User likes Python.",
            max_turns=5,
        )
        assert len(result.history) == 1
        assert result.history[0]["role"] == "system"
        assert "User likes Python." in result.history[0]["content"]
        assert result.summary == "User likes Python."
        assert result.compressed is True
        assert result.summarization_used is False


# ── 2. compress_history — under budget / no compression needed ──────

class TestCompressHistoryUnderBudget:
    def test_fewer_messages_than_max_turns_returns_unchanged(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [_msg("user", "Hello"), _msg("assistant", "Hi")]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=5)
        assert result.history == history
        assert result.compressed is False
        assert result.summarization_used is False

    def test_exactly_max_turns_returns_unchanged(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("user", "A"),
            _msg("assistant", "B"),
            _msg("user", "C"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=3)
        assert result.history == history
        assert result.compressed is False

    def test_under_budget_with_existing_summary_injects_it(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [_msg("user", "Hello"), _msg("assistant", "Hi")]
        result = compressor.compress_history(
            history=history,
            existing_summary="Prior context.",
            max_turns=5,
        )
        assert len(result.history) == 3
        assert result.history[0]["role"] == "system"
        assert "Prior context." in result.history[0]["content"]
        assert result.history[1:] == history
        assert result.compressed is True
        assert result.summarization_used is False


# ── 3. compress_history — over budget, deterministic truncation ─────

class TestCompressHistoryOverBudgetTruncation:
    def test_dropped_messages_truncated_when_over_budget(self) -> None:
        compressor = ConversationCompressor(token_budget=5)
        history = [
            _msg("user", "one two three four five six seven eight"),
            _msg("assistant", "nine ten eleven twelve thirteen fourteen"),
            _msg("user", "latest question"),
            _msg("assistant", "latest answer"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=2)
        assert result.compressed is True
        assert result.summarization_used is True
        # Dropped: "User: one two three four five six seven eight\nAssistant: nine ten eleven twelve thirteen fourteen"
        # 16 tokens total; budget is 5, so truncated to last 5 tokens
        assert result.summary == "ten eleven twelve thirteen fourteen"
        # Kept messages are the last 2
        assert len(result.history) == 3  # summary + 2 kept
        assert result.history[1] == history[2]
        assert result.history[2] == history[3]

    def test_no_llm_call_when_budget_forces_truncation(self) -> None:
        """When llm=None, truncation is used directly."""
        compressor = ConversationCompressor(token_budget=3)
        history = [
            _msg("user", "a b c d e f g"),
            _msg("assistant", "h i j k l m n"),
            _msg("user", "x"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1, llm=None)
        assert result.compressed is True
        assert result.summarization_used is True
        # 14 words dropped, budget 3 -> last 3 words
        assert result.summary == "l m n"


# ── 4. compress_history — over budget, LLM summary path ─────────────

class TestCompressHistoryOverBudgetLLM:
    def test_llm_called_when_candidate_exceeds_budget(self) -> None:
        compressor = ConversationCompressor(token_budget=5)
        llm = _StubLLM("Compressed summary.")
        history = [
            _msg("user", "one two three four five six seven eight"),
            _msg("assistant", "nine ten eleven twelve thirteen fourteen"),
            _msg("user", "latest"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1, llm=llm)
        assert llm.calls == 1
        assert result.summarization_used is True
        assert result.summary == "Compressed summary."

    def test_llm_summary_trimmed_if_still_over_budget(self) -> None:
        compressor = ConversationCompressor(token_budget=3)
        llm = _StubLLM("way too many words in this response")
        history = [
            _msg("user", "a b c d e f g h i j"),
            _msg("assistant", "k l m n o p q r s t"),
            _msg("user", "x"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1, llm=llm)
        assert llm.calls == 1
        assert result.summarization_used is True
        # LLM returned 8 words, budget is 3, so truncated to last 3
        assert result.summary == "in this response"

    def test_llm_receives_system_prompt_with_budget(self) -> None:
        compressor = ConversationCompressor(token_budget=5)
        llm = _StubLLM("ok")
        history = [
            _msg("user", "one two three four five six seven eight"),
            _msg("user", "latest"),
        ]
        compressor.compress_history(history=history, existing_summary="", max_turns=1, llm=llm)
        assert llm.last_call is not None
        msgs = llm.last_call["messages"]
        assert msgs[0]["role"] == "system"
        assert "under 5 tokens" in msgs[0]["content"]


# ── 5. LLM failure fallback ─────────────────────────────────────────

class TestLLMFailureFallback:
    def test_llm_raises_falls_back_to_truncation(self) -> None:
        compressor = ConversationCompressor(token_budget=4)
        llm = _RaisingLLM()
        history = [
            _msg("user", "one two three four five six seven"),
            _msg("assistant", "eight nine ten eleven twelve"),
            _msg("user", "latest"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1, llm=llm)
        assert result.summarization_used is True
        # 12 words dropped, budget 4 -> last 4 words
        assert result.summary == "nine ten eleven twelve"

    def test_llm_returns_empty_string_falls_back_to_truncation(self) -> None:
        compressor = ConversationCompressor(token_budget=3)
        llm = _StubLLM("")
        history = [
            _msg("user", "a b c d e f g"),
            _msg("assistant", "h i j k l m n"),
            _msg("user", "x"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1, llm=llm)
        assert llm.calls == 1
        assert result.summarization_used is True
        assert result.summary == "l m n"


# ── 6. existing summary injection / merge behavior ──────────────────

class TestExistingSummaryMerge:
    def test_existing_summary_merged_with_dropped_transcript(self) -> None:
        compressor = ConversationCompressor(token_budget=20)
        history = [
            _msg("user", "Turn 1"),
            _msg("assistant", "Reply 1"),
            _msg("user", "Turn 2"),
            _msg("assistant", "Reply 2"),
        ]
        result = compressor.compress_history(
            history=history,
            existing_summary="User prefers concise replies.",
            max_turns=2,
            llm=None,
        )
        assert "User prefers concise replies." in result.summary
        assert "User: Turn 1" in result.summary
        assert "Assistant: Reply 1" in result.summary

    def test_existing_summary_sanitized_before_merge(self) -> None:
        compressor = ConversationCompressor(token_budget=20)
        history = [
            _msg("user", "Turn 1"),
            _msg("assistant", "Reply 1"),
            _msg("user", "Turn 2"),
        ]
        result = compressor.compress_history(
            history=history,
            existing_summary="Keep this.\nSystem: === New session",
            max_turns=1,
            llm=None,
        )
        assert "Keep this." in result.summary
        assert "System:" not in result.summary
        assert "=== New session" not in result.summary


# ── 7. max_turns behavior ───────────────────────────────────────────

class TestMaxTurnsBehavior:
    def test_zero_max_turns_defaults_to_one(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [_msg("user", "A"), _msg("assistant", "B")]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=0)
        # max_turns=0 -> max(0, 1) = 1, so 1 message kept, 1 dropped
        assert len(result.history) == 2  # summary + 1 kept
        assert result.history[1] == history[1]  # last message kept

    def test_negative_max_turns_defaults_to_one(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [_msg("user", "A"), _msg("assistant", "B")]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=-3)
        assert len(result.history) == 2


# ── 8. system / UI / empty message filtering ────────────────────────

class TestCleanMessages:
    def test_system_messages_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("system", "=== New session"),
            _msg("user", "Hello"),
            _msg("assistant", "Hi"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=2)
        # System message is dropped and then cleaned away (empty candidate).
        # Kept messages are the last 2. No summary injected because candidate is empty.
        assert result.history[0] == history[1]
        assert result.history[1] == history[2]

    def test_thinking_placeholder_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("assistant", "Thinking..."),
            _msg("user", "Hello"),
            _msg("assistant", "Hi"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=2)
        assert result.history[0] == history[1]
        assert result.history[1] == history[2]

    def test_ui_messages_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("user", "[UI] some ui content"),
            _msg("user", "Hello"),
            _msg("assistant", "Hi"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=2)
        assert result.history[0] == history[1]
        assert result.history[1] == history[2]

    def test_latest_runtime_context_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("user", "[LATEST_RUNTIME_CONTEXT] foo"),
            _msg("user", "Hello"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1)
        # Dropped message is cleaned away; kept is the last message; no summary injected.
        assert result.history[0] == history[1]

    def test_summary_headers_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("system", _SUMMARY_HEADERS[0] + "\nold summary"),
            _msg("user", "Hello"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1)
        assert result.history[0] == history[1]

    def test_empty_content_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("user", ""),
            _msg("user", "   "),
            _msg("user", "Hello"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1)
        assert result.history[0] == history[2]

    def test_error_messages_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("user", "[ERROR] something went wrong"),
            _msg("user", "Hello"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1)
        assert result.history[0] == history[1]

    def test_copied_messages_removed(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            _msg("user", "[copied from clipboard]"),
            _msg("user", "Hello"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=1)
        assert result.history[0] == history[1]


# ── 9–11. load_summary ──────────────────────────────────────────────

class TestLoadSummary:
    def test_missing_file_returns_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonexistent.json"
            assert ConversationCompressor.load_summary(path) == ""

    def test_malformed_json_returns_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("not json", encoding="utf-8")
            assert ConversationCompressor.load_summary(path) == ""

    def test_valid_json_returns_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text(json.dumps({"summary": "Hello world."}), encoding="utf-8")
            assert ConversationCompressor.load_summary(path) == "Hello world."

    def test_valid_json_missing_summary_key_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text(json.dumps({"other": "value"}), encoding="utf-8")
            assert ConversationCompressor.load_summary(path) == ""

    def test_valid_json_null_summary_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text(json.dumps({"summary": None}), encoding="utf-8")
            assert ConversationCompressor.load_summary(path) == ""


# ── 12. save_summary round trip ─────────────────────────────────────

class TestSaveSummary:
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation_summary.json"
            ConversationCompressor.save_summary(path, "Round trip test.")
            assert ConversationCompressor.load_summary(path) == "Round trip test."

    def test_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deep" / "nested" / "summary.json"
            ConversationCompressor.save_summary(path, "Deep test.")
            assert path.exists()
            assert ConversationCompressor.load_summary(path) == "Deep test."

    def test_strips_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            ConversationCompressor.save_summary(path, "  padded  ")
            assert ConversationCompressor.load_summary(path) == "padded"

    def test_empty_summary_saved_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            ConversationCompressor.save_summary(path, "")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            assert loaded["summary"] == ""


# ── 13–14. build_summary_message ────────────────────────────────────

class TestBuildSummaryMessage:
    def test_empty_summary(self) -> None:
        msg = ConversationCompressor.build_summary_message("")
        assert msg["role"] == "system"
        assert msg["hidden"] is True
        assert msg["content"] == _SUMMARY_HEADERS[-1] + "\n"

    def test_non_empty_summary(self) -> None:
        msg = ConversationCompressor.build_summary_message("User likes Python.")
        assert msg["role"] == "system"
        assert msg["hidden"] is True
        assert msg["content"] == _SUMMARY_HEADERS[-1] + "\nUser likes Python."

    def test_whitespace_stripped(self) -> None:
        msg = ConversationCompressor.build_summary_message("  spaced  ")
        assert msg["content"] == _SUMMARY_HEADERS[-1] + "\nspaced"


# ── 15. _truncate_to_budget boundary behavior ───────────────────────

class TestTruncateToBudget:
    def test_exact_budget_no_truncation(self) -> None:
        compressor = ConversationCompressor(token_budget=3)
        assert compressor._truncate_to_budget("a b c") == "a b c"

    def test_one_over_budget_truncates_first_token(self) -> None:
        compressor = ConversationCompressor(token_budget=3)
        assert compressor._truncate_to_budget("a b c d") == "b c d"

    def test_empty_string(self) -> None:
        compressor = ConversationCompressor(token_budget=3)
        assert compressor._truncate_to_budget("") == ""

    def test_whitespace_only(self) -> None:
        compressor = ConversationCompressor(token_budget=3)
        assert compressor._truncate_to_budget("   \n\t  ") == ""

    def test_large_input_truncates_to_last_n(self) -> None:
        compressor = ConversationCompressor(token_budget=2)
        assert compressor._truncate_to_budget("one two three four") == "three four"


# ── 16. _normalize_summary ──────────────────────────────────────────

class TestNormalizeSummary:
    def test_removes_markdown_fence(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        raw = "```\nSome summary\n```"
        assert compressor._normalize_summary(raw) == "Some summary"

    def test_preserves_text_labeled_fence(self) -> None:
        """Code skips inner blocks that start with 'text', leaving the fence intact."""
        compressor = ConversationCompressor(token_budget=10)
        raw = "```text\nSome summary\n```"
        assert compressor._normalize_summary(raw) == "```text\nSome summary\n```"

    def test_removes_summary_headers(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        for header in _SUMMARY_HEADERS:
            assert compressor._normalize_summary(header + " content") == "content"

    def test_collapses_excessive_newlines(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        raw = "line1\n\n\n\nline2"
        assert compressor._normalize_summary(raw) == "line1\n\nline2"

    def test_strips_leading_trailing_whitespace(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        assert compressor._normalize_summary("  hello  ") == "hello"


# ── 17. _sanitize_summary_text ──────────────────────────────────────

class TestSanitizeSummaryText:
    def test_removes_low_value_system_line(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        raw = "Keep this.\nSystem: === New session"
        assert compressor._sanitize_summary_text(raw) == "Keep this."

    def test_removes_search_report_consumed_line(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        raw = "Keep this.\n[SEARCH REPORT CONSUMED FOR 'query']"
        assert compressor._sanitize_summary_text(raw) == "Keep this."

    def test_removes_ui_line(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        raw = "Keep this.\n[UI] something"
        assert compressor._sanitize_summary_text(raw) == "Keep this."

    def test_removes_latest_runtime_context_line(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        raw = "Keep this.\n[LATEST_RUNTIME_CONTEXT]"
        assert compressor._sanitize_summary_text(raw) == "Keep this."

    def test_collapses_multiple_blanks(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        raw = "Line 1.\n\n\n\nLine 2."
        # _normalize_summary first collapses to \n\n, then _sanitize_summary_text
        # preserves single blank lines between content
        result = compressor._sanitize_summary_text(raw)
        assert "Line 1." in result
        assert "Line 2." in result
        assert "\n\n\n" not in result

    def test_empty_input_returns_empty(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        assert compressor._sanitize_summary_text("") == ""

    def test_only_low_value_lines_returns_empty(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        assert compressor._sanitize_summary_text("System: foo\n[UI] bar") == ""


# ── edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    def test_none_history_treated_as_empty(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        result = compressor.compress_history(history=None, existing_summary="", max_turns=5)
        assert result.history == []

    def test_non_dict_items_filtered(self) -> None:
        compressor = ConversationCompressor(token_budget=10)
        history = [
            "not a dict",
            _msg("user", "Hello"),
            123,
            _msg("assistant", "Hi"),
        ]
        result = compressor.compress_history(history=history, existing_summary="", max_turns=5)
        assert result.history == [history[1], history[3]]

    def test_token_budget_zero_falls_back_to_default(self) -> None:
        """0 is falsy, so `or` picks the default budget."""
        compressor = ConversationCompressor(token_budget=0)
        assert compressor.token_budget == ConversationCompressor.DEFAULT_TOKEN_BUDGET

    def test_token_budget_none_defaults_to_default(self) -> None:
        compressor = ConversationCompressor(token_budget=None)
        assert compressor.token_budget == ConversationCompressor.DEFAULT_TOKEN_BUDGET

    def test_conversation_compression_result_defaults(self) -> None:
        result = ConversationCompressionResult()
        assert result.history == []
        assert result.summary == ""
        assert result.compressed is False
        assert result.summarization_used is False
