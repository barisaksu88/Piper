from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.contracts import StageOutcomePack  # noqa: E402
from core.services.verification import VerificationResult  # noqa: E402
from core.orchestrator_graph import (  # noqa: E402
    OrchestratorGraphContext,
    build_orchestrator_graph_runtime,
    clear_langgraph_recovery_record,
    load_langgraph_recovery_record,
    run_agent_loop_with_langgraph,
    save_langgraph_recovery_record,
    snapshot_orchestrator_state,
)
from core.orchestrator import OrchestratorConfig  # noqa: E402
from scripts.langgraph_test_fixtures import BaseDummyOrchestrator  # noqa: E402


class BaseRecoveryOrchestrator(BaseDummyOrchestrator):
    def __init__(self) -> None:
        super().__init__(turn_id="checkpoint-recovery-smoke", user_msg="Say hello through the graph.")
        self.last_stage_outcome = StageOutcomePack(
            status="PARTIAL",
            detail="Recovery smoke in progress",
            effective_success=False,
            state_owner="",
            mutation_kind="",
            auto_reroute=False,
            reroute_reason="",
            allow_persona_reroute=True,
        )
        self.last_verification = VerificationResult.partial("Recovery smoke in progress.", retry_budget=1)
        self.latest_search_failed = False
        self.latest_search_error = ""
        self.synthetic_user_turn = False
        self.is_search_result = False
        self.pending_file_target_confirmation = None
        self.calls: list[str] = []

    def dispatch_stage(self, stage_name: str) -> None:
        self.calls.append(stage_name)
        if stage_name == "ROUTE":
            self.route_decision = {"decision": "CHAT"}
            self.scratchpad.append("ROUTE: checkpointed")
            self.next_stage = "PERSONA"
            return
        if stage_name == "PERSONA":
            self.scratchpad.append("PERSONA: recovered")
            self.next_stage = "FINISHED"
            return
        raise RuntimeError(f"Unexpected recovery smoke stage: {stage_name}")


class FailingPersonaOrchestrator(BaseRecoveryOrchestrator):
    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "PERSONA":
            self.calls.append(stage_name)
            raise RuntimeError("intentional-persona-crash")
        super().dispatch_stage(stage_name)


class ResumingOrchestrator(BaseRecoveryOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        # This must be replaced by checkpoint state during resume.
        self.next_stage = "SHOULD_NOT_RUN"


def _count_rows(path: Path, table_name: str) -> int:
    connection = sqlite3.connect(str(path))
    try:
        return int(connection.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0])
    finally:
        connection.close()


def run_recovery_smoke() -> dict:
    thread_id = "checkpoint-recovery-smoke"
    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "langgraph_recovery.sqlite"

        runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        failing = FailingPersonaOrchestrator()
        first_error = ""
        try:
            try:
                runtime.graph.invoke(
                    snapshot_orchestrator_state(failing, stage_trace=[]),
                    config={"configurable": {"thread_id": thread_id}},
                    context=OrchestratorGraphContext(orchestrator=failing),
                )
            except RuntimeError as exc:
                first_error = str(exc)
            snapshot_after_failure = runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            runtime.close()

        resumed_runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        resumed = ResumingOrchestrator()
        try:
            result = resumed_runtime.graph.invoke(
                None,
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=resumed),
            )
            snapshot_after_resume = resumed_runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
            pruned = resumed_runtime.prune_checkpoints()
        finally:
            resumed_runtime.close()

        stage_trace = list(result.get("stage_trace") or [])
        failure_next = tuple(getattr(snapshot_after_failure, "next", ()) or ())
        success = bool(
            first_error == "intentional-persona-crash"
            and failing.calls == ["ROUTE", "PERSONA"]
            and resumed.calls == ["PERSONA"]
            and stage_trace == ["ROUTE", "PERSONA"]
            and result.get("next_stage") == "FINISHED"
            and failure_next == ("persona",)
        )
        return {
            "success": success,
            "thread_id": thread_id,
            "checkpoint_path": str(checkpoint_path),
            "first_error": first_error,
            "first_run_calls": failing.calls,
            "failure_checkpoint_next": list(failure_next),
            "failure_checkpoint_stage_trace": list((snapshot_after_failure.values or {}).get("stage_trace") or []),
            "resume_calls": resumed.calls,
            "final_stage_trace": stage_trace,
            "final_next_stage": result.get("next_stage"),
            "final_scratchpad": result.get("scratchpad") or [],
            "final_checkpoint_next": list(getattr(snapshot_after_resume, "next", ()) or ()),
            "checkpoint_count": _count_rows(checkpoint_path, "checkpoints"),
            "write_count": _count_rows(checkpoint_path, "writes"),
            "pruned": pruned,
            "verification": asdict(resumed.last_verification) if resumed.last_verification else None,
        }


