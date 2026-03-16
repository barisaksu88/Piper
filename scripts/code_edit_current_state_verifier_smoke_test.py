from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.file_checker_rules import LocalFileOpRuleChecker  # noqa: E402


@dataclass(frozen=True)
class CodeEditCurrentStateVerifierReport:
    success: bool
    verdict: str
    reason: str


def run_smoke() -> CodeEditCurrentStateVerifierReport:
    stage = {
        "stage_goal": "Read 'control_demo.py', apply the requested code changes for this step, and save the updated file. Latest request: Fix control_demo.py so both left and right controls work correctly and the boundary clamp uses the proper width constant.",
        "stage_type": "FILE_WORK",
        "success_condition": "The modified artifact is 'control_demo.py' and satisfies the latest user request: Fix control_demo.py so both left and right controls work correctly and the boundary clamp uses the proper width constant..",
        "context": [
            "Previous diagnosis identified typo 'SCREEN_WIDT' instead of 'SCREEN_WIDTH' in clamp_position.",
            "File content shows PLAYER_SPEED = 5 and SCREEN_WIDTH = 20 defined at top.",
            "The relevant workspace code file is 'control_demo.py'.",
        ],
    }
    fixed_source = """PLAYER_SPEED = 5
SCREEN_WIDTH = 20


def handle_key(key: str, velocity: int) -> int:
    if key in ("left", "a"):
        return -PLAYER_SPEED
    if key == "space":
        return 0
    if key == "right":
        return PLAYER_SPEED
    return velocity


def clamp_position(x: int) -> int:
    if x < 0:
        return 0
    if x > SCREEN_WIDTH:
        return SCREEN_WIDTH
    return x


if __name__ == "__main__":
    left = handle_key("left", 0)
    right = handle_key("right", 0)
    print(f"left={left}, right={right}, clamp={clamp_position(30)}")
"""
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "control_demo.py").write_text(fixed_source, encoding="utf-8")
        checker = LocalFileOpRuleChecker(root, stage, preferred_paths=["control_demo.py"])
        decision = checker.evaluate_current_stage_state() or {}
    verdict = str(decision.get("verdict", "")).upper()
    reason = str(decision.get("reason", "")).strip()
    success = verdict == "VERIFIED" and "SCREEN_WIDTH" in reason and "left/right" in reason
    return CodeEditCurrentStateVerifierReport(
        success=bool(success),
        verdict=verdict,
        reason=reason,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Verify current-state code edit recovery can certify a corrected artifact.")


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
