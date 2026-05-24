from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.search_contracts import (
    is_background_search_payload,
    is_search_reporter_instruction,
    normalize_search_error,
    parse_background_search_content,
)

_SEARCH_RECENCY_HINT_RE = re.compile(
    r"(?i)\b(latest|current|recent|news|headline|headlines|today|this week|this month)\b"
)

_SEARCH_PREVIEW_SOURCE_CHOICE_WORDS = {
    "actually",
    "do",
    "internet",
    "instead",
    "it",
    "online",
    "please",
    "pls",
    "search",
    "that",
    "the",
    "web",
    "yeah",
    "yep",
    "yes",
}

_SEARCH_PREVIEW_SOURCE_WORDS = {"internet", "online", "web"}

_SEARCH_SUMMARY_QUERY_RE = re.compile(
    r"(?is)^\[SEARCH SUMMARY FOR ['\"](?P<query>.+?)['\"]\]"
)


@dataclass(frozen=True)
class SearchReporterContext:
    """Immutable result of parsing the reporter turn from recent history."""

    raw_content: str = ""
    instruction_content: str = ""
    query: str = "Unknown Query"
    data: str = ""
    failed: bool = False
    normalized_error: str = ""


@dataclass(frozen=True)
class SearchPreviewContext:
    """Immutable inputs for the search first-pass preview turn."""

    query: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    first_pass_rule: str = ""
    fallback_text: str = ""
    recency_sensitive: bool = False


