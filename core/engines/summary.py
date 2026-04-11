"""core/engines/summary.py

SummaryEngine — single owner of scratchpad-level extraction and carry-forward compression.

Responsibilities:
  - scratchpad slicing (latest stage entries)
  - stage evidence extraction (verified result, proposal, exact file read, file lookup)
  - outcome status and runtime-note building (carry-forward for [LATEST_RUNTIME_CONTEXT])
  - outcome block construction (OUTCOME entry + [INSTRUCTION] directive)
  - outcome detail selection and observation detail extraction
  - generic file-work summary detection (single definition, no duplication)
  - text utilities (sanitize_note, truncate_scratchpad, truncate_text)

What this engine does NOT own:
  - persona pack assembly                (ContextPackEngine)
  - directive / tail-block selection     (ContextPackEngine.build_persona_directive_pack)
  - runtime context message rendering    (ContextPackRenderer)
  - planner / inspector prompt building  (PromptBuilder)
  - stage / step formatting              (ScratchpadFormatter)
  - observation field filtering          (ScratchpadFormatter._stringify_observation)
  - LLM calls                            (none)
  - file read / write                    (none)

Extracted from:
  - core/engines/context_pack.py  (12 methods)
  - core/scratchpad_formatter.py  (4 methods; _is_generic_file_work_summary deduped)
  - core/prompt_builder.py        (2 utilities)
"""

from __future__ import annotations

import json
import re


_GENERIC_FILE_WORK_PREFIXES = (
    "execution succeeded",
    "wrote text file",
    "wrote json file",
    "updated json file",
    "read text file",
    "read files",
    "queued workspace script",
    "found ",
    "listed ",
)

_FILE_READ_EXACT_PATTERN = re.compile(
    r"FILE_READ_EXACT_PATH:\s*(?P<path>[^\n]+)\nFILE_READ_EXACT_CONTENT:\n"
    r"(?P<content>.*?)(?=\nFILE_READ_EXACT_PATH:|\n=== STAGE|\Z)",
    re.DOTALL,
)
_STAGE_OUTCOME_HEADER_RE = re.compile(
    r"^\s*=== STAGE \d+ OUTCOME ===(?:\n|$)",
    re.IGNORECASE,
)


