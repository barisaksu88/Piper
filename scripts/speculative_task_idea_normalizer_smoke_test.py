from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class SpeculativeTaskIdeaNormalizerReport:
    success: bool
    speculative_decision: str
    explicit_decision: str
    explicit_goal: str


def _base_code_decision() -> dict:
    return {
        "decision": "TASK",
        "card": {
            "goal": "Design a fuzzy string matching algorithm capable of handling phonetic variations and common speech recognition errors",
            "context": [
                "User attempted to say 'Biper' but it was misinterpreted as 'Viper'.",
                "User is struggling with voice input accuracy for their name.",
            ],
            "stages": [
                {
                    "stage_goal": "Design a fuzzy string matching algorithm capable of handling phonetic variations and common speech recognition errors",
                    "stage_type": "FILE_WORK",
                    "success_condition": "A code structure or algorithm description is generated that accounts for phonetic similarities and speech recognition failures.",
                }
            ],
        },
    }


def run_smoke() -> SpeculativeTaskIdeaNormalizerReport:
    speculative = normalize_route_decision(
        _base_code_decision(),
        "Maybe I should create a fuzzy words code.",
    )
    explicit = normalize_route_decision(
        _base_code_decision(),
        "Could you maybe create a fuzzy words code for me?",
    )

    speculative_decision = str(speculative.get("decision") or "")
    explicit_decision = str(explicit.get("decision") or "")
    explicit_goal = str((explicit.get("card") or {}).get("goal") or "")

    success = speculative_decision == "CHAT" and explicit_decision == "TASK" and "fuzzy string matching" in explicit_goal.lower()
    return SpeculativeTaskIdeaNormalizerReport(
        success=bool(success),
        speculative_decision=speculative_decision,
        explicit_decision=explicit_decision,
        explicit_goal=explicit_goal,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
