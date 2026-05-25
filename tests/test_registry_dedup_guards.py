"""tests/test_registry_dedup_guards.py

Tests for idempotent registry decorators.

Verifies that registering the same function twice (by module + qualname)
does not create duplicate entries, while still allowing distinct functions
and cross-type registrations.
"""
from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Feature hooks
# ---------------------------------------------------------------------------

class TestFeatureHookDedup:
    def test_same_hook_twice_same_type_stores_once(self, monkeypatch):
        from core.feature_hooks import _HOOKS, register_hook

        @register_hook("_test_dedup")
        def _hook_a() -> None:
            pass

        before = len(_HOOKS["_test_dedup"])

        # Register the same function object again
        register_hook("_test_dedup")(_hook_a)

        after = len(_HOOKS["_test_dedup"])
        assert after == before

        # Cleanup
        _HOOKS["_test_dedup"] = [h for h in _HOOKS["_test_dedup"] if h is not _hook_a]

    def test_same_hook_different_types_stores_both(self, monkeypatch):
        from core.feature_hooks import _HOOKS, register_hook

        @register_hook("_test_dedup_a")
        @register_hook("_test_dedup_b")
        def _hook_b() -> None:
            pass

        assert any(h is _hook_b for h in _HOOKS["_test_dedup_a"])
        assert any(h is _hook_b for h in _HOOKS["_test_dedup_b"])

        # Cleanup
        _HOOKS["_test_dedup_a"] = [h for h in _HOOKS["_test_dedup_a"] if h is not _hook_b]
        _HOOKS["_test_dedup_b"] = [h for h in _HOOKS["_test_dedup_b"] if h is not _hook_b]

    def test_different_hooks_same_type_both_stored(self, monkeypatch):
        from core.feature_hooks import _HOOKS, register_hook

        @register_hook("_test_dedup")
        def _hook_c() -> None:
            pass

        @register_hook("_test_dedup")
        def _hook_d() -> None:
            pass

        assert any(h is _hook_c for h in _HOOKS["_test_dedup"])
        assert any(h is _hook_d for h in _HOOKS["_test_dedup"])

        # Cleanup
        _HOOKS["_test_dedup"] = [h for h in _HOOKS["_test_dedup"] if h not in (_hook_c, _hook_d)]

    def test_dedup_uses_module_qualname_not_identity(self, monkeypatch):
        from core.feature_hooks import _HOOKS, register_hook

        def _hook_e() -> None:
            pass

        def _hook_f() -> None:
            pass

        # Make _hook_f look like _hook_e by module + qualname
        _hook_f.__module__ = _hook_e.__module__
        _hook_f.__qualname__ = _hook_e.__qualname__

        register_hook("_test_dedup")(_hook_e)
        before = len(_HOOKS["_test_dedup"])
        register_hook("_test_dedup")(_hook_f)
        after = len(_HOOKS["_test_dedup"])

        assert after == before

        # Cleanup
        _HOOKS["_test_dedup"] = [h for h in _HOOKS["_test_dedup"] if h not in (_hook_e, _hook_f)]

    def test_first_registration_order_preserved(self, monkeypatch):
        from core.feature_hooks import _HOOKS, register_hook

        @register_hook("_test_dedup")
        def _hook_g() -> None:
            pass

        first_index = next(
            i for i, h in enumerate(_HOOKS["_test_dedup"]) if h is _hook_g
        )

        # Re-register; should not move or duplicate
        register_hook("_test_dedup")(_hook_g)
        second_index = next(
            i for i, h in enumerate(_HOOKS["_test_dedup"]) if h is _hook_g
        )

        assert first_index == second_index
        assert sum(1 for h in _HOOKS["_test_dedup"] if h is _hook_g) == 1

        # Cleanup
        _HOOKS["_test_dedup"] = [h for h in _HOOKS["_test_dedup"] if h is not _hook_g]


# ---------------------------------------------------------------------------
# Route interceptors
# ---------------------------------------------------------------------------

