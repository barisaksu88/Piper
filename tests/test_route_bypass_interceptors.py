"""Guard tests for registry-driven route bypass engines.

Environment query and operational state answer interceptors moved from
procedural blocks in orchestrator_phases.py into self-registering engine
modules.  These tests verify the interceptors behave correctly and preserve
registry ordering.
"""

from __future__ import annotations

import pytest


class _FakeOrc:
    """Minimal fake orchestrator for operational-state interceptor tests."""

    def __init__(self, readonly_answer: str = "") -> None:
        self._cached_readonly_state_answer = ""
        self.prompt_context = _FakePromptContext(readonly_answer)


class _FakePromptContext:
    def __init__(self, readonly_answer: str = "") -> None:
        self._readonly_answer = readonly_answer

    def build_readonly_state_answer(self, user_msg: str) -> str:
        return self._readonly_answer


class TestEnvironmentQueryInterceptor:
    """Tests for core.engines.environment_query._registered_environment_query_interceptor."""

    def test_positive_what_time(self) -> None:
        from core.engines.environment_query import _registered_environment_query_interceptor

        result = _registered_environment_query_interceptor("What time is it?", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"
        assert result["next_stage"] == "PERSONA"
        assert result["bypass"] == "environment_query"

    def test_positive_what_date(self) -> None:
        from core.engines.environment_query import _registered_environment_query_interceptor

        result = _registered_environment_query_interceptor("What's today's date?", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"

    def test_positive_what_day(self) -> None:
        from core.engines.environment_query import _registered_environment_query_interceptor

        result = _registered_environment_query_interceptor("What day is it?", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"

    def test_positive_todays_date(self) -> None:
        from core.engines.environment_query import _registered_environment_query_interceptor

        result = _registered_environment_query_interceptor("Today's date", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"

    def test_negative_date_of_meeting(self) -> None:
        from core.engines.environment_query import _registered_environment_query_interceptor

        result = _registered_environment_query_interceptor("What's the deadline for the meeting?", [])
        assert result is None

    def test_negative_time_of_flight(self) -> None:
        from core.engines.environment_query import _registered_environment_query_interceptor

        result = _registered_environment_query_interceptor("What time is the flight?", [])
        assert result is None

    def test_negative_empty(self) -> None:
        from core.engines.environment_query import _registered_environment_query_interceptor

        result = _registered_environment_query_interceptor("", [])
        assert result is None


class TestOperationalStateInterceptor:
    """Tests for core.engines.operational_state_answer._registered_operational_state_interceptor."""

    def test_positive_with_answer(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        orc = _FakeOrc(readonly_answer="Pending tasks: buy milk.")
        result = _registered_operational_state_interceptor("What tasks do I have?", [], orc)
        assert result is not None
        assert result["kind"] == "OPERATIONAL_STATE_QUERY"
        assert result["next_stage"] == "PERSONA"
        assert result["bypass"] == "operational_state_query"
        assert orc._cached_readonly_state_answer == "Pending tasks: buy milk."

    def test_positive_empty_store(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        orc = _FakeOrc(readonly_answer="No pending tasks.")
        result = _registered_operational_state_interceptor("What tasks do I have?", [], orc)
        assert result is not None
        assert result["kind"] == "OPERATIONAL_STATE_QUERY"

    def test_negative_no_answer(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        orc = _FakeOrc(readonly_answer="")
        result = _registered_operational_state_interceptor("Tell me a joke", [], orc)
        assert result is None
        assert orc._cached_readonly_state_answer == ""

    def test_negative_mutation_add_task(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        orc = _FakeOrc(readonly_answer="")
        result = _registered_operational_state_interceptor("Add task buy milk", [], orc)
        assert result is None

    def test_negative_mutation_delete_event(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        orc = _FakeOrc(readonly_answer="")
        result = _registered_operational_state_interceptor("Delete event dentist", [], orc)
        assert result is None

    def test_negative_orc_is_none(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        result = _registered_operational_state_interceptor("What tasks do I have?", [], None)
        assert result is None

    def test_caches_answer_on_orc(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        orc = _FakeOrc(readonly_answer="Upcoming events: dentist on Monday.")
        _registered_operational_state_interceptor("Any events?", [], orc)
        assert orc._cached_readonly_state_answer == "Upcoming events: dentist on Monday."

    def test_exception_in_build_answer_returns_none(self) -> None:
        from core.engines.operational_state_answer import _registered_operational_state_interceptor

        class _BrokenPromptContext:
            def build_readonly_state_answer(self, user_msg: str) -> str:
                raise RuntimeError("boom")

        orc = _FakeOrc()
        orc.prompt_context = _BrokenPromptContext()
        result = _registered_operational_state_interceptor("What tasks?", [], orc)
        assert result is None


class TestRegistryCompatibility:
    """Existing interceptors must continue working with the extended dispatcher."""

    def test_existing_interceptors_still_work(self) -> None:
        from core.routing.route_normalizer import detect_route_interceptor
        from core import orchestrator  # noqa: F401 — ensures all interceptors registered

        # UNDO interceptor
        result = detect_route_interceptor("Please undo that")
        assert result is not None
        assert result["kind"] == "UNDO"

        # EXPLAIN interceptor
        result = detect_route_interceptor("Explain the last turn")
        assert result is not None
        assert result["kind"] == "EXPLAIN"

    def test_existing_interceptors_ignore_orc_parameter(self) -> None:
        """Passing orc= to detect_route_interceptor must not break 2-arg interceptors."""
        from core.routing.route_normalizer import detect_route_interceptor
        from core import orchestrator  # noqa: F401

        result = detect_route_interceptor("Please undo that", [], orc="dummy")
        assert result is not None
        assert result["kind"] == "UNDO"

    def test_environment_query_runs_after_existing_interceptors(self) -> None:
        """UNDO/EXPLAIN must win over ENVIRONMENT_QUERY because they are registered first."""
        from core.routing.route_normalizer import detect_route_interceptor
        from core import orchestrator  # noqa: F401

        result = detect_route_interceptor("Please undo that")
        assert result is not None
        assert result["kind"] == "UNDO"

    def test_reminder_set_wins_over_environment_query(self) -> None:
        """Proactive monitor's REMINDER_SET interceptor wins over env query for reminder text."""
        from core.routing.route_normalizer import detect_route_interceptor
        from core import orchestrator  # noqa: F401

        result = detect_route_interceptor("Set a reminder for tomorrow at 3")
        assert result is not None
        assert result["kind"] == "REMINDER_SET"

    def test_explain_wins_over_operational_state(self) -> None:
        """EXPLAIN interceptor wins over operational state for explain requests."""
        from core.routing.route_normalizer import detect_route_interceptor
        from core import orchestrator  # noqa: F401

        result = detect_route_interceptor("Explain that")
        assert result is not None
        assert result["kind"] == "EXPLAIN"


class TestImportWiring:
    """New engine modules must be imported for side-effect registration."""

    def test_environment_query_registered_once(self) -> None:
        from core.routing.route_normalizer import _ROUTE_INTERCEPTOR_REGISTRY
        from core import orchestrator  # noqa: F401 — side-effect import

        names = [
            fn.__module__ + "." + fn.__name__
            for fn in _ROUTE_INTERCEPTOR_REGISTRY
        ]
        assert names.count("core.engines.environment_query._registered_environment_query_interceptor") == 1

    def test_operational_state_registered_once(self) -> None:
        from core.routing.route_normalizer import _ROUTE_INTERCEPTOR_REGISTRY
        from core import orchestrator  # noqa: F401 — side-effect import

        names = [
            fn.__module__ + "." + fn.__name__
            for fn in _ROUTE_INTERCEPTOR_REGISTRY
        ]
        assert names.count("core.engines.operational_state_answer._registered_operational_state_interceptor") == 1

    def test_both_interceptors_present_in_registry(self) -> None:
        from core.routing.route_normalizer import _ROUTE_INTERCEPTOR_REGISTRY
        from core import orchestrator  # noqa: F401 — side-effect import

        names = {fn.__name__ for fn in _ROUTE_INTERCEPTOR_REGISTRY}
        assert "_registered_environment_query_interceptor" in names
        assert "_registered_operational_state_interceptor" in names


class TestLiveEnvironmentQueryImport:
    """Regression: _is_live_environment_chat_query must not crash with NameError."""

    def test_live_environment_date_query_returns_true(self) -> None:
        from core.orchestrator_phases import _is_live_environment_chat_query

        assert _is_live_environment_chat_query("what is today's date") is True

    def test_live_environment_time_query_returns_true(self) -> None:
        from core.orchestrator_phases import _is_live_environment_chat_query

        assert _is_live_environment_chat_query("what time is it") is True

    def test_normal_search_query_returns_false(self) -> None:
        from core.orchestrator_phases import _is_live_environment_chat_query

        assert _is_live_environment_chat_query("who invented the telephone") is False

    def test_empty_query_returns_false(self) -> None:
        from core.orchestrator_phases import _is_live_environment_chat_query

        assert _is_live_environment_chat_query("") is False
