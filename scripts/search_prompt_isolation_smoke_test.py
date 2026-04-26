from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator_phases import (  # noqa: E402
    _build_search_first_pass_fallback,
    _build_search_first_pass_rule,
    _build_search_preview_history,
    _build_search_report_history,
)
from core.prompting import build_persona_messages  # noqa: E402


def main() -> int:
    preview_history = _build_search_preview_history("search for the latest nvidia news", "latest nvidia news")
    report_history = _build_search_report_history(
        [
            {
                "role": "system",
                "content": "[SEARCH SUMMARY FOR 'latest news on llama.cpp performance benchmarks']\nOld summary",
                "hidden": True,
            },
            {
                "role": "system",
                "content": "[SEARCH REPORT CONSUMED FOR 'latest news on llama.cpp performance benchmarks']",
                "hidden": True,
            },
            {
                "role": "assistant",
                "content": "I have searched for the latest news regarding llama.cpp performance benchmarks.",
            },
            {"role": "user", "content": "search for the latest nvidia news"},
            {
                "role": "system",
                "content": "[SEARCH SUMMARY FOR 'latest nvidia news']\nFresh Nvidia summary",
                "hidden": True,
            },
        ],
        user_msg="search for the latest nvidia news",
    )

    final_messages = build_persona_messages(
        system_content="BASE_SYSTEM",
        history=report_history,
        tail_system_content="[SEARCH_REPORT_RULE]\nUse the completed search summary.",
        model_path="Qwen3.5-9B-Q6_K.gguf",
    )

    preview_messages = build_persona_messages(
        system_content="BASE_SYSTEM",
        history=preview_history,
        tail_system_content=_build_search_first_pass_rule("latest nvidia news"),
        model_path="Qwen3.5-9B-Q6_K.gguf",
    )

    final_system = str(final_messages[0].get("content") or "") if final_messages else ""
    preview_system = str(preview_messages[0].get("content") or "") if preview_messages else ""
    preview_user = str(preview_messages[1].get("content") or "") if len(preview_messages) > 1 else ""
    fallback_text = _build_search_first_pass_fallback("search the web for latest Python 3.13 news")

    success = (
        len(preview_history) == 1
        and preview_history[0].get("role") == "user"
        and preview_history[0].get("content") == "search for the latest nvidia news"
        and len(report_history) == 2
        and str(report_history[0].get("content") or "").startswith("[SEARCH SUMMARY FOR 'latest nvidia news']")
        and "[SEARCH SUMMARY FOR 'latest nvidia news']" in final_system
        and "[SEARCH SUMMARY FOR 'latest news on llama.cpp performance benchmarks']" not in final_system
        and "[SEARCH REPORT CONSUMED FOR 'latest news on llama.cpp performance benchmarks']" not in final_system
        and "Background search complete for 'latest news on llama.cpp performance benchmarks'" not in final_system
        and "I have searched for the latest news regarding llama.cpp performance benchmarks." not in final_system
        and "[SEARCH_FIRST_PASS_RULE]" in preview_system
        and fallback_text == (
            'I\'m checking the web for "latest Python 3.13 news" now. '
            "I'll bring the results back automatically in a moment."
        )
        and "The query is recency-sensitive" in preview_system
        and "Do not state current/live facts" in preview_system
        and "[SEARCH SUMMARY FOR 'latest news on llama.cpp performance benchmarks']" not in preview_system
        and preview_user == "search for the latest nvidia news"
    )

    print(
        json.dumps(
            {
                "success": bool(success),
                "preview_history": preview_history,
                "report_history": report_history,
                "preview_system": preview_system,
                "final_system_excerpt": final_system[:1200],
                "fallback_text": fallback_text,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
