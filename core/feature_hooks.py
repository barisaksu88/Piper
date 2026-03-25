from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


HookFn = Callable[..., None]
_HOOKS: dict[str, list[HookFn]] = defaultdict(list)


def register_hook(hook_type: str) -> Callable[[HookFn], HookFn]:
    def _decorator(fn: HookFn) -> HookFn:
        _HOOKS[str(hook_type or "").strip()].append(fn)
        return fn

    return _decorator


def fire_hooks(hook_type: str, orc, **kwargs: Any) -> None:
    for hook in list(_HOOKS.get(str(hook_type or "").strip(), [])):
        hook(orc, **kwargs)
