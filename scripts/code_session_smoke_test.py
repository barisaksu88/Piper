from __future__ import annotations

import argparse
import json
import queue
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from core.code_session import EmbeddedCodeSession


@dataclass(frozen=True)
class PhaseReport:
    name: str
    passed: bool
    statuses: list[str]
    active_events: list[bool]
    output: str


@dataclass(frozen=True)
class CodeSessionSmokeReport:
    success: bool
    phases: list[PhaseReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _write_script(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _drain_events(event_queue: "queue.Queue[tuple[str, object]]", sink: list[tuple[str, object]]) -> None:
    while True:
        try:
            sink.append(event_queue.get_nowait())
        except queue.Empty:
            return


def _wait_for(
    event_queue: "queue.Queue[tuple[str, object]]",
    sink: list[tuple[str, object]],
    predicate,
    *,
    timeout: float = 5.0,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _drain_events(event_queue, sink)
        if predicate(sink):
            return True
        time.sleep(0.05)
    _drain_events(event_queue, sink)
    return predicate(sink)


def _phase_statuses(events: list[tuple[str, object]]) -> list[str]:
    return [str(payload) for kind, payload in events if kind == "code_session_status"]


def _phase_active(events: list[tuple[str, object]]) -> list[bool]:
    return [bool(payload) for kind, payload in events if kind == "code_session_active"]


def _phase_output(events: list[tuple[str, object]]) -> str:
    return "".join(str(payload) for kind, payload in events if kind == "code_session_output")


def _interactive_phase(session: EmbeddedCodeSession, event_queue: "queue.Queue[tuple[str, object]]") -> PhaseReport:
    events: list[tuple[str, object]] = []
    session.start_script("echo_game.py")
    prompt_ready = _wait_for(
        event_queue,
        events,
        lambda current: "Enter guess: " in _phase_output(current),
    )
    if prompt_ready:
        session.send_input("1234")
    finished = _wait_for(
        event_queue,
        events,
        lambda current: "Finished: echo_game.py" in _phase_statuses(current) and _phase_active(current)[-1:] == [False],
    )
    statuses = _phase_statuses(events)
    output = _phase_output(events)
    passed = bool(prompt_ready and finished)
    passed = passed and "$ python echo_game.py" in output
    passed = passed and "Welcome" in output
    passed = passed and "Enter guess: " in output
    passed = passed and "1234" in output
    passed = passed and "You said 1234" in output
    passed = passed and "[Process exited with code 0]" in output
    passed = passed and statuses[:2] == ["Running: echo_game.py", "Finished: echo_game.py"]
    return PhaseReport(
        name="interactive_echo",
        passed=passed,
        statuses=statuses,
        active_events=_phase_active(events),
        output=output,
    )


def _rerun_phase(session: EmbeddedCodeSession, event_queue: "queue.Queue[tuple[str, object]]") -> PhaseReport:
    events: list[tuple[str, object]] = []
    session.start_script("blocker.py")
    blocker_ready = _wait_for(
        event_queue,
        events,
        lambda current: "Blocker ready" in _phase_output(current),
    )
    if blocker_ready:
        session.start_script("echo_game.py")
    prompt_ready = _wait_for(
        event_queue,
        events,
        lambda current: "Enter guess: " in _phase_output(current),
    )
    if prompt_ready:
        session.send_input("5678")
    finished = _wait_for(
        event_queue,
        events,
        lambda current: "Finished: echo_game.py" in _phase_statuses(current) and _phase_active(current)[-1:] == [False],
    )
    statuses = _phase_statuses(events)
    active_events = _phase_active(events)
    output = _phase_output(events)
    blocker_exit_noise = any(
        marker in status
        for status in statuses
        for marker in (
            "Stopped: blocker.py",
            "Finished: blocker.py",
            "Exited (",
            "Ended: blocker.py",
        )
    )
    passed = bool(blocker_ready and prompt_ready and finished)
    passed = passed and "$ python blocker.py" in output
    passed = passed and "$ python echo_game.py" in output
    passed = passed and "You said 5678" in output
    passed = passed and statuses == [
        "Running: blocker.py",
        "Running: echo_game.py",
        "Finished: echo_game.py",
    ]
    passed = passed and active_events == [True, False, True, False]
    passed = passed and not blocker_exit_noise
    return PhaseReport(
        name="silent_rerun",
        passed=passed,
        statuses=statuses,
        active_events=active_events,
        output=output,
    )


def run_smoke() -> CodeSessionSmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-code-session-") as tmp_dir:
        workspace = Path(tmp_dir)
        _write_script(
            workspace / "echo_game.py",
            (
                'print("Welcome", flush=True)\n'
                'guess = input("Enter guess: ")\n'
                'print(f"You said {guess}", flush=True)\n'
            ),
        )
        _write_script(
            workspace / "blocker.py",
            (
                'import time\n'
                'print("Blocker ready", flush=True)\n'
                'time.sleep(30)\n'
            ),
        )
        event_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        session = EmbeddedCodeSession(workspace, lambda kind, payload: event_queue.put((kind, payload)))
        try:
            phases = [
                _interactive_phase(session, event_queue),
                _rerun_phase(session, event_queue),
            ]
        finally:
            session.shutdown()
        return CodeSessionSmokeReport(
            success=all(phase.passed for phase in phases),
            phases=phases,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a direct embedded Code-session smoke test.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        for phase in report.phases:
            print(f"{phase.name}: passed={phase.passed}")
            print(f"  statuses={phase.statuses}")
            print(f"  active_events={phase.active_events}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
