"""tests/test_memory_insertion_engine.py

Tests for the memory insertion engine wrapper.

Verifies that:
- hooks are registered in the engine layer, not in memory.world_model
- on_turn_end behavior matches the original implementation
- edge cases (synthetic turns, knowledge disabled, short history) are handled
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.feature_hooks import _HOOKS, fire_hooks

# Importing the engine module triggers side-effect registrations.
from core.engines import memory_insertion  # noqa: F401


def _memory_hooks() -> list[Any]:
    return list(_HOOKS.get("on_turn_end", []))


def _find_hook(name: str):
    for hook in _memory_hooks():
        if hook.__name__ == name:
            return hook
    return None


class TestRegistryPlacement:
    def test_memory_hooks_registered_exactly_once(self):
        hooks = _memory_hooks()
        consolidate = [h for h in hooks if h.__name__ == "_hook_consolidate_recent_memory"]
        refresh = [h for h in hooks if h.__name__ == "_hook_refresh_profile_knowledge"]
        assert len(consolidate) == 1
        assert len(refresh) == 1

    def test_memory_hook_modules_point_to_engine(self):
        consolidate = _find_hook("_hook_consolidate_recent_memory")
        refresh = _find_hook("_hook_refresh_profile_knowledge")
        assert consolidate is not None
        assert refresh is not None
        assert consolidate.__module__ == "core.engines.memory_insertion"
        assert refresh.__module__ == "core.engines.memory_insertion"

    def test_memory_world_model_does_not_register_hooks(self):
        # Re-importing world_model should not add new on_turn_end hooks.
        # We snapshot the count before and after.
        before = len(_memory_hooks())
        import memory.world_model  # noqa: F401
        after = len(_memory_hooks())
        assert after == before


class TestHookBehavior:
    def _make_orc(
        self,
        *,
        knowledge_enabled: bool = True,
        synthetic_user_turn: bool = False,
        recent_messages_return: list[dict[str, str]] | None = None,
    ):
        orc = MagicMock()
        orc.knowledge_enabled = knowledge_enabled
        orc.synthetic_user_turn = synthetic_user_turn
        orc.chat.recent_messages.return_value = recent_messages_return or []
        return orc

    def test_consolidate_called_when_three_messages_and_knowledge_enabled(self):
        orc = self._make_orc(
            recent_messages_return=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "what is up"},
            ]
        )
        hook = _find_hook("_hook_consolidate_recent_memory")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.consolidate_memory_async.assert_called_once()

    def test_consolidate_skipped_when_fewer_than_three_messages(self):
        orc = self._make_orc(
            recent_messages_return=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        )
        hook = _find_hook("_hook_consolidate_recent_memory")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.consolidate_memory_async.assert_not_called()

    def test_consolidate_skipped_when_knowledge_disabled(self):
        orc = self._make_orc(
            knowledge_enabled=False,
            recent_messages_return=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ]
        )
        hook = _find_hook("_hook_consolidate_recent_memory")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.consolidate_memory_async.assert_not_called()

    def test_consolidate_skipped_on_synthetic_turn(self):
        orc = self._make_orc(
            synthetic_user_turn=True,
            recent_messages_return=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ]
        )
        hook = _find_hook("_hook_consolidate_recent_memory")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.consolidate_memory_async.assert_not_called()

    def test_refresh_called_when_four_messages_and_knowledge_enabled(self):
        orc = self._make_orc(
            recent_messages_return=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
                {"role": "assistant", "content": "d"},
            ]
        )
        hook = _find_hook("_hook_refresh_profile_knowledge")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.update_knowledge_async.assert_called_once()

    def test_refresh_skipped_when_fewer_than_four_messages(self):
        orc = self._make_orc(
            recent_messages_return=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ]
        )
        hook = _find_hook("_hook_refresh_profile_knowledge")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.update_knowledge_async.assert_not_called()

    def test_refresh_skipped_when_knowledge_disabled(self):
        orc = self._make_orc(
            knowledge_enabled=False,
            recent_messages_return=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
                {"role": "assistant", "content": "d"},
            ]
        )
        hook = _find_hook("_hook_refresh_profile_knowledge")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.update_knowledge_async.assert_not_called()

    def test_refresh_skipped_on_synthetic_turn(self):
        orc = self._make_orc(
            synthetic_user_turn=True,
            recent_messages_return=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
                {"role": "assistant", "content": "d"},
            ]
        )
        hook = _find_hook("_hook_refresh_profile_knowledge")
        assert hook is not None
        hook(orc, reporter_just_ran=False)
        orc.knowledge.update_knowledge_async.assert_not_called()

    def test_fire_hooks_invokes_memory_hooks_via_registry(self):
        orc = self._make_orc(
            recent_messages_return=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
                {"role": "assistant", "content": "d"},
                {"role": "user", "content": "e"},
            ]
        )
        fire_hooks("on_turn_end", orc, reporter_just_ran=False)
        orc.knowledge.consolidate_memory_async.assert_called_once()
        orc.knowledge.update_knowledge_async.assert_called_once()
