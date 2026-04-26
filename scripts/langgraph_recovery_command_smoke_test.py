from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.commands import handle_command  # noqa: E402
from core.orchestrator_graph import (  # noqa: E402
    clear_langgraph_interrupt_record,
    clear_langgraph_recovery_record,
    describe_langgraph_interrupt_record,
    describe_langgraph_recovery_record,
    load_langgraph_interrupt_record,
    load_langgraph_recovery_record,
    save_langgraph_interrupt_record,
    save_langgraph_recovery_record,
)


class DummyStyleManager:
    styles_dir = ROOT_DIR / "data" / "styles"


def main() -> int:
    parsed = {
        "/graph": handle_command("/graph", style_mgr=DummyStyleManager()),
        "/graph status": handle_command("/graph status", style_mgr=DummyStyleManager()),
        "/graph resume": handle_command("/graph resume", style_mgr=DummyStyleManager()),
        "/langgraph clear": handle_command("/langgraph clear", style_mgr=DummyStyleManager()),
    }
    command_ok = (
        parsed["/graph"].handled
        and parsed["/graph"].action == "langgraph_recovery"
        and parsed["/graph"].graph_action == "status"
        and parsed["/graph status"].graph_action == "status"
        and parsed["/graph resume"].graph_action == "resume"
        and parsed["/langgraph clear"].graph_action == "clear"
    )

    with TemporaryDirectory() as tmp_dir:
        recovery_path = Path(tmp_dir) / "langgraph_recovery.json"
        record = {
            "schema": 1,
            "status": "failed",
            "thread_id": "thread-123",
            "checkpoint_id": "checkpoint-456",
            "checkpoint_next": ["persona"],
            "stage_trace": ["ROUTE"],
            "user_msg": "hello graph",
            "error": "boom",
        }
        save_langgraph_recovery_record(record, path=recovery_path)
        loaded = load_langgraph_recovery_record(path=recovery_path)
        description = describe_langgraph_recovery_record(path=recovery_path)
        clear_wrong_thread = clear_langgraph_recovery_record(path=recovery_path, thread_id="other-thread")
        still_present = bool(load_langgraph_recovery_record(path=recovery_path))
        clear_right_thread = clear_langgraph_recovery_record(path=recovery_path, thread_id="thread-123")
        cleared = not load_langgraph_recovery_record(path=recovery_path)

    with TemporaryDirectory() as tmp_dir:
        interrupt_path = Path(tmp_dir) / "langgraph_interrupt.json"
        interrupt_record = {
            "schema": 1,
            "status": "pending",
            "thread_id": "interrupt-thread",
            "checkpoint_id": "interrupt-checkpoint",
            "interrupt_payload": {
                "kind": "missing_file_target_confirmation",
                "question": "Did you mean notes/b.txt?",
            },
        }
        save_langgraph_interrupt_record(interrupt_record, path=interrupt_path)
        loaded_interrupt = load_langgraph_interrupt_record(path=interrupt_path)
        interrupt_description = describe_langgraph_interrupt_record(path=interrupt_path)
        clear_interrupt_wrong = clear_langgraph_interrupt_record(path=interrupt_path, thread_id="other")
        interrupt_still_present = bool(load_langgraph_interrupt_record(path=interrupt_path))
        clear_interrupt_right = clear_langgraph_interrupt_record(path=interrupt_path, thread_id="interrupt-thread")
        interrupt_cleared = not load_langgraph_interrupt_record(path=interrupt_path)

    record_ok = (
        loaded.get("thread_id") == "thread-123"
        and "thread-123" in description
        and "persona" in description
        and not clear_wrong_thread
        and still_present
        and clear_right_thread
        and cleared
        and loaded_interrupt.get("thread_id") == "interrupt-thread"
        and "Did you mean notes/b.txt?" in interrupt_description
        and not clear_interrupt_wrong
        and interrupt_still_present
        and clear_interrupt_right
        and interrupt_cleared
    )
    report = {
        "success": bool(command_ok and record_ok),
        "command_ok": bool(command_ok),
        "record_ok": bool(record_ok),
        "parsed": {
            key: {
                "handled": value.handled,
                "action": value.action,
                "graph_action": value.graph_action,
                "ui_message": value.ui_message,
            }
            for key, value in parsed.items()
        },
        "description": description,
        "interrupt_description": interrupt_description,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
