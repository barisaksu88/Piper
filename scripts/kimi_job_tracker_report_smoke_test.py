from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from _bootstrap import ROOT_DIR

import scripts.kimi_job_tracker as tracker


def _write_job(job_dir: Path, *, pid: int, status: str, stdout: str = "", stderr: str = "", exit_code: int | None = None) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "job_id": job_dir.name,
        "pid": pid,
        "started_at": "2026-05-02T00:00:00+0000",
        "work_dir": str(ROOT_DIR),
        "kimi_cli": "kimi.exe",
        "mode": "print",
        "prompt_path": str(job_dir / "prompt.txt"),
        "stdout_log": str(job_dir / "stdout.log"),
        "stderr_log": str(job_dir / "stderr.log"),
        "share_dir": str(job_dir / "kimi_share"),
    }
    (job_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (job_dir / "prompt.txt").write_text("dummy prompt", encoding="utf-8")
    (job_dir / "stdout.log").write_text(stdout, encoding="utf-8")
    (job_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    if status == "finished" and exit_code is not None:
        (job_dir / "exit_code.txt").write_text(str(exit_code), encoding="utf-8")


def _capture_report(namespace: argparse.Namespace) -> dict[str, object]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = tracker.cmd_report(namespace)
    report = json.loads(buffer.getvalue())
    report["_exit_code"] = exit_code
    return report


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = Path(tmp) / "runtime"
        jobs_dir = runtime / "kimi_jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        old_jobs_dir = tracker.JOBS_DIR
        old_profile_path = tracker.PROFILE_PATH
        old_profile_lock = tracker.PROFILE_LOCK_PATH
        try:
            tracker.JOBS_DIR = jobs_dir
            tracker.PROFILE_PATH = jobs_dir / "profile.json"
            tracker.PROFILE_LOCK_PATH = jobs_dir / "profile.lock"

            _write_job(
                jobs_dir / "20260502-010101-aaaa1111",
                pid=101,
                status="finished",
                stdout="all good\n",
                stderr="",
                exit_code=0,
            )
            _write_job(
                jobs_dir / "20260502-010202-bbbb2222",
                pid=102,
                status="finished",
                stdout="warning: suspicious issue found\n",
                stderr="error: something odd happened\n",
                exit_code=0,
            )
            sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                _write_job(
                    jobs_dir / "20260502-010303-cccc3333",
                    pid=sleeper.pid,
                    status="running",
                    stdout="still running\n",
                    stderr="",
                )

                report = _capture_report(
                    argparse.Namespace(recent=2, tail_lines=3, flag_anomalies=True, all=False)
                )
                jobs = report["jobs"]
                assert report["total_considered"] == 2, report
                assert report["recent_limit"] == 2, report
                assert report["tail_lines"] == 3, report
                assert report["flagged_count"] == 1, report
                assert [job["job_id"] for job in jobs] == [
                    "20260502-010202-bbbb2222",
                    "20260502-010101-aaaa1111",
                ], report
                assert jobs[0]["flagged"] is True, report
                assert "issue" in jobs[0]["flag_hits"], report
                assert "warning" in jobs[0]["flag_hits"], report
                assert "error" in jobs[0]["flag_hits"], report
                assert jobs[1]["flagged"] is False, report

                report_all = _capture_report(
                    argparse.Namespace(recent=10, tail_lines=1, flag_anomalies=False, all=True)
                )
                assert report_all["total_considered"] == 3, report_all
                assert report_all["flagged_count"] == 0, report_all
                running_job = next(
                    (job for job in report_all["jobs"] if job["job_id"] == "20260502-010303-cccc3333"),
                    None,
                )
                assert running_job is not None, report_all
                assert running_job["status"] == "running", running_job
            finally:
                sleeper.terminate()
                try:
                    sleeper.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    sleeper.kill()
                    sleeper.wait()
        finally:
            tracker.JOBS_DIR = old_jobs_dir
            tracker.PROFILE_PATH = old_profile_path
            tracker.PROFILE_LOCK_PATH = old_profile_lock

    print("KIMI_JOB_TRACKER_REPORT_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
