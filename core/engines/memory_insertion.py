"""core/engines/memory_insertion.py

Thin engine wrapper for memory insertion hooks.

This module registers on_turn_end hooks that trigger asynchronous memory
consolidation and profile knowledge refresh.  The actual memory mutation
remains owned by WorldModelManager and the knowledge stores; this module
only provides the engine/registry boundary for hook registration.
"""
from __future__ import annotations

from core.feature_hooks import register_hook


@register_hook("on_turn_end")
def _hook_consolidate_recent_memory(orc, *, reporter_just_ran: bool = False) -> None:
    del reporter_just_ran
    if bool(getattr(orc, "synthetic_user_turn", False)):
        return
    recent_messages = orc.chat.recent_messages(3)
    if orc.knowledge_enabled and len(recent_messages) >= 3:
        orc.knowledge.consolidate_memory_async(recent_messages)


@register_hook("on_turn_end")
def _hook_refresh_profile_knowledge(orc, *, reporter_just_ran: bool = False) -> None:
    del reporter_just_ran
    if bool(getattr(orc, "synthetic_user_turn", False)):
        return
    profile_messages = orc.chat.recent_messages(8)
    if orc.knowledge_enabled and len(profile_messages) >= 4:
        orc.knowledge.update_knowledge_async(profile_messages)