class TestRouteInterceptorDedup:
    def test_same_interceptor_twice_stores_once(self, monkeypatch):
        from core.routing.route_normalizer import (
            _ROUTE_INTERCEPTOR_REGISTRY,
            register_route_interceptor,
        )

        @register_route_interceptor
        def _interceptor_a(_text, _history):
            return None

        before = len(_ROUTE_INTERCEPTOR_REGISTRY)
        register_route_interceptor(_interceptor_a)
        after = len(_ROUTE_INTERCEPTOR_REGISTRY)

        assert after == before

        # Cleanup
        if _interceptor_a in _ROUTE_INTERCEPTOR_REGISTRY:
            _ROUTE_INTERCEPTOR_REGISTRY.remove(_interceptor_a)

    def test_different_interceptors_preserve_order(self, monkeypatch):
        from core.routing.route_normalizer import (
            _ROUTE_INTERCEPTOR_REGISTRY,
            register_route_interceptor,
        )

        @register_route_interceptor
        def _interceptor_b(_text, _history):
            return None

        @register_route_interceptor
        def _interceptor_c(_text, _history):
            return None

        keys = [
            f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', '')}"
            for fn in _ROUTE_INTERCEPTOR_REGISTRY
        ]
        b_key = f"{_interceptor_b.__module__}.{_interceptor_b.__qualname__}"
        c_key = f"{_interceptor_c.__module__}.{_interceptor_c.__qualname__}"
        assert keys.index(b_key) < keys.index(c_key)

        # Cleanup
        for fn in (_interceptor_b, _interceptor_c):
            if fn in _ROUTE_INTERCEPTOR_REGISTRY:
                _ROUTE_INTERCEPTOR_REGISTRY.remove(fn)

    def test_dedup_uses_module_qualname_not_identity(self, monkeypatch):
        from core.routing.route_normalizer import (
            _ROUTE_INTERCEPTOR_REGISTRY,
            register_route_interceptor,
        )

        def _interceptor_d(_text, _history):
            return None

        def _interceptor_e(_text, _history):
            return None

        _interceptor_e.__module__ = _interceptor_d.__module__
        _interceptor_e.__qualname__ = _interceptor_d.__qualname__

        register_route_interceptor(_interceptor_d)
        before = len(_ROUTE_INTERCEPTOR_REGISTRY)
        register_route_interceptor(_interceptor_e)
        after = len(_ROUTE_INTERCEPTOR_REGISTRY)

        assert after == before

        # Cleanup
        for fn in (_interceptor_d, _interceptor_e):
            if fn in _ROUTE_INTERCEPTOR_REGISTRY:
                _ROUTE_INTERCEPTOR_REGISTRY.remove(fn)


# ---------------------------------------------------------------------------
# Tail blocks
# ---------------------------------------------------------------------------

class TestTailBlockDedup:
    def test_same_tail_block_twice_stores_once(self, monkeypatch):
        from core.engines.tail_block_registry import (
            _TAIL_BLOCK_REGISTRY,
            register_tail_block,
        )

        @register_tail_block
        def _block_a(_ctx):
            return ""

        before = len(_TAIL_BLOCK_REGISTRY)
        register_tail_block(_block_a)
        after = len(_TAIL_BLOCK_REGISTRY)

        assert after == before

        # Cleanup
        if _block_a in _TAIL_BLOCK_REGISTRY:
            _TAIL_BLOCK_REGISTRY.remove(_block_a)

    def test_different_tail_blocks_preserve_order(self, monkeypatch):
        from core.engines.tail_block_registry import (
            _TAIL_BLOCK_REGISTRY,
            register_tail_block,
        )

        @register_tail_block
        def _block_b(_ctx):
            return ""

        @register_tail_block
        def _block_c(_ctx):
            return ""

        keys = [
            f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', '')}"
            for fn in _TAIL_BLOCK_REGISTRY
        ]
        b_key = f"{_block_b.__module__}.{_block_b.__qualname__}"
        c_key = f"{_block_c.__module__}.{_block_c.__qualname__}"
        assert keys.index(b_key) < keys.index(c_key)

        # Cleanup
        for fn in (_block_b, _block_c):
            if fn in _TAIL_BLOCK_REGISTRY:
                _TAIL_BLOCK_REGISTRY.remove(fn)

    def test_dedup_uses_module_qualname_not_identity(self, monkeypatch):
        from core.engines.tail_block_registry import (
            _TAIL_BLOCK_REGISTRY,
            register_tail_block,
        )

        def _block_d(_ctx):
            return ""

        def _block_e(_ctx):
            return ""

        _block_e.__module__ = _block_d.__module__
        _block_e.__qualname__ = _block_d.__qualname__

        register_tail_block(_block_d)
        before = len(_TAIL_BLOCK_REGISTRY)
        register_tail_block(_block_e)
        after = len(_TAIL_BLOCK_REGISTRY)

        assert after == before

        # Cleanup
        for fn in (_block_d, _block_e):
            if fn in _TAIL_BLOCK_REGISTRY:
                _TAIL_BLOCK_REGISTRY.remove(fn)
