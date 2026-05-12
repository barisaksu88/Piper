from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import tools.search as search_module  # noqa: E402


class _FakeDDGS:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def news(self, query: str, max_results: int = 8):
        del max_results
        clean = str(query or "").strip().lower()
        if clean == "latest news on llama.cpp performance benchmarks":
            return []
        if clean == "llama.cpp performance benchmarks":
            return [
                {
                    "title": "Benchmark roundup",
                    "body": "Latest benchmark roundup for llama.cpp performance.",
                    "url": "https://example.test/benchmarks",
                }
            ]
        if clean == "latest python 3.13 features":
            return [
                {
                    "title": "Breaking down Python 3.13's Latest Features",
                    "body": "A snippet for an InfoQ article about Python 3.13 feature updates.",
                    "url": "https://example.test/low-quality-infoq",
                }
            ]
        if clean == "latest python 3.13 release":
            return [
                {
                    "title": "Python 3.13.9 released",
                    "body": "Python 3.13.9 is a maintenance release with security and bug fixes.",
                    "url": "https://example.test/python-release",
                }
            ]
        if "python 3.13" in clean:
            return [
                {
                    "title": "Python decorators explained",
                    "body": "A general guide to decorators, walrus expressions, and shift operators in Python.",
                    "url": "https://example.test/python-syntax",
                }
            ]
        return []

    def text(self, query: str, max_results: int = 8):
        del max_results
        clean = str(query or "").strip().lower()
        if clean == "latest python 3.13 release":
            return [
                {
                    "title": "What is New In Python 3.13",
                    "body": "Python 3.13 release documentation covers the improved shell and free-threaded mode.",
                    "url": "https://example.test/python-whats-new",
                }
            ]
        if "python 3.13" in clean:
            return [
                {
                    "title": "Python syntax questions",
                    "body": "Common Python syntax questions about decorators and operators.",
                    "url": "https://example.test/python-syntax-text",
                }
            ]
        return []


class _RateLimitedDDGS:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def news(self, query: str, max_results: int = 8):
        del query, max_results
        raise RuntimeError("https://duckduckgo.com/news.js?q=Python+3.13+news 403 Ratelimit")

    def text(self, query: str, max_results: int = 8):
        del query, max_results
        raise RuntimeError("https://duckduckgo.com/html?q=Python+3.13+news 403 Ratelimit")


class _FakeHTTPResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        data = self._text.encode("utf-8")
        if size is None or size < 0:
            return data
        return data[:size]


