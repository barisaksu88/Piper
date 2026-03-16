from __future__ import annotations

import os
from pathlib import Path
from typing import Dict


def canonical_path(path: Path) -> Path:
    return Path(os.path.normcase(os.path.realpath(path)))


def file_extension_key(path: Path) -> str:
    return path.suffix.lower() or "[no_ext]"


def top_level_bucket(root_path: Path, path: Path) -> str:
    rel = path.relative_to(root_path)
    return rel.parts[0] if len(rel.parts) > 1 else "[root]"


def semantic_folder_score(ext: str, folder_name: str) -> int:
    name = str(folder_name or "").strip().lower()
    if not name or name == "[root]":
        return 0

    category_aliases = {
        "images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"},
        "text": {".txt", ".md", ".rtf"},
        "documents": {".txt", ".md", ".doc", ".docx", ".pdf", ".rtf"},
        "scripts": {".py", ".js", ".ts", ".sh", ".ps1", ".bat"},
        "code": {".py", ".js", ".ts", ".java", ".cpp", ".c", ".rs", ".go"},
        "data": {".json", ".yaml", ".yml", ".csv", ".tsv", ".xml"},
        "logs": {".log", ".txt"},
    }
    alias_words = {
        "images": {"image", "images", "img", "photo", "photos", "picture", "pictures", "pic", "pics", "screenshot", "screenshots"},
        "text": {"text", "texts", "text_file", "text_files", "note", "notes"},
        "documents": {"document", "documents", "doc", "docs"},
        "scripts": {"script", "scripts", "python", "python_script", "python_scripts", "src", "source", "code"},
        "data": {"data", "dataset", "datasets", "json", "config", "configs"},
        "logs": {"log", "logs"},
    }

    score = 0
    ext_name = ext.lstrip(".")
    if ext_name and (name == ext_name or name == f".{ext_name}" or ext_name in name):
        score = max(score, 1)

    for category, extensions in category_aliases.items():
        if ext in extensions and any(word in name for word in alias_words.get(category, set())):
            score = max(score, 3)
    return score


def suggest_extension_folder_name(ext: str) -> str:
    category_defaults = {
        ".png": "images",
        ".jpg": "images",
        ".jpeg": "images",
        ".gif": "images",
        ".webp": "images",
        ".bmp": "images",
        ".svg": "images",
        ".txt": "text",
        ".md": "text",
        ".log": "logs",
        ".py": "python_scripts",
        ".json": "data",
        ".yaml": "data",
        ".yml": "data",
        ".csv": "data",
        ".tsv": "data",
        ".xml": "data",
    }
    if ext == "[no_ext]":
        return "files"
    return category_defaults.get(ext, ext.lstrip(".") or "files")


def infer_extension_destinations(
    root_path: Path,
    workspace_root: Path,
    files_by_extension: Dict[str, list[Path]],
) -> Dict[str, str]:
    root_rel = canonical_path(root_path).relative_to(canonical_path(workspace_root)).as_posix()
    root_prefix = "" if root_rel == "." else root_rel
    destinations: Dict[str, str] = {}

    for ext, files in files_by_extension.items():
        counts: Dict[str, int] = {}
        for path in files:
            bucket = top_level_bucket(root_path, path)
            counts[bucket] = counts.get(bucket, 0) + 1

        candidates = [bucket for bucket in counts if bucket != "[root]"]
        if candidates:
            best_bucket = max(
                candidates,
                key=lambda bucket: (
                    counts.get(bucket, 0),
                    semantic_folder_score(ext, bucket),
                    int(not str(bucket).startswith(".")),
                    -len(str(bucket)),
                ),
            )
        else:
            best_bucket = suggest_extension_folder_name(ext)

        dest_rel = best_bucket if not root_prefix else f"{root_prefix}/{best_bucket}"
        destinations[ext] = dest_rel

    return destinations


def build_extension_inventory(
    root_path: Path,
    workspace_root: Path,
    *,
    extensions: set[str] | None = None,
) -> Dict[str, object]:
    files_by_extension: Dict[str, list[Path]] = {}
    folder_extension_counts: Dict[str, Dict[str, int]] = {}
    empty_dirs: list[str] = []

    for path in sorted(root_path.rglob("*")):
        if path.is_dir():
            if not any(path.iterdir()):
                empty_dirs.append(canonical_path(path).relative_to(canonical_path(workspace_root)).as_posix())
            continue
        if not path.is_file():
            continue
        ext = file_extension_key(path)
        if extensions and ext not in extensions:
            continue
        files_by_extension.setdefault(ext, []).append(path)
        bucket = top_level_bucket(root_path, path)
        bucket_counts = folder_extension_counts.setdefault(bucket, {})
        bucket_counts[ext] = bucket_counts.get(ext, 0) + 1

    extension_counts = {ext: len(paths) for ext, paths in files_by_extension.items()}
    destination_hints = infer_extension_destinations(root_path, workspace_root, files_by_extension)
    root_files_by_extension = {
        ext: sorted(
            canonical_path(path).relative_to(canonical_path(workspace_root)).as_posix()
            for path in paths
            if top_level_bucket(root_path, path) == "[root]"
        )[:20]
        for ext, paths in files_by_extension.items()
    }

    return {
        "files_by_extension": files_by_extension,
        "extension_counts": dict(sorted(extension_counts.items(), key=lambda item: (-item[1], item[0]))),
        "folder_extension_counts": {
            folder: dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
            for folder, counts in sorted(folder_extension_counts.items())
        },
        "destination_hints": destination_hints,
        "root_files_by_extension": root_files_by_extension,
        "empty_dirs": sorted(empty_dirs),
    }
