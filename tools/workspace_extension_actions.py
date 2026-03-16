from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from tools.file_ops import FileOpError, resolve_workspace_path


def handle_consolidate_by_extension(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    root_raw = payload.get("root", ".")
    root_path, root_rel = resolve_workspace_path(runtime.workspace, root_raw)
    if not root_path.exists():
        return file_op_error(f"FILE_OP target not found: {root_rel}")
    if not root_path.is_dir():
        return file_op_error(f"FILE_OP consolidate_by_extension requires a directory root: {root_rel}")

    requested_extensions = runtime._normalize_extension_list(payload.get("extensions"))

    # Build exclusion sets — accept any plausible alias the planner might emit
    _EXCLUDE_ALIASES = (
        "exclude_files", "exclude", "excluded", "excluded_files",
        "skip", "skip_files", "ignore", "ignore_files",
        "omit", "omit_files", "except", "leave_out",
        "preserve", "preserve_files", "keep", "keep_files",
        "not_move", "keep_in_place",
    )
    excluded_paths: set[Path] = set()
    excluded_names: set[str] = set()
    exclude_raw = next(
        (payload[k] for k in _EXCLUDE_ALIASES if payload.get(k)),
        [],
    )
    if isinstance(exclude_raw, list):
        for raw_item in exclude_raw:
            item_str = str(raw_item or "").strip()
            if not item_str:
                continue
            full_path, _ = resolve_workspace_path(runtime.workspace, item_str)
            excluded_paths.add(full_path.resolve())
            excluded_names.add(Path(item_str).name.lower())

    runtime._raise_if_cancelled(cancel_token)
    inventory = runtime._build_extension_inventory(
        root_path,
        runtime.workspace.resolve(),
        extensions=requested_extensions or None,
    )
    files_by_extension = inventory["files_by_extension"]
    destination_map_payload = payload.get("destination_map") or {}
    if destination_map_payload and not isinstance(destination_map_payload, dict):
        raise FileOpError("FILE_OP field 'destination_map' must be a JSON object.")
    destinations = dict(inventory["destination_hints"])
    for raw_ext, raw_dest in (destination_map_payload or {}).items():
        ext = runtime._normalize_extension_token(raw_ext)
        if not ext:
            continue
        _, dest_rel = resolve_workspace_path(runtime.workspace, raw_dest)
        destinations[ext] = dest_rel

    create_missing = bool(payload.get("create_missing", True))
    dedupe_identical = bool(payload.get("dedupe_identical", True))
    workspace_root = runtime.workspace.resolve()
    planned_moves: list[dict[str, str]] = []
    duplicate_deletes: list[str] = []
    collisions: list[str] = []
    target_sources: dict[str, list[Path]] = {}
    target_existing: dict[str, Path] = {}

    for ext, files in files_by_extension.items():
        runtime._raise_if_cancelled(cancel_token)
        dest_rel = destinations.get(ext, "")
        if not dest_rel:
            continue
        dest_dir, dest_rel = resolve_workspace_path(runtime.workspace, dest_rel)
        destinations[ext] = dest_rel
        if create_missing:
            dest_dir.mkdir(parents=True, exist_ok=True)

        for src_path in files:
            if runtime._is_within_dir(src_path, dest_dir):
                continue
            if src_path.resolve() in excluded_paths or src_path.name.lower() in excluded_names:
                continue
            target_path = dest_dir / src_path.name
            target_rel = runtime._workspace_rel(target_path, workspace_root=workspace_root)
            src_rel = runtime._workspace_rel(src_path, workspace_root=workspace_root)
            target_sources.setdefault(target_rel, []).append(src_path)
            if target_path.exists():
                target_existing[target_rel] = target_path

    for target_rel, sources in target_sources.items():
        runtime._raise_if_cancelled(cancel_token)
        existing_target = target_existing.get(target_rel)
        if existing_target is not None:
            existing_sha1 = runtime._sha1_file(existing_target)
            for src_path in sources:
                src_rel = runtime._workspace_rel(src_path, workspace_root=workspace_root)
                if dedupe_identical and runtime._sha1_file(src_path) == existing_sha1:
                    duplicate_deletes.append(src_rel)
                else:
                    collisions.append(f"{src_rel} -> {target_rel}")
            continue

        if len(sources) == 1:
            src_path = sources[0]
            planned_moves.append(
                {
                    "src": runtime._workspace_rel(src_path, workspace_root=workspace_root),
                    "dst": target_rel,
                }
            )
            continue

        base_sha1 = runtime._sha1_file(sources[0])
        first_src = sources[0]
        planned_moves.append(
            {
                "src": runtime._workspace_rel(first_src, workspace_root=workspace_root),
                "dst": target_rel,
            }
        )
        for src_path in sources[1:]:
            src_rel = runtime._workspace_rel(src_path, workspace_root=workspace_root)
            if dedupe_identical and runtime._sha1_file(src_path) == base_sha1:
                duplicate_deletes.append(src_rel)
            else:
                collisions.append(f"{src_rel} -> {target_rel}")

    if collisions:
        return file_op_error(
            "FILE_OP consolidate_by_extension found name collisions with different content.",
            requested_root=root_rel,
            requested_extensions=sorted(requested_extensions),
            destinations=destinations,
            collisions=collisions[:20],
        )

    for item in planned_moves:
        runtime._raise_if_cancelled(cancel_token)
        src_path, _ = resolve_workspace_path(runtime.workspace, item["src"])
        dst_path, _ = resolve_workspace_path(runtime.workspace, item["dst"])
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))
    for rel_path in sorted(set(duplicate_deletes)):
        runtime._raise_if_cancelled(cancel_token)
        dup_path, _ = resolve_workspace_path(runtime.workspace, rel_path)
        if dup_path.exists():
            dup_path.unlink()

    runtime._raise_if_cancelled(cancel_token)
    after_inventory = runtime._build_extension_inventory(
        root_path,
        workspace_root,
        extensions=requested_extensions or None,
    )
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": (
            f"Consolidated {len(planned_moves)} files across {len(destinations)} extension groups"
            + (f" and removed {len(set(duplicate_deletes))} duplicate files." if duplicate_deletes else ".")
        ),
        "action": action,
        "requested_root": root_rel,
        "requested_extensions": sorted(requested_extensions),
        "destinations": destinations,
        "requested_moves": planned_moves,
        "deduplicated_files": sorted(set(duplicate_deletes)),
        "moved_count": len(planned_moves),
        "deduplicated_count": len(set(duplicate_deletes)),
        "extension_counts": after_inventory["extension_counts"],
        "folder_extension_counts": after_inventory["folder_extension_counts"],
        "empty_dirs": after_inventory["empty_dirs"][:40],
    }


