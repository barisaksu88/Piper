"""SearXNG backend adapter for Piper search.

Calls a local (or remote) SearXNG instance via its JSON API.
Honest about failures — never fabricates results.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published_at: str = ""
    source: str = ""


class SearchBackend(Protocol):
    """Protocol for pluggable search backends."""

    name: str

    def search(self, query: str, *, max_results: int = 8) -> list[SearchResult]:
        ...


class SearXNGBackend:
    """SearXNG JSON API backend.

    Expects a SearXNG instance reachable at *base_url*.
    Default ``http://127.0.0.1:8888`` matches a typical local install.
    """

    name = "searxng"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8888",
        timeout_s: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def search(self, query: str, *, max_results: int = 8) -> list[SearchResult]:
        if not query or not query.strip():
            return []

        params = urllib.parse.urlencode({
            "q": query.strip(),
            "format": "json",
        })
        url = f"{self.base_url}/search?{params}"

        _LOG.debug("SearXNG query: %s", url)

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s, context=context) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            _LOG.warning("SearXNG HTTP error %s: %s", exc.code, exc.reason)
            raise RuntimeError(f"SearXNG HTTP error {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            _LOG.warning("SearXNG connection error: %s", exc.reason)
            raise RuntimeError(f"SearXNG unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            _LOG.warning("SearXNG timeout after %.1fs", self.timeout_s)
            raise RuntimeError(f"SearXNG timeout after {self.timeout_s}s") from exc
        except Exception as exc:
            _LOG.warning("SearXNG unexpected error: %s", exc)
            raise RuntimeError(f"SearXNG error: {exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            _LOG.warning("SearXNG returned invalid JSON: %s", exc)
            raise RuntimeError(f"SearXNG returned invalid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            _LOG.warning("SearXNG JSON payload is not a dict")
            return []

        results = payload.get("results")
        if not isinstance(results, list):
            # Some SearXNG instances wrap results differently; be tolerant
            _LOG.debug("SearXNG 'results' key missing or not a list")
            return []

        parsed: list[SearchResult] = []
        for idx, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or item.get("href") or "").strip()
            if not title or not url:
                # Skip malformed entries
                continue
            snippet = str(item.get("content") or item.get("snippet") or item.get("body") or "").strip()
            published = str(item.get("publishedDate") or item.get("published") or "").strip()
            source = str(item.get("engine") or item.get("source") or "").strip()
            parsed.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    published_at=published,
                    source=source,
                )
            )
            if len(parsed) >= max_results:
                break

        return parsed
