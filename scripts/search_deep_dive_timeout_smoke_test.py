#!/usr/bin/env python3
"""Smoke tests for search deep-dive wall timeout and skip logic.

Usage:
    python scripts/search_deep_dive_timeout_smoke_test.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.search as search_module
from core.runtime_control import CancellationToken, OperationCancelled


class FakeHTTPResponse:
    """Mock urllib response with controllable read behaviour."""

    def __init__(self, chunks: list[bytes], delay_s: float = 0.0):
        self._chunks = chunks
        self._idx = 0
        self.delay_s = delay_s

    def read(self, size: int = -1) -> bytes:
        if self.delay_s:
            time.sleep(self.delay_s)
        if self._idx < len(self._chunks):
            chunk = self._chunks[self._idx]
            self._idx += 1
            return chunk
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestFetchCleanTextWallTimeout(unittest.TestCase):
    def test_returns_timeout_error_when_wall_clock_exceeded(self):
        """A slow trickle that stays under the socket timeout but exceeds wall timeout must return an error."""
        with patch.object(search_module.CFG, "SEARCH_FETCH_WALL_TIMEOUT_S", 0.2):
            with patch.object(search_module.CFG, "SEARCH_URL_FETCH_TIMEOUT_S", 10.0):
                with patch("urllib.request.urlopen") as mock_open:
                    # Each chunk arrives quickly (0s) but we emit many chunks so total > wall timeout
                    mock_open.return_value = FakeHTTPResponse(
                        [b"x"] * 200, delay_s=0.01
                    )
                    result = search_module.fetch_clean_text("https://example.test/slow")
        self.assertIn("Error reading page", result)
        self.assertIn("fetch wall timeout", result)

    def test_normal_fetch_succeeds_within_deadline(self):
        with patch.object(search_module.CFG, "SEARCH_FETCH_WALL_TIMEOUT_S", 5.0):
            with patch.object(search_module.CFG, "SEARCH_URL_FETCH_TIMEOUT_S", 10.0):
                with patch("urllib.request.urlopen") as mock_open:
                    mock_open.return_value = FakeHTTPResponse(
                        [b"This is a long enough article body to pass the min-length check. " * 10]
                    )
                    result = search_module.fetch_clean_text("https://example.test/fast")
        self.assertNotIn("Error reading page", result)
        self.assertIn("article body", result)

    def test_cancellation_interrupts_between_chunks(self):
        token = CancellationToken()
        token.cancel()
        with patch.object(search_module.CFG, "SEARCH_FETCH_WALL_TIMEOUT_S", 5.0):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.return_value = FakeHTTPResponse([b"x"] * 100, delay_s=0.05)
                with self.assertRaises(OperationCancelled):
                    search_module.fetch_clean_text("https://example.test/cancel", cancel_token=token)


class TestShouldSkipDeepDive(unittest.TestCase):
    def test_youtube_skipped(self):
        self.assertTrue(search_module._should_skip_deep_dive("https://youtube.com/watch?v=abc"))
        self.assertTrue(search_module._should_skip_deep_dive("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(search_module._should_skip_deep_dive("https://youtu.be/abc"))

    def test_tiktok_skipped(self):
        self.assertTrue(search_module._should_skip_deep_dive("https://tiktok.com/@user/video/123"))

    def test_video_path_hints_skipped(self):
        self.assertTrue(search_module._should_skip_deep_dive("https://example.com/video/news"))
        self.assertTrue(search_module._should_skip_deep_dive("https://example.com/live/stream"))

    def test_normal_article_not_skipped(self):
        self.assertFalse(search_module._should_skip_deep_dive("https://reuters.com/world/ukraine"))
        self.assertFalse(search_module._should_skip_deep_dive("https://dw.com/en/article-123"))


class TestPerformSearchResilience(unittest.TestCase):
    def _fake_results(self) -> list[dict]:
        return [
            {"title": "Reuters", "body": "Reuters snippet", "href": "https://reuters.com/article"},
            {"title": "YouTube", "body": "YouTube snippet", "href": "https://youtube.com/watch?v=abc"},
            {"title": "BBC", "body": "BBC snippet", "href": "https://bbc.com/news"},
            {"title": "Slow", "body": "Slow snippet", "href": "https://slow.test/page"},
        ]

    @patch("tools.search._collect_search_results")
    @patch("tools.search.fetch_clean_text")
    def test_continues_after_one_fetch_timeout(self, mock_fetch, mock_collect):
        mock_collect.return_value = (self._fake_results(), "query", "searxng")
        logs: list[str] = []

        def fake_fetch(url, *, cancel_token=None):
            if "slow" in url:
                return "Error reading page: fetch wall timeout after 10s"
            return "This is enough content to pass the length check. " * 10

        mock_fetch.side_effect = fake_fetch

        result = search_module.perform_search(
            "test query",
            data_dir=Path("."),
            log_callback=logs.append,
        )
        self.assertIn("Search deep-dive complete", " ".join(logs))
        self.assertIn("Reuters", result)
        self.assertIn("BBC", result)
        # Slow link logged as skipped
        self.assertTrue(any("Skipped (fetch error)" in lg and "slow" in lg for lg in logs))

    @patch("tools.search._collect_search_results")
    @patch("tools.search.fetch_clean_text")
    def test_youtube_skipped_for_deep_dive(self, mock_fetch, mock_collect):
        mock_collect.return_value = (self._fake_results(), "query", "searxng")
        logs: list[str] = []
        mock_fetch.return_value = "This is enough content to pass the length check. " * 10

        search_module.perform_search("test query", data_dir=Path("."), log_callback=logs.append)

        self.assertTrue(any("Skipped (media target)" in lg and "youtube" in lg for lg in logs))
        # fetch_clean_text should never be called for youtube
        youtube_calls = [call for call in mock_fetch.call_args_list if "youtube" in str(call)]
        self.assertEqual(len(youtube_calls), 0)

    @patch("tools.search._collect_search_results")
    @patch("tools.search.fetch_clean_text")
    def test_logs_completion_and_context_size(self, mock_fetch, mock_collect):
        mock_collect.return_value = (self._fake_results(), "query", "searxng")
        logs: list[str] = []
        mock_fetch.return_value = "This is enough content to pass the length check. " * 10

        result = search_module.perform_search("test query", data_dir=Path("."), log_callback=logs.append)

        self.assertTrue(any("Search deep-dive complete" in lg for lg in logs))
        self.assertTrue(any("Search context chars:" in lg for lg in logs))
        self.assertTrue(any("Returning search context to caller" in lg for lg in logs))
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if args.json:
        print(
            json.dumps(
                {
                    "success": result.wasSuccessful(),
                    "tests_run": result.testsRun,
                    "failures": len(result.failures),
                    "errors": len(result.errors),
                }
            )
        )

    sys.exit(0 if result.wasSuccessful() else 1)
