"""core/search.py

Online Search Tool using DuckDuckGo + Jina.ai Reader.
"""

import logging
import urllib.request
import urllib.parse
import ssl
import json
import re
import html
import warnings
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from core.runtime_control import CancellationToken, OperationCancelled
from core.search_contracts import SEARCH_TOOL_ERROR_PREFIX
from core.search.backends.searxng import SearXNGBackend
# 1. Search Library
# Import lazily/fault-tolerantly so test harnesses can patch perform_search/DDGS
# without requiring the live DuckDuckGo backend in every environment.
_DDGS_IMPORT_ERROR = ""
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None
        _DDGS_IMPORT_ERROR = "Please install: pip install ddgs"

from config import CFG, Config
# Daily facts removed

_LOG = logging.getLogger(__name__)

_NEWS_QUERY_HINT_RE = re.compile(r"(?i)\b(news|latest|current|recent|headline|headlines)\b")
_DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_DDG_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
_DDG_HTML_MAX_BYTES = 1_000_000
_SEARCH_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:search(?:\s+the\s+web)?\s+for|look\s+up|look\s+for|find|locate)\s+"
)
_NEWS_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:(?:the\s+)?(?:latest|current|recent)\s+news|news)\s+(?:on|about|for)\s+"
)
_LEADING_RECENCY_RE = re.compile(r"(?i)^\s*(?:latest|current|recent)\s+")
_QUERY_TOKEN_RE = re.compile(r"(?i)\d+(?:\.\d+)+|[a-z][a-z0-9.+#-]*")
_VERSION_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)+$")
_RELEVANCE_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "current",
    "find",
    "for",
    "from",
    "headlines",
    "latest",
    "locate",
    "look",
    "news",
    "of",
    "on",
    "please",
    "recent",
    "search",
    "the",
    "up",
    "web",
    "with",
}
_BLOCKED_PAGE_HINTS = (
    "403 forbidden",
    "403: forbidden",
    "access denied",
    "are you a robot",
    "captcha",
    "checking your browser",
    "cloudflare ray id",
    "security verification",
    "verify you are human",
)
_VERSIONED_NEWS_CONTEXT_TERMS = (
    "alpha",
    "announced",
    "available",
    "beta",
    "bugfix",
    "bug fix",
    "candidate",
    "changelog",
    "cpython",
    "download",
    "feature",
    "features",
    "final",
    "free-threaded",
    "gil",
    "interpreter",
    "jit",
    "maintenance",
    "pep",
    "performance",
    "rc",
    "release",
    "release notes",
    "released",
    "security",
    "standard library",
    "typing",
    "update",
    "updates",
    "what's new",
    "whats new",
)

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


def _important_query_tokens(query: str) -> list[str]:
    normalized = _normalize_search_query(query)
    tokens: list[str] = []
    for match in _QUERY_TOKEN_RE.finditer(normalized):
        token = match.group(0).strip(".").casefold()
        if not token or token in _RELEVANCE_STOPWORDS:
            continue
        if len(token) < 3 and not _VERSION_TOKEN_RE.match(token):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _result_haystack(result: dict) -> str:
    parts = [
        str(result.get("title") or ""),
        str(result.get("body") or ""),
        str(result.get("href") or result.get("url") or ""),
    ]
    return " ".join(parts).casefold()


def _result_text_haystack(result: dict) -> str:
    return " ".join(
        [
            str(result.get("title") or ""),
            str(result.get("body") or ""),
        ]
    ).casefold()


def _token_occurrences(text: str, token: str) -> int:
    token_text = str(token or "").casefold()
    if not token_text:
        return 0
    if _VERSION_TOKEN_RE.match(token_text):
        pattern = rf"(?<!\d){re.escape(token_text)}(?!\d)"
    else:
        pattern = rf"(?<![a-z0-9]){re.escape(token_text)}(?![a-z0-9])"
    return len(re.findall(pattern, str(text or "").casefold()))


def _contains_versioned_news_context(text: str) -> bool:
    haystack = str(text or "").casefold()
    for term in _VERSIONED_NEWS_CONTEXT_TERMS:
        if " " in term or "'" in term:
            if term in haystack:
                return True
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        if re.search(pattern, haystack):
            return True
    return False


def _requires_versioned_news_evidence(query: str) -> bool:
    if not _query_looks_like_news(query):
        return False
    return any(_VERSION_TOKEN_RE.match(token) for token in _important_query_tokens(query))


