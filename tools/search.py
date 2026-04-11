"""core/search.py

Online Search Tool using DuckDuckGo + Jina.ai Reader.
"""

import logging
import urllib.request
import ssl
import json
import re
from datetime import datetime
from pathlib import Path

from core.runtime_control import CancellationToken, OperationCancelled
# 1. Search Library
# Import lazily/fault-tolerantly so test harnesses can patch perform_search/DDGS
# without requiring the live DuckDuckGo backend in every environment.
_DDGS_IMPORT_ERROR = ""
try:
    from duckduckgo_search import DDGS
except ImportError:
    # Fallback for older versions just in case
    try:
        from ddgs import DDGS
    except ImportError:
        DDGS = None
        _DDGS_IMPORT_ERROR = "Please install: pip install duckduckgo-search"

from config import CFG, Config
# Daily facts removed

_LOG = logging.getLogger(__name__)

_NEWS_QUERY_HINT_RE = re.compile(r"(?i)\b(news|latest|current|recent|headline|headlines)\b")
_SEARCH_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:search(?:\s+the\s+web)?\s+for|look\s+up|look\s+for|find|locate)\s+"
)
_NEWS_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:(?:the\s+)?(?:latest|current|recent)\s+news|news)\s+(?:on|about|for)\s+"
)
_LEADING_RECENCY_RE = re.compile(r"(?i)^\s*(?:latest|current|recent)\s+")

# 2. The "Magic" Reader (Jina.ai)
def _raise_if_cancelled(cancel_token: CancellationToken | None) -> None:
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()


def fetch_clean_text(url, *, cancel_token: CancellationToken | None = None):
    try:
        _raise_if_cancelled(cancel_token)
        reader_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(reader_url, headers={'User-Agent': 'Mozilla/5.0'}) # Changed to standard browser UA
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        # Use configured timeout for news sites (from config.SEARCH_URL_FETCH_TIMEOUT_S)
        with urllib.request.urlopen(req, timeout=CFG.SEARCH_URL_FETCH_TIMEOUT_S, context=context) as resp:
            _raise_if_cancelled(cancel_token)
            data = resp.read().decode('utf-8', errors='ignore')

            # CHECK FOR PAYWALL/LOGIN WALLS
            # If the returned text is very short or contains certain keywords, it failed
            if len(data) < CFG.SEARCH_MIN_CONTENT_LENGTH:
                return "Error: Page content too short (likely blocked/empty)"

            return data
            
    except OperationCancelled:
        raise
    except Exception as e:
        return f"Error reading page: {e}"


def _query_looks_like_news(query: str) -> bool:
    return bool(_NEWS_QUERY_HINT_RE.search(str(query or "")))


def _normalize_search_query(raw_query: str) -> str:
    text = " ".join(str(raw_query or "").split()).strip(" .?!")
    if not text:
        return ""
    text = _SEARCH_PREFIX_RE.sub("", text, count=1)
    text = _NEWS_PREFIX_RE.sub("", text, count=1)
    text = _LEADING_RECENCY_RE.sub("", text, count=1)
    return " ".join(text.split()).strip(" .?!")


def _result_url(result: dict) -> str:
    return str(result.get("href") or result.get("url") or "").strip()


def _run_ddgs_query(
    *,
    query: str,
    mode: str,
    cancel_token: CancellationToken | None = None,
) -> list[dict]:
    _raise_if_cancelled(cancel_token)
    if DDGS is None:
        raise RuntimeError(_DDGS_IMPORT_ERROR or "DuckDuckGo search backend is unavailable.")
    with DDGS() as ddgs:
        if mode == "news":
            return list(ddgs.news(query, max_results=CFG.SEARCH_MAX_RESULTS))
        return list(ddgs.text(query, max_results=CFG.SEARCH_MAX_RESULTS))


