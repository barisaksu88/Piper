"""Search backends."""

from __future__ import annotations

from core.search.backends.base import SearchBackend
from core.search.backends.duckduckgo import DuckDuckGoBackend

__all__ = ["SearchBackend", "DuckDuckGoBackend"]
