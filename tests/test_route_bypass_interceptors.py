"""Guard tests for route bypass interceptors.

Covers:
- Environment query interceptor (2-arg signature)
- Operational state interceptor (3-arg signature with orc)
- Registry compatibility (existing 2-arg interceptors still work)
- Ordering / priority with existing interceptors
- Import wiring through core.orchestrator
"""

from __future__ import annotations

import pytest


class _FakePromptContext:
    def __init__(self, readonly_answer: str = "") -> None:
        self._readonly_answer = readonly_answer

    def build_readonly_state_answer(self, user_msg: str) -> str:
        return self._readonly_answer


class _FakeOrc:
    """Minimal fake orchestrator for operational-state interceptor tests."""

    def __init__(self, readonly_answer: str = "") -> None:
        self._cached_readonly_state_answer = ""
        self.prompt_context = _FakePromptContext(readonly_answer)


@pytest.fixture
def detect_route_interceptor():
    from core import orchestrator  # noqa: F401 - ensure app-start import wiring
    from core.routing.route_normalizer import detect_route_interceptor as _detect

    return _detect


def _route_interceptor_names() -> list[str]:
    from core import orchestrator  # noqa: F401 - ensure app-start import wiring
    from core.routing.route_normalizer import _ROUTE_INTERCEPTOR_REGISTRY

    return [fn.__module__ + "." + fn.__name__ for fn in _ROUTE_INTERCEPTOR_REGISTRY]


