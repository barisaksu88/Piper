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
from core.runtime_control import CancellationToken, OperationCancelled
from core.persona_output import sanitize_persona_output
from core.routing.route_normalizer import normalize_route_decision


@dataclass(frozen=True)
class SearchV1SmokeReport:
    success: bool
    meta_followup_guard_ok: bool
    quote_attribution_query_ok: bool
    unquoted_excerpt_query_ok: bool
    cancellation_token_contract_ok: bool
    no_evidence_not_verified_ok: bool
    recall_marker_stripped_ok: bool
    broad_news_needs_better_evidence_ok: bool
    recency_search_fetches_deeper_ok: bool
    contextual_search_followup_repair_ok: bool
    explicit_web_search_context_anchor_ok: bool
    search_request_tail_stripped_ok: bool
    search_correction_followup_ok: bool
    explicit_online_search_subject_ok: bool
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


def _test_unquoted_excerpt_query() -> bool:
    """Unquoted lyric/quote excerpts should still trigger verification-friendly variants."""
    variants = plan_queries("Never made it as a wise man, couldn't cut it as a poor man's stealing. Do you know the lyrics?")
    return (
        any('"Never made it as a wise man, couldn\'t cut it as a poor man\'s stealing"' in v for v in variants)
        and any("lyrics" in v for v in variants)
        and any("song" in v for v in variants)
    )


def _test_cancellation_token_contract() -> bool:
    """Pipeline should honor the shared CancellationToken API without attribute drift."""
    from core.search.backends.base import SearchBackend
    from core.search.pipeline import GroundedSearchPipeline

    class FakeBackend(SearchBackend):
        name = "fake"

        def search(self, query: str, *, max_results: int = 8):
            del query, max_results
            return []

    token = CancellationToken()
    token.cancel("test cancel")
    pipeline = GroundedSearchPipeline(backend=FakeBackend())
    try:
        pipeline.run("cancel me", cancel_token=token)
    except OperationCancelled:
        return True
    except Exception:
        return False
    return False


def _test_no_evidence_not_verified() -> bool:
    """If no passages are chosen, verdict must be 'not_verified'."""
    evidence = SearchAnswerEvidence(query="xyz unknown thing 12345")
    build_answer(evidence)
    return evidence.verdict == "not_verified" and "sufficient evidence" in evidence.answer_text.lower()


def _test_recall_marker_stripped() -> bool:
    cleaned = sanitize_persona_output("[RECALL: Baris interests AI tech]\nI found two recent AI updates.")
    return "[RECALL:" not in cleaned and "I found two recent AI updates." in cleaned


def _test_broad_news_needs_better_evidence() -> bool:
    evidence = SearchAnswerEvidence(
        query="latest AI developments news",
        chosen_sources=[
            SourcePassage(
                text="The latest AI trends include the rise of autonomous AI agents across enterprises.",
                source_url="https://www.quetext.com/blog/ai-trends-2026",
                source_title="AI Trends to Watch in 2026 | Quetext",
                relevance_score=1.08,
            ),
            SourcePassage(
                text="Agentic AI news and breakthroughs continue to shape the 2026 market.",
                source_url="https://www.crescendo.ai/news/latest-ai-news-and-updates",
                source_title="Latest AI News, Developments, and Breakthroughs | Crescendo",
                relevance_score=0.94,
            ),
        ],
    )
    build_answer(evidence)
    return evidence.verdict != "verified" and "mixed" in evidence.answer_text.lower() or "incomplete" in evidence.answer_text.lower() or "solid roundup" in evidence.answer_text.lower()


def _test_recency_search_fetches_deeper() -> bool:
    from core.search.pipeline import GroundedSearchPipeline
    from core.search.backends.base import SearchBackend
    import core.search.pipeline as _pipeline_mod

    class FakeBackend(SearchBackend):
        name = "fake"

        def search(self, query: str, *, max_results: int = 8):
            del query, max_results
            return [
                SearchResult(title="Soft 1", url="https://soft.example/1", snippet="latest AI developments news"),
                SearchResult(title="Soft 2", url="https://soft.example/2", snippet="latest AI developments news"),
                SearchResult(title="Soft 3", url="https://soft.example/3", snippet="latest AI developments news"),
                SearchResult(title="Reuters AI", url="https://www.reuters.com/technology/artificial-intelligence/", snippet="latest AI developments news"),
            ]

    fetched_urls: list[str] = []

    def _fake_fetch_source(result, *, cancel_token=None, min_length=100):
        del cancel_token, min_length
        url = str(result.get("href") or result.get("url") or "")
        fetched_urls.append(url)
        return FetchedSource(
            url=url,
            title=str(result.get("title") or ""),
            extracted_text="Latest AI developments across enterprises and infrastructure.",
            status="ok",
        )

    _orig_fetch = _pipeline_mod.fetch_source
    _pipeline_mod.fetch_source = _fake_fetch_source
    try:
        pipeline = GroundedSearchPipeline(backend=FakeBackend(), max_fetch=3, max_fetch_attempts=6, max_fetch_for_recency=5)
        pipeline.run("latest AI developments news")
        return any("reuters.com" in url for url in fetched_urls) and len(fetched_urls) >= 4
    finally:
        _pipeline_mod.fetch_source = _orig_fetch


