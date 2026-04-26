from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator_graph import (  # noqa: E402
    OrchestratorGraphContext,
    build_orchestrator_graph_runtime,
    snapshot_orchestrator_state,
)
from scripts.langgraph_checkpoint_inspect import inspect_checkpoints  # noqa: E402
from scripts.langgraph_test_fixtures import BaseDummyOrchestrator  # noqa: E402


class InspectSmokeOrchestrator(BaseDummyOrchestrator):
    def __init__(self) -> None:
        super().__init__(turn_id="checkpoint-inspect-smoke", user_msg="Inspect checkpoint state.")
        self.latest_search_failed = False
        self.latest_search_error = ""
        self.synthetic_user_turn = False
        self.is_search_result = False
        self.pending_file_target_confirmation = None

    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "ROUTE":
            self.route_decision = {"decision": "CHAT"}
            self.scratchpad.append("ROUTE: inspect smoke")
            self.next_stage = "PERSONA"
            return
        if stage_name == "PERSONA":
            self.scratchpad.append("PERSONA: inspect smoke")
            self.next_stage = "FINISHED"
            return
        raise RuntimeError(f"Unexpected inspect smoke stage: {stage_name}")


def main() -> int:
    thread_id = "checkpoint-inspect-smoke"
    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "checkpoint_inspect.sqlite"
        runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        try:
            orc = InspectSmokeOrchestrator()
            result = runtime.graph.invoke(
                snapshot_orchestrator_state(orc, stage_trace=[]),
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=orc),
            )
        finally:
            runtime.close()

        listing = inspect_checkpoints(path=checkpoint_path, limit=10, thread_id=thread_id)
        checkpoints = list(listing.get("checkpoints") or [])
        latest_id = str((checkpoints[0] or {}).get("checkpoint_id") or "") if checkpoints else ""
        selected_without_scratchpad = inspect_checkpoints(
            path=checkpoint_path,
            limit=10,
            thread_id=thread_id,
            checkpoint_id=latest_id,
            include_values=True,
            include_scratchpad=False,
        )
        selected_with_scratchpad = inspect_checkpoints(
            path=checkpoint_path,
            limit=10,
            thread_id=thread_id,
            checkpoint_id=latest_id,
            include_values=True,
            include_scratchpad=True,
        )

    selected_values = dict((selected_without_scratchpad.get("selected_checkpoint") or {}).get("values") or {})
    selected_values_with_scratchpad = dict(
        (selected_with_scratchpad.get("selected_checkpoint") or {}).get("values") or {}
    )
    scratchpad_without = selected_values.get("scratchpad")
    scratchpad_with = selected_values_with_scratchpad.get("scratchpad")
    success = bool(
        result.get("stage_trace") == ["ROUTE", "PERSONA"]
        and checkpoints
        and listing.get("threads")
        and latest_id
        and selected_values.get("next_stage") == "FINISHED"
        and selected_values.get("stage_trace") == ["ROUTE", "PERSONA"]
        and isinstance(scratchpad_without, dict)
        and "omitted" in scratchpad_without
        and isinstance(scratchpad_with, list)
        and "PERSONA: inspect smoke" in scratchpad_with
    )
    report = {
        "success": success,
        "checkpoint_count": len(checkpoints),
        "thread_count": len(listing.get("threads") or []),
        "latest_checkpoint_id": latest_id,
        "selected_next_stage": selected_values.get("next_stage"),
        "selected_stage_trace": selected_values.get("stage_trace"),
        "scratchpad_omitted": scratchpad_without,
        "scratchpad_included": scratchpad_with,
        "errors": {
            "listing": listing.get("error", ""),
            "selected_without_scratchpad": selected_without_scratchpad.get("error", ""),
            "selected_with_scratchpad": selected_with_scratchpad.get("error", ""),
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
