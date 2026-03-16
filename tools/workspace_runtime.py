from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict

from core.runtime_control import CancellationToken, OperationCancelled
from tools.file_ops import FileOpError, parse_payload as parse_file_op_payload, resolve_workspace_path
from tools.workspace_extension_ops import (
    build_extension_inventory,
    canonical_path,
    file_extension_key,
    infer_extension_destinations,
    semantic_folder_score,
    suggest_extension_folder_name,
    top_level_bucket,
)
from tools.interpreter import Interpreter
from tools.workspace_file_actions import execute_file_op


class WorkspaceToolRuntime:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _canonical_path(path: Path) -> Path:
        return canonical_path(path)

    def _workspace_rel(self, path: Path, *, workspace_root: Path | None = None) -> str:
        canonical_root = self._canonical_path(workspace_root or self.workspace)
        canonical_path = self._canonical_path(path)
        return canonical_path.relative_to(canonical_root).as_posix()

    def _is_within_dir(self, path: Path, parent: Path) -> bool:
        try:
            self._canonical_path(path).relative_to(self._canonical_path(parent))
            return True
        except ValueError:
            return False

    def _strip_fences(self, content: str) -> str:
        """Removes markdown code fences from content."""
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].strip().startswith("```"):
                lines.pop(0)
            if lines and lines[-1].strip() == "```":
                lines.pop()
            return "\n".join(lines).strip()
        return content

    @staticmethod
    def _normalize_run_code(code: str) -> str:
        if "\\n" in code and "\n" not in code:
            try:
                code = bytes(code, "utf-8").decode("unicode_escape")
            except Exception:
                code = (
                    code.replace("\\r\\n", "\n")
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace('\\"', '"')
                    .replace("\\'", "'")
                )
        lines = []
        for line in code.splitlines():
            if line.strip().lower() == "<python code>":
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_run_workspace_script(code: str) -> str:
        text = str(code or "").strip()
        if not text:
            return ""
        try:
            parsed = ast.parse(text, mode="exec")
        except SyntaxError:
            parsed = None
        if (
            parsed is not None
            and len(parsed.body) == 1
            and isinstance(parsed.body[0], ast.Expr)
            and isinstance(parsed.body[0].value, ast.Call)
        ):
            call = parsed.body[0].value
            if isinstance(call.func, ast.Name) and call.func.id == "run_workspace_script" and len(call.args) == 1 and not call.keywords:
                arg = call.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return arg.value.strip()

        if "subprocess." in text and ".py" in text:
            matches = re.findall(r"['\"]([^'\"]+\.py)['\"]", text, flags=re.IGNORECASE)
            unique_matches: list[str] = []
            seen: set[str] = set()
            for match in matches:
                candidate = str(match).strip()
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                unique_matches.append(candidate)
            if len(unique_matches) == 1:
                return unique_matches[0]
        return ""

    def _request_workspace_python_script_launch(
        self,
        raw_path: str,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> Dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        full_path, rel_path = resolve_workspace_path(self.workspace, raw_path)
        if full_path.suffix.lower() != ".py":
            raise FileOpError("run_workspace_script only supports relative .py files inside the workspace.")
        if not full_path.is_file():
            raise FileOpError(f"Workspace script not found: {rel_path}")

        return {
            "tool": "RUN_CODE",
            "action": "run_workspace_script",
            "status": "EXECUTED",
            "summary": f"Queued workspace script for embedded Code tab launch: {rel_path}",
            "stdout": "",
            "stderr": "",
            "return_code": None,
            "launched_script": rel_path,
            "script_running": True,
            "launch_mode": "embedded_code_tab",
            "requires_interaction": True,
            "evidence_files": [rel_path],
        }

    def _workspace_snapshot(self) -> Dict[str, Dict[str, int]]:
        snapshot: Dict[str, Dict[str, int]] = {}
        for path in self.workspace.rglob("*"):
            rel = path.relative_to(self.workspace).as_posix()
            if not rel or rel == "temp_exec.py" or "__pycache__" in path.parts:
                continue
            if path.is_dir():
                snapshot[rel] = {"kind": 0}
                continue
            if not path.is_file():
                continue
            stat = path.stat()
            snapshot[rel] = {"kind": 1, "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
        return snapshot

    def _read_workspace_snippet(self, rel_path: str, *, max_chars: int = 6000) -> Dict[str, Any]:
        path = self.workspace / rel_path
        if not path.exists() or not path.is_file():
            return {"status": "missing"}
        try:
            full_text = path.read_text(encoding="utf-8")
            truncated = len(full_text) > max_chars
            preview = full_text[:max_chars]
            if truncated:
                preview = preview + "\n[PREVIEW TRUNCATED - file on disk is longer]"
            digest = hashlib.sha1(full_text.encode("utf-8", errors="replace")).hexdigest()
            return {
                "status": "text",
                "sha1": digest,
                "content": preview,
                "truncated": truncated,
                "full_char_count": len(full_text),
                "preview_char_count": len(preview),
            }
        except Exception:
            stat = path.stat()
            return {
                "status": "binary",
                "size_bytes": int(stat.st_size),
            }

    def _workspace_diff(self, before: Dict[str, Dict[str, int]], after: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        before_keys = set(before)
        after_keys = set(after)
        created_all = sorted(after_keys - before_keys)
        deleted_all = sorted(before_keys - after_keys)
        updated_all = sorted(
            path for path in (before_keys & after_keys) if before[path] != after[path]
        )
        created_dirs = [path for path in created_all if after[path].get("kind") == 0]
        created_files = [path for path in created_all if after[path].get("kind") == 1]
        deleted_dirs = [path for path in deleted_all if before[path].get("kind") == 0]
        deleted_files = [path for path in deleted_all if before[path].get("kind") == 1]
        updated_files = [path for path in updated_all if after[path].get("kind") == 1]
        changed = created_files + updated_files + deleted_files + created_dirs + deleted_dirs
        evidence_paths = changed[:6]
        return {
            "workspace_changed": bool(changed),
            "created_files": created_files,
            "updated_files": updated_files,
            "deleted_files": deleted_files,
            "created_dirs": created_dirs,
            "deleted_dirs": deleted_dirs,
            "evidence_files": evidence_paths,
            "file_snippets": {
                rel_path: self._read_workspace_snippet(rel_path)
                for rel_path in evidence_paths
                if rel_path not in deleted_files and rel_path not in created_dirs and rel_path not in deleted_dirs
            },
        }

    def _sanitize(self, filename: str) -> str:
        return os.path.basename(filename).replace("..", "")

    def _file_op_error(self, message: str, *, action: str = "", **extra: Any) -> Dict[str, Any]:
        payload = {
            "tool": "FILE_OP",
            "status": "FAILED",
            "summary": message,
            "action": action,
            "workspace_changed": False,
            "created_files": [],
            "updated_files": [],
            "deleted_files": [],
            "created_dirs": [],
            "deleted_dirs": [],
            "evidence_files": [],
            "file_snippets": {},
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _raise_if_cancelled(cancel_token: CancellationToken | None) -> None:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

    @staticmethod
    def _normalize_text_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False, indent=2)

    @staticmethod
    def _normalize_json_object(value: Any, *, field_name: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise FileOpError(f"FILE_OP field '{field_name}' must be a JSON object.")
        return value

    @staticmethod
    def _normalize_path_list(value: Any) -> list[str]:
        if not isinstance(value, list) or not value:
            raise FileOpError("FILE_OP field 'paths' must be a non-empty JSON array.")
        return [str(item) for item in value]

    @staticmethod
    def _normalize_move_items(value: Any, *, field_name: str) -> list[dict[str, str]]:
        if not isinstance(value, list) or not value:
            raise FileOpError(f"FILE_OP field '{field_name}' must be a non-empty JSON array.")
        items: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                raise FileOpError(f"Each item in FILE_OP field '{field_name}' must be a JSON object.")
            src = str(item.get("src") or "").strip()
            dst = str(item.get("dst") or "").strip()
            if not src or not dst:
                raise FileOpError(f"Each item in FILE_OP field '{field_name}' must include 'src' and 'dst'.")
            items.append({"src": src, "dst": dst})
        return items

    @staticmethod
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
        return aliases.get(normalized, normalized)

    @staticmethod
    def _normalize_extension_token(token: Any) -> str:
        raw = str(token or "").strip().lower()
        if not raw:
            return ""
        if raw == "[no_ext]":
            return raw
        if not raw.startswith("."):
            raw = f".{raw.lstrip('.')}"
        return raw

    @classmethod
    def _normalize_extension_list(cls, value: Any) -> set[str]:
        if value in (None, "", []):
            return set()
        if isinstance(value, str):
            items = [part for part in re.split(r"[\s,]+", value) if part]
        elif isinstance(value, list):
            items = [str(part) for part in value]
        else:
            raise FileOpError("FILE_OP field 'extensions' must be a string or JSON array.")
        normalized = {cls._normalize_extension_token(item) for item in items}
        return {item for item in normalized if item}

    @staticmethod
    def _file_extension_key(path: Path) -> str:
        return file_extension_key(path)

    @staticmethod
    def _top_level_bucket(root_path: Path, path: Path) -> str:
        return top_level_bucket(root_path, path)

    @staticmethod
    def _sha1_file(path: Path) -> str:
        digest = hashlib.sha1()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _semantic_folder_score(cls, ext: str, folder_name: str) -> int:
        return semantic_folder_score(ext, folder_name)

    @classmethod
    def _suggest_extension_folder_name(cls, ext: str) -> str:
        return suggest_extension_folder_name(ext)

    @classmethod
    def _infer_extension_destinations(
        cls,
        root_path: Path,
        workspace_root: Path,
        files_by_extension: Dict[str, list[Path]],
    ) -> Dict[str, str]:
        return infer_extension_destinations(root_path, workspace_root, files_by_extension)

    @classmethod
    def _build_extension_inventory(
        cls,
        root_path: Path,
        workspace_root: Path,
        *,
        extensions: set[str] | None = None,
    ) -> Dict[str, Any]:
        return build_extension_inventory(root_path, workspace_root, extensions=extensions)

    def build_extension_inventory(
        self,
        root_path: Path,
        workspace_root: Path,
        *,
        extensions: set[str] | None = None,
    ) -> Dict[str, Any]:
        return self._build_extension_inventory(
            root_path,
            workspace_root,
            extensions=extensions,
        )


    def exec_file_op(self, payload_text: str, *, cancel_token: CancellationToken | None = None) -> Dict[str, Any]:
        try:
            self._raise_if_cancelled(cancel_token)
            payload = parse_file_op_payload(self._strip_fences(payload_text))
            action = self._normalize_file_op_action(payload.get("action", ""))
            file_op_error = lambda message, **extra: self._file_op_error(message, action=action, **extra)
            if not action:
                return self._file_op_error("FILE_OP action is required.")

            before = self._workspace_snapshot()
            result = execute_file_op(self, payload, action, file_op_error, cancel_token=cancel_token)
            if "workspace_changed" not in result:
                after = self._workspace_snapshot()
                evidence = self._workspace_diff(before, after)
                result.update(evidence)
            if action in {"read_text", "read_many"}:
                files = result.get("files") or {}
                if not result.get("evidence_files"):
                    result["evidence_files"] = list(files.keys())[:6]
            if action == "list_tree":
                entries = result.get("entries") or []
                if not result.get("evidence_files"):
                    result["evidence_files"] = [str(item.get("path")) for item in entries[:6]]
            if action == "find_paths":
                matches = result.get("matches") or []
                if not result.get("evidence_files"):
                    result["evidence_files"] = [str(item) for item in matches[:6]]
            return result
        except OperationCancelled:
            raise
        except FileOpError as e:
            return self._file_op_error(str(e), action=locals().get("action", ""))
        except Exception as e:
            return self._file_op_error(f"FILE_OP system error: {e}", action=locals().get("action", ""))

    def exec_run_code(self, code: str, *, cancel_token: CancellationToken | None = None) -> str:
        """Executes Python code."""
        try:
            clean_code = self._normalize_run_code(self._strip_fences(code))
            before = self._workspace_snapshot()
            launch_target = self._parse_run_workspace_script(clean_code)
            if launch_target:
                result = self._request_workspace_python_script_launch(launch_target, cancel_token=cancel_token)
                after = self._workspace_snapshot()
                evidence = self._workspace_diff(before, after)
                result.update(evidence)
                return result
            interpreter = Interpreter(self.workspace)
            report = interpreter.run_report(clean_code, cancel_token=cancel_token)
            after = self._workspace_snapshot()
            evidence = self._workspace_diff(before, after)
            result = {
                "tool": "RUN_CODE",
                "status": report.status.upper(),
                "summary": report.summary,
                "stdout": report.stdout,
                "stderr": report.stderr,
                "return_code": report.return_code,
            }
            result.update(evidence)
            return result
        except OperationCancelled:
            raise
        except Exception as e:
            return {
                "tool": "RUN_CODE",
                "status": "FAILED",
                "summary": f"Execution System Error: {e}",
                "stdout": "",
                "stderr": "",
                "return_code": None,
                "workspace_changed": False,
                "created_files": [],
                "updated_files": [],
                "deleted_files": [],
                "created_dirs": [],
                "deleted_dirs": [],
                "evidence_files": [],
                "file_snippets": {},
            }
