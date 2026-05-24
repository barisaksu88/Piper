"""core/services/context_pack_paths.py

Pure helpers for extracting and normalizing runtime context paths from
orchestrator state.  No lifecycle hooks, no registries, no engine deps.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from config import CFG
from core.services.summary import SummaryEngine

_RUNTIME_CONTEXT_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/][^\s`\"'<>|]+|/mnt/[a-z]/[^\s`\"'<>|]+|[\w./\\-]+\.[A-Za-z0-9]{1,8})"
)


def normalize_runtime_context_path(raw_path: str, workspace_root: Path | None) -> str:
    candidate = str(raw_path or "").strip().strip("`\"'.,;:()[]{}")
    if not candidate:
        return ""
    normalized = candidate.replace("\\", "/").strip()
    if not normalized or normalized.endswith(":"):
        return ""

    resolved: Path | None = None
    windows_match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if windows_match:
        drive = windows_match.group(1).lower()
        suffix = windows_match.group(2)
        if os.name == "nt":
            windows_suffix = suffix.replace("/", "\\")
            resolved = Path(f"{drive.upper()}:\\{windows_suffix}")
        else:
            resolved = Path(f"/mnt/{drive}/{suffix}")
    elif normalized.startswith("/mnt/"):
        if os.name == "nt" and len(normalized) > 6:
            drive = normalized[5].upper()
            suffix = normalized[7:].replace("/", "\\")
            resolved = Path(f"{drive}:\\{suffix}")
        else:
            resolved = Path(normalized)
    else:
        resolved = (workspace_root / normalized).resolve() if workspace_root is not None else Path(normalized)

    if workspace_root is not None:
        try:
            canonical_workspace = Path(os.path.normcase(os.path.realpath(workspace_root)))
            canonical_candidate = Path(os.path.normcase(os.path.realpath(resolved)))
            rel = canonical_candidate.relative_to(canonical_workspace)
            if canonical_candidate.exists():
                return rel.as_posix()
            return ""
        except Exception:
            return ""

    return normalized


def collect_runtime_context_paths(orc) -> list[str]:
    workspace_root = Path(getattr(getattr(orc, "brain", None), "workspace", CFG.DATA_DIR / "workspace")).resolve()
    blobs: list[str] = [str(getattr(orc, "user_msg", "") or "").strip()]
    card = dict(getattr(orc, "context_card", {}) or getattr(orc, "route_decision", {}).get("card") or {})
    blobs.append(str(card.get("goal") or "").strip())
    blobs.extend(str(item or "").strip() for item in (card.get("context") or []))
    for stage in card.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        blobs.append(str(stage.get("stage_goal") or "").strip())
        blobs.append(str(stage.get("success_condition") or "").strip())
    blobs.extend(str(entry or "") for entry in SummaryEngine.latest_stage_entries(getattr(orc, "scratchpad", []) or []))

    ordered: list[str] = []
    seen: set[str] = set()
    for blob in blobs:
        for match in _RUNTIME_CONTEXT_PATH_RE.findall(blob):
            normalized = normalize_runtime_context_path(str(match or ""), workspace_root)
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(normalized)
    return ordered
