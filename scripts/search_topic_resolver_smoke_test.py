#!/usr/bin/env python3
"""Deterministic smoke tests for SearchTopicResolver.

Usage:
    python scripts/search_topic_resolver_smoke_test.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.search.topic_resolver import resolve_search_topic, SearchTopicResolution


def _ok(name: str) -> dict:
    return {"name": name, "status": "PASS"}


def _fail(name: str, reason: str) -> dict:
    return {"name": name, "status": "FAIL", "reason": reason}


def run_tests() -> list[dict]:
    results: list[dict] = []

    # ── 1. Explicit search query extraction ────────────────────────────────
    res = resolve_search_topic(
        "Search online for Python 3.14 release date",
        [],
    )
    if res.query == "Python 3.14 release date" and res.confidence == "high":
        results.append(_ok("explicit_search_query_extraction"))
    else:
        results.append(_fail("explicit_search_query_extraction", f"got query={res.query!r}, confidence={res.confidence}"))

    # ── 2. Pronoun follow-up resolution ────────────────────────────────────
    res = resolve_search_topic(
        "Can you search for it please online?",
        [{"role": "user", "content": "Tell me about quantum computing"}],
        previous_user_request="Tell me about quantum computing",
    )
    if res.query == "quantum computing":
        results.append(_ok("pronoun_followup_resolution"))
    else:
        results.append(_fail("pronoun_followup_resolution", f"got query={res.query!r} reason={res.reason}"))

    # ── 3. Correction handling ─────────────────────────────────────────────
    res = resolve_search_topic(
        "No, I meant Python 3.13",
        [],
    )
    if res.query == "Python 3.13" and res.confidence == "high":
        results.append(_ok("correction_handling"))
    else:
        results.append(_fail("correction_handling", f"got query={res.query!r}"))

    # ── 4. Generic + context merge ─────────────────────────────────────────
    res = resolve_search_topic(
        "search for recent models",
        [{"role": "user", "content": "What do you know about AI?"}],
        previous_user_request="What do you know about AI?",
    )
    # Should merge "recent" with "AI" -> "recent AI"
    if "recent" in res.query and "AI" in res.query:
        results.append(_ok("generic_context_merge"))
    else:
        results.append(_fail("generic_context_merge", f"got query={res.query!r} reason={res.reason}"))

    # ── 5. Ambiguous pronoun → clarification ───────────────────────────────
    res = resolve_search_topic(
        "Can you look it up?",
        [],
        previous_user_request="",
        last_search_query="",
    )
    if res.needs_clarification and res.clarification_question:
        results.append(_ok("ambiguous_pronoun_clarification"))
    else:
        results.append(_fail("ambiguous_pronoun_clarification", f"needs_clarification={res.needs_clarification}, question={res.clarification_question!r}"))

    # ── 6. Stale greeting guard ────────────────────────────────────────────
    res = resolve_search_topic(
        "Thanks!",
        [],
    )
    if res.needs_clarification and res.reason == "stale_greeting":
        results.append(_ok("stale_greeting_guard"))
    else:
        results.append(_fail("stale_greeting_guard", f"needs_clarification={res.needs_clarification}, reason={res.reason}"))

    # ── 7. Last search query fallback ──────────────────────────────────────
    res = resolve_search_topic(
        "Search for it",
        [],
        last_search_query="climate change effects",
    )
    if res.query == "climate change effects":
        results.append(_ok("last_search_query_fallback"))
    else:
        results.append(_fail("last_search_query_fallback", f"got query={res.query!r} reason={res.reason}"))

    # ── 8. Empty input → clarification ─────────────────────────────────────
    res = resolve_search_topic("", [])
    if res.needs_clarification:
        results.append(_ok("empty_input_clarification"))
    else:
        results.append(_fail("empty_input_clarification", f"needs_clarification={res.needs_clarification}"))

    # ── 9. Trailing filler stripped ────────────────────────────────────────
    res = resolve_search_topic(
        "look up Rust async runtime please if you can",
        [],
    )
    if res.query == "Rust async runtime":
        results.append(_ok("trailing_filler_stripped"))
    else:
        results.append(_fail("trailing_filler_stripped", f"got query={res.query!r}"))

    # ── 10. Pronoun resolved from history ──────────────────────────────────
    res = resolve_search_topic(
        "Find that for me",
        [
            {"role": "user", "content": "What is the capital of Mongolia?"},
            {"role": "assistant", "content": "The capital is Ulaanbaatar."},
        ],
    )
    if res.query == "the capital of Mongolia":
        results.append(_ok("pronoun_resolved_from_history"))
    else:
        results.append(_fail("pronoun_resolved_from_history", f"got query={res.query!r} reason={res.reason}"))

    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = run_tests()
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")

    if args.json:
        print(json.dumps({"passed": passed, "failed": failed, "tests": results}, indent=2))
    else:
        for r in results:
            mark = "✓" if r["status"] == "PASS" else "✗"
            print(f"{mark} {r['name']}: {r['status']}")
            if r["status"] == "FAIL":
                print(f"    reason: {r['reason']}")
        print(f"\nResults: {passed}/{len(results)} passed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
