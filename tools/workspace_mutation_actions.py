from __future__ import annotations

import hashlib
import json
import shutil
from typing import Any

from tools.file_ops import resolve_workspace_path


def handle_ensure_dir(runtime: Any, payload: dict[str, Any], action: str) -> dict[str, Any]:
    full_path, rel_path = resolve_workspace_path(runtime.workspace, payload.get("path"))
    full_path.mkdir(parents=True, exist_ok=True)
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Directory ready: {rel_path}",
        "action": action,
        "path": rel_path,
        "requested_path": rel_path,
    }


def handle_ensure_dirs(runtime: Any, payload: dict[str, Any], action: str, *, cancel_token=None) -> dict[str, Any]:
    rel_paths: list[str] = []
    for raw_path in runtime._normalize_path_list(payload.get("paths")):
        runtime._raise_if_cancelled(cancel_token)
        full_path, rel_path = resolve_workspace_path(runtime.workspace, raw_path)
        full_path.mkdir(parents=True, exist_ok=True)
        rel_paths.append(rel_path)
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Prepared {len(rel_paths)} directories.",
        "action": action,
        "requested_paths": rel_paths,
    }


def handle_write_text(runtime: Any, payload: dict[str, Any], action: str) -> dict[str, Any]:
    full_path, rel_path = resolve_workspace_path(runtime.workspace, payload.get("path"))
    content = runtime._normalize_text_content(payload.get("content", ""))
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Wrote text file: {rel_path}",
        "action": action,
        "path": rel_path,
        "requested_path": rel_path,
        "requested_content_sha1": hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest(),
    }


def handle_append_text(runtime: Any, payload: dict[str, Any], action: str) -> dict[str, Any]:
    full_path, rel_path = resolve_workspace_path(runtime.workspace, payload.get("path"))
    content = runtime._normalize_text_content(payload.get("content", ""))
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with full_path.open("a", encoding="utf-8") as handle:
        handle.write(content)
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Appended text file: {rel_path}",
        "action": action,
        "path": rel_path,
        "requested_path": rel_path,
        "requested_append_sha1": hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest(),
    }


def handle_write_json(runtime: Any, payload: dict[str, Any], action: str) -> dict[str, Any]:
    full_path, rel_path = resolve_workspace_path(runtime.workspace, payload.get("path"))
    data = payload.get("data")
    indent = int(payload.get("indent", 2))
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Wrote JSON file: {rel_path}",
        "action": action,
        "path": rel_path,
        "requested_path": rel_path,
        "requested_data": data,
    }


