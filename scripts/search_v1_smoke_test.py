"""Deterministic smoke tests for Search v1 grounded retrieval pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.search.contracts import FetchedSource, SearchAnswerEvidence, SearchResult, SourcePassage
from core.search.extractor import extract_passages
from core.search.fetcher import fetch_source
from core.search.query_planner import plan_queries
from core.search.scorer import score_passages, score_source
from core.search.answer_builder import build_answer


@dataclass(frozen=True)
class SearchV1SmokeReport:
    success: bool
    meta_followup_guard_ok: bool
    quote_attribution_query_ok: bool
    no_evidence_not_verified_ok: bool
    extraction_produces_text_ok: bool
    scorer_prefers_exact_match_ok: bool
    end_to_end_pipeline_ok: bool


def _test_meta_followup_guard() -> bool:
    """Meta follow-ups like 'search more' should not become literal queries."""
    variants = plan_queries("search more")
    return variants == ["search more"]


def _test_quote_attribution_query() -> bool:
    """Quoted text should trigger attribution query variants."""
    variants = plan_queries('Who said "to be or not to be"?')
    return any("attribution" in v for v in variants) and any("source" in v for v in variants)


def _test_no_evidence_not_verified() -> bool:
    """If no passages are chosen, verdict must be 'not_verified'."""
    evidence = SearchAnswerEvidence(query="xyz unknown thing 12345")
    build_answer(evidence)
    return evidence.verdict == "not_verified" and "sufficient evidence" in evidence.answer_text.lower()


def _test_extraction_produces_text() -> bool:
    """Passage extraction must return non-empty text from readable content."""
    content = (
        "Python 3.13 was released in October 2024. "
        "It includes significant performance improvements and a new incremental garbage collector. "
        "The release also introduces improved error messages for common syntax mistakes."
    )
    passages = extract_passages(
        content,
        query="Python 3.13 release date",
        source_url="https://example.com/python-news",
        source_title="Python News",
    )
    return len(passages) > 0 and all(len(p.text) > 0 for p in passages)


def _test_scorer_prefers_exact_match() -> bool:
    """Source scorer should rank exact phrase matches higher than generic overlap."""
    exact_match = FetchedSource(
        url="https://example.com/exact",
        title="Exact",
        extracted_text="The Python 3.13 release date was October 2024.",
        status="ok",
    )
    generic = FetchedSource(
        url="https://example.com/generic",
        title="Generic",
        extracted_text="Python is a popular programming language used by millions of developers worldwide.",
        status="ok",
    )
    exact_score = score_source(exact_match, "Python 3.13 release date")
    generic_score = score_source(generic, "Python 3.13 release date")
    return exact_score > generic_score


def _test_end_to_end_pipeline() -> bool:
    """Run the full pipeline with a fake backend and monkeypatched fetch."""
    from core.search.backends.base import SearchBackend
    from core.search.pipeline import GroundedSearchPipeline

    class FakeBackend(SearchBackend):
        name = "fake"

        def search(self, query: str, *, max_results: int = 8):
            return [
                SearchResult(
                    title="Python 3.13 Release",
                    url="https://example.com/python-3-13",
                    snippet="Python 3.13 was released in October 2024 with new features.",
                )
            ]

    # Monkeypatch fetch_source so the fake URL returns readable text.
    # Patch both modules because GroundedSearchPipeline imports it directly.
    def _fake_fetch_source(result, *, cancel_token=None, min_length=100):
        url = str(result.get("href") or result.get("url") or "").strip()
        if "example.com" in url:
            return FetchedSource(
                url=url,
                title=str(result.get("title") or ""),
                extracted_text=(
                    "Python 3.13 was released in October 2024. "
                    "It includes a new incremental garbage collector and improved error messages."
                ),
                status="ok",
            )
        return fetch_source(result, cancel_token=cancel_token, min_length=min_length)

    import core.search.pipeline as _pipeline_mod
    import core.search.fetcher as _fetcher_mod

    _orig_pipeline_fetch = _pipeline_mod.fetch_source
    _orig_fetcher_fetch = _fetcher_mod.fetch_source
    _pipeline_mod.fetch_source = _fake_fetch_source
    _fetcher_mod.fetch_source = _fake_fetch_source
    try:
        pipeline = GroundedSearchPipeline(backend=FakeBackend(), max_fetch=1)
        evidence = pipeline.run("Python 3.13 release date")

        if evidence.verdict not in ("verified", "partial"):
            return False
        if not evidence.chosen_sources:
            return False
        reporter = evidence.to_reporter_string()
        if "VERDICT:" not in reporter:
            return False
        if "ANSWER:" not in reporter:
            return False
        return True
    finally:
        _pipeline_mod.fetch_source = _orig_pipeline_fetch
        _fetcher_mod.fetch_source = _orig_fetcher_fetch


def run_smoke() -> SearchV1SmokeReport:
    results = {
        "meta_followup_guard_ok": _test_meta_followup_guard(),
        "quote_attribution_query_ok": _test_quote_attribution_query(),
        "no_evidence_not_verified_ok": _test_no_evidence_not_verified(),
        "extraction_produces_text_ok": _test_extraction_produces_text(),
        "scorer_prefers_exact_match_ok": _test_scorer_prefers_exact_match(),
        "end_to_end_pipeline_ok": _test_end_to_end_pipeline(),
    }
    success = all(results.values())
    return SearchV1SmokeReport(success=success, **results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search v1 deterministic smoke tests.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print report as JSON.")
    args = parser.parse_args()

    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"SUCCESS: {report.success}")
        for field_name, value in asdict(report).items():
            if field_name != "success":
                print(f"  {field_name}: {value}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
