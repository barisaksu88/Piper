from __future__ import annotations

from core.routing.environment_queries import looks_like_live_environment_query
from core.routing.route_normalizer import register_route_interceptor


@register_route_interceptor
def _registered_environment_query_interceptor(
    user_msg: str, recent_history
) -> dict[str, Any] | None:
    del recent_history
    if not looks_like_live_environment_query(user_msg):
        return None
    return {
        "kind": "ENVIRONMENT_QUERY",
        "next_stage": "PERSONA",
        "stats_decision": "CHAT",
        "bypass": "environment_query",
        "log_message": "   -> Live environment query. Skipping Secretary/router LLM and answering in PERSONA.",
        "route_decision": {
            "decision": "CHAT",
            "interceptor": "ENVIRONMENT_QUERY",
            "card": {"query": user_msg},
        },
    }