def handle_delete_empty_dirs(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    root_raw = payload.get("root", ".")
    root_path, root_rel = resolve_workspace_path(runtime.workspace, root_raw)
    if not root_path.exists():
        return file_op_error(f"FILE_OP target not found: {root_rel}")
    if not root_path.is_dir():
        return file_op_error(f"FILE_OP delete_empty_dirs requires a directory root: {root_rel}")

    excluded = set()
    for raw_path in runtime._normalize_path_list(payload.get("exclude") or payload.get("exclude_paths") or [".__never_used__"]):
        if raw_path == ".__never_used__":
            continue
        full_path, rel_path = resolve_workspace_path(runtime.workspace, raw_path)
        excluded.add(full_path.resolve())
        excluded.add(Path(rel_path))
    deleted_paths: list[str] = []
    for dir_path in sorted((path for path in root_path.rglob("*") if path.is_dir()), key=lambda item: len(item.parts), reverse=True):
        runtime._raise_if_cancelled(cancel_token)
        if dir_path.resolve() in excluded:
            continue
        if dir_path == root_path:
            continue
        if any(parent.resolve() in excluded for parent in dir_path.parents):
            continue
        if any(dir_path.iterdir()):
            continue
        rel = runtime._workspace_rel(dir_path)
        dir_path.rmdir()
        deleted_paths.append(rel)
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Deleted {len(deleted_paths)} empty directories under {root_rel}.",
        "action": action,
        "requested_root": root_rel,
        "requested_paths": deleted_paths,
        "deleted_dir_count": len(deleted_paths),
    }
