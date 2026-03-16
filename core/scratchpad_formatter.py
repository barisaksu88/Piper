from __future__ import annotations

import json
from typing import Any, Dict, Iterable

from core.engines.state_mutation import StateMutationEngine
from core.engines.summary import SummaryEngine


_STATE_MUTATION_ENGINE = StateMutationEngine()


class ScratchpadFormatter:
    """Handles the structured formatting of orchestrator history for the LLM."""

    _DEFAULT_OBSERVATION_LIMIT = 500
    _FILE_READ_OBSERVATION_LIMIT = 1800
    _FILE_MULTI_READ_OBSERVATION_LIMIT = 1400
    _FILE_MUTATION_OBSERVATION_LIMIT = 2200

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
                    ("missing_files", 12),
                    ("matches", 12),
                    ("deduplicated_files", 12),
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
    ):
        stage_type_upper = str(stage_type or "").upper()
        if stage_type_upper in {"TASK_EVENT_WORK", "MEMORY_WORK"}:
            return _STATE_MUTATION_ENGINE.build_outcome_pack(
                success=success,
                stage_type=stage_type_upper,
                fallback_observation=last_observation,
                status_override=status_override,
                stage_entries=stage_entries,
            )

        if status_override:
            status = status_override
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
            SummaryEngine.select_outcome_detail(stage_type, list(stage_entries) if stage_entries is not None else None, last_observation)
        )
        from core.contracts import StageOutcomePack

        return StageOutcomePack(
            status=status,
            detail=extracted,
            effective_success=bool(success or status_override),
        )

    # _select_outcome_detail, _extract_observation_detail, _is_generic_file_work_summary,
    # _truncate_text → moved to SummaryEngine (core/engines/summary.py)
