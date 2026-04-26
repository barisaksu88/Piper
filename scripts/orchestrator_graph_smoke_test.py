from __future__ import annotations

import json
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
from core.engines.verification import VerificationResult  # noqa: E402
from core.orchestrator_graph import (  # noqa: E402
    OrchestratorGraphContext,
    build_orchestrator_graph,
    build_orchestrator_graph_runtime,
    restore_orchestrator_state,
    snapshot_orchestrator_state,
)


class DummyUi:
    def __init__(self) -> None:
        self.events = []

    def put(self, event) -> None:
        self.events.append(event)


class DummyOrchestrator:
    def __init__(self) -> None:
        self.ui = DummyUi()
        self.turn_stats = SimpleNamespace(turn_id="graph-smoke")
        self.next_stage = "MANAGER"
        self.user_msg = "Refactor the loop."
        self.route_decision = {"decision": "TASK", "card": {"goal": "Refactor the loop."}}
        self.context_card = {"goal": "Refactor the loop."}
        self.scratchpad = ["=== STAGE START ===", "RESULT: PARTIAL"]
        self.ingested_document_chat = False
        self.document_focus_text = ""
        self.document_focus_refs = []
        self.document_focus_sources = []
        self.turn_screen_image_path = None
        self.turn_screen_image_kind = ""
        self.latest_codex_escalation = {"decision": "monitor"}
        self.failed_task_router_retries = 1
        self.last_stage_outcome = StageOutcomePack(
            status="PARTIAL",
            detail="Awaiting verification",
            effective_success=False,
            state_owner="",
            mutation_kind="",
            auto_reroute=False,
            reroute_reason="",
            allow_persona_reroute=True,
        )
        self.last_verification = VerificationResult.partial("Awaiting artifact proof.", retry_budget=1)
        self.route_interceptor = ""
        self.reporter_just_ran = False
        self.latest_search_summary = ""
        self.synthetic_user_turn = False
        self.is_search_result = False
        self.pending_file_target_confirmation = {"question": "Use report.md?"}
        self.pending_stage_pause = {"pause_type": "user_input", "question": "Which report?"}


class RuntimeDummyOrchestrator(DummyOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.next_stage = "ROUTE"
        self.route_decision = {}
        self.context_card = {}
        self.scratchpad = []
        self.pending_file_target_confirmation = None
        self.pending_stage_pause = None

    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "ROUTE":
            self.route_decision = {"decision": "CHAT"}
            self.next_stage = "PERSONA"
            self.scratchpad.append("ROUTE: CHAT")
            return
        if stage_name == "PERSONA":
            self.next_stage = "FINISHED"
            self.scratchpad.append("PERSONA: finished")
            return
        raise RuntimeError(f"Unexpected smoke-test stage: {stage_name}")


def _run_sqlite_checkpoint_smoke() -> dict:
    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "langgraph_checkpoints.sqlite"
        runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=20,
        )
        try:
            dummy = RuntimeDummyOrchestrator()
            initial_state = snapshot_orchestrator_state(dummy, stage_trace=[])
            result = runtime.graph.invoke(
                initial_state,
                config={"configurable": {"thread_id": "graph-smoke"}},
                context=OrchestratorGraphContext(orchestrator=dummy),
            )
            pruned = runtime.prune_checkpoints()
        finally:
            runtime.close()

        connection = sqlite3.connect(str(checkpoint_path))
        try:
            checkpoint_count = int(connection.execute("SELECT count(*) FROM checkpoints").fetchone()[0])
            write_count = int(connection.execute("SELECT count(*) FROM writes").fetchone()[0])
        finally:
            connection.close()

        stage_trace = list(result.get("stage_trace") or [])
        return {
            "ok": bool(
                checkpoint_path.exists()
                and checkpoint_path.stat().st_size > 0
                and checkpoint_count > 0
                and write_count > 0
                and stage_trace == ["ROUTE", "PERSONA"]
                and result.get("next_stage") == "FINISHED"
            ),
            "path_exists": checkpoint_path.exists(),
            "size_bytes": checkpoint_path.stat().st_size if checkpoint_path.exists() else 0,
            "checkpoint_count": checkpoint_count,
            "write_count": write_count,
            "stage_trace": stage_trace,
            "next_stage": result.get("next_stage"),
            "pruned": pruned,
        }


def main() -> int:
    dummy = DummyOrchestrator()
    state = snapshot_orchestrator_state(dummy, stage_trace=["ROUTE", "MANAGER"])

    restored = DummyOrchestrator()
    restored.last_stage_outcome = None
    restored.last_verification = None
    restored.pending_file_target_confirmation = None
    restore_orchestrator_state(restored, state)

    graph_available = True
    graph_error = ""
    try:
        build_orchestrator_graph(with_checkpointer=False)
    except RuntimeError as exc:
        graph_available = False
        graph_error = str(exc)

    roundtrip_ok = (
        restored.next_stage == "MANAGER"
        and restored.user_msg == dummy.user_msg
        and restored.route_decision == dummy.route_decision
        and restored.context_card == dummy.context_card
        and restored.scratchpad == dummy.scratchpad
        and restored.failed_task_router_retries == 1
        and getattr(restored.last_verification, "verdict", "") == "PARTIAL"
        and getattr(restored.last_stage_outcome, "status", "") == "PARTIAL"
        and restored.pending_file_target_confirmation == dummy.pending_file_target_confirmation
        and restored.pending_stage_pause == dummy.pending_stage_pause
    )

    sqlite_checkpoint = {"ok": False, "error": "graph unavailable"}
    if graph_available:
        try:
            sqlite_checkpoint = _run_sqlite_checkpoint_smoke()
        except Exception as exc:
            sqlite_checkpoint = {"ok": False, "error": str(exc)}

    success = bool(roundtrip_ok and graph_available and sqlite_checkpoint.get("ok"))
    report = {
        "success": success,
        "roundtrip_ok": bool(roundtrip_ok),
        "graph_available": bool(graph_available),
        "graph_error": graph_error,
        "sqlite_checkpoint": sqlite_checkpoint,
        "state_keys": sorted(state.keys()),
        "stage_timings_present": "stage_timings" in state,
        "verification": asdict(restored.last_verification) if restored.last_verification else None,
        "stage_outcome": asdict(restored.last_stage_outcome) if restored.last_stage_outcome else None,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
