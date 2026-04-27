from __future__ import annotations

from typing import Any

from tools.workspace_extension_actions import (
    handle_consolidate_by_extension,
    handle_delete_empty_dirs,
)
from tools.workspace_mutation_actions import (
    handle_append_text,
    handle_copy_many,
    handle_copy_path,
    handle_delete_many,
    handle_delete_path,
    handle_ensure_dir,
    handle_ensure_dirs,
    handle_move_many,
    handle_move_path,
    handle_update_json,
    handle_write_json,
    handle_write_text,
)
from tools.workspace_query_actions import (
    handle_extension_inventory,
    handle_find_paths,
    handle_list_tree,
    handle_read_many,
    handle_read_text,
)


# Common aliases that models guess when they do not recall exact registry names.
_FILE_OP_ALIASES: dict[str, str] = {
    "check_file": "find_paths",
    "check": "find_paths",
    "delete": "delete_path",
    "remove": "delete_path",
    "read_file": "read_text",
    "read_files": "read_many",
    "copy": "copy_path",
    "move": "move_path",
    "rename": "move_path",
}


def execute_file_op(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    resolved_action = _FILE_OP_ALIASES.get(action, action)
    # Normalize common key aliases models use instead of the canonical names.
    if resolved_action in ("move_path", "copy_path"):
        payload = dict(payload)
        if payload.get("source") and not payload.get("src"):
            payload["src"] = payload["source"]
        if payload.get("destination") and not payload.get("dst"):
            payload["dst"] = payload["destination"]
    if resolved_action == "ensure_dir":
        return handle_ensure_dir(runtime, payload, action)
    if resolved_action == "ensure_dirs":
        return handle_ensure_dirs(runtime, payload, action, cancel_token=cancel_token)
    if resolved_action == "list_tree":
        return handle_list_tree(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if resolved_action == "find_paths":
        return handle_find_paths(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if resolved_action == "extension_inventory":
        return handle_extension_inventory(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if resolved_action == "consolidate_by_extension":
        return handle_consolidate_by_extension(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if resolved_action == "delete_empty_dirs":
        return handle_delete_empty_dirs(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if resolved_action == "write_text":
        return handle_write_text(runtime, payload, action)
    if resolved_action == "append_text":
        return handle_append_text(runtime, payload, action, file_op_error)
    if resolved_action == "write_json":
        return handle_write_json(runtime, payload, action)
    if resolved_action == "update_json":
        return handle_update_json(runtime, payload, action, file_op_error)
    if resolved_action == "read_text":
        if isinstance(payload.get("paths"), list) and payload.get("paths"):
            return handle_read_many(runtime, payload, "read_many", cancel_token=cancel_token)
        return handle_read_text(runtime, payload, action, file_op_error)
    if resolved_action == "read_many":
        if payload.get("path"):
            single_payload = dict(payload)
            single_payload["paths"] = [payload.get("path")]
            return handle_read_many(runtime, single_payload, action, cancel_token=cancel_token)
        return handle_read_many(runtime, payload, action, cancel_token=cancel_token)
    if resolved_action == "move_path":
        return handle_move_path(runtime, payload, action, file_op_error)
    if resolved_action == "move_many":
        return handle_move_many(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if resolved_action == "copy_path":
        return handle_copy_path(runtime, payload, action, file_op_error)
    if resolved_action == "copy_many":
        return handle_copy_many(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if resolved_action == "delete_path":
        return handle_delete_path(runtime, payload, action, file_op_error)
    if resolved_action == "delete_many":
        return handle_delete_many(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    return file_op_error(f"Unsupported FILE_OP action: {action}")
