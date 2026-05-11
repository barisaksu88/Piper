"""Page fetcher for the grounded search pipeline."""

from __future__ import annotations

from typing import Optional

from core.runtime_control import CancellationToken
from core.search.contracts import FetchedSource


def fetch_source(
    result,
    *,
    cancel_token: CancellationToken | None = None,
    min_length: int = 100,
) -> FetchedSource:
    """Fetch and extract content for a single search result.

    Args:
        result: A dict-like search result with 'title', 'href'/'url', 'body'/'snippet'.
        cancel_token: Optional cancellation token.
        min_length: Minimum content length to consider readable.

    Returns:
        FetchedSource with status and extracted text.
    """
    title = str(result.get("title") or "").strip()
    url = str(result.get("href") or result.get("url") or "").strip()
    snippet = str(result.get("body") or result.get("snippet") or "").strip()

    if not url:
        return FetchedSource(
            url="",
            title=title,
            extracted_text="",
            status="error",
            error="No URL in search result",
        )

    try:
        from tools.search import fetch_clean_text

        text = fetch_clean_text(url, cancel_token=cancel_token)
    except Exception as exc:
        return FetchedSource(
            url=url,
            title=title,
            extracted_text="",
            status="error",
            error=str(exc),
        )

    # Detect blocked pages
    blocked_hints = (
        "403 forbidden",
        "access denied",
        "are you a robot",
        "captcha",
        "checking your browser",
        "cloudflare ray id",
        "security verification",
        "verify you are human",
    )
    lower_text = text.lower()
    if any(h in lower_text for h in blocked_hints):
        return FetchedSource(
            url=url,
            title=title,
            extracted_text=text,
            status="blocked",
            error="Page appears blocked or requires verification",
        )

    if text.startswith("Error:") or text.startswith("Error reading page"):
        return FetchedSource(
            url=url,
            title=title,
            extracted_text=text,
            status="error",
            error=text,
        )

    if len(text) < min_length:
        return FetchedSource(
            url=url,
            title=title,
            extracted_text=text,
            status="too_short",
            error=f"Content too short ({len(text)} chars)",
        )

    return FetchedSource(
        url=url,
        title=title,
        extracted_text=text,
        status="ok",
    )
