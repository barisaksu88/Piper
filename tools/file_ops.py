from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


class FileOpError(ValueError):
    pass


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
        raise FileOpError("FILE_OP path escapes the workspace.")
    return full_path, candidate.as_posix()
