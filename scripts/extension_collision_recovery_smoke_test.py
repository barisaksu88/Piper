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
class ExtensionCollisionRecoveryReport:
    success: bool
    observation_contains_collisions: bool
    observation_contains_requested_extensions: bool
    recovery_hint_mentions_exclusions: bool
    recovery_hint_mentions_no_list_tree: bool
    recovery_hint: str
    observation_preview: str


def run_smoke() -> ExtensionCollisionRecoveryReport:
    stage = {
        "stage_goal": "Consolidate files so each extension lives in one chosen destination folder without creating duplicates.",
        "stage_type": "FILE_WORK",
        "file_stage_kind": "STRUCTURE_PREP",
        "success_condition": "For every relevant extension, files are consolidated into a single destination folder and duplicate identical files are not kept twice.",
        "allowed_tools": ["FILE_OP"],
        "context": [
            "Use the workspace_cleanup flow.",
        ],
    }
    tool_result = {
        "tool": "FILE_OP",
        "status": "FAILED",
        "summary": "FILE_OP consolidate_by_extension found name collisions with different content.",
        "action": "consolidate_by_extension",
        "requested_root": ".",
        "requested_extensions": [".txt", "[no_ext]"],
        "destinations": {
            ".txt": "text_files",
            "[no_ext]": "files",
        },
        "collisions": [
            "archive/beta.txt -> text_files/beta.txt",
            "browser_downloads_real/rfc2606.txt -> text_files/rfc2606.txt",
            "show capabilities -> files/show capabilities",
        ],
    }

    observation = ScratchpadFormatter.format_step(
        1,
        "Consolidation hit a collision and needs a narrower retry.",
        '[FILE_OP] {"action":"consolidate_by_extension","root":"."} [/FILE_OP]',
        tool_result,
    )
    recovery_hint = FileStagePolicy.file_recovery_hint(stage, tool_result)

    observation_contains_collisions = (
        '"collisions"' in observation
        and "archive/beta.txt -> text_files/beta.txt" in observation
        and "show capabilities -> files/show capabilities" in observation
    )
    observation_contains_requested_extensions = (
        '"requested_extensions"' in observation
        and '".txt"' in observation
        and '"[no_ext]"' in observation
    )
    hint_lower = recovery_hint.lower()
    recovery_hint_mentions_exclusions = "exclude_files" in recovery_hint
    recovery_hint_mentions_no_list_tree = "do not repeat list_tree" in hint_lower
    success = (
        observation_contains_collisions
        and observation_contains_requested_extensions
        and recovery_hint_mentions_exclusions
        and recovery_hint_mentions_no_list_tree
    )
    return ExtensionCollisionRecoveryReport(
        success=bool(success),
        observation_contains_collisions=bool(observation_contains_collisions),
        observation_contains_requested_extensions=bool(observation_contains_requested_extensions),
        recovery_hint_mentions_exclusions=bool(recovery_hint_mentions_exclusions),
        recovery_hint_mentions_no_list_tree=bool(recovery_hint_mentions_no_list_tree),
        recovery_hint=recovery_hint,
        observation_preview=observation,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify consolidate-by-extension collision details and recovery guidance stay visible to the planner."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
