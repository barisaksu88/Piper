"""tests.test_app_shutdown

Verify the module-level shutdown dispatcher behaviour.
"""

from __future__ import annotations

import atexit

import pytest

from app import _ShutdownDispatcher


class TestShutdownDispatcher:
    def test_registers_only_one_atexit_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher = _ShutdownDispatcher()
        registered: list[callable] = []
        monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))

        dispatcher.add(lambda: None)
        dispatcher.add(lambda: None)
        assert len(registered) == 1

    def test_runs_all_added_functions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher = _ShutdownDispatcher()
        monkeypatch.setattr(atexit, "register", lambda fn: None)

        calls: list[int] = []
        dispatcher.add(lambda: calls.append(1))
        dispatcher.add(lambda: calls.append(2))
        dispatcher._run()
        assert calls == [1, 2]

    def test_run_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher = _ShutdownDispatcher()
        monkeypatch.setattr(atexit, "register", lambda fn: None)

        calls: list[int] = []
        dispatcher.add(lambda: calls.append(1))
        dispatcher._run()
        dispatcher._run()
        assert calls == [1]

    def test_exceptions_do_not_stop_other_shutdowns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher = _ShutdownDispatcher()
        monkeypatch.setattr(atexit, "register", lambda fn: None)

        calls: list[int] = []
        dispatcher.add(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        dispatcher.add(lambda: calls.append(1))
        dispatcher._run()
        assert calls == [1]
