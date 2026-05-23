from __future__ import annotations

import re
from typing import Any

from core.search_contracts import normalize_search_error

_SEARCH_RECENCY_HINT_RE = re.compile(
    r"(?i)\b(latest|current|recent|news|headline|headlines|today|this week|this month)\b"
)


class SearchWorkflowEngine:
    """Pure helper/service methods for the search workflow lifecycle.

    This module is a **direct-call utility** in Stage 03. It contains no
    registry hooks, no LLM calls, no threading, no I/O, and no in-flight
    state management.  In-flight state remains owned by the orchestrator
    and controller boundaries.
    """

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
        current_user = str(user_msg or query or "").strip()
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
        for message in reversed(list(history or [])):
            if str(message.get("role") or "").strip().lower() != "system":
                continue
            content = str(message.get("content") or "").strip()
            if content.startswith("[SEARCH SUMMARY FOR "):
                latest_summary = {"role": "system", "content": content}
                break
        if latest_summary is not None:
            filtered.append(latest_summary)
        filtered.extend(self.build_search_preview_history(user_msg, user_msg))
        return filtered