def _collect_search_results(
    query: str,
    *,
    log,
    cancel_token: CancellationToken | None = None,
) -> tuple[list[dict], str, str]:
    original_query = " ".join(str(query or "").split()).strip()
    relaxed_query = _normalize_search_query(original_query)
    strategies: list[tuple[str, str]] = []
    if _query_looks_like_news(original_query):
        strategies.append(("news", original_query))
        if relaxed_query and relaxed_query.casefold() != original_query.casefold():
            strategies.append(("news", relaxed_query))
    strategies.append(("text", original_query))
    if relaxed_query and relaxed_query.casefold() != original_query.casefold():
        strategies.append(("text", relaxed_query))

    seen: set[tuple[str, str]] = set()
    last_error = ""
    for mode, attempt_query in strategies:
        normalized_attempt = " ".join(str(attempt_query or "").split()).strip()
        if not normalized_attempt:
            continue
        dedupe_key = (mode, normalized_attempt.casefold())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        log(f"Search attempt ({mode}): {normalized_attempt}")
        try:
            results = _run_ddgs_query(
                query=normalized_attempt,
                mode=mode,
                cancel_token=cancel_token,
            )
        except OperationCancelled:
            raise
        except Exception as exc:
            last_error = str(exc)
            log(f"Search attempt failed ({mode}): {exc}")
            continue
        _raise_if_cancelled(cancel_token)
        if results:
            if normalized_attempt.casefold() != original_query.casefold():
                log(f"Search fallback succeeded with relaxed query: {normalized_attempt}")
            else:
                log(f"Search results found via {mode}.")
            return results, normalized_attempt, mode
        log(f"No results via {mode} for: {normalized_attempt}")

    if last_error:
        raise RuntimeError(last_error)
    return [], "", ""

def perform_search(query: str, data_dir, log_callback=None, cancel_token: CancellationToken | None = None):
    # Helper to log to both Console and UI
    def log(msg):
        _LOG.info("%s", msg)
        if log_callback:
            log_callback(msg)
            
    # Helper to clean URLs for display
    def clean_url(u):
        return u.replace("https://", "").replace("http://", "").replace("www.", "").strip()

    log(f"Searching web for: {query}")
    
    results = []
    used_mode = "text"
    
    # 1. Search
    try:
        results, used_query, used_mode = _collect_search_results(
            query,
            log=log,
            cancel_token=cancel_token,
        )
        if not results: 
            log("Search Error: Zero results.")
            return "Search Error: Zero results."
    except OperationCancelled:
        raise
    except Exception as e:
        log(f"Search Error: {e}")
        return f"Search Error: {e}"

    # Filter Blacklist
    filtered = [r for r in results if not any(d in _result_url(r) for d in CFG.SEARCH_BLACKLIST)]
    if not filtered and results:
        filtered = list(results)
        log("All search results were blacklisted. Falling back to the unfiltered result set.")
    log(f"Collected {len(filtered)} candidate results via {used_mode} search.")
    
    # 2. Build Base Context (Snippets)
    output_parts = ["SEARCH SNIPPETS:"]
    for r in filtered[:CFG.SEARCH_SNIPPETS_LIMIT]:
        output_parts.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}")

    # 3. Greedy Deep Dive (Top 6 Links)
    output_parts.append("\n--- DEEP DIVE (Full Content) ---")
    log(f"Deep-diving up to {CFG.SEARCH_DEEP_DIVE_LINKS_LIMIT} links.")
    
    links_visited = 0

    for r in filtered:
        _raise_if_cancelled(cancel_token)
        if links_visited >= CFG.SEARCH_DEEP_DIVE_LINKS_LIMIT: break
        
        link = _result_url(r)
        if not link: continue
        
        log(clean_url(link))
        content = fetch_clean_text(link, cancel_token=cancel_token)
        
        # Check for errors
        if "Error reading page" in content:
            # We can keep errors silent or logged to console only to keep UI clean?
            # Let's log to console but not UI for cleaner activity window
            # log(f"Skipped: {clean_url(link)} (Error)")
            continue
        if len(content) < CFG.SEARCH_MIN_CONTENT_LENGTH:
            continue

        output_parts.append(f"\nSource: {link}\nContent: {content[:CFG.SEARCH_CONTENT_SLICE_LENGTH]}")
        links_visited += 1

    if links_visited == 0:
        if filtered:
            log("Search found results, but no deep-dive pages were readable. Returning snippet-only context.")
            output_parts.append("No readable full-content pages were available. Use the snippet evidence above only.")
            return "\n".join(output_parts)
        log("Error: Found results but could not read content from any link.")
        return "Search Error: Found results but could not read content from any link."

    return "\n".join(output_parts)
