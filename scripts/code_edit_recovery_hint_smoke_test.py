from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.file_stage_policy import FileStagePolicy  # noqa: E402
from core.scratchpad_formatter import ScratchpadFormatter  # noqa: E402


@dataclass(frozen=True)
class CodeEditRecoveryHintReport:
    success: bool
    hint_mentions_newlines: bool
    hint_mentions_localized_edits: bool
    hint_mentions_target: bool
    observation_includes_snippet_content: bool
    observation_mentions_file: bool
    hint: str
    observation_preview: str


def run_smoke() -> CodeEditRecoveryHintReport:
    stage = {
        "stage_goal": "Read 'control_demo.py', apply the requested code changes for this step, and save the updated file.",
        "stage_type": "FILE_WORK",
        "success_condition": "The modified artifact is 'control_demo.py' and satisfies the latest user request.",
        "context": [
            "The relevant workspace code file is 'control_demo.py'.",
            "Use 'control_demo.py' directly for this task.",
        ],
    }
    tool_result = {
        "tool": "RUN_CODE",
        "status": "EXECUTED",
        "summary": "Execution succeeded with output.",
        "updated_files": ["control_demo.py"],
        "evidence_files": ["control_demo.py"],
        "file_snippets": {
            "control_demo.py": {
                "status": "text",
                "truncated": False,
                "full_char_count": 132,
                "content": "PLAYER_SPEED = 5 SCREEN_WIDTH = 20 def handle_key(key, velocity): return velocity\n",
            }
        },
    }
    file_check = {
        "verdict": "FAILED",
        "reason": "Text file at control_demo.py does not match the requested content.",
        "evidence_files": ["control_demo.py"],
    }
    hint = FileStagePolicy.file_checker_recovery_hint(stage, tool_result, file_check)
    observation_preview = ScratchpadFormatter.format_step(
        2,
        "Attempting the rewrite.",
        "[RUN_CODE] ... [/RUN_CODE]",
        tool_result,
    )
    hint_mentions_newlines = "newlines" in hint.lower()
    hint_mentions_localized_edits = "localized edits" in hint.lower()
    hint_mentions_target = "control_demo.py" in hint
    observation_includes_snippet_content = "PLAYER_SPEED = 5" in observation_preview
    observation_mentions_file = "control_demo.py" in observation_preview
    success = all(
        (
            bool(hint.strip()),
            hint_mentions_newlines,
            hint_mentions_localized_edits,
            hint_mentions_target,
            observation_includes_snippet_content,
            observation_mentions_file,
        )
    )
    return CodeEditRecoveryHintReport(
        success=bool(success),
        hint_mentions_newlines=bool(hint_mentions_newlines),
        hint_mentions_localized_edits=bool(hint_mentions_localized_edits),
        hint_mentions_target=bool(hint_mentions_target),
        observation_includes_snippet_content=bool(observation_includes_snippet_content),
        observation_mentions_file=bool(observation_mentions_file),
        hint=hint,
        observation_preview=observation_preview,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify code-edit checker failures produce recovery hints and expose current snippet content."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
