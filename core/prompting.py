"""Prompt construction utilities."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from core.prompt_builder import PromptBuilder
from core.scratchpad_formatter import ScratchpadFormatter
from core.search_contracts import (
    SEARCH_FAILURE_PREFIX as _BACKGROUND_SEARCH_FAILED_PREFIX,
    SEARCH_FAILURE_REPORTER_INSTRUCTION as _SEARCH_FAILURE_REPORTER_INSTRUCTION,
    SEARCH_REPORTER_INSTRUCTION as _SEARCH_REPORTER_INSTRUCTION,
    SEARCH_RESULT_PREFIX as _BACKGROUND_SEARCH_COMPLETE_PREFIX,
)
from core.turn_explanation import LAST_TURN_EXPLANATION_PREFIX

_SEARCH_SUMMARY_PREFIX = "[SEARCH SUMMARY FOR "
_SEARCH_REPORT_CONSUMED_PREFIX = "[SEARCH REPORT CONSUMED FOR "
from core.services.reminders import (
    PROACTIVE_TRIGGER_PREFIX as _PROACTIVE_TRIGGER_PREFIX,
    PROACTIVE_TRIGGER_CONSUMED_PREFIX as _PROACTIVE_TRIGGER_CONSUMED_PREFIX,
)


def _clean_for_model(messages: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    """Drop low-value noise and UI-only chatter that poisons context."""
    out: List[Dict[str, str]] = []
    for message in messages:
        role = (message.get("role") or "user").strip() or "user"
        content = (message.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant" and content in {"Thinking...", "Thinking…"}:
            continue
        if content.startswith("[UI]"):
            continue
        if content.startswith("[LATEST_RUNTIME_CONTEXT]"):
            continue
        if content.startswith(_SEARCH_REPORT_CONSUMED_PREFIX):
            continue
        if content.startswith(_BACKGROUND_SEARCH_COMPLETE_PREFIX):
            continue
        if content.startswith(_BACKGROUND_SEARCH_FAILED_PREFIX):
            continue
        if content == _SEARCH_REPORTER_INSTRUCTION:
            continue
        if content == _SEARCH_FAILURE_REPORTER_INSTRUCTION:
            continue
        if content.startswith(_PROACTIVE_TRIGGER_PREFIX):
            continue
        if content.startswith(_PROACTIVE_TRIGGER_CONSUMED_PREFIX):
            continue
        if content.startswith(LAST_TURN_EXPLANATION_PREFIX):
            continue
        if "GGML_ASSERT" in content or "llama-context.cpp" in content:
            continue
        if content.startswith("[copied"):
            continue
        if content.startswith("[ERROR]"):
            continue
        out.append({"role": role, "content": content})
    return out


def _render_transcript_block(messages: List[Dict[str, str]]) -> str:
    if not messages:
        return ""
    lines: List[str] = ["[CONVERSATION_TRANSCRIPT]"]
    for idx, message in enumerate(messages, start=1):
        role = str(message.get("role") or "user").strip().lower() or "user"
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"[{idx}] ROLE: {role}")
        lines.append("CONTENT:")
        lines.append(content)
        lines.append("---")
    if lines[-1] == "---":
        lines.pop()
    return "\n".join(lines)


def _persona_requires_single_system_message(model_path: Optional[str | object] = None) -> bool:
    model_name = str(model_path or "").lower()
    return "qwen3.5" in model_name or "qwen35" in model_name


def build_persona_messages(
    *,
    system_content: str,
    history: List[Dict[str, str]],
    outcome_block: str = "",
    tail_system_content: str = "",
    model_path: Optional[Path | str] = None,
) -> List[Dict[str, str]]:
    """Build persona-phase chat messages with model-specific compatibility rules."""
    cleaned_history = _clean_for_model(history)

    latest_terminal_search_event = ""

    if not _persona_requires_single_system_message(model_path):
        messages = [{"role": "system", "content": system_content}]
        for message in cleaned_history:
            role = (message.get("role") or "user").strip().lower()
            content = (message.get("content") or "").strip()
            if not content:
                continue
            if role == "system" and content.startswith(_SEARCH_SUMMARY_PREFIX):
                latest_terminal_search_event = content
                continue
            messages.append({"role": role, "content": content})
        if latest_terminal_search_event:
            messages.append({"role": "system", "content": latest_terminal_search_event})
        if tail_system_content:
            messages.append({"role": "system", "content": tail_system_content})
        if outcome_block:
            messages.append({"role": "system", "content": outcome_block})
        return messages

    merged_system_parts: List[str] = [system_content.strip()]
    supplemental_system: List[str] = []
    convo: List[Dict[str, str]] = []
    runtime_context_content = ""

    for message in cleaned_history:
        role = (message.get("role") or "user").strip().lower()
        content = (message.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            if content.startswith(_SEARCH_SUMMARY_PREFIX):
                latest_terminal_search_event = content
            else:
                supplemental_system.append(content)
            continue
        if role in {"user", "assistant"}:
            convo.append({"role": role, "content": content})

    if supplemental_system:
        merged_system_parts.append(
            "[SUPPLEMENTAL_SYSTEM_CONTEXT]\n" + "\n\n".join(supplemental_system)
        )
    if tail_system_content or outcome_block or latest_terminal_search_event:
        runtime_context_parts: List[str] = []
        if latest_terminal_search_event:
            runtime_context_parts.append("[LATEST_SYSTEM_EVENT]\n" + latest_terminal_search_event)
        if tail_system_content:
            # tail_system_content holds directive rules (NO_MUTATION_RULE, ACTIVE_SKILL, etc.)
            # — these are instructions, not system events. No [LATEST_SYSTEM_EVENT] wrapper.
            runtime_context_parts.append(tail_system_content)
        if outcome_block:
            runtime_context_parts.append(
                "[LATEST_SYSTEM_EVENT]\n[FINAL_STAGE_OUTCOME]\n" + outcome_block
            )
        if runtime_context_parts:
            runtime_context_content = "[LATEST_RUNTIME_CONTEXT]\n" + "\n\n".join(
                part for part in runtime_context_parts if part
            )

    latest_user_idx: Optional[int] = None
    latest_user_message = ""
    for idx in range(len(convo) - 1, -1, -1):
        if (convo[idx].get("role") or "").strip().lower() == "user":
            latest_user_idx = idx
            latest_user_message = (convo[idx].get("content") or "").strip()
            break

    transcript_messages = list(convo)
    if latest_user_idx is not None:
        transcript_messages = convo[:latest_user_idx]
    if runtime_context_content:
        if latest_user_message:
            transcript_messages.append(
                {
                    "role": "user",
                    "content": (
                        "[CURRENT_USER_TURN]\n"
                        "The latest user message is supplied as the final chat message outside this transcript. "
                        "Treat the following system block as newer system context that applies to that current user turn."
                    ),
                }
            )
        transcript_messages.append(
            {
                "role": "system",
                "content": (
                    "[MESSAGE_PROTOCOL]\n"
                    "Blocks inside [LATEST_RUNTIME_CONTEXT] are authoritative runtime context supplied by the system. "
                    "Treat them as the latest system facts, not as user claims or prior assistant narration.\n\n"
                    + runtime_context_content
                ),
            }
        )
    transcript_block = _render_transcript_block(transcript_messages)
    if transcript_block:
        merged_system_parts.append(transcript_block)

    messages = [{"role": "system", "content": "\n\n".join(part for part in merged_system_parts if part)}]
    if latest_user_message:
        messages.append({"role": "user", "content": latest_user_message})
    return messages
