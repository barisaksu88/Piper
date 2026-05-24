"""Verify engine hook modules register their decorators at app-start import time.

These tests ensure that registry-driven engine modules are actually imported
for side-effect registration.  A module with @register_hook or
@register_route_interceptor that is never imported is dead code.
"""

from __future__ import annotations


class TestEngineHookRegistration:
    """Engine hook registration guard tests."""

    def test_import_orchestrator_registers_hooks(self) -> None:
        """Importing core.orchestrator must trigger all engine registrations."""
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401 — side-effect import

        hooks = list_hooks()
        assert "on_pre_route" in hooks
        assert "on_turn_end" in hooks
        assert "on_task_verified" in hooks

    def test_stats_collector_pre_route_hook_registered(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        pre_route = hooks.get("on_pre_route", [])
        assert "core.engines.stats_collector._hook_note_pre_route_user_msg" in pre_route

    def test_stats_collector_hook_not_duplicated(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        pre_route = hooks.get("on_pre_route", [])
        assert pre_route.count("core.engines.stats_collector._hook_note_pre_route_user_msg") == 1

    def test_conversation_compressor_turn_end_hook_registered(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        turn_end = hooks.get("on_turn_end", [])
        assert "core.engines.conversation_compressor._hook_deferred_conversation_summary" in turn_end

    def test_conversation_compressor_hook_not_duplicated(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        turn_end = hooks.get("on_turn_end", [])
        assert turn_end.count("core.engines.conversation_compressor._hook_deferred_conversation_summary") == 1

    def test_proactive_monitor_turn_end_hook_registered(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        turn_end = hooks.get("on_turn_end", [])
        assert "core.engines.proactive_monitor._hook_finalize_proactive_trigger" in turn_end

    def test_proactive_monitor_hook_not_duplicated(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        turn_end = hooks.get("on_turn_end", [])
        assert turn_end.count("core.engines.proactive_monitor._hook_finalize_proactive_trigger") == 1

    def test_change_journal_task_verified_hook_registered(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        task_verified = hooks.get("on_task_verified", [])
        assert "core.engines.change_journal._hook_record_change_journal" in task_verified

    def test_change_journal_hook_not_duplicated(self) -> None:
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        task_verified = hooks.get("on_task_verified", [])
        assert task_verified.count("core.engines.change_journal._hook_record_change_journal") == 1

    def test_no_duplicate_hook_registration_anywhere(self) -> None:
        """Every hook must appear exactly once in its respective list."""
        from core.feature_hooks import list_hooks
        from core import orchestrator  # noqa: F401

        hooks = list_hooks()
        for hook_type, name_list in hooks.items():
            for name in set(name_list):
                assert name_list.count(name) == 1, (
                    f"Hook {name!r} is registered {name_list.count(name)} times in {hook_type!r}"
                )
