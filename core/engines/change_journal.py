from __future__ import annotations

import base64
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from memory.storage import ensure_parent
from tools.file_ops import FileOpError, parse_payload as parse_file_op_payload, resolve_workspace_path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _normalize_file_op_action(action: str) -> str:
    normalized = str(action or "").strip().lower()
    aliases = {
        "mkdir": "ensure_dir",
        "mkdirs": "ensure_dirs",
        "make_dir": "ensure_dir",
        "create_dir": "ensure_dir",
        "create_directory": "ensure_dir",
        "make_directory": "ensure_dir",
        "make_directories": "ensure_dirs",
        "create_directories": "ensure_dirs",
        "create_json": "write_json",
        "modify_json": "update_json",
        "patch_json": "update_json",
        "move_file": "move_path",
        "move_files": "move_many",
        "rename_path": "move_path",
        "rename_file": "move_path",
        "rename_files": "move_many",
        "copy_file": "copy_path",
        "copy_files": "copy_many",
        "delete_file": "delete_path",
        "delete_files": "delete_many",
        "remove_file": "delete_path",
        "remove_files": "delete_many",
    }
    return aliases.get(normalized, normalized)


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_bytes(data: str) -> bytes:
    return base64.b64decode(str(data or "").encode("ascii"))


def _path_depth(path: str) -> int:
    cleaned = str(path or "").strip().strip("/")
    if not cleaned:
        return 0
    return len([part for part in cleaned.split("/") if part])