def _result_matches_query(result: dict, query: str) -> bool:
    tokens = _important_query_tokens(query)
    if not tokens:
        return True
    haystack = _result_haystack(result)
    text_haystack = _result_text_haystack(result)
    version_tokens = [token for token in tokens if _VERSION_TOKEN_RE.match(token)]
    if version_tokens and not all(token in haystack for token in version_tokens):
        return False
    if _requires_versioned_news_evidence(query):
        if not all(token in text_haystack for token in version_tokens):
            return False
        if not _contains_versioned_news_context(text_haystack):
            return False
    lexical_tokens = [token for token in tokens if token not in version_tokens]
    if not lexical_tokens:
        return True
    match_count = sum(1 for token in lexical_tokens if token in haystack)
    required = 1 if version_tokens or len(lexical_tokens) <= 2 else 2
    return match_count >= required


def _filter_relevant_results(results: list[dict], query: str) -> list[dict]:
    return [result for result in results if _result_matches_query(result, query)]


def _result_dedupe_key(result: dict) -> tuple[str, str]:
    url = _result_url(result).casefold()
    if url:
        return ("url", url)
    fallback = " ".join(
        [
            str(result.get("title") or ""),
            str(result.get("body") or ""),
        ]
    ).casefold()
    return ("text", fallback)


def _clean_html_text(value: object) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def _unwrap_duckduckgo_html_href(href: object) -> str:
    raw = html.unescape(str(href or "").strip())
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif raw.startswith("/"):
        raw = urllib.parse.urljoin("https://duckduckgo.com", raw)

    parsed = urllib.parse.urlparse(raw)
    params = urllib.parse.parse_qs(parsed.query)
    for key in ("uddg", "u"):
        values = params.get(key) or []
        if values and values[0]:
            return html.unescape(values[0]).strip()
    return raw


class _DuckDuckGoHTMLResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._current: dict[str, str] | None = None
        self._capture_field = ""
        self._capture_tag = ""

    @staticmethod
    def _class_text(attrs: list[tuple[str, str | None]]) -> str:
        for key, value in attrs:
            if key == "class":
                return str(value or "")
        return ""

    @staticmethod
    def _attr_value(attrs: list[tuple[str, str | None]], name: str) -> str:
        for key, value in attrs:
            if key == name:
                return str(value or "")
        return ""

    def _finalize_current(self) -> None:
        if not self._current:
            return
        title = _clean_html_text(self._current.get("title", ""))
        body = _clean_html_text(self._current.get("body", ""))
        href = _unwrap_duckduckgo_html_href(self._current.get("href", ""))
        if title and href:
            self.results.append({"title": title, "body": body, "href": href})
        self._current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        class_text = self._class_text(attrs)
        if tag == "a" and ("result__a" in class_text or "result-link" in class_text):
            self._finalize_current()
            self._current = {"title": "", "body": "", "href": self._attr_value(attrs, "href")}
            self._capture_field = "title"
            self._capture_tag = tag
            return
        if self._current is not None and ("result__snippet" in class_text or "result-snippet" in class_text):
            self._capture_field = "body"
            self._capture_tag = tag

    def handle_data(self, data: str) -> None:
        if self._current is None or not self._capture_field:
            return
        existing = self._current.get(self._capture_field, "")
        separator = " " if existing and data else ""
        self._current[self._capture_field] = f"{existing}{separator}{data}"

    def handle_endtag(self, tag: str) -> None:
        if self._capture_field and tag == self._capture_tag:
            self._capture_field = ""
            self._capture_tag = ""

    def close(self) -> None:
        super().close()
        self._finalize_current()


def _looks_like_blocked_page(content: str) -> bool:
    sample = str(content or "")[:3000].casefold()
    return any(hint in sample for hint in _BLOCKED_PAGE_HINTS)


def _content_matches_query(content: str, query: str) -> bool:
    if not _requires_versioned_news_evidence(query):
        return True
    if not _result_matches_query({"title": "", "body": content, "url": ""}, query):
        return False

    tokens = _important_query_tokens(query)
    version_tokens = [token for token in tokens if _VERSION_TOKEN_RE.match(token)]
    lexical_tokens = [token for token in tokens if token not in version_tokens]

    # Versioned news pages that mention the target only once are often login,
    # nav, or recommendation pages with one relevant title and unrelated body.
    for token in version_tokens:
        if _token_occurrences(content, token) < 2:
            return False
    for token in lexical_tokens[:2]:
        if _token_occurrences(content, token) < 2:
            return False
    return True


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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        warnings.filterwarnings(
            "ignore",
            message=r"This package .* has been renamed to `ddgs`.*",
            category=RuntimeWarning,
        )
        try:
            ddgs_context = DDGS(headers=headers)
        except TypeError:
            ddgs_context = DDGS()
        with ddgs_context as ddgs:
            if mode == "news":
                return list(ddgs.news(query, max_results=CFG.SEARCH_MAX_RESULTS))
            return list(ddgs.text(query, max_results=CFG.SEARCH_MAX_RESULTS))


