#!/usr/bin/env python3
"""Route-level smokes for contextual search topic handoff."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.routing.route_normalizer import normalize_route_decision


@dataclass(frozen=True)
class SearchTopicRouteReport:
    success: bool
    bare_models_with_ai_context_ok: bool
    search_for_models_with_ai_context_ok: bool
    pronoun_online_with_ai_context_ok: bool
    conversational_refocus_with_ai_context_ok: bool
    conversational_refocus_chat_to_search_ok: bool
    bare_models_without_context_clarifies_ok: bool
    web_choice_after_models_clarification_ok: bool
    preview_tail_stripped_ok: bool


def _query(result: dict) -> str:
    return str(((result or {}).get("card") or {}).get("query") or "")


def _notice_kind(result: dict) -> str:
    return str(((result or {}).get("system_notice") or {}).get("kind") or "")


def _runtime_context(previous_request: str, search_query: str = "") -> dict[str, object]:
    return {
        "role": "system",
        "hidden": True,
        "content": (
            "[LATEST_RUNTIME_CONTEXT]\n"
            "Previous route: SEARCH\n"
            f"Previous user request: {previous_request}\n"
            f"Search query: {search_query}\n"
            "Execution status: SEARCH COMPLETED\n"
            "Runtime note: Search summary was prepared for the user.\n"
            "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
            "Prefer it over assistant narration when they conflict."
        ),
    }


def _legacy_runtime_context_without_route(previous_request: str, search_query: str = "") -> dict[str, object]:
    return {
        "role": "system",
        "hidden": True,
        "content": (
            "[LATEST_RUNTIME_CONTEXT]\n"
            f"Previous user request: {previous_request}\n"
            f"Search query: {search_query}\n"
            "Execution status: SEARCH COMPLETED\n"
            "Runtime note: Search summary was prepared for the user.\n"
            "Use this block as authoritative runtime context for follow-up routing and clarification handling. "
            "Prefer it over assistant narration when they conflict."
        ),
    }


def run_smoke() -> SearchTopicRouteReport:
    bare_models = normalize_route_decision(
        {"decision": "SEARCH", "card": {"query": "models"}, "source_scope": "web", "confidence": "high"},
        "search the models",
        [
            {"role": "user", "content": "Search online for recent developments in AI."},
            _runtime_context("Search online for recent developments in AI.", "AI developments"),
        ],
    )
    bare_models_with_ai_context_ok = _query(bare_models) == "AI models"

    search_for_models = normalize_route_decision(
        {
            "decision": "TASK",
            "card": {
                "goal": "Clarify lookup source (web vs workspace) for: models",
                "stages": [{"stage_goal": "Ask whether to search web or workspace.", "stage_type": "CHAT"}],
            },
        },
        "search for the models",
        [
            {"role": "user", "content": "Search online for recent developments in AI."},
            _legacy_runtime_context_without_route("Search online for recent developments in AI.", "recent developments in AI"),
        ],
    )
    search_for_models_with_ai_context_ok = _query(search_for_models) == "AI models"

    pronoun_online = normalize_route_decision(
        {"decision": "SEARCH", "card": {"query": "it please online"}, "source_scope": "web", "confidence": "high"},
        "Can you search for it please online?",
        [
            {"role": "user", "content": "You know how AI is always developing?"},
            {"role": "assistant", "content": "Yes. The field is maturing rapidly, especially around recent AI developments."},
            _runtime_context("You know how AI is always developing?", ""),
        ],
    )
    pronoun_online_with_ai_context_ok = _query(pronoun_online) == "AI developments"

    conversational_refocus = normalize_route_decision(
        {
            "decision": "SEARCH",
            "card": {"query": "actually i was asking more about models"},
            "source_scope": "web",
            "confidence": "high",
        },
        "actually i was asking more about models",
        [
            {"role": "user", "content": "Search online for recent developments in AI."},
            _runtime_context("Search online for recent developments in AI.", "recent developments in AI"),
        ],
    )
    conversational_refocus_with_ai_context_ok = _query(conversational_refocus) == "AI models"

    conversational_refocus_chat = normalize_route_decision(
        {"decision": "CHAT"},
        "actually i was asking more about models",
        [
            {"role": "user", "content": "Search online for recent developments in AI."},
            _runtime_context("Search online for recent developments in AI.", "recent developments in AI"),
        ],
    )
    conversational_refocus_chat_to_search_ok = _query(conversational_refocus_chat) == "AI models"

    bare_models_no_context = normalize_route_decision(
        {"decision": "SEARCH", "card": {"query": "models"}, "source_scope": "web", "confidence": "high"},
        "search the models",
        [],
    )
    bare_models_without_context_clarifies_ok = (
        str((bare_models_no_context or {}).get("decision") or "") == "CHAT"
        and _notice_kind(bare_models_no_context) == "search_clarification"
    )

    web_choice_after_models = normalize_route_decision(
        {"decision": "CHAT"},
        "web",
        [
            {"role": "user", "content": "Search online for recent developments in AI."},
            _legacy_runtime_context_without_route("Search online for recent developments in AI.", "recent developments in AI"),
            {"role": "user", "content": "search for the models"},
            {"role": "assistant", "content": 'Did you want me to search the web for "models", or look for it in your workspace files?'},
        ],
    )
    web_choice_after_models_clarification_ok = _query(web_choice_after_models) == "AI models"

    preview_tail = normalize_route_decision(
        {
            "decision": "SEARCH",
            "card": {
                "query": "Project Halcyon Lantern and tell me what you already know while it loads",
            },
            "source_scope": "web",
            "confidence": "high",
        },
        "Search the web for Project Halcyon Lantern and tell me what you already know while it loads.",
        [],
    )
    preview_tail_stripped_ok = _query(preview_tail) == "Project Halcyon Lantern"

    success = (
        bare_models_with_ai_context_ok
        and search_for_models_with_ai_context_ok
        and pronoun_online_with_ai_context_ok
        and conversational_refocus_with_ai_context_ok
        and conversational_refocus_chat_to_search_ok
        and bare_models_without_context_clarifies_ok
        and web_choice_after_models_clarification_ok
        and preview_tail_stripped_ok
    )
    return SearchTopicRouteReport(
        success=success,
        bare_models_with_ai_context_ok=bare_models_with_ai_context_ok,
        search_for_models_with_ai_context_ok=search_for_models_with_ai_context_ok,
        pronoun_online_with_ai_context_ok=pronoun_online_with_ai_context_ok,
        conversational_refocus_with_ai_context_ok=conversational_refocus_with_ai_context_ok,
        conversational_refocus_chat_to_search_ok=conversational_refocus_chat_to_search_ok,
        bare_models_without_context_clarifies_ok=bare_models_without_context_clarifies_ok,
        web_choice_after_models_clarification_ok=web_choice_after_models_clarification_ok,
        preview_tail_stripped_ok=preview_tail_stripped_ok,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify contextual search topic handoff before web search.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    args = parser.parse_args()

    report = run_smoke()
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"SUCCESS: {report.success}")
        for key, value in asdict(report).items():
            if key != "success":
                print(f"  {key}: {value}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
