#!/usr/bin/env python3
"""Deterministic smoke for adaptive SearXNG search depth.

Usage:
    python scripts/search_tool_searxng_depth_smoke_test.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.search as search_module


@dataclass(frozen=True)
class SearxngDepthReport:
    success: bool
    attempted_queries: list[str]
    result_preview: str
    logs: list[str]


def _fake_results(query: str) -> list[dict]:
    normalized = " ".join(str(query or "").split()).strip()
    by_query = {
        "recent developments in AI": [
            ("AI developments briefing one", "Recent developments in AI include model releases and agent tooling.", "https://example.test/ai-1"),
            ("AI developments briefing two", "Recent developments in AI include infrastructure and deployment changes.", "https://example.test/ai-2"),
        ],
        "developments in AI": [
            ("AI developments briefing three", "Developments in AI include benchmark updates and multimodal systems.", "https://example.test/ai-3"),
            ("AI developments briefing four", "Developments in AI include enterprise automation and agents.", "https://example.test/ai-4"),
        ],
        "latest developments in AI news": [
            ("Latest AI developments briefing five", "Latest developments in AI news include new model evaluations.", "https://example.test/ai-5"),
            ("Latest AI developments briefing six", "Latest developments in AI news include inference improvements.", "https://example.test/ai-6"),
        ],
    }
    return [
        {"title": title, "body": body, "href": href}
        for title, body, href in by_query.get(normalized, [])
    ]


def run_smoke() -> SearxngDepthReport:
    original_backend = search_module.CFG.SEARCH_BACKEND
    original_run_searxng = search_module._run_searxng_search
    original_fetch = search_module.fetch_clean_text
    attempted_queries: list[str] = []
    logs: list[str] = []

    def fake_run_searxng(query: str, *, cancel_token=None):  # noqa: ARG001
        attempted_queries.append(" ".join(str(query or "").split()).strip())
        return _fake_results(query)

    def fake_fetch(url: str, *, cancel_token=None):  # noqa: ARG001
        clean_url = str(url or "").strip()
        return (
            "Readable full-content article about recent developments in AI. "
            "This article discusses model releases, agent tooling, benchmark updates, "
            f"and deployment shifts. Source URL: {clean_url}. "
            "The content is intentionally long enough for the search reader threshold."
        )

    try:
        search_module.CFG.SEARCH_BACKEND = "searxng"
        search_module._run_searxng_search = fake_run_searxng
        search_module.fetch_clean_text = fake_fetch
        result = search_module.perform_search(
            "recent developments in AI",
            ROOT / "data",
            log_callback=logs.append,
            cancel_token=None,
        )
    finally:
        search_module.CFG.SEARCH_BACKEND = original_backend
        search_module._run_searxng_search = original_run_searxng
        search_module.fetch_clean_text = original_fetch

    success = (
        attempted_queries[:3] == [
            "recent developments in AI",
            "developments in AI",
            "latest developments in AI news",
        ]
        and "Candidate results after filtering: 6" in result
        and "SOURCE COVERAGE: 6 readable source(s) from 6 candidate result(s)." in result
    )
    return SearxngDepthReport(
        success=bool(success),
        attempted_queries=attempted_queries,
        result_preview=str(result or "")[:800],
        logs=logs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify SearXNG search keeps gathering when the first query is thin.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    args = parser.parse_args()

    report = run_smoke()
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"ATTEMPTED_QUERIES: {report.attempted_queries}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