def main() -> int:
    original_ddgs = search_module.DDGS
    original_fetch = search_module.fetch_clean_text
    original_urlopen = search_module.urllib.request.urlopen
    original_backend = search_module.CFG.SEARCH_BACKEND
    logs: list[str] = []

    def fake_fetch(url: str, cancel_token=None) -> str:  # noqa: ARG001
        if "low-quality-infoq" in url:
            return (
                "Title: Breaking down Python 3.13's Latest Features\n"
                + "Agent workflows and transport as a first-order concern for multi-turn tool-heavy loops. " * 12
            )
        if "python-release" in url:
            return (
                "Python 3.13 release notes. Python 3.13.9 is a maintenance release "
                "with security and bug fixes for CPython. Python 3.13 downloads are available. "
                * 6
            )
        if "python-31313" in url:
            return (
                "Python 3.13 release notes. Python 3.13.13 is a maintenance release "
                "with security and bug fixes for CPython. Python 3.13 downloads are available. "
                * 6
            )
        if "python-whats-new" in url:
            return (
                "What is new in Python 3.13. Python 3.13 release documentation describes "
                "the improved interactive interpreter, free-threaded CPython, and typing updates. "
                * 6
            )
        if "whatsnew/3.13" in url:
            return (
                "What is new in Python 3.13. Python 3.13 release documentation describes "
                "the improved interactive interpreter, free-threaded CPython, and typing updates. "
                * 6
            )
        return "A" * 160

    def fake_urlopen(req, timeout=None, **kwargs):  # noqa: ARG001
        url = str(getattr(req, "full_url", req) or "")
        if "html.duckduckgo.com/html/" not in url and "lite.duckduckgo.com/lite/" not in url:
            raise AssertionError(f"Unexpected fallback URL: {url}")
        if search_module.DDGS is not _RateLimitedDDGS:
            return _FakeHTTPResponse("<html><body></body></html>")
        if "html.duckduckgo.com/html/" in url:
            raise OSError("primary HTML endpoint transient failure")
        return _FakeHTTPResponse(
            """
            <html><body>
              <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2Frelease%2Fpython-31313%2F">
                Python Release Python 3.13.13 | Python.org
              </a>
              <a class="result__snippet">Python 3.13.13 is a maintenance release with security and bug fixes.</a>
              <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3.13%2Fwhatsnew%2F3.13.html">
                What's New In Python 3.13
              </a>
              <a class="result__snippet">Python 3.13 release documentation covers free-threaded mode and typing updates.</a>
            </body></html>
            """
        )

    try:
        search_module.CFG.SEARCH_BACKEND = "duckduckgo"
        search_module.DDGS = _FakeDDGS
        search_module.fetch_clean_text = fake_fetch
        search_module.urllib.request.urlopen = fake_urlopen
        fallback_result = search_module.perform_search(
            "latest news on llama.cpp performance benchmarks",
            data_dir=".",
            log_callback=logs.append,
            cancel_token=None,
        )
        relevance_result = search_module.perform_search(
            "search the web for latest Python 3.13 news",
            data_dir=".",
            log_callback=logs.append,
            cancel_token=None,
        )
        low_quality_deep_dive_result = search_module.perform_search(
            "latest Python 3.13 features",
            data_dir=".",
            log_callback=logs.append,
            cancel_token=None,
        )
        relevant_deep_dive_result = search_module.perform_search(
            "latest Python 3.13 release",
            data_dir=".",
            log_callback=logs.append,
            cancel_token=None,
        )
        rate_limit_log_start = len(logs)
        search_module.DDGS = _RateLimitedDDGS
        rate_limit_fallback_result = search_module.perform_search(
            "search the web for latest Python 3.13 news",
            data_dir=".",
            log_callback=logs.append,
            cancel_token=None,
        )
    finally:
        search_module.CFG.SEARCH_BACKEND = original_backend
        search_module.DDGS = original_ddgs
        search_module.fetch_clean_text = original_fetch
        search_module.urllib.request.urlopen = original_urlopen

    rate_limit_logs = logs[rate_limit_log_start:]
    success = (
        "Search Error: Zero results." not in fallback_result
        and "Source: https://example.test/benchmarks" in fallback_result
        and relevance_result.startswith("Search Error:")
        and any("No results via news for: latest news on llama.cpp performance benchmarks" in entry for entry in logs)
        and any("Search fallback succeeded with relaxed query: llama.cpp performance benchmarks" in entry for entry in logs)
        and any("did not match the core query terms" in entry for entry in logs)
        and "Source: https://example.test/low-quality-infoq" not in low_quality_deep_dive_result
        and "agent workflows" not in low_quality_deep_dive_result.casefold()
        and "No readable full-content pages were available" in low_quality_deep_dive_result
        and "Source: https://example.test/python-release" in relevant_deep_dive_result
        and "Source: https://example.test/python-whats-new" in relevant_deep_dive_result
        and "SOURCE COVERAGE: 2 readable source(s)" in relevant_deep_dive_result
        and any("Skipped low-relevance page content: example.test/low-quality-infoq" in entry for entry in logs)
        and any("Added 1 relevant result(s) from text: latest Python 3.13 release" in entry for entry in logs)
        and "Search Error:" not in rate_limit_fallback_result
        and "Source: https://www.python.org/downloads/release/python-31313/" in rate_limit_fallback_result
        and "Source: https://docs.python.org/3.13/whatsnew/3.13.html" in rate_limit_fallback_result
        and any("Search attempt (html): search the web for latest Python 3.13 news" in entry for entry in rate_limit_logs)
        and not any("403 Ratelimit" in entry for entry in rate_limit_logs)
        and not any("Search attempt (news)" in entry for entry in rate_limit_logs)
        and any("Added 2 relevant result(s) from html: search the web for latest Python 3.13 news" in entry for entry in logs)
        and any("Deep-diving up to" in entry for entry in logs)
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "logs": logs,
                "result_preview": fallback_result[:300],
                "relevance_result": relevance_result,
                "low_quality_deep_dive_result": low_quality_deep_dive_result[:500],
                "relevant_deep_dive_result": relevant_deep_dive_result[:500],
                "rate_limit_fallback_result": rate_limit_fallback_result[:500],
                "rate_limit_logs": rate_limit_logs,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
