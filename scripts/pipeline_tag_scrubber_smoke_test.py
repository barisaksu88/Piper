from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.pipeline import TagScrubber  # noqa: E402


@dataclass(frozen=True)
class PipelineTagScrubberReport:
    success: bool
    split_closing_think_removed: bool
    split_opening_think_removed: bool
    unsplit_think_tags_removed: bool
    non_think_angle_text_preserved: bool
    run_code_block_removed: bool


def _feed(chunks: list[str]) -> tuple[str, str]:
    scrubber = TagScrubber()
    visible = "".join(scrubber.process_delta(chunk) for chunk in chunks)
    tail = scrubber.flush()
    return visible + tail, tail


def run_smoke() -> PipelineTagScrubberReport:
    split_closing, split_closing_tail = _feed(["Hello </thi", "nk>world"])
    split_opening, split_opening_tail = _feed(["Hello <thi", "nk>world"])
    unsplit, _ = _feed(["Alpha </think> Beta <think> Gamma"])
    non_think, _ = _feed(["Use <tool_call> literally."])
    run_code, _ = _feed(["A [RUN_CODE]print('hidden')[/RUN_CODE] B"])

    split_closing_think_removed = split_closing == "Hello world" and split_closing_tail == ""
    split_opening_think_removed = split_opening == "Hello world" and split_opening_tail == ""
    unsplit_think_tags_removed = unsplit == "Alpha  Beta  Gamma"
    non_think_angle_text_preserved = non_think == "Use <tool_call> literally."
    run_code_block_removed = run_code == "A  B"
    success = all(
        [
            split_closing_think_removed,
            split_opening_think_removed,
            unsplit_think_tags_removed,
            non_think_angle_text_preserved,
            run_code_block_removed,
        ]
    )
    return PipelineTagScrubberReport(
        success=success,
        split_closing_think_removed=split_closing_think_removed,
        split_opening_think_removed=split_opening_think_removed,
        unsplit_think_tags_removed=unsplit_think_tags_removed,
        non_think_angle_text_preserved=non_think_angle_text_preserved,
        run_code_block_removed=run_code_block_removed,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
