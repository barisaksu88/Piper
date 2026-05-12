"""SearXNG lifecycle management for Piper.

Manages a local SearXNG Docker container:
- Checks health before use
- Auto-starts container if configured and not already running
- Tracks ownership (did Piper start it, or was it already there?)
- Stops container on shutdown only if Piper started it and config allows

This module does NOT run Docker commands during import.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from config import CFG

_LOG = logging.getLogger(__name__)

_DEFAULT_SETTINGS_YML = """use_default_settings: true

server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "piper-local-test-only-change-me"
  limiter: false
  image_proxy: false

search:
  formats:
    - html
    - json
"""


@dataclass(frozen=True)
class SearXNGServiceResult:
    ok: bool
    owned_by_piper: bool
    message: str


class SearXNGService:
    """Manages the lifecycle of a local SearXNG Docker container."""

    def __init__(self, cfg=None) -> None:
        self._cfg = cfg or CFG
        self._owned_by_piper = False

    # ── Public API ──────────────────────────────────────────────────────────

    def ensure_available(self) -> SearXNGServiceResult:
        """Ensure SearXNG is available for use.

        If SEARCH_BACKEND != 'searxng', returns immediately.
        If already healthy, returns success without touching Docker.
        If unhealthy and auto-start is enabled, attempts to start Docker.
        If Docker unavailable or start fails, returns honest failure.
        """
        if str(getattr(self._cfg, "SEARCH_BACKEND", "")).strip().lower() != "searxng":
            return SearXNGServiceResult(ok=True, owned_by_piper=False, message="backend not searxng")

        if self.health_check():
            _LOG.info("SearXNG already healthy at %s", self._url())
            return SearXNGServiceResult(ok=True, owned_by_piper=False, message="already healthy")

        if not getattr(self._cfg, "SEARXNG_AUTO_START", True):
            msg = f"SearXNG not reachable at {self._url()} and auto-start is disabled"
            _LOG.warning(msg)
            return SearXNGServiceResult(ok=False, owned_by_piper=False, message=msg)

        if not self.docker_available():
            msg = "SearXNG not reachable and Docker is unavailable"
            _LOG.warning(msg)
            if getattr(self._cfg, "SEARXNG_REQUIRE", False):
                return SearXNGServiceResult(ok=False, owned_by_piper=False, message=msg)
            return SearXNGServiceResult(ok=True, owned_by_piper=False, message=f"{msg}; continuing without SearXNG")

        result = self.start_container()
        if not result.ok:
            if getattr(self._cfg, "SEARXNG_REQUIRE", False):
                return result
            return SearXNGServiceResult(
                ok=True,
                owned_by_piper=False,
                message=f"{result.message}; continuing without SearXNG",
            )

        # If the container was already running before we got here, we don't own it
        # and don't need to wait for health confirmation.
        if result.ok and not result.owned_by_piper and "already running" in result.message:
            return result

        # Wait briefly for health to stabilize
        for _ in range(10):
            if self.health_check():
                self._owned_by_piper = True
                _LOG.info("SearXNG started by Piper and is healthy")
                return SearXNGServiceResult(ok=True, owned_by_piper=True, message="started and healthy")
            time.sleep(0.5)

        msg = "SearXNG container started but never became healthy"
        _LOG.warning(msg)
        if getattr(self._cfg, "SEARXNG_REQUIRE", False):
            return SearXNGServiceResult(ok=False, owned_by_piper=True, message=msg)
        return SearXNGServiceResult(ok=True, owned_by_piper=True, message=f"{msg}; continuing without SearXNG")

    def shutdown(self) -> SearXNGServiceResult:
        """Shutdown SearXNG if Piper owns it and config allows stopping.

        Safe to call multiple times.
        """
        if str(getattr(self._cfg, "SEARCH_BACKEND", "")).strip().lower() != "searxng":
            return SearXNGServiceResult(ok=True, owned_by_piper=False, message="backend not searxng")

        if not self._owned_by_piper:
            return SearXNGServiceResult(ok=True, owned_by_piper=False, message="not owned by Piper; leaving container running")

        if not getattr(self._cfg, "SEARXNG_STOP_ON_EXIT", True):
            return SearXNGServiceResult(ok=True, owned_by_piper=True, message="owned but SEARXNG_STOP_ON_EXIT is false; leaving container running")

        container = self._container_name()
        _LOG.info("Stopping SearXNG container %s (Piper-owned)", container)
        proc = self._run_docker(["stop", container], timeout=15.0)
        if proc.returncode == 0:
            self._owned_by_piper = False
            return SearXNGServiceResult(ok=True, owned_by_piper=False, message="container stopped")
        return SearXNGServiceResult(ok=False, owned_by_piper=True, message=f"docker stop failed: {proc.stderr.strip()}")

    def health_check(self) -> bool:
        """Quick health check: GET /search?q=test&format=json must return HTTP 200 + parseable JSON."""
        url = f"{self._url()}/search?q=test&format=json"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self._health_timeout()) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
                payload = json.loads(data)
                results = payload.get("results") if isinstance(payload, dict) else None
                return isinstance(results, list)
        except Exception:
            return False

    def docker_available(self) -> bool:
        """True if the `docker` CLI is on PATH and responds to `docker info`."""
        if shutil.which("docker") is None:
            return False
        proc = self._run_docker(["info"], timeout=5.0)
        return proc.returncode == 0

    def start_container(self) -> SearXNGServiceResult:
        """Start the SearXNG Docker container if not already running.

        If container exists but is stopped, starts it.
        If container does not exist, creates and starts it with volume mount.
        """
        container = self._container_name()

        # Already running?
        if self._container_running(container):
            return SearXNGServiceResult(ok=True, owned_by_piper=False, message="container already running")

        # Exists but stopped?
        if self._container_exists(container):
            _LOG.info("Starting existing SearXNG container %s", container)
            proc = self._run_docker(["start", container], timeout=15.0)
            if proc.returncode == 0:
                return SearXNGServiceResult(ok=True, owned_by_piper=True, message="existing container started")
            return SearXNGServiceResult(ok=False, owned_by_piper=False, message=f"docker start failed: {proc.stderr.strip()}")

        # Create config dir + settings.yml before first run
        self._ensure_config_dir()

        host_port = int(getattr(self._cfg, "SEARXNG_DOCKER_HOST_PORT", 8888))
        container_port = int(getattr(self._cfg, "SEARXNG_DOCKER_CONTAINER_PORT", 8080))
        image = str(getattr(self._cfg, "SEARXNG_DOCKER_IMAGE", "searxng/searxng:latest"))
        config_dir = self._config_dir()

        port_map = f"{host_port}:{container_port}"
        volume_map = f"{config_dir}:/etc/searxng"

        _LOG.info("Creating SearXNG container %s (%s -> %s)", container, image, port_map)
        proc = self._run_docker(
            [
                "run", "--rm", "-d",
                "--name", container,
                "-p", port_map,
                "-v", volume_map,
                image,
            ],
            timeout=60.0,
        )
        if proc.returncode == 0:
            return SearXNGServiceResult(ok=True, owned_by_piper=True, message="new container created and started")
        return SearXNGServiceResult(ok=False, owned_by_piper=False, message=f"docker run failed: {proc.stderr.strip()}")

    # ── Internal helpers ────────────────────────────────────────────────────

    def _url(self) -> str:
        return str(getattr(self._cfg, "SEARXNG_URL", "http://127.0.0.1:8888")).rstrip("/")

    def _health_timeout(self) -> float:
        return float(getattr(self._cfg, "SEARXNG_TIMEOUT_S", 10.0))

    def _container_name(self) -> str:
        return str(getattr(self._cfg, "SEARXNG_DOCKER_CONTAINER", "piper-searxng"))

    def _config_dir(self) -> str:
        raw = str(getattr(self._cfg, "SEARXNG_DOCKER_CONFIG_DIR", ".local/searxng"))
        path = Path(raw)
        if not path.is_absolute():
            root = Path(getattr(self._cfg, "ROOT_DIR", Path.cwd()))
            path = root / path
        return str(path.resolve())

    def _ensure_config_dir(self) -> None:
        config_dir = Path(self._config_dir())
        config_dir.mkdir(parents=True, exist_ok=True)
        settings_path = config_dir / "settings.yml"
        if not settings_path.exists():
            _LOG.info("Creating default SearXNG settings at %s", settings_path)
            settings_path.write_text(_DEFAULT_SETTINGS_YML, encoding="utf-8")

    def _container_running(self, name: str) -> bool:
        proc = self._run_docker(["ps", "--format", "{{.Names}}"], timeout=5.0)
        if proc.returncode != 0:
            return False
        return name in proc.stdout.splitlines()

    def _container_exists(self, name: str) -> bool:
        proc = self._run_docker(["ps", "-a", "--format", "{{.Names}}"], timeout=5.0)
        if proc.returncode != 0:
            return False
        return name in proc.stdout.splitlines()

    @staticmethod
    def _run_docker(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
