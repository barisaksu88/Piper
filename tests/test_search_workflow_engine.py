"""Unit tests for core.engines.search_workflow.SearchWorkflowEngine.

These tests require no web search, no LLM, no threading, and no UI.
They validate the extracted pure helpers that back phase_search and phase_reporter.
"""

from __future__ import annotations

import pytest

from core.engines.search_workflow import SearchWorkflowEngine


@pytest.fixture
def engine() -> SearchWorkflowEngine:
    return SearchWorkflowEngine()


# ── build_search_failure_summary ──

class TestBuildSearchFailureSummary:
    def test_includes_all_required_lines(self, engine: SearchWorkflowEngine) -> None:
        summary = engine.build_search_failure_summary("Python 3.14", "Search Error: 403 Ratelimit")
        assert "The web search failed before usable results were retrieved." in summary
        assert "- Query: Python 3.14" in summary
        assert "- Error: 403 Ratelimit" in summary
        assert "- Verified web findings: none." in summary

    def test_normalizes_search_error_prefix(self, engine: SearchWorkflowEngine) -> None:
        summary = engine.build_search_failure_summary("query", "Search Error: Zero results.")
        assert "- Error: Zero results." in summary
        assert "Search Error:" not in summary.split("- Error:")[1]

    def test_fallback_unknown_query_when_empty(self, engine: SearchWorkflowEngine) -> None:
        summary = engine.build_search_failure_summary("", "")
        assert "- Query: Unknown Query" in summary

    def test_fallback_unknown_query_when_none(self, engine: SearchWorkflowEngine) -> None:
        summary = engine.build_search_failure_summary(None, None)  # type: ignore[arg-type]
        assert "- Query: Unknown Query" in summary
        assert "- Error: The search backend failed before returning usable results." in summary


# ── summarize_search_error_for_user ──