class TestEnvironmentQueryInterceptor:
    """Environment query route interceptor tests."""

    def test_positive_what_is_the_date(self, detect_route_interceptor) -> None:
        result = detect_route_interceptor("What is the date?", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"
        assert result["next_stage"] == "PERSONA"
        assert result["stats_decision"] == "CHAT"
        assert result["bypass"] == "environment_query"
        assert result["route_decision"]["decision"] == "CHAT"
        assert result["route_decision"]["interceptor"] == "ENVIRONMENT_QUERY"

    def test_positive_what_time_is_it(self, detect_route_interceptor) -> None:
        result = detect_route_interceptor("What time is it?", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"

    def test_positive_what_day_is_it(self, detect_route_interceptor) -> None:
        result = detect_route_interceptor("What day is it?", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"

    def test_positive_todays_date(self, detect_route_interceptor) -> None:
        result = detect_route_interceptor("Today's date", [])
        assert result is not None
        assert result["kind"] == "ENVIRONMENT_QUERY"

    def test_false_positive_not_environment_query(self, detect_route_interceptor) -> None:
        result = detect_route_interceptor("What's your favorite color?", [])
        assert result is None

    def test_false_positive_date_of_meeting(self, detect_route_interceptor) -> None:
        """Current looks_like_live_environment_query behavior must stay unchanged."""
        result = detect_route_interceptor("What is the date of the meeting?", [])
        assert result is None


class TestOperationalStateInterceptor:
    """Operational state route interceptor tests."""

    def test_positive_with_answer(self, detect_route_interceptor) -> None:
        orc = _FakeOrc(readonly_answer="Pending tasks: buy milk.")
        result = detect_route_interceptor("What tasks do I have?", [], orc=orc)
        assert result is not None
        assert result["kind"] == "OPERATIONAL_STATE_QUERY"
        assert result["next_stage"] == "PERSONA"
        assert result["stats_decision"] == "CHAT"
        assert result["bypass"] == "operational_state_query"
        assert result["route_decision"]["decision"] == "CHAT"
        assert result["route_decision"]["interceptor"] == "OPERATIONAL_STATE_QUERY"
        assert orc._cached_readonly_state_answer == "Pending tasks: buy milk."

    def test_positive_empty_store(self, detect_route_interceptor) -> None:
        orc = _FakeOrc(readonly_answer="No pending tasks.")
        result = detect_route_interceptor("What tasks do I have?", [], orc=orc)
        assert result is not None
        assert result["kind"] == "OPERATIONAL_STATE_QUERY"

    def test_negative_no_answer(self, detect_route_interceptor) -> None:
        orc = _FakeOrc(readonly_answer="")
        result = detect_route_interceptor("Tell me a joke", [], orc=orc)
        assert result is None
        assert orc._cached_readonly_state_answer == ""

    def test_negative_mutation_add_task(self, detect_route_interceptor) -> None:
        orc = _FakeOrc(readonly_answer="")
        result = detect_route_interceptor("Add task buy milk", [], orc=orc)
        assert result is None

    def test_negative_mutation_delete_event(self, detect_route_interceptor) -> None:
        orc = _FakeOrc(readonly_answer="")
        result = detect_route_interceptor("Delete event dentist", [], orc=orc)
        assert result is None

    def test_negative_orc_is_none(self, detect_route_interceptor) -> None:
        result = detect_route_interceptor("What tasks do I have?", [], orc=None)
        assert result is None

    def test_caches_answer_on_orc(self, detect_route_interceptor) -> None:
        orc = _FakeOrc(readonly_answer="Upcoming events: dentist on Monday.")
        _ = detect_route_interceptor("Any events?", [], orc=orc)
        assert orc._cached_readonly_state_answer == "Upcoming events: dentist on Monday."

    def test_exception_in_build_answer_returns_none(self, detect_route_interceptor) -> None:
        class _BrokenPromptContext:
            def build_readonly_state_answer(self, user_msg: str) -> str:
                raise RuntimeError("boom")

        orc = _FakeOrc()
        orc.prompt_context = _BrokenPromptContext()
        result = detect_route_interceptor("What tasks?", [], orc=orc)
        assert result is None


class TestRegistryCompatibility:
    """Existing interceptors must continue working with the extended dispatcher."""

    def test_existing_interceptors_still_work(self, detect_route_interceptor) -> None:
        result = detect_route_interceptor("Please undo that")
        assert result is not None
        assert result["kind"] == "UNDO"

        result = detect_route_interceptor("Explain the last turn")
        assert result is not None
        assert result["kind"] == "EXPLAIN"

    def test_existing_interceptors_ignore_orc_parameter(self, detect_route_interceptor) -> None:
        """Passing orc= to detect_route_interceptor must not break 2-arg interceptors."""
        result = detect_route_interceptor("Please undo that", [], orc="dummy")
        assert result is not None
        assert result["kind"] == "UNDO"

    def test_environment_query_runs_after_existing_interceptors(self, detect_route_interceptor) -> None:
        """UNDO/EXPLAIN must win over ENVIRONMENT_QUERY because they are registered first."""
        result = detect_route_interceptor("Please undo that")
        assert result is not None
        assert result["kind"] == "UNDO"

    def test_reminder_set_wins_over_environment_query(self, detect_route_interceptor) -> None:
        """Proactive monitor's REMINDER_SET interceptor wins over env query for reminder text."""
        result = detect_route_interceptor("Set a reminder for tomorrow at 3")
        assert result is not None
        assert result["kind"] == "REMINDER_SET"

    def test_explain_wins_over_operational_state(self, detect_route_interceptor) -> None:
        """EXPLAIN interceptor wins over operational state for explain requests."""
        result = detect_route_interceptor("Explain that")
        assert result is not None
        assert result["kind"] == "EXPLAIN"


class TestImportWiring:
    """New engine modules must be imported for side-effect registration."""

    def test_environment_query_registered_once(self) -> None:
        names = _route_interceptor_names()
        assert names.count("core.engines.environment_query._registered_environment_query_interceptor") == 1

    def test_operational_state_registered_once(self) -> None:
        names = _route_interceptor_names()
        assert names.count("core.engines.operational_state_answer._registered_operational_state_interceptor") == 1

    def test_both_interceptors_present_in_registry(self) -> None:
        names = {name.rsplit(".", 1)[-1] for name in _route_interceptor_names()}
        assert "_registered_environment_query_interceptor" in names
        assert "_registered_operational_state_interceptor" in names