class SummaryEngine:
    """Single owner of scratchpad-level extraction and carry-forward compression.

    All public methods are static or class methods — the engine carries no
    instance state and is safe to use without instantiation.
    """

    # ------------------------------------------------------------------ #
    # A. Scratchpad slicing                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def latest_stage_entries(scratchpad: list[str]) -> list[str]:
        """Return only the entries from the latest stage header to the end.

        Falls back to the last 6 entries when no ``=== STAGE N START ===``
        header is found.

        Extracted from ContextPackEngine.latest_stage_entries.
        """
        entries = list(scratchpad or [])
        latest_start = -1
        for idx in range(len(entries) - 1, -1, -1):
            if "=== STAGE " in entries[idx] and " START ===" in entries[idx]:
                latest_start = idx
                break
        if latest_start < 0:
            return [str(entry or "") for entry in entries[-6:]]
        return [str(entry or "") for entry in entries[latest_start:]]

    @staticmethod
    def _is_stage_outcome_entry(entry: str) -> bool:
        return bool(_STAGE_OUTCOME_HEADER_RE.match(str(entry or "")))

    # ------------------------------------------------------------------ #
    # B. Stage evidence extraction                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def extract_verified_result(cls, scratchpad: list[str]) -> str:
        """Return a human-readable sentence describing the verified file-work result.

        Parses the last ``FILE_WORK_VERIFIED_RESULT:`` JSON entry in the latest
        stage and formats it according to action type and content.  Returns ``""``
        when no verified result is found.

        Extracted from ContextPackEngine.extract_verified_file_work_answer.
        """
        entries = [
            str(entry or "")
            for entry in cls.latest_stage_entries(scratchpad)
            if str(entry or "").lstrip().startswith("FILE_WORK_VERIFIED_RESULT:")
        ]
        if not entries:
            return ""
        _, _, payload = entries[-1].partition("FILE_WORK_VERIFIED_RESULT:")
        try:
            data = json.loads(payload.strip())
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""

        kind = str(data.get("kind", "")).strip().lower()
        action = str(data.get("action", "")).strip().lower()
        summary = str(data.get("summary", "")).strip().rstrip(".")
        reason = str(data.get("reason", "")).strip()
        paths = [str(item).strip() for item in (data.get("paths") or []) if str(item).strip()]
        label = paths[0] if len(paths) == 1 else ", ".join(paths[:3])
        operation_label = str(data.get("operation_label") or "").strip().lower()

        if kind == "state_already_satisfied":
            if label:
                return f"The requested file state is already satisfied in {label}."
            if reason:
                return reason
            return "The requested file state is already satisfied."

        if action in {"write_text", "append_text", "write_json", "update_json"} and label:
            verb = operation_label.capitalize() if operation_label in {"created", "updated"} else "Updated"
            return f"{verb} {label} and verified the file change."
        if action in {"delete_path", "delete_many"} and label:
            return f"Removed {label} and verified the file change."
        if action in {"move_path", "move_many", "copy_path", "copy_many"} and summary:
            return summary + "."
        if reason and cls.is_generic_file_work_summary(summary):
            return reason
        if summary:
            return summary + "."
        if label:
            return f"Updated {label} and verified the file change."
        if reason:
            return reason
        return ""

    @classmethod
    def extract_verified_browser_answer(cls, scratchpad: list[str]) -> str:
        """Return a natural-language answer from the latest verified browser result."""
        data = cls._extract_latest_verified_browser_payload(scratchpad)
        if not data:
            return ""
        return cls._format_verified_browser_answer(data)

    @classmethod
    def extract_proposal(cls, scratchpad: list[str]) -> str:
        """Return the text of the last ``PROPOSAL:`` marker in the latest stage.

        Returns ``""`` when no proposal is found.

        Extracted from ContextPackEngine.extract_latest_stage_proposal_answer.
        """
        for entry in reversed(cls.latest_stage_entries(scratchpad)):
            text = str(entry or "")
            marker = "PROPOSAL:"
            if marker not in text:
                continue
            _, _, payload = text.partition(marker)
            proposal = payload.strip()
            if proposal:
                return proposal
        return ""

    @classmethod
    def extract_exact_file_read(cls, scratchpad: list[str]) -> str:
        """Return the content from ``FILE_READ_EXACT_PATH/CONTENT`` blocks in the latest stage.

        Single-file reads return the content directly.  Multi-file reads return
        ``path:\\ncontent`` sections joined by blank lines.  Returns ``""`` when
        no exact-read entries are found.

        Extracted from ContextPackEngine.extract_exact_file_read_answer.
        """
        blobs = [
            str(entry or "")
            for entry in cls.latest_stage_entries(scratchpad)
            if str(entry or "").lstrip().startswith("FILE_READ_EXACT_PATH:")
        ]
        if not blobs:
            return ""
        blob = "\n\n".join(blobs)
        matches = list(_FILE_READ_EXACT_PATTERN.finditer(blob))
        if not matches:
            return ""
        if len(matches) == 1:
            return matches[0].group("content").strip("\n")
        rendered: list[str] = []
        for match in matches:
            rendered.append(f"{match.group('path').strip()}:\n{match.group('content').strip()}")
        return "\n\n".join(part for part in rendered if part).strip()

    @classmethod
    def extract_file_lookup(cls, scratchpad: list[str]) -> str:
        """Return the file-path lines from the last ``FILE_LOOKUP_MATCHES:`` entry.

        Returns ``""`` when no lookup entry is found; returns
        ``"No matching files found."`` when the entry is present but empty.

        Extracted from ContextPackEngine.extract_file_lookup_answer.
        """
        lookup_entries = [
            str(entry or "")
            for entry in cls.latest_stage_entries(scratchpad)
            if str(entry or "").lstrip().startswith("FILE_LOOKUP_MATCHES:")
        ]
        if not lookup_entries:
            return ""
        last = lookup_entries[-1]
        _, _, payload = last.partition("FILE_LOOKUP_MATCHES:")
        matches = [line.strip() for line in payload.splitlines() if line.strip()]
        if not matches:
            return "No matching files found."
        return "\n".join(matches)

    # ------------------------------------------------------------------ #
    # C. Outcome status and runtime note                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def extract_stage_status(cls, scratchpad: list[str]) -> str:
        """Return the ``RESULT`` value from the latest ``=== STAGE N OUTCOME ===`` entry.

        Returns ``""`` when no outcome entry is found.

        Extracted from ContextPackEngine._extract_latest_stage_status.
        """
        for entry in reversed(cls.latest_stage_entries(scratchpad)):
            text = str(entry or "")
            if not cls._is_stage_outcome_entry(text):
                continue
            upper_entry = text.upper()
            if "RESULT: FILE OPERATION SUCCESS" in upper_entry:
                return "FILE OPERATION SUCCESS"
            if "RESULT: SEARCH COMPLETED" in upper_entry:
                return "SEARCH COMPLETED"
            if "RESULT: FAILED" in upper_entry or "FAILED / INCOMPLETE" in upper_entry:
                return "FAILED / INCOMPLETE"
            if "PAUSED / AWAITING USER INPUT" in upper_entry:
                return "PAUSED / AWAITING USER INPUT"
            if "PAUSED / AWAITING USER APPROVAL" in upper_entry:
                return "PAUSED / AWAITING USER APPROVAL"
            result_match = re.search(r"RESULT:\s*(.+)", text)
            if result_match:
                return str(result_match.group(1) or "").strip()
        return ""

    @classmethod
    def build_runtime_note(cls, scratchpad: list[str]) -> str:
        """Build the carry-forward runtime note for ``[LATEST_RUNTIME_CONTEXT]``.

        Checks (in priority order):
        1. Verified result sentence  (``extract_verified_result``)
        2. Last exact-read path label
        3. File-lookup brief
        4. ``LAST_LOG:`` line from the latest OUTCOME entry, resolving any
           embedded ``FILE_WORK_VERIFIED_RESULT:`` JSON
        5. ``FILE_WORK_VERIFIED_RESULT:`` bare line
        6. ``OBSERVATION_TEXT:`` line

        Returns ``""`` when nothing useful is found.

        Extracted from ContextPackEngine._extract_latest_runtime_note.
        """
        verified = cls.extract_verified_result(scratchpad)
        if verified:
            return cls.sanitize_note(verified)

        verified_browser = cls.extract_verified_browser_answer(scratchpad)
        if verified_browser:
            return cls.sanitize_note(verified_browser)

        exact_path = cls._extract_latest_exact_read_path(scratchpad)
        if exact_path:
            return f"Last direct file read targeted '{exact_path}'."

        lookup = cls._extract_latest_lookup_brief(scratchpad)
        if lookup:
            return cls.sanitize_note(lookup)

        for entry in reversed(cls.latest_stage_entries(scratchpad)):
            text = str(entry or "")
            if not cls._is_stage_outcome_entry(text):
                continue
            match = re.search(r"^LAST_LOG:\s*(.+)$", text, flags=re.MULTILINE)
            if match:
                raw = str(match.group(1) or "").strip()
                if raw.startswith("FILE_WORK_VERIFIED_RESULT:"):
                    _, _, payload = raw.partition("FILE_WORK_VERIFIED_RESULT:")
                    try:
                        data = json.loads(payload.strip())
                    except Exception:
                        return cls.sanitize_note(raw)
                    summary = str(data.get("summary") or data.get("reason") or "").strip()
                    if summary:
                        return cls.sanitize_note(summary)
                return cls.sanitize_note(raw)
        for entry in reversed(cls.latest_stage_entries(scratchpad)):
            match = re.search(r"^FILE_WORK_VERIFIED_RESULT:\s*(.+)$", str(entry or ""), flags=re.MULTILINE)
            if match:
                return cls.sanitize_note(str(match.group(1) or "").strip())
        for entry in reversed(cls.latest_stage_entries(scratchpad)):
            match = re.search(r"^OBSERVATION_TEXT:\s*(.+)$", str(entry or ""), flags=re.MULTILINE)
            if match:
                return cls.sanitize_note(str(match.group(1) or "").strip())
        return ""

    # ------------------------------------------------------------------ #
    # D. Outcome block construction                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def build_outcome_block(
        scratchpad: list[str],
        *,
        escalation_active: bool = False,
        allow_persona_reroute: bool = True,
    ) -> str:
        """Collect all OUTCOME entries and attach the appropriate [INSTRUCTION] directive.

        For multi-stage tasks, all stage outcome entries are included so the
        persona can summarise the complete task rather than only the final stage.
        The [INSTRUCTION] directive is determined by the final (most authoritative)
        outcome entry.

        Returns ``"=== STAGE 1 OUTCOME ===\\n...\\n\\n=== STAGE N OUTCOME ===\\n...\\n\\n[INSTRUCTION]\\n..."``.
        Returns ``""`` when no OUTCOME entry is present.
        """
        all_outcomes = [
            str(entry)
            for entry in (scratchpad or [])
            if SummaryEngine._is_stage_outcome_entry(str(entry))
        ]
        if not all_outcomes:
            return ""

        # Instruction is driven by the final outcome (highest authority).
        last_entry_upper = all_outcomes[-1].upper()
        if "PAUSED / AWAITING USER INPUT" in last_entry_upper:
            instruction = (
                "[INSTRUCTION]\nThe task is paused pending user input. Ask the user for the "
                "missing details described by LAST_LOG, present that clarification request "
                "clearly, and do not claim execution happened or that the requested artifact "
                "is ready."
            )
        elif "PAUSED / AWAITING USER APPROVAL" in last_entry_upper:
            instruction = (
                "[INSTRUCTION]\nThe task is paused pending user approval. Present the proposed "
                "next actions clearly, ask for confirmation, and do not claim execution happened."
            )
        elif "FAILED" in last_entry_upper:
            if escalation_active:
                instruction = (
                    "[INSTRUCTION]\nThe task FAILED and engineering support has been briefed. "
                    "Report the failure honestly. Do NOT append [ROUTER]. "
                    "This turn must end here — let the user decide what to do next."
                )
            elif not allow_persona_reroute:
                instruction = (
                    "[INSTRUCTION]\nThe task FAILED or was incomplete. Inform the user about "
                    "the error honestly. Use LAST_LOG as the authoritative failure cause and "
                    "do not invent a different one. Do not claim success. "
                    "Do not state that any file was moved, copied, renamed, created, or deleted "
                    "unless FILE_CHECKER VERIFIED confirmation appears in the scratchpad — "
                    "tool execution is not the same as verified completion. "
                    "Do NOT append [ROUTER]. This turn must end here — let the user decide what to do next."
                )
            else:
                instruction = (
                    "[INSTRUCTION]\nThe task FAILED or was incomplete. Inform the user about "
                    "the error honestly. Use LAST_LOG as the authoritative failure cause and "
                    "do not invent a different one. Do not claim success. "
                    "Do not state that any file was moved, copied, renamed, created, or deleted "
                    "unless FILE_CHECKER VERIFIED confirmation appears in the scratchpad — "
                    "tool execution is not the same as verified completion. "
                    "If the best next step is to retry through the agent workflow, you may "
                    "append [ROUTER] to trigger a fresh routing pass.\n"
                    "CRITICAL LANGUAGE RULE — if you append [ROUTER]: your message MUST open "
                    "with a declarative statement such as 'Retrying now.' or 'Initiating "
                    "another pass.' You MUST NOT use interrogative phrasing ('Shall I', "
                    "'Shall we', 'Should I', 'Would you like me to') anywhere in the same "
                    "message. [ROUTER] executes the retry immediately — asking for permission "
                    "first is contradictory and will confuse the user."
                )
        else:
            if len(all_outcomes) > 1:
                instruction = (
                    "[INSTRUCTION]\nAll stages of the task are complete. Summarise the full "
                    "outcome across all stages naturally. Each stage outcome above contains "
                    "LAST_LOG evidence — use all of them as the authoritative record of what "
                    "happened. Do not only describe the final stage; cover the whole task."
                )
            else:
                instruction = (
                    "[INSTRUCTION]\nThe task is complete. Inform the user naturally. Use LAST_LOG "
                    "as the authoritative completion evidence. If LAST_LOG says the target was "
                    "already absent, already present, or already satisfied, describe that current "
                    "state honestly instead of implying a fresh mutation happened in this turn."
                )
        combined = "\n\n".join(all_outcomes)
        return f"{combined}\n\n{instruction}"

    # ------------------------------------------------------------------ #
    # E. Outcome detail selection and observation detail extraction       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def select_outcome_detail(
        stage_type: str,
        stage_entries: list[str] | None,
        fallback: str,
    ) -> str:
        """Select the most meaningful detail string for a stage outcome.

        Priority for FILE_WORK stages:
          FILE_WORK_VERIFIED_RESULT → FILE_CHECKER_VERDICT → FILE_LOOKUP_MATCHES
          → (block sentinel) → PROPOSAL → FILE_READ_EXACT_PATH → fallback

        For other stages: PROPOSAL → fallback.

        Extracted from ScratchpadFormatter._select_outcome_detail.
        """
        entries = [str(entry or "") for entry in (stage_entries or []) if str(entry or "").strip()]
        if not entries:
            return fallback

        if str(stage_type or "").upper() == "FILE_WORK":
            for prefix in (
                "FILE_WORK_VERIFIED_RESULT:",
                "FILE_CHECKER_VERDICT:",
                "FILE_LOOKUP_MATCHES:",
                "SYSTEM ERROR: FILE_WORK cannot complete until FILE_CHECKER_VERDICT is VERIFIED.",
            ):
                for entry in reversed(entries):
                    if entry.lstrip().startswith(prefix):
                        return entry

        if str(stage_type or "").upper() == "COMPUTER_USE":
            for entry in reversed(entries):
                if entry.lstrip().startswith("COMPUTER_USE_VERIFIED_RESULT:"):
                    return entry
            for entry in reversed(entries):
                if '"tool": "BROWSER_OP"' in entry or '"tool":"BROWSER_OP"' in entry:
                    return entry

        for entry in reversed(entries):
            if "PROPOSAL:" in entry:
                return entry

        if str(stage_type or "").upper() == "FILE_WORK":
            for entry in reversed(entries):
                if entry.lstrip().startswith("FILE_READ_EXACT_PATH:"):
                    return entry

        return fallback

    @classmethod
    def extract_observation_detail(cls, last_observation: str) -> str:
        """Peel the concise detail string from a raw observation entry.

        Handles:
        - ``FILE_WORK_VERIFIED_RESULT:`` JSON payload (summary → reason → paths)
        - ``OBSERVATION_TEXT:`` prefix (tail of last 300 chars)
        - bare last 200 chars of the string

        Returns ``""`` for empty input.

        Extracted from ScratchpadFormatter._extract_observation_detail.
        """
        if not last_observation:
            return ""
        if last_observation.startswith("FILE_WORK_VERIFIED_RESULT:"):
            _, _, payload = last_observation.partition("FILE_WORK_VERIFIED_RESULT:")
            try:
                data = json.loads(payload.strip())
            except Exception:
                data = {}
            if isinstance(data, dict):
                summary = str(data.get("summary") or "").strip()
                reason = str(data.get("reason") or "").strip()
                paths = [str(item).strip() for item in (data.get("paths") or []) if str(item).strip()]
                operation_label = str(data.get("operation_label") or "").strip().lower()
                # When the summary is generic ("Wrote text file: …", "Execution succeeded", etc.)
                # prefer the richer reason string from the verifier.  Also build an explicit
                # operation label line (e.g. "Created: keep_me.txt, move_me.txt") when
                # the payload carries path + operation metadata so the persona uses the
                # correct verb rather than guessing from the generic summary.
                if reason and cls.is_generic_file_work_summary(summary):
                    if operation_label in {"created", "updated"} and paths:
                        label_line = f"{operation_label.capitalize()}: {', '.join(paths[:6])}"
                        return f"{label_line}. {reason}" if reason else label_line
                    return reason
                if operation_label in {"created", "updated"} and paths and cls.is_generic_file_work_summary(summary):
                    label_line = f"{operation_label.capitalize()}: {', '.join(paths[:6])}"
                    return label_line
                if summary and reason:
                    return f"{summary}. {reason}"
                if summary:
                    return summary
                if reason:
                    return reason
                if paths:
                    return ", ".join(paths[:3])
        if last_observation.startswith("COMPUTER_USE_VERIFIED_RESULT:"):
            _, _, payload = last_observation.partition("COMPUTER_USE_VERIFIED_RESULT:")
            try:
                data = json.loads(payload.strip())
            except Exception:
                data = {}
            if isinstance(data, dict):
                rendered = cls._format_verified_browser_answer(data)
                if rendered:
                    return rendered
        if '"tool": "BROWSER_OP"' in last_observation or '"tool":"BROWSER_OP"' in last_observation:
            try:
                if "OBSERVATION_TEXT:" in last_observation:
                    _, _, payload = last_observation.partition("OBSERVATION_TEXT:")
                    data = json.loads(payload.strip())
                else:
                    data = json.loads(last_observation.strip())
            except Exception:
                data = {}
            if isinstance(data, dict):
                summary = str(data.get("summary") or "").strip()
                extracted_text = str(data.get("extracted_text") or "").strip()
                title = str(data.get("title") or "").strip()
                field_value = str(data.get("field_value") or "").strip()
                saved_path = str(data.get("saved_path") or "").strip()
                current_url = str(data.get("current_url") or "").strip()
                requested_topic = str(data.get("topic") or "").strip()
                if extracted_text:
                    if requested_topic:
                        return f"Extracted text about {requested_topic}: {extracted_text}"
                    return f"Extracted text: {extracted_text}"
                if field_value:
                    return f"Verified field value: {field_value}"
                if saved_path:
                    return f"Downloaded artifact: {saved_path}"
                if title and current_url:
                    return f"Verified browser page '{title}' at {current_url}"
                if title:
                    return f"Verified browser page title: {title}"
                if summary:
                    return summary
        if "OBSERVATION_TEXT:" in last_observation:
            try:
                parts = last_observation.split("OBSERVATION_TEXT:")
                if len(parts) > 1:
                    return parts[-1].strip()[:300]
            except Exception:
                return ""
        return last_observation[-200:]

    @staticmethod
    def _extract_latest_verified_browser_payload(scratchpad: list[str]) -> dict:
        entries = [
            str(entry or "")
            for entry in SummaryEngine.latest_stage_entries(scratchpad)
            if str(entry or "").lstrip().startswith("COMPUTER_USE_VERIFIED_RESULT:")
        ]
        if not entries:
            return {}
        _, _, payload = entries[-1].partition("COMPUTER_USE_VERIFIED_RESULT:")
        try:
            data = json.loads(payload.strip())
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _format_verified_browser_answer(data: dict[str, object]) -> str:
        summary = str(data.get("summary") or "").strip()
        status_text = str(data.get("status_text") or "").strip()
        extracted_text = str(data.get("extracted_text") or "").strip()
        saved_path = str(data.get("saved_path") or "").strip()
        download_label = str(data.get("download_label") or "").strip()
        download_hint = str(data.get("download_hint") or "").strip()
        reported_title = str(data.get("reported_title") or data.get("title") or "").strip()
        current_url = str(data.get("current_url") or "").strip()
        requested_topic = str(data.get("requested_topic") or "").strip()
        matched_heading = str(data.get("matched_heading") or "").strip()
        extracts = [item for item in (data.get("extracts") or []) if isinstance(item, dict)]
        element_inventory = [item for item in (data.get("element_inventory") or []) if isinstance(item, dict)]

        heading_text = ""
        for item in extracts:
            selector = str(item.get("selector") or "").strip().lower()
            text_value = str(item.get("text") or "").strip()
            topic_value = str(item.get("topic") or "").strip()
            normalized_text = " ".join(text_value.split())
            if (
                selector == "h1"
                and not topic_value
                and normalized_text
                and len(normalized_text) <= 160
                and normalized_text.count(".") <= 1
                and normalized_text.count("?") == 0
                and normalized_text.count("!") == 0
            ):
                heading_text = text_value
                break

        extra_headings: list[str] = []
        link_texts: list[str] = []
        seen_heading_texts: set[str] = set()
        seen_link_texts: set[str] = set()
        for item in element_inventory:
            tag = str(item.get("tag") or "").strip().lower()
            text_value = str(item.get("text") or "").strip()
            if not text_value:
                continue
            normalized = " ".join(text_value.split())
            if tag in {"h2", "h3"}:
                key = normalized.lower()
                if key not in seen_heading_texts:
                    seen_heading_texts.add(key)
                    extra_headings.append(normalized)
            elif tag == "a":
                key = normalized.lower()
                if key not in seen_link_texts:
                    seen_link_texts.add(key)
                    link_texts.append(normalized)

        is_generic_browser_overview = requested_topic.lower() in {
            "general info",
            "general information",
            "overview",
            "summary",
        }
        display_topic = "" if is_generic_browser_overview else requested_topic

        if not extracted_text:
            for item in extracts:
                text_value = str(item.get("text") or "").strip()
                if text_value:
                    extracted_text = text_value
                    break

        if status_text and saved_path:
            return f'The status text is "{status_text}", and the download was saved to `{saved_path}`.'
        if status_text:
            return f'The status text is "{status_text}".'
        if heading_text and saved_path:
            if current_url:
                return f'The main heading at {current_url} is "{heading_text}", and the download was saved to `{saved_path}`.'
            return f'The main heading is "{heading_text}", and the download was saved to `{saved_path}`.'
        if heading_text:
            if current_url:
                return f'The main heading at {current_url} is "{heading_text}".'
            return f'The main heading is "{heading_text}".'
        if extracted_text and saved_path:
            preview_limit = 420 if is_generic_browser_overview else 260
            preview = SummaryEngine.truncate_text(" ".join(extracted_text.split()), preview_limit)
            if is_generic_browser_overview:
                return f"Here is more from the page: {preview} The download was saved to `{saved_path}`."
            if display_topic:
                return (
                    f"Here is the section about '{display_topic}': {preview} "
                    f"The download was saved to `{saved_path}`."
                )
            return f"Here is the requested text: {preview} The download was saved to `{saved_path}`."
        if extracted_text:
            preview_limit = 420 if is_generic_browser_overview else 260
            preview = SummaryEngine.truncate_text(" ".join(extracted_text.split()), preview_limit)
            extra_bits: list[str] = []
            if is_generic_browser_overview and extra_headings:
                extra_bits.append(
                    "Other visible sections include "
                    + ", ".join(f'"{item}"' for item in extra_headings[:3])
                    + "."
                )
            if is_generic_browser_overview and not extra_headings and link_texts:
                extra_bits.append(
                    "Visible links or items include "
                    + ", ".join(f'"{item}"' for item in link_texts[:3])
                    + "."
                )
            if is_generic_browser_overview and current_url:
                if matched_heading:
                    base = f'Here is more from {current_url} under "{matched_heading}": {preview}'
                else:
                    base = f"Here is more from {current_url}: {preview}"
                if extra_bits:
                    return base + " " + " ".join(extra_bits)
                return base
            if display_topic and current_url:
                if matched_heading:
                    base = (
                        f"Here is the section about '{display_topic}' from {current_url} "
                        f"under \"{matched_heading}\": {preview}"
                    )
                    if extra_bits:
                        return base + " " + " ".join(extra_bits)
                    return base
                base = f"Here is the section about '{display_topic}' from {current_url}: {preview}"
                if extra_bits:
                    return base + " " + " ".join(extra_bits)
                return base
            if is_generic_browser_overview:
                if matched_heading:
                    base = f'Here is more from the page under "{matched_heading}": {preview}'
                else:
                    base = f"Here is more from the page: {preview}"
                if extra_bits:
                    return base + " " + " ".join(extra_bits)
                return base
            if display_topic:
                if matched_heading:
                    base = f"Here is the section about '{display_topic}' under \"{matched_heading}\": {preview}"
                    if extra_bits:
                        return base + " " + " ".join(extra_bits)
                    return base
                base = f"Here is the section about '{display_topic}': {preview}"
                if extra_bits:
                    return base + " " + " ".join(extra_bits)
                return base
            if current_url:
                base = f"Here is the requested text from {current_url}: {preview}"
                if extra_bits:
                    return base + " " + " ".join(extra_bits)
                return base
            base = f"Here is the requested text: {preview}"
            if extra_bits:
                return base + " " + " ".join(extra_bits)
            return base
        if saved_path:
            if download_label:
                return f'Downloaded "{download_label}" to `{saved_path}`.'
            if download_hint:
                return f"Downloaded the artifact matching '{download_hint}' to `{saved_path}`."
            return f'The download was saved to `{saved_path}`.'
        if reported_title and current_url:
            return f'The page title at {current_url} is "{reported_title}".'
        if reported_title:
            return f'The page title is "{reported_title}".'
        if summary:
            return summary
        return ""

    # ------------------------------------------------------------------ #
    # F. Generic summary detection                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def is_generic_file_work_summary(summary: str) -> bool:
        """Return True when a tool summary string is too generic to present to the persona.

        Used as a suppression gate: if True, the ``reason`` field is shown instead.

        Deduplicated from ContextPackEngine._is_generic_file_work_summary and
        ScratchpadFormatter._is_generic_file_work_summary (identical implementations).
        """
        cleaned = str(summary or "").strip().lower()
        if not cleaned:
            return True
        return cleaned.startswith(_GENERIC_FILE_WORK_PREFIXES)

    # ------------------------------------------------------------------ #
    # G. Text utility                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def sanitize_note(text: str) -> str:
        """Collapse whitespace and cap at 280 characters.

        Extracted from ContextPackEngine._sanitize_runtime_note.
        """
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""
        return cleaned[:280]

    @staticmethod
    def truncate_scratchpad(text: str, *, limit: int) -> str:
        """Tail-slice a scratchpad string to *limit* characters with a header marker.

        Returns the original string unchanged when ``len(text) <= limit``.

        Extracted from PromptBuilder._truncate_scratchpad.
        """
        if not text or len(text) <= limit:
            return text
        return "[TRUNCATED older scratchpad history]\n" + text[-limit:]

    @staticmethod
    def truncate_text(text: str, limit: int) -> str:
        """Tail-slice a text string to *limit* characters with a ``[TRUNCATED]`` marker.

        Returns the original string unchanged when ``len(value) <= limit``.

        Extracted from ScratchpadFormatter._truncate_text.
        """
        value = str(text or "")
        if len(value) <= limit:
            return value
        return value[:limit] + "\n[TRUNCATED]"

    # ------------------------------------------------------------------ #
    # Private helpers (used inside build_runtime_note)                   #
    # ------------------------------------------------------------------ #

    @classmethod
    def _extract_latest_exact_read_path(cls, scratchpad: list[str]) -> str:
        for entry in reversed(cls.latest_stage_entries(scratchpad)):
            match = re.search(r"FILE_READ_EXACT_PATH:\s*([^\n]+)", str(entry or ""))
            if match:
                return match.group(1).strip()
        return ""

    @classmethod
    def _extract_latest_lookup_brief(cls, scratchpad: list[str]) -> str:
        for entry in reversed(cls.latest_stage_entries(scratchpad)):
            text = str(entry or "")
            if not text.lstrip().startswith("FILE_LOOKUP_MATCHES:"):
                continue
            _, _, payload = text.partition("FILE_LOOKUP_MATCHES:")
            matches = [line.strip() for line in payload.splitlines() if line.strip()]
            if matches:
                return "Latest file lookup matches: " + " | ".join(matches[:3])
        return ""
