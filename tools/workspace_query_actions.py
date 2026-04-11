from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from tools.file_ops import resolve_workspace_path


def _normalize_search_fragment(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return " ".join(cleaned.split())


def _looks_like_exact_filename_query(query: str) -> bool:
    raw = str(query or "").strip()
    return bool(Path(raw).suffix or "/" in raw or "\\" in raw)


def _token_prefix_match(query_norm: str, candidate_norm: str) -> bool:
    """Return True when every query token prefix-matches at least one candidate
    token (or vice versa).

    Handles STT one-character truncations such as:
      "grocer list" → "grocery list"   ("grocer" is a prefix of "grocery")
      "appoint" → "appointment"
    Requires at least 3 characters per query token to avoid trivial matches.
    """
    q_tokens = query_norm.split()
    c_tokens = candidate_norm.split()
    if not q_tokens or not c_tokens:
        return False
    checked = 0
    for qt in q_tokens:
        if len(qt) < 3:
            continue  # skip very short tokens to avoid noise
        if not any(ct.startswith(qt) or qt.startswith(ct) for ct in c_tokens):
            return False
        checked += 1
    return checked > 0  # vacuously True if no token long enough → False


def handle_list_tree(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    root_raw = payload.get("root", payload.get("path", "."))
    root_path, root_rel = resolve_workspace_path(runtime.workspace, root_raw)
    if not root_path.exists():
        return file_op_error(f"FILE_OP target not found: {root_rel}")
    if not root_path.is_dir():
        return file_op_error(f"FILE_OP list_tree requires a directory: {root_rel}")
    max_depth = int(payload.get("max_depth", 6))
    include_dirs = bool(payload.get("include_dirs", True))
    include_files = bool(payload.get("include_files", True))
    entries: list[dict[str, Any]] = []
    ext_counts: dict[str, int] = {}
    workspace_root = runtime.workspace.resolve()
    base_depth = len(Path(runtime._workspace_rel(root_path, workspace_root=workspace_root)).parts)
    for path in sorted(root_path.rglob("*")):
        runtime._raise_if_cancelled(cancel_token)
        rel = runtime._workspace_rel(path, workspace_root=workspace_root)
        depth = len(Path(rel).parts) - base_depth
        if depth > max_depth:
            continue
        if path.is_dir():
            if include_dirs:
                entries.append({"path": rel, "kind": "dir"})
            continue
        if path.is_file() and include_files:
            ext = path.suffix.lower() or "[no_ext]"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            entries.append({"path": rel, "kind": "file", "size": int(path.stat().st_size)})
    top_level_dirs = sorted(
        path.relative_to(root_path).parts[0]
        for path in root_path.iterdir()
        if path.is_dir()
    ) if root_path.exists() else []
    top_level_files = sorted(
        path.relative_to(root_path).as_posix()
        for path in root_path.iterdir()
        if path.is_file()
    ) if root_path.exists() else []
    top_level_dir_file_counts = {}
    if root_path.exists():
        for path in sorted(root_path.iterdir(), key=lambda item: item.name.lower()):
            if path.is_dir():
                top_level_dir_file_counts[path.name] = sum(1 for item in path.rglob("*") if item.is_file())
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Listed {len(entries)} workspace entries under {root_rel}.",
        "action": action,
        "requested_root": root_rel,
        "entry_count": len(entries),
        "top_level_dirs": top_level_dirs[:20],
        "top_level_files": top_level_files[:20],
        "top_level_dir_file_counts": top_level_dir_file_counts,
        "extension_counts": dict(sorted(ext_counts.items(), key=lambda item: (-item[1], item[0]))[:12]),
        "entries": entries,
    }


def handle_find_paths(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    root_raw = payload.get("root", ".")
    root_path, root_rel = resolve_workspace_path(runtime.workspace, root_raw)
    if not root_path.exists():
        return file_op_error(f"FILE_OP target not found: {root_rel}")
    if not root_path.is_dir():
        return file_op_error(f"FILE_OP find_paths requires a directory root: {root_rel}")
    query = str(payload.get("query") or payload.get("pattern") or payload.get("name") or "").strip()
    if not query:
        return file_op_error("FILE_OP find_paths requires 'query', 'pattern', or 'name'.")
    mode = str(payload.get("mode", "basename") or "basename").strip().lower()
    if mode not in {"basename", "glob", "substring"}:
        return file_op_error("FILE_OP find_paths mode must be one of: basename, glob, substring.")
    wildcard_query = any(char in query for char in "*?[]")
    max_results = max(1, min(int(payload.get("max_results", 50)), 200))
    include_dirs = bool(payload.get("include_dirs", False))
    include_files = bool(payload.get("include_files", True))

    query_l = query.lower()
    query_norm = _normalize_search_fragment(query)
    exact_filename_query = _looks_like_exact_filename_query(query)
    workspace_root = runtime.workspace.resolve()
    matches: list[str] = []
    for path in sorted(root_path.rglob("*")):
        runtime._raise_if_cancelled(cancel_token)
        if path.is_dir() and not include_dirs:
            continue
        if path.is_file() and not include_files:
            continue
        rel = runtime._workspace_rel(path, workspace_root=workspace_root)
        candidate_rel = rel.lower()
        candidate_name = path.name.lower()
        candidate_stem = path.stem.lower()
        candidate_name_norm = _normalize_search_fragment(candidate_name)
        candidate_stem_norm = _normalize_search_fragment(candidate_stem)
        candidate_rel_norm = _normalize_search_fragment(candidate_rel)
        matched = False
        if mode == "basename":
            if wildcard_query:
                matched = fnmatch.fnmatch(candidate_name, query_l)
            else:
                matched = candidate_name == query_l or candidate_stem == query_l
                if not matched and query_norm:
                    matched = query_norm == candidate_name_norm or query_norm == candidate_stem_norm
                if not matched and query_norm and not exact_filename_query:
                    matched = (
                        query_norm in candidate_name_norm
                        or query_norm in candidate_stem_norm
                    )
                if not matched and query_norm and not exact_filename_query:
                    # Token-prefix overlap for STT robustness:
                    # "grocer list" should find "grocery list.txt" because
                    # "grocer" is a prefix of "grocery" and "list" == "list".
                    # Each query token must prefix-match at least one candidate
                    # token (or the reverse — handles truncations both ways).
                    matched = _token_prefix_match(query_norm, candidate_name_norm) or \
                              _token_prefix_match(query_norm, candidate_stem_norm)
        elif mode == "glob":
            matched = fnmatch.fnmatch(candidate_name, query_l) or fnmatch.fnmatch(candidate_rel, query_l)
        elif mode == "substring":
            matched = query_l in candidate_name or query_l in candidate_rel
            if not matched and query_norm:
                matched = query_norm in candidate_name_norm or query_norm in candidate_rel_norm
        if matched:
            matches.append(rel)
            if len(matches) >= max_results:
                break

    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Found {len(matches)} matches for {query} under {root_rel}.",
        "action": action,
        "requested_root": root_rel,
        "requested_query": query,
        "requested_mode": mode,
        "match_count": len(matches),
        "matches": matches,
    }


def handle_extension_inventory(runtime: Any, payload: dict[str, Any], action: str, file_op_error, *, cancel_token=None) -> dict[str, Any]:
    root_raw = payload.get("root", ".")
    root_path, root_rel = resolve_workspace_path(runtime.workspace, root_raw)
    if not root_path.exists():
        return file_op_error(f"FILE_OP target not found: {root_rel}")
    if not root_path.is_dir():
        return file_op_error(f"FILE_OP extension_inventory requires a directory root: {root_rel}")
    requested_extensions = runtime._normalize_extension_list(payload.get("extensions"))
    runtime._raise_if_cancelled(cancel_token)
    inventory = runtime._build_extension_inventory(
        root_path,
        runtime.workspace.resolve(),
        extensions=requested_extensions or None,
    )
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Built extension inventory for {sum(inventory['extension_counts'].values())} files under {root_rel}.",
        "action": action,
        "requested_root": root_rel,
        "requested_extensions": sorted(requested_extensions),
        "extension_counts": inventory["extension_counts"],
        "folder_extension_counts": inventory["folder_extension_counts"],
        "destination_hints": inventory["destination_hints"],
        "root_files_by_extension": inventory["root_files_by_extension"],
        "empty_dirs": inventory["empty_dirs"][:40],
        "match_count": sum(inventory["extension_counts"].values()),
        "matches": [
            path
            for paths in inventory["root_files_by_extension"].values()
            for path in paths
        ][:20],
    }


