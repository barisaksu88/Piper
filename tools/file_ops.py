from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable


class FileOpError(ValueError):
    pass


FILE_OP_ACTION_ALIASES: Dict[str, str] = {
    "mkdir": "ensure_dir",
    "mkdirs": "ensure_dirs",
    "make_dir": "ensure_dir",
    "create_dir": "ensure_dir",
    "create_directory": "ensure_dir",
    "make_directory": "ensure_dir",
    "make_directories": "ensure_dirs",
    "create_directories": "ensure_dirs",
    "read_file": "read_text",
    "read_files": "read_many",
    "list_files": "list_tree",
    "list_dir": "list_tree",
    "list_directory": "list_tree",
    "list_workspace": "list_tree",
    "find_file": "find_paths",
    "find_files": "find_paths",
    "find_path": "find_paths",
    "search_paths": "find_paths",
    "search_files": "find_paths",
    "locate_file": "find_paths",
    "locate_files": "find_paths",
    "scan_extensions": "extension_inventory",
    "inventory_extensions": "extension_inventory",
    "extension_scan": "extension_inventory",
    "group_by_extension": "consolidate_by_extension",
    "merge_by_extension": "consolidate_by_extension",
    "consolidate_extensions": "consolidate_by_extension",
    "cleanup_empty_dirs": "delete_empty_dirs",
    "remove_empty_dirs": "delete_empty_dirs",
    "remove_empty_directories": "delete_empty_dirs",
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

_SINGLE_PATH_ACTIONS = {
    "ensure_dir",
    "read_text",
    "write_text",
    "append_text",
    "write_json",
    "update_json",
    "delete_path",
}
_ROOT_ACTIONS = {
    "list_tree",
    "find_paths",
    "extension_inventory",
    "consolidate_by_extension",
    "delete_empty_dirs",
}
_MULTI_PATH_ACTIONS = {"ensure_dirs", "read_many", "delete_many"}


def _first_present_string(payload: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _escape_control_chars_inside_strings(text: str) -> str:
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            out.append(ch)
            in_string = not in_string
            continue
        if in_string and ch == "\n":
            out.append("\\n")
            continue
        if in_string and ch == "\r":
            out.append("\\r")
            continue
        if in_string and ch == "\t":
            out.append("\\t")
            continue
        out.append(ch)
    return "".join(out)


def parse_payload(text: str) -> Dict[str, Any]:
    raw_text = (text or "").strip()
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        try:
            payload = json.loads(_escape_control_chars_inside_strings(raw_text))
        except json.JSONDecodeError:
            raise FileOpError(f"Invalid FILE_OP JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise FileOpError("FILE_OP payload must be a JSON object.")
    return payload


def normalize_action(action: Any) -> str:
    normalized = str(action or "").strip().lower()
    return FILE_OP_ACTION_ALIASES.get(normalized, normalized)


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload or {})
    action = normalize_action(normalized.get("action", ""))
    if action:
        normalized["action"] = action

    if action in _SINGLE_PATH_ACTIONS and not str(normalized.get("path") or "").strip():
        alias_value = _first_present_string(
            normalized,
            ("root", "target", "target_path", "file", "file_path"),
        )
        if alias_value:
            normalized["path"] = alias_value

    if action in _ROOT_ACTIONS and not str(normalized.get("root") or "").strip():
        alias_value = _first_present_string(
            normalized,
            ("path", "target", "target_path", "directory", "dir", "folder"),
        )
        if alias_value:
            normalized["root"] = alias_value

    if action in _MULTI_PATH_ACTIONS and not normalized.get("paths"):
        for key in ("targets", "target_paths", "files", "file_paths"):
            value = normalized.get(key)
            if isinstance(value, list) and value:
                normalized["paths"] = value
                break

    if action in {"copy_path", "move_path"}:
        if not str(normalized.get("src") or "").strip():
            src_value = _first_present_string(
                normalized,
                ("source", "source_path", "from", "path"),
            )
            if src_value:
                normalized["src"] = src_value
        if not str(normalized.get("dst") or "").strip():
            dst_value = _first_present_string(
                normalized,
                ("destination", "destination_path", "dest", "to", "target", "target_path"),
            )
            if dst_value:
                normalized["dst"] = dst_value

    if action in {"copy_many", "move_many"}:
        field_name = "copies" if action == "copy_many" else "moves"
        if not normalized.get(field_name):
            for key in ("items", "paths", "operations"):
                value = normalized.get(key)
                if isinstance(value, list) and value:
                    normalized[field_name] = value
                    break
        if isinstance(normalized.get(field_name), list):
            normalized_items: list[dict[str, Any]] = []
            for item in normalized[field_name]:
                if not isinstance(item, dict):
                    normalized_items.append(item)
                    continue
                normalized_item = dict(item)
                if not str(normalized_item.get("src") or "").strip():
                    src_value = _first_present_string(
                        normalized_item,
                        ("source", "source_path", "from", "path"),
                    )
                    if src_value:
                        normalized_item["src"] = src_value
                if not str(normalized_item.get("dst") or "").strip():
                    dst_value = _first_present_string(
                        normalized_item,
                        ("destination", "destination_path", "dest", "to", "target", "target_path"),
                    )
                    if dst_value:
                        normalized_item["dst"] = dst_value
                normalized_items.append(normalized_item)
            normalized[field_name] = normalized_items

    if action == "find_paths" and not str(normalized.get("query") or "").strip():
        alias_value = _first_present_string(
            normalized,
            ("pattern", "name", "target", "target_path", "file", "file_path"),
        )
        if alias_value:
            normalized["query"] = alias_value

    return normalized


def parse_normalized_payload(text: str) -> Dict[str, Any]:
    return normalize_payload(parse_payload(text))


def extract_tag_payload_text(tool_tag: str, *, tag: str = "FILE_OP") -> str:
    text = str(tool_tag or "").strip()
    if not text:
        return ""
    if text.startswith("{") and text.endswith("}"):
        return text
    tag_name = re.escape(str(tag or "").strip())
    block_match = re.search(
        rf"\[{tag_name}\](.*?)\[/{tag_name}\]",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if block_match:
        return str(block_match.group(1) or "").strip()
    inline_match = re.search(
        rf"\[{tag_name}:\s*(.*?)\]$",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if inline_match:
        return str(inline_match.group(1) or "").strip()
    malformed_inline_match = re.search(
        rf"\[{tag_name}\s+(.+)\]$",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if malformed_inline_match:
        return str(malformed_inline_match.group(1) or "").strip()
    return ""


def parse_normalized_tool_tag_payload(tool_tag: str, *, tag: str = "FILE_OP") -> Dict[str, Any]:
    payload_text = extract_tag_payload_text(tool_tag, tag=tag)
    if not payload_text:
        raise FileOpError(f"{tag} payload is required.")
    return parse_normalized_payload(payload_text)


def normalized_action_from_payload(payload: Dict[str, Any] | str) -> str:
    if isinstance(payload, dict):
        return normalize_action(payload.get("action", ""))
    try:
        parsed = parse_normalized_tool_tag_payload(str(payload or ""))
    except FileOpError:
        return ""
    return normalize_action(parsed.get("action", ""))


def primary_path_from_payload(payload: Dict[str, Any]) -> str:
    return str(payload.get("path") or "").strip()


def path_list_from_payload(payload: Dict[str, Any]) -> list[str]:
    paths: list[str] = []
    primary = primary_path_from_payload(payload)
    if primary:
        paths.append(primary)
    raw_list = payload.get("paths")
    if isinstance(raw_list, list):
        for item in raw_list:
            clean = str(item or "").strip()
            if clean and clean not in paths:
                paths.append(clean)
    return paths


def source_paths_from_payload(payload: Dict[str, Any]) -> list[str]:
    sources: list[str] = []
    direct = str(payload.get("src") or "").strip()
    if direct:
        sources.append(direct)
    for field_name in ("moves", "copies"):
        raw_items = payload.get(field_name)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            clean = str(item.get("src") or "").strip()
            if clean and clean not in sources:
                sources.append(clean)
    return sources


def resolve_workspace_path(workspace: Path, raw_path: Any) -> tuple[Path, str]:
    rel_path = str(raw_path or "").strip().replace("\\", "/")
    if not rel_path:
        raise FileOpError("FILE_OP path is required.")
    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise FileOpError("FILE_OP paths must be relative to the workspace.")
    full_path = (workspace / candidate).resolve()
    canonical_full = Path(os.path.normcase(os.path.realpath(full_path)))
    canonical_root = Path(os.path.normcase(os.path.realpath(workspace.resolve())))
    try:
        canonical_full.relative_to(canonical_root)
    except ValueError:
        raise FileOpError("FILE_OP path escapes the workspace. I can only read and write files inside the workspace folder.")
    return full_path, candidate.as_posix()
