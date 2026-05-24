from __future__ import annotations

from typing import Any, Sequence

from core.routing.route_normalizer import register_route_interceptor


@register_route_interceptor
def _registered_operational_state_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
    orc,
) -> dict[str, Any] | None:
    del recent_history
    if orc is None:
        return None
    try:
        answer = orc.prompt_context.build_readonly_state_answer(user_msg)
    except Exception:
        return None
    if not answer:
        return None
    # Cache the answer so persona does not recompute it.
    try:
        orc._cached_readonly_state_answer = answer
    except Exception:
        pass
    return {
        "kind": "OPERATIONAL_STATE_QUERY",
        "next_stage": "PERSONA",
        "stats_decision": "CHAT",
        "bypass": "operational_state_query",
        "log_message": "   -> Operational state query. Skipping Secretary/router LLM and answering in PERSONA.",
        "route_decision": {
            "decision": "CHAT",
            "interceptor": "OPERATIONAL_STATE_QUERY",
            "card": {"query": user_msg},
        },
    }
