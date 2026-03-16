from __future__ import annotations

import re
from typing import Any, Iterable


LATEST_RUNTIME_CONTEXT_PREFIX = "[LATEST_RUNTIME_CONTEXT]"
_RUNTIME_CONTEXT_LINE_RE = re.compile(r"^([^:\n]+):\s*(.+)$", re.MULTILINE)


def extract_latest_runtime_context_fields(
    recent_history: Iterable[dict[str, Any]] | None,
) -> dict[str, str]:
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "system":
            continue
        content = str(item.get("content") or "")
        if not content.startswith(LATEST_RUNTIME_CONTEXT_PREFIX):
            continue
        fields: dict[str, str] = {}
        for key, value in _RUNTIME_CONTEXT_LINE_RE.findall(content):
            fields[str(key or "").strip().lower().replace(" ", "_")] = str(value or "").strip()
        return fields
    return {}


def extract_previous_user_message(
    recent_history: Iterable[dict[str, Any]] | None,
    *,
    current_text: str = "",
) -> str:
    current_clean = " ".join(str(current_text or "").split()).strip().lower()
    skipped_current = False
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(item.get("content") or "").split()).strip()
        if not content:
            continue
        if current_clean and content.lower() == current_clean and not skipped_current:
            skipped_current = True
            continue
        return content
    return ""
