#!/usr/bin/env python3
"""End-to-end smoke test for the Kimi job tracker.

Launches real subprocess jobs through the tracker, waits for them to finish,
and verifies that metadata (both the file and the status API) includes the
share_dir and mode fields.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from _bootstrap import ROOT_DIR

import kimi_job_tracker as tracker


def _make_fake_kimi(tmp_dir: Path) -> Path:
    """Create a fake Kimi CLI executable that prints output and echoes KIMI_SHARE_DIR."""
    if os.name == "nt":
        fake = tmp_dir / "fake_kimi.bat"
        fake.write_text(
            "@echo off\n"
            "echo fake_kimi_ran\n"
            "echo share_dir=%KIMI_SHARE_DIR%\n"
            "exit /b 0\n",
            encoding="utf-8",
        )
    else:
        fake = tmp_dir / "fake_kimi.sh"
        fake.write_text(
            '#!/bin/bash\necho "fake_kimi_ran"\necho "share_dir=$KIMI_SHARE_DIR"\nexit 0\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
    return fake


def _assert_metadata_has_fields(meta: dict, *, expected_mode: str) -> None:
    assert "share_dir" in meta, f"share_dir missing from metadata keys: {list(meta.keys())}"
    assert "mode" in meta, f"mode missing from metadata keys: {list(meta.keys())}"
    assert meta["mode"] == expected_mode, meta["mode"]
    share_dir = Path(meta["share_dir"])
    assert str(share_dir).endswith("kimi_share"), meta["share_dir"]
    assert share_dir.exists(), f"share_dir does not exist: {share_dir}"
    assert share_dir.is_dir(), f"share_dir is not a directory: {share_dir}"


def _assert_stdout_has_env(stdout_path: Path, share_dir: Path) -> None:
    assert stdout_path.exists(), f"stdout.log not found: {stdout_path}"
    text = stdout_path.read_text(encoding="utf-8")
    assert "fake_kimi_ran" in text, text
    expected_env = tracker._windows_workdir(share_dir)
    assert f"share_dir={expected_env}" in text, text


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        jobs_dir = tmp_path / "kimi_jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        old_jobs_dir = tracker.JOBS_DIR
        old_profile_path = tracker.PROFILE_PATH
        old_profile_lock = tracker.PROFILE_LOCK_PATH
        try:
            tracker.JOBS_DIR = jobs_dir
            tracker.PROFILE_PATH = jobs_dir / "profile.json"
            tracker.PROFILE_LOCK_PATH = jobs_dir / "profile.lock"

            fake_kimi = _make_fake_kimi(tmp_path)

            # --- Test mode="print" ---
            args_print = argparse.Namespace(
                prompt="Smoke test prompt.",
                prompt_file=None,
                work_dir=str(ROOT_DIR),
                kimi_cli=str(fake_kimi),
                mode="print",
                force_full_prompt=False,
            )
            start_result = tracker._start_job(args_print)
            job_id_print = start_result["job_id"]
            assert start_result["status"] == "running", start_result

            wait_args = argparse.Namespace(job_id=job_id_print, timeout=30.0, poll_interval=0.2)
            exit_code = tracker.cmd_wait(wait_args)
            assert exit_code == 0, f"Print-mode job exited with {exit_code}"

            # Verify metadata file
            meta_print = json.loads(tracker._metadata_path(job_id_print).read_text(encoding="utf-8"))
            _assert_metadata_has_fields(meta_print, expected_mode="print")
            _assert_stdout_has_env(Path(meta_print["stdout_log"]), Path(meta_print["share_dir"]))

            # Verify status API also exposes the fields
            status_print = tracker._job_status(job_id_print)
            assert status_print.get("mode") == "print", status_print
            assert "share_dir" in status_print, f"share_dir missing from status: {list(status_print.keys())}"

            # --- Test mode="wire" ---
            args_wire = argparse.Namespace(
                prompt=None,
                prompt_file=None,
                work_dir=str(ROOT_DIR),
                kimi_cli=str(fake_kimi),
                mode="wire",
                force_full_prompt=False,
            )
            start_wire = tracker._start_job(args_wire)
            job_id_wire = start_wire["job_id"]
            assert start_wire["status"] == "running", start_wire

            wait_args_wire = argparse.Namespace(job_id=job_id_wire, timeout=30.0, poll_interval=0.2)
            exit_code_wire = tracker.cmd_wait(wait_args_wire)
            assert exit_code_wire == 0, f"Wire-mode job exited with {exit_code_wire}"

            meta_wire = json.loads(tracker._metadata_path(job_id_wire).read_text(encoding="utf-8"))
            _assert_metadata_has_fields(meta_wire, expected_mode="wire")
            _assert_stdout_has_env(Path(meta_wire["stdout_log"]), Path(meta_wire["share_dir"]))

            status_wire = tracker._job_status(job_id_wire)
            assert status_wire.get("mode") == "wire", status_wire
            assert "share_dir" in status_wire, f"share_dir missing from wire status: {list(status_wire.keys())}"

            print("KIMI_JOB_TRACKER_E2E_SMOKE_OK")
            return 0
        finally:
            tracker.JOBS_DIR = old_jobs_dir
            tracker.PROFILE_PATH = old_profile_path
            tracker.PROFILE_LOCK_PATH = old_profile_lock


if __name__ == "__main__":
    raise SystemExit(main())
