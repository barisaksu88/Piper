#!/usr/bin/env python3
"""Deterministic smoke tests for SearXNGService lifecycle management.

Mocks Docker and HTTP — does NOT require real Docker.

Usage:
    python scripts/searxng_service_lifecycle_smoke_test.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Ensure repo root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.search.searxng_service import SearXNGService, SearXNGServiceResult


def _ok(name: str) -> dict:
    return {"name": name, "status": "PASS"}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "status": "FAIL", "reason": reason}


def _make_cfg(**kwargs):
    defaults = {
        "SEARCH_BACKEND": "searxng",
        "SEARXNG_URL": "http://127.0.0.1:8888",
        "SEARXNG_TIMEOUT_S": 10.0,
        "SEARXNG_AUTO_START": True,
        "SEARXNG_STOP_ON_EXIT": True,
        "SEARXNG_DOCKER_CONTAINER": "piper-searxng",
        "SEARXNG_DOCKER_IMAGE": "searxng/searxng:latest",
        "SEARXNG_DOCKER_HOST_PORT": 8888,
        "SEARXNG_DOCKER_CONTAINER_PORT": 8080,
        "SEARXNG_DOCKER_CONFIG_DIR": ".local/searxng",
        "SEARXNG_REQUIRE": False,
        "ROOT_DIR": ROOT,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _docker_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def _http_response(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *args: None
    return resp


def run_tests() -> list[dict]:
    results: list[dict] = []

    # ── 1. SEARCH_BACKEND != searxng → no-op ───────────────────────────────
    svc = SearXNGService(cfg=_make_cfg(SEARCH_BACKEND="duckduckgo"))
    res = svc.ensure_available()
    if res.ok and not res.owned_by_piper and "backend not searxng" in res.message:
        results.append(_ok("backend_not_searxng_noop"))
    else:
        results.append(_fail("backend_not_searxng_noop", f"unexpected: {res}"))

    # ── 2. Already healthy → no Docker start; shutdown does not stop ───────
    with patch("urllib.request.urlopen", return_value=_http_response(
        b'{"results": [{"title": "t", "url": "http://x"}]}'
    )):
        svc = SearXNGService(cfg=_make_cfg())
        res = svc.ensure_available()
        if res.ok and not res.owned_by_piper and "already healthy" in res.message:
            results.append(_ok("already_healthy_no_docker"))
        else:
            results.append(_fail("already_healthy_no_docker", f"unexpected: {res}"))

        # shutdown should NOT stop container when not owned
        with patch("subprocess.run") as mock_run:
            shut = svc.shutdown()
            mock_run.assert_not_called()
            if shut.ok and "not owned by Piper" in shut.message:
                results.append(_ok("shutdown_not_owned_no_stop"))
            else:
                results.append(_fail("shutdown_not_owned_no_stop", f"unexpected: {shut}"))

    # ── 3. Unhealthy + Docker unavailable + require=false → continues ──────
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with patch("shutil.which", return_value=None):
            svc = SearXNGService(cfg=_make_cfg(SEARXNG_REQUIRE=False))
            res = svc.ensure_available()
            if res.ok and "Docker is unavailable" in res.message and "continuing without SearXNG" in res.message:
                results.append(_ok("unhealthy_docker_missing_require_false"))
            else:
                results.append(_fail("unhealthy_docker_missing_require_false", f"unexpected: {res}"))

    # ── 3b. Unhealthy + Docker unavailable + require=true → fails ───────────
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with patch("shutil.which", return_value=None):
            svc = SearXNGService(cfg=_make_cfg(SEARXNG_REQUIRE=True))
            res = svc.ensure_available()
            if not res.ok and "Docker is unavailable" in res.message:
                results.append(_ok("unhealthy_docker_missing_require_true"))
            else:
                results.append(_fail("unhealthy_docker_missing_require_true", f"unexpected: {res}"))

    # ── 4. Unhealthy + auto-start disabled → no Docker start ───────────────
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_docker_proc()) as mock_run:
                svc = SearXNGService(cfg=_make_cfg(SEARXNG_AUTO_START=False))
                res = svc.ensure_available()
                mock_run.assert_not_called()
                if not res.ok and "auto-start is disabled" in res.message:
                    results.append(_ok("auto_start_disabled"))
                else:
                    results.append(_fail("auto_start_disabled", f"unexpected: {res}"))

    # ── 5. Unhealthy + Docker available + start succeeds + health OK ───────
    health_responses = [
        urllib.error.URLError("refused"),  # first check fails
        _http_response(b'{"results": [{"title": "t", "url": "http://x"}]}'),  # after start
    ]
    with patch("urllib.request.urlopen", side_effect=health_responses):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                # docker info OK
                # docker ps -> empty (no container)
                # docker run -> success
                mock_run.side_effect = [
                    _docker_proc(),  # docker info
                    _docker_proc(),  # docker ps (running)
                    _docker_proc(),  # docker ps -a (exists)
                    _docker_proc("piper-searxng\n"),  # docker ps -a (exists)
                    _docker_proc("abc123\n"),  # docker run
                ]
                svc = SearXNGService(cfg=_make_cfg())
                res = svc.ensure_available()
                if res.ok and res.owned_by_piper and "started and healthy" in res.message:
                    results.append(_ok("docker_start_and_health_ok"))
                else:
                    results.append(_fail("docker_start_and_health_ok", f"unexpected: {res}"))

                # shutdown should stop container when owned
                mock_run.reset_mock()
                mock_run.side_effect = [
                    _docker_proc(),  # docker stop
                ]
                shut = svc.shutdown()
                if shut.ok and not shut.owned_by_piper and "container stopped" in shut.message:
                    results.append(_ok("shutdown_owned_stops_container"))
                else:
                    results.append(_fail("shutdown_owned_stops_container", f"unexpected: {shut}"))

    # ── 6. shutdown when not owned → no docker stop ────────────────────────
    svc = SearXNGService(cfg=_make_cfg(SEARCH_BACKEND="duckduckgo"))
    with patch("subprocess.run") as mock_run:
        shut = svc.shutdown()
        mock_run.assert_not_called()
        if "backend not searxng" in shut.message:
            results.append(_ok("shutdown_not_owned_no_docker"))
        else:
            results.append(_fail("shutdown_not_owned_no_docker", f"unexpected: {shut}"))

    # ── 7. Container already running before Piper boots → not owned ────────
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = [
                    _docker_proc(),  # docker info
                    _docker_proc("piper-searxng\n"),  # docker ps (running)
                ]
                svc = SearXNGService(cfg=_make_cfg())
                res = svc.ensure_available()
                if res.ok and not res.owned_by_piper and "already running" in res.message:
                    results.append(_ok("pre_existing_container_not_owned"))
                else:
                    results.append(_fail("pre_existing_container_not_owned", f"unexpected: {res}"))

                # shutdown should NOT stop pre-existing container
                mock_run.reset_mock()
                shut = svc.shutdown()
                mock_run.assert_not_called()
                if "not owned by Piper" in shut.message:
                    results.append(_ok("shutdown_pre_existing_no_stop"))
                else:
                    results.append(_fail("shutdown_pre_existing_no_stop", f"unexpected: {shut}"))

    # ── 8. SEARXNG_REQUIRE=False allows boot despite failure ───────────────
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with patch("shutil.which", return_value=None):
            svc = SearXNGService(cfg=_make_cfg(SEARXNG_REQUIRE=False))
            res = svc.ensure_available()
            if res.ok and "continuing without SearXNG" in res.message:
                results.append(_ok("require_false_allows_continue"))
            else:
                results.append(_fail("require_false_allows_continue", f"unexpected: {res}"))

    # ── 9. SEARXNG_REQUIRE=True fails on missing Docker ────────────────────
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with patch("shutil.which", return_value=None):
            svc = SearXNGService(cfg=_make_cfg(SEARXNG_REQUIRE=True))
            res = svc.ensure_available()
            if not res.ok and "Docker is unavailable" in res.message:
                results.append(_ok("require_true_fails"))
            else:
                results.append(_fail("require_true_fails", f"unexpected: {res}"))

    # ── 10. STOP_ON_EXIT=false leaves container running ─────────────────────
    with patch("urllib.request.urlopen", return_value=_http_response(
        b'{"results": [{"title": "t", "url": "http://x"}]}'
    )):
        svc = SearXNGService(cfg=_make_cfg(SEARXNG_STOP_ON_EXIT=False))
        res = svc.ensure_available()
        # Manually set ownership so shutdown actually reaches the stop_on_exit check
        svc._owned_by_piper = True
        with patch("subprocess.run") as mock_run:
            shut = svc.shutdown()
            mock_run.assert_not_called()
            if "SEARXNG_STOP_ON_EXIT is false" in shut.message:
                results.append(_ok("stop_on_exit_false"))
            else:
                results.append(_fail("stop_on_exit_false", f"unexpected: {shut}"))

    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = run_tests()
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")

    if args.json:
        print(json.dumps({"passed": passed, "failed": failed, "tests": results}, indent=2))
    else:
        for r in results:
            mark = "✓" if r["status"] == "PASS" else "✗"
            print(f"{mark} {r['name']}: {r['status']}")
            if r["status"] == "FAIL":
                print(f"    reason: {r['reason']}")
        print(f"\nResults: {passed}/{len(results)} passed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
