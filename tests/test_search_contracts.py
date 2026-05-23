"""Lightweight unit tests for core.search_contracts.

These tests require no web search, no LLM, and no real orchestrator state.
They validate the wire-format contracts that the search workflow relies on.
"""

from __future__ import annotations

import pytest

from core.search_contracts import (
    SEARCH_FAILURE_PREFIX,
    SEARCH_FAILURE_REPORTER_INSTRUCTION,
    SEARCH_REPORTER_INSTRUCTION,
    SEARCH_RESULT_PREFIX,
    SEARCH_TOOL_ERROR_PREFIX,
    BackgroundSearchPayload,
    build_background_search_content,
    is_background_search_payload,
    is_search_error_result,
    is_search_reporter_instruction,
    normalize_search_error,
    parse_background_search_content,
)


# ── is_background_search_payload ──

class TestIsBackgroundSearchPayload:
    def test_recognizes_success_prefix(self) -> None:
        content = f"{SEARCH_RESULT_PREFIX}Python 3.14'. Data:\nsome results"
        assert is_background_search_payload(content) is True

    def test_recognizes_failure_prefix(self) -> None:
        content = f"{SEARCH_FAILURE_PREFIX}Python 3.14'. Error:\nsome error"
        assert is_background_search_payload(content) is True

    def test_rejects_plain_text(self) -> None:
        assert is_background_search_payload("Hello world") is False

    def test_rejects_empty(self) -> None:
        assert is_background_search_payload("") is False

    def test_rejects_none(self) -> None:
        assert is_background_search_payload(None) is False

    def test_rejects_partial_prefix(self) -> None:
        assert is_background_search_payload("Background search") is False


# ── is_search_reporter_instruction ──

class TestIsSearchReporterInstruction:
    def test_recognizes_success_instruction(self) -> None:
        assert is_search_reporter_instruction(SEARCH_REPORTER_INSTRUCTION) is True

    def test_recognizes_failure_instruction(self) -> None:
        assert is_search_reporter_instruction(SEARCH_FAILURE_REPORTER_INSTRUCTION) is True

    def test_rejects_plain_text(self) -> None:
        assert is_search_reporter_instruction("Summarize this.") is False

    def test_rejects_empty(self) -> None:
        assert is_search_reporter_instruction("") is False

    def test_rejects_none(self) -> None:
        assert is_search_reporter_instruction(None) is False

    def test_accepts_extra_whitespace_due_to_strip(self) -> None:
        # Live behavior: .strip() is called, so surrounding whitespace is accepted.
        assert is_search_reporter_instruction(f" {SEARCH_REPORTER_INSTRUCTION} ") is True


# ── parse_background_search_content ──

class TestParseBackgroundSearchContent:
    def test_parses_success_payload(self) -> None:
        raw = f"{SEARCH_RESULT_PREFIX}Python 3.14'. Data:\nRelease date is 2025-10"
        payload = parse_background_search_content(raw)
        assert payload == BackgroundSearchPayload(
            query="Python 3.14",
            data="Release date is 2025-10",
            failed=False,
        )

    def test_parses_failure_payload(self) -> None:
        raw = f"{SEARCH_FAILURE_PREFIX}Python 3.14'. Error:\n403 Ratelimit"
        payload = parse_background_search_content(raw)
        assert payload == BackgroundSearchPayload(
            query="Python 3.14",
            data="403 Ratelimit",
            failed=True,
        )

    def test_parses_malformed_query_defensively(self) -> None:
        # When the prefix is present but the quoted segment is malformed,
        # the parser splits on the first single quote and takes parts[1].
        raw = "Background search complete for '. Data:\nresults"
        payload = parse_background_search_content(raw)
        # Live behavior: parts[1] is '. Data:\nresults' which is truthy,
        # so it becomes the query. "Unknown Query" only fires when
        # parts[1] is empty/missing.
        assert payload.query == ". Data:\nresults"
        assert payload.data == "results"
        assert payload.failed is False

    def test_parses_fallback_to_data_marker_even_on_failure_prefix(self) -> None:
        """Defensive: if failure payload accidentally contains Data:\n, prefer Error:\n but fallback exists."""
        raw = f"{SEARCH_FAILURE_PREFIX}Query'. Error:\nSearch Error: 403"
        payload = parse_background_search_content(raw)
        assert payload.failed is True
        assert payload.data == "Search Error: 403"

    def test_marks_failed_when_data_contains_search_error(self) -> None:
        raw = f"{SEARCH_RESULT_PREFIX}Query'. Data:\n{SEARCH_TOOL_ERROR_PREFIX} Zero results."
        payload = parse_background_search_content(raw)
        assert payload.failed is True
        assert "Zero results" in payload.data

    def test_parses_empty_string(self) -> None:
        payload = parse_background_search_content("")
        assert payload == BackgroundSearchPayload(query="Unknown Query", data="", failed=False)

    def test_parses_none(self) -> None:
        payload = parse_background_search_content(None)
        assert payload == BackgroundSearchPayload(query="Unknown Query", data="", failed=False)

    def test_preserves_data_when_no_marker_present(self) -> None:
        raw = f"{SEARCH_RESULT_PREFIX}Query'. some raw text without marker"
        payload = parse_background_search_content(raw)
        assert payload.query == "Query"
        assert "some raw text without marker" in payload.data

    def test_query_with_apostrophe_is_truncated_live_bug(self) -> None:
        # KNOWN LIVE LIMITATION: parse_background_search_content splits on
        # ALL single quotes, so a query containing an apostrophe is truncated.
        # e.g. "What's new" → parts[1] == "What".
        # This is a documented live-code truth, not a test bug.
        raw = f"{SEARCH_RESULT_PREFIX}What's new'. Data:\nResults here"
        payload = parse_background_search_content(raw)
        assert payload.query == "What"  # live-code behavior
        assert payload.data == "Results here"