class EntryPointResumeOrchestrator(BaseRecoveryOrchestrator):
    instances: list["EntryPointResumeOrchestrator"] = []

    def __init__(self, _cfg) -> None:
        super().__init__()
        self.next_stage = "SHOULD_BE_RESTORED"
        self._turn_stats_recorded = False
        EntryPointResumeOrchestrator.instances.append(self)

    def prepare_turn(self) -> None:
        self.turn_stats = SimpleNamespace(turn_id="entrypoint-pre-resume")

    def _record_turn_stats_if_ready(self, *, aborted: bool = False, detail: str = "", phase: str = "") -> None:
        self._turn_stats_recorded = True

    def _log_dashboard(self, text: str) -> None:
        self.ui.put(("status_widget_dashboard_activity", text))


def run_entrypoint_resume_smoke() -> dict:
    import core.orchestrator_graph as graph_module

    thread_id = "entrypoint-recovery-smoke"
    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "entrypoint_recovery.sqlite"
        recovery_path = Path(tmp_dir) / "entrypoint_recovery.json"

        runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        failing = FailingPersonaOrchestrator()
        try:
            try:
                runtime.graph.invoke(
                    snapshot_orchestrator_state(failing, stage_trace=[]),
                    config={"configurable": {"thread_id": thread_id}},
                    context=OrchestratorGraphContext(orchestrator=failing),
                )
            except RuntimeError:
                pass
            snapshot_after_failure = runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            runtime.close()

        checkpoint_id = str(
            ((getattr(snapshot_after_failure, "config", {}) or {}).get("configurable") or {}).get("checkpoint_id")
            or ""
        )
        save_langgraph_recovery_record(
            {
                "schema": 1,
                "status": "failed",
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "checkpoint_next": list(getattr(snapshot_after_failure, "next", ()) or ()),
                "stage_trace": list((snapshot_after_failure.values or {}).get("stage_trace") or []),
                "user_msg": "entrypoint resume",
                "error": "intentional-persona-crash",
            },
            path=recovery_path,
        )

        old_checkpoint_path = os.environ.get("PIPER_LANGGRAPH_CHECKPOINT_PATH")
        old_recovery_path = os.environ.get("PIPER_LANGGRAPH_RECOVERY_PATH")
        old_orchestrator = graph_module.Orchestrator
        EntryPointResumeOrchestrator.instances = []
        os.environ["PIPER_LANGGRAPH_CHECKPOINT_PATH"] = str(checkpoint_path)
        os.environ["PIPER_LANGGRAPH_RECOVERY_PATH"] = str(recovery_path)
        graph_module.Orchestrator = EntryPointResumeOrchestrator
        try:
            cfg = OrchestratorConfig(
                llm=None,
                brain=None,
                knowledge=None,
                prompt_context=None,
                chat=None,
                styles=None,
                pipeline=None,
                ui=None,
                get_context=lambda: [],
                boot=None,
                img_gen=None,
                langgraph_resume_thread_id=thread_id,
                langgraph_resume_checkpoint_id=checkpoint_id,
            )
            run_agent_loop_with_langgraph(cfg)
        finally:
            graph_module.Orchestrator = old_orchestrator
            if old_checkpoint_path is None:
                os.environ.pop("PIPER_LANGGRAPH_CHECKPOINT_PATH", None)
            else:
                os.environ["PIPER_LANGGRAPH_CHECKPOINT_PATH"] = old_checkpoint_path
            if old_recovery_path is None:
                os.environ.pop("PIPER_LANGGRAPH_RECOVERY_PATH", None)
            else:
                os.environ["PIPER_LANGGRAPH_RECOVERY_PATH"] = old_recovery_path

        instance = EntryPointResumeOrchestrator.instances[-1] if EntryPointResumeOrchestrator.instances else None
        recovery_after = load_langgraph_recovery_record(path=recovery_path)
        cleared_again = clear_langgraph_recovery_record(path=recovery_path, thread_id=thread_id)
        success = bool(
            instance is not None
            and instance.calls == ["PERSONA"]
            and instance.next_stage == "FINISHED"
            and getattr(instance.turn_stats, "turn_id", "") == thread_id
            and instance._turn_stats_recorded
            and not recovery_after
            and not cleared_again
        )
        return {
            "success": success,
            "calls": list(instance.calls if instance else []),
            "next_stage": getattr(instance, "next_stage", ""),
            "turn_id": getattr(getattr(instance, "turn_stats", None), "turn_id", ""),
            "stats_recorded": bool(getattr(instance, "_turn_stats_recorded", False)),
            "recovery_after": recovery_after,
            "cleared_again": bool(cleared_again),
        }


def main() -> int:
    low_level = run_recovery_smoke()
    entrypoint = run_entrypoint_resume_smoke()
    report = {
        "success": bool(low_level.get("success") and entrypoint.get("success")),
        "low_level": low_level,
        "entrypoint": entrypoint,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
