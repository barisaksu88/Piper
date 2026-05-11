"""DuckDuckGo search backend."""

from __future__ import annotations

from typing import List

from core.search.backends.base import SearchBackend
from core.search.contracts import SearchResult


class DuckDuckGoBackend(SearchBackend):
    """Backend that uses DuckDuckGo (HTML + DDGS) for search."""

    name = "duckduckgo"

    def search(self, query: str, *, max_results: int = 8) -> List[SearchResult]:
        """Run DuckDuckGo search and return structured results."""
        # Import lazily to avoid heavy dependencies at module load time.
        from tools.search import _collect_search_results

        results, used_query, _used_mode = _collect_search_results(
            query,
            log=lambda _msg: None,
            cancel_token=None,
        )
        out: List[SearchResult] = []
        for r in results[:max_results]:
            title = str(r.get("title") or "").strip()
            body = str(r.get("body") or "").strip()
            href = str(r.get("href") or "").strip()
            if not href:
                continue
            out.append(
                SearchResult(
                    title=title,
                    url=href,
                    snippet=body,
                )
            )
        return out
