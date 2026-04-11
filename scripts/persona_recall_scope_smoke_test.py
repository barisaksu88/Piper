from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator_phases import _persona_recall_allowed  # noqa: E402


def _orc(
    *,
    decision: str = "CHAT",
    ingested_document_chat: bool = False,
    document_focus_text: str = "",
):
    return SimpleNamespace(
        route_decision={"decision": decision},
        ingested_document_chat=ingested_document_chat,
        document_focus_text=document_focus_text,
    )


def main() -> int:
    chat_allowed = _persona_recall_allowed(
        _orc(decision="CHAT"),
        reporter_just_ran=False,
        explain_last_turn=False,
    )
    task_blocked = _persona_recall_allowed(
        _orc(decision="TASK"),
        reporter_just_ran=False,
        explain_last_turn=False,
    )
    search_report_blocked = _persona_recall_allowed(
        _orc(decision="SEARCH"),
        reporter_just_ran=True,
        explain_last_turn=False,
    )
    explain_blocked = _persona_recall_allowed(
        _orc(decision="CHAT"),
        reporter_just_ran=False,
        explain_last_turn=True,
    )
    document_focus_blocked = _persona_recall_allowed(
        _orc(decision="CHAT", document_focus_text="Focused document answer"),
        reporter_just_ran=False,
        explain_last_turn=False,
    )
    ingested_doc_blocked = _persona_recall_allowed(
        _orc(decision="CHAT", ingested_document_chat=True),
        reporter_just_ran=False,
        explain_last_turn=False,
    )

    success = all(
        [
            chat_allowed is True,
            task_blocked is False,
            search_report_blocked is False,
            explain_blocked is False,
            document_focus_blocked is False,
            ingested_doc_blocked is False,
        ]
    )
    print(
        json.dumps(
            {
                "success": success,
                "chat_allowed": chat_allowed,
                "task_blocked": task_blocked,
                "search_report_blocked": search_report_blocked,
                "explain_blocked": explain_blocked,
                "document_focus_blocked": document_focus_blocked,
                "ingested_doc_blocked": ingested_doc_blocked,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
