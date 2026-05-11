"""Base search backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from core.search.contracts import SearchResult


class SearchBackend(ABC):
    """Pluggable search backend."""

    name: str = "abstract"

    @abstractmethod
    def search(self, query: str, *, max_results: int = 8) -> List[SearchResult]:
        """Execute a search query and return structured results.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.

        Returns:
            List of SearchResult objects.
        """
        ...
