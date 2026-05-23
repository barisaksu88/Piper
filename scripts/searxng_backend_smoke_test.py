#!/usr/bin/env python3
"""Deterministic smoke tests for SearXNGBackend (with mocked HTTP).

Usage:
    python scripts/searxng_backend_smoke_test.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.search.backends.searxng import SearXNGBackend, SearchResult


def _ok(name: str) -> dict:
    return {"name": name, "status": "PASS"}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "status": "FAIL", "reason": reason}


def _make_mock_response(body_bytes: bytes) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = body_bytes
    mock.__enter__ = lambda self: self
    mock.__exit__ = lambda *args: None
    return mock


def run_tests() -> list[dict]:
    results: list[dict] = []

    # ── 1. Mocked JSON → SearchResult list ─────────────────────────────────
    backend = SearXNGBackend(base_url="http://test-searxng:8888")
    mock_payload = json.dumps({
        "query": "python",
        "results": [
            {
                "title": "Python Programming Language",
                "url": "https://python.org",
                "content": "The official home of the Python Programming Language.",
                "engine": "google",
            },
            {
                "title": "Learn Python",
                "url": "https://realpython.com",
                "content": "Python tutorials and articles.",
            },
        ],
    }).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_payload)):
        try:
            search_results = backend.search("python", max_results=5)
            if (
                len(search_results) == 2
                and search_results[0].title == "Python Programming Language"
                and search_results[0].url == "https://python.org"
                and search_results[0].snippet == "The official home of the Python Programming Language."
                and search_results[0].source == "google"
            ):
                results.append(_ok("mocked_json_to_results"))
            else:
                results.append(_fail("mocked_json_to_results", f"unexpected results: {search_results}"))
        except Exception as exc:
            results.append(_fail("mocked_json_to_results", f"raised {type(exc).__name__}: {exc}"))

    # ── 2. Empty results → empty list ──────────────────────────────────────
    mock_empty = json.dumps({"results": []}).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_empty)):
        try:
            search_results = backend.search("xyznonexistent12345", max_results=5)
            if search_results == []:
                results.append(_ok("empty_results_empty_list"))
            else:
                results.append(_fail("empty_results_empty_list", f"expected [], got {search_results}"))
        except Exception as exc:
            results.append(_fail("empty_results_empty_list", f"raised {type(exc).__name__}: {exc}"))

    # ── 3. Timeout error → reported clearly ────────────────────────────────
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        try:
            backend.search("anything")
            results.append(_fail("timeout_reported", "expected RuntimeError, got success"))
        except RuntimeError as exc:
            if "timeout" in str(exc).lower():
                results.append(_ok("timeout_reported"))
            else:
                results.append(_fail("timeout_reported", f"wrong message: {exc}"))
        except Exception as exc:
            results.append(_fail("timeout_reported", f"wrong exception type: {type(exc).__name__}: {exc}"))

    # ── 4. Connection error → reported clearly ─────────────────────────────
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        try:
            backend.search("anything")
            results.append(_fail("connection_error_reported", "expected RuntimeError, got success"))
        except RuntimeError as exc:
            if "unreachable" in str(exc).lower() or "connection" in str(exc).lower():
                results.append(_ok("connection_error_reported"))
            else:
                results.append(_fail("connection_error_reported", f"wrong message: {exc}"))
        except Exception as exc:
            results.append(_fail("connection_error_reported", f"wrong exception type: {type(exc).__name__}: {exc}"))

    # ── 5. max_results respected ───────────────────────────────────────────
    many_results = {
        "results": [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": f"Content {i}"}
            for i in range(20)
        ],
    }
    mock_many = json.dumps(many_results).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_many)):
        try:
            search_results = backend.search("test", max_results=5)
            if len(search_results) == 5:
                results.append(_ok("max_results_respected"))
            else:
                results.append(_fail("max_results_respected", f"expected 5, got {len(search_results)}"))
        except Exception as exc:
            results.append(_fail("max_results_respected", f"raised {type(exc).__name__}: {exc}"))

    # ── 6. Malformed entries skipped safely ────────────────────────────────
    malformed_payload = json.dumps({
        "results": [
            {"title": "Valid", "url": "https://valid.com"},
            {"title": "Missing URL"},  # skipped — no URL
            "not a dict",  # skipped
            {"url": "https://no-title.com"},  # skipped — no title
            {"title": "Second Valid", "url": "https://valid2.com", "href": "https://valid2.com"},
        ],
    }).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=_make_mock_response(malformed_payload)):
        try:
            search_results = backend.search("test", max_results=10)
            if len(search_results) == 2 and search_results[0].title == "Valid" and search_results[1].title == "Second Valid":
                results.append(_ok("malformed_entries_skipped"))
            else:
                results.append(_fail("malformed_entries_skipped", f"unexpected results: {search_results}"))
        except Exception as exc:
            results.append(_fail("malformed_entries_skipped", f"raised {type(exc).__name__}: {exc}"))

    # ── 7. Invalid JSON → RuntimeError ─────────────────────────────────────
    with patch("urllib.request.urlopen", return_value=_make_mock_response(b"not json")):
        try:
            backend.search("test")
            results.append(_fail("invalid_json_error", "expected RuntimeError, got success"))
        except RuntimeError as exc:
            if "json" in str(exc).lower():
                results.append(_ok("invalid_json_error"))
            else:
                results.append(_fail("invalid_json_error", f"wrong message: {exc}"))
        except Exception as exc:
            results.append(_fail("invalid_json_error", f"wrong exception type: {type(exc).__name__}: {exc}"))

    # ── 8. Empty query → empty list ────────────────────────────────────────
    with patch("urllib.request.urlopen") as mock_urlopen:
        search_results = backend.search("", max_results=5)
        mock_urlopen.assert_not_called()
        if search_results == []:
            results.append(_ok("empty_query_no_request"))
        else:
            results.append(_fail("empty_query_no_request", f"expected [], got {search_results}"))

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
