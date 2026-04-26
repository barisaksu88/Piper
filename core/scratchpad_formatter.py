from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

from core.engines.state_mutation import StateMutationEngine
from core.engines.summary import SummaryEngine
from core.file_stage_policy import FileStagePolicy


_STATE_MUTATION_ENGINE = StateMutationEngine()


class ScratchpadFormatter:
    """Handles the structured formatting of orchestrator history for the LLM."""

    _DEFAULT_OBSERVATION_LIMIT = 500
    _FILE_READ_OBSERVATION_LIMIT = 1800
    _FILE_MULTI_READ_OBSERVATION_LIMIT = 1400
    _FILE_MUTATION_OBSERVATION_LIMIT = 2200
    _BROWSER_OBSERVATION_LIMIT = 1200

    @staticmethod
    def _stage_timeout_hit(stage_entries: Iterable[str] | None) -> bool:
        return any("=== STAGE TIMEOUT ===" in str(entry or "") for entry in (stage_entries or []))

    @staticmethod
    def _stage_action_budget_hit(stage_entries: Iterable[str] | None) -> bool:
        return any("=== ACTION BUDGET EXHAUSTED ===" in str(entry or "") for entry in (stage_entries or []))

    @staticmethod
    def _status_override_counts_as_success(status_override: str) -> bool:
        return str(status_override or "").strip().upper().startswith("PAUSED / AWAITING USER")

    @staticmethod
    def format_stage_header(stage_num: int, stage: Dict) -> str:
        goal = stage.get("stage_goal", "Unknown")
        stype = stage.get("stage_type", "Unknown")
        condition = stage.get("success_condition", "Unknown")

        return f"""=== STAGE {stage_num} START ===
STAGE_GOAL: {goal}
STAGE_TYPE: {stype}
SUCCESS_CONDITION: {condition}"""

    @staticmethod
    def format_step(step_num: int, thought: str, action: str, observation: Any) -> str:
        observation_text = ScratchpadFormatter._stringify_observation(observation)

        lower_res = observation_text.lower()
        if "error" in lower_res or "security violation" in lower_res or "failed" in lower_res:
            obs_kind = "error"
        elif "done" in lower_res or "success" in lower_res or "saved" in lower_res:
            obs_kind = "success"
        else:
            obs_kind = "info"

        limit = ScratchpadFormatter._observation_limit(observation)
        obs_text = observation_text[:limit] + ("..." if len(observation_text) > limit else "")
        action_clean = action.replace("\n", " ").strip()

        return f"""STEP {step_num}
THOUGHT: {thought}
ACTION: {action_clean}
OBSERVATION_KIND: {obs_kind}
OBSERVATION_TEXT: {obs_text}"""

    @staticmethod
    def _stringify_observation(observation: Any) -> str:
        if isinstance(observation, dict):
            tool = str(observation.get("tool", "")).upper()
            if tool in {"FILE_OP", "RUN_CODE"}:
                safe: Dict[str, Any] = {}
                action = str(observation.get("action", "")).lower()
                for key in (
                    "tool",
                    "status",
                    "summary",
                    "action",
                    "requested_root",
                    "requested_query",
                    "requested_mode",
                    "requested_path",
                    "path",
                    "entry_count",
                    "match_count",
                    "moved_count",
                    "deduplicated_count",
                    "deleted_dir_count",
                    "extension_counts",
                    "top_level_dir_file_counts",
                    "destination_hints",
                    "destinations",
                    "folder_extension_counts",
                    "requested_content_sha1",
                    "requested_append_sha1",
                ):
                    if key in observation:
                        safe[key] = observation.get(key)

                for key, limit in (
                    ("top_level_dirs", 8),
                    ("top_level_files", 12),
                    ("requested_paths", 12),
                    ("requested_extensions", 12),
                    ("missing_files", 12),
                    ("matches", 12),
                    ("collisions", 12),
                    ("deduplicated_files", 12),
                    ("excluded_names", 12),
                    ("excluded_prefixes", 12),
                    ("empty_dirs", 12),
                    ("created_files", 12),
                    ("updated_files", 12),
                    ("deleted_files", 12),
                    ("created_dirs", 12),
                    ("deleted_dirs", 12),
                    ("evidence_files", 12),
                ):
                    values = observation.get(key)
                    if isinstance(values, list) and values:
                        safe[key] = values[:limit]

                for key, limit in (("requested_moves", 8), ("requested_copies", 8)):
                    values = observation.get(key)
                    if isinstance(values, list) and values:
                        safe[key] = values[:limit]

                files = observation.get("files")
                if isinstance(files, dict) and files:
                    safe["files"] = list(files.keys())[:8]
                    if action == "read_text":
                        path, content = next(iter(files.items()))
                        safe["file_contents"] = {
                            str(path): SummaryEngine.truncate_text(str(content), 1200)
                        }
                    elif action == "read_many":
                        safe["file_contents"] = {
                            str(path): SummaryEngine.truncate_text(str(content), 260)
                            for path, content in list(files.items())[:4]
                        }

                file_snippets = observation.get("file_snippets")
                if isinstance(file_snippets, dict) and file_snippets:
                    snippet_payload: Dict[str, Any] = {}
                    for path, snippet in list(file_snippets.items())[:4]:
                        if not isinstance(snippet, dict):
                            snippet_payload[str(path)] = str(snippet)
                            continue
                        item: Dict[str, Any] = {
                            "status": str(snippet.get("status", "")).strip(),
                        }
                        if "truncated" in snippet:
                            item["truncated"] = bool(snippet.get("truncated"))
                        if "full_char_count" in snippet:
                            item["full_char_count"] = int(snippet.get("full_char_count") or 0)
                        if "content" in snippet:
                            item["content"] = SummaryEngine.truncate_text(str(snippet.get("content") or ""), 900)
                        if "size_bytes" in snippet:
                            item["size_bytes"] = int(snippet.get("size_bytes") or 0)
                        snippet_payload[str(path)] = item
                    safe["file_snippets"] = snippet_payload

                try:
                    return json.dumps(safe, ensure_ascii=False)
                except Exception:
                    pass
            if tool == "BROWSER_OP":
                safe: Dict[str, Any] = {}
                action = str(observation.get("action") or "").strip().lower()
                for key in (
                    "tool",
                    "status",
                    "summary",
                    "action",
                    "backend",
                    "current_url",
                    "title",
                    "selector",
                    "selector_strategy",
                    "extracted_text",
                    "field_value",
                    "saved_path",
                ):
                    if key in observation:
                        safe[key] = observation.get(key)
                inventory = observation.get("element_inventory")
                if isinstance(inventory, list) and inventory:
                    compact_inventory = []
                    for item in inventory[:12]:
                        if not isinstance(item, dict):
                            continue
                        compact_item: Dict[str, Any] = {}
                        for field_name in ("selector", "tag", "type"):
                            if field_name in item:
                                compact_item[field_name] = item.get(field_name)
                        for field_name in ("id", "data_testid", "name", "href", "text"):
                            if field_name not in item:
                                continue
                            value = SummaryEngine.truncate_text(str(item.get(field_name) or "").strip(), 72)
                            if value:
                                compact_item[field_name] = value
                        if compact_item:
                            compact_inventory.append(compact_item)
                    if compact_inventory:
                        safe["element_inventory"] = compact_inventory
                field_values = observation.get("field_values")
                if isinstance(field_values, dict) and field_values:
                    safe["field_values"] = {
                        str(key): value
                        for key, value in list(field_values.items())[:8]
                        if str(key).strip()
                    }
                text_preview = str(observation.get("text_preview") or "").strip()
                if text_preview and action not in {"goto_url", "open_page"}:
                    safe["text_preview"] = SummaryEngine.truncate_text(text_preview, 180)
                verification = observation.get("verification")
                if isinstance(verification, dict) and verification:
                    safe["verification"] = verification
                try:
                    return json.dumps(safe, ensure_ascii=False)
                except Exception:
                    pass
        if isinstance(observation, str):
            return observation
        try:
            return json.dumps(observation, ensure_ascii=False)
        except Exception:
            return str(observation)

    @staticmethod
    def _observation_limit(observation: Any) -> int:
        if isinstance(observation, dict):
            tool = str(observation.get("tool", "")).upper()
            action = str(observation.get("action", "")).lower()
            file_snippets = observation.get("file_snippets")
            if tool in {"FILE_OP", "RUN_CODE"} and isinstance(file_snippets, dict) and file_snippets:
                return ScratchpadFormatter._FILE_MUTATION_OBSERVATION_LIMIT
            if tool == "FILE_OP" and action == "read_text":
                return ScratchpadFormatter._FILE_READ_OBSERVATION_LIMIT
            if tool == "FILE_OP" and action == "read_many":
                return ScratchpadFormatter._FILE_MULTI_READ_OBSERVATION_LIMIT
            if tool == "BROWSER_OP":
                return ScratchpadFormatter._BROWSER_OBSERVATION_LIMIT
        return ScratchpadFormatter._DEFAULT_OBSERVATION_LIMIT

    @staticmethod
    def format_outcome(
        stage_num: int,
        success: bool,
        stage_type: str,
        last_observation: str = "",
        *,
        status_override: str = "",
        stage_entries: Iterable[str] | None = None,
    ) -> str:
        pack = ScratchpadFormatter.build_outcome_pack(
            success=success,
            stage_type=stage_type,
            last_observation=last_observation,
            status_override=status_override,
            stage_entries=stage_entries,
        )
        detail = f"\nLAST_LOG: {pack.detail}" if pack.detail else ""

        return f"""=== STAGE {stage_num} OUTCOME ===
RESULT: {pack.status}{detail}"""

    @staticmethod
    def build_outcome_pack(
        *,
        success: bool,
        stage_type: str,
        last_observation: str = "",
        status_override: str = "",
        stage_entries: Iterable[str] | None = None,
        stage: Dict[str, Any] | None = None,
    ):
        stage_type_upper = str(stage_type or "").upper()
        if stage_type_upper in {"TASK_EVENT_WORK", "MEMORY_WORK"}:
            return _STATE_MUTATION_ENGINE.build_outcome_pack(
                success=success,
                stage_type=stage_type_upper,
                fallback_observation=last_observation,
                status_override=status_override,
                stage_entries=stage_entries,
                stage=stage,
            )

        stage_entries_list = list(stage_entries) if stage_entries is not None else None
        if status_override:
            status = status_override
        elif not success and ScratchpadFormatter._stage_timeout_hit(stage_entries_list):
            status = "TIMEOUT"
        elif not success and ScratchpadFormatter._stage_action_budget_hit(stage_entries_list):
            status = "ACTION BUDGET EXHAUSTED"
        elif success:
            if stage_type == "IMAGE_WORK":
                status = "IMAGE GENERATED"
            elif stage_type == "FILE_WORK":
                status = "FILE OPERATION SUCCESS"
            elif stage_type == "MEMORY_WORK":
                status = "MEMORY UPDATED"
            else:
                status = "SUCCESS"
        else:
            status = "FAILED / INCOMPLETE"

        extracted = SummaryEngine.extract_observation_detail(
            SummaryEngine.select_outcome_detail(stage_type, stage_entries_list, last_observation)
        )
        from core.contracts import StageOutcomePack

        allow_persona_reroute = True
        if stage_type_upper == "FILE_WORK":
            allow_persona_reroute = not ScratchpadFormatter._has_terminal_missing_named_file_target_failure(
                stage=stage,
                stage_entries=stage_entries_list,
            )

        return StageOutcomePack(
            status=status,
            detail=extracted,
            effective_success=bool(success or ScratchpadFormatter._status_override_counts_as_success(status_override)),
            allow_persona_reroute=allow_persona_reroute,
        )

    @staticmethod
    def _has_terminal_missing_named_file_target_failure(
        *,
        stage: Dict[str, Any] | None,
        stage_entries: list[str] | None,
    ) -> bool:
        if not stage or not FileStagePolicy.stage_is_file_work(stage):
            return False
        if FileStagePolicy.stage_allows_absence_confirmation(stage):
            return False
        if ScratchpadFormatter._stage_may_create_missing_target(stage):
            return False

        target_terms = {
            str(term).strip().lower()
            for term in FileStagePolicy.stage_missing_target_terms(stage)
            if str(term).strip()
        }
        if not target_terms:
            return False

        entries = [str(entry or "") for entry in (stage_entries or []) if str(entry or "").strip()]
        if any("FILE_WORK_VERIFIED_RESULT:" in entry for entry in entries):
            return False
        for entry in entries:
            missing_target = ScratchpadFormatter._extract_missing_named_target(entry)
            if missing_target and ScratchpadFormatter._term_matches_target(missing_target, target_terms):
                return True
            missing_query = ScratchpadFormatter._extract_failed_find_query(entry)
            if missing_query and ScratchpadFormatter._term_matches_target(missing_query, target_terms):
                return True
        return False

    @staticmethod
    def _stage_may_create_missing_target(stage: Dict[str, Any]) -> bool:
        return FileStagePolicy.stage_may_create_missing_target(stage)

    @staticmethod
    def _extract_missing_named_target(entry: str) -> str:
        for candidate_text in (
            ScratchpadFormatter._extract_file_op_summary(entry),
            str(entry or ""),
        ):
            candidate = ScratchpadFormatter._extract_missing_named_target_from_text(candidate_text)
            if candidate:
                return candidate
        return ""

    @staticmethod
    def _extract_file_op_summary(entry: str) -> str:
        text = str(entry or "")
        if "OBSERVATION_TEXT:" not in text:
            return ""
        _, _, payload = text.partition("OBSERVATION_TEXT:")
        try:
            data = json.loads(payload.strip())
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        if str(data.get("tool", "")).upper() != "FILE_OP":
            return ""
        return str(data.get("summary") or "").strip()

    @staticmethod
    def _extract_missing_named_target_from_text(text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        match = re.search(
            r"file_op (?:target|source) not found:\s*`?(.+?)`?(?:\",|\n|$)",
            raw,
            re.IGNORECASE,
        )
        if not match:
            return ""
        return str(match.group(1) or "").strip().strip("`'\"").lower()

    @staticmethod
    def _extract_failed_find_query(entry: str) -> str:
        text = str(entry or "")
        if "OBSERVATION_TEXT:" not in text or '"action": "find_paths"' not in text or '"match_count": 0' not in text:
            return ""
        _, _, payload = text.partition("OBSERVATION_TEXT:")
        try:
            data = json.loads(payload.strip())
        except Exception:
            return ""
        if str(data.get("action", "")).lower() != "find_paths":
            return ""
        try:
            if int(data.get("match_count", 0) or 0) != 0:
                return ""
        except (TypeError, ValueError):
            return ""
        return str(data.get("requested_query") or "").strip().strip("`'\"").lower()

    @staticmethod
    def _term_matches_target(term: str, targets: set[str]) -> bool:
        candidate = str(term or "").strip().lower()
        if not candidate:
            return False
        return any(
            candidate == target
            or candidate.endswith(target)
            or target.endswith(candidate)
            for target in targets
            if target
        )

    # _select_outcome_detail, _extract_observation_detail, _is_generic_file_work_summary,
    # _truncate_text → moved to SummaryEngine (core/engines/summary.py)
