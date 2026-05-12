#!/usr/bin/env python3
"""Check whether a local SearXNG instance is available for Piper manual testing.

Usage:
    python scripts/searxng_local_check.py [--json] [--start]

Options:
    --json   Output machine-readable JSON.
    --start  Attempt to start the SearXNG Docker container if not running.
             Never installs Docker; fails clearly if docker is missing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"
TEST_QUERY_URL = f"{DEFAULT_SEARXNG_URL}/search?q=test&format=json"
DOCKER_CONTAINER_NAME = "piper-searxng"
DOCKER_IMAGE = "searxng/searxng:latest"
DOCKER_PORT_MAP = "8888:8080"


def _run(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_info() -> dict:
    proc = _run(["docker", "info"], timeout=5.0)
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def searxng_responds(url: str = TEST_QUERY_URL, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
            payload = json.loads(data)
            results = payload.get("results") if isinstance(payload, dict) else None
            return {
                "ok": True,
                "status": resp.status,
                "has_results": isinstance(results, list) and len(results) > 0,
                "result_count": len(results) if isinstance(results, list) else 0,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "has_results": False, "result_count": 0, "error": f"HTTP {exc.code}: {exc.reason}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "status": 0, "has_results": False, "result_count": 0, "error": f"Connection failed: {exc.reason}"}
    except TimeoutError:
        return {"ok": False, "status": 0, "has_results": False, "result_count": 0, "error": "Request timed out"}
    except json.JSONDecodeError as exc:
        return {"ok": False, "status": 0, "has_results": False, "result_count": 0, "error": f"Invalid JSON: {exc}"}
    except Exception as exc:
        return {"ok": False, "status": 0, "has_results": False, "result_count": 0, "error": f"Unexpected error: {exc}"}


def container_exists(name: str = DOCKER_CONTAINER_NAME) -> bool:
    proc = _run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=5.0)
    if proc.returncode != 0:
        return False
    return name in proc.stdout.splitlines()


def container_running(name: str = DOCKER_CONTAINER_NAME) -> bool:
    proc = _run(["docker", "ps", "--format", "{{.Names}}"], timeout=5.0)
    if proc.returncode != 0:
        return False
    return name in proc.stdout.splitlines()


def start_searxng() -> dict:
    if not docker_available():
        return {"ok": False, "error": "Docker is not installed or not on PATH. Install Docker Desktop manually."}

    if container_running():
        return {"ok": True, "error": "", "note": "Container already running."}

    if container_exists():
        # Container exists but is stopped; start it
        proc = _run(["docker", "start", DOCKER_CONTAINER_NAME], timeout=10.0)
        if proc.returncode == 0:
            return {"ok": True, "error": "", "note": "Existing container started."}
        return {"ok": False, "error": f"Failed to start existing container: {proc.stderr.strip()}"}

    # Create and start new container
    proc = _run(
        [
            "docker", "run", "--rm", "-d",
            "--name", DOCKER_CONTAINER_NAME,
            "-p", DOCKER_PORT_MAP,
            DOCKER_IMAGE,
        ],
        timeout=60.0,
    )
    if proc.returncode == 0:
        return {"ok": True, "error": "", "note": "New container started."}
    return {"ok": False, "error": f"Failed to start container: {proc.stderr.strip()}"}


def build_report(start: bool = False) -> dict:
    report: dict = {
        "docker_available": False,
        "docker_info_ok": False,
        "searxng_responds": False,
        "searxng_result_count": 0,
        "container_running": False,
        "container_exists": False,
        "actions": [],
        "next_steps": [],
    }

    if not docker_available():
        report["next_steps"].append(
            "Docker is not installed or not on PATH. "
            "Install Docker Desktop for Windows manually: https://www.docker.com/products/docker-desktop/"
        )
        report["next_steps"].append("Enable the WSL2 backend during Docker setup, then restart your terminal.")
        return report

    report["docker_available"] = True

    info = docker_info()
    report["docker_info_ok"] = info["ok"]
    if not info["ok"]:
        report["next_steps"].append("Docker command exists but 'docker info' failed. Is Docker Desktop running?")
        return report

    report["container_exists"] = container_exists()
    report["container_running"] = container_running()

    if start:
        action = start_searxng()
        report["actions"].append(action)
        if action["ok"]:
            report["container_running"] = container_running()
        else:
            report["next_steps"].append(action["error"])

    health = searxng_responds()
    report["searxng_responds"] = health["ok"]
    report["searxng_result_count"] = health["result_count"]

    if health["ok"]:
        report["next_steps"].append(
            f"SearXNG is healthy at {DEFAULT_SEARXNG_URL} with {health['result_count']} result(s)."
        )
        report["next_steps"].append(
            'Set SEARCH_BACKEND = "searxng" in config.py and restart Piper to test.'
        )
    else:
        report["next_steps"].append(
            f"SearXNG did not respond at {DEFAULT_SEARXNG_URL}: {health['error']}"
        )
        if report["container_running"]:
            report["next_steps"].append("Container is running but SearXNG is not responding yet. Wait a few seconds and retry.")
        elif report["container_exists"]:
            report["next_steps"].append(f"Container exists but is stopped. Start it with: docker start {DOCKER_CONTAINER_NAME}")
        else:
            report["next_steps"].append(
                f"No container found. Start one with: docker run --rm -d --name {DOCKER_CONTAINER_NAME} -p {DOCKER_PORT_MAP} {DOCKER_IMAGE}"
            )
            report["next_steps"].append("Or run this script with --start to start it automatically.")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local SearXNG availability for Piper.")
    parser.add_argument("--json", action="store_true", help="Output JSON report.")
    parser.add_argument("--start", action="store_true", help="Start the SearXNG container if needed.")
    args = parser.parse_args()

    report = build_report(start=args.start)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("SearXNG Local Check for Piper")
        print("=" * 40)
        print(f"Docker available:      {'yes' if report['docker_available'] else 'no'}")
        print(f"Docker info OK:        {'yes' if report['docker_info_ok'] else 'no'}")
        print(f"Container exists:      {'yes' if report['container_exists'] else 'no'}")
        print(f"Container running:     {'yes' if report['container_running'] else 'no'}")
        print(f"SearXNG responds:      {'yes' if report['searxng_responds'] else 'no'}")
        print(f"SearXNG result count:  {report['searxng_result_count']}")
        if report["actions"]:
            print()
            print("Actions taken:")
            for action in report["actions"]:
                mark = "OK" if action["ok"] else "FAIL"
                print(f"  [{mark}] {action.get('note', action.get('error', ''))}")
        print()
        print("Next steps:")
        for step in report["next_steps"]:
            print(f"  - {step}")

    return 0 if report["searxng_responds"] else 1


if __name__ == "__main__":
    sys.exit(main())