def handle_read_text(runtime: Any, payload: dict[str, Any], action: str, file_op_error) -> dict[str, Any]:
    full_path, rel_path = resolve_workspace_path(runtime.workspace, payload.get("path"))
    if not full_path.exists():
        return file_op_error(f"FILE_OP target not found: {rel_path}")
    content = full_path.read_text(encoding="utf-8")
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Read text file: {rel_path}",
        "action": action,
        "path": rel_path,
        "requested_path": rel_path,
        "files": {rel_path: content},
    }


def handle_read_many(runtime: Any, payload: dict[str, Any], action: str, *, cancel_token=None) -> dict[str, Any]:
    files: dict[str, str] = {}
    missing: list[str] = []
    for raw_path in runtime._normalize_path_list(payload.get("paths")):
        runtime._raise_if_cancelled(cancel_token)
        full_path, rel_path = resolve_workspace_path(runtime.workspace, raw_path)
        if not full_path.exists():
            missing.append(rel_path)
            continue
        files[rel_path] = full_path.read_text(encoding="utf-8")
    if missing:
        return {
            "tool": "FILE_OP",
            "status": "FAILED",
            "summary": f"FILE_OP could not read all requested files. Missing: {', '.join(missing)}",
            "action": action,
            "files": files,
            "missing_files": missing,
            "workspace_changed": False,
            "created_files": [],
            "updated_files": [],
            "deleted_files": [],
            "created_dirs": [],
            "deleted_dirs": [],
            "evidence_files": list(files.keys())[:6],
            "file_snippets": {},
        }
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": f"Read {len(files)} files.",
        "action": action,
        "requested_paths": sorted(files.keys()),
        "files": files,
    }
