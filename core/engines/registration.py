"""core/engines/registration.py

Centralized loader for built-in engine registrations.

Importing this module and calling `register_builtin_engines()` triggers all
side-effect registrations (route interceptors, feature hooks, tail blocks)
without requiring `core.orchestrator` to own a long list of direct imports.
"""
from __future__ import annotations

_REGISTERED = False


def register_builtin_engines() -> None:
    """Import all built-in engine modules for decorator side-effect registration.

    Calling this function multiple times is safe; subsequent calls are no-ops.
    """
    global _REGISTERED
    if _REGISTERED:
        return

    from core.engines import proactive_monitor as _proactive_monitor_registration  # noqa: F401
    from core.engines import change_journal as _change_journal_registration  # noqa: F401
    from core.engines import conversation_compressor as _conversation_compressor_registration  # noqa: F401
    from core.engines import stats_collector as _stats_collector_registration  # noqa: F401
    from core.engines import environment_query as _environment_query_registration  # noqa: F401
    from core.engines import operational_state_answer as _operational_state_answer_registration  # noqa: F401
    from core.engines import memory_insertion as _memory_insertion_registration  # noqa: F401
    from core import prompt_context as _prompt_context_registration  # noqa: F401

    _REGISTERED = True