class ChangeJournal:
    _SUPPORTED_MUTATING_ACTIONS = {
        "append_text",
        "copy_many",
        "copy_path",
        "delete_many",
        "delete_path",
        "ensure_dir",
        "ensure_dirs",
        "move_many",
        "move_path",
        "update_json",
        "write_json",
        "write_text",
    }

    def __init__(self, path: Path, *, max_entries: int = 10) -> None:
        self.path = Path(path)
        self.max_entries = max(5, int(max_entries or 40))

    def load_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, dict)]

    def save_entries(self, entries: list[dict[str, Any]]) -> None:
        ensure_parent(self.path)
        self.path.write_text(json.dumps(entries[-self.max_entries :], indent=2, ensure_ascii=False), encoding="utf-8")

    def prepare_file_op_capture_from_tool_tag(self, tool_tag: str, workspace: Path) -> dict[str, Any] | None:
        payload_text = self._extract_file_op_payload_text(tool_tag)
        if not payload_text:
            return None
        return self.prepare_file_op_capture(payload_text, workspace)

    def prepare_file_op_capture(self, payload_text: str, workspace: Path) -> dict[str, Any] | None:
        try:
            payload = parse_file_op_payload(str(payload_text or "").strip())
        except Exception:
            return None
        action = _normalize_file_op_action(str(payload.get("action") or ""))
        if action not in self._SUPPORTED_MUTATING_ACTIONS:
            return None
        snapshot_paths = self._capture_snapshot_paths(Path(workspace), action, payload)
        if not snapshot_paths:
            return None
        snapshots = [self._snapshot_path(Path(workspace), rel_path) for rel_path in snapshot_paths]
        return {
            "action": action,
            "requested_paths": snapshot_paths,
            "snapshots": snapshots,
        }

    def finalize_file_op_capture(self, prepared: dict[str, Any] | None, tool_result: Any) -> dict[str, Any] | None:
        if not prepared or not isinstance(prepared, dict) or not isinstance(tool_result, dict):
            return None
        action = _normalize_file_op_action(str(tool_result.get("action") or prepared.get("action") or ""))
        if action not in self._SUPPORTED_MUTATING_ACTIONS:
            return None
        if str(tool_result.get("status") or "").strip().upper() != "EXECUTED":
            return None
        if not bool(tool_result.get("workspace_changed")):
            return None
        snapshots = [dict(item) for item in (prepared.get("snapshots") or []) if isinstance(item, dict)]
        if not snapshots:
            return None
        return {
            "action": action,
            "summary": str(tool_result.get("summary") or "").strip(),
            "requested_paths": [str(item).strip() for item in (prepared.get("requested_paths") or []) if str(item).strip()],
            "evidence_paths": self._candidate_paths(tool_result),
            "snapshots": snapshots,
        }

    def record_turn(
        self,
        *,
        turn_id: str,
        user_msg: str,
        task_goal: str,
        task_success: bool,
        operations: list[dict[str, Any]],
        rollback_manifests: list[str] | None = None,
    ) -> dict[str, Any] | None:
        cleaned_ops = [dict(item) for item in (operations or []) if isinstance(item, dict) and (item.get("snapshots") or [])]
        manifests = [str(p) for p in (rollback_manifests or []) if str(p).strip()]
        if not cleaned_ops and not manifests:
            return None
        entry = {
            "turn_id": str(turn_id or "").strip() or _utc_now_iso(),
            "timestamp": _utc_now_iso(),
            "user_msg": str(user_msg or "").strip(),
            "task_goal": str(task_goal or "").strip(),
            "task_success": bool(task_success),
            "operations": cleaned_ops,
            "rollback_manifests": manifests,
            "primary_paths": self._primary_paths_from_operations(cleaned_ops),
            "undone_at": "",
            "undo_last_status": "",
            "undo_last_error": "",
        }
        entries = self.load_entries()
        entries.append(entry)
        self.save_entries(entries)
        return entry

    def mark_entry_undone(self, turn_id: str, *, status: str = "VERIFIED", detail: str = "") -> None:
        """Mark a specific journal entry (by turn_id) as undone.

        Called by phase_undo after a manifest-based rollback so a second
        'undo' attempt is correctly refused.
        """
        entries = self.load_entries()
        for i, entry in enumerate(entries):
            if str(entry.get("turn_id") or "") == str(turn_id or "").strip():
                entry["undone_at"] = _utc_now_iso()
                entry["undo_last_status"] = str(status or "VERIFIED")
                entry["undo_last_error"] = str(detail or "")
                entries[i] = entry
                self.save_entries(entries)
                return

    def has_pending_undo(self) -> bool:
        latest = self.peek_latest_entry()
        return bool(latest and not str(latest.get("undone_at") or "").strip())

    def peek_latest_entry(self) -> dict[str, Any] | None:
        entries = self.load_entries()
        if not entries:
            return None
        latest = entries[-1]
        return dict(latest) if isinstance(latest, dict) else None

    def undo_latest(self, workspace: Path) -> dict[str, Any]:
        entries = self.load_entries()
        if not entries:
            return {
                "status": "FAILED",
                "summary": "No undoable file task was recorded yet",
                "detail": "No mutating FILE_WORK task has been recorded in the change journal yet.",
                "paths": [],
                "workspace_changed": False,
            }

        latest_index = len(entries) - 1
        latest = dict(entries[latest_index] or {})
        if str(latest.get("undone_at") or "").strip():
            return {
                "status": "FAILED",
                "summary": "The last recorded file task is already undone",
                "detail": "The most recent change-journal entry was already reverted.",
                "paths": [str(item).strip() for item in (latest.get("primary_paths") or []) if str(item).strip()],
                "workspace_changed": False,
            }

        operations = [dict(item) for item in (latest.get("operations") or []) if isinstance(item, dict)]
        if not operations:
            return {
                "status": "FAILED",
                "summary": "The last recorded file task does not have undo data",
                "detail": "The most recent change-journal entry does not contain reversible operations.",
                "paths": [],
                "workspace_changed": False,
            }

        restored_paths: list[str] = []
        errors: list[str] = []
        for operation in reversed(operations):
            snapshots = [dict(item) for item in (operation.get("snapshots") or []) if isinstance(item, dict)]
            snapshots.sort(key=lambda item: _path_depth(str(item.get("path") or "")), reverse=True)
            for snapshot in snapshots:
                try:
                    restored = self._restore_snapshot(Path(workspace), snapshot)
                    if restored and restored not in restored_paths:
                        restored_paths.append(restored)
                except Exception as exc:
                    path = str(snapshot.get("path") or "").strip() or "unknown path"
                    errors.append(f"{path}: {exc}")

        if errors and restored_paths:
            status = "PARTIAL"
            summary = "Undo partially restored the last file task"
            detail = "; ".join(errors[:4])
        elif errors:
            status = "FAILED"
            summary = "Undo could not restore the last file task"
            detail = "; ".join(errors[:4])
        else:
            status = "VERIFIED"
            primary_paths = [str(item).strip() for item in (latest.get("primary_paths") or []) if str(item).strip()]
            label = ", ".join(primary_paths[:4]) if primary_paths else "the previous file changes"
            summary = f"Reverted the last file task and restored {label}"
            detail = f"Restored {len(restored_paths)} recorded path snapshots."
            latest["undone_at"] = _utc_now_iso()

        latest["undo_last_status"] = status
        latest["undo_last_error"] = detail
        entries[latest_index] = latest
        self.save_entries(entries)
        return {
            "status": status,
            "summary": summary,
            "detail": detail,
            "paths": restored_paths or [str(item).strip() for item in (latest.get("primary_paths") or []) if str(item).strip()],
            "workspace_changed": bool(restored_paths),
        }

    @staticmethod
    def _extract_file_op_payload_text(tool_tag: str) -> str:
        text = str(tool_tag or "").strip()
        if not text:
            return ""
        block_match = json_block = None
        import re

        block_match = re.search(r"\[FILE_OP\](.*?)\[/FILE_OP\]", text, re.DOTALL | re.IGNORECASE)
        if block_match:
            return str(block_match.group(1) or "").strip()
        inline_match = re.search(r"\[FILE_OP:\s*(.*?)\]$", text, re.DOTALL | re.IGNORECASE)
        if inline_match:
            return str(inline_match.group(1) or "").strip()
        malformed_inline_match = re.search(r"\[FILE_OP\s+(.+)\]$", text, re.DOTALL | re.IGNORECASE)
        if malformed_inline_match:
            return str(malformed_inline_match.group(1) or "").strip()
        return ""

    @classmethod
    def _capture_snapshot_paths(cls, workspace: Path, action: str, payload: dict[str, Any]) -> list[str]:
        ordered: list[str] = []

        def add_snapshot_target(raw_path: Any, *, include_missing_parents: bool = False) -> None:
            try:
                _, rel_path = resolve_workspace_path(workspace, raw_path)
            except FileOpError:
                return
            if include_missing_parents:
                for parent in cls._missing_parent_dirs(workspace, rel_path):
                    if parent not in ordered:
                        ordered.append(parent)
            if rel_path not in ordered:
                ordered.append(rel_path)

        if action in {"write_text", "append_text", "write_json", "update_json"}:
            add_snapshot_target(payload.get("path"), include_missing_parents=True)
        elif action == "ensure_dir":
            add_snapshot_target(payload.get("path"), include_missing_parents=True)
        elif action in {"ensure_dirs", "delete_many"}:
            for raw_path in payload.get("paths") or []:
                add_snapshot_target(raw_path, include_missing_parents=(action == "ensure_dirs"))
        elif action == "delete_path":
            add_snapshot_target(payload.get("path"))
        elif action == "move_path":
            add_snapshot_target(payload.get("src"))
            add_snapshot_target(payload.get("dst"), include_missing_parents=True)
        elif action == "copy_path":
            add_snapshot_target(payload.get("dst"), include_missing_parents=True)
        elif action in {"move_many", "copy_many"}:
            field_name = "moves" if action == "move_many" else "copies"
            for item in payload.get(field_name) or []:
                if not isinstance(item, dict):
                    continue
                if action == "move_many":
                    add_snapshot_target(item.get("src"))
                add_snapshot_target(item.get("dst"), include_missing_parents=True)
        return ordered

    @staticmethod
    def _missing_parent_dirs(workspace: Path, rel_path: str) -> list[str]:
        parent = PurePosixPath(str(rel_path or "").strip()).parent
        if str(parent) in {"", "."}:
            return []
        missing: list[str] = []
        parts = [part for part in parent.parts if part not in {"", "."}]
        for index in range(1, len(parts) + 1):
            candidate = PurePosixPath(*parts[:index]).as_posix()
            try:
                full_path, _ = resolve_workspace_path(workspace, candidate)
            except FileOpError:
                continue
            if not full_path.exists():
                missing.append(candidate)
        return missing

    @staticmethod
    def _snapshot_path(workspace: Path, rel_path: str) -> dict[str, Any]:
        full_path, normalized = resolve_workspace_path(workspace, rel_path)
        if not full_path.exists():
            return {"path": normalized, "kind": "absent"}
        if full_path.is_dir():
            entries: list[dict[str, Any]] = [{"rel": "", "kind": "dir"}]
            for child in sorted(full_path.rglob("*")):
                rel = child.relative_to(full_path).as_posix()
                if child.is_dir():
                    entries.append({"rel": rel, "kind": "dir"})
                elif child.is_file():
                    entries.append(
                        {
                            "rel": rel,
                            "kind": "file",
                            "bytes_b64": _encode_bytes(child.read_bytes()),
                        }
                    )
            return {"path": normalized, "kind": "dir", "entries": entries}
        return {
            "path": normalized,
            "kind": "file",
            "bytes_b64": _encode_bytes(full_path.read_bytes()),
        }

    @staticmethod
    def _restore_snapshot(workspace: Path, snapshot: dict[str, Any]) -> str:
        path = str(snapshot.get("path") or "").strip()
        if not path:
            raise ValueError("Snapshot path is missing.")
        full_path, rel_path = resolve_workspace_path(workspace, path)
        kind = str(snapshot.get("kind") or "").strip().lower()
        if kind == "absent":
            _remove_path(full_path)
            return rel_path
        if kind == "file":
            _remove_path(full_path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(_decode_bytes(str(snapshot.get("bytes_b64") or "")))
            return rel_path
        if kind == "dir":
            _remove_path(full_path)
            full_path.mkdir(parents=True, exist_ok=True)
            entries = [dict(item) for item in (snapshot.get("entries") or []) if isinstance(item, dict)]
            dir_entries = [entry for entry in entries if str(entry.get("kind") or "").strip().lower() == "dir"]
            file_entries = [entry for entry in entries if str(entry.get("kind") or "").strip().lower() == "file"]
            for entry in sorted(dir_entries, key=lambda item: _path_depth(str(item.get("rel") or ""))):
                rel = str(entry.get("rel") or "").strip()
                if not rel:
                    continue
                (full_path / rel).mkdir(parents=True, exist_ok=True)
            for entry in file_entries:
                rel = str(entry.get("rel") or "").strip()
                if not rel:
                    continue
                target = full_path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_decode_bytes(str(entry.get("bytes_b64") or "")))
            return rel_path
        raise ValueError(f"Unsupported snapshot kind: {kind}")

    @staticmethod
    def _candidate_paths(tool_result: dict[str, Any]) -> list[str]:
        seen: list[str] = []
        for key in (
            "requested_path",
            "path",
            "requested_paths",
            "created_files",
            "updated_files",
            "deleted_files",
            "created_dirs",
            "deleted_dirs",
            "evidence_files",
        ):
            value = tool_result.get(key)
            if isinstance(value, list):
                items = [str(item).strip() for item in value if str(item).strip()]
            else:
                item = str(value or "").strip()
                items = [item] if item else []
            for item in items:
                if item not in seen:
                    seen.append(item)
        for key in ("requested_moves", "requested_copies"):
            value = tool_result.get(key) or []
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                for field_name in ("src", "dst"):
                    candidate = str(item.get(field_name) or "").strip()
                    if candidate and candidate not in seen:
                        seen.append(candidate)
        return seen[:12]

    @staticmethod
    def _primary_paths_from_operations(operations: list[dict[str, Any]]) -> list[str]:
        seen: list[str] = []
        for operation in operations:
            for collection_name in ("evidence_paths", "requested_paths"):
                for item in operation.get(collection_name) or []:
                    candidate = str(item).strip()
                    if candidate and candidate not in seen:
                        seen.append(candidate)
        return seen[:8]
