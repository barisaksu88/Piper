"""Build a grounded answer from extracted evidence."""

from __future__ import annotations

import re
from urllib.parse import urlparse
from typing import List

from core.search.contracts import SearchAnswerEvidence, SourcePassage

_RECENCY_QUERY_RE = re.compile(r"(?i)\b(latest|current|recent|news|developments|updates|today|this week|this month)\b")
_LOW_SIGNAL_DOMAIN_HINTS = (
    "crazygames.",
    "poki.",
    "funnygames.",
    "y8.com",
    "miniclip.",
    "roblox.",
    "kongregate.",
)
_AUTHORITATIVE_DOMAIN_HINTS = (
    ".gov",
    ".edu",
    "wikipedia.org",
    "github.com",
    "docs.python.org",
    "developer.mozilla.org",
    "rfc-editor.org",
    "ietf.org",
    "arxiv.org",
    "pubmed",
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "nytimes.com",
    "technologyreview.com",
    "techcrunch.com",
    "pcmag.com",
    "sciencedaily.com",
    "hai.stanford.edu",
)


def _domain_kind(url: str) -> str:
    host = (urlparse(str(url or "")).netloc or "").lower()
    if any(hint in host for hint in _LOW_SIGNAL_DOMAIN_HINTS):
        return "low_signal"
    if any(hint in host for hint in _AUTHORITATIVE_DOMAIN_HINTS):
        return "authoritative"
    return "neutral"


def _normalized_passage_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _unique_sources(passages: list[SourcePassage]) -> list[SourcePassage]:
    seen: set[str] = set()
    unique: list[SourcePassage] = []
    for passage in passages:
        url = str(passage.source_url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(passage)
    return unique


def build_answer(evidence: SearchAnswerEvidence) -> SearchAnswerEvidence:
    """Populate evidence.answer_text and evidence.verdict from chosen sources.

    Rules:
    - If the top chosen passage has a strong relevance score (>= 1.0), verdict is "verified".
    - If there are some relevant passages but none are strong, verdict is "partial".
    - If no passages were found, verdict is "not_verified" and answer says so.
    """
    passages = evidence.chosen_sources
    if not passages:
        evidence.verdict = "not_verified"
        evidence.answer_text = (
            "The search did not return sufficient evidence to answer this query."
        )
        return evidence

    top_score = passages[0].relevance_score if passages else 0.0
    unique_sources = _unique_sources(passages)
    authoritative_count = sum(1 for item in unique_sources if _domain_kind(item.source_url) == "authoritative")
    low_signal_count = sum(1 for item in unique_sources if _domain_kind(item.source_url) == "low_signal")
    recency_query = bool(_RECENCY_QUERY_RE.search(str(evidence.query or "")))

    if top_score >= 1.0 and (authoritative_count >= 1 or len(unique_sources) >= 2):
        evidence.verdict = "verified"
    elif top_score >= 0.3:
        evidence.verdict = "partial"
    else:
        evidence.verdict = "not_verified"

    if recency_query and authoritative_count == 0:
        evidence.verdict = "partial" if len(unique_sources) >= 2 else "not_verified"
        if low_signal_count >= 1 and len(unique_sources) < 2:
            evidence.verdict = "not_verified"

    lines: List[str] = []
    if evidence.verdict == "verified":
        if recency_query and authoritative_count == 0:
            lines.append("I found multiple relevant results, but the source quality is mixed.")
        else:
            lines.append("I found solid supporting evidence.")
    elif evidence.verdict == "partial":
        if recency_query and authoritative_count == 0:
            lines.append("I found some relevant coverage, but nothing strong enough to treat as a solid roundup.")
        else:
            lines.append("I found some supporting evidence, but it is incomplete.")
    else:
        lines.append("I could not verify a useful answer from the results I found.")

    lines.append("")
    lines.append("Supporting evidence:")
    seen_passages: set[str] = set()
    bullet_count = 0
    for passage in passages:
        normalized = _normalized_passage_text(passage.text)
        if not normalized or normalized in seen_passages:
            continue
        seen_passages.add(normalized)
        bullet_count += 1
        lines.append(f"{bullet_count}. {passage.text}")
        lines.append(f"   — {passage.source_title}")
        if bullet_count >= 4:
            break

    evidence.answer_text = "\n".join(lines)
    return evidence
