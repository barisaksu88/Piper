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
        return []

    def text(self, query: str, max_results: int = 8):
        del query, max_results
        return []


def main() -> int:
    original_ddgs = search_module.DDGS
    original_fetch = search_module.fetch_clean_text
    logs: list[str] = []
    try:
        search_module.DDGS = _FakeDDGS
        search_module.fetch_clean_text = lambda url, cancel_token=None: "A" * 160  # noqa: ARG005
        result = search_module.perform_search(
            "latest news on llama.cpp performance benchmarks",
            data_dir=".",
            log_callback=logs.append,
            cancel_token=None,
        )
    finally:
        search_module.DDGS = original_ddgs
        search_module.fetch_clean_text = original_fetch

    success = (
        "Search Error: Zero results." not in result
        and "Source: https://example.test/benchmarks" in result
        and any("No results via news for: latest news on llama.cpp performance benchmarks" in entry for entry in logs)
        and any("Search fallback succeeded with relaxed query: llama.cpp performance benchmarks" in entry for entry in logs)
        and any("Deep-diving up to" in entry for entry in logs)
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "logs": logs,
                "result_preview": result[:300],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
