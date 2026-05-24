from __future__ import annotations

import threading
from typing import Any

from config import CFG
from core.feature_hooks import register_hook
from core.runtime_control import OperationCancelled
from core.services.conversation_compressor import ConversationCompressor


@register_hook("on_turn_end")
def _hook_deferred_conversation_summary(orc, *, reporter_just_ran: bool = False) -> None:
    """Run LLM summarization after the reply is delivered so it never blocks stream start."""
    if reporter_just_ran:
        return
    if bool(getattr(orc, "synthetic_user_turn", False)):
        return
    if not getattr(orc, "knowledge_enabled", True):
        return
    limit = getattr(CFG, "MODEL_MAX_TURNS", 10)
    history = list(orc.get_context())
    existing_summary = str(getattr(orc, "conversation_summary", "") or "")
    llm = orc.llm
    cancel_token = getattr(orc, "cancel_token", None)
    compressor = orc.conversation_compressor

    def _run() -> None:
        try:
            result = compressor.compress_history(
                history=history,
                existing_summary=existing_summary,
                max_turns=limit,
                llm=llm,
                cancel_token=cancel_token,
            )
            if result.summarization_used and result.summary != existing_summary:
                orc.update_conversation_summary(result.summary)
                orc.ui.put(("agent_log", "   -> Conversation summary updated."))
        except OperationCancelled:
            pass
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