class SearchWorkflowEngine:
    """Pure helper/service methods for the search workflow lifecycle.

    This module is a **direct-call utility**. It contains no registry hooks,
    no LLM calls, no threading, no I/O, and no in-flight state management.
    In-flight state remains owned by the orchestrator and controller boundaries.
    """

    # ------------------------------------------------------------------
    # Reporter context
    # ------------------------------------------------------------------

    def prepare_reporter_context(
        self,
        recent_history: list[dict] | tuple[dict, ...] | None,
    ) -> SearchReporterContext:
        """Scan recent history for the latest search payload and reporter instruction.

        Mirrors the exact logic formerly inline in ``phase_reporter``:
        - Walk reversed recent_history for the latest system message matching
          ``is_background_search_payload``.
        - Walk reversed recent_history for the latest system message matching
          ``is_search_reporter_instruction``.
        - Parse the payload with ``parse_background_search_content``.
        - Return an immutable ``SearchReporterContext``.
        """
        raw_content = ""
        instruction_content = ""

        for message in reversed(list(recent_history or [])):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "system":
                if is_background_search_payload(message.get("content", "")):
                    raw_content = str(message.get("content", ""))
                    break

        for message in reversed(list(recent_history or [])):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "system":
                if is_search_reporter_instruction(message.get("content", "")):
                    instruction_content = str(message.get("content", ""))
                    break

        payload = parse_background_search_content(raw_content)
        search_failed = bool(payload.failed)
        return SearchReporterContext(
            raw_content=raw_content,
            instruction_content=instruction_content,
            query=payload.query,
            data=payload.data,
            failed=search_failed,
            normalized_error=normalize_search_error(payload.data) if search_failed else "",
        )

    # ------------------------------------------------------------------
    # Preview context
    # ------------------------------------------------------------------

    def prepare_preview_context(
        self,
        *,
        user_msg: str,
        query: str,
    ) -> SearchPreviewContext:
        """Build immutable preview inputs for ``phase_search``.

        Uses existing engine helpers so ``phase_search`` only calls
        one method instead of four separate helpers.
        """
        clean_query = str(query or "").strip()
        return SearchPreviewContext(
            query=clean_query,
            history=self.build_search_preview_history(user_msg, query),
            first_pass_rule=self.build_search_first_pass_rule(query),
            fallback_text=self.build_search_first_pass_fallback(query),
            recency_sensitive=bool(_SEARCH_RECENCY_HINT_RE.search(str(query or ""))),
        )

    # ------------------------------------------------------------------
    # Failure / error helpers
    # ------------------------------------------------------------------

    def build_search_failure_summary(self, query: str, error_text: str) -> str:
        clean_error = normalize_search_error(error_text) or "The search backend failed before returning usable results."
        clean_query = str(query or "Unknown Query").strip() or "Unknown Query"
        return "\n".join(
            [
                "The web search failed before usable results were retrieved.",
                f"- Query: {clean_query}",
                f"- Error: {clean_error}",
                "- Verified web findings: none.",
            ]
        )

    def summarize_search_error_for_user(self, error_text: str) -> str:
        clean_error = normalize_search_error(error_text) or "the search backend failed"
        lower = clean_error.casefold()
        if "zero results" in lower:
            return "the search provider returned zero usable results"
        if "403" in lower and "ratelimit" in lower:
            return "the search provider returned HTTP 403 Ratelimit"
        if "403" in lower:
            return "the search provider returned HTTP 403"
        if "rate" in lower and "limit" in lower:
            return "the search provider rate-limited the request"
        return clean_error

    # ------------------------------------------------------------------
    # In-flight / collision helpers
    # ------------------------------------------------------------------

    def build_search_in_flight_reply(self, active_query: str, requested_query: str) -> str:
        active = str(active_query or "").strip()
        requested = str(requested_query or "").strip()
        if active and requested and active.casefold() != requested.casefold():
            return (
                f'I already have a web search running for "{active}". '
                f'Let that finish first, then ask again about "{requested}" and I will take it next.'
            )
        if active:
            return (
                f'I already have a web search running for "{active}". '
                "Let that finish first, then ask again if you want me to continue from there."
            )
        if requested:
            return (
                "I already have a web search running right now. "
                f'Let that finish first, then ask again about "{requested}" and I will take it next.'
            )
        return "I already have a web search running right now. Let that finish first, then ask again and I will take the next search."

    # ------------------------------------------------------------------
    # First-pass preview helpers
    # ------------------------------------------------------------------

    def build_search_first_pass_rule(self, query: str) -> str:
        clean_query = str(query or "").strip()
        lines = [
            "[SEARCH_FIRST_PASS_RULE]",
            "A background web search is already running for the user's latest request.",
        ]
        if clean_query:
            lines.append(f"Search query: {clean_query}")
        lines.extend(
            [
                "While it runs, engage with the topic using the current system context and your existing knowledge only.",
                "Give a useful first-pass response: relevant context, a best-effort answer, or one focused follow-up question if that would materially help.",
                "The runtime will automatically deliver the completed search results on this same turn as soon as the search finishes.",
                "Do not ask whether to proceed, whether the user wants the results, or whether you should continue once the search completes.",
                "Do not tell the user to wait, reply, or confirm before the search finishes.",
                "If you ask a question, it must clarify the search topic itself, not permission to continue the search.",
                "Stay tightly on the search topic. Do not riff on unrelated profile facts, tasks, events, memories, or document excerpts.",
                "Ignore any personal or workspace context unless it is directly relevant to the search query itself.",
                "If the query asks for externally verifiable facts, results, status, rankings, dates, specs, or claims not supplied by current system context, frame uncertainty plainly and defer specifics until the web results arrive.",
                "Do not speculate that the web findings are empty, quiet, lacking breakthroughs, or already leaning one way unless the current system context explicitly says so.",
                "Do not present your existing knowledge as if it came from the live web search.",
                "Make it clear the web findings will follow shortly.",
                "Do not emit control tags such as [ROUTER] or [RECALL].",
            ]
        )
        if _SEARCH_RECENCY_HINT_RE.search(clean_query):
            lines.extend(
                [
                    "The query is recency-sensitive. Do not state current/live facts, release status, dates, version status, rankings, prices, or 'latest news' claims from memory.",
                    "For recency-sensitive searches, keep the first-pass response brief: say what you are checking and defer factual claims until the web results arrive.",
                    "Do not say a version is already out, current, upcoming, quiet, settled, or lacking news unless supplied by explicit current system evidence.",
                ]
            )
        return "\n".join(lines)

    def build_search_first_pass_fallback(self, query: str) -> str:
        clean_query = str(query or "").strip()
        clean_query = re.sub(
            r"(?i)^\s*(?:please\s+)?(?:search(?:\s+the\s+web)?\s+for|look\s+up|look\s+for|find|locate)\s+",
            "",
            clean_query,
            count=1,
        ).strip(" .?!")
        if clean_query:
            return f'I\'m checking the web for "{clean_query}" now. I\'ll bring the results back automatically in a moment.'
        return "I'm checking the web for that now. I'll bring the results back automatically in a moment."

    def build_search_preview_history(self, user_msg: str, query: str) -> list[dict[str, str]]:
        raw_user = str(user_msg or "").strip()
        clean_query = str(query or "").strip()
        current_user = raw_user or clean_query
        if clean_query and raw_user and raw_user.casefold() != clean_query.casefold():
            words = set(re.findall(r"[a-z]+", raw_user.casefold()))
            source_choice_only = bool(words & _SEARCH_PREVIEW_SOURCE_WORDS) and words.issubset(
                _SEARCH_PREVIEW_SOURCE_CHOICE_WORDS
            )
            if source_choice_only:
                current_user = f"Search the web for {clean_query}."
        if not current_user:
            return []
        return [{"role": "user", "content": current_user}]

    def build_search_report_history(
        self,
        history: list[dict] | tuple[dict, ...] | None,
        *,
        user_msg: str,
    ) -> list[dict[str, str]]:
        filtered: list[dict[str, str]] = []
        latest_summary = None
        latest_summary_query = ""
        for message in reversed(list(history or [])):
            if str(message.get("role") or "").strip().lower() != "system":
                continue
            content = str(message.get("content") or "").strip()
            if content.startswith("[SEARCH SUMMARY FOR "):
                latest_summary = {"role": "system", "content": content}
                match = _SEARCH_SUMMARY_QUERY_RE.match(content)
                latest_summary_query = str(match.group("query") if match else "").strip()
                break
        if latest_summary is not None:
            filtered.append(latest_summary)
        filtered.extend(self.build_search_preview_history(user_msg, latest_summary_query or user_msg))
        return filtered
