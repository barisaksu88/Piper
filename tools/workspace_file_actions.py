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


def execute_file_op(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    if action == "ensure_dir":
        return handle_ensure_dir(runtime, payload, action)
    if action == "ensure_dirs":
        return handle_ensure_dirs(runtime, payload, action, cancel_token=cancel_token)
    if action == "list_tree":
        return handle_list_tree(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if action == "find_paths":
        return handle_find_paths(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if action == "extension_inventory":
        return handle_extension_inventory(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if action == "consolidate_by_extension":
        return handle_consolidate_by_extension(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if action == "delete_empty_dirs":
        return handle_delete_empty_dirs(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if action == "write_text":
        return handle_write_text(runtime, payload, action)
    if action == "append_text":
        return handle_append_text(runtime, payload, action)
    if action == "write_json":
        return handle_write_json(runtime, payload, action)
    if action == "update_json":
        return handle_update_json(runtime, payload, action, file_op_error)
    if action == "read_text":
        return handle_read_text(runtime, payload, action, file_op_error)
    if action == "read_many":
        return handle_read_many(runtime, payload, action, cancel_token=cancel_token)
    if action == "move_path":
        return handle_move_path(runtime, payload, action, file_op_error)
    if action == "move_many":
        return handle_move_many(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if action == "copy_path":
        return handle_copy_path(runtime, payload, action, file_op_error)
    if action == "copy_many":
        return handle_copy_many(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    if action == "delete_path":
        return handle_delete_path(runtime, payload, action, file_op_error)
    if action == "delete_many":
        return handle_delete_many(runtime, payload, action, file_op_error, cancel_token=cancel_token)
    return file_op_error(f"Unsupported FILE_OP action: {action}")
