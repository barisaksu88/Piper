"""Grounded Search v1 Pipeline orchestrator."""

from __future__ import annotations

import logging
from typing import List, Optional

from core.runtime_control import CancellationToken
from core.search.backends.base import SearchBackend
from core.search.backends.duckduckgo import DuckDuckGoBackend
from core.search.contracts import (
    FetchedSource,
    SearchAnswerEvidence,
    SearchResult,
    SourcePassage,
)
from core.search.extractor import extract_passages
from core.search.fetcher import fetch_source
from core.search.query_planner import plan_queries
from core.search.scorer import score_passages, score_source
from core.search.answer_builder import build_answer

_LOG = logging.getLogger(__name__)


class GroundedSearchPipeline:
    """Orchestrates query planning, search, fetching, extraction, scoring, and answer building."""

    def __init__(
        self,
        backend: Optional[SearchBackend] = None,
        max_results_per_query: int = 5,
        max_fetch: int = 3,
        max_passages_per_source: int = 2,
        max_total_passages: int = 5,
    ) -> None:
        self.backend = backend or DuckDuckGoBackend()
        self.max_results_per_query = max_results_per_query
        self.max_fetch = max_fetch
        self.max_passages_per_source = max_passages_per_source
        self.max_total_passages = max_total_passages

    def run(
        self,
        query: str,
        *,
        cancel_token: CancellationToken | None = None,
        log_callback=None,
    ) -> SearchAnswerEvidence:
        """Run the full grounded search pipeline.

        Args:
            query: The original user query.
            cancel_token: Optional cancellation token.
            log_callback: Optional callback for progress messages.

        Returns:
            SearchAnswerEvidence with structured results and grounded answer.
        """

        def log(msg: str) -> None:
            _LOG.info("%s", msg)
            if log_callback:
                log_callback(msg)

        evidence = SearchAnswerEvidence(query=query)

        # 1. Query planning
        variants = plan_queries(query)
        evidence.query_variants = variants
        log(f"Search query variants: {variants}")

        if not variants:
            evidence.verdict = "not_verified"
            evidence.answer_text = "The query was too ambiguous to search. Please clarify."
            return evidence

        # 2. Search each variant and collect unique results
        seen_urls: set[str] = set()
        all_results: List[SearchResult] = []

        for variant in variants:
            if cancel_token and cancel_token.cancelled:
                raise RuntimeError("Search cancelled.")

            try:
                results = self.backend.search(variant, max_results=self.max_results_per_query)
            except Exception as exc:
                log(f"Search backend error for variant '{variant}': {exc}")
                continue

            for r in results:
                if r.url in seen_urls:
                    continue
                seen_urls.add(r.url)
                all_results.append(r)

        evidence.results = all_results
        log(f"Collected {len(all_results)} unique results.")

        if not all_results:
            evidence.verdict = "not_verified"
            evidence.answer_text = "No search results were found for this query."
            return evidence

        # 3. Fetch top candidate pages
        fetched: List[FetchedSource] = []
        fetch_count = 0
        for r in all_results:
            if cancel_token and cancel_token.cancelled:
                raise RuntimeError("Search cancelled.")
            if fetch_count >= self.max_fetch:
                break

            log(f"Fetching: {r.url}")
            source = fetch_source(
                {"title": r.title, "href": r.url, "body": r.snippet},
                cancel_token=cancel_token,
            )
            fetched.append(source)
            if source.status == "ok":
                fetch_count += 1
                log(f"  -> OK ({len(source.extracted_text)} chars)")
            else:
                log(f"  -> {source.status}: {source.error or ''}")

        evidence.fetched_sources = fetched

        # 4. Extract passages from fetched sources
        all_passages: List[SourcePassage] = []
        for source in fetched:
            if source.status != "ok":
                continue
            passages = extract_passages(
                source.extracted_text,
                query=query,
                source_url=source.url,
                source_title=source.title,
                max_passages=self.max_passages_per_source,
            )
            # Apply source-level score boost (construct new frozen instances)
            source_score = score_source(source, query)
            for p in passages:
                boosted = SourcePassage(
                    text=p.text,
                    source_url=p.source_url,
                    source_title=p.source_title,
                    relevance_score=round(p.relevance_score + source_score * 0.1, 3),
                )
                all_passages.append(boosted)

        # 5. Score and rank passages
        ranked = score_passages(all_passages)
        evidence.chosen_sources = ranked[: self.max_total_passages]
        log(f"Selected {len(evidence.chosen_sources)} supporting passages.")

        # 6. Build grounded answer
        build_answer(evidence)
        log(f"Search verdict: {evidence.verdict}")
        return evidence
