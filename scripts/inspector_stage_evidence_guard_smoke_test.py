from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.executor import StageExecutor  # noqa: E402


class _DummyUI:
    def put(self, _event):
        return None


def main() -> int:
    executor = StageExecutor(
        llm_client=None,
        agent_brain=None,
        img_gen=None,
        boot_mgr=None,
        ui_queue=_DummyUI(),
    )
    empty_evidence = executor._inspector_finish_has_stage_evidence()
    executor.scratchpad.append("PROPOSAL: Ask the user to confirm the target event.")
    proposal_evidence = executor._inspector_finish_has_stage_evidence()
    executor.scratchpad.clear()
    executor._last_successful_tool_name = "COMPLETE_EVENT"
    tool_evidence = executor._inspector_finish_has_stage_evidence()
    success = (empty_evidence is False) and (proposal_evidence is True) and (tool_evidence is True)
    print(
        json.dumps(
            {
                "success": bool(success),
                "empty_evidence": bool(empty_evidence),
                "proposal_evidence": bool(proposal_evidence),
                "tool_evidence": bool(tool_evidence),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
