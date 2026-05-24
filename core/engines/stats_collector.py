from __future__ import annotations

from typing import Any

from core.feature_hooks import register_hook
from core.services.stats_collector import StatsCollector, TurnStatsState


@register_hook("on_pre_route")
def _hook_note_pre_route_user_msg(orc, *, recent_history: list[dict[str, Any]] | None = None) -> None:
    del recent_history
    orc.stats_collector.note_user_msg(orc.turn_stats, orc.user_msg)


__all__ = ["StatsCollector", "TurnStatsState"]
