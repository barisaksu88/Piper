#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT_DIR / "data" / "runtime" / "kimi_jobs"
DEFAULT_KIMI_CLI = Path("/mnt/c/Users/Hawk Gaming/.local/bin/kimi.exe")
PROFILE_PATH = JOBS_DIR / "profile.json"
PROFILE_LOCK_PATH = JOBS_DIR / "profile.lock"
DEFAULT_REFRESH_EVERY = 5
KIMI_MODES = ("print", "wire")
if os.name == "nt":
    import msvcrt
DEFAULT_SYSTEM_PROMPT = """Kimi role for Piper work:
- You are the heavy implementation helper for this repository.
- Follow the requested file scope exactly and do not broaden the task on your own.
- Prefer implementation, scaffolding, and large first-pass edits over architecture invention.
- Respect repository docs and local constraints named in the user prompt.
- Do not silently fix unrelated issues.
- Report anything suspicious you notice along the way:
  - repo state that looks off
  - doc/spec conflicts
  - unclear ownership or architecture placement
  - broken behavior that is out of scope
- If you hit a conflict or uncertainty, call it out clearly in your final report.
- In your final response, always include:
  - files changed
  - tests run
  - issues or anomalies noticed
"""


def _windows_workdir(path: Path) -> str:
    resolved = path.resolve()
    text = str(resolved)
    if text.startswith("/mnt/") and len(text) > 6:
        drive = text[5].upper()
        rest = text[7:].replace("/", "\\")
        return f"{drive}:\\{rest}"
    return text


def _to_windows_path(text: str) -> str:
    """Convert a WSL /mnt/X/... path to a Windows X:\\... path."""
    if text.startswith("/mnt/") and len(text) > 6:
        drive = text[5].upper()
        rest = text[7:].replace("/", "\\")
        return f"{drive}:\\{rest}"
    return text


def _load_prompt(args: argparse.Namespace) -> str:
    if str(getattr(args, "mode", "print") or "print").strip().lower() == "wire":
        return ""
    inline = str(args.prompt or "").strip()
    prompt_file = str(args.prompt_file or "").strip()
    if inline and prompt_file:
        raise SystemExit("Use either --prompt or --prompt-file, not both.")
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8")
    if inline:
        return inline
    data = sys.stdin.read()
    if data.strip():
        return data
    raise SystemExit("Provide a prompt via --prompt, --prompt-file, or stdin.")


def _default_profile() -> dict[str, Any]:
    return {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "reminder_prompt": "Remember: stay in scope, follow repo docs, and report anomalies you notice.",
        "refresh_every": DEFAULT_REFRESH_EVERY,
        "turn_count": 0,
        "updated_at": "",
    }


def _load_profile() -> dict[str, Any]:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if not PROFILE_PATH.exists():
        profile = _default_profile()
        _save_profile(profile)
        return profile
    try:
        raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    profile = _default_profile()
    profile.update(raw if isinstance(raw, dict) else {})
    return profile


def _save_profile(profile: dict[str, Any]) -> None:
    payload = dict(_default_profile())
    payload.update(profile or {})
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@contextlib.contextmanager
def _profile_lock():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        fd = os.open(str(PROFILE_LOCK_PATH), os.O_CREAT | os.O_RDWR)
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)
    else:
        import fcntl

        with PROFILE_LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _compose_prompt(user_prompt: str, profile: dict[str, Any], *, force_full: bool = False) -> tuple[str, dict[str, Any]]:
    clean_user_prompt = str(user_prompt or "").strip()
    if not clean_user_prompt:
        raise SystemExit("Prompt is empty.")
    refresh_every = max(1, int(profile.get("refresh_every") or DEFAULT_REFRESH_EVERY))
    turn_count = max(0, int(profile.get("turn_count") or 0))
    next_turn = turn_count + 1
    include_full = force_full or turn_count == 0 or (next_turn - 1) % refresh_every == 0
    prefix = str(profile.get("system_prompt") if include_full else profile.get("reminder_prompt") or "").strip()
    composed = clean_user_prompt if not prefix else f"{prefix}\n\nTask:\n{clean_user_prompt}"
    updated = dict(profile)
    updated["turn_count"] = next_turn
    return composed, updated


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _metadata_path(job_id: str) -> Path:
    return _job_dir(job_id) / "metadata.json"


