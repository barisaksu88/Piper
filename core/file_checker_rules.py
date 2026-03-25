from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from core.contracts import FileCheckDecision, StageCard
from core.file_stage_policy import FileStagePolicy

# Matches "except X", "excluding X", "leave out X", "skip X", "ignore X", "omit X",
# "except for X", "not including X" — capturing the minimal subject token.
# Uses a non-greedy match that stops at common delimiters (file suffix words, punctuation,
# relative clauses, end-of-string) so short nouns like "FCOM" are captured cleanly.
_EXCLUSION_CLAUSE_RE = re.compile(
    r"(?:except(?:\s+for)?|exclud(?:e|ing)|leave\s+out|skip|ignore|omit|not\s+including)"
    r"\s+(?:the\s+)?(\w[\w.\-]{1,30}?)"
    r"(?=\s+files?|\s+pdf|\s+doc|\s+which|\s+that|\s+from|\s+must|\s+should|\s*[,;.!?]|\s*$)",
    re.IGNORECASE,
)


class LocalFileOpRuleChecker:
    def __init__(self, workspace: Path, stage: StageCard, preferred_paths: list[str] | None = None) -> None:
        self.workspace = Path(workspace)
        self.stage = stage
        self.preferred_paths = [str(path).strip().replace("\\", "/") for path in (preferred_paths or []) if str(path).strip()]
        self.stage_goal_text = str(stage.get("stage_goal", ""))
        self.stage_success_text = str(stage.get("success_condition", ""))
        self.stage_context_text = " ".join(str(item) for item in (stage.get("context") or []))
        self.stage_raw_text = " ".join(
            part
            for part in (self.stage_goal_text, self.stage_success_text, self.stage_context_text)
            if part
        )
        self.stage_eval_text = " ".join(
            [
                self.stage_goal_text,
                self.stage_success_text,
            ]
        ).lower()
        self.stage_intent_text = FileStagePolicy.stage_intent_text(stage)

    def evaluate(self, tool_result: Any) -> FileCheckDecision | None:
        if not isinstance(tool_result, dict):
            return None
        if str(tool_result.get("tool", "")).upper() != "FILE_OP":
            return None

        action = str(tool_result.get("action", "")).lower()
        requested_path = str(tool_result.get("requested_path") or tool_result.get("path") or "").strip()

        if action == "extension_inventory":
            return self._check_extension_inventory(tool_result)
        if action == "ensure_dir":
            return self._check_ensure_dir(requested_path)
        if action == "ensure_dirs":
            return self._check_ensure_dirs(tool_result)
        if action == "write_text":
            return self._check_write_text(requested_path, tool_result)
        if action == "verify_text_state":
            return self._check_text_state(requested_path, tool_result)
        if action == "write_json":
            return self._check_write_json(requested_path, tool_result)
        if action == "update_json":
            return self._check_update_json(requested_path, tool_result)
        if action == "consolidate_by_extension":
            return self._check_consolidate_by_extension(tool_result)
        if action == "delete_empty_dirs":
            return self._check_delete_empty_dirs(tool_result)
        if action in {"move_path", "move_many"}:
            return self._check_moves(tool_result)
        if action in {"copy_path", "copy_many"}:
            return self._check_copies(tool_result)
        if action in {"delete_path", "delete_many"}:
            return self._check_deletes(tool_result)
        return None

    def evaluate_current_stage_state(self) -> FileCheckDecision | None:
        code_edit_decision = self._evaluate_current_code_edit_state()
        if code_edit_decision is not None:
            return code_edit_decision
        if FileStagePolicy.stage_is_extension_file_reorg(self.stage):
            return None
        synthetic = self._build_current_state_synthetic_result()
        if synthetic is None:
            return None
        return self.evaluate(synthetic)

    def _resolved(self, rel_path: str) -> Path:
        return (self.workspace / rel_path).resolve()

    @staticmethod
    def _canonical(path: Path) -> Path:
        return Path(os.path.normcase(os.path.realpath(path)))

    def _rel_to_workspace(self, path: Path) -> str:
        return self._canonical(path).relative_to(self._canonical(self.workspace)).as_posix()

    def _is_within(self, path: Path, root: Path) -> bool:
        try:
            self._canonical(path).relative_to(self._canonical(root))
            return True
        except ValueError:
            return False

    def _stage_scope_file_count(self) -> int:
        quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", self.stage_eval_text)
        candidates = [next((part for part in group if part), "") for group in quoted]
        for candidate in candidates:
            cleaned = str(candidate).strip().strip("/\\")
            if not cleaned:
                continue
            scoped_path = self._resolved(cleaned)
            if scoped_path.is_dir():
                return sum(1 for item in scoped_path.rglob("*") if item.is_file())
            if scoped_path.is_file():
                return 1
        return sum(1 for item in self.workspace.rglob("*") if item.is_file())

    def _stage_expected_item_count(self) -> int:
        counts = [
            int(match.group(1))
            for match in re.finditer(r"\b(\d+)\s+(?:entries|entry|files|file|items|item|paths|path)\b", self.stage_eval_text)
        ]
        return max(counts) if counts else 0

    def _stage_requires_broad_coverage(self) -> bool:
        if self._stage_expected_item_count() > 0:
            return True
        return bool(
            re.search(
                r"\b(all files|all entries|all items|entire workspace|whole workspace|fully sorted|workspace is tidy|everything|remaining entries|all identified files|correct directories|proper directories|designated directories|categorize all|sort all|organize all)\b",
                self.stage_intent_text,
            )
            or re.search(r"\b(reorgani[sz]e|organi[sz]e|sort|categori[sz]e)\b.*\bworkspace\b", self.stage_intent_text)
        )

    def _directories_only_are_partial(self) -> bool:
        return bool(
            re.search(
                r"\b(move|relocate|rename|copy|delete|remove|distribute|sort into|organize files|empty|clear out|rewrite|create .* file|write .* file|save .* file)\b",
                self.stage_intent_text,
            )
        )

    def _build_current_state_synthetic_result(self) -> dict[str, Any] | None:
        ordered_paths = self._ordered_file_targets()
        intent = self.stage_intent_text
        text_state_synthetic = self._build_text_state_synthetic_result(ordered_paths, intent)
        if text_state_synthetic is not None:
            return text_state_synthetic

        if self._stage_implies_copy_action(intent) and len(ordered_paths) >= 2:
            return {
                "tool": "FILE_OP",
                "action": "copy_path",
                "requested_copies": [{"src": ordered_paths[0], "dst": ordered_paths[1]}],
                "current_state_only": True,
            }
        if re.search(r"\b(move|rename|relocate)\b", intent) and len(ordered_paths) >= 2:
            return {
                "tool": "FILE_OP",
                "action": "move_path",
                "requested_moves": [{"src": ordered_paths[0], "dst": ordered_paths[1]}],
                "current_state_only": True,
            }
        if re.search(r"\b(delete|remove)\b", intent) and ordered_paths:
            return {
                "tool": "FILE_OP",
                "action": "delete_path",
                "requested_paths": [ordered_paths[0]],
                "current_state_only": True,
            }
        if re.search(r"\b(create|write|rewrite|overwrite|save|update|edit|append)\b", intent) and ordered_paths:
            path = ordered_paths[0]
            expected_content = self._extract_expected_text_content()
            if expected_content is None:
                return None
            return {
                "tool": "FILE_OP",
                "action": "write_text",
                "requested_path": path,
                "path": path,
                "requested_content_sha1": hashlib.sha1(expected_content.encode("utf-8", errors="replace")).hexdigest(),
                "current_state_only": True,
            }
        if re.search(r"\b(directory|directories|folder|folders)\b", intent):
            directory_targets = self._directory_targets()
            if len(directory_targets) == 1:
                return {
                    "tool": "FILE_OP",
                    "action": "ensure_dir",
                    "requested_path": directory_targets[0],
                    "path": directory_targets[0],
                    "current_state_only": True,
                }
            if len(directory_targets) > 1:
                return {
                    "tool": "FILE_OP",
                    "action": "ensure_dirs",
                    "requested_paths": directory_targets,
                    "current_state_only": True,
            }
        return None

    def _build_text_state_synthetic_result(self, ordered_paths: list[str], intent: str) -> dict[str, Any] | None:
        if not FileStagePolicy.stage_is_content_edit_stage(self.stage):
            return None
        if not ordered_paths:
            return None
        target_path = ordered_paths[0]
        text_targets = self._content_text_targets()
        if not text_targets:
            return None
        if re.search(r"\b(remove|delete)\b", intent):
            return {
                "tool": "FILE_OP",
                "action": "verify_text_state",
                "requested_path": target_path,
                "path": target_path,
                "expected_absent_texts": text_targets,
                "current_state_only": True,
            }
        if re.search(r"\breplace\b", intent) and len(text_targets) >= 2:
            return {
                "tool": "FILE_OP",
                "action": "verify_text_state",
                "requested_path": target_path,
                "path": target_path,
                "expected_absent_texts": [text_targets[0]],
                "expected_present_texts": [text_targets[1]],
                "current_state_only": True,
            }
        return None

    def _evaluate_current_code_edit_state(self) -> FileCheckDecision | None:
        if not FileStagePolicy.stage_is_content_edit_stage(self.stage):
            return None
        ordered_paths = self._ordered_file_targets()
        if len(ordered_paths) != 1 or not FileStagePolicy.is_code_path(ordered_paths[0]):
            return None
        requested_path = ordered_paths[0]
        path = self._resolved(requested_path)
        if not path.is_file():
            return {
                "verdict": "FAILED",
                "reason": f"Expected code file is missing at {requested_path}.",
                "evidence_files": [],
            }

        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None

        satisfied_checks: list[str] = []
        remaining_issues: list[str] = []
        replacement_pair = self._diagnosed_replacement_pair()
        if replacement_pair is not None:
            old, new = replacement_pair
            text_l = text.lower()
            if self._contains_expected_text(text_l, old):
                remaining_issues.append(f"'{old}' is still present")
            elif not self._contains_expected_text(text_l, new):
                remaining_issues.append(f"'{new}' is still missing")
            else:
                satisfied_checks.append(f"replaced '{old}' with '{new}'")

        if self._stage_mentions_directional_controls():
            left_ok = self._has_directional_control_logic(text, "left")
            right_ok = self._has_directional_control_logic(text, "right")
            if left_ok and right_ok:
                satisfied_checks.append("left/right control handlers are present")
            else:
                remaining_issues.append("left/right control handling is still incomplete")

        if not satisfied_checks:
            return None
        if remaining_issues:
            return {
                "verdict": "PARTIAL",
                "reason": "Current code state made progress but still has unresolved issues: " + "; ".join(remaining_issues) + ".",
                "evidence_files": [requested_path],
            }
        return {
            "verdict": "VERIFIED",
            "reason": "Current code state satisfies the diagnosed repair requirements: " + ", ".join(satisfied_checks) + ".",
            "evidence_files": [requested_path],
        }

    def _ordered_file_targets(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for candidate in self.preferred_paths:
            if candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
        for match in re.finditer(r"[\w./\\-]+\.[A-Za-z0-9]{1,8}", self.stage_raw_text):
            candidate = match.group(0).replace("\\", "/").strip().rstrip(".,;:!?")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
        return ordered

    @staticmethod
    def _quoted_values_from_text(text: str) -> list[str]:
        values: list[str] = []
        for single, double in re.findall(r"'([^']*)'|\"([^\"]*)\"", str(text or "")):
            candidate = single or double
            if candidate:
                values.append(candidate.strip())
        return values

    def _quoted_values(self) -> list[str]:
        values: list[str] = []
        for text in (self.stage_goal_text, self.stage_success_text, self.stage_context_text):
            values.extend(self._quoted_values_from_text(text))
        return values

    def _goal_success_quoted_values(self) -> list[str]:
        values: list[str] = []
        for text in (self.stage_goal_text, self.stage_success_text):
            values.extend(self._quoted_values_from_text(text))
        return values

    @staticmethod
    def _is_non_content_literal(value: str) -> bool:
        candidate = str(value or "").strip()
        return candidate in {".", ".."}

    @staticmethod
    def _stage_implies_copy_action(intent: str) -> bool:
        text = str(intent or "").strip().lower()
        if not text:
            return False
        if re.search(r"\bcop(?:y|ies|ied|ying)\b", text):
            return True
        return bool(re.search(r"\bduplicat(?:e|es|ed|ing)\b(?:\s+\w+){0,5}\b(?:to|as|into)\b", text))

    def _content_text_targets(self) -> list[str]:
        file_targets = set(self._ordered_file_targets())
        targets: list[str] = []
        seen: set[str] = set()
        for value in self._goal_success_quoted_values():
            candidate = value.strip()
            normalized = candidate.replace("\\", "/").strip()
            if not candidate:
                continue
            if self._is_non_content_literal(candidate):
                continue
            if normalized in file_targets or self._looks_like_file_path(normalized):
                continue
            if any(token in normalized for token in ("/", "\\")):
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            targets.append(candidate)
        return targets

    def _diagnosed_replacement_pair(self) -> tuple[str, str] | None:
        patterns = (
            r"['\"]([^'\"]+)['\"]\s+instead of\s+['\"]([^'\"]+)['\"]",
            r"['\"]([^'\"]+)['\"]\s+to\s+['\"]([^'\"]+)['\"]",
        )
        for pattern in patterns:
            match = re.search(pattern, self.stage_raw_text, re.IGNORECASE)
            if not match:
                continue
            first = str(match.group(1) or "").strip()
            second = str(match.group(2) or "").strip()
            if not first or not second or first == second:
                continue
            if "instead of" in match.group(0).lower():
                return first, second
            return first, second
        pair = FileStagePolicy.stage_replacement_pair(self.stage)
        if pair is not None:
            return pair
        return None

    def _stage_mentions_directional_controls(self) -> bool:
        text = self.stage_raw_text.lower()
        return bool(
            "left" in text
            and "right" in text
            and re.search(r"\b(control|controls|movement|keyboard|input|handler|handlers|move|moving)\b", text)
        )

    @staticmethod
    def _has_directional_control_logic(text: str, direction: str) -> bool:
        text_l = str(text or "").lower()
        direction_l = str(direction or "").lower().strip()
        if not direction_l:
            return False
        if f"pygame.k_{direction_l}" in text_l:
            direction_windows = LocalFileOpRuleChecker._direction_logic_windows(text_l, direction_l)
        else:
            direction_windows = LocalFileOpRuleChecker._direction_logic_windows(text_l, direction_l)
        if not direction_windows:
            return False

        negative_motion = re.compile(
            r"(?:return|=)\s*-\s*[a-z_][a-z0-9_.]*",
            re.IGNORECASE,
        )
        positive_motion = re.compile(
            r"(?:return|=)\s*(?:\+?\s*)[a-z_][a-z0-9_.]*",
            re.IGNORECASE,
        )
        if direction_l == "left":
            return any(negative_motion.search(window) for window in direction_windows)
        if direction_l == "right":
            return any(positive_motion.search(window) and not negative_motion.search(window) for window in direction_windows)
        return any(direction_windows)

    @staticmethod
    def _direction_logic_windows(text_l: str, direction_l: str) -> list[str]:
        lines = text_l.splitlines()
        windows: list[str] = []
        line_re = re.compile(
            rf"\b(?:if|elif|case)\b[^\n]{{0,160}}(?:pygame\.k_{direction_l}|['\"]{direction_l}['\"])",
            re.IGNORECASE,
        )
        for idx, line in enumerate(lines):
            if not line_re.search(line):
                continue
            start_indent = len(line) - len(line.lstrip())
            block_lines = [line]
            for follow in lines[idx + 1 : idx + 5]:
                stripped = follow.lstrip()
                if stripped and (len(follow) - len(stripped)) <= start_indent and not stripped.startswith("#"):
                    break
                block_lines.append(follow)
            windows.append("\n".join(block_lines))
        return windows

    @staticmethod
    def _looks_like_file_path(value: str) -> bool:
        candidate = str(value or "").strip().replace("\\", "/")
        return bool(re.fullmatch(r"[\w./-]+\.[A-Za-z0-9]{1,8}", candidate))

    @staticmethod
    def _contains_expected_text(text_l: str, needle: str) -> bool:
        candidate = str(needle or "").strip().lower()
        if not candidate:
            return False
        if re.fullmatch(r"[a-z_][a-z0-9_]*", candidate):
            pattern = rf"(?<![a-z0-9_]){re.escape(candidate)}(?![a-z0-9_])"
            return bool(re.search(pattern, text_l, re.IGNORECASE))
        return candidate in text_l

    def _directory_targets(self) -> list[str]:
        targets: list[str] = []
        seen: set[str] = set()
        for value in self._quoted_values():
            candidate = value.replace("\\", "/").strip().rstrip(".,;:!?")
            if not candidate or " " in candidate or self._looks_like_file_path(candidate):
                continue
            if candidate not in seen:
                seen.add(candidate)
                targets.append(candidate)
        return targets

    def _extract_expected_text_content(self) -> str | None:
        file_targets = set(self._ordered_file_targets())
        for value in reversed(self._goal_success_quoted_values()):
            candidate = value.strip()
            if not candidate:
                continue
            if self._is_non_content_literal(candidate):
                continue
            normalized = candidate.replace("\\", "/").strip()
            if normalized in file_targets or self._looks_like_file_path(normalized):
                continue
            if any(token in normalized for token in ("/", "\\")):
                continue
            return candidate
        return None

    def _check_extension_inventory(self, tool_result: dict[str, Any]) -> FileCheckDecision:
        destinations = tool_result.get("destination_hints") or {}
        extension_counts = tool_result.get("extension_counts") or {}
        if isinstance(destinations, dict) and destinations and isinstance(extension_counts, dict) and extension_counts:
            evidence = [str(path) for path in list(destinations.values())[:6]]
            return {
                "verdict": "VERIFIED",
                "reason": f"Extension inventory identified {len(extension_counts)} extension groups and destination folders.",
                "evidence_files": evidence,
            }
        return {
            "verdict": "FAILED",
            "reason": "Extension inventory did not include destination hints and extension counts.",
            "evidence_files": [],
        }

    def _check_ensure_dir(self, requested_path: str) -> FileCheckDecision | None:
        if not requested_path:
            return None
        path = self._resolved(requested_path)
        if path.is_dir():
            if self._directories_only_are_partial():
                return {
                    "verdict": "PARTIAL",
                    "reason": f"Directory exists at {requested_path}, but the stage still requires additional file changes.",
                    "evidence_files": [requested_path],
                }
            return {
                "verdict": "VERIFIED",
                "reason": f"Directory exists at {requested_path}.",
                "evidence_files": [requested_path],
            }
        return {
            "verdict": "FAILED",
            "reason": f"Directory does not exist at {requested_path}.",
            "evidence_files": [],
        }

    def _check_ensure_dirs(self, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        requested_paths = [str(item) for item in (tool_result.get("requested_paths") or []) if str(item).strip()]
        if not requested_paths:
            return None
        missing = [rel_path for rel_path in requested_paths if not self._resolved(rel_path).is_dir()]
        if not missing:
            if self._directories_only_are_partial():
                return {
                    "verdict": "PARTIAL",
                    "reason": f"Requested directories exist, but the stage still requires moving or updating files.",
                    "evidence_files": requested_paths[:6],
                }
            return {
                "verdict": "VERIFIED",
                "reason": f"All requested directories exist: {', '.join(requested_paths)}.",
                "evidence_files": requested_paths[:6],
            }
        return {
            "verdict": "FAILED",
            "reason": f"Some requested directories are missing: {', '.join(missing)}.",
            "evidence_files": [path for path in requested_paths if path not in missing][:6],
        }

    def _check_write_text(self, requested_path: str, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        if not requested_path:
            return None
        path = self._resolved(requested_path)
        if not path.is_file():
            return {
                "verdict": "FAILED",
                "reason": f"Expected text file is missing at {requested_path}.",
                "evidence_files": [],
            }
        actual_sha1 = hashlib.sha1(path.read_text(encoding="utf-8").encode("utf-8", errors="replace")).hexdigest()
        expected_sha1 = str(tool_result.get("requested_content_sha1", "")).strip()
        if expected_sha1 and actual_sha1 == expected_sha1:
            return {
                "verdict": "VERIFIED",
                "reason": f"Text file at {requested_path} matches the requested content exactly.",
                "evidence_files": [requested_path],
            }
        return {
            "verdict": "FAILED",
            "reason": f"Text file at {requested_path} does not match the requested content.",
            "evidence_files": [requested_path],
        }

    def _check_text_state(self, requested_path: str, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        if not requested_path:
            return None
        path = self._resolved(requested_path)
        if not path.is_file():
            return {
                "verdict": "FAILED",
                "reason": f"Expected text file is missing at {requested_path}.",
                "evidence_files": [],
            }
        text = path.read_text(encoding="utf-8")
        text_l = text.lower()
        expected_absent = [str(item).strip() for item in (tool_result.get("expected_absent_texts") or []) if str(item).strip()]
        expected_present = [str(item).strip() for item in (tool_result.get("expected_present_texts") or []) if str(item).strip()]
        still_present = [item for item in expected_absent if self._contains_expected_text(text_l, item)]
        still_missing = [item for item in expected_present if not self._contains_expected_text(text_l, item)]
        if not still_present and not still_missing:
            current_state_only = bool(tool_result.get("current_state_only"))
            if current_state_only and expected_absent and not expected_present:
                reason = "Requested text is already absent, so the success condition is satisfied."
            elif current_state_only and expected_present:
                reason = "Requested text state is already satisfied in the current file."
            else:
                reason = f"Text state at {requested_path} matches the requested content constraints."
            return {
                "verdict": "VERIFIED",
                "reason": reason,
                "evidence_files": [requested_path],
            }
        problems: list[str] = []
        if still_present:
            problems.append("still present: " + ", ".join(still_present))
        if still_missing:
            problems.append("missing required text: " + ", ".join(still_missing))
        return {
            "verdict": "FAILED",
            "reason": f"Text state at {requested_path} does not satisfy the stage ({'; '.join(problems)}).",
            "evidence_files": [requested_path],
        }

    def _check_write_json(self, requested_path: str, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        if not requested_path:
            return None
        path = self._resolved(requested_path)
        if not path.is_file():
            return {
                "verdict": "FAILED",
                "reason": f"Expected JSON file is missing at {requested_path}.",
                "evidence_files": [],
            }
        try:
            actual_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {
                "verdict": "FAILED",
                "reason": f"JSON file at {requested_path} is invalid: {exc.msg}",
                "evidence_files": [requested_path],
            }
        if actual_data == tool_result.get("requested_data"):
            return {
                "verdict": "VERIFIED",
                "reason": f"JSON file at {requested_path} matches the requested object exactly.",
                "evidence_files": [requested_path],
            }
        return {
            "verdict": "FAILED",
            "reason": f"JSON file at {requested_path} does not match the requested object.",
            "evidence_files": [requested_path],
        }

    def _check_update_json(self, requested_path: str, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        if not requested_path:
            return None
        path = self._resolved(requested_path)
        if not path.is_file():
            return {
                "verdict": "FAILED",
                "reason": f"Expected JSON file is missing at {requested_path}.",
                "evidence_files": [],
            }
        try:
            actual_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {
                "verdict": "FAILED",
                "reason": f"JSON file at {requested_path} is invalid: {exc.msg}",
                "evidence_files": [requested_path],
            }
        expected_updates = tool_result.get("requested_updates") or {}
        if not isinstance(actual_data, dict) or not isinstance(expected_updates, dict):
            return None
        if all(actual_data.get(key) == value for key, value in expected_updates.items()):
            return {
                "verdict": "VERIFIED",
                "reason": f"JSON file at {requested_path} contains all requested updates.",
                "evidence_files": [requested_path],
            }
        return {
            "verdict": "FAILED",
            "reason": f"JSON file at {requested_path} does not contain all requested updates.",
            "evidence_files": [requested_path],
        }

    def _exclusion_patterns_from_stage(self) -> list[str]:
        """Extract lowercase keyword tokens from exclusion clauses in stage text."""
        patterns: list[str] = []
        for match in _EXCLUSION_CLAUSE_RE.finditer(self.stage_raw_text):
            token = match.group(1).strip().lower()
            # Keep only meaningful tokens (skip generic words like "file", "the", "all")
            if len(token) >= 3 and token not in {"file", "the", "all", "any", "this", "that"}:
                patterns.append(token)
        return patterns

    def _check_consolidate_by_extension(self, tool_result: dict[str, Any]) -> FileCheckDecision:
        root_rel = str(tool_result.get("requested_root") or ".").strip() or "."
        root_path = self._resolved(root_rel)
        destinations = tool_result.get("destinations") or {}
        if not isinstance(destinations, dict) or not destinations:
            return {
                "verdict": "FAILED",
                "reason": "consolidate_by_extension did not report destination folders.",
                "evidence_files": [],
            }

        # Check for exclusion violations: if the stage context mentions files that should
        # be excluded ("except the FCOM", "leave out X", etc.) but the tool result shows
        # those files were moved anyway, flag as FAILED before checking workspace state.
        exclusion_patterns = self._exclusion_patterns_from_stage()
        if exclusion_patterns:
            created = [str(p) for p in (tool_result.get("created_files") or [])]
            for pattern in exclusion_patterns:
                for created_path in created:
                    if pattern in Path(created_path).name.lower():
                        return {
                            "verdict": "FAILED",
                            "reason": (
                                f"File matching exclusion constraint '{pattern}' was moved: {created_path}. "
                                "Re-run consolidate_by_extension with an 'exclude_files' list "
                                "and move the excluded file back to its original location first."
                            ),
                            "evidence_files": [created_path],
                        }

        # Read exclusion info emitted by the tool so intentionally-skipped files
        # are not counted as off-target during verification.
        excluded_names: set[str] = {
            str(n).strip().lower()
            for n in (tool_result.get("excluded_names") or [])
            if str(n).strip()
        }
        excluded_prefixes: list[str] = [
            str(p).strip().lower()
            for p in (tool_result.get("excluded_prefixes") or [])
            if str(p).strip()
        ]
        # Fallback: also derive exclusion tokens from the stage text itself.
        # This covers the case where the tool_result's excluded_names/excluded_prefixes
        # fields are absent (e.g. synthetic STATE_CHECK results) but the stage context
        # explicitly names files or prefixes that should be left in place.
        stage_excl_patterns = self._exclusion_patterns_from_stage()

        off_target: list[str] = []
        evidence: list[str] = []
        for raw_ext, raw_dest in destinations.items():
            ext = str(raw_ext or "").strip().lower()
            dest_rel = str(raw_dest or "").strip()
            if not ext or not dest_rel:
                continue
            dest_path = self._resolved(dest_rel)
            if not dest_path.is_dir():
                off_target.append(f"missing destination {dest_rel}")
                continue
            evidence.append(dest_rel)
            for path in root_path.rglob("*"):
                if not path.is_file():
                    continue
                path_ext = path.suffix.lower() or "[no_ext]"
                if path_ext != ext:
                    continue
                if self._is_within(path, dest_path):
                    continue
                name_lower = path.name.lower()
                if name_lower in excluded_names:
                    continue
                if any(name_lower.startswith(p) for p in excluded_prefixes):
                    continue
                # Stage-text fallback: skip if any exclusion keyword from the stage
                # is a prefix of or matches this filename (e.g. "keep" from "except keep*").
                if any(name_lower.startswith(p) or name_lower == p for p in stage_excl_patterns):
                    continue
                off_target.append(self._rel_to_workspace(path))
        if off_target:
            return {
                "verdict": "FAILED",
                "reason": f"Some files are still outside their destination folders: {', '.join(off_target[:8])}.",
                "evidence_files": evidence[:6],
            }
        return {
            "verdict": "VERIFIED",
            "reason": "All tracked extensions are consolidated into their chosen destination folders.",
            "evidence_files": evidence[:6],
        }

    def _check_delete_empty_dirs(self, tool_result: dict[str, Any]) -> FileCheckDecision:
        root_rel = str(tool_result.get("requested_root") or ".").strip() or "."
        root_path = self._resolved(root_rel)
        remaining_empty = [
            self._rel_to_workspace(path)
            for path in root_path.rglob("*")
            if path.is_dir() and not any(path.iterdir())
        ]
        if remaining_empty:
            return {
                "verdict": "FAILED",
                "reason": f"Empty directories remain: {', '.join(remaining_empty[:8])}.",
                "evidence_files": [str(item) for item in (tool_result.get('requested_paths') or [])[:6]],
            }
        return {
            "verdict": "VERIFIED",
            "reason": "No empty directories remain under the requested root.",
            "evidence_files": [str(item) for item in (tool_result.get('requested_paths') or [])[:6]],
        }

    def _check_moves(self, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        requested_moves = tool_result.get("requested_moves") or []
        if not isinstance(requested_moves, list) or not requested_moves:
            return None
        failed: list[str] = []
        evidence: list[str] = []
        for item in requested_moves:
            if not isinstance(item, dict):
                continue
            src = str(item.get("src") or "").strip()
            dst = str(item.get("dst") or "").strip()
            if not src or not dst:
                continue
            src_exists = self._resolved(src).exists()
            dst_exists = self._resolved(dst).exists()
            if src_exists or not dst_exists:
                failed.append(f"{src}->{dst}")
            else:
                evidence.append(dst)
        if not failed:
            moved_count = len(evidence)
            expected_count = self._stage_expected_item_count()
            scope_count = self._stage_scope_file_count() if self._stage_requires_broad_coverage() else 0
            target_count = max(expected_count, scope_count)
            if target_count and moved_count < target_count:
                return {
                    "verdict": "PARTIAL",
                    "reason": f"Verified {moved_count} moved paths, but the stage scope implies {target_count} file(s) still need coverage.",
                    "evidence_files": evidence[:6],
                }
            return {
                "verdict": "VERIFIED",
                "reason": f"All requested moves completed: {', '.join(evidence)}.",
                "evidence_files": evidence[:6],
            }
        return {
            "verdict": "FAILED",
            "reason": f"Some requested moves were not verified: {', '.join(failed)}.",
            "evidence_files": evidence[:6],
        }

    def _check_copies(self, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        requested_copies = tool_result.get("requested_copies") or []
        if not isinstance(requested_copies, list) or not requested_copies:
            return None
        missing: list[str] = []
        mismatched: list[str] = []
        evidence: list[str] = []
        for item in requested_copies:
            if not isinstance(item, dict):
                continue
            src = str(item.get("src") or "").strip()
            dst = str(item.get("dst") or "").strip()
            if not dst:
                continue
            src_path = self._resolved(src) if src else None
            dst_path = self._resolved(dst)
            if src and (src_path is None or not src_path.exists()):
                missing.append(src)
                continue
            if not dst_path.exists():
                missing.append(dst)
                continue
            if src:
                assert src_path is not None
                if src_path.is_file() != dst_path.is_file():
                    mismatched.append(f"{src}->{dst}")
                    continue
                if src_path.is_file() and dst_path.is_file():
                    src_sha1 = hashlib.sha1(src_path.read_bytes()).hexdigest()
                    dst_sha1 = hashlib.sha1(dst_path.read_bytes()).hexdigest()
                    if src_sha1 != dst_sha1:
                        mismatched.append(dst)
                        continue
            evidence.append(dst)
        if not missing and not mismatched:
            copied_count = len(evidence)
            expected_count = self._stage_expected_item_count()
            scope_count = self._stage_scope_file_count() if self._stage_requires_broad_coverage() else 0
            target_count = max(expected_count, scope_count)
            if target_count and copied_count < target_count:
                return {
                    "verdict": "PARTIAL",
                    "reason": f"Verified {copied_count} copied paths, but the stage scope implies {target_count} file(s) still need coverage.",
                    "evidence_files": evidence[:6],
                }
            return {
                "verdict": "VERIFIED",
                "reason": "All requested copies exist at their destination paths and match the source content when applicable.",
                "evidence_files": evidence[:6],
            }
        if missing:
            return {
                "verdict": "FAILED",
                "reason": f"Some requested copy paths are missing or the source no longer exists: {', '.join(missing)}.",
                "evidence_files": evidence[:6],
            }
        return {
            "verdict": "FAILED",
            "reason": f"Some requested copies do not match the source content: {', '.join(mismatched)}.",
            "evidence_files": evidence[:6],
        }

    def _check_deletes(self, tool_result: dict[str, Any]) -> FileCheckDecision | None:
        requested_paths = [str(item) for item in (tool_result.get("requested_paths") or []) if str(item).strip()]
        if not requested_paths:
            return None
        still_present = [rel_path for rel_path in requested_paths if self._resolved(rel_path).exists()]
        if not still_present:
            current_state_only = bool(tool_result.get("current_state_only"))
            return {
                "verdict": "VERIFIED",
                "reason": (
                    "Requested paths are already absent, so the success condition is satisfied."
                    if current_state_only
                    else "Requested paths were deleted successfully."
                ),
                "evidence_files": requested_paths[:6],
            }
        return {
            "verdict": "FAILED",
            "reason": f"Some requested deletions did not occur: {', '.join(still_present)}.",
            "evidence_files": [path for path in requested_paths if path not in still_present][:6],
        }