def _test_contextual_search_followup_repair() -> bool:
    history = [
        {"role": "user", "content": "What are the latest AI improvements?"},
        {"role": "assistant", "content": "I can check online if you want."},
        {
            "role": "system",
            "content": (
                "[LATEST_RUNTIME_CONTEXT]\n"
                "Previous route: SEARCH\n"
                "Previous user request: What are the latest AI improvements?\n"
                "Search query: latest AI improvements\n"
                "Execution status: SEARCH COMPLETED\n"
            ),
            "hidden": True,
        },
    ]
    decision = {"decision": "SEARCH", "card": {"query": "Now search for the recent models"}}
    normalized = normalize_route_decision(decision, "Now search for the recent models", history)
    query = str(((normalized or {}).get("card") or {}).get("query") or "")
    return "ai" in query.lower() and "models" in query.lower()


def _test_explicit_web_search_context_anchor() -> bool:
    history = [
        {"role": "user", "content": "What are the latest AI improvements?"},
        {"role": "assistant", "content": "I can check online if you want."},
        {
            "role": "system",
            "content": (
                "[LATEST_RUNTIME_CONTEXT]\n"
                "Previous route: SEARCH\n"
                "Previous user request: What are the latest AI improvements?\n"
                "Search query: latest AI improvements\n"
                "Execution status: SEARCH COMPLETED\n"
            ),
            "hidden": True,
        },
    ]
    decision = {"decision": "CHAT", "card": {"query": ""}}
    normalized = normalize_route_decision(
        decision,
        "Do an online search for the recent models.",
        history,
    )
    query = str(((normalized or {}).get("card") or {}).get("query") or "")
    return (
        str((normalized or {}).get("decision") or "") == "SEARCH"
        and "ai" in query.lower()
        and "models" in query.lower()
        and "hello" not in query.lower()
    )


def _test_search_request_tail_stripped() -> bool:
    decision = {"decision": "SEARCH", "card": {"query": ""}}
    normalized = normalize_route_decision(
        decision,
        "Search the web for Project Halcyon Lantern and tell me what you already know while it loads.",
        [],
    )
    query = str(((normalized or {}).get("card") or {}).get("query") or "")
    return query == "Project Halcyon Lantern"


def _test_search_correction_followup() -> bool:
    history = [
        {
            "role": "system",
            "content": (
                "[LATEST_RUNTIME_CONTEXT]\n"
                "Previous route: SEARCH\n"
                "Previous user request: Research on the\n"
                "Search query: Research on the\n"
                "Execution status: SEARCH COMPLETED\n"
                "Runtime note: Search summary was prepared for the user.\n"
            ),
            "hidden": True,
        },
        {
            "role": "system",
            "content": "[SEARCH SUMMARY FOR 'Research on the']\nOld summary",
            "hidden": True,
        },
        {
            "role": "assistant",
            "content": "Both 'research on' and 'research in' are correct.",
        },
    ]
    decision = {"decision": "CHAT", "card": {"query": "It got cut off, I meant research on the latest AI news."}}
    normalized = normalize_route_decision(
        decision,
        "It got cut off, I meant research on the latest AI news.",
        history,
    )
    query = str(((normalized or {}).get("card") or {}).get("query") or "")
    return str((normalized or {}).get("decision") or "") == "SEARCH" and query.lower() == "research on the latest ai news"


def _test_explicit_online_search_subject() -> bool:
    history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "What should I call you?"},
    ]
    decision = {"decision": "SEARCH", "card": {"query": "Do an online search for the recent developments in the AI."}}
    normalized = normalize_route_decision(
        decision,
        "Do an online search for the recent developments in the AI.",
        history,
    )
    query = str(((normalized or {}).get("card") or {}).get("query") or "")
    return (
        str((normalized or {}).get("decision") or "") == "SEARCH"
        and "recent developments in the ai" in query.lower()
        and "hello" not in query.lower()
    )


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
        "unquoted_excerpt_query_ok": _test_unquoted_excerpt_query(),
        "cancellation_token_contract_ok": _test_cancellation_token_contract(),
        "no_evidence_not_verified_ok": _test_no_evidence_not_verified(),
        "recall_marker_stripped_ok": _test_recall_marker_stripped(),
        "broad_news_needs_better_evidence_ok": _test_broad_news_needs_better_evidence(),
        "recency_search_fetches_deeper_ok": _test_recency_search_fetches_deeper(),
        "contextual_search_followup_repair_ok": _test_contextual_search_followup_repair(),
        "explicit_web_search_context_anchor_ok": _test_explicit_web_search_context_anchor(),
        "search_request_tail_stripped_ok": _test_search_request_tail_stripped(),
        "search_correction_followup_ok": _test_search_correction_followup(),
        "explicit_online_search_subject_ok": _test_explicit_online_search_subject(),
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
