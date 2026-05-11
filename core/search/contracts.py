"""Data contracts for the grounded search v1 pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SearchResult:
    """A single result from a search backend."""

    title: str
    url: str
    snippet: str
    date: Optional[str] = None


@dataclass(frozen=True)
class FetchedSource:
    """A page that was fetched and had its content extracted."""

    url: str
    title: str
    extracted_text: str
    status: str = "ok"  # "ok", "blocked", "timeout", "error", "too_short", "irrelevant"
    error: Optional[str] = None


@dataclass(frozen=True)
class SourcePassage:
    """A supporting passage chosen from a fetched source."""

    text: str
    source_url: str
    source_title: str
    relevance_score: float = 0.0


@dataclass
class SearchAnswerEvidence:
    """Structured output of the grounded search pipeline."""

    query: str
    query_variants: list[str] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    fetched_sources: list[FetchedSource] = field(default_factory=list)
    chosen_sources: list[SourcePassage] = field(default_factory=list)
    verdict: str = "not_verified"  # "verified", "partial", "not_verified"
    answer_text: str = ""

    def to_reporter_string(self) -> str:
        """Format as structured input for the reporter/persona layer."""
        lines: list[str] = [
            "SEARCH META:",
            f"Original query: {self.query}",
        ]
        if self.query_variants:
            lines.append(f"Query variants: {', '.join(self.query_variants)}")
        lines.append(f"Candidate results: {len(self.results)}")
        readable = [s for s in self.fetched_sources if s.status == "ok"]
        lines.append(f"Sources fetched: {len(self.fetched_sources)}")
        lines.append(f"Sources readable: {len(readable)}")

        lines.append("")
        lines.append("SEARCH RESULTS:")
        for i, r in enumerate(self.results[:5], 1):
            date_part = f" | Date: {r.date}" if r.date else ""
            lines.append(f"{i}. Title: {r.title} | URL: {r.url}{date_part}")
            lines.append(f"   Snippet: {r.snippet}")

        lines.append("")
        lines.append("GROUNDED EVIDENCE:")
        if self.chosen_sources:
            for p in self.chosen_sources:
                lines.append(f"[Source: {p.source_title} ({p.source_url})]")
                lines.append(f"Passage: {p.text}")
                lines.append("")
        else:
            lines.append("No supporting passages could be extracted from fetched sources.")

        lines.append("")
        lines.append(f"VERDICT: {self.verdict.upper()}")
        lines.append("")
        lines.append("ANSWER:")
        if self.answer_text:
            lines.append(self.answer_text)
        else:
            lines.append("The search did not return sufficient evidence to answer the query.")

        return "\n".join(lines)