def _run_duckduckgo_html_query(
    *,
    query: str,
    cancel_token: CancellationToken | None = None,
) -> list[dict]:
    _raise_if_cancelled(cancel_token)
    encoded_query = urllib.parse.urlencode({"q": str(query or "").strip(), "kl": "us-en"})
    last_blocked = False
    last_error = ""
    for endpoint in (_DDG_HTML_ENDPOINT, _DDG_LITE_ENDPOINT):
        req = urllib.request.Request(
            f"{endpoint}?{encoded_query}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=CFG.SEARCH_URL_FETCH_TIMEOUT_S) as resp:
                _raise_if_cancelled(cancel_token)
                data = resp.read(_DDG_HTML_MAX_BYTES + 1).decode("utf-8", errors="ignore")
        except OperationCancelled:
            raise
        except Exception as exc:
            last_error = str(exc)
            continue
        _raise_if_cancelled(cancel_token)
        if _looks_like_blocked_page(data):
            last_blocked = True
            continue
        parser = _DuckDuckGoHTMLResultParser()
        parser.feed(data)
        parser.close()
        if parser.results:
            return parser.results[: CFG.SEARCH_MAX_RESULTS]
    if last_blocked:
        raise RuntimeError("DuckDuckGo HTML search was blocked before returning usable results.")
    if last_error:
        raise RuntimeError(last_error)
    return []


def _run_search_query(
    *,
    query: str,
    mode: str,
    cancel_token: CancellationToken | None = None,
) -> list[dict]:
    if mode == "html":
        return _run_duckduckgo_html_query(query=query, cancel_token=cancel_token)
    return _run_ddgs_query(query=query, mode=mode, cancel_token=cancel_token)


def _run_searxng_search(
    query: str,
    *,
    cancel_token: CancellationToken | None = None,
) -> list[dict]:
    _raise_if_cancelled(cancel_token)
    backend = SearXNGBackend(
        base_url=CFG.SEARXNG_URL,
        timeout_s=CFG.SEARXNG_TIMEOUT_S,
    )
    results = backend.search(query, max_results=CFG.SEARCH_MAX_RESULTS)
    return [
        {"title": r.title, "body": r.snippet, "href": r.url}
        for r in results
    ]


def _collect_search_results(
    query: str,
    *,
    log,
    cancel_token: CancellationToken | None = None,
) -> tuple[list[dict], str, str]:
    original_query = " ".join(str(query or "").split()).strip()

    # SearXNG path: single backend call, no multi-mode fallback
    if str(CFG.SEARCH_BACKEND or "").strip().lower() == "searxng":
        log(f"Search attempt (searxng): {original_query}")
        try:
            results = _run_searxng_search(query=original_query, cancel_token=cancel_token)
        except OperationCancelled:
            raise
        except Exception as exc:
            log(f"Search attempt failed (searxng): {exc}")
            raise
        _raise_if_cancelled(cancel_token)
        if results:
            relevant_results = _filter_relevant_results(results, original_query)
            if relevant_results:
                if len(relevant_results) != len(results):
                    log(f"Filtered {len(results) - len(relevant_results)} low-relevance search result(s).")
                return relevant_results[: CFG.SEARCH_MAX_RESULTS], original_query, "searxng"
        return [], "", ""

    relaxed_query = _normalize_search_query(original_query)
    strategies: list[tuple[str, str]] = []
    news_like_query = _query_looks_like_news(original_query)

    def add_strategy(mode: str, attempt_query: str) -> None:
        normalized = " ".join(str(attempt_query or "").split()).strip()
        if normalized:
            strategies.append((mode, normalized))

    def add_original_and_relaxed(mode: str) -> None:
        add_strategy(mode, original_query)
        if relaxed_query and relaxed_query.casefold() != original_query.casefold():
            add_strategy(mode, relaxed_query)

    if news_like_query:
        # DDGS' unofficial news endpoint is prone to 403 rate limits under
        # repeated local testing. The plain HTML result page is slower but more
        # resilient, so lead with it for recency/news queries and only touch
        # DDGS if HTML cannot produce relevant candidates.
        add_original_and_relaxed("html")
        add_original_and_relaxed("text")
        add_original_and_relaxed("news")
    else:
        add_original_and_relaxed("text")
        add_original_and_relaxed("html")

    seen_queries: set[tuple[str, str]] = set()
    seen_results: set[tuple[str, str]] = set()
    collected: list[dict] = []
    used_modes: list[str] = []
    used_queries: list[str] = []
    last_error = ""
    completed_attempt = False
    for mode, attempt_query in strategies:
        if mode == "html" and collected:
            continue
        normalized_attempt = " ".join(str(attempt_query or "").split()).strip()
        if not normalized_attempt:
            continue
        dedupe_key = (mode, normalized_attempt.casefold())
        if dedupe_key in seen_queries:
            continue
        seen_queries.add(dedupe_key)
        log(f"Search attempt ({mode}): {normalized_attempt}")
        try:
            results = _run_search_query(
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
        completed_attempt = True
        if results:
            relevant_results = _filter_relevant_results(results, normalized_attempt)
            if not relevant_results:
                log(f"Search results via {mode} did not match the core query terms for: {normalized_attempt}")
                continue
            if normalized_attempt.casefold() != original_query.casefold():
                log(f"Search fallback succeeded with relaxed query: {normalized_attempt}")
            else:
                log(f"Search results found via {mode}.")
            if len(relevant_results) != len(results):
                log(f"Filtered {len(results) - len(relevant_results)} low-relevance search result(s).")
            added = 0
            for result in relevant_results:
                result_key = _result_dedupe_key(result)
                if result_key in seen_results:
                    continue
                seen_results.add(result_key)
                collected.append(result)
                added += 1
                if len(collected) >= CFG.SEARCH_MAX_RESULTS:
                    break
            if added:
                if mode not in used_modes:
                    used_modes.append(mode)
                if normalized_attempt not in used_queries:
                    used_queries.append(normalized_attempt)
                log(f"Added {added} relevant result(s) from {mode}: {normalized_attempt}")
                if news_like_query and mode == "html":
                    break
            if len(collected) >= CFG.SEARCH_MAX_RESULTS:
                break
            continue
        log(f"No results via {mode} for: {normalized_attempt}")

    if collected:
        mode_label = "+".join(used_modes) if used_modes else "search"
        query_label = "; ".join(used_queries) if used_queries else original_query
        return collected, query_label, mode_label

    if last_error and not completed_attempt:
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
            log(f"{SEARCH_TOOL_ERROR_PREFIX} Zero results.")
            return f"{SEARCH_TOOL_ERROR_PREFIX} Zero results."
    except OperationCancelled:
        raise
    except Exception as e:
        log(f"{SEARCH_TOOL_ERROR_PREFIX} {e}")
        return f"{SEARCH_TOOL_ERROR_PREFIX} {e}"

    # Filter Blacklist
    filtered = [r for r in results if not any(d in _result_url(r) for d in CFG.SEARCH_BLACKLIST)]
    if not filtered and results:
        filtered = list(results)
        log("All search results were blacklisted. Falling back to the unfiltered result set.")
    log(f"Collected {len(filtered)} candidate results via {used_mode} search.")
    
    # 2. Build Base Context (Snippets)
    output_parts = [
        "SEARCH META:",
        f"Query used: {used_query}",
        f"Candidate results after filtering: {len(filtered)}",
        "SEARCH SNIPPETS:",
    ]
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
        if _looks_like_blocked_page(content):
            log(f"Skipped blocked page: {clean_url(link)}")
            continue
        if not _content_matches_query(content, used_query):
            log(f"Skipped low-relevance page content: {clean_url(link)}")
            continue
        if len(content) < CFG.SEARCH_MIN_CONTENT_LENGTH:
            continue

        output_parts.append(f"\nSource: {link}\nContent: {content[:CFG.SEARCH_CONTENT_SLICE_LENGTH]}")
        links_visited += 1

    output_parts.append(f"\nSOURCE COVERAGE: {links_visited} readable source(s) from {len(filtered)} candidate result(s).")

    if links_visited == 0:
        if filtered:
            log("Search found results, but no deep-dive pages were readable. Returning snippet-only context.")
            output_parts.append(
                "No readable full-content pages were available. "
                "Use the snippet evidence above only; do not infer article details beyond the title/snippet."
            )
            return "\n".join(output_parts)
        log("Error: Found results but could not read content from any link.")
        return f"{SEARCH_TOOL_ERROR_PREFIX} Found results but could not read content from any link."

    return "\n".join(output_parts)
