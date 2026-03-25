from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict

from core.contracts import PromptContext
from core.engines.summary import SummaryEngine
from core.file_stage_policy import FileStagePolicy
from memory.documents import extract_document_reference_labels
from tools.registry import render_stage_guide


class PromptBuilder:
    """Constructs prompts for planner, inspector, and persona phases."""

    _PLANNER_SCRATCHPAD_MAX_CHARS = 6000
    _PLANNER_FILE_READ_SCRATCHPAD_MAX_CHARS = 14000
    _INSPECTOR_SCRATCHPAD_MAX_CHARS = 8000
    _PLANNER_EXACT_READ_CODE_MAX_CHARS = 5000
    _PLANNER_EXACT_READ_TEXT_MAX_CHARS = 2500
    _PLANNER_EXACT_READ_TOTAL_MAX_CHARS = 8000
    _PLANNER_EXACT_READ_PATTERN = re.compile(
        r"FILE_READ_EXACT_PATH:\s*(?P<path>[^\n]+)\nFILE_READ_EXACT_CONTENT:\n"
        r"(?P<content>.*?)(?=\nFILE_READ_EXACT_PATH:|\n=== STAGE |\Z)",
        re.DOTALL,
    )
    _PLANNER_EXACT_READ_TRUNCATION_NOTE = (
        "\n[TRUNCATED FOR PLANNER BUDGET. FULL EXACT CONTENT REMAINS IN RUNTIME STATE. "
        "USE RUN_CODE IF YOU NEED THE WHOLE FILE.]\n"
    )

    # _truncate_scratchpad → moved to SummaryEngine.truncate_scratchpad
    # _scratchpad_exact_read_paths → removed; use FileWorkEngine.exact_read_paths_from_scratchpad

    @staticmethod
    def _format_memory_age_label(metadata: Dict[str, Any]) -> str:
        date_str = str((metadata or {}).get("date") or "").strip()
        if not date_str:
            return "memory with unknown age"
        try:
            mem_date = datetime.datetime.strptime(date_str, "%b %d, %Y").date()
        except ValueError:
            return "memory with unknown age"

        age_days = max((datetime.date.today() - mem_date).days, 0)
        if age_days == 0:
            return "memory from today"
        if age_days == 1:
            return "memory from 1 day ago"
        return f"memory from {age_days} days ago"

    @staticmethod
    def _append_unique(items: list[str], seen: set[str], value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if text in seen:
            return
        seen.add(text)
        items.append(text)

    @staticmethod
    def _render_active_skill_guide(stage: Dict[str, Any]) -> str:
        skill = stage.get("skill") or {}
        if not isinstance(skill, dict):
            return ""
        name = str(skill.get("name") or "").strip()
        procedure = [str(item).strip() for item in (skill.get("procedure") or []) if str(item).strip()]
        planner_hint = str(skill.get("planner_hint") or "").strip()
        if not name and not procedure and not planner_hint:
            return ""
        lines = ["### ACTIVE_SKILL", ""]
        if name:
            lines.append(f"- Name: {name}")
        if procedure:
            lines.append("- Procedure:")
            for step in procedure:
                lines.append(f"  - {step}")
        if planner_hint:
            lines.append(f"- Guidance: {planner_hint}")
        return "\n".join(lines)

    @staticmethod
    def _render_planner_boundary_block(stage: Dict[str, Any], planner_input: Any | None = None) -> str:
        objective = str(
            getattr(planner_input, "objective", None)
            if planner_input is not None
            else stage.get("objective", "")
            or stage.get("objective", "")
            or ""
        ).strip()
        stage_goal = str(
            getattr(planner_input, "stage_goal", None)
            if planner_input is not None
            else stage.get("stage_goal", "")
            or stage.get("stage_goal", "")
            or ""
        ).strip()
        stage_type = str(
            getattr(planner_input, "stage_type", None)
            if planner_input is not None
            else stage.get("stage_type", "")
            or stage.get("stage_type", "")
            or ""
        ).strip()
        success_condition = str(
            getattr(planner_input, "success_condition", None)
            if planner_input is not None
            else stage.get("success_condition", "")
            or stage.get("success_condition", "")
            or ""
        ).strip()
        allowed_tools = list(
            getattr(planner_input, "allowed_tools", None)
            if planner_input is not None
            else stage.get("allowed_tools", [])
            or stage.get("allowed_tools", [])
            or []
        )
        active_targets = list(
            getattr(planner_input, "active_targets", None)
            if planner_input is not None
            else stage.get("active_targets", [])
            or stage.get("active_targets", [])
            or []
        )
        evidence_required = str(
            getattr(planner_input, "evidence_required", None)
            if planner_input is not None
            else stage.get("evidence_required", "")
            or stage.get("evidence_required", "")
            or ""
        ).strip()
        lines = ["[PLANNER_BOUNDARY]"]
        lines.append(f"objective: {objective or '(none)'}")
        lines.append(f"stage_goal: {stage_goal or '(missing)'}")
        lines.append(f"success_condition: {success_condition or '(missing)'}")
        lines.append(f"stage_type: {stage_type or '(missing)'}")
        lines.append("allowed_tools: " + (" | ".join(str(tool).strip() for tool in allowed_tools if str(tool).strip()) or "(none)"))
        lines.append("active_targets: " + (" | ".join(str(target).strip() for target in active_targets if str(target).strip()) or "(none identified)"))
        lines.append(f"evidence_required: {evidence_required or '(none)'}")
        lines.append("Treat this block as the authoritative normalized planner contract for the current stage.")
        return "\n".join(lines)

    @classmethod
    def _truncate_exact_read_for_planner(cls, content: str, *, limit: int) -> str:
        text = str(content or "")
        if len(text) <= limit:
            return text
        note = cls._PLANNER_EXACT_READ_TRUNCATION_NOTE
        if limit <= len(note) + 80:
            return text[: max(limit, 0)]
        remaining = limit - len(note)
        head = max(40, remaining // 2)
        tail = max(40, remaining - head)
        if head + tail >= len(text):
            return text
        return text[:head] + note + text[-tail:]

    @classmethod
    def _compact_exact_read_blocks_for_planner(cls, scratchpad_text: str) -> str:
        text = str(scratchpad_text or "")
        if "FILE_READ_EXACT_CONTENT:" not in text:
            return text

        remaining_total = cls._PLANNER_EXACT_READ_TOTAL_MAX_CHARS

        def _replace(match: re.Match[str]) -> str:
            nonlocal remaining_total

            path = str(match.group("path") or "").strip()
            content = str(match.group("content") or "").strip("\n")
            per_file_limit = (
                cls._PLANNER_EXACT_READ_CODE_MAX_CHARS
                if FileStagePolicy.paths_are_code_files([path])
                else cls._PLANNER_EXACT_READ_TEXT_MAX_CHARS
            )
            allowed = max(0, min(per_file_limit, remaining_total))
            if allowed <= 0:
                rendered = "[OMITTED FROM PLANNER BUDGET. FULL EXACT CONTENT REMAINS IN RUNTIME STATE.]"
            else:
                rendered = cls._truncate_exact_read_for_planner(content, limit=allowed)
                remaining_total = max(0, remaining_total - len(rendered))
            return f"FILE_READ_EXACT_PATH: {path}\nFILE_READ_EXACT_CONTENT:\n{rendered}"

        return cls._PLANNER_EXACT_READ_PATTERN.sub(_replace, text)

    @staticmethod
    def build_planner_prompt(
        base_template: str,
        stage: Dict,
        scratchpad_text: str,
        step_count: int,
        *,
        planner_input: Any | None = None,
    ) -> str:
        stage_card_text = json.dumps(stage, indent=2)
        scratchpad_limit = PromptBuilder._PLANNER_SCRATCHPAD_MAX_CHARS
        if (
            FileStagePolicy.stage_is_file_work(stage)
            and (
                FileStagePolicy.stage_requires_targeted_read(stage)
                or FileStagePolicy.stage_is_content_edit_stage(stage)
                or FileStagePolicy.is_file_inspection_stage(stage)
            )
        ):
            scratchpad_limit = PromptBuilder._PLANNER_FILE_READ_SCRATCHPAD_MAX_CHARS
        scratchpad_text = PromptBuilder._compact_exact_read_blocks_for_planner(scratchpad_text)
        scratchpad_text = SummaryEngine.truncate_scratchpad(
            scratchpad_text,
            limit=scratchpad_limit,
        )

        prompt = base_template
        prompt = prompt.replace("[STEP]", str(step_count))
        prompt = prompt.replace("[STAGE_CARD]", stage_card_text)
        prompt = prompt.replace(
            "[PLANNER_BOUNDARY]",
            PromptBuilder._render_planner_boundary_block(stage, planner_input=planner_input),
        )
        prompt = prompt.replace("[SCRATCHPAD]", scratchpad_text)
        tool_guide = render_stage_guide(
            stage.get("stage_type", "UNKNOWN"),
            stage.get("allowed_tools", []),
        )
        from core.engines.file_work import FileWorkEngine  # avoid circular import at module level
        exact_read_paths = FileWorkEngine.exact_read_paths_from_scratchpad([scratchpad_text])
        if (
            FileStagePolicy.stage_is_content_edit_stage(stage)
            and FileStagePolicy.paths_are_code_files(exact_read_paths)
        ):
            tool_guide = (
                tool_guide
                + "\n\n### CODE_EDIT_OVERRIDE\n\n"
                + "- This stage edits an existing code file.\n"
                + "- After inspection, prefer RUN_CODE to read-modify-write the file.\n"
                + "- Do not embed a full source file inside FILE_OP write_text JSON.\n"
                + "- For localized code repairs, prefer minimal line-level or token-level edits to rebuilding the whole file from scratch.\n"
                + "- If you rewrite a file in RUN_CODE, preserve real newlines and indentation; do not collapse the source into a one-line triple-quoted blob.\n"
                + "- If you already computed the exact final source and can emit valid JSON, one FILE_OP write_text is an acceptable fallback.\n"
                + "- Use FILE_OP for reads and simple path operations, but prefer RUN_CODE for substantive code rewrites.\n"
            )
        if FileStagePolicy.stage_is_content_edit_stage(stage) and exact_read_paths:
            tool_guide = (
                tool_guide
                + "\n\n### EXACT_READ_READY\n\n"
                + "- Exact current file contents are already present in the scratchpad for: "
                + ", ".join(exact_read_paths[:4])
                + ".\n"
                + "- Do not repeat read_text/read_many on the same unchanged file just because an observation preview looked truncated.\n"
                + "- Use the exact-read block in the scratchpad as the authoritative current source.\n"
            )
        skill_guide = PromptBuilder._render_active_skill_guide(stage)
        if skill_guide:
            tool_guide = tool_guide + "\n\n" + skill_guide
        prompt = prompt.replace("[TOOL_GUIDE]", tool_guide)
        return prompt

    @staticmethod
    def build_inspector_prompt(base_template: str, stage: Dict, scratchpad_text: str) -> str:
        stage_card_text = json.dumps(stage, indent=2)
        scratchpad_text = SummaryEngine.truncate_scratchpad(
            scratchpad_text,
            limit=PromptBuilder._INSPECTOR_SCRATCHPAD_MAX_CHARS,
        )

        prompt = base_template
        prompt = prompt.replace("[STAGE_CARD]", stage_card_text)
        prompt = prompt.replace("[SCRATCHPAD]", scratchpad_text)
        return prompt

    @staticmethod
    def build_persona_prompt(context: PromptContext) -> str:
        parts = []
        instruction_text = str(context.instructions or "")

        if instruction_text:
            parts.append(instruction_text)
        if context.style_overlay:
            parts.append(context.style_overlay)

        # [ENVIRONMENT] (Today / Weather / System load) appears BEFORE world_state
        # so the model reads the current date before it encounters the Birthday
        # field in [WORLD STATE].  This prevents the birthday from biasing the
        # model when it is asked "what's today's date?".
        if context.env_block:
            parts.append(context.env_block)

        if context.world_state:
            parts.append(context.world_state)

        if context.situational_state:
            parts.append(context.situational_state)

        if context.intent_state:
            parts.append(context.intent_state)

        if context.operational_state:
            parts.append(context.operational_state)

        has_relevance_block = bool(
            re.search(r"(?im)^\s*(?:##\s*)?\[?RELEVANCE DISCIPLINE\]?\s*$", instruction_text)
            or "[RELEVANCE DISCIPLINE]" in instruction_text
            or "## RELEVANCE DISCIPLINE" in instruction_text
        )

        if any(
            (
                context.world_state,
                context.situational_state,
                context.intent_state,
                context.operational_state,
                context.brain_hits,
            )
        ) and not has_relevance_block:
            parts.append(
                "[RELEVANCE DISCIPLINE]\n"
                "Use only the most directly relevant contextual fact by default.\n"
                "Do not pile multiple unrelated profile facts, memories, and future plans into one reply.\n"
                "Mention upcoming events only when they are directly relevant to the current turn or runtime outcome.\n"
                "If a detail feels like garnish rather than the point, leave it out.\n"
                "Do not exaggerate recalled facts just to make the tone sharper."
            )

        if context.vision_notes:
            vision_lines = [
                "[VISION SESSION NOTES]",
                "These are ephemeral recent visual commentary notes from active vision mode. They are not durable memory.",
            ]
            for note in context.vision_notes:
                if str(note or "").strip():
                    vision_lines.append(f"- {str(note).strip()}")
            parts.append("\n".join(vision_lines))

        if context.brain_hits:
            brain_lines = ["[RETRIEVED MEMORY]"]
            for hit in context.brain_hits:
                text = hit.get("text", "")
                meta = hit.get("metadata", {})
                age_label = PromptBuilder._format_memory_age_label(meta)
                brain_lines.append(f"- {text} [{age_label}]")
            parts.append("\n".join(brain_lines))

        _focus_text = str(context.document_focus or "").strip()
        if _focus_text:
            focus_lines = ["[DOCUMENT FOCUS]"]
            if context.document_sources:
                focus_lines.append("Sources: " + ", ".join(str(item) for item in context.document_sources))
            if context.document_references:
                focus_lines.append("References: " + " | ".join(str(item) for item in context.document_references))
            focus_lines.append(_focus_text)
            parts.append("\n".join(line for line in focus_lines if line))

        if context.document_hits and not _focus_text:
            document_lines = ["[DOCUMENT MATCHES]"]
            grouped_matches: Dict[str, list[str]] = {}
            grouped_seen: Dict[str, set[str]] = {}
            for hit in context.document_hits:
                meta = hit.get("metadata", {}) or {}
                name = str(meta.get("name") or meta.get("source_path") or "document")
                refs = grouped_matches.setdefault(name, [])
                seen_refs = grouped_seen.setdefault(name, set())
                page_number = meta.get("page_number")
                if page_number:
                    PromptBuilder._append_unique(refs, seen_refs, f"Page {page_number}")
                section_label = str(meta.get("section_label") or "").strip()
                if section_label:
                    PromptBuilder._append_unique(refs, seen_refs, f"Section {section_label}")
                if not refs:
                    for ref in extract_document_reference_labels(str(hit.get("content") or ""), limit=3):
                        PromptBuilder._append_unique(refs, seen_refs, ref)
            # Only emit block if at least one named document was found.
            if grouped_matches:
                for name, refs in grouped_matches.items():
                    document_lines.append(f"- {name}")
                    if refs:
                        document_lines.append("  refs: " + " | ".join(refs[:6]))
                document_lines.append(
                    "Use these matches only as a hint that relevant ingested material exists; do not quote or paraphrase raw document text unless [DOCUMENT FOCUS] is present."
                )
                parts.append("\n".join(document_lines))

        return "\n\n".join(parts)
