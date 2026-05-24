"""core/services/rollback_engine.py

Bulk mutation rollback manifest engine.

Writes a flat recipe manifest for bulk FILE_OP mutations so 'undo that'
after a large consolidation / multi-file move can be inverted
mechanically without relying on binary file-content snapshots.

Lifecycle
---------
record_manifest(...)   — write manifest JSON from tool result, return Path
invert_manifest(...)   — replay in reverse, return result dict (same shape
                         as ChangeJournal.undo_latest)

The manifest is written from the tool result (post-execution).  For
consolidate_by_extension and move_many the full recipe is only knowable
after the operation completes; the manifest is therefore marked
committed=True immediately on write.

At most _MAX_MANIFESTS are kept on disk; older ones are pruned on each
write so the data/rollback/ directory stays bounded.

Limitations (v1)
----------------
- Only the most recent bulk operation is reversible via manifest.
- delete_many deletions are recorded but cannot be restored (no content
  snapshot); invert_manifest skips them and reports them as non-fatal.
- RUN_CODE operations are excluded — script side-effects are opaque.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory.storage import ensure_parent
from tools.file_ops import FileOpError, resolve_workspace_path

_BULK_ACTIONS = frozenset(
    {"consolidate_by_extension", "move_many", "copy_many", "delete_many"}
)
_MAX_MANIFESTS = 5


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _manifest_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "rollback"


def _safe_turn_id(turn_id: str) -> str:
    raw = str(turn_id or "").strip()
    if not raw:
        return ""
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)
    safe = re.sub(r"_+", "_", safe).strip(" ._")
    return safe


def is_bulk_action(action: str) -> bool:
    """Return True when *action* is a bulk FILE_OP that warrants a manifest."""
    return str(action or "").strip().lower() in _BULK_ACTIONS


def record_manifest(
    turn_id: str,
    action: str,
    tool_result: dict[str, Any],
    data_dir: Path,
) -> Path | None:
    """Write a rollback manifest for a completed bulk FILE_OP.

    Returns the manifest ``Path`` on success, ``None`` when the result
    carries no recoverable recipe (e.g. nothing was moved).
    """
    action_norm = str(action or "").strip().lower()
    if action_norm not in _BULK_ACTIONS:
        return None

    moves: list[dict[str, str]] = []
    deletions: list[str] = []
    created_dirs: list[str] = []

    for item in tool_result.get("requested_moves") or []:
        if not isinstance(item, dict):
            continue
        src = str(item.get("src") or "").strip()
        dst = str(item.get("dst") or "").strip()
        if src and dst:
            moves.append({"from": src, "to": dst})

    for path in tool_result.get("deleted_files") or []:
        p = str(path or "").strip()
        if p:
            deletions.append(p)

    for d in tool_result.get("created_dirs") or []:
        p = str(d or "").strip()
        if p:
            created_dirs.append(p)

    if not moves and not deletions:
        return None

    safe_id = _safe_turn_id(turn_id) or _safe_turn_id(_utc_now_iso())
    manifest_path = _manifest_dir(data_dir) / f"rollback_{safe_id}.json"
    manifest: dict[str, Any] = {
        "turn_id": str(turn_id or "").strip(),
        "timestamp": _utc_now_iso(),
        "action": action_norm,
        "committed": True,
        "rolled_back": False,
        "moves": moves,
        "deletions": deletions,
        "created_dirs": created_dirs,
    }
    ensure_parent(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _prune_old_manifests(data_dir)
    return manifest_path


def invert_manifest(manifest_path: Path, workspace: Path) -> dict[str, Any]:
    """Replay the manifest in reverse.

    Returns a result dict with the same keys as
    ``ChangeJournal.undo_latest``:
      status          — "VERIFIED" | "PARTIAL" | "FAILED"
      summary         — human-readable one-liner
      detail          — error detail or confirmation text
      paths           — list of restored workspace-relative paths
      workspace_changed — bool
    """
    try:
        raw = Path(manifest_path).read_text(encoding="utf-8")
        manifest = json.loads(raw)
    except Exception as exc:
        return {
            "status": "FAILED",
            "summary": f"Could not read rollback manifest: {exc}",
            "detail": str(exc),
            "paths": [],
            "workspace_changed": False,
        }

    if not isinstance(manifest, dict):
        return {
            "status": "FAILED",
            "summary": "Rollback manifest is malformed.",
            "detail": "Expected a JSON object.",
            "paths": [],
            "workspace_changed": False,
        }

    if bool(manifest.get("rolled_back")):
        return {
            "status": "FAILED",
            "summary": "This bulk operation has already been rolled back.",
            "detail": "The manifest is marked rolled_back=True.",
            "paths": [],
            "workspace_changed": False,
        }

    moves: list[dict[str, str]] = [
        m for m in (manifest.get("moves") or []) if isinstance(m, dict)
    ]
    created_dirs: list[str] = [
        str(d or "").strip()
        for d in (manifest.get("created_dirs") or [])
        if str(d or "").strip()
    ]
    action = str(manifest.get("action") or "").strip()

    restored: list[str] = []
    errors: list[str] = []

    # Invert moves in reverse order: move dst → original src
    for move in reversed(moves):
        src = str(move.get("from") or "").strip()
        dst = str(move.get("to") or "").strip()
        if not src or not dst:
            continue
        try:
            dst_full, _ = resolve_workspace_path(workspace, dst)
            src_full, src_rel = resolve_workspace_path(workspace, src)
            if not dst_full.exists():
                # Already gone — the file may have been moved again after
                # the manifest was recorded.  Skip silently.
                continue
            src_full.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst_full), str(src_full))
            restored.append(src_rel)
        except (FileOpError, Exception) as exc:
            errors.append(f"{dst} → {src}: {exc}")

    # Remove auto-created destination dirs that are now empty (deepest first)
    for d in sorted(created_dirs, key=lambda p: p.count("/"), reverse=True):
        try:
            full, _ = resolve_workspace_path(workspace, d)
            if full.exists() and full.is_dir() and not any(full.iterdir()):
                full.rmdir()
        except Exception:
            pass

    # deletions cannot be recovered — note them if present
    deletions = [str(p or "").strip() for p in (manifest.get("deletions") or []) if str(p or "").strip()]
    if deletions and not errors:
        errors.append(
            f"{len(deletions)} deleted file(s) cannot be restored "
            "(no content snapshot for delete_many)."
        )

    if errors and restored:
        status = "PARTIAL"
        summary = f"Bulk undo partially reversed the {action} operation"
    elif errors and not restored:
        status = "FAILED"
        summary = f"Bulk undo could not reverse the {action} operation"
    else:
        count = len(restored)
        status = "VERIFIED"
        summary = (
            f"Reversed {count} file movement{'s' if count != 1 else ''} "
            f"from {action}"
        )

    if status in ("VERIFIED", "PARTIAL"):
        manifest["rolled_back"] = True
        try:
            Path(manifest_path).write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    return {
        "status": status,
        "summary": summary,
        "detail": "; ".join(errors[:4]) if errors else f"Restored {len(restored)} paths.",
        "paths": restored[:8],
        "workspace_changed": bool(restored),
    }


def _prune_old_manifests(data_dir: Path) -> None:
    d = _manifest_dir(data_dir)
    if not d.exists():
        return
    manifests = sorted(d.glob("rollback_*.json"), key=lambda p: p.stat().st_mtime)
    for old in manifests[:-_MAX_MANIFESTS]:
        try:
            old.unlink()
        except Exception:
            pass
