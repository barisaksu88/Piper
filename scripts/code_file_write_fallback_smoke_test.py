from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG  # noqa: E402
from core.executor import StageExecutor  # noqa: E402


class _DummyUi:
    def put(self, _event) -> None:
        return


class _DummyBrain:
    workspace = str(ROOT_DIR / "data" / "workspace")


@dataclass(frozen=True)
class CodeWriteFallbackReport:
    success: bool
    block_code_write: bool
    code_write_hint: str
    block_redundant_read: bool
    redundant_read_hint: str
    executor_max_steps: int


def run_smoke() -> CodeWriteFallbackReport:
    executor = StageExecutor(
        llm_client=None,
        agent_brain=_DummyBrain(),
        img_gen=None,
        boot_mgr=None,
        ui_queue=_DummyUi(),
    )
    executor.scratchpad.append("FILE_READ_EXACT_PATH: catch_the_stars.py\nFILE_READ_EXACT_CONTENT:\nprint('fixture')\n")
    stage = {
        "stage_goal": "Modify catch_the_stars.py to resolve the identified control issue.",
        "stage_type": "FILE_WORK",
        "success_condition": "The file is updated with the corrected control logic.",
    }
    write_tool_tag = (
        '[FILE_OP] {"action":"write_text","path":"catch_the_stars.py","content":"import pygame\\nprint(1)\\n"} [/FILE_OP]'
    )
    read_tool_tag = '[FILE_OP] {"action":"read_text","path":"catch_the_stars.py"} [/FILE_OP]'
    block_code_write, code_write_hint = executor._should_block_code_file_write_text(stage, write_tool_tag)
    block_redundant_read, redundant_read_hint = executor._should_block_redundant_exact_read(stage, read_tool_tag)
    success = (
        not block_code_write
        and block_redundant_read
        and "FILE_OP write_text" in redundant_read_hint
        and int(getattr(CFG, "EXECUTOR_MAX_STEPS", 0) or 0) >= 12
    )
    return CodeWriteFallbackReport(
        success=bool(success),
        block_code_write=bool(block_code_write),
        code_write_hint=code_write_hint,
        block_redundant_read=bool(block_redundant_read),
        redundant_read_hint=redundant_read_hint,
        executor_max_steps=int(getattr(CFG, "EXECUTOR_MAX_STEPS", 0) or 0),
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Verify code-file edit stages allow valid FILE_OP write_text fallback while still blocking redundant rereads.")


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
