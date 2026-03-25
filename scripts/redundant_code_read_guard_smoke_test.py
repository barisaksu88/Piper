from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.executor import StageExecutor  # noqa: E402
from core.engines.file_work import FileWorkEngine  # noqa: E402
from core.prompt_builder import PromptBuilder  # noqa: E402


class _DummyUI:
    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []

    def put(self, item: tuple[str, object]) -> None:
        self.items.append(item)


@dataclass(frozen=True)
class RedundantReadGuardReport:
    success: bool
    blocked: bool
    hint: str
    prompt_mentions_exact_read: bool


def run_smoke() -> RedundantReadGuardReport:
    stage = {
        "stage_goal": "Correct the input handling logic to ensure left and right buttons control the game character.",
        "stage_type": "FILE_WORK",
        "success_condition": "The code is updated with the corrected input handling logic.",
        "allowed_tools": ["FILE_OP", "RUN_CODE"],
    }
    ui = _DummyUI()
    executor = StageExecutor(None, None, None, None, ui)
    executor.scratchpad = [
        "=== STAGE 1 START ===\n"
        "STAGE_GOAL: Inspect the code.\n"
        "STAGE_TYPE: FILE_WORK\n"
        "SUCCESS_CONDITION: The current code is read.",
        "FILE_READ_EXACT_PATH: catch_the_stars.py\n"
        "FILE_READ_EXACT_CONTENT:\n"
        "print('full source available')\n",
    ]
    tool_tag = '[FILE_OP] {"action":"read_text","path":"catch_the_stars.py"} [/FILE_OP]'
    exact_paths = FileWorkEngine.exact_read_paths_from_scratchpad(executor.scratchpad)
    block = FileWorkEngine.should_block(stage, tool_tag, exact_paths)
    blocked = bool(block.blocked)
    hint = str(block.reason or "")
    prompt = PromptBuilder.build_planner_prompt(
        base_template="[STEP]\n[STAGE_CARD]\n[SCRATCHPAD]\n[TOOL_GUIDE]",
        stage=stage,
        scratchpad_text="\n\n".join(executor.scratchpad),
        step_count=2,
    )
    prompt_mentions_exact_read = "EXACT_READ_READY" in prompt and "catch_the_stars.py" in prompt
    success = blocked and "already in the scratchpad" in hint and prompt_mentions_exact_read
    return RedundantReadGuardReport(
        success=bool(success),
        blocked=bool(blocked),
        hint=hint,
        prompt_mentions_exact_read=bool(prompt_mentions_exact_read),
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Run a deterministic smoke for the redundant code-read guard.")


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
