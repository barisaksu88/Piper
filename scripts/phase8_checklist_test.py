#!/usr/bin/env python3
"""Phase 8 checklist automated test runner.

Exercises the LangGraph orchestrator through every checklist item that can be
automated via the harness.  Interrupt/resume flows (approval, deny, change mind)
are supported by a small PiperHarness subclass that reads the LangGraph interrupt
record and resumes with the user's reply text.

Run from the repository root or from scripts/:
    python scripts/phase8_checklist_test.py [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Ensure repo root is on path when running from scripts/
_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _SCRIPT_DIR.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

# Must set env vars BEFORE config is imported for the first time
os.environ["PIPER_USE_LANGGRAPH_ORCHESTRATOR"] = "true"
# Mirror the public Phase 8 checklist docs: the user enables the graph
# orchestrator flag, but does not need to know about the alternate
# runtime-specific gate used by deeper LangGraph harnesses.
os.environ.pop("PIPER_LANGGRAPH_RUNTIME_ENABLED", None)
os.environ["PIPER_DEBUG_LANGGRAPH_TRACE"] = "true"

from config import CFG, data_state_path
from core.orchestrator_graph import (
    load_langgraph_interrupt_record,
    clear_langgraph_interrupt_record,
)
from AGENTS.harness.session import PiperHarness

# ---------------------------------------------------------------------------
# Graph-aware harness wrapper
# ---------------------------------------------------------------------------

class GraphAwarePiperHarness(PiperHarness):
    """PiperHarness that can resume a LangGraph interrupt turn."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pending_interrupt: dict[str, Any] | None = None

    def send_text(
        self,
        text: str,
        *,
        timeout_s: float = 180.0,
        idle_grace_s: float = 0.75,
    ) -> Any:
        # If we have a pending graph interrupt, resume instead of normal flow.
        if self._pending_interrupt:
            return self._resume_interrupt(text, timeout_s=timeout_s, idle_grace_s=idle_grace_s)
        result = super().send_text(text, timeout_s=timeout_s, idle_grace_s=idle_grace_s)
        self._detect_interrupt()
        return result

    def _detect_interrupt(self) -> None:
        """Check disk for a pending LangGraph interrupt record."""
        record = load_langgraph_interrupt_record(path=CFG.LANGGRAPH_INTERRUPT_PATH)
        if record and str(record.get("status", "")).strip().lower() == "pending":
            self._pending_interrupt = dict(record)
        else:
            self._pending_interrupt = None

    def _resume_interrupt(
        self,
        text: str,
        *,
        timeout_s: float,
        idle_grace_s: float,
    ) -> Any:
        """Resume the graph from a pending interrupt with *text* as the reply."""
        record = self._pending_interrupt
        self._pending_interrupt = None

        thread_id = str(record.get("thread_id", "") if record else "").strip()
        checkpoint_id = str(record.get("checkpoint_id", "") if record else "").strip()

        if not thread_id:
            # Fallback to normal send if we lost the record
            return super().send_text(text, timeout_s=timeout_s, idle_grace_s=idle_grace_s)

        start_time = time.monotonic()
        msg_start = len(self.chat_state.get_messages_snapshot())
        event_start = len(self._events)
        utterance_start = len(self.tts.utterances)
        tts_event_start = len(self.tts.events)
        status_start = len(self._statuses)
        image_start = len(self._images)

        self._start_resume(thread_id, checkpoint_id, text)
        timed_out = not self._wait_for_idle(timeout_s=timeout_s, idle_grace_s=idle_grace_s)

        # Clean the interrupt record so it is not picked up again
        clear_langgraph_interrupt_record(thread_id=thread_id)
        self._detect_interrupt()  # in case a new interrupt was raised

        snapshot = self.chat_state.get_messages_snapshot()
        new_messages = snapshot[msg_start:]
        assistant_messages = [m for m in new_messages if m.get("role") == "assistant"]
        if not assistant_messages:
            latest_user_idx = -1
            for idx in range(len(snapshot) - 1, -1, -1):
                message = snapshot[idx]
                if message.get("role") == "user":
                    latest_user_idx = idx
                    break
            if latest_user_idx >= 0:
                new_messages = snapshot[latest_user_idx + 1 :]
                assistant_messages = [m for m in new_messages if m.get("role") == "assistant"]
        assistant_text = assistant_messages[-1]["content"] if assistant_messages else ""

        system_messages = [
            str(m.get("content", ""))
            for m in new_messages
            if m.get("role") == "system" and not m.get("hidden")
        ]

        from AGENTS.harness.session import HarnessEvent
        return type(
            "HarnessTurnResult",
            (),
            {
                "user_text": f"[RESUME] {text}",
                "assistant_text": assistant_text,
                "messages": new_messages,
                "system_messages": system_messages,
                "tts_utterances": self.tts.snapshot_utterances(utterance_start),
                "tts_events": self.tts.snapshot_events(tts_event_start),
                "ui_events": [asdict(event) for event in self._events[event_start:]],
                "status_history": list(self._statuses[status_start:]),
                "images": list(self._images[image_start:]),
                "timed_out": timed_out,
                "duration_s": round(time.monotonic() - start_time, 3),
            },
        )()

    def _start_resume(self, thread_id: str, checkpoint_id: str, resume_value: str) -> None:
        from core.orchestrator import OrchestratorConfig, run_agent_loop
        from core.orchestrator_graph import _checkpoint_config
        from core.graph_nodes import PiperState

        with self._active_lock:
            self._active_runs += 1
        self._last_activity = time.monotonic()

        def _run() -> None:
            try:
                orc_cfg = OrchestratorConfig(
                    llm=self.llm,
                    brain=self.agent_brain,
                    knowledge=self.knowledge_mgr,
                    prompt_context=self.prompt_context_service,
                    chat=self.chat_state,
                    styles=self.style_mgr,
                    pipeline=self.pipeline,
                    ui=self.ui_queue,
                    get_context=self.chat_state.for_model,
                    boot=self.boot_mgr,
                    img_gen=self.img_gen,
                    conversation_summary_path=self.data_dir / "state" / "conversation_summary.json",
                    is_search_in_flight=self.is_search_in_flight,
                    retain_search_in_flight=self.retain_search_in_flight,
                    release_search_in_flight=self.release_search_in_flight,
                    current_search_query=self.current_search_query,
                    langgraph_resume_thread_id=thread_id,
                    langgraph_resume_checkpoint_id=checkpoint_id,
                    langgraph_resume_value=resume_value,
                )
                run_agent_loop(orc_cfg)
            except Exception as exc:
                self.ui_queue.put(("error", f"Harness Resume Error: {exc}"))
            finally:
                self.agent_brain.suspend_runtime_sessions()
                self.ui_queue.put(("status", "IDLE"))
                with self._active_lock:
                    self._active_runs -= 1
                self._last_activity = time.monotonic()

        threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Test dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChecklistTestResult:
    name: str
    passed: bool
    timed_out: bool
    duration_s: float
    assistant_text: str
    status_history: list[str]
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class ChecklistReport:
    ready: bool
    success: bool
    data_dir: str
    tests: list[ChecklistTestResult]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")
    # Stale interrupt/recovery records from live data can leak into isolated runs
    for stale in ("langgraph_interrupt.json", "langgraph_recovery.json"):
        p = data_dir / "state" / stale
        if p.exists():
            p.unlink()


