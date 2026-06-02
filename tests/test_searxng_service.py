"""Tests for SearXNGService Docker timeout resilience.

These tests do not require Docker to be installed or running.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


class FakeCompletedProcess:
    """Lightweight stand-in for subprocess.CompletedProcess."""

    def __init__(self, args, returncode, stdout="", stderr=""):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestRunDockerTimeoutHandling:
    def test_timeout_returns_nonzero_completed_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_docker must catch TimeoutExpired and return a CompletedProcess
        with a nonzero returncode instead of letting the exception escape."""
        from core.search.searxng_service import SearXNGService

        def _raising_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=5.0)

        monkeypatch.setattr(subprocess, "run", _raising_run)

        proc = SearXNGService._run_docker(["info"], timeout=5.0)
        assert proc.returncode == 124
        assert proc.stdout == ""
        assert "timed out after 5.0s" in proc.stderr
        assert "docker info" in proc.stderr

    def test_timeout_preserves_partial_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If TimeoutExpired carries partial stdout/stderr, it should be preserved."""
        from core.search.searxng_service import SearXNGService

        exc = subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=5.0)
        exc.stdout = b"partial stdout"
        exc.stderr = b"partial stderr"

        def _raising_run(*a, **k):
            raise exc

        monkeypatch.setattr(subprocess, "run", _raising_run)

        proc = SearXNGService._run_docker(["info"], timeout=5.0)
        assert proc.returncode == 124
        assert "partial stdout" in proc.stdout
        assert "partial stderr" in proc.stderr

    def test_os_error_returns_nonzero_completed_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_docker must catch OSError (e.g. FileNotFoundError) and return a
        CompletedProcess with a nonzero returncode instead of raising."""
        from core.search.searxng_service import SearXNGService

        def _raising_run(*a, **k):
            raise FileNotFoundError(2, "No such file or directory", "docker")

        monkeypatch.setattr(subprocess, "run", _raising_run)

        proc = SearXNGService._run_docker(["info"], timeout=5.0)
        assert proc.returncode == 127
        assert proc.stdout == ""
        assert "docker command failed" in proc.stderr


class TestDockerAvailableTimeoutHandling:
    def test_docker_available_returns_false_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """docker_available() must return False when _run_docker times out,
        not let the exception escape."""
        from core.search.searxng_service import SearXNGService

        def _timeout_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=5.0)

        monkeypatch.setattr(subprocess, "run", _timeout_run)
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker")

        svc = SearXNGService()
        assert svc.docker_available() is False


class TestEnsureAvailableTimeoutHandling:
    def test_ensure_available_does_not_raise_when_docker_times_out_and_not_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If Docker times out and SEARXNG_REQUIRE is False, ensure_available
        must return a result (ok=True with a warning message), not raise."""
        from core.search.searxng_service import SearXNGService

        cfg = SimpleNamespace(
            SEARCH_BACKEND="searxng",
            SEARXNG_AUTO_START=True,
            SEARXNG_REQUIRE=False,
            SEARXNG_URL="http://127.0.0.1:8888",
            SEARXNG_TIMEOUT_S=10.0,
            DOCKER_DESKTOP_START_TIMEOUT_S=0.0,
        )

        def _timeout_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=5.0)

        monkeypatch.setattr(subprocess, "run", _timeout_run)
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(SearXNGService, "health_check", lambda self: False)

        svc = SearXNGService(cfg=cfg)
        result = svc.ensure_available()
        assert result.ok is True
        assert "Docker" in result.message or "SearXNG" in result.message

    def test_ensure_available_returns_ok_false_when_required_and_docker_times_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If Docker times out and SEARXNG_REQUIRE is True, ensure_available
        must return ok=False (not raise an exception)."""
        from core.search.searxng_service import SearXNGService

        cfg = SimpleNamespace(
            SEARCH_BACKEND="searxng",
            SEARXNG_AUTO_START=True,
            SEARXNG_REQUIRE=True,
            SEARXNG_URL="http://127.0.0.1:8888",
            SEARXNG_TIMEOUT_S=10.0,
            DOCKER_DESKTOP_START_TIMEOUT_S=0.0,
        )

        def _timeout_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=5.0)

        monkeypatch.setattr(subprocess, "run", _timeout_run)
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker")
        monkeypatch.setattr(SearXNGService, "health_check", lambda self: False)

        svc = SearXNGService(cfg=cfg)
        result = svc.ensure_available()
        assert result.ok is False
        assert "Docker" in result.message or "SearXNG" in result.message

    def test_ensure_available_skips_when_backend_not_searxng(self) -> None:
        """If SEARCH_BACKEND is not searxng, ensure_available returns immediately."""
        from core.search.searxng_service import SearXNGService

        cfg = SimpleNamespace(SEARCH_BACKEND="duckduckgo")
        svc = SearXNGService(cfg=cfg)
        result = svc.ensure_available()
        assert result.ok is True
        assert "backend not searxng" in result.message


class TestShutdownTimeoutHandling:
    def test_shutdown_does_not_raise_on_docker_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """shutdown() must handle a timeout during docker stop gracefully."""
        from core.search.searxng_service import SearXNGService

        cfg = SimpleNamespace(
            SEARCH_BACKEND="searxng",
            SEARXNG_STOP_ON_EXIT=True,
            SEARXNG_DOCKER_CONTAINER="piper-searxng",
        )

        def _timeout_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["docker", "stop", "piper-searxng"], timeout=15.0)

        monkeypatch.setattr(subprocess, "run", _timeout_run)

        svc = SearXNGService(cfg=cfg)
        # Simulate that Piper owns the container
        svc._owned_by_piper = True
        result = svc.shutdown()
        assert result.ok is False
        assert "timed out" in result.message.lower() or "docker" in result.message.lower()