class TestSummarizeSearchErrorForUser:
    def test_zero_results(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user("Search Error: Zero results.") == "the search provider returned zero usable results"

    def test_403_ratelimit(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user("Search Error: 403 Ratelimit") == "the search provider returned HTTP 403 Ratelimit"

    def test_403_without_ratelimit(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user("Search Error: 403 Forbidden") == "the search provider returned HTTP 403"

    def test_rate_limit_without_403(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user("Search Error: rate limit exceeded") == "the search provider rate-limited the request"

    def test_generic_error(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user("Search Error: connection timeout") == "connection timeout"

    def test_no_prefix(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user("some random failure") == "some random failure"

    def test_empty(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user("") == "the search backend failed"

    def test_none(self, engine: SearchWorkflowEngine) -> None:
        assert engine.summarize_search_error_for_user(None) == "the search backend failed"  # type: ignore[arg-type]


# ── build_search_in_flight_reply ──

class TestBuildSearchInFlightReply:
    def test_different_active_and_requested(self, engine: SearchWorkflowEngine) -> None:
        reply = engine.build_search_in_flight_reply("Python", "JavaScript")
        assert 'running for "Python"' in reply
        assert 'ask again about "JavaScript"' in reply

    def test_only_active_query(self, engine: SearchWorkflowEngine) -> None:
        reply = engine.build_search_in_flight_reply("Python", "")
        assert 'running for "Python"' in reply
        assert "ask again if you want me to continue from there" in reply

    def test_only_requested_query(self, engine: SearchWorkflowEngine) -> None:
        reply = engine.build_search_in_flight_reply("", "JavaScript")
        assert "running right now" in reply
        assert 'ask again about "JavaScript"' in reply

    def test_neither_query(self, engine: SearchWorkflowEngine) -> None:
        reply = engine.build_search_in_flight_reply("", "")
        assert reply == "I already have a web search running right now. Let that finish first, then ask again and I will take the next search."

    def test_case_insensitive_match_considered_same(self, engine: SearchWorkflowEngine) -> None:
        reply = engine.build_search_in_flight_reply("Python", "python")
        # casefold match means they're considered the same, so "only active" path
        assert 'running for "Python"' in reply
        assert "ask again if you want me to continue from there" in reply

    def test_whitespace_stripped(self, engine: SearchWorkflowEngine) -> None:
        reply = engine.build_search_in_flight_reply("  Python  ", "  JavaScript  ")
        assert 'running for "Python"' in reply
        assert 'ask again about "JavaScript"' in reply


# ── build_search_first_pass_rule ──

class TestBuildSearchFirstPassRule:
    def test_includes_base_rules(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("test query")
        assert "[SEARCH_FIRST_PASS_RULE]" in rule
        assert "Search query: test query" in rule
        assert "Do not ask whether to proceed" in rule
        assert "Do not emit control tags such as [ROUTER] or [RECALL]." in rule

    def test_includes_externally_verifiable_facts_sentence(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("test query")
        assert "externally verifiable facts" in rule
        assert "frame uncertainty plainly and defer specifics until the web results arrive" in rule

    def test_omits_query_line_when_empty(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("")
        assert "[SEARCH_FIRST_PASS_RULE]" in rule
        assert "Search query:" not in rule

    def test_includes_recency_restrictions_for_latest(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("latest Python news")
        assert "The query is recency-sensitive." in rule
        assert "Do not state current/live facts" in rule
        assert "defer factual claims until the web results arrive" in rule

    def test_includes_recency_restrictions_for_today(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("what happened today")
        assert "The query is recency-sensitive." in rule

    def test_includes_recency_restrictions_for_headlines(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("current headlines")
        assert "The query is recency-sensitive." in rule

    def test_omits_recency_for_ordinary_query(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("Python string methods")
        assert "The query is recency-sensitive." not in rule
        assert "defer factual claims" not in rule

    def test_recency_case_insensitive(self, engine: SearchWorkflowEngine) -> None:
        rule = engine.build_search_first_pass_rule("LATEST release")
        assert "The query is recency-sensitive." in rule


# ── build_search_first_pass_fallback ──

class TestBuildSearchFirstPassFallback:
    def test_strips_search_prefix(self, engine: SearchWorkflowEngine) -> None:
        fallback = engine.build_search_first_pass_fallback("search the web for Python 3.14")
        assert 'checking the web for "Python 3.14"' in fallback

    def test_strips_look_up_prefix(self, engine: SearchWorkflowEngine) -> None:
        fallback = engine.build_search_first_pass_fallback("look up Django ORM")
        assert 'checking the web for "Django ORM"' in fallback

    def test_strips_find_prefix(self, engine: SearchWorkflowEngine) -> None:
        fallback = engine.build_search_first_pass_fallback("find best practices")
        assert 'checking the web for "best practices"' in fallback

    def test_strips_please_prefix(self, engine: SearchWorkflowEngine) -> None:
        fallback = engine.build_search_first_pass_fallback("please search for asyncio tutorial")
        assert 'checking the web for "asyncio tutorial"' in fallback

    def test_fallback_when_empty(self, engine: SearchWorkflowEngine) -> None:
        fallback = engine.build_search_first_pass_fallback("")
        assert fallback == "I'm checking the web for that now. I'll bring the results back automatically in a moment."

    def test_strips_trailing_punctuation(self, engine: SearchWorkflowEngine) -> None:
        fallback = engine.build_search_first_pass_fallback("search for Python?")
        assert 'checking the web for "Python"' in fallback


# ── build_search_preview_history ──

class TestBuildSearchPreviewHistory:
    def test_returns_user_msg_when_present(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("hello", "query")
        assert result == [{"role": "user", "content": "hello"}]

    def test_falls_back_to_query(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("", "query")
        assert result == [{"role": "user", "content": "query"}]

    def test_returns_empty_when_both_empty(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("", "")
        assert result == []

    def test_strips_whitespace(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("  hello  ", "  query  ")
        assert result == [{"role": "user", "content": "hello"}]

    def test_rewrites_source_choice_only(self, engine: SearchWorkflowEngine) -> None:
        # User said something like "search the web for that" which is detected as source-choice
        result = engine.build_search_preview_history("search the web", "Python async")
        assert result == [{"role": "user", "content": "Search the web for Python async."}]

    def test_rewrites_internet_please(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("please internet", "Python async")
        assert result == [{"role": "user", "content": "Search the web for Python async."}]

    def test_does_not_rewrite_when_user_msg_has_substantive_words(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("tell me about Python", "Python async")
        assert result == [{"role": "user", "content": "tell me about Python"}]

    def test_does_not_rewrite_when_same_as_query(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("Python async", "Python async")
        assert result == [{"role": "user", "content": "Python async"}]

    def test_rewrites_yes_online(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("yes online", "Rust concurrency")
        assert result == [{"role": "user", "content": "Search the web for Rust concurrency."}]

    def test_rewrites_do_internet(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_preview_history("do internet", "Go modules")
        assert result == [{"role": "user", "content": "Search the web for Go modules."}]


# ── build_search_report_history ──

class TestBuildSearchReportHistory:
    def test_keeps_latest_search_summary_and_user(self, engine: SearchWorkflowEngine) -> None:
        history = [
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old answer"},
            {"role": "system", "content": "[SEARCH SUMMARY FOR 'old']\nold summary", "hidden": True},
            {"role": "system", "content": "[SEARCH SUMMARY FOR 'new']\nnew summary", "hidden": True},
        ]
        result = engine.build_search_report_history(history, user_msg="current user")
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "[SEARCH SUMMARY FOR 'new']\nnew summary"}
        assert result[1] == {"role": "user", "content": "current user"}

    def test_extracts_query_from_summary_marker(self, engine: SearchWorkflowEngine) -> None:
        history = [
            {"role": "system", "content": "[SEARCH SUMMARY FOR 'extracted query']\nsummary text", "hidden": True},
        ]
        result = engine.build_search_report_history(history, user_msg="yes online")
        # The extracted query is used instead of user_msg for preview history
        assert result[1] == {"role": "user", "content": "Search the web for extracted query."}

    def test_extracts_query_with_double_quotes(self, engine: SearchWorkflowEngine) -> None:
        history = [
            {"role": "system", "content": '[SEARCH SUMMARY FOR "double quoted query"]\nsummary text', "hidden": True},
        ]
        result = engine.build_search_report_history(history, user_msg="yes online")
        assert result[1] == {"role": "user", "content": "Search the web for double quoted query."}

    def test_omits_non_system_messages(self, engine: SearchWorkflowEngine) -> None:
        history = [
            {"role": "user", "content": "user msg"},
            {"role": "assistant", "content": "assistant msg"},
        ]
        result = engine.build_search_report_history(history, user_msg="current")
        assert result == [{"role": "user", "content": "current"}]

    def test_returns_only_user_when_no_summary(self, engine: SearchWorkflowEngine) -> None:
        history = [
            {"role": "system", "content": "some other system msg"},
        ]
        result = engine.build_search_report_history(history, user_msg="user")
        assert result == [{"role": "user", "content": "user"}]

    def test_handles_none_history(self, engine: SearchWorkflowEngine) -> None:
        result = engine.build_search_report_history(None, user_msg="user")
        assert result == [{"role": "user", "content": "user"}]

    def test_matches_prefix_only(self, engine: SearchWorkflowEngine) -> None:
        history = [
            {"role": "system", "content": "SEARCH SUMMARY FOR 'x']\nnot a real marker"},
        ]
        result = engine.build_search_report_history(history, user_msg="user")
        assert result == [{"role": "user", "content": "user"}]


# ── prepare_reporter_context ──

class TestPrepareReporterContext:
    def test_success_payload_and_instruction_found(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_REPORTER_INSTRUCTION, build_background_search_content

        history = [
            {"role": "system", "content": build_background_search_content("Python 3.14", "Results here"), "hidden": True},
            {"role": "system", "content": SEARCH_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.raw_content == history[0]["content"]
        assert ctx.instruction_content == SEARCH_REPORTER_INSTRUCTION
        assert ctx.query == "Python 3.14"
        assert ctx.data == "Results here"
        assert ctx.failed is False
        assert ctx.normalized_error == ""

    def test_failure_payload_and_failure_instruction_found(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_FAILURE_REPORTER_INSTRUCTION, build_background_search_content

        history = [
            {"role": "system", "content": build_background_search_content("Python 3.14", "Search Error: 403", failed=True), "hidden": True},
            {"role": "system", "content": SEARCH_FAILURE_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.raw_content == history[0]["content"]
        assert ctx.instruction_content == SEARCH_FAILURE_REPORTER_INSTRUCTION
        assert ctx.query == "Python 3.14"
        assert ctx.data == "Search Error: 403"
        assert ctx.failed is True
        assert ctx.normalized_error == "403"

    def test_latest_payload_wins_when_multiple_exist(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import build_background_search_content

        history = [
            {"role": "system", "content": build_background_search_content("old query", "old data"), "hidden": True},
            {"role": "system", "content": build_background_search_content("new query", "new data"), "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.query == "new query"
        assert ctx.data == "new data"

    def test_latest_instruction_wins_when_multiple_exist(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_REPORTER_INSTRUCTION, SEARCH_FAILURE_REPORTER_INSTRUCTION

        history = [
            {"role": "system", "content": SEARCH_REPORTER_INSTRUCTION, "hidden": True},
            {"role": "system", "content": SEARCH_FAILURE_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.instruction_content == SEARCH_FAILURE_REPORTER_INSTRUCTION

    def test_non_system_messages_ignored(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_REPORTER_INSTRUCTION, build_background_search_content

        history = [
            {"role": "assistant", "content": build_background_search_content("x", "y")},
            {"role": "user", "content": SEARCH_REPORTER_INSTRUCTION},
            {"role": "system", "content": build_background_search_content("real", "data"), "hidden": True},
            {"role": "system", "content": SEARCH_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.query == "real"
        assert ctx.data == "data"
        assert ctx.instruction_content == SEARCH_REPORTER_INSTRUCTION

    def test_no_payload_preserves_fallback(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_reporter_context([])
        assert ctx.query == "Unknown Query"
        assert ctx.data == ""
        assert ctx.failed is False
        assert ctx.normalized_error == ""
        assert ctx.raw_content == ""
        assert ctx.instruction_content == ""

    def test_none_history_safe(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_reporter_context(None)
        assert ctx.query == "Unknown Query"
        assert ctx.failed is False

    def test_malformed_non_dict_items_skipped(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_REPORTER_INSTRUCTION, build_background_search_content

        history = [
            None,
            "not a dict",
            123,
            {"role": "system", "content": build_background_search_content("safe", "data"), "hidden": True},
            {"role": "system", "content": SEARCH_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.query == "safe"
        assert ctx.instruction_content == SEARCH_REPORTER_INSTRUCTION

    def test_input_not_mutated(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import build_background_search_content

        history = [
            {"role": "system", "content": build_background_search_content("q", "d"), "hidden": True},
        ]
        original = [dict(item) for item in history]
        engine.prepare_reporter_context(history)
        assert history == original

    def test_role_case_sensitive_system_ignored(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_REPORTER_INSTRUCTION, build_background_search_content

        history = [
            {"role": "System", "content": build_background_search_content("case", "test"), "hidden": True},
            {"role": "System", "content": SEARCH_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.raw_content == ""
        assert ctx.instruction_content == ""
        assert ctx.query == "Unknown Query"

    def test_role_whitespace_system_ignored(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_REPORTER_INSTRUCTION, build_background_search_content

        history = [
            {"role": " system ", "content": build_background_search_content("space", "test"), "hidden": True},
            {"role": " system ", "content": SEARCH_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.raw_content == ""
        assert ctx.instruction_content == ""
        assert ctx.query == "Unknown Query"

    def test_exact_role_system_accepted(self, engine: SearchWorkflowEngine) -> None:
        from core.search_contracts import SEARCH_REPORTER_INSTRUCTION, build_background_search_content

        history = [
            {"role": "system", "content": build_background_search_content("exact", "match"), "hidden": True},
            {"role": "system", "content": SEARCH_REPORTER_INSTRUCTION, "hidden": True},
        ]
        ctx = engine.prepare_reporter_context(history)
        assert ctx.raw_content == history[0]["content"]
        assert ctx.instruction_content == SEARCH_REPORTER_INSTRUCTION
        assert ctx.query == "exact"


# ── prepare_preview_context ──

class TestPreparePreviewContext:
    def test_normal_query_returns_all_fields(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_preview_context(user_msg="search for Python", query="Python")
        assert ctx.query == "Python"
        assert ctx.history == [{"role": "user", "content": "search for Python"}]
        assert "[SEARCH_FIRST_PASS_RULE]" in ctx.first_pass_rule
        assert 'checking the web for "Python"' in ctx.fallback_text
        assert ctx.recency_sensitive is False

    def test_recency_query_sets_recency_sensitive_true(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_preview_context(user_msg="latest news", query="latest news")
        assert ctx.recency_sensitive is True
        assert "recency-sensitive" in ctx.first_pass_rule

    def test_ordinary_query_sets_recency_sensitive_false(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_preview_context(user_msg="Python docs", query="Python docs")
        assert ctx.recency_sensitive is False
        assert "recency-sensitive" not in ctx.first_pass_rule

    def test_empty_user_msg_falls_back_to_query_in_history(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_preview_context(user_msg="", query="Django")
        assert ctx.history == [{"role": "user", "content": "Django"}]
        assert ctx.query == "Django"

    def test_empty_user_msg_and_empty_query_returns_empty_history_and_generic_fallback(
        self, engine: SearchWorkflowEngine
    ) -> None:
        ctx = engine.prepare_preview_context(user_msg="", query="")
        assert ctx.history == []
        assert ctx.query == ""
        assert ctx.fallback_text == "I'm checking the web for that now. I'll bring the results back automatically in a moment."

    def test_preview_context_is_frozen(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_preview_context(user_msg="hello", query="world")
        with pytest.raises(AttributeError):
            ctx.query = "changed"  # type: ignore[misc]

    def test_default_history_is_not_shared_between_instances(self, engine: SearchWorkflowEngine) -> None:
        ctx1 = engine.prepare_preview_context(user_msg="", query="")
        ctx2 = engine.prepare_preview_context(user_msg="", query="")
        assert ctx1.history is not ctx2.history
        ctx1.history.append({"role": "user", "content": "mutated"})
        assert ctx2.history == []

    def test_source_choice_rewrite_in_preview_context(self, engine: SearchWorkflowEngine) -> None:
        ctx = engine.prepare_preview_context(user_msg="yes online", query="Python 3.14")
        assert ctx.history == [{"role": "user", "content": "Search the web for Python 3.14."}]
