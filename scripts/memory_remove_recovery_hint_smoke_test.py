from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.executor import StageExecutor  # noqa: E402


@dataclass(frozen=True)
class MemoryRemoveRecoveryHintSmokeReport:
    success: bool
    remove_hint: str
    list_hint: str


def run_smoke() -> MemoryRemoveRecoveryHintSmokeReport:
    stage = {
        "stage_goal": "Remove the durable user fact 'works on: Catch the Stars' from memory",
        "stage_type": "MEMORY_WORK",
        "success_condition": "Knowledge store no longer contains the fact works on: Catch the Stars",
        "allowed_tools": ["REMOVE_KNOWLEDGE", "LIST_KNOWLEDGE"],
    }
    remove_hint = StageExecutor._memory_remove_recovery_hint(
        stage,
        "REMOVE_KNOWLEDGE",
        "Key not found: works on: Catch the Stars",
    )
    list_hint = StageExecutor._memory_remove_recovery_hint(
        stage,
        "LIST_KNOWLEDGE",
        "[WORLD STATE]\n- works on: Catch the Stars\nEntity: Catch the Stars (project)\n- File Name: catch_the_stars.py",
    )
    success = (
        "LIST_KNOWLEDGE once" in remove_hint
        and "retry REMOVE_KNOWLEDGE with the exact key" in remove_hint
        and "current world-state listing is now in the scratchpad" in list_hint
    )
    return MemoryRemoveRecoveryHintSmokeReport(
        success=bool(success),
        remove_hint=remove_hint,
        list_hint=list_hint,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