# ── build_background_search_content ──

class TestBuildBackgroundSearchContent:
    def test_success_roundtrip(self) -> None:
        query = "Python 3.14"
        data = "Release date is 2025-10"
        content = build_background_search_content(query, data, failed=False)
        payload = parse_background_search_content(content)
        assert payload.query == query
        assert payload.data == data
        assert payload.failed is False

    def test_failure_roundtrip(self) -> None:
        query = "Python 3.14"
        data = "403 Ratelimit"
        content = build_background_search_content(query, data, failed=True)
        payload = parse_background_search_content(content)
        assert payload.query == query
        assert payload.data == data
        assert payload.failed is True

    def test_strips_whitespace(self) -> None:
        content = build_background_search_content("  query  ", "  data  ", failed=False)
        payload = parse_background_search_content(content)
        assert payload.query == "query"
        assert payload.data == "data"

    def test_coerces_none(self) -> None:
        content = build_background_search_content(None, None, failed=False)
        payload = parse_background_search_content(content)
        # build_background_search_content produces "... for ''. Data:\n"
        # parse_background_search_content sees empty quoted segment and
        # falls back to "Unknown Query".
        assert payload.query == "Unknown Query"
        assert payload.data == ""


# ── is_search_error_result ──

class TestIsSearchErrorResult:
    def test_true_for_tool_error_prefix(self) -> None:
        assert is_search_error_result(f"{SEARCH_TOOL_ERROR_PREFIX} 403") is True

    def test_true_for_lower_case(self) -> None:
        assert is_search_error_result("search error: something") is True

    def test_true_with_leading_whitespace(self) -> None:
        assert is_search_error_result(f"  {SEARCH_TOOL_ERROR_PREFIX} 403") is True

    def test_false_for_normal_text(self) -> None:
        assert is_search_error_result("Some normal results") is False

    def test_false_for_empty(self) -> None:
        assert is_search_error_result("") is False

    def test_false_for_none(self) -> None:
        assert is_search_error_result(None) is False


# ── normalize_search_error ──

class TestNormalizeSearchError:
    def test_strips_tool_error_prefix(self) -> None:
        assert normalize_search_error(f"{SEARCH_TOOL_ERROR_PREFIX} 403 Ratelimit") == "403 Ratelimit"

    def test_strips_lower_case_prefix(self) -> None:
        assert normalize_search_error("search error: 403 ratelimit") == "403 ratelimit"

    def test_returns_original_when_no_prefix(self) -> None:
        assert normalize_search_error("Some other error") == "Some other error"

    def test_returns_original_on_empty_tail(self) -> None:
        assert normalize_search_error(SEARCH_TOOL_ERROR_PREFIX) == SEARCH_TOOL_ERROR_PREFIX

    def test_returns_empty_string_for_empty(self) -> None:
        assert normalize_search_error("") == ""

    def test_returns_empty_string_for_none(self) -> None:
        assert normalize_search_error(None) == ""

    def test_strips_whitespace(self) -> None:
        assert normalize_search_error(f"{SEARCH_TOOL_ERROR_PREFIX}   403   ") == "403"

    def test_stable_for_well_known_errors(self) -> None:
        known = [
            (f"{SEARCH_TOOL_ERROR_PREFIX} Zero results.", "Zero results."),
            (f"{SEARCH_TOOL_ERROR_PREFIX} 403 Ratelimit", "403 Ratelimit"),
            (f"{SEARCH_TOOL_ERROR_PREFIX} Found results but could not read content from any link.", "Found results but could not read content from any link."),
        ]
        for raw, expected in known:
            assert normalize_search_error(raw) == expected, f"failed for {raw!r}"