def _reset_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("test_hello.txt", "old_name.txt", "new_name.txt", "demo_folder"):
        p = workspace / name
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def _file_content(workspace: Path, rel_path: str) -> str | None:
    p = workspace / rel_path
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return None


def _file_exists(workspace: Path, rel_path: str) -> bool:
    return (workspace / rel_path).is_file()


def _has_manager_work(status_history: list[str]) -> bool:
    return any("Working" in s or "MANAGER" in s or "manager" in s.lower() for s in status_history)


def _has_error_status(status_history: list[str]) -> bool:
    return any("Error" in s for s in status_history)


def _send_turn(
    harness: GraphAwarePiperHarness,
    text: str,
    *,
    timeout: float = 180.0,
) -> tuple[Any, bool, str]:
    """Send one turn and return (result, passed, reason)."""
    try:
        result = harness.send_text(text, timeout_s=timeout)
        if result.timed_out:
            return result, False, "timed_out"
        if _has_error_status(result.status_history):
            return result, False, "error_status"
        return result, True, ""
    except Exception as exc:
        dummy = type(
            "DummyResult",
            (),
            {
                "assistant_text": "",
                "status_history": [],
                "timed_out": False,
                "duration_s": 0.0,
            },
        )()
        return dummy, False, f"exception: {exc}"


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def _test_chat(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    text = "What is the difference between a list and a tuple in Python?"
    # Snapshot workspace files before turn
    before_files = {p.name for p in workspace.iterdir() if p.is_file()}
    result, passed, reason = _send_turn(harness, text, timeout=timeout)
    after_files = {p.name for p in workspace.iterdir() if p.is_file()}
    new_files = after_files - before_files
    passed = passed and not new_files and len(result.assistant_text) > 20
    return ChecklistTestResult(
        name="test_1_chat",
        passed=passed,
        timed_out=result.timed_out,
        duration_s=result.duration_s,
        assistant_text=result.assistant_text,
        status_history=list(result.status_history),
        details={"reason": reason, "new_files": list(new_files)},
    )


def _test_search(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    text = "Search my notes for anything about deployment."
    result, passed, reason = _send_turn(harness, text, timeout=timeout)
    # Should return a conversational summary, not create files
    wrote_file = "created" in result.assistant_text.lower() or "wrote" in result.assistant_text.lower()
    passed = passed and not wrote_file and len(result.assistant_text) > 10
    return ChecklistTestResult(
        name="test_2_search",
        passed=passed,
        timed_out=result.timed_out,
        duration_s=result.duration_s,
        assistant_text=result.assistant_text,
        status_history=list(result.status_history),
        details={"reason": reason, "wrote_file": wrote_file},
    )


def _test_file_creation(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    text = 'Create a file called test_hello.txt in my workspace with the text "Hello World"'
    result, passed, reason = _send_turn(harness, text, timeout=timeout)
    content = _file_content(workspace, "test_hello.txt")
    has_content = content is not None and "Hello World" in content
    passed = passed and has_content
    return ChecklistTestResult(
        name="test_3_file_creation",
        passed=passed,
        timed_out=result.timed_out,
        duration_s=result.duration_s,
        assistant_text=result.assistant_text,
        status_history=list(result.status_history),
        details={"reason": reason, "file_content": content},
    )


def _test_file_edit(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    text = 'Add a line "Goodbye World" to the end of test_hello.txt'
    result, passed, reason = _send_turn(harness, text, timeout=timeout)
    content = _file_content(workspace, "test_hello.txt")
    has_both = content is not None and "Hello World" in content and "Goodbye World" in content
    passed = passed and has_both
    return ChecklistTestResult(
        name="test_4_file_edit",
        passed=passed,
        timed_out=result.timed_out,
        duration_s=result.duration_s,
        assistant_text=result.assistant_text,
        status_history=list(result.status_history),
        details={"reason": reason, "file_content": content},
    )


def _test_approval_approve(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    # Ensure file exists
    test_file = workspace / "test_hello.txt"
    test_file.write_text("Hello World\nGoodbye World\n", encoding="utf-8")

    text = "Delete the file test_hello.txt"
    result, passed, reason = _send_turn(harness, text, timeout=timeout)

    # Did we get an interrupt?
    has_interrupt = harness._pending_interrupt is not None
    interrupt_kind = ""
    if has_interrupt:
        interrupt_kind = str(harness._pending_interrupt.get("interrupt_payload", {}).get("kind", ""))

    if not has_interrupt:
        # If no interrupt, maybe it deleted directly (which is a failure for this test)
        deleted = not _file_exists(workspace, "test_hello.txt")
        passed = False
        return ChecklistTestResult(
            name="test_5_approval_approve",
            passed=False,
            timed_out=result.timed_out,
            duration_s=result.duration_s,
            assistant_text=result.assistant_text,
            status_history=list(result.status_history),
            details={"reason": "no_interrupt_raised" if not deleted else "deleted_without_approval", "interrupt_kind": interrupt_kind},
        )

    # Send approval resume
    resume_result, resume_passed, resume_reason = _send_turn(harness, "yes", timeout=timeout)
    deleted = not _file_exists(workspace, "test_hello.txt")
    passed = resume_passed and deleted
    combined_text = f"{result.assistant_text}\n[RESUME]\n{resume_result.assistant_text}"
    return ChecklistTestResult(
        name="test_5_approval_approve",
        passed=passed,
        timed_out=result.timed_out or resume_result.timed_out,
        duration_s=round(result.duration_s + resume_result.duration_s, 3),
        assistant_text=combined_text,
        status_history=list(result.status_history) + list(resume_result.status_history),
        details={"reason": resume_reason, "interrupt_kind": interrupt_kind, "deleted": deleted},
    )


def _test_approval_deny(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    # Ensure file exists
    test_file = workspace / "test_hello.txt"
    test_file.write_text("test content\n", encoding="utf-8")

    text = "Delete the file test_hello.txt"
    result, passed, reason = _send_turn(harness, text, timeout=timeout)

    has_interrupt = harness._pending_interrupt is not None
    interrupt_kind = ""
    if has_interrupt:
        interrupt_kind = str(harness._pending_interrupt.get("interrupt_payload", {}).get("kind", ""))

    if not has_interrupt:
        deleted = not _file_exists(workspace, "test_hello.txt")
        return ChecklistTestResult(
            name="test_6_approval_deny",
            passed=False,
            timed_out=result.timed_out,
            duration_s=result.duration_s,
            assistant_text=result.assistant_text,
            status_history=list(result.status_history),
            details={"reason": "no_interrupt_raised", "interrupt_kind": interrupt_kind, "deleted": deleted},
        )

    resume_result, resume_passed, resume_reason = _send_turn(harness, "no", timeout=timeout)
    still_exists = _file_exists(workspace, "test_hello.txt")
    passed = resume_passed and still_exists
    combined_text = f"{result.assistant_text}\n[RESUME]\n{resume_result.assistant_text}"
    return ChecklistTestResult(
        name="test_6_approval_deny",
        passed=passed,
        timed_out=result.timed_out or resume_result.timed_out,
        duration_s=round(result.duration_s + resume_result.duration_s, 3),
        assistant_text=combined_text,
        status_history=list(result.status_history) + list(resume_result.status_history),
        details={"reason": resume_reason, "interrupt_kind": interrupt_kind, "still_exists": still_exists},
    )


def _test_change_mind(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    # Ensure old file doesn't exist
    old_file = workspace / "old_name.txt"
    new_file = workspace / "new_name.txt"
    if old_file.exists():
        old_file.unlink()
    if new_file.exists():
        new_file.unlink()

    text = 'Create a file called old_name.txt with "content"'
    result, passed, reason = _send_turn(harness, text, timeout=timeout)

    has_interrupt = harness._pending_interrupt is not None
    if has_interrupt:
        # Resume with changed target
        resume_result, resume_passed, resume_reason = _send_turn(
            harness, "Actually, name it new_name.txt instead", timeout=timeout
        )
        new_exists = _file_exists(workspace, "new_name.txt")
        old_exists = _file_exists(workspace, "old_name.txt")
        passed = resume_passed and new_exists and not old_exists
        combined_text = f"{result.assistant_text}\n[RESUME]\n{resume_result.assistant_text}"
        return ChecklistTestResult(
            name="test_7_change_mind",
            passed=passed,
            timed_out=result.timed_out or resume_result.timed_out,
            duration_s=round(result.duration_s + resume_result.duration_s, 3),
            assistant_text=combined_text,
            status_history=list(result.status_history) + list(resume_result.status_history),
            details={"new_exists": new_exists, "old_exists": old_exists},
        )
    else:
        # No interrupt was raised — interpret as normal creation + follow-up turn
        old_exists = _file_exists(workspace, "old_name.txt")
        if old_exists:
            # Send correction as a new turn
            corr_result, corr_passed, corr_reason = _send_turn(
                harness, "Actually, name it new_name.txt instead", timeout=timeout
            )
            new_exists = _file_exists(workspace, "new_name.txt")
            old_exists_after = _file_exists(workspace, "old_name.txt")
            passed = corr_passed and new_exists and not old_exists_after
            combined_text = f"{result.assistant_text}\n[FOLLOWUP]\n{corr_result.assistant_text}"
            return ChecklistTestResult(
                name="test_7_change_mind",
                passed=passed,
                timed_out=result.timed_out or corr_result.timed_out,
                duration_s=round(result.duration_s + corr_result.duration_s, 3),
                assistant_text=combined_text,
                status_history=list(result.status_history) + list(corr_result.status_history),
                details={"new_exists": new_exists, "old_exists": old_exists_after, "no_interrupt": True},
            )
        else:
            # Something else happened
            return ChecklistTestResult(
                name="test_7_change_mind",
                passed=False,
                timed_out=result.timed_out,
                duration_s=result.duration_s,
                assistant_text=result.assistant_text,
                status_history=list(result.status_history),
                details={"reason": "neither_interrupt_nor_creation", "old_exists": old_exists},
            )


def _test_memory(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    # Turn 1: state memory
    r1, p1, reason1 = _send_turn(harness, "My favorite color is blue.", timeout=timeout)
    # Turn 2: recall
    r2, p2, reason2 = _send_turn(harness, "What is my favorite color?", timeout=timeout)
    remembers = "blue" in r2.assistant_text.lower()
    passed = p1 and p2 and remembers
    return ChecklistTestResult(
        name="test_8_memory",
        passed=passed,
        timed_out=r1.timed_out or r2.timed_out,
        duration_s=round(r1.duration_s + r2.duration_s, 3),
        assistant_text=f"T1: {r1.assistant_text}\nT2: {r2.assistant_text}",
        status_history=list(r1.status_history) + list(r2.status_history),
        details={"remembers_blue": remembers, "reason1": reason1, "reason2": reason2},
    )


def _test_complex_task(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    text = 'Create a folder called demo_folder, create a file inside it called readme.md with "# Demo", and then list the contents of demo_folder'
    result, passed, reason = _send_turn(harness, text, timeout=timeout)
    readme_content = _file_content(workspace, Path("demo_folder") / "readme.md")
    has_readme = readme_content is not None and "# Demo" in readme_content
    passed = passed and has_readme
    return ChecklistTestResult(
        name="test_10_complex_task",
        passed=passed,
        timed_out=result.timed_out,
        duration_s=result.duration_s,
        assistant_text=result.assistant_text,
        status_history=list(result.status_history),
        details={"reason": reason, "readme_content": readme_content},
    )


def _test_edge_case(harness: GraphAwarePiperHarness, workspace: Path, timeout: float) -> ChecklistTestResult:
    text = "asdfghjkl"
    result, passed, reason = _send_turn(harness, text, timeout=timeout)
    # Should not crash and should not create files
    created_any = any(
        (workspace / name).exists()
        for name in ("asdfghjkl.txt", "asdfghjkl.md", "output.txt")
    )
    passed = passed and not created_any and len(result.assistant_text) > 0
    return ChecklistTestResult(
        name="test_11_edge_case",
        passed=passed,
        timed_out=result.timed_out,
        duration_s=result.duration_s,
        assistant_text=result.assistant_text,
        status_history=list(result.status_history),
        details={"reason": reason, "created_any": created_any},
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TEST_FUNCTIONS = [
    _test_chat,
    _test_search,
    _test_file_creation,
    _test_file_edit,
    _test_approval_approve,
    _test_approval_deny,
    _test_change_mind,
    _test_memory,
    _test_complex_task,
    _test_edge_case,
]


def run_checklist(*, timeout: float, keep_data_copy: bool) -> ChecklistReport:
    harness = GraphAwarePiperHarness(
        isolated_data=True,
        keep_data_copy=keep_data_copy,
        enable_memory_learning=True,
    )
    _clear_isolated_chat_memory(harness.data_dir)
    workspace = harness.data_dir / "workspace"
    _reset_workspace(workspace)
    boot = harness.start()
    tests: list[ChecklistTestResult] = []
    try:
        for fn in TEST_FUNCTIONS:
            print(f"Running {fn.__name__} ...", flush=True)
            try:
                result = fn(harness, workspace, timeout)
            except Exception as exc:
                result = ChecklistTestResult(
                    name=fn.__name__,
                    passed=False,
                    timed_out=False,
                    duration_s=0.0,
                    assistant_text="",
                    status_history=[],
                    details={},
                    error=str(exc),
                )
            tests.append(result)
            print(f"  {'PASS' if result.passed else 'FAIL'}  ({result.duration_s}s)  {result.assistant_text[:80]!r}", flush=True)
    finally:
        harness.close()

    return ChecklistReport(
        ready=bool(boot.ready),
        success=bool(boot.ready) and all(t.passed for t in tests),
        data_dir=str(harness.data_dir),
        tests=tests,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Piper Phase 8 LangGraph checklist.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()
    report = run_checklist(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"DATA_DIR: {report.data_dir}")
        for test in report.tests:
            print(f"{test.name}: passed={test.passed} timed_out={test.timed_out} duration_s={test.duration_s}")
            if test.error:
                print(f"  ERROR: {test.error}")
            print(f"  assistant={test.assistant_text[:120]!r}")
            if test.details:
                print(f"  details={test.details}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
