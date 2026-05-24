from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from config import CFG
from core.contracts import FileCheckDecision, StageCard
from core.services.file_work import FileWorkEngine
from core.file_stage_policy import FileStagePolicy
from core.file_checker_rules import LocalFileOpRuleChecker
from core.json_utils import parse_json_response
from core.runtime_control import CancellationToken, OperationCancelled


class FileWorkChecker:
    def __init__(self, llm_client, ui_queue, agent_brain, cancel_token: CancellationToken | None = None) -> None:
        self.llm = llm_client
        self.ui = ui_queue
        self.brain = agent_brain
        self.cancel_token = cancel_token

    # _candidate_paths_from_evidence → FileWorkEngine.candidate_paths()

    def verify_current_file_stage_state(self, stage: StageCard, tool_result: Any | None = None) -> FileCheckDecision | None:
        if not FileStagePolicy.stage_requires_file_verification(stage):
            return None

        workspace = getattr(self.brain, "workspace", None)
        if not workspace:
            return None
        current_checker = LocalFileOpRuleChecker(
            Path(workspace),
            stage,
            preferred_paths=FileWorkEngine.candidate_paths(tool_result),
        )
        current_decision = current_checker.evaluate_current_stage_state()
        if current_decision is not None:
            return current_decision

        absence_decision = self._verify_lookup_absence_confirmation(stage, tool_result)
        if absence_decision is not None:
            return absence_decision

        current_read = self._build_current_state_read_result(stage, tool_result)
        if current_read is not None:
            current_read_decision = self.run_file_checker(stage, current_read)
            if str(current_read_decision.get("verdict", "")).upper() in {"VERIFIED", "PARTIAL"}:
                return current_read_decision

        if not FileStagePolicy.stage_is_extension_file_reorg(stage) and not FileStagePolicy.stage_is_broad_file_reorg(stage):
            return None

        runtime = getattr(self.brain, "workspace_runtime", None)
        inventory_builder = getattr(runtime, "build_extension_inventory", None)
        if inventory_builder is None:
            inventory_builder = getattr(self.brain, "_build_extension_inventory", None)
        if inventory_builder is None:
            return None

        workspace_root = Path(workspace).resolve()
        requested_root = FileStagePolicy.file_op_root(tool_result) or FileStagePolicy.stage_scope_root(stage) or "."
        root_path = workspace_root if requested_root in {"", "."} else (workspace_root / requested_root).resolve()
        try:
            root_path.relative_to(workspace_root)
        except ValueError:
            root_path = workspace_root
            requested_root = "."
        if not root_path.exists() or not root_path.is_dir():
            root_path = workspace_root
            requested_root = "."

        inventory = inventory_builder(
            root_path,
            workspace_root,
            extensions=None,
        )
        stage_text = FileStagePolicy.stage_file_text(stage)
        if re.search(r"\b(delete empty|remove empty|no empty folders remain|empty directories)\b", stage_text):
            synthetic = {
                "tool": "FILE_OP",
                "action": "delete_empty_dirs",
                "requested_root": requested_root,
            }
        else:
            synthetic = {
                "tool": "FILE_OP",
                "action": "consolidate_by_extension",
                "requested_root": requested_root,
                "destinations": inventory.get("destination_hints", {}),
            }
            # Carry exclusion info from the original tool_result so files that were
            # intentionally skipped (e.g. exclude_files: ["keep*"]) are not flagged
            # as off-target when the rule checker re-evaluates current state.
            if isinstance(tool_result, dict):
                if tool_result.get("excluded_names"):
                    synthetic["excluded_names"] = tool_result["excluded_names"]
                if tool_result.get("excluded_prefixes"):
                    synthetic["excluded_prefixes"] = tool_result["excluded_prefixes"]
        return self.run_local_file_op_checker(stage, synthetic)

    @staticmethod
    def _verify_lookup_absence_confirmation(stage: StageCard, tool_result: Any | None) -> FileCheckDecision | None:
        if not isinstance(tool_result, dict):
            return None
        if str(tool_result.get("tool", "")).upper() != "FILE_OP":
            return None
        if str(tool_result.get("action", "")).lower() != "find_paths":
            return None
        if not FileStagePolicy.stage_allows_absence_confirmation(stage):
            return None
        if not FileStagePolicy.stage_requires_targeted_lookup(stage):
            return None

        try:
            match_count = int(tool_result.get("match_count", len(tool_result.get("matches") or [])) or 0)
        except (TypeError, ValueError):
            return None
        if match_count != 0:
            return None

        query = str(tool_result.get("requested_query") or "").strip()
        if not FileStagePolicy._query_matches_stage_targets(query, stage):
            return None

        return {
            "verdict": "VERIFIED",
            "reason": f"No plausible file match was found for '{query}', so the absence-based success condition is satisfied.",
            "evidence_files": [],
        }

    def build_current_file_stage_read_result(self, stage: StageCard, tool_result: Any | None = None) -> dict[str, Any] | None:
        if not FileStagePolicy.stage_is_file_work(stage):
            return None
        return self._build_current_state_read_result(stage, tool_result)

    def run_local_file_op_checker(self, stage: StageCard, tool_result: Any) -> FileCheckDecision | None:
        workspace = getattr(self.brain, "workspace", None)
        if not workspace:
            return None
        return LocalFileOpRuleChecker(Path(workspace), stage).evaluate(tool_result)

    def _build_current_state_read_result(self, stage: StageCard, tool_result: Any | None = None) -> dict[str, Any] | None:
        workspace = getattr(self.brain, "workspace", None)
        if not workspace:
            return None
        workspace_root = Path(workspace).resolve()
        preferred = FileWorkEngine.candidate_paths(tool_result)
        candidates = preferred + FileStagePolicy.stage_named_file_targets(stage)
        files: dict[str, str] = {}
        evidence_files: list[str] = []
        total_chars = 0
        for candidate in candidates:
            resolved = self._resolve_workspace_file(workspace_root, candidate)
            if resolved is None or not resolved.is_file():
                continue
            rel_path = resolved.relative_to(workspace_root).as_posix()
            if rel_path in files:
                continue
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if total_chars and total_chars + len(content) > 20000:
                break
            files[rel_path] = content
            evidence_files.append(rel_path)
            total_chars += len(content)
            if len(files) >= 2:
                break
        if not files:
            return None
        if len(files) == 1:
            rel_path, content = next(iter(files.items()))
            return {
                "tool": "FILE_OP",
                "status": "EXECUTED",
                "summary": f"Current file state read for verification: {rel_path}",
                "action": "read_text",
                "requested_path": rel_path,
                "path": rel_path,
                "files": {rel_path: content},
                "evidence_files": [rel_path],
                "current_state_only": True,
            }
        return {
            "tool": "FILE_OP",
            "status": "EXECUTED",
            "summary": f"Current file state read for verification: {', '.join(evidence_files)}",
            "action": "read_many",
            "requested_paths": evidence_files,
            "files": files,
            "evidence_files": evidence_files,
            "current_state_only": True,
        }

    @staticmethod
    def _resolve_workspace_file(workspace_root: Path, raw_path: str) -> Path | None:
        candidate = str(raw_path or "").strip().replace("\\", "/")
        if not candidate:
            return None

        resolved: Path | None = None
        windows_match = re.match(r"^([A-Za-z]):/(.*)$", candidate)
        if windows_match and os.name != "nt":
            drive = windows_match.group(1).lower()
            suffix = windows_match.group(2)
            resolved = Path(f"/mnt/{drive}/{suffix}")
        elif os.name == "nt" and candidate.startswith("/mnt/") and len(candidate) > 6:
            drive = candidate[5].upper()
            suffix = candidate[7:].replace("/", "\\")
            resolved = Path(f"{drive}:\\{suffix}")
        elif Path(candidate).is_absolute():
            resolved = Path(candidate)
        else:
            resolved = (workspace_root / candidate).resolve()

        try:
            canonical = Path(os.path.normcase(os.path.realpath(resolved)))
            canonical.relative_to(Path(os.path.normcase(os.path.realpath(workspace_root))))
        except Exception:
            return None
        return canonical

    def run_file_checker(self, stage: StageCard, tool_result: Any) -> FileCheckDecision:
        if not isinstance(tool_result, dict):
            return {
                "verdict": "FAILED",
                "reason": "File tool did not return structured evidence.",
                "evidence_files": [],
            }

        local_decision = self.run_local_file_op_checker(stage, tool_result)
        if local_decision is not None:
            return local_decision

        status = str(tool_result.get("status", "")).upper()
        if status in {"FAILED", "BLOCKED"}:
            return {
                "verdict": "FAILED",
                "reason": str(tool_result.get("summary", "File tool execution failed.")),
                "evidence_files": list(tool_result.get("evidence_files") or []),
            }

        prompt_path = CFG.DATA_DIR / "prompts" / "file_checker.txt"
        template = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
        if not template:
            return {
                "verdict": "PARTIAL",
                "reason": "FILE_CHECKER prompt missing; cannot verify file state fully.",
                "evidence_files": list(tool_result.get("evidence_files") or []),
            }

        stage_card_text = json.dumps(stage, indent=2, ensure_ascii=False)
        evidence_text = json.dumps(tool_result, indent=2, ensure_ascii=False)
        sys_prompt = template.replace("[STAGE_CARD]", stage_card_text).replace("[FILE_EVIDENCE]", evidence_text)
        messages = [
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": (
                    "Return the file checker JSON for the latest current file-state evidence."
                    if bool(tool_result.get("current_state_only"))
                    else "Return the file checker JSON for the latest RUN_CODE evidence."
                ),
            },
        ]

        try:
            if self.cancel_token is not None:
                self.cancel_token.raise_if_cancelled()
            raw = self.llm.generate(
                messages,
                temperature=0.0,
                max_tokens=int(getattr(CFG, "FILE_CHECKER_MAX_TOKENS", 220)),
                cancel_token=self.cancel_token,
            )
            if self.cancel_token is not None:
                self.cancel_token.raise_if_cancelled()
            self.ui.put(("agent_log", f"[FILE_CHECKER] {raw.strip()}"))
            parsed = parse_json_response(raw)
            verdict = str(parsed.get("verdict", "FAILED")).upper()
            if verdict not in {"VERIFIED", "PARTIAL", "FAILED"}:
                verdict = "FAILED"
            return {
                "verdict": verdict,
                "reason": str(parsed.get("reason", "")).strip() or "No checker reason provided.",
                "evidence_files": [str(item) for item in (parsed.get("evidence_files") or tool_result.get("evidence_files") or [])],
            }
        except OperationCancelled:
            raise
        except Exception as e:
            self.ui.put(("agent_log", f"[FILE_CHECKER] ERROR: {e}"))
            return {
                "verdict": "FAILED",
                "reason": f"FILE_CHECKER failed: {e}",
                "evidence_files": list(tool_result.get("evidence_files") or []),
            }
