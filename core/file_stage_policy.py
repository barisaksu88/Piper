from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.contracts import FileStageKind, StageCard
from core.file_extensions import CODE_FILE_EXTENSIONS
from core.file_reference_matcher import file_reference_matches
from tools.file_ops import (
    FileOpError,
    normalized_action_from_payload,
    parse_normalized_tool_tag_payload,
    path_list_from_payload,
    primary_path_from_payload,
    source_paths_from_payload,
)


class FileStagePolicy:
    _FILE_STAGE_KINDS: frozenset[FileStageKind] = frozenset(
        {
            "INSPECTION",
            "CONTENT_EDIT",
            "STRUCTURE_PREP",
            "BROAD_REORG",
            "SCRIPT_LAUNCH",
            "DEPENDENCY_RECOVERY",
            "UNKNOWN",
        }
    )
    _QUOTED_VALUE_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
    _PATHISH_TOKEN_RE = re.compile(r"(?:[a-z0-9_.-]+[/\\])+[a-z0-9_.-]+|[a-z0-9_.-]+\.[a-z0-9]{1,8}", re.IGNORECASE)
    _NEGATED_MUTATION_RE = re.compile(
        r"\b(?:"
        r"without\s+(?:modif(?:y|ying)|edit(?:ing)?|chang(?:e|ing)|rewrit(?:e|ing)|updat(?:e|ing)|touch(?:ing)?|mutat(?:e|ing))"
        r"(?:\s+(?:the\s+)?(?:file|files|source|script|code|artifact))?"
        r"|do\s+not\s+(?:modif(?:y|ying)|edit|change|rewrite|update|touch|mutate)"
        r"(?:\s+(?:the\s+)?(?:file|files|source|script|code|artifact))?"
        r"|don't\s+(?:modif(?:y|ying)|edit|change|rewrite|update|touch|mutate)"
        r"(?:\s+(?:the\s+)?(?:file|files|source|script|code|artifact))?"
        r"|no\s+file\s+edits?"
        r"|read[\s-]?only"
        r"|diagnos(?:e|is)\s+only"
        r"|analysis\s+only"
        r"|inspect\s+only"
        r")\b",
        re.IGNORECASE,
    )
    _MUTATION_ACTION_RE = re.compile(
        r"\b(?:creat\w*|writ\w*|rewrit\w*|updat\w*|modif\w*|edit\w*|append\w*|insert\w*|sav\w*|chang\w*|delet\w*|remov\w*|renam\w*|relocat\w*|cop(?:y|ies|ied|ying)|move(?:s|d|ing)?|add inside)\b"
    )
    _STRONG_MUTATION_ACTION_RE = re.compile(
        r"\b(?:creat\w*|writ\w*|rewrit\w*|modif\w*|edit\w*|append\w*|insert\w*|sav\w*|chang\w*|delet\w*|remov\w*|renam\w*|relocat\w*|cop(?:y|ies|ied|ying)|move(?:s|d|ing)?|add inside)\b"
    )
    _CODE_FILE_EXTENSIONS = CODE_FILE_EXTENSIONS  # canonical source: core/file_extensions.py

    @staticmethod
    def _normalize_lookup_term(text: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
        return " ".join(cleaned.split())

    @classmethod
    def _token_prefix_match(cls, query_norm: str, candidate_norm: str) -> bool:
        q_tokens = query_norm.split()
        c_tokens = candidate_norm.split()
        if not q_tokens or not c_tokens:
            return False
        checked = 0
        for qt in q_tokens:
            if len(qt) < 2:
                continue
            if not any(ct.startswith(qt) or qt.startswith(ct) for ct in c_tokens):
                return False
            checked += 1
        return checked > 0

    @staticmethod
    def stage_is_file_work(stage: StageCard) -> bool:
        return str(stage.get("stage_type", "")).upper() == "FILE_WORK"

    @classmethod
    def stage_kind(cls, stage: StageCard) -> FileStageKind | str:
        if not cls.stage_is_file_work(stage):
            return ""
        kind = str(stage.get("file_stage_kind", "") or "").strip().upper()
        return kind if kind in cls._FILE_STAGE_KINDS else ""

    @staticmethod
    def stage_file_text(stage: StageCard) -> str:
        return " ".join(
            [
                str(stage.get("stage_goal", "")),
                str(stage.get("success_condition", "")),
                " ".join(str(item) for item in (stage.get("context") or [])),
            ]
        ).lower()

    @classmethod
    def stage_goal_success_text(cls, stage: StageCard) -> str:
        return " ".join(
            [
                str(stage.get("stage_goal", "")),
                str(stage.get("success_condition", "")),
            ]
        ).lower()

    @classmethod
    def stage_may_create_missing_target(cls, stage: StageCard) -> bool:
        raw = cls.stage_goal_success_text(stage)
        if not raw:
            return False

        # Strip explicit "do not create" guidance before looking for positive
        # create/build cues. Existing-file edit cards often mention creation
        # only to forbid it when the target is missing.
        sanitized = re.sub(
            r"\b(?:do\s+not|don't|not\s+to|without|instead\s+of|rather\s+than)\s+"
            r"(?:creat\w*|mak\w*|generat\w*|build\w*|ensur\w*)\b[^.;]*",
            " ",
            raw,
            flags=re.IGNORECASE,
        )
        sanitized = re.sub(
            r"\bif\s+[^.;]*\bmissing\b[^.;]*\b(?:stop|report|do\s+not|don't)\b[^.;]*"
            r"\b(?:creat\w*|mak\w*|generat\w*|build\w*)\b[^.;]*",
            " ",
            sanitized,
            flags=re.IGNORECASE,
        )

        if (
            "existing file" in raw
            and re.search(
                r"\b(?:do\s+not|don't|not\s+to)\s+creat\w*\b|\binstead of creat\w*\b",
                raw,
                flags=re.IGNORECASE,
            )
        ):
            return False

        return bool(
            re.search(
                r"\b(?:create|make|generate|build|new file|create or overwrite|create the text file|write the text file|ensure .* exists?)\b",
                sanitized,
                flags=re.IGNORECASE,
            )
        )

    @classmethod
    def stage_intent_text(cls, stage: StageCard) -> str:
        raw = cls.stage_goal_success_text(stage)
        if not raw:
            return ""
        sanitized = cls._QUOTED_VALUE_RE.sub(" ", raw)
        sanitized = cls._PATHISH_TOKEN_RE.sub(" ", sanitized)
        sanitized = cls._NEGATED_MUTATION_RE.sub(" ", sanitized)
        sanitized = sanitized.replace("_", " ")
        return " ".join(sanitized.split())

    @classmethod
    def is_file_inspection_stage(cls, stage: StageCard) -> bool:
        kind = cls.stage_kind(stage)
        if kind and kind != "UNKNOWN":
            return kind == "INSPECTION"
        text = cls.stage_intent_text(stage)
        if not text:
            return False
        explicit_readback = bool(
            re.search(r"\b(read|show|tell|report|display|return)\b", text)
            and re.search(r"\b(contents?|text|full text|exact contents|file contents?)\b", text)
        )
        if explicit_readback:
            contextual_only = re.sub(
                r"\bafter the (?:requested )?(?:removal|change|edit|update|replacement|cleanup)\b|\bafter the [a-z]+ step\b",
                " ",
                text,
            )
            contextual_only = re.sub(r"\bupdated\b", " ", contextual_only)
            if not cls._STRONG_MUTATION_ACTION_RE.search(contextual_only):
                return True
        inspection_re = re.compile(
            r"\b(read|show|tell|report|summarize|display|list|return|inspect|check|search|find|locate|identify|verify|confirm|scan|analy[sz]e|analysis|diagnos\w*|debug|review|audit|root cause|why)\b"
        )
        if cls._MUTATION_ACTION_RE.search(text):
            return False
        return bool(inspection_re.search(text) or "contents" in text)

    @classmethod
    def is_file_planning_stage(cls, stage: StageCard) -> bool:
        kind = cls.stage_kind(stage)
        if kind and kind not in {"INSPECTION", "UNKNOWN"}:
            return False
        text = cls.stage_intent_text(stage)
        if not text:
            return False
        if re.search(r"\b(?:execut\w*|appl\w*)\b", text) or cls._MUTATION_ACTION_RE.search(text):
            return False
        return bool(
            re.search(
                r"\b(propose|proposal|plan|planning|suggest|recommend|design|present|approval|approve|confirmation|confirm)\b",
                text,
            )
        )

    @staticmethod
    def stage_requires_user_approval(stage: StageCard) -> bool:
        text = " ".join(
            [
                str(stage.get("stage_goal", "")),
                str(stage.get("success_condition", "")),
            ]
        ).lower()
        text = FileStagePolicy._QUOTED_VALUE_RE.sub(" ", text)
        text = FileStagePolicy._PATHISH_TOKEN_RE.sub(" ", text)
        text = text.replace("_", " ")
        text = " ".join(text.split())
        if not text:
            return False
        return bool(
            re.search(
                r"\b(user confirmation|user approval|for approval|await approval|ask for approval|ask for confirmation|present .* for approval|present .* for confirmation|confirm before executing|approval before executing)\b",
                text,
            )
        )

    @classmethod
    def stage_requires_file_computation(cls, stage: StageCard) -> bool:
        kind = cls.stage_kind(stage)
        if kind and kind not in {"INSPECTION", "UNKNOWN"}:
            return False
        text = cls.stage_intent_text(stage)
        if not text:
            return False
        return bool(
            re.search(
                r"\b(count|calculate|hash|checksum|duplicate|compare|parse|extract|convert|transform|regex|pattern|analy[sz]e|classify|find largest|find smallest|group by|sort by)\b",
                text,
            )
        )

    @classmethod
    def stage_requires_analysis_report(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind not in {"INSPECTION", "UNKNOWN"}:
            return False
        raw = cls.stage_goal_success_text(stage)
        text = cls.stage_intent_text(stage)
        if not raw or not text:
            return False
        analysis_cues = re.search(
            r"\b(analy[sz]e|analysis|diagnos\w*|identify|explain|debug|review|audit|root cause|issue|issues|bug|bugs|why)\b",
            raw,
        )
        report_cues = re.search(
            r"\b(report|summary|summarize|explanation|explained|reason|reasons|cause|causes|diagnosis)\b",
            raw,
        )
        explicit_content_read = bool(
            re.search(r"\b(show|display|return|tell|quote|open)\b", text)
            and re.search(r"\b(contents?|text|full text|exact contents|file contents?)\b", raw)
        ) or any(
            phrase in raw
            for phrase in (
                "exact contents",
                "show the contents",
                "display the contents",
                "report its contents",
                "full text content",
                "what it says",
            )
        )
        return bool((analysis_cues or report_cues) and not explicit_content_read)

    @classmethod
    def stage_is_script_launch_stage(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind != "UNKNOWN":
            return kind == "SCRIPT_LAUNCH"
        raw = cls.stage_goal_success_text(stage)
        text = cls.stage_intent_text(stage)
        if not raw or not text:
            return False
        script_targets = [path for path in cls.stage_named_file_targets(stage) if Path(path).suffix.lower() == ".py"]
        mentions_script = ".py" in raw or "script" in raw or "game" in raw
        launch_intent = re.search(r"\b(run|execute|launch|start|open|play)\b", text)
        return bool(launch_intent and (script_targets or mentions_script))

    @classmethod
    def stage_is_interactive_runtime_verification(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind not in {"SCRIPT_LAUNCH", "UNKNOWN"}:
            return False
        raw = cls.stage_goal_success_text(stage)
        text = cls.stage_intent_text(stage)
        if not raw or not text:
            return False
        verification_cue = re.search(r"\b(verify|confirm|check|observe|test|try|report)\b", text)
        interaction_cue = re.search(
            r"\b(controls?|input|movement|left|right|up|down|keyboard|mouse|responsive|respond|press|click|catch|gameplay|playable|works?)\b",
            raw,
        )
        app_cue = re.search(r"\b(game|application|app|script|player)\b", raw)
        mutation_cue = re.search(r"\b(write|rewrite|overwrite|update|modify|edit|append|insert|replace|change|remove|save)\b", text)
        return bool(verification_cue and interaction_cue and app_cue and not mutation_cue)

    @classmethod
    def stage_mentions_directional_controls(cls, stage: StageCard) -> bool:
        raw = cls.stage_goal_success_text(stage)
        if not raw:
            return False
        return bool(
            "left" in raw
            and "right" in raw
            and re.search(r"\b(control|controls|movement|keyboard|input|handler|handlers|move|moving)\b", raw)
        )

    @classmethod
    def stage_is_non_mutating_file_stage(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        if cls.stage_requires_user_approval(stage):
            return True
        kind = cls.stage_kind(stage)
        if kind and kind != "UNKNOWN":
            return kind == "INSPECTION"
        return not cls.stage_is_script_launch_stage(stage) and (
            cls.is_file_inspection_stage(stage)
            or cls.is_file_planning_stage(stage)
        )

    @classmethod
    def stage_is_structure_prep_stage(cls, stage: StageCard) -> bool:
        kind = cls.stage_kind(stage)
        if kind and kind not in {"STRUCTURE_PREP", "UNKNOWN"}:
            return False
        text = cls.stage_intent_text(stage)
        if not text:
            return False
        if re.search(r"\b(mov\w*|relocat\w*|renam\w*|cop\w*|delet\w*|remov\w*|execut\w*|appl\w*|reorgani[sz]\w*)\b", text):
            return False
        return bool(
            re.search(r"\b(folder structure|directory structure|directory layout|folder layout|create the folders|create the directories|establish .* structure|prepare .* directories)\b", text)
        )

    @classmethod
    def stage_is_content_edit_stage(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind != "UNKNOWN":
            return kind == "CONTENT_EDIT"
        intent = cls.stage_intent_text(stage)
        raw = cls.stage_goal_success_text(stage)
        if not intent or not raw:
            return False
        mutation_cues = re.search(
            r"\b(write|rewrite|overwrite|update|modify|edit|append|insert|replace|change|remove|trim|clean up|save|correct|fix|repair)\b",
            intent,
        )
        content_cues = re.search(
            r"\b(text|content|contents|line|lines|word|words|item|items|entry|entries|json|field|fields|key|keys|value|values|paragraph|sentence|sentences|code|source|script|program|function|functions|method|methods|class|classes|logic|handler|handlers|controls?|mechanic|mechanics|input|collision|event loop|event handling)\b",
            raw,
        )
        path_ops = re.search(r"\b(move|rename|relocate|copy|duplicate|directory|directories|folder|folders)\b", intent)
        return bool(mutation_cues and content_cues and not path_ops)

    @classmethod
    def stage_is_broad_file_reorg(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind != "UNKNOWN":
            return kind == "BROAD_REORG"
        text = cls.stage_intent_text(stage)
        if not text:
            return False
        broad_scope = re.search(
            r"\b(all files|all entries|all items|entire workspace|whole workspace|fully sorted|workspace is tidy|everything|remaining entries|all identified files|correct directories|proper directories|designated directories)\b",
            text,
        )
        reorg_intent = re.search(
            r"\b(reorgani[sz]\w*|organi[sz]\w*|sort|categori[sz]\w*|tidy|clean up|move\b.{0,40}\bto\b|move\b.{0,40}\binto\b|relocat\w*)\b",
            text,
        )
        file_scope = re.search(r"\b(workspace|directory|directories|folders|files)\b", text)
        return bool((broad_scope or file_scope) and reorg_intent)

    @classmethod
    def stage_is_extension_file_reorg(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind not in {"STRUCTURE_PREP", "UNKNOWN"}:
            return False
        text = cls.stage_intent_text(stage)
        if not text:
            return False
        extension_cues = re.search(
            r"\b(extension|extensions|same extension|file types?|png|jpg|jpeg|gif|webp|txt|json|py|photos?|images?|text files?)\b",
            text,
        )
        reorg_cues = re.search(
            r"\b(group|consolidat\w*|merge|organi[sz]\w*|sort|move|relocat\w*|delete empty|remove empty)\b",
            text,
        )
        return bool(extension_cues and reorg_cues)

    @classmethod
    def stage_is_dependency_recovery(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind != "UNKNOWN":
            return kind == "DEPENDENCY_RECOVERY"
        text = cls.stage_intent_text(stage)
        return bool(
            re.search(
                r"\b(missing module|missing dependency|module import error|import error|third-party|install package|dependency)\b",
                text,
            )
        )

    @classmethod
    def stage_requires_file_verification(cls, stage: StageCard) -> bool:
        return (
            cls.stage_is_file_work(stage)
            and not cls.stage_is_non_mutating_file_stage(stage)
            and not cls.stage_is_dependency_recovery(stage)
            and not cls.stage_is_script_launch_stage(stage)
        )

    @staticmethod
    def tool_requires_file_checker(tool_name: str, tool_result: Any) -> bool:
        if tool_name == "RUN_CODE":
            return True
        if tool_name != "FILE_OP" or not isinstance(tool_result, dict):
            return False
        action = str(tool_result.get("action", "")).lower()
        return action not in {"read_text", "read_many", "list_tree", "find_paths", "extension_inventory"}

    @staticmethod
    def is_file_read_result(tool_name: str, tool_result: Any) -> bool:
        if tool_name != "FILE_OP" or not isinstance(tool_result, dict):
            return False
        return str(tool_result.get("action", "")).lower() in {"read_text", "read_many", "list_tree", "find_paths", "extension_inventory"}

    @staticmethod
    @lru_cache(maxsize=512)
    def _planned_file_op_payload(tool_tag: str) -> dict[str, Any]:
        try:
            return parse_normalized_tool_tag_payload(tool_tag, tag="FILE_OP")
        except FileOpError:
            return {}

    @staticmethod
    def planned_file_op_action(tool_tag: str) -> str:
        return normalized_action_from_payload(FileStagePolicy._planned_file_op_payload(str(tool_tag or "")))

    @staticmethod
    def planned_file_op_path(tool_tag: str) -> str:
        return primary_path_from_payload(FileStagePolicy._planned_file_op_payload(str(tool_tag or "")))

    @staticmethod
    def planned_file_op_paths(tool_tag: str) -> list[str]:
        return path_list_from_payload(FileStagePolicy._planned_file_op_payload(str(tool_tag or "")))

    @staticmethod
    def planned_file_op_source_paths(tool_tag: str) -> list[str]:
        return source_paths_from_payload(FileStagePolicy._planned_file_op_payload(str(tool_tag or "")))

    @classmethod
    def is_code_path(cls, path: str) -> bool:
        return Path(str(path or "")).suffix.lower() in cls._CODE_FILE_EXTENSIONS

    @classmethod
    def paths_are_code_files(cls, paths: list[str]) -> bool:
        cleaned = [str(path or "").strip() for path in (paths or []) if str(path or "").strip()]
        return bool(cleaned) and all(cls.is_code_path(path) for path in cleaned)

    @staticmethod
    def file_op_root(tool_result: Any) -> str:
        if not isinstance(tool_result, dict):
            return ""
        return str(tool_result.get("requested_root") or tool_result.get("requested_path") or tool_result.get("path") or "").strip()

    @staticmethod
    def _normalize_stage_path_target(raw: Any) -> str:
        clean = str(raw or "").replace("\\", "/").strip().strip("'\"")
        if not clean:
            return ""
        lower = clean.lower()
        workspace_roots = {
            "c:/projects/piper/data/workspace",
            "/mnt/c/projects/piper/data/workspace",
            "/projects/piper/data/workspace",
            "data/workspace",
            "./data/workspace",
            "workspace",
            "./workspace",
        }
        if lower in workspace_roots:
            return "."
        for prefix in tuple(f"{root}/" for root in workspace_roots):
            if lower.startswith(prefix):
                clean = clean[len(prefix):]
                break
        clean = clean.lstrip("/")
        if clean.startswith("./"):
            clean = clean[2:]
        clean = clean.rstrip("/")
        if clean in {"", "."}:
            return "."
        normalized = Path(clean).as_posix()
        if normalized.startswith("../"):
            return ""
        return normalized

    @classmethod
    def stage_scope_root(cls, stage: StageCard) -> str:
        if not (cls.stage_is_extension_file_reorg(stage) or cls.stage_is_broad_file_reorg(stage)):
            return ""
        declared = cls._normalize_stage_path_target(stage.get("declared_scope_root"))
        if declared:
            return declared
        candidates: list[str] = []
        for raw in stage.get("active_targets") or []:
            normalized = cls._normalize_stage_path_target(raw)
            if normalized:
                candidates.append(normalized)
        if not candidates:
            raw_text = " ".join(
                [
                    str(stage.get("stage_goal", "")),
                    str(stage.get("success_condition", "")),
                    " ".join(str(item) for item in (stage.get("context") or [])),
                ]
            )
            for parts in re.findall(r"'([^']+)'|\"([^\"]+)\"", raw_text):
                candidate = next((part for part in parts if part), "")
                normalized = cls._normalize_stage_path_target(candidate)
                if normalized:
                    candidates.append(normalized)
        for candidate in candidates:
            if candidate == ".":
                return "."
            if Path(candidate).suffix and "/" not in candidate:
                continue
            return candidate
        return "." if "workspace root" in cls.stage_file_text(stage) else ""

    @staticmethod
    def describe_scope_root(root: str) -> str:
        clean = FileStagePolicy._normalize_stage_path_target(root)
        if not clean or clean == ".":
            return "the workspace root '.'"
        return f"'./{clean}'"

    @staticmethod
    def file_read_paths(tool_result: Any) -> list[str]:
        if not isinstance(tool_result, dict):
            return []
        action = str(tool_result.get("action", "")).lower()
        if action == "read_text":
            path = str(tool_result.get("requested_path") or tool_result.get("path") or "").strip()
            return [path] if path else []
        if action == "read_many":
            requested = [str(item).strip() for item in (tool_result.get("requested_paths") or []) if str(item).strip()]
            if requested:
                return requested
            files = tool_result.get("files") or {}
            if isinstance(files, dict):
                return [str(path).strip() for path in files.keys() if str(path).strip()]
        return []

    @staticmethod
    def stage_named_file_targets(stage: StageCard) -> list[str]:
        declared = [
            str(item).strip().lower()
            for item in (stage.get("declared_exact_targets") or [])
            if str(item).strip()
        ]
        if declared:
            seen: set[str] = set()
            ordered: list[str] = []
            for item in declared:
                if item not in seen:
                    seen.add(item)
                    ordered.append(item)
            return ordered
        text = " ".join(
            [
                str(stage.get("stage_goal", "")),
                str(stage.get("success_condition", "")),
                " ".join(str(item) for item in (stage.get("context") or [])),
            ]
        )
        return sorted({match.lower() for match in re.findall(r"[\w.-]+\.[A-Za-z0-9]{1,8}", text)})

    @classmethod
    def stage_lookup_terms(cls, stage: StageCard) -> list[str]:
        text = cls.stage_file_text(stage)
        if not text:
            return []
        terms: set[str] = set()
        for parts in re.findall(r"'([^']+)'|\"([^\"]+)\"", text):
            value = next((part for part in parts if part), "").strip()
            normalized = cls._normalize_lookup_term(value)
            if normalized:
                terms.add(value.lower())
        for match in re.finditer(r"\b([a-z0-9][a-z0-9_.-]*)-related\b", text, re.IGNORECASE):
            value = match.group(1).strip().lower()
            if cls._normalize_lookup_term(value):
                terms.add(value)
        return sorted(terms)

    @classmethod
    def stage_target_terms(cls, stage: StageCard) -> list[str]:
        return sorted(set(cls.stage_named_file_targets(stage)) | set(cls.stage_lookup_terms(stage)))

    @classmethod
    def stage_replacement_pair(cls, stage: StageCard) -> tuple[str, str] | None:
        text = cls.stage_goal_success_text(stage)
        if not text:
            return None
        match = re.search(
            r"\b(?:replace(?: the typo)?|correct(?: the typo)?|fix(?: the typo)?|rename|update)\s+['\"]([^'\"]+)['\"]\s+(?:with|to)\s+['\"]([^'\"]+)['\"]",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        old = str(match.group(1) or "").strip()
        new = str(match.group(2) or "").strip()
        if not old or not new or old == new:
            return None
        return old, new

    @classmethod
    def stage_has_overlapping_replacement_pair(cls, stage: StageCard) -> bool:
        pair = cls.stage_replacement_pair(stage)
        if pair is None:
            return False
        old, new = pair
        return old.lower() in new.lower()

    @classmethod
    def stage_requires_targeted_read(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind not in {"INSPECTION", "UNKNOWN"}:
            return False
        if cls.stage_requires_file_verification(stage):
            return False
        raw = cls.stage_goal_success_text(stage)
        text = cls.stage_intent_text(stage)
        if cls.stage_requires_analysis_report(stage):
            return False
        if not text:
            return False
        has_target = bool(cls.stage_target_terms(stage)) or "matching file" in raw or "best match" in raw
        if not has_target:
            return False
        explicit_content_request = bool(
            re.search(r"\b(read|show|tell|display|return|report|open)\b", text)
            and re.search(r"\b(contents?|text|full text|exact contents|file contents?)\b", raw)
        ) or any(
            phrase in raw
            for phrase in (
                "exact contents",
                "read its exact contents",
                "contents are read",
                "report its contents",
                "display the contents",
                "show the contents",
                "full text content",
            )
        )
        analysis_request = bool(
            re.search(
                r"\b(analy[sz]e|analysis|diagnos\w*|identify|debug|review|audit|root cause|bug|bugs|issue|issues|logical errors?)\b",
                raw,
            )
        )
        if analysis_request and not explicit_content_request:
            return False
        return bool(
            explicit_content_request
            or "exact contents" in raw
            or "contents are read" in raw
            or "report its contents" in raw
        )

    @classmethod
    def stage_requires_targeted_lookup(cls, stage: StageCard) -> bool:
        if not cls.stage_is_file_work(stage):
            return False
        kind = cls.stage_kind(stage)
        if kind and kind not in {"INSPECTION", "UNKNOWN"}:
            return False
        if cls.stage_is_script_launch_stage(stage):
            return False
        if cls.stage_requires_analysis_report(stage):
            return False
        raw = cls.stage_goal_success_text(stage)
        text = cls.stage_intent_text(stage)
        targets = cls.stage_target_terms(stage)
        has_target_hint = bool(targets) or any(
            phrase in raw
            for phrase in (
                "matching file",
                "matching files",
                "filename match",
                "filenames",
                "best match",
            )
        )
        if not has_target_hint:
            return False
        return bool(
            re.search(
                r"\b(find|search|locate|verify|availability|exists?|existence|missing|resolve|identify|confirm|match|scan)\b",
                text,
            )
        )

    @classmethod
    def stage_allows_absence_confirmation(cls, stage: StageCard) -> bool:
        raw = cls.stage_goal_success_text(stage)
        return bool(
            "absence" in raw
            or "not found" in raw
            or "no such file" in raw
            or "no such files exist" in raw
        )

    @classmethod
    def _query_matches_stage_targets(cls, query: str, stage: StageCard) -> bool:
        query_norm = cls._normalize_lookup_term(query)
        if not query_norm:
            return False
        for target in cls.stage_target_terms(stage):
            target_norm = cls._normalize_lookup_term(target)
            if target_norm and (
                query_norm == target_norm
                or query_norm in target_norm
                or target_norm in query_norm
            ):
                return True
        return False

    @classmethod
    def find_workspace_target_candidates(
        cls,
        workspace: Path,
        target: str,
        *,
        limit: int = 3,
    ) -> list[str]:
        root = Path(workspace)
        clean_target = str(target or "").replace("\\", "/").strip()
        if not clean_target or not root.exists():
            return []

        target_name = Path(clean_target).name.lower()
        target_stem = Path(clean_target).stem.lower()
        target_suffix = Path(clean_target).suffix.lower()
        target_norms = {
            cls._normalize_lookup_term(clean_target),
            cls._normalize_lookup_term(target_name),
            cls._normalize_lookup_term(target_stem),
        }
        target_norms.discard("")

        ranked: list[tuple[float, str]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            rel_l = rel.lower()
            if rel_l == clean_target.lower():
                continue

            candidate_name = path.name.lower()
            candidate_stem = path.stem.lower()
            candidate_norms = {
                cls._normalize_lookup_term(rel_l),
                cls._normalize_lookup_term(candidate_name),
                cls._normalize_lookup_term(candidate_stem),
            }
            candidate_norms.discard("")

            score = 0.0
            score = max(
                score,
                difflib.SequenceMatcher(None, target_name, candidate_name).ratio(),
                difflib.SequenceMatcher(None, target_stem, candidate_stem).ratio(),
            )
            if target_suffix and path.suffix.lower() == target_suffix:
                score += 0.08
            if target_name and candidate_name.startswith(target_name):
                score += 0.08
            if candidate_name and target_name.startswith(candidate_name):
                score += 0.12
            if target_stem and candidate_stem.startswith(target_stem):
                score += 0.08
            if candidate_stem and target_stem.startswith(candidate_stem):
                score += 0.12
            if any(
                query_norm
                and candidate_norm
                and cls._token_prefix_match(query_norm, candidate_norm)
                for query_norm in target_norms
                for candidate_norm in candidate_norms
            ):
                score += 0.14
            if any(
                query_norm
                and candidate_norm
                and (query_norm in candidate_norm or candidate_norm in query_norm)
                for query_norm in target_norms
                for candidate_norm in candidate_norms
            ):
                score += 0.08

            if score >= 0.55:
                ranked.append((round(score, 6), rel))

        ranked.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
        if not ranked:
            return []

        top_score = ranked[0][0]
        results: list[str] = []
        for score, rel in ranked:
            if score + 0.08 < top_score:
                break
            if rel not in results:
                results.append(rel)
            if len(results) >= max(1, int(limit or 1)):
                break
        return results

    @classmethod
    def _path_matches_stage_targets(cls, path: str, stage: StageCard) -> bool:
        path_l = str(path or "").lower().strip()
        if not path_l:
            return False
        targets = set(cls.stage_named_file_targets(stage))
        if path_l in targets or Path(path_l).name.lower() in targets:
            return True
        candidate_norms = {
            cls._normalize_lookup_term(path_l),
            cls._normalize_lookup_term(Path(path_l).name),
            cls._normalize_lookup_term(Path(path_l).stem),
        }
        for target in cls.stage_lookup_terms(stage):
            target_norm = cls._normalize_lookup_term(target)
            if target_norm and any(target_norm in candidate for candidate in candidate_norms):
                return True
            if file_reference_matches(target, path_l):
                return True
        return False

    @classmethod
    def non_mutating_file_stage_is_satisfied(cls, stage: StageCard, tool_name: str, tool_result: Any) -> bool:
        if not cls.is_file_read_result(tool_name, tool_result):
            return False
        if not isinstance(tool_result, dict):
            return False
        action = str(tool_result.get("action", "")).lower()
        if cls.stage_requires_targeted_read(stage):
            if action == "find_paths":
                match_count = int(tool_result.get("match_count", len(tool_result.get("matches") or [])) or 0)
                return (
                    match_count == 0
                    and cls.stage_allows_absence_confirmation(stage)
                    and cls._query_matches_stage_targets(str(tool_result.get("requested_query") or ""), stage)
                )
            if action == "read_text":
                requested = str(tool_result.get("requested_path") or tool_result.get("path") or "")
                return cls._path_matches_stage_targets(requested, stage)
            if action == "read_many":
                files = tool_result.get("files") or {}
                if isinstance(files, dict):
                    return any(cls._path_matches_stage_targets(str(path), stage) for path in files.keys())
            return False
        if cls.stage_requires_targeted_lookup(stage):
            if action == "find_paths":
                query = str(tool_result.get("requested_query") or "")
                matches = [str(path) for path in (tool_result.get("matches") or [])]
                match_count = int(tool_result.get("match_count", len(matches)) or 0)
                matches_target = any(cls._path_matches_stage_targets(path, stage) for path in matches)
                if not matches_target and not cls._query_matches_stage_targets(query, stage):
                    return False
                return bool(matches_target or (match_count == 0 and cls.stage_allows_absence_confirmation(stage)))
            if action == "read_text":
                requested = str(tool_result.get("requested_path") or tool_result.get("path") or "")
                return cls._path_matches_stage_targets(requested, stage)
            if action == "read_many":
                files = tool_result.get("files") or {}
                if isinstance(files, dict):
                    return any(cls._path_matches_stage_targets(str(path), stage) for path in files.keys())
            return False
        if cls.stage_requires_analysis_report(stage):
            return False
        if action == "list_tree":
            return True
        if action == "find_paths":
            return True
        if action == "read_text":
            return bool(str(tool_result.get("requested_path") or tool_result.get("path") or "").strip())
        if action == "read_many":
            files = tool_result.get("files") or {}
            return isinstance(files, dict) and bool(files)
        if action == "extension_inventory":
            return True
        return False

    @classmethod
    def file_recovery_hint(cls, stage: StageCard, tool_result: Any) -> str:
        if not isinstance(tool_result, dict):
            return ""
        action = str(tool_result.get("action", "")).lower()
        summary = str(tool_result.get("summary", "")).strip()
        status = str(tool_result.get("status", "")).upper()
        if cls.stage_is_script_launch_stage(stage) and status == "EXECUTED" and action == "find_paths":
            matches = [str(item).strip() for item in (tool_result.get("matches") or []) if str(item).strip()]
            script_match = next((path for path in matches if Path(path).suffix.lower() == ".py"), "")
            if script_match:
                return (
                    "SYSTEM HINT: You found the target script. "
                    f'Use RUN_CODE with exactly: run_workspace_script("{script_match}")'
                )
        if status == "BLOCKED" and cls.stage_is_script_launch_stage(stage):
            summary_l = summary.lower()
            if "importing 'subprocess' is blocked" in summary_l or "usage of 'subprocess' is blocked" in summary_l:
                script_targets = [path for path in cls.stage_named_file_targets(stage) if Path(path).suffix.lower() == ".py"]
                target = script_targets[0] if script_targets else "relative/path.py"
                return (
                    "SYSTEM HINT: To execute an existing workspace Python script, do not import subprocess or sys. "
                    f'Use RUN_CODE with exactly: run_workspace_script("{target}")'
                )
        if status == "BLOCKED" and cls.stage_is_content_edit_stage(stage):
            summary_l = summary.lower()
            if "importing 'sys' is blocked" in summary_l or "importing 'subprocess' is blocked" in summary_l:
                targets = cls.stage_named_file_targets(stage)
                target = targets[0] if targets else "relative/path.py"
                return (
                    f"SYSTEM HINT: The previous RUN_CODE edit was blocked by a restricted import. "
                    f"Do not import sys or subprocess for this code-file edit. "
                    f"Use plain open()/pathlib-based Python to patch '{target}', "
                    "or use one valid FILE_OP write_text if you already computed the final source."
                )
        if (
            (
                cls.stage_is_content_edit_stage(stage)
                or cls.stage_requires_targeted_read(stage)
                or cls.is_file_inspection_stage(stage)
            )
            and status == "EXECUTED"
            and action in {"read_text", "read_many"}
        ):
            paths = cls.file_read_paths(tool_result)
            if paths:
                target = paths[0] if len(paths) == 1 else ", ".join(paths[:3])
                if all(Path(path).suffix.lower() == ".json" for path in paths):
                    return (
                        f"SYSTEM HINT: You already inspected {target}. The current content is in the scratchpad. "
                        "This stage requires changing that artifact, so do not repeat the same unchanged read. "
                        "For JSON object changes, use FILE_OP update_json or write_json next."
                    )
                if cls.stage_is_content_edit_stage(stage) and cls.paths_are_code_files(paths):
                    files = tool_result.get("files") or {}
                    content_hints: list[str] = []
                    if isinstance(files, dict) and len(files) == 1:
                        _, content = next(iter(files.items()))
                        content_text = str(content or "")
                        for hint in (
                            cls._directional_control_gap_hint(stage, content_text),
                            cls._identifier_typo_hint(content_text),
                        ):
                            if hint:
                                content_hints.append(hint)
                    replacement_pair = cls.stage_replacement_pair(stage)
                    if replacement_pair is not None and cls.stage_has_overlapping_replacement_pair(stage):
                        old, new = replacement_pair
                        return (
                            f"SYSTEM HINT: You are correcting '{old}' to '{new}', and the desired token contains the old token. "
                            f"Do not use a naive global string replace on '{target}', because it can corrupt already-correct text. "
                            "Use the exact current source in the scratchpad, compute the full final file content once, and overwrite the file atomically."
                        )
                    base_hint = (
                        f"SYSTEM HINT: You already inspected {target}. The current code is in the scratchpad. "
                        "This is a code-file edit stage. Prefer RUN_CODE next to read-modify-write the file in Python. "
                        "If you already computed the exact final source, one valid FILE_OP write_text is also acceptable."
                    )
                    return (" ".join(content_hints + [base_hint])).strip() if content_hints else base_hint
                if cls.stage_requires_analysis_report(stage) and cls.paths_are_code_files(paths):
                    files = tool_result.get("files") or {}
                    content_hints: list[str] = []
                    if isinstance(files, dict) and len(files) == 1:
                        _, content = next(iter(files.items()))
                        content_text = str(content or "")
                        for hint in (
                            cls._directional_control_gap_hint(stage, content_text),
                            cls._identifier_typo_hint(content_text),
                        ):
                            if hint:
                                content_hints.append(hint)
                    base_hint = (
                        f"SYSTEM HINT: You already inspected {target}. The current source is in the scratchpad. "
                        "This is a diagnosis stage, so do not stop at the read. Identify all concrete visible issues in the current source, not just one, "
                        "and summarize them explicitly in the proposal field. "
                        "Summarize the concrete bug or missing handler explicitly in the proposal field."
                    )
                    return (" ".join(content_hints + [base_hint])).strip() if content_hints else base_hint
                return (
                    f"SYSTEM HINT: You already inspected {target}. The current content is in the scratchpad. "
                    "Do not repeat the same unchanged read just because the observation preview was truncated. "
                    "Use the exact scratchpad content for analysis or proceed to the next concrete change."
                )
        if cls.stage_is_extension_file_reorg(stage) and action == "list_tree" and status == "EXECUTED":
            scope_root = cls.stage_scope_root(stage) or "."
            scope_label = cls.describe_scope_root(scope_root)
            return (
                "SYSTEM HINT: This stage is about organizing files by extension. "
                f"Use FILE_OP extension_inventory on {scope_label}, then FILE_OP consolidate_by_extension "
                "or delete_empty_dirs as needed."
            )
        if cls.stage_is_extension_file_reorg(stage) and action == "consolidate_by_extension" and status == "FAILED":
            collisions = [
                str(item).strip()
                for item in (tool_result.get("collisions") or [])
                if str(item).strip()
            ]
            if collisions:
                collision_preview = "; ".join(collisions[:3])
                return (
                    "SYSTEM HINT: consolidate_by_extension already identified different-content name collisions. "
                    f"Review the collision list in OBSERVATION_TEXT ({collision_preview}). "
                    "Do not repeat list_tree. Re-run consolidate_by_extension with an `exclude_files` list for the specific "
                    "colliding source filenames you want to leave in place, or use FILE_OP find_paths on one collided basename "
                    "if you need to inspect that case before choosing."
                )
        if (
            (
                cls.stage_requires_targeted_lookup(stage)
                or (
                    cls.stage_is_content_edit_stage(stage)
                    and bool(cls.stage_target_terms(stage))
                    and not cls.stage_named_file_targets(stage)
                )
            )
            and action == "list_tree"
            and status == "EXECUTED"
        ):
            targets = cls.stage_target_terms(stage)
            if targets:
                return (
                    "SYSTEM HINT: This stage is a targeted file lookup. Use FILE_OP find_paths with a filename query like "
                    f"'{targets[0]}' instead of broad list_tree."
                )
        if (
            (cls.stage_is_content_edit_stage(stage) or cls.stage_requires_targeted_lookup(stage))
            and action == "find_paths"
            and status == "EXECUTED"
        ):
            matches = [str(item).strip() for item in (tool_result.get("matches") or []) if str(item).strip()]
            if len(matches) > 1:
                named_targets = {
                    str(path).strip().replace("\\", "/").lower()
                    for path in cls.stage_named_file_targets(stage)
                    if str(path).strip()
                }
                exact_match = next((path for path in matches if path.lower() in named_targets), "")
                if exact_match:
                    alternates = [path for path in matches if path.lower() != exact_match.lower()]
                    alt_preview = ", ".join(alternates[:2])
                    if alt_preview:
                        return (
                            "SYSTEM HINT: An exact file match was found. "
                            f"Read '{exact_match}' directly before nested alternatives like {alt_preview}. "
                            "Do not compare multiple files unless the user explicitly asked you to."
                        )
                    return (
                        "SYSTEM HINT: An exact file match was found. "
                        f"Read '{exact_match}' directly."
                    )
                preferred = next((path for path in matches if "/" not in path), matches[0])
                alternates = [path for path in matches if path != preferred]
                alt_preview = ", ".join(alternates[:2])
                if alt_preview:
                    return (
                        "SYSTEM HINT: Multiple plausible file matches were found. "
                        f"Unless the user specified a subdirectory, prefer '{preferred}' before nested alternatives like {alt_preview}. "
                        "Proceed to inspect or edit the preferred match instead of asking for clarification too early."
                    )
                return (
                    "SYSTEM HINT: Multiple plausible file matches were found. "
                    f"Unless the user specified a subdirectory, prefer '{preferred}' first."
                )
        missing = [str(item) for item in (tool_result.get("missing_files") or []) if str(item).strip()]
        if status == "FAILED" and "source not found:" in summary.lower():
            missing_path = missing[0] if missing else summary.split(":", 1)[-1].strip()
            if missing_path:
                return (
                    "SYSTEM HINT: A move/copy source path was missing. Use FILE_OP find_paths on basename "
                    f"'{Path(missing_path).name}' under '.' before planning more moves."
                )
        return ""

    @classmethod
    def _directional_control_gap_hint(cls, stage: StageCard, content: str) -> str:
        if not cls.stage_mentions_directional_controls(stage):
            return ""
        text = str(content or "")
        left_ok = cls._has_directional_control_logic(text, "left")
        right_ok = cls._has_directional_control_logic(text, "right")
        if left_ok and not right_ok:
            return (
                "SYSTEM HINT: The current source contains left-direction control handling but no matching right-direction control handler. "
                "The stage is not solved until both directions are handled."
            )
        if right_ok and not left_ok:
            return (
                "SYSTEM HINT: The current source contains right-direction control handling but no matching left-direction control handler. "
                "The stage is not solved until both directions are handled."
            )
        return ""

    @staticmethod
    def _has_directional_control_logic(content: str, direction: str) -> bool:
        text = str(content or "").lower()
        direction_l = str(direction or "").strip().lower()
        if not direction_l:
            return False
        patterns = (
            rf"pygame\.k_{direction_l}",
            rf"\b(?:if|elif|case)\b[^\n]{{0,160}}\b(?:event\.key|key|input|direction|move|moving|velocity|dx)\b[^\n]{{0,160}}['\"]{direction_l}['\"]",
            rf"\b(?:if|elif|case)\b[^\n]{{0,160}}['\"]{direction_l}['\"][^\n]{{0,160}}\b(?:event\.key|key|input|direction|move|moving|velocity|dx)\b",
        )
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    @staticmethod
    def _identifier_typo_hint(content: str) -> str:
        text = str(content or "")
        if not text:
            return ""
        defined = {match for match in re.findall(r"\b([A-Z][A-Z0-9_]{2,})\s*=", text)}
        used = {match for match in re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", text)}
        suspect_pairs: list[tuple[str, str]] = []
        for used_token in sorted(used - defined):
            for defined_token in sorted(defined):
                if used_token == defined_token:
                    continue
                if abs(len(used_token) - len(defined_token)) > 2:
                    continue
                score = difflib.SequenceMatcher(a=used_token, b=defined_token).ratio()
                if score >= 0.88:
                    suspect_pairs.append((used_token, defined_token))
                    break
        if not suspect_pairs:
            return ""
        used_token, defined_token = suspect_pairs[0]
        return (
            f"SYSTEM HINT: The current source appears to use '{used_token}' while defining the near-match token '{defined_token}' elsewhere. "
            "That looks like an identifier typo and should be included in the diagnosis or repair plan."
        )

    @staticmethod
    def file_checker_recovery_hint(stage: Any, tool_result: Any, file_check: Any) -> str:
        """Return a SYSTEM HINT string to guide the planner past a FAILED verification.

        .. deprecated:: 2.0
            Use :meth:`core.engines.file_work.FileWorkEngine.recovery_hint` instead.
            This shim will be removed in a future version.
        """
        import warnings
        warnings.warn(
            "FileStagePolicy.file_checker_recovery_hint is deprecated. "
            "Use FileWorkEngine.recovery_hint instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from core.engines.file_work import FileWorkEngine
        return FileWorkEngine.recovery_hint(stage, tool_result, file_check)
