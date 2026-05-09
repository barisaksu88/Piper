from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from _bootstrap import ROOT_DIR

import kimi_job_tracker as tracker


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        jobs_dir = Path(tmp) / "kimi_jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        old_jobs_dir = tracker.JOBS_DIR
        old_profile_path = tracker.PROFILE_PATH
        old_profile_lock = tracker.PROFILE_LOCK_PATH
        try:
            tracker.JOBS_DIR = jobs_dir
            tracker.PROFILE_PATH = jobs_dir / "profile.json"
            tracker.PROFILE_LOCK_PATH = jobs_dir / "profile.lock"

            profile = tracker._default_profile()
            prompt_print, updated_print = tracker._compose_prompt("Build the thing.", profile)
            assert prompt_print.endswith("Task:\nBuild the thing."), prompt_print
            assert updated_print["turn_count"] == 1, updated_print

            wire_args = argparse.Namespace(prompt=None, prompt_file=None, mode="wire")
            assert tracker._load_prompt(wire_args) == ""

            start_args = argparse.Namespace(
                prompt=None,
                prompt_file=None,
                work_dir=str(ROOT_DIR),
                kimi_cli=str(tracker.DEFAULT_KIMI_CLI),
                mode="wire",
                force_full_prompt=False,
            )
            # Do not launch the process; just confirm the job prep path accepts wire mode.
            raw_prompt = tracker._load_prompt(start_args)
            assert raw_prompt == "", raw_prompt

            job_id = "20260502-999999-smoke000"
            share_dir = tracker._job_share_dir(job_id)
            assert str(share_dir).endswith("kimi_share"), share_dir
            assert str(share_dir).startswith(str(jobs_dir)), share_dir
            print("KIMI_JOB_TRACKER_MODE_SMOKE_OK")
            return 0
        finally:
            tracker.JOBS_DIR = old_jobs_dir
            tracker.PROFILE_PATH = old_profile_path
            tracker.PROFILE_LOCK_PATH = old_profile_lock


if __name__ == "__main__":
    raise SystemExit(main())
