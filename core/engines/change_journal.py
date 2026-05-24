from __future__ import annotations

from typing import Any

from core.feature_hooks import register_hook


@register_hook("on_task_verified")
def _hook_record_change_journal(
    orc,
    *,
    completed_change_operations: list[dict[str, Any]] | None = None,
    completed_rollback_manifests: list[str] | None = None,
    completed_all_stages: bool = False,
    task_failed: bool = False,
    task_paused: bool = False,
) -> None:
    operations = [dict(item) for item in (completed_change_operations or []) if isinstance(item, dict)]
    manifests = [str(p) for p in (completed_rollback_manifests or []) if str(p).strip()]
    task_success = bool(completed_all_stages and not task_failed and not task_paused)
    orc.last_change_journal_entry = orc.change_journal.record_turn(
        turn_id=str(getattr(getattr(orc, "turn_stats", None), "turn_id", "") or ""),
        user_msg=str(getattr(orc, "user_msg", "") or ""),
        task_goal=str((orc.context_card or {}).get("goal") or ""),
        task_success=task_success,
        operations=operations,
        rollback_manifests=manifests,
    )
    orc.undo_notice_pending = bool(orc.last_change_journal_entry) and task_success
