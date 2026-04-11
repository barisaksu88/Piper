"""core/engines/file_work.py

FileWorkEngine — centralised owner for file/code evidence-handling mechanics.

Responsibilities:
  - unified candidate path extraction from tool results
  - code/artifact view rendering
  - exact-read scratchpad capture
  - blocked-write guards
  - recovery hint generation
  - code extension registry (single definition site for executor-side code)
  - stage kind classification

What this engine does NOT own:
  - VERIFIED / PARTIAL / FAILED decisions   (VerificationEngine)
  - deterministic rule checking              (LocalFileOpRuleChecker)
  - LLM-based file checking                 (FileWorkChecker)
  - workspace mutation                       (workspace_mutation_actions)
  - the stage step loop                      (StageExecutor)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from core.contracts import FileStageKind, FileWorkBlock, FileWorkEvidence, StageCard
from core.file_extensions import CODE_FILE_EXTENSIONS
from core.file_stage_policy import FileStagePolicy


class FileWorkEngine:
    """Centralised owner for file/code evidence-handling mechanics.

    All public methods are static or class methods — the engine carries no
    instance state and is safe to use without instantiation.
    """

    # ------------------------------------------------------------------ #
    # Code extension registry                                             #
    # Executor-side code imports CODE_FILE_EXTENSIONS from here.          #
    # file_stage_policy imports from core.file_extensions directly        #
    # (circular import prevention).                                       #
    # ------------------------------------------------------------------ #
    CODE_FILE_EXTENSIONS: frozenset[str] = CODE_FILE_EXTENSIONS

    # Exact-read budget constants (mirrored from StageExecutor class vars).
    EXACT_READ_MAX_FILES: int = 2
    EXACT_READ_MAX_TOTAL_CHARS: int = 14_000
    CODE_WRITE_TEXT_TAG_MAX_CHARS: int = 50_000
    TASK_EVENT_RUN_CODE_HELPERS: frozenset[str] = frozenset(
        {
            "add_event",
            "add_task",
            "close_event",
            "complete_event",
            "complete_task",
            "delete_task",
            "list_events",
            "list_tasks",
            "remove_event",
            "remove_task",
            "reschedule_event",
        }
    )
    TASK_EVENT_RUN_CODE_STORES: frozenset[str] = frozenset({"event_store", "task_store"})

    # ------------------------------------------------------------------ #
    # Candidate path extraction                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def candidate_paths(tool_result: Any) -> list[str]:
        """Extract and de-duplicate all candidate file paths from a tool result dict.

        Unified superset of the previously scattered implementations in:
          - executor._file_result_candidate_paths
          - file_checker._candidate_paths_from_evidence
          - file_stage_policy.tool_result_candidate_paths

        Returns an ordered list with backslashes normalised to forward-slashes and
        all duplicates removed.
        """
        if not isinstance(tool_result, dict):
            return []
        candidates: list[str] = []
        for key in ("requested_path", "path", "launched_script"):
            value = str(tool_result.get(key) or "").strip()
            if value:
                candidates.append(value)
        for key in (
            "requested_paths",
            "matches",
            "updated_files",
            "created_files",
            "deleted_files",
            "evidence_files",
        ):
            values = tool_result.get(key) or []
            if isinstance(values, list):
                candidates.extend(str(item).strip() for item in values if str(item).strip())
        for key in ("requested_moves", "requested_copies"):
            values = tool_result.get(key) or []
            if isinstance(values, list):
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    for inner_key in ("src", "dst"):
                        value = str(item.get(inner_key) or "").strip()
                        if value:
                            candidates.append(value)
        files = tool_result.get("files") or {}
        if isinstance(files, dict):
            candidates.extend(str(path).strip() for path in files.keys() if str(path).strip())
        snippets = tool_result.get("file_snippets") or {}
        if isinstance(snippets, dict):
            candidates.extend(str(path).strip() for path in snippets.keys() if str(path).strip())

        ordered: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = candidate.replace("\\", "/").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    # ------------------------------------------------------------------ #
    # Scratchpad exact-read path extraction                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def exact_read_paths_from_scratchpad(scratchpad: list[str]) -> list[str]:
        """Return the ordered de-duplicated list of FILE_READ_EXACT_PATH values
        recorded in the scratchpad entries.

        Extracted from executor._scratchpad_exact_read_paths.
        """
        paths: list[str] = []
        for entry in scratchpad:
            for match in re.findall(r"FILE_READ_EXACT_PATH:\s*([^\n]+)", str(entry or "")):
                clean = str(match or "").strip()
                if clean:
                    paths.append(clean)
        ordered: list[str] = []
        seen: set[str] = set()
        for path in paths:
            lowered = path.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(path)
        return ordered

    # ------------------------------------------------------------------ #
    # Artifact rendering                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def _is_code_path(cls, path: str) -> bool:
        return Path(str(path or "")).suffix.lower() in cls.CODE_FILE_EXTENSIONS

    @classmethod
    def render_artifact_view(cls, tool_result: Any) -> str:
        """Format a code/content preview string from a tool result.

        Returns an empty string if the result contains no displayable code files.
        The caller is responsible for pushing the returned string to the UI queue.

        Extracted from executor._render_code_view.
        """
        if not isinstance(tool_result, dict):
            return ""

        sections: list[tuple[str, str, bool, int]] = []
        seen_paths: set[str] = set()

        files = tool_result.get("files")
        if isinstance(files, dict):
            for path, content in list(files.items())[:6]:
                if not cls._is_code_path(str(path)):
                    continue
                clean_path = str(path)
                if clean_path in seen_paths:
                    continue
                seen_paths.add(clean_path)
                sections.append((clean_path, str(content or ""), False, len(str(content or ""))))
                if len(sections) >= 3:
                    break

        file_snippets = tool_result.get("file_snippets")
        if isinstance(file_snippets, dict) and len(sections) < 3:
            for path, snippet in list(file_snippets.items())[:6]:
                if not cls._is_code_path(str(path)):
                    continue
                if str(path) in seen_paths or not isinstance(snippet, dict):
                    continue
                if str(snippet.get("status", "")).lower() != "text":
                    continue
                seen_paths.add(str(path))
                sections.append(
                    (
                        str(path),
                        str(snippet.get("content") or ""),
                        bool(snippet.get("truncated")),
                        int(snippet.get("full_char_count") or 0),
                    )
                )
                if len(sections) >= 3:
                    break

        if not sections:
            return ""

        parts = ["Latest code preview(s)", ""]
        for path, content, truncated, full_char_count in sections:
            parts.append(f"Path: {path}")
            if truncated:
                parts.append(f"Preview only: showing the first part of a {full_char_count}-character file.")
            parts.append("")
            parts.append(content.strip() or "[empty file]")
            parts.append("")
            parts.append("-" * 72)
            parts.append("")
        return "\n".join(parts).rstrip()

    # ------------------------------------------------------------------ #
    # Exact-read capture                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def capture_exact_read(
        cls,
        stage: StageCard,
        tool_result: Any,
        existing_read_paths: list[str],
    ) -> str | None:
        """Decide whether a file read result should be captured to the planner
        scratchpad, and if so return the formatted note string.

        Returns None if the result should NOT be captured.

        The caller (executor) is responsible for appending the returned string to
        the scratchpad and for enforcing de-duplication (do not append if the note
        is already present).

        Combines executor._should_capture_exact_file_read_for_planner and
        executor._append_exact_file_read_note_from_result.

        Parameters
        ----------
        stage:
            The current stage card.
        tool_result:
            The FILE_OP tool result dict.
        existing_read_paths:
            Paths already captured this stage, as returned by
            ``exact_read_paths_from_scratchpad``.  Used only to satisfy the
            call signature; budget enforcement uses the files count in the result.
        """
        if not isinstance(tool_result, dict):
            return None
        action = str(tool_result.get("action", "")).lower()
        if action == "read_text":
            should_capture = True
        elif action == "read_many":
            files = tool_result.get("files") or {}
            file_count = len(files) if isinstance(files, dict) else 0
            if file_count <= 1:
                should_capture = True
            else:
                should_capture = bool(
                    file_count <= cls.EXACT_READ_MAX_FILES
                    and (
                        FileStagePolicy.stage_requires_targeted_read(stage)
                        or FileStagePolicy.stage_is_content_edit_stage(stage)
                        or FileStagePolicy.is_file_inspection_stage(stage)
                    )
                )
        else:
            should_capture = False

        if not should_capture:
            return None

        files = tool_result.get("files") or {}
        if not isinstance(files, dict) or not files:
            return None
        parts: list[str] = []
        total_chars = 0
        for path, content in list(files.items())[: cls.EXACT_READ_MAX_FILES]:
            content_text = str(content or "")
            addition = len(path) + len(content_text)
            if parts and (total_chars + addition) > cls.EXACT_READ_MAX_TOTAL_CHARS:
                break
            parts.append(f"FILE_READ_EXACT_PATH: {path}\nFILE_READ_EXACT_CONTENT:\n{content_text}")
            total_chars += addition
        note = "\n\n".join(parts).strip()
        return note if note else None

    # ------------------------------------------------------------------ #
    # Blocked-write guards                                                #
    # ------------------------------------------------------------------ #

    @classmethod
    def should_block(
        cls,
        stage: StageCard,
        tool_tag: str,
        exact_read_paths: list[str],
        *,
        operational_state_service: Any = None,
    ) -> FileWorkBlock:
        """Check whether the proposed tool call should be suppressed.

        Covers three scenarios (checked in this order):
          1. Cross-domain dependency — DELETE or MOVE targets a path that is
             referenced by an active task or event (R-6 State Mutex).
             Returns a *fatal* block — the executor stops the stage entirely.
          2. Redundant exact read of a file already captured in the scratchpad.
          3. Full-source embedding inside a FILE_OP write_text payload.

        Returns a FileWorkBlock with blocked=True and a SYSTEM ERROR reason string
        when a block applies; otherwise returns FileWorkBlock(blocked=False).

        Parameters
        ----------
        stage:
            The current stage card.
        tool_tag:
            The raw JSON string of the planned tool call.
        exact_read_paths:
            Paths already captured this stage, as returned by
            ``exact_read_paths_from_scratchpad``.
        operational_state_service:
            Optional OperationalStateService used for cross-domain dependency
            checks.  Pass None to skip Guard 1 (safe for tests or contexts
            where the service is unavailable).
        """
        # Guard 1 (cross-domain dependency): applies before the content-edit
        # gate so it fires for RELOCATION and other non-content-edit stages too.
        if operational_state_service is not None:
            _dep_block = cls._check_active_dependency(
                tool_tag,
                operational_state_service,
                dependency_override_authorized=bool(stage.get("dependency_override_authorized")),
            )
            if _dep_block.blocked:
                return _dep_block

        planned_action = FileStagePolicy.planned_file_op_action(tool_tag)
        if FileStagePolicy.stage_is_file_work(stage) and planned_action == "read_many":
            named_targets = [
                str(path).strip().replace("\\", "/").lower()
                for path in FileStagePolicy.stage_named_file_targets(stage)
                if str(path).strip()
            ]
            planned_paths = [
                str(path).strip().replace("\\", "/")
                for path in FileStagePolicy.planned_file_op_paths(tool_tag)
                if str(path).strip()
            ]
            if len(named_targets) == 1 and len(planned_paths) > 1:
                exact_target = named_targets[0]
                if any(path.lower() == exact_target for path in planned_paths) and any(
                    path.lower() != exact_target for path in planned_paths
                ):
                    return FileWorkBlock(
                        blocked=True,
                        reason=(
                            f"SYSTEM ERROR: This stage already names the exact file '{exact_target}'. "
                            "Do not read alternate matches in the same step. "
                            "Use FILE_OP read_text on the exact target only unless the user explicitly asked to compare files."
                        ),
                    )

        # Guards 2 & 3 only apply to CONTENT_EDIT stages.
        if not FileStagePolicy.stage_is_content_edit_stage(stage):
            return FileWorkBlock()

        # Guard 1 (checked first, matches executor priority): redundant exact read
        if planned_action in {"read_text", "read_many"}:
            planned_paths = FileStagePolicy.planned_file_op_paths(tool_tag)
            if planned_paths and exact_read_paths:
                exact_lookup = {path.lower(): path for path in exact_read_paths}
                overlapping = [
                    exact_lookup[path.lower()] for path in planned_paths if path.lower() in exact_lookup
                ]
                if overlapping:
                    target = ", ".join(overlapping[:3])
                    if FileStagePolicy.paths_are_code_files(overlapping):
                        reason = (
                            f"SYSTEM ERROR: Exact current source for '{target}' is already in the scratchpad. "
                            "Do not reread the same unchanged code file or claim the read was truncated. "
                            "Use the existing source and choose RUN_CODE to modify it, "
                            "or use one valid FILE_OP write_text if you already computed the final source, "
                            "or return is_complete true with a proposal if the stage is already solved."
                        )
                    else:
                        reason = (
                            f"SYSTEM ERROR: Exact current file contents for '{target}' are already in the scratchpad. "
                            "Do not reread the same unchanged file or claim the read was truncated. "
                            "Use the existing content to compute the next edit."
                        )
                    return FileWorkBlock(blocked=True, reason=reason)

        # Guard 2: full-source embedding in write_text
        if planned_action == "write_text":
            planned_path = FileStagePolicy.planned_file_op_path(tool_tag)
            candidate_paths = [path for path in [planned_path, *exact_read_paths] if str(path or "").strip()]
            if FileStagePolicy.paths_are_code_files(candidate_paths[:1] or candidate_paths):
                # If the planner is rewriting the exact code file that was already read
                # into scratchpad, let the structured FILE_OP write go through.
                already_read = planned_path and any(
                    path.lower() == planned_path.lower() for path in exact_read_paths
                )
                if not already_read and (
                    len(str(tool_tag or "")) >= cls.CODE_WRITE_TEXT_TAG_MAX_CHARS or exact_read_paths
                ):
                    target = planned_path or (exact_read_paths[0] if exact_read_paths else "")
                    reason = (
                        f"SYSTEM ERROR: This is a code-file edit stage for '{target or 'the current source file'}'. "
                        "Do not embed a full source file inside FILE_OP write_text JSON. "
                        "Use RUN_CODE to read-modify-write the file in Python instead."
                    )
                    return FileWorkBlock(blocked=True, reason=reason)

        return FileWorkBlock()

    @classmethod
    def _check_run_code_dependency(
        cls,
        tool_tag: str,
        operational_state_service: Any,
        *,
        dependency_override_authorized: bool = False,
    ) -> FileWorkBlock:
        """Return a fatal FileWorkBlock if a RUN_CODE payload deletes or moves a
        file that is referenced by an active task or event (R-6 State Mutex).

        Scans the Python code for common deletion and rename/move patterns and
        extracts string-literal path arguments.  Only straightforward constant
        paths can be detected — dynamic paths (variables, f-strings) are not
        checked and pass through silently.

        Called by the executor when ``base_tag == "RUN_CODE"`` and
        ``operational_state_service`` is available.
        """
        if dependency_override_authorized:
            return FileWorkBlock()
        import re as _re
        code = cls._extract_run_code_body(tool_tag)
        if not code.strip():
            return FileWorkBlock()

        # Regex patterns that extract a single string-literal path argument.
        # Only the SOURCE path matters (first argument) for dependency checks.
        _DELETION_PATTERNS = [
            r'os\.(?:remove|unlink)\s*\(\s*["\']([^"\']+)["\']',
            r'Path\s*\(\s*["\']([^"\']+)["\'\s)]+\)\.unlink\s*\(',
            r'shutil\.rmtree\s*\(\s*["\']([^"\']+)["\']',
        ]
        _MOVE_PATTERNS = [
            r'(?:os\.rename|shutil\.move)\s*\(\s*["\']([^"\']+)["\']',
            r'Path\s*\(\s*["\']([^"\']+)["\'\s)]+\)\.rename\s*\(',
        ]
        paths_to_check: list[str] = []
        for pattern in _DELETION_PATTERNS + _MOVE_PATTERNS:
            for m in _re.finditer(pattern, code, _re.IGNORECASE):
                p = m.group(1).strip().replace("\\", "/")
                if p and p not in paths_to_check:
                    paths_to_check.append(p)

        for path in paths_to_check:
            try:
                conflicts = operational_state_service.find_references(path)
            except Exception:
                continue
            if conflicts:
                first = conflicts[0]
                name = str(first.get("name") or "unknown")
                kind = str(first.get("kind") or "item")
                # Determine verb from which pattern list matched.
                is_delete = any(
                    _re.search(p, code, _re.IGNORECASE) for p in _DELETION_PATTERNS
                )
                verb = "delete" if is_delete else "move"
                reason = (
                    f"ACTIVE_TASK_DEPENDENCY: Cannot {verb} '{path}': "
                    f"referenced by active {kind} '{name}'. "
                    "Close or update the dependent task/event first, or override explicitly."
                )
                return FileWorkBlock(blocked=True, reason=reason, fatal=True)

        return FileWorkBlock()

    @staticmethod
    def _extract_run_code_body(tool_tag: str) -> str:
        code_match = re.search(
            r"\[RUN_CODE\]\s*(.*?)\s*\[/RUN_CODE\]",
            tool_tag or "",
            re.DOTALL | re.IGNORECASE,
        )
        return code_match.group(1) if code_match else str(tool_tag or "")

    @classmethod
    def _check_run_code_task_event_escape(cls, tool_tag: str) -> FileWorkBlock:
        """Block FILE_WORK RUN_CODE payloads that try to mutate or inspect
        task/event state instead of staying within the file/code domain.

        The router owns domain selection. Once execution is inside FILE_WORK,
        retrying earlier TASK_EVENT_WORK prerequisites through ad-hoc RUN_CODE
        is a domain escape and must not proceed.
        """
        code = cls._extract_run_code_body(tool_tag)
        if not code.strip():
            return FileWorkBlock()

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return FileWorkBlock()

        escape_name = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "") == "workspace":
                for alias in node.names:
                    name = str(alias.name or "").strip()
                    if name in cls.TASK_EVENT_RUN_CODE_HELPERS:
                        escape_name = name
                        break
            elif isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = str(node.func.id or "").strip()
                elif isinstance(node.func, ast.Attribute):
                    func_name = str(node.func.attr or "").strip()
                if func_name in cls.TASK_EVENT_RUN_CODE_HELPERS:
                    escape_name = func_name
            elif isinstance(node, ast.Attribute):
                attr_name = str(node.attr or "").strip()
                if attr_name in cls.TASK_EVENT_RUN_CODE_STORES:
                    escape_name = attr_name
            if escape_name:
                break

        if not escape_name:
            return FileWorkBlock()

        reason = (
            "SYSTEM ERROR: FILE_WORK RUN_CODE cannot inspect or mutate task/event state "
            f"(detected '{escape_name}'). "
            "Prior TASK_EVENT_WORK results already recorded in the scratchpad are authoritative. "
            "Do not redo task/event work inside this FILE_WORK stage. "
            "Proceed with file operations only, or stop and let routing create the required TASK_EVENT_WORK stage."
        )
        return FileWorkBlock(blocked=True, reason=reason)

    @classmethod
    def _check_active_dependency(
        cls,
        tool_tag: str,
        operational_state_service: Any,
        *,
        dependency_override_authorized: bool = False,
    ) -> FileWorkBlock:
        """Return a fatal FileWorkBlock if a DELETE or MOVE targets a path that
        is referenced by an active task or event.

        Called by should_block() when operational_state_service is provided.
        Returns FileWorkBlock() (not blocked) when the action is safe to proceed.
        """
        if dependency_override_authorized:
            return FileWorkBlock()
        planned_action = FileStagePolicy.planned_file_op_action(tool_tag)
        _delete_actions = {"delete_path", "delete_many"}
        _move_actions = {"move_path", "move_many"}
        if planned_action not in _delete_actions | _move_actions:
            return FileWorkBlock()

        paths_to_check: list[str] = []
        if planned_action == "delete_path":
            p = FileStagePolicy.planned_file_op_path(tool_tag)
            if p:
                paths_to_check.append(p)
        elif planned_action == "delete_many":
            # delete_many encodes targets in a "paths" array.
            for p in FileStagePolicy.planned_file_op_paths(tool_tag):
                if p and p not in paths_to_check:
                    paths_to_check.append(p)
        else:
            for p in FileStagePolicy.planned_file_op_source_paths(tool_tag):
                if p and p not in paths_to_check:
                    paths_to_check.append(p)

        is_delete = planned_action in _delete_actions
        for path in paths_to_check:
            try:
                conflicts = operational_state_service.find_references(path)
            except Exception:
                continue
            if conflicts:
                first = conflicts[0]
                name = str(first.get("name") or "unknown")
                kind = str(first.get("kind") or "item")
                verb = "delete" if is_delete else "move"
                reason = (
                    f"ACTIVE_TASK_DEPENDENCY: Cannot {verb} '{path}': "
                    f"referenced by active {kind} '{name}'. "
                    "Close or update the dependent task/event first, or override explicitly."
                )
                return FileWorkBlock(blocked=True, reason=reason, fatal=True)

        return FileWorkBlock()

    # ------------------------------------------------------------------ #
    # Recovery hint                                                       #
    # ------------------------------------------------------------------ #

    @classmethod
    def recovery_hint(cls, stage: StageCard, tool_result: Any, file_check: Any) -> str:
        """Return a SYSTEM HINT string to guide the planner past a FAILED verification.

        Returns an empty string if no specific recovery guidance applies.

        Extracted from FileStagePolicy.file_checker_recovery_hint.
        """
        if not isinstance(tool_result, dict) or not isinstance(file_check, dict):
            return ""
        if not FileStagePolicy.stage_is_file_work(stage):
            return ""

        verdict = str(file_check.get("verdict", "")).upper()
        if verdict != "FAILED":
            return ""

        reason = str(file_check.get("reason", "")).strip()
        reason_l = reason.lower()
        tool_name = str(tool_result.get("tool", "")).upper()
        action = str(tool_result.get("action", "")).lower()
        candidates = cls.candidate_paths(file_check) + cls.candidate_paths(tool_result)
        target = next((path for path in candidates if path), "")

        if action == "write_text" and "invalid file_op json" in reason_l:
            return (
                "SYSTEM HINT: The previous FILE_OP write_text payload was invalid JSON. "
                "Do not paste raw multiline code into FILE_OP JSON unless every newline is escaped. "
                "For substantive code rewrites, prefer RUN_CODE. If you stay with FILE_OP write_text, "
                "emit one valid JSON object only."
            )

        if (
            FileStagePolicy.stage_is_content_edit_stage(stage)
            and tool_name == "RUN_CODE"
            and FileStagePolicy.paths_are_code_files(candidates[:1] or candidates)
            and (
                "does not match the requested content" in reason_l
                or "does not satisfy" in reason_l
                or "expected_present_texts" in reason_l
                or "expected_absent_texts" in reason_l
            )
        ):
            return (
                f"SYSTEM HINT: The previous code rewrite changed '{target or 'the current source file'}', "
                "but the artifact on disk still does not satisfy the requested final state. "
                "Use the exact current on-disk source from the scratchpad as the new baseline. "
                "Do not repeat the same full-file rewrite. Preserve real newlines and indentation, "
                "make only the necessary localized edits, "
                "and avoid building corrected_content as a one-line triple-quoted blob. "
                "For bounded fixes, patch the current text or rebuild a list of lines and join with "
                "'\\n' before writing once."
            )

        return ""

    # ------------------------------------------------------------------ #
    # Stage classification                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def classify(stage: StageCard) -> FileStageKind:
        """Map a StageCard to a FileStageKind constant.

        Provides a single dispatch point for callers that need to know what a
        file/code stage is doing without calling FileStagePolicy directly.
        """
        if FileStagePolicy.stage_is_script_launch_stage(stage):
            return "SCRIPT_LAUNCH"
        if FileStagePolicy.stage_is_dependency_recovery(stage):
            return "DEPENDENCY_RECOVERY"
        if FileStagePolicy.is_file_inspection_stage(stage) or FileStagePolicy.is_file_planning_stage(stage):
            return "INSPECTION"
        if FileStagePolicy.stage_is_content_edit_stage(stage):
            return "CONTENT_EDIT"
        if (
            FileStagePolicy.stage_is_extension_file_reorg(stage)
            or FileStagePolicy.stage_is_structure_prep_stage(stage)
        ):
            return "STRUCTURE_PREP"
        if FileStagePolicy.stage_is_broad_file_reorg(stage):
            return "BROAD_REORG"
        return "UNKNOWN"

    # ------------------------------------------------------------------ #
    # Convenience: collect all evidence in one call                      #
    # ------------------------------------------------------------------ #

    @classmethod
    def collect_evidence(
        cls,
        stage: StageCard,
        tool_result: Any,
        existing_read_paths: list[str],
    ) -> FileWorkEvidence:
        """Collect all evidence from a tool result in one call.

        Combines candidate_paths, render_artifact_view, and capture_exact_read.
        """
        paths = cls.candidate_paths(tool_result)
        view = cls.render_artifact_view(tool_result)
        note = cls.capture_exact_read(stage, tool_result, existing_read_paths) or ""
        return FileWorkEvidence(candidate_paths=paths, artifact_view=view, exact_read_note=note)

    # ------------------------------------------------------------------ #
    # Constraint derivation                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def derive_constraints(
        stage: StageCard,
        tool_result: Any = None,
    ) -> list[dict]:
        """Derive a PlanConstraint list for VerificationEngine to check.

        Priority:
          1. Explicit ``constraints`` field on the stage card (router- or
             planner-emitted). Returned as-is if present and non-empty.
          2. Structural derivation from the tool result for unambiguous
             single-operation cases (one MOVED pair, one DELETED/CREATED file).
          3. Empty list — no constraints determinable; caller falls through
             to the existing RULES → LLM verification path.

        Only derive from tool results when the mapping is unambiguous.
        Bulk operations (50 moves) still go to LocalFileOpRuleChecker.
        """
        # Priority 1: explicit constraints already set
        explicit = [c for c in (stage.get("constraints") or []) if isinstance(c, dict)]
        if explicit:
            return explicit

        if not isinstance(tool_result, dict):
            return []

        derived: list[dict] = []

        # MOVED: only derive when exactly one move pair present
        moves = [m for m in (tool_result.get("requested_moves") or []) if isinstance(m, dict)]
        if len(moves) == 1:
            src = str(moves[0].get("src") or "").strip().replace("\\", "/")
            dst = str(moves[0].get("dst") or "").strip().replace("\\", "/")
            if src and dst:
                derived.append({"type": "MOVED", "scope": "FILE", "from_path": src, "to_path": dst})

        # DELETED: only derive when exactly one path present
        deleted = [str(p).strip().replace("\\", "/") for p in (tool_result.get("deleted_files") or []) if str(p).strip()]
        if len(deleted) == 1:
            derived.append({"type": "DELETED", "scope": "FILE", "path": deleted[0]})

        # CREATED: intentionally NOT auto-derived from tool results.
        # write_text is frequently an intermediate step inside a multi-file stage;
        # deriving a CREATED constraint from a single write result would cause the
        # VerificationEngine to return VERIFIED prematurely (after only the first
        # write), breaking stages that need to create two or more files.
        # CREATED constraints must be provided explicitly by the planner so the
        # full set of expected files is known upfront.

        return derived