def _read_metadata(job_id: str) -> dict[str, Any]:
    path = _metadata_path(job_id)
    if not path.exists():
        raise SystemExit(f"Unknown job id: {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_metadata(job_dir: Path, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (_metadata_path(payload["job_id"])).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        status_path = Path("/proc") / str(pid) / "status"
        if status_path.exists():
            try:
                for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("State:"):
                        state = line.split(":", 1)[1].strip()
                        if state.startswith("Z"):
                            return False
                        return True
            except Exception:
                pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _launch_process(
    command: list[str],
    *,
    cwd: str,
    stdout_path: Path,
    stderr_path: Path,
    exit_code_path: Path,
    env: dict[str, str],
) -> subprocess.Popen[Any]:
    # Build a batch file that sets env vars explicitly.
    # This is required on WSL because WSL interop does not reliably
    # pass custom env vars (like KIMI_SHARE_DIR) to Windows processes.
    batch_path = stdout_path.parent / "run_kimi.bat"

    # Convert command args to Windows paths where needed.
    # Newlines must be removed because batch files cannot embed them
    # in a single command line.
    win_command = [
        _to_windows_path(part).replace("\n", " ").replace("\r", " ")
        for part in command
    ]
    quoted_command = subprocess.list2cmdline(win_command)

    stdout_win = _to_windows_path(str(stdout_path))
    stderr_win = _to_windows_path(str(stderr_path))
    exit_win = _to_windows_path(str(exit_code_path))

    batch_lines = ["@echo off"]

    # Only set env vars that the tracker explicitly overrode.
    # On WSL, copying os.environ into env would otherwise try to
    # inject the entire Linux environment into a Windows process.
    explicit_env = {
        k: v
        for k, v in env.items()
        if k not in os.environ or os.environ.get(k) != v
    }
    for k, v in explicit_env.items():
        batch_lines.append(f'set "{k}={v}"')

    batch_lines.append(f'call {quoted_command} > "{stdout_win}" 2> "{stderr_win}"')
    batch_lines.append("set KIMI_EXIT=%ERRORLEVEL%")
    batch_lines.append(f'echo %KIMI_EXIT% > "{exit_win}"')
    batch_lines.append("exit /b %KIMI_EXIT%")

    batch_path.write_text("\n".join(batch_lines), encoding="utf-8")

    if os.name == "nt":
        # Native Windows: run batch file directly.
        return subprocess.Popen(
            ["cmd.exe", "/d", "/s", "/c", _to_windows_path(str(batch_path))],
            cwd=_to_windows_path(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    # WSL: run batch file via cmd.exe from bash, then capture exit code.
    batch_win = _to_windows_path(str(batch_path))
    bash_cmd = (
        f'/mnt/c/Windows/System32/cmd.exe /c "{batch_win}"; '
        f"code=$?; printf '%s' \"$code\" > {shlex.quote(str(exit_code_path))}"
    )
    return subprocess.Popen(
        ["/bin/bash", "-lc", bash_cmd],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )


def _job_share_dir(job_id: str) -> Path:
    return _job_dir(job_id) / "kimi_share"


_ANOMALY_KEYWORDS = [
    "anomaly", "anomalies", "suspicious", "unexpected",
    "issue", "issues", "warning", "warn", "error",
    "failed", "failure", "broken", "mismatch",
    "conflict", "regression", "out of scope", "doc/spec",
    "doc-code", "does not match", "inconsistent",
    "questionable", "concern", "red flag", "caution",
    "attention needed", "notable", "strange", "odd",
    "weird", "problem", "problems", "degraded",
    "hang", "stuck", "timeout", "orphan",
]


def _tail_text(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    if lines <= 0:
        return []
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return content[-lines:]


def _flag_anomalies(text: str) -> tuple[bool, list[str]]:
    lowered = text.lower()
    hits = [kw for kw in _ANOMALY_KEYWORDS if kw in lowered]
    return bool(hits), hits


def _job_status(job_id: str) -> dict[str, Any]:
    meta = _read_metadata(job_id)
    job_dir = _job_dir(job_id)
    pid = int(meta.get("pid") or 0)
    exit_code_path = job_dir / "exit_code.txt"
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    exit_code: int | None = None
    if exit_code_path.exists():
        try:
            exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
        except Exception:
            exit_code = None
    running = bool(pid and _pid_alive(pid))
    finished = bool(exit_code_path.exists()) and not running
    status = "running" if running else "finished" if finished else "unknown"
    return {
        "job_id": job_id,
        "status": status,
        "running": running,
        "finished": finished,
        "pid": pid,
        "exit_code": exit_code,
        "work_dir": meta.get("work_dir"),
        "started_at": meta.get("started_at"),
        "kimi_cli": meta.get("kimi_cli"),
        "mode": meta.get("mode"),
        "share_dir": meta.get("share_dir"),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "stdout_tail": _tail_text(stdout_path, 20),
        "stderr_tail": _tail_text(stderr_path, 20),
    }


def cmd_profile_show(args: argparse.Namespace) -> int:
    with _profile_lock():
        print(json.dumps(_load_profile(), indent=2))
    return 0


def cmd_profile_set(args: argparse.Namespace) -> int:
    with _profile_lock():
        profile = _load_profile()
        if args.system_prompt is not None:
            profile["system_prompt"] = str(args.system_prompt)
        if args.system_prompt_file:
            profile["system_prompt"] = Path(args.system_prompt_file).read_text(encoding="utf-8")
        if args.reminder_prompt is not None:
            profile["reminder_prompt"] = str(args.reminder_prompt)
        if args.reminder_prompt_file:
            profile["reminder_prompt"] = Path(args.reminder_prompt_file).read_text(encoding="utf-8")
        if args.refresh_every is not None:
            profile["refresh_every"] = max(1, int(args.refresh_every))
        if args.reset_turns:
            profile["turn_count"] = 0
        _save_profile(profile)
    print(json.dumps(profile, indent=2))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    result = _start_job(args)
    print(json.dumps({"success": True, **result}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(_job_status(args.job_id), indent=2))
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.time() + max(0.0, float(args.timeout))
    interval = max(0.1, float(args.poll_interval))
    while True:
        status = _job_status(args.job_id)
        if status["finished"]:
            print(json.dumps(status, indent=2))
            return int(status["exit_code"] or 0)
        if time.time() >= deadline:
            status["timed_out"] = True
            print(json.dumps(status, indent=2))
            return 124
        time.sleep(interval)


def _start_job(args: argparse.Namespace) -> dict[str, Any]:
    raw_prompt = _load_prompt(args)
    mode = str(getattr(args, "mode", "print") or "print").strip().lower()
    if mode not in KIMI_MODES:
        raise SystemExit(f"Unsupported Kimi mode: {mode}")
    kimi_cli = Path(str(args.kimi_cli or DEFAULT_KIMI_CLI)).expanduser()
    if not kimi_cli.exists():
        raise SystemExit(f"Kimi CLI not found: {kimi_cli}")

    work_dir = Path(str(args.work_dir or ROOT_DIR)).resolve()
    if not work_dir.exists():
        raise SystemExit(f"Work dir does not exist: {work_dir}")

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _profile_lock():
        profile = _load_profile()
        if mode == "wire":
            prompt = ""
            updated_profile = dict(profile)
        else:
            prompt, updated_profile = _compose_prompt(raw_prompt, profile, force_full=bool(args.force_full_prompt))
        _save_profile(updated_profile)
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    share_dir = _job_share_dir(job_id)
    share_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    exit_code_path = job_dir / "exit_code.txt"
    prompt_path = job_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    windows_dir = _windows_workdir(work_dir)
    if mode == "wire":
        command = [str(kimi_cli), "--work-dir", windows_dir, "--wire"]
    else:
        command = [str(kimi_cli), "--work-dir", windows_dir, "--quiet", "--prompt", prompt]
    env = dict(os.environ)
    env["KIMI_SHARE_DIR"] = _windows_workdir(share_dir)
    proc = _launch_process(
        command,
        cwd=str(work_dir),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        exit_code_path=exit_code_path,
        env=env,
    )

    meta = {
        "job_id": job_id,
        "pid": proc.pid,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "work_dir": str(work_dir),
        "work_dir_windows": windows_dir,
        "kimi_cli": str(kimi_cli),
        "profile_path": str(PROFILE_PATH),
        "profile_turn": int(updated_profile.get("turn_count") or 0),
        "profile_refresh_every": int(updated_profile.get("refresh_every") or DEFAULT_REFRESH_EVERY),
        "mode": mode,
        "used_full_prompt": bool(
            bool(args.force_full_prompt)
            or int(profile.get("turn_count") or 0) == 0
            or (int(updated_profile.get("turn_count") or 1) - 1) % max(1, int(updated_profile.get("refresh_every") or DEFAULT_REFRESH_EVERY)) == 0
        ),
        "prompt_path": str(prompt_path),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "share_dir": str(share_dir),
    }
    _write_metadata(job_dir, meta)
    return {"job_id": job_id, "pid": proc.pid, "status": "running"}


def cmd_run(args: argparse.Namespace) -> int:
    start_info = _start_job(args)
    wait_args = argparse.Namespace(
        job_id=start_info["job_id"],
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )
    return cmd_wait(wait_args)


def cmd_tail(args: argparse.Namespace) -> int:
    status = _job_status(args.job_id)
    payload = {
        "job_id": status["job_id"],
        "status": status["status"],
        "stdout_tail": _tail_text(Path(status["stdout_log"]), args.lines),
        "stderr_tail": _tail_text(Path(status["stderr_log"]), args.lines),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, Any]] = []
    for meta_path in sorted(JOBS_DIR.glob("*/metadata.json"), reverse=True):
        job_id = meta_path.parent.name
        try:
            jobs.append(_job_status(job_id))
        except Exception:
            continue
    print(json.dumps({"jobs": jobs}, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    recent = max(1, int(args.recent)) if args.recent is not None else 20
    tail_lines = max(0, int(args.tail_lines)) if args.tail_lines is not None else 5
    flag_anomalies = bool(args.flag_anomalies)
    include_all = bool(args.all)

    meta_paths = sorted(JOBS_DIR.glob("*/metadata.json"), reverse=True)
    entries: list[dict[str, Any]] = []
    for meta_path in meta_paths:
        job_id = meta_path.parent.name
        try:
            status = _job_status(job_id)
        except Exception:
            continue
        if not include_all and status["status"] != "finished":
            continue
        stdout_text = "\n".join(status["stdout_tail"])
        stderr_text = "\n".join(status["stderr_tail"])
        combined = f"{stdout_text}\n{stderr_text}"
        flagged, hits = _flag_anomalies(combined) if flag_anomalies else (False, [])
        meta = _read_metadata(job_id)
        prompt_path = meta.get("prompt_path", "")
        entry = {
            "job_id": job_id,
            "status": status["status"],
            "exit_code": status["exit_code"],
            "started_at": status["started_at"],
            "prompt_path": prompt_path,
            "stdout_tail": _tail_text(Path(status["stdout_log"]), tail_lines),
            "stderr_tail": _tail_text(Path(status["stderr_log"]), tail_lines),
            "flagged": flagged,
            "flag_hits": hits,
        }
        entries.append(entry)
        if len(entries) >= recent:
            break

    report = {
        "total_considered": len(entries),
        "recent_limit": recent,
        "tail_lines": tail_lines,
        "flagged_count": sum(1 for e in entries if e["flagged"]),
        "jobs": entries,
    }
    print(json.dumps(report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track long-running Kimi CLI jobs.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a tracked Kimi job.")
    start.add_argument("--prompt", help="Inline prompt text.")
    start.add_argument("--prompt-file", help="Path to a prompt file.")
    start.add_argument("--work-dir", help="Workspace directory for Kimi. Defaults to repo root.")
    start.add_argument("--kimi-cli", help="Path to kimi.exe.")
    start.add_argument("--mode", choices=KIMI_MODES, default="print", help="Kimi runtime mode to use.")
    start.add_argument("--force-full-prompt", action="store_true", help="Inject the full profile prompt on this job.")
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="Show tracked job status.")
    status.add_argument("job_id")
    status.set_defaults(func=cmd_status)

    wait = sub.add_parser("wait", help="Wait until a tracked job finishes.")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=float, default=1800.0, help="Max wait time in seconds.")
    wait.add_argument("--poll-interval", type=float, default=2.0, help="Poll interval in seconds.")
    wait.set_defaults(func=cmd_wait)

    watch = sub.add_parser("watch", help="Alias for wait.")
    watch.add_argument("job_id")
    watch.add_argument("--timeout", type=float, default=1800.0, help="Max wait time in seconds.")
    watch.add_argument("--poll-interval", type=float, default=2.0, help="Poll interval in seconds.")
    watch.set_defaults(func=cmd_wait)

    tail = sub.add_parser("tail", help="Show the latest job log lines.")
    tail.add_argument("job_id")
    tail.add_argument("--lines", type=int, default=20)
    tail.set_defaults(func=cmd_tail)

    listing = sub.add_parser("list", help="List tracked jobs.")
    listing.set_defaults(func=cmd_list)

    report = sub.add_parser("report", help="Summarize recent finished Kimi jobs.")
    report.add_argument("--recent", type=int, default=20, help="Limit to N most recent jobs.")
    report.add_argument("--tail-lines", type=int, default=5, help="Number of log lines to include per stream.")
    report.add_argument("--flag-anomalies", action="store_true", default=True, help="Flag jobs whose output may mention anomalies.")
    report.add_argument("--no-flag-anomalies", action="store_false", dest="flag_anomalies", help="Disable anomaly flagging.")
    report.add_argument("--all", action="store_true", help="Include running/unknown jobs, not just finished.")
    report.set_defaults(func=cmd_report)

    profile = sub.add_parser("profile", help="Show or update the persistent prompt profile.")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)

    profile_show = profile_sub.add_parser("show", help="Show current tracker profile.")
    profile_show.set_defaults(func=cmd_profile_show)

    profile_set = profile_sub.add_parser("set", help="Update current tracker profile.")
    profile_set.add_argument("--system-prompt")
    profile_set.add_argument("--system-prompt-file")
    profile_set.add_argument("--reminder-prompt")
    profile_set.add_argument("--reminder-prompt-file")
    profile_set.add_argument("--refresh-every", type=int)
    profile_set.add_argument("--reset-turns", action="store_true")
    profile_set.set_defaults(func=cmd_profile_set)

    run = sub.add_parser("run", help="Start a tracked Kimi job and wait for completion.")
    run.add_argument("--prompt", help="Inline prompt text.")
    run.add_argument("--prompt-file", help="Path to a prompt file.")
    run.add_argument("--work-dir", help="Workspace directory for Kimi. Defaults to repo root.")
    run.add_argument("--kimi-cli", help="Path to kimi.exe.")
    run.add_argument("--mode", choices=KIMI_MODES, default="print", help="Kimi runtime mode to use.")
    run.add_argument("--force-full-prompt", action="store_true", help="Inject the full profile prompt on this job.")
    run.add_argument("--timeout", type=float, default=1800.0, help="Max wait time in seconds.")
    run.add_argument("--poll-interval", type=float, default=2.0, help="Poll interval in seconds.")
    run.set_defaults(func=cmd_run)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