def handle_update_json(runtime: Any, payload: dict[str, Any], action: str, file_op_error) -> dict[str, Any]:
    full_path, rel_path = resolve_workspace_path(runtime.workspace, payload.get("path"))
    updates = runtime._normalize_json_object(payload.get("updates"), field_name="updates")
    create_if_missing = bool(payload.get("create_if_missing", False))
    indent = int(payload.get("indent", 2))
    if full_path.exists():
        try:
            current = json.loads(full_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return file_op_error(f"FILE_OP could not parse JSON file '{rel_path}': {exc.msg}")
        if not isinstance(current, dict):
            return file_op_error(f"FILE_OP update_json requires '{rel_path}' to contain a JSON object.")
    elif create_if_missing:
        current = {}
        full_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        return file_op_error(f"FILE_OP target not found: {rel_path}")
    current.update(updates)
    full_path.write_text(json.dumps(current, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Updated JSON file: {rel_path}",
        "action": action,
        "path": rel_path,
        "requested_path": rel_path,
        "requested_updates": updates,
    }


def handle_move_path(runtime: Any, payload: dict[str, Any], action: str, file_op_error) -> dict[str, Any]:
    src_path, src_rel = resolve_workspace_path(runtime.workspace, payload.get("src"))
    dst_path, dst_rel = resolve_workspace_path(runtime.workspace, payload.get("dst"))
    if not src_path.exists():
        return file_op_error(f"FILE_OP source not found: {src_rel}", missing_files=[src_rel])
    if src_path.resolve() == dst_path.resolve():
        return file_op_error(f"FILE_OP move_path source and destination are identical: {src_rel}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_path), str(dst_path))
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Moved {src_rel} to {dst_rel}.",
        "action": action,
        "requested_moves": [{"src": src_rel, "dst": dst_rel}],
    }


def handle_move_many(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    moves = runtime._normalize_move_items(payload.get("moves"), field_name="moves")
    resolved_moves: list[dict[str, str]] = []
    for item in moves:
        runtime._raise_if_cancelled(cancel_token)
        src_path, src_rel = resolve_workspace_path(runtime.workspace, item["src"])
        dst_path, dst_rel = resolve_workspace_path(runtime.workspace, item["dst"])
        if not src_path.exists():
            return file_op_error(
                f"FILE_OP source not found: {src_rel}",
                missing_files=[src_rel],
                requested_moves=resolved_moves + [{"src": src_rel, "dst": dst_rel}],
            )
        if src_path.resolve() == dst_path.resolve():
            return file_op_error(f"FILE_OP move_many source and destination are identical: {src_rel}")
        resolved_moves.append({"src": src_rel, "dst": dst_rel})
    for item in resolved_moves:
        runtime._raise_if_cancelled(cancel_token)
        src_path, _ = resolve_workspace_path(runtime.workspace, item["src"])
        dst_path, _ = resolve_workspace_path(runtime.workspace, item["dst"])
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Moved {len(resolved_moves)} paths.",
        "action": action,
        "requested_moves": resolved_moves,
    }


def handle_copy_path(runtime: Any, payload: dict[str, Any], action: str, file_op_error) -> dict[str, Any]:
    src_path, src_rel = resolve_workspace_path(runtime.workspace, payload.get("src"))
    dst_path, dst_rel = resolve_workspace_path(runtime.workspace, payload.get("dst"))
    if not src_path.exists():
        return file_op_error(f"FILE_OP source not found: {src_rel}", missing_files=[src_rel])
    if src_path.resolve() == dst_path.resolve():
        return file_op_error(f"FILE_OP copy_path source and destination are identical: {src_rel}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if src_path.is_dir():
        shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
    else:
        shutil.copy2(src_path, dst_path)
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Copied {src_rel} to {dst_rel}.",
        "action": action,
        "requested_copies": [{"src": src_rel, "dst": dst_rel}],
    }


def handle_copy_many(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    copies = runtime._normalize_move_items(payload.get("copies"), field_name="copies")
    resolved_copies: list[dict[str, str]] = []
    for item in copies:
        runtime._raise_if_cancelled(cancel_token)
        src_path, src_rel = resolve_workspace_path(runtime.workspace, item["src"])
        dst_path, dst_rel = resolve_workspace_path(runtime.workspace, item["dst"])
        if not src_path.exists():
            return file_op_error(
                f"FILE_OP source not found: {src_rel}",
                missing_files=[src_rel],
                requested_copies=resolved_copies + [{"src": src_rel, "dst": dst_rel}],
            )
        if src_path.resolve() == dst_path.resolve():
            return file_op_error(f"FILE_OP copy_many source and destination are identical: {src_rel}")
        resolved_copies.append({"src": src_rel, "dst": dst_rel})
    for item in resolved_copies:
        runtime._raise_if_cancelled(cancel_token)
        src_path, _ = resolve_workspace_path(runtime.workspace, item["src"])
        dst_path, _ = resolve_workspace_path(runtime.workspace, item["dst"])
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        else:
            shutil.copy2(src_path, dst_path)
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Copied {len(resolved_copies)} paths.",
        "action": action,
        "requested_copies": resolved_copies,
    }


def handle_delete_path(runtime: Any, payload: dict[str, Any], action: str, file_op_error) -> dict[str, Any]:
    full_path, rel_path = resolve_workspace_path(runtime.workspace, payload.get("path"))
    if not full_path.exists():
        return file_op_error(f"FILE_OP target not found: {rel_path}")
    if full_path.is_dir():
        shutil.rmtree(full_path)
    else:
        full_path.unlink()
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Deleted {rel_path}.",
        "action": action,
        "requested_paths": [rel_path],
    }


def handle_delete_many(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    rel_paths = runtime._normalize_path_list(payload.get("paths"))
    resolved_paths: list[str] = []
    for raw_path in rel_paths:
        runtime._raise_if_cancelled(cancel_token)
        full_path, rel_path = resolve_workspace_path(runtime.workspace, raw_path)
        if not full_path.exists():
            return file_op_error(f"FILE_OP target not found: {rel_path}")
        resolved_paths.append(rel_path)
    for rel_path in resolved_paths:
        runtime._raise_if_cancelled(cancel_token)
        full_path, _ = resolve_workspace_path(runtime.workspace, rel_path)
        if full_path.is_dir():
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Deleted {len(resolved_paths)} paths.",
        "action": action,
        "requested_paths": resolved_paths,
    }
