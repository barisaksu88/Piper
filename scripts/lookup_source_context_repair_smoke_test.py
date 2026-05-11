from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.routing.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class LookupSourceContextRepairReport:
    success: bool
    decision: str
    query: str


def run_smoke() -> LookupSourceContextRepairReport:
    decision = {
        "decision": "TASK",
        "card": {
            "goal": "Clarify lookup source (web vs workspace) for: I'm the latest deep-seek version",
            "context": [],
            "stages": [
                {
                    "stage_goal": 'Ask the user: Did you want me to search the web for "I\'m the latest deep-seek version", or look for it in your workspace files?',
                    "stage_type": "CHAT",
                    "success_condition": "A concise source-clarification question is ready for the user.",
                    "allowed_tools": [],
                }
            ],
        },
    }
    recent_history = [
        {"role": "user", "content": "What's the latest deep-seek version?"},
        {"role": "assistant", "content": "Do you want a direct answer or should I verify it online?"},
        {"role": "user", "content": "I'm the latest deep-seek version."},
        {"role": "assistant", "content": 'Did you want me to search the web for "I\'m the latest deep-seek version", or look for it in your workspace files?'},
    ]
    result = normalize_route_decision(decision, "web", recent_history)
    query = str(((result.get("card") or {}).get("query") or "")).strip()
    success = (
        str(result.get("decision") or "").strip().upper() == "SEARCH"
        and query.lower() == "what's the latest deep-seek version".lower()
    )
    return LookupSourceContextRepairReport(
        success=bool(success),
        decision=str(result.get("decision") or ""),
        query=query,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify web/workspace clarification follow-ups recover the topical search subject from prior conversation context when the immediate subject is a bad STT assertion."
    )


def main() -> int:
    build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
