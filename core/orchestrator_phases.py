from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from config import CFG
from core.contracts import RouteDecision, StageOutcomePack
from core.document_focus import build_document_focus_messages, extract_document_focus
from core.debug_tools import log_prompt_debug
from core.feature_hooks import fire_hooks
from core.engines.context_pack import _hook_upsert_runtime_context
from core.engines.followup_resolution import FollowupResolutionEngine
from core.engines.rollback_engine import invert_manifest as invert_rollback_manifest
from core.engines.summary import SummaryEngine
from core.engines.proactive_monitor import (
    PROACTIVE_TRIGGER_PREFIX,
    ReminderStore,
    display_fire_at_local,
    parse_proactive_trigger_message,
    parse_reminder_request,
)
from core.engines.route_clarity import RouteClarifier, _PATHISH_RE
from core.engines.state_mutation import StateMutationEngine
from core.executor import StageExecutor
from core.file_stage_policy import FileStagePolicy
from core.persona_output import sanitize_persona_output
from core.prompting import ScratchpadFormatter, PromptBuilder, build_persona_messages
from core.route_boundary import BoundaryValidationError, RouterBoundary
from core.routing.environment_queries import looks_like_live_environment_query
from core.routing.route_normalizer import (
    annotate_file_stage_kinds,
    detect_route_interceptor,
    looks_like_explicit_browser_request,
    normalize_route_decision,
)
from core.search_contracts import (
    is_background_search_payload,
    is_search_error_result,
    is_search_reporter_instruction,
    normalize_search_error,
    parse_background_search_content,
)
from core.skills import apply_route_skill_layer
from core.stage_policy import stage_requires_user_approval, stage_requires_user_input, stage_is_explicit_proposal
from core.stream_filter import stream_thinking_filter
from llm.llm_server_client import LLMClientError
from core.runtime_control import OperationCancelled
from tools.vision import VisionError, generate_stream_with_image_attachment, generate_with_image_attachment

_LOG = logging.getLogger(__name__)

FALLBACK_SECRETARY = "You are a Router. Output JSON: {decision: 'CHAT' or 'TASK', card: {goal, context}}."
_CONFIRMATION_PHRASES = (
    "shall i",
    "should i",
    "would you like",
    "do you want",
    "want me to",
    "may i",
    "can i",
    "attempt to re-route",
    "attempt to reroute",
)
_RECALL_TAG_RE = re.compile(r"\[RECALL:\s*(.*?)\]", re.IGNORECASE | re.DOTALL)
_SCRATCHPAD_ERROR_PREFIXES = (
    "system error:",
    "security violation:",
    "traceback",
    "exception:",
    "execution error:",
)
_SEARCH_RECENCY_HINT_RE = re.compile(r"(?i)\b(latest|current|recent|news|headline|headlines|today|this week|this month)\b")
_INGESTED_DOC_META_ACTION_RE = re.compile(
    r"(?i)^\s*/ingest\b|\b(ingest|upload|import|attach|add)\b.*\b(document|pdf|docx|file)\b"
)
_INGESTED_DOC_ACTION_RE = re.compile(
    r"(?i)\b("
    r"summarize|summary|what does|what(?:'s| is)(?: in)?|tell me|explain|describe|"
    r"does .* mention|mention|mentions|find the part|find(?: me)?|locate|where does|"
    r"which page|what page|chapter|section|quote|quoted|cover|covers|"
    r"how many|how much|list|show me|give me|is there|are there"
    r")\b"
)
_INGESTED_DOC_REFERENCE_RE = re.compile(
    r"(?i)\b(document|documents|doc|docs|pdf|pdfs|manual|manuals|chapter|section|page|pages|fcom)\b"
)
_INGESTED_DOC_EXPLICIT_RE = re.compile(r"(?i)\b(ingested|injected|uploaded|attached)\b")
_INGESTED_DOC_COMMON_NAME_WORDS = {
    "aircraft",
    "copy",
    "crew",
    "date",
    "dec",
    "document",
    "flight",
    "issue",
    "manual",
    "operating",
    "unofficial",
}
_LIVE_SCREEN_VISUAL_CHAT_RE = re.compile(
    r"(?i)\b("
    r"read|identify|describe|tell me|show me|what(?:'s| is)?|which|"
    r"can you see|do you see|check|look at"
    r")\b"
)
_LIVE_SCREEN_VISUAL_TARGET_RE = re.compile(
    r"(?i)\b("
    r"screen|display|monitor|desktop|window|tab|button|label|title|"
    r"filename|file name|path|text|icon|menu|screenshot|image|frame"
    r")\b"
)
_LIVE_SCREEN_POINTER_CUE_RE = re.compile(
    r"(?i)\b("
    r"look here|right here|this|that|this one|that one|this tab|that tab|"
    r"this label|that label|this button|that button|this text|that text|"
    r"under (?:my )?(?:cursor|pointer)|at (?:my )?(?:cursor|pointer)|"
    r"where i(?:'m| am) pointing|near my pointer|near my cursor"
    r")\b"
)
_LIVE_SCREEN_FILE_ACTION_RE = re.compile(
    r"(?i)\b("
    r"create|edit|modify|delete|remove|move|copy|rename|write|save|replace|"
    r"run|execute|ingest|upload|import"
    r")\b"
)
_LIVE_SCREEN_WORKSPACE_HINT_RE = re.compile(
    r"(?i)\b(workspace|repo|repository|folder|directory|disk|drive)\b"
)
_LIVE_SCREEN_PATH_LITERAL_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/]|/mnt/[a-z]/|[\w./\\-]+\.[A-Za-z0-9]{1,8})"
)
_LIVE_SCREEN_ROUTER_ATTACHMENT = (
    "[LIVE_SCREEN]\n"
    "A current screen image for this turn is attached.\n"
    "Use it only as present visual context for routing the user's request.\n"
    "If the user is asking what text, filename, label, button, tab, or other content is visible in the attached frame, route that as CHAT.\n"
    "Do not create FILE_WORK just to identify visible on-screen content."
)
_LIVE_SCREEN_PERSONA_ATTACHMENT = (
    "[LIVE_SCREEN]\n"
    "A current screen image for this turn is attached.\n"
    "Use it when relevant to the user's latest request.\n"
    "Do not claim continuous vision beyond this attached frame."
)
_LIVE_SCREEN_POINTER_PERSONA_ATTACHMENT = (
    "[LIVE_SCREEN]\n"
    "A pointer-centered crop around the user's current cursor position is attached for this turn.\n"
    "Prioritize the content nearest the cursor and answer from that local area first.\n"
    "Do not claim continuous vision beyond this attached frame."
)
_STATE_MUTATION_ENGINE = StateMutationEngine()
_FOLLOWUP_RESOLUTION_ENGINE = FollowupResolutionEngine(state_mutation_engine=_STATE_MUTATION_ENGINE)
_ROUTE_CLARIFIER = RouteClarifier()
_LIVE_SCREEN_WINDOW_PERSONA_ATTACHMENT = (
    "[LIVE_SCREEN]\n"
    "The current active window is attached for this turn.\n"
    "Prioritize the visible content inside that window.\n"
    "Do not claim continuous vision beyond this attached frame."
)
_LATEST_RUNTIME_CONTEXT_PREFIX = "[LATEST_RUNTIME_CONTEXT]"


def _is_live_environment_chat_query(text: str) -> bool:
    return bool(looks_like_live_environment_query(str(text or "").strip()))


# --- Privacy model: stage types that require admin privilege ---
_ADMIN_ONLY_STAGE_TYPES: set[str] = {"FILE_WORK"}


def _route_requires_admin_privilege(route_decision: dict[str, Any] | None) -> bool:
    """Return True if the route decision contains any admin-only stage types."""
    if not route_decision:
        return False
    card = dict(route_decision.get("card") or {})
    for stage in card.get("stages") or []:
        if str(stage.get("stage_type") or "").strip().upper() in _ADMIN_ONLY_STAGE_TYPES:
            return True
    return False


def _apply_non_admin_route_guard(orc) -> None:
    """Override admin-only routes to CHAT when the active user is not admin.

    Implements the route-level guard from the Piper Memory & Privacy Model:
    non-admin users must never be routed to FILE_WORK, RUN_CODE, or desktop
    computer-use domains.  If the Secretary suggests such a route, it is
    overridden to CHAT with a friendly explanation.
    """
    user_runtime = getattr(getattr(orc, "_cfg", None), "user_runtime", None)
    if user_runtime is None:
        return
    try:
        profile = user_runtime.active_profile()
    except Exception:
        return
    if getattr(profile, "is_admin", False):
        return
    if not _route_requires_admin_privilege(orc.route_decision):
        return
    orc.ui.put((
        "agent_log",
        "   -> Non-admin route guard: overriding admin-only route to CHAT.",
    ))
    orc.route_decision = {
        "decision": "CHAT",
        "interceptor": "NON_ADMIN_ROUTE_GUARD",
        "system_notice": {
            "kind": "non_admin_route_guard",
            "reply": (
                "I cannot access files or run code — only Baris can do that on this system. "
                "I can help you with chat, search the web, or answer questions though!"
            ),
        },
    }


def _is_pending_search_payload(message: dict) -> bool:
    return (
        message.get("role") == "system"
        and is_background_search_payload(message.get("content", ""))
    )


def _is_pending_proactive_trigger(message: dict) -> bool:
    return (
        message.get("role") == "system"
        and str(message.get("content", "")).startswith(PROACTIVE_TRIGGER_PREFIX)
    )


def _latest_proactive_trigger_payload(messages: list[dict] | tuple[dict, ...] | None) -> dict[str, Any] | None:
    for message in reversed(list(messages or [])):
        if not _is_pending_proactive_trigger(message):
            continue
        payload = parse_proactive_trigger_message(str(message.get("content") or ""))
        if payload:
            payload["raw_message"] = str(message.get("content") or "")
            return payload
    return None


def _is_search_reporter_instruction(message: dict) -> bool:
    return (
        message.get("role") == "system"
        and is_search_reporter_instruction(message.get("content", ""))
    )


def _build_search_failure_summary(query: str, error_text: str) -> str:
    clean_error = normalize_search_error(error_text) or "The search backend failed before returning usable results."
    clean_query = str(query or "Unknown Query").strip() or "Unknown Query"
    return "\n".join(
        [
            "The web search failed before usable results were retrieved.",
            f"- Query: {clean_query}",
            f"- Error: {clean_error}",
            "- Verified web findings: none.",
        ]
    )


def _summarize_search_error_for_user(error_text: str) -> str:
    clean_error = normalize_search_error(error_text) or "the search backend failed"
    lower = clean_error.casefold()
    if "zero results" in lower:
        return "the search provider returned zero usable results"
    if "403" in lower and "ratelimit" in lower:
        return "the search provider returned HTTP 403 Ratelimit"
    if "403" in lower:
        return "the search provider returned HTTP 403"
    if "rate" in lower and "limit" in lower:
        return "the search provider rate-limited the request"
    return clean_error


def _build_search_failed_persona_reply(orc) -> str:
    error_text = _summarize_search_error_for_user(str(getattr(orc, "latest_search_error", "") or ""))
    return (
        "The web search failed before usable results were retrieved. "
        f"Reason: {error_text}. "
        "Verified web findings from this attempt: none."
    )


def _build_search_in_flight_reply(notice: dict) -> str:
    active_query = str(notice.get("active_query", "") or "").strip()
    requested_query = str(notice.get("requested_query", "") or "").strip()
    if active_query and requested_query and active_query.casefold() != requested_query.casefold():
        return (
            f'I already have a web search running for "{active_query}". '
            f'Let that finish first, then ask again about "{requested_query}" and I will take it next.'
        )
    if active_query:
        return (
            f'I already have a web search running for "{active_query}". '
            "Let that finish first, then ask again if you want me to continue from there."
        )
    if requested_query:
        return (
            "I already have a web search running right now. "
            f'Let that finish first, then ask again about "{requested_query}" and I will take it next.'
        )
    return "I already have a web search running right now. Let that finish first, then ask again and I will take the next search."


def _build_search_first_pass_rule(query: str) -> str:
    clean_query = str(query or "").strip()
    lines = [
        "[SEARCH_FIRST_PASS_RULE]",
        "A background web search is already running for the user's latest request.",
    ]
    if clean_query:
        lines.append(f"Search query: {clean_query}")
    lines.extend(
        [
            "While it runs, engage with the topic using the current system context and your existing knowledge only.",
            "Give a useful first-pass response: relevant context, a best-effort answer, or one focused follow-up question if that would materially help.",
            "The runtime will automatically deliver the completed search results on this same turn as soon as the search finishes.",
            "Do not ask whether to proceed, whether the user wants the results, or whether you should continue once the search completes.",
            "Do not tell the user to wait, reply, or confirm before the search finishes.",
            "If you ask a question, it must clarify the search topic itself, not permission to continue the search.",
            "Stay tightly on the search topic. Do not riff on unrelated profile facts, tasks, events, memories, or document excerpts.",
            "Ignore any personal or workspace context unless it is directly relevant to the search query itself.",
            "Do not speculate that the web findings are empty, quiet, lacking breakthroughs, or already leaning one way unless the current system context explicitly says so.",
            "Do not present your existing knowledge as if it came from the live web search.",
            "Make it clear the web findings will follow shortly.",
            "Do not emit control tags such as [ROUTER] or [RECALL].",
        ]
    )
    if _SEARCH_RECENCY_HINT_RE.search(clean_query):
        lines.extend(
            [
                "The query is recency-sensitive. Do not state current/live facts, release status, dates, version status, rankings, prices, or 'latest news' claims from memory.",
                "For recency-sensitive searches, keep the first-pass response brief: say what you are checking and defer factual claims until the web results arrive.",
                "Do not say a version is already out, current, upcoming, quiet, settled, or lacking news unless supplied by explicit current system evidence.",
            ]
        )
    return "\n".join(lines)


def _build_search_first_pass_fallback(query: str) -> str:
    clean_query = str(query or "").strip()
    clean_query = re.sub(
        r"(?i)^\s*(?:please\s+)?(?:search(?:\s+the\s+web)?\s+for|look\s+up|look\s+for|find|locate)\s+",
        "",
        clean_query,
        count=1,
    ).strip(" .?!")
    if clean_query:
        return f'I\'m checking the web for "{clean_query}" now. I\'ll bring the results back automatically in a moment.'
    return "I'm checking the web for that now. I'll bring the results back automatically in a moment."


def _build_search_preview_history(user_msg: str, query: str) -> list[dict[str, str]]:
    current_user = str(user_msg or query or "").strip()
    if not current_user:
        return []
    return [{"role": "user", "content": current_user}]


def _build_search_report_history(
    history: list[dict] | tuple[dict, ...] | None,
    *,
    user_msg: str,
) -> list[dict[str, str]]:
    filtered: list[dict[str, str]] = []
    latest_summary = None
    for message in reversed(list(history or [])):
        if str(message.get("role") or "").strip().lower() != "system":
            continue
        content = str(message.get("content") or "").strip()
        if content.startswith("[SEARCH SUMMARY FOR "):
            latest_summary = {"role": "system", "content": content}
            break
    if latest_summary is not None:
        filtered.append(latest_summary)
    filtered.extend(_build_search_preview_history(user_msg, user_msg))
    return filtered


def _scratchpad_steps_executed(stage_log: list[str]) -> bool:
    return any(str(entry or "").lstrip().startswith("STEP ") for entry in stage_log)


def _scratchpad_entry_indicates_error(entry: str) -> bool:
    text = str(entry or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(lower.startswith(prefix) for prefix in _SCRATCHPAD_ERROR_PREFIXES):
        return True
    if "observation_kind: error" in lower:
        return True
    if lower.startswith("file_checker_verdict:"):
        return "verified" not in lower
    if "result: failed / incomplete" in lower or "result: failed" in lower:
        return True
    return False


def _iter_ingested_document_aliases(documents: list[dict]) -> set[str]:
    aliases: set[str] = set()
    for item in documents:
        name = str(item.get("name") or item.get("source_path") or "").strip()
        stem = Path(name).stem if name else ""
        for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]*", stem):
            token = raw.strip("._-").lower()
            if not token or token.isdigit():
                continue
            alpha_count = sum(1 for ch in raw if ch.isalpha())
            if alpha_count >= 2 and raw.isupper() and len(token) >= 4:
                aliases.add(token)
                continue
            if len(token) >= 5 and token not in _INGESTED_DOC_COMMON_NAME_WORDS:
                aliases.add(token)
    return aliases


def _should_route_ingested_document_chat(
    user_msg: str,
    recent_history: list[dict],
    documents: list[dict],
) -> bool:
    text = str(user_msg or "").strip()
    if not text or text.startswith("/"):
        return False
    if not documents:
        return False

    lowered = text.lower()
    if _INGESTED_DOC_META_ACTION_RE.search(lowered):
        return False
    if not _INGESTED_DOC_ACTION_RE.search(lowered):
        return False

    aliases = _iter_ingested_document_aliases(documents)
    explicit_reference = bool(_INGESTED_DOC_EXPLICIT_RE.search(lowered))
    named_reference = any(alias in lowered for alias in aliases)
    generic_reference = bool(_INGESTED_DOC_REFERENCE_RE.search(lowered))

    history_blob = " ".join(
        str(message.get("content") or "")
        for message in recent_history[-6:]
        if isinstance(message, dict)
    ).lower()
    history_reference = bool(_INGESTED_DOC_EXPLICIT_RE.search(history_blob)) or any(
        alias in history_blob for alias in aliases
    )

    if explicit_reference or named_reference:
        return True
    if generic_reference and (len(documents) == 1 or history_reference):
        return True
    if history_reference and len(documents) == 1:
        return True
    return False


def _current_live_screen_path(orc):
    if getattr(orc, "live_screen", None) is None:
        return None
    try:
        return orc.live_screen.current_image_path(require_fresh=True)
    except Exception:
        return None


def _should_route_live_screen_visual_chat(user_msg: str, *, live_screen_path) -> bool:
    if live_screen_path is None:
        return False
    text = str(user_msg or "").strip()
    if not text or text.startswith("/"):
        return False
    if _LIVE_SCREEN_FILE_ACTION_RE.search(text):
        return False
    if _LIVE_SCREEN_WORKSPACE_HINT_RE.search(text):
        return False
    if _LIVE_SCREEN_PATH_LITERAL_RE.search(text):
        return False
    if _LIVE_SCREEN_VISUAL_CHAT_RE.search(text) and _LIVE_SCREEN_POINTER_CUE_RE.search(text):
        return True
    return bool(_LIVE_SCREEN_VISUAL_CHAT_RE.search(text) and _LIVE_SCREEN_VISUAL_TARGET_RE.search(text))


def _should_use_pointer_focus_for_turn(user_msg: str, *, live_screen_path) -> bool:
    if not _should_route_live_screen_visual_chat(user_msg, live_screen_path=live_screen_path):
        return False
    text = str(user_msg or "").strip()
    return bool(_LIVE_SCREEN_POINTER_CUE_RE.search(text))


def _resolve_live_screen_turn_image(orc):
    existing = getattr(orc, "turn_screen_image_path", None)
    if existing is not None:
        try:
            if Path(existing).exists():
                return existing
        except Exception:
            pass

    live_screen_path = _current_live_screen_path(orc)
    if live_screen_path is None:
        orc.turn_screen_image_path = None
        orc.turn_screen_image_kind = ""
        return None

    if _should_use_pointer_focus_for_turn(orc.user_msg, live_screen_path=live_screen_path):
        try:
            focus_path = orc.live_screen.capture_focus_image()
            orc.turn_screen_image_path = focus_path
            orc.turn_screen_image_kind = "pointer"
            orc.ui.put(("agent_log", "   -> Using pointer-focus frame for this turn."))
            return focus_path
        except Exception as exc:
            orc.ui.put(("agent_log", f"   -> Pointer-focus capture failed: {exc}"))

    orc.turn_screen_image_path = live_screen_path
    try:
        orc.turn_screen_image_kind = str(orc.live_screen.mode() or "display").strip().lower()
    except Exception:
        orc.turn_screen_image_kind = "display"
    return live_screen_path


def _live_screen_persona_attachment_text(orc) -> str:
    kind = str(getattr(orc, "turn_screen_image_kind", "") or "").strip().lower()
    if kind == "pointer":
        return _LIVE_SCREEN_POINTER_PERSONA_ATTACHMENT
    if kind == "window":
        return _LIVE_SCREEN_WINDOW_PERSONA_ATTACHMENT
    return _LIVE_SCREEN_PERSONA_ATTACHMENT


def _latest_runtime_context_message(messages: list[dict] | tuple[dict, ...] | None) -> str:
    for message in reversed(list(messages or [])):
        if str(message.get("role") or "").lower() != "system":
            continue
        content = str(message.get("content") or "").strip()
        if content.startswith(_LATEST_RUNTIME_CONTEXT_PREFIX):
            return content
    return ""


def _build_followup_resolution_history(
    messages: list[dict] | tuple[dict, ...] | None,
    *,
    current_user_msg: str = "",
) -> list[dict[str, Any]]:
    """Preserve the latest hidden runtime context for deterministic follow-up routing.

    The router prompt uses a small trimmed history for token economy. The follow-up
    resolver is local logic and needs a slightly richer view so short clarifications
    like "I mean the file" still see the latest `[LATEST_RUNTIME_CONTEXT]` block even
    when hidden explanation messages have pushed it out of the router prompt tail.
    """
    current_clean = " ".join(str(current_user_msg or "").split()).strip().lower()
    skipped_current = False
    history: list[dict[str, Any]] = []

    for message in reversed(list(messages or [])):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "")
        if role == "assistant" and content.strip() == "Thinking...":
            continue
        if role == "user":
            content_clean = " ".join(content.split()).strip().lower()
            if not skipped_current and (not current_clean or content_clean == current_clean):
                skipped_current = True
                continue
        history.append(dict(message))

    history.reverse()

    latest_runtime_context = _latest_runtime_context_message(messages)
    if latest_runtime_context and not any(
        str(item.get("role") or "").strip().lower() == "system"
        and str(item.get("content") or "").strip().startswith(_LATEST_RUNTIME_CONTEXT_PREFIX)
        for item in history
    ):
        history.append(
            {
                "role": "system",
                "content": latest_runtime_context,
                "hidden": True,
            }
        )

    return history


def _extract_latest_stage_outcome_entry(scratchpad: list[str]) -> str:
    for entry in reversed(scratchpad or []):
        text = str(entry or "")
        if " OUTCOME ===" in text and "RESULT:" in text:
            return text
    return ""

def _consume_pipeline_stream_metrics(orc) -> list[dict[str, float | str]]:
    try:
        return list(getattr(orc.pipeline, "consume_completed_stream_metrics", lambda: [])() or [])
    except Exception:
        return []


def _finalize_persona_turn(orc, *, reporter_just_ran: bool = False) -> None:
    orc.reporter_just_ran = False
    orc.latest_search_summary = ""
    orc.latest_search_failed = False
    orc.latest_search_error = ""
    orc.undo_notice_pending = False
    fire_hooks("on_turn_end", orc, reporter_just_ran=reporter_just_ran)


def _finish_persona_fast_path(
    orc,
    text: str,
    *,
    reporter_just_ran: bool = False,
    emit_start: bool = False,
) -> None:
    del reporter_just_ran
    if emit_start:
        orc.ui.put(("assistant_stream_start", {"tts_voice": orc.ss.tts_voice, "tts_speed": orc.ss.tts_speed}))
    # Stream the pre-computed answer word-by-word so it appears progressively
    # in the UI rather than all at once.
    chunks = re.split(r'(\s+)', text)
    for chunk in chunks:
        if chunk:
            orc.ui.put(("assistant_stream_delta", {"text": chunk}))
    orc.ui.put(("assistant_stream_end", ""))
    orc.next_stage = "FINISHED"


def _append_undo_notice_if_needed(orc, text: str) -> str:
    del orc
    return str(text or "").strip()


def _build_file_state_correction_ack_reply(system_notice: dict[str, Any]) -> str:
    reply = str(system_notice.get("reply") or "").strip()
    if reply:
        return reply
    target = str(system_notice.get("target") or "").strip()
    desired_state = str(system_notice.get("desired_state") or "").strip().lower()
    if target and desired_state == "absent":
        return f"Indeed. The verified final state was already that `{target}` did not exist."
    return "Indeed. The verified final state already matched that correction."


def _build_file_target_confirmation_cancelled_reply(system_notice: dict[str, Any]) -> str:
    reply = str(system_notice.get("reply") or "").strip()
    if reply:
        return reply
    exact_target = str(system_notice.get("exact_target") or "").strip()
    if exact_target:
        return f"Understood. I will leave `{exact_target}` alone."
    return "Understood. I will leave the workspace unchanged."


def _build_stage_approval_cancelled_reply(system_notice: dict[str, Any]) -> str:
    reply = str(system_notice.get("reply") or "").strip()
    if reply:
        return reply
    stage_goal = str(system_notice.get("stage_goal") or "").strip()
    if stage_goal:
        return f"Understood. I will stop before `{stage_goal}` and leave things unchanged."
    return "Understood. I will stop here and leave things unchanged."


def _build_stage_approval_no_remaining_work_reply(system_notice: dict[str, Any]) -> str:
    reply = str(system_notice.get("reply") or "").strip()
    if reply:
        return reply
    return (
        "I have your approval, but there is no follow-up execution stage recorded, "
        "so I will stop here instead of guessing."
    )


def _build_destructive_prompt_injection_refusal_reply(system_notice: dict[str, Any]) -> str:
    reply = str(system_notice.get("reply") or "").strip()
    if reply:
        return reply
    return "I cannot follow override-style instructions to remove workspace files."


def _build_remaining_route_decision(orc, *, start_stage_index: int) -> dict[str, Any]:
    route = json.loads(json.dumps(dict(getattr(orc, "route_decision", {}) or {}), ensure_ascii=False))
    if not isinstance(route, dict):
        return {}
    if str(route.get("decision") or "").strip().upper() != "TASK":
        return {}
    card = dict(route.get("card") or {})
    stages = [dict(item) for item in (card.get("stages") or []) if isinstance(item, dict)]
    if stages:
        start_index = max(0, min(int(start_stage_index or 0), len(stages)))
        card["stages"] = stages[start_index:]
    route["card"] = card
    return route


def _build_remaining_route_decision_for_target_confirmation(orc, *, stage_index: int) -> dict[str, Any]:
    return _build_remaining_route_decision(orc, start_stage_index=stage_index)


def _maybe_pause_for_missing_file_target_confirmation(
    orc,
    executor: StageExecutor,
    *,
    stage: dict[str, Any],
    stage_index: int,
    stage_log: list[str],
) -> bool:
    requested_target = str(getattr(executor, "terminal_missing_file_target", "") or "").strip()
    if not requested_target:
        return False
    if not FileStagePolicy.stage_is_file_work(stage):
        return False
    if FileStagePolicy.stage_allows_absence_confirmation(stage):
        return False
    if FileStagePolicy.stage_may_create_missing_target(stage):
        return False
    stage_text = FileStagePolicy.stage_goal_success_text(stage)
    if not re.search(r"\b(delete|remove)\b", stage_text):
        return False
    workspace = Path(getattr(orc.brain, "workspace", "."))
    candidates = FileStagePolicy.find_workspace_target_candidates(workspace, requested_target, limit=3)
    if not candidates:
        return False

    if len(candidates) == 1:
        question = f"I can't find `{requested_target}`. Did you mean `{candidates[0]}`?"
    else:
        rendered = ", ".join(f"`{item}`" for item in candidates[:3])
        question = f"I can't find `{requested_target}`. Which of these did you mean: {rendered}?"

    proposal_entry = ScratchpadFormatter.format_step(
        max(1, _scratchpad_steps_executed(stage_log) + 1),
        "Pause and ask the user to confirm the intended file target.",
        "[NO_TOOL_PROPOSAL]",
        f"PROPOSAL: {question}",
    )
    stage_log.append(proposal_entry)
    executor.pause_requested = True
    executor.pause_mode = "user_input"
    orc.pending_file_target_confirmation = {
        "kind": "missing_file_target_confirmation",
        "exact_target": requested_target,
        "candidates": candidates[:3],
        "question": question,
        "route_decision": _build_remaining_route_decision_for_target_confirmation(
            orc,
            stage_index=stage_index,
        ),
        "stage_type": str(stage.get("stage_type") or "").strip(),
    }
    orc.ui.put(("agent_log", "   -> Exact file target is missing, but a close workspace match exists. Pausing for confirmation."))
    orc._log_dashboard("Awaiting file-target confirmation.")
    return True


def _build_pending_stage_pause(
    orc,
    *,
    pause_type: str,
    stage: dict[str, Any],
    stage_index: int,
    stage_num: int,
    total_stages: int,
    stage_log: list[str],
    pause_status: str,
) -> dict[str, Any]:
    normalized_pause_type = str(pause_type or "").strip().lower() or "user_input"
    question = SummaryEngine.extract_proposal(stage_log)
    if not question:
        stage_goal_text = str(stage.get("stage_goal") or "").strip()
        if normalized_pause_type == "approval":
            if stage_goal_text:
                question = f"The next step is: {stage_goal_text}. Should I proceed?"
            else:
                question = "Please review the proposal and confirm whether I should continue."
        else:
            question = "Please provide the requested details so I can continue."
    approved_start_index = stage_index
    if normalized_pause_type == "approval" and stage_requires_user_approval(stage):
        # Proposal stages ("ask for approval") are skipped after approval;
        # destructive stages ("delete the file") are re-run after approval.
        if stage_is_explicit_proposal(stage):
            approved_start_index = stage_index + 1
        else:
            approved_start_index = stage_index
    payload = {
        "kind": "stage_pause",
        "pause_type": normalized_pause_type,
        "question": question,
        "stage_index": int(stage_index),
        "stage_num": int(stage_num),
        "total_stages": int(total_stages),
        "stage_type": str(stage.get("stage_type") or "").strip(),
        "stage_goal": str(stage.get("stage_goal") or "").strip(),
        "success_condition": str(stage.get("success_condition") or "").strip(),
        "status": str(pause_status or "").strip(),
        "stage": json.loads(json.dumps(dict(stage or {}), ensure_ascii=False)),
        "route_decision": _build_remaining_route_decision_for_target_confirmation(
            orc,
            stage_index=stage_index,
        ),
        "scratchpad_tail": [str(item) for item in stage_log[-6:]],
    }
    if normalized_pause_type == "approval":
        payload["approved_route_decision"] = _build_remaining_route_decision(
            orc,
            start_stage_index=approved_start_index,
        )
        payload["approval_resume_mode"] = "after_stage" if approved_start_index > stage_index else "current_stage"
    return payload


def _build_compound_file_sequence_final_state_reply(orc) -> str:
    entry = dict(getattr(orc, "last_change_journal_entry", {}) or {})
    if not entry or not bool(entry.get("task_success")):
        return ""

    operations = [dict(item) for item in (entry.get("operations") or []) if isinstance(item, dict)]
    if len(operations) < 4:
        return ""

    actions = [str(item.get("action") or "").strip().lower() for item in operations]
    if actions != ["write_text", "delete_path", "write_text", "delete_path"]:
        return ""

    primary_paths = [str(item).strip() for item in (entry.get("primary_paths") or []) if str(item).strip()]
    if len(primary_paths) != 1:
        return ""

    path = primary_paths[0]
    return f"Completed the requested file sequence. The final state is that `{path}` does not exist."


def _rewrite_undo_result_for_file_target_correction(
    orc,
    *,
    summary: str,
    detail: str,
) -> tuple[str, str]:
    notice = dict((getattr(orc, "route_decision", {}) or {}).get("system_notice") or {})
    if str(notice.get("kind") or "").strip().lower() != "file_target_correction":
        return summary, detail

    wrong_target = str(notice.get("wrong_target") or "").strip()
    correct_target = str(notice.get("correct_target") or "").strip()
    desired_state = str(notice.get("desired_state") or "").strip().lower()

    base_summary = f"Reverted the mistaken change to {wrong_target}."
    if not correct_target:
        return base_summary, detail or base_summary

    workspace = Path(getattr(orc.brain, "workspace", "."))
    correct_exists = (workspace / correct_target).exists()
    if desired_state == "absent":
        if not correct_exists:
            return (
                f"{base_summary} {correct_target} was already absent.",
                f"Restored {wrong_target} after your correction. The intended final state for {correct_target} was already satisfied.",
            )
        return (
            base_summary,
            f"Restored {wrong_target} after your correction. {correct_target} still exists, so the intended deleted final state is not yet satisfied.",
        )

    return (
        base_summary,
        f"Restored {wrong_target} because the previous action targeted the wrong file instead of {correct_target}.",
    )


def _merge_secretary_system_prompt(base_prompt: str, latest_runtime_context: str) -> str:
    prompt = str(base_prompt or "").strip()
    runtime = str(latest_runtime_context or "").strip()
    if not runtime:
        return prompt
    if runtime in prompt:
        return prompt
    if prompt:
        return prompt + "\n\n" + runtime
    return runtime


def _run_route_core(orc) -> None:
    """Core routing decision logic extracted from ``phase_route``.

    Performs the full ROUTE computation including interceptors, secretary LLM,
    normalization, skill-layer application, and ``next_stage`` selection.
    Side-effects on *orc* are expected (this is the legacy boundary).
    """
    full_history = orc.get_context()
    recent_history = full_history[-6:]
    latest_runtime_context = _latest_runtime_context_message(full_history)
    proactive_trigger = _latest_proactive_trigger_payload(recent_history)
    orc.user_msg = ""
    orc.synthetic_user_turn = False
    if proactive_trigger is not None:
        orc.user_msg = str(proactive_trigger.get("message") or "").strip()
        orc.synthetic_user_turn = True
    for message in reversed(recent_history):
        if orc.synthetic_user_turn:
            break
        if message.get("role") == "user":
            orc.user_msg = message.get("content", "")
            break
    fire_hooks("on_pre_route", orc, recent_history=recent_history)

    # Build router_history: exclude the current user turn (passed separately as
    # user_msg) so it is not duplicated in the JSON history block.  Also strip
    # "Thinking..." assistant placeholder entries — they add noise without signal.
    _current_skipped = False
    router_history = []
    for _msg in reversed(recent_history):
        if not _current_skipped and _msg.get("role") == "user":
            _current_skipped = True
            continue
        if _msg.get("role") == "assistant" and str(_msg.get("content", "")).strip() == "Thinking...":
            continue
        router_history.append(_msg)
    router_history.reverse()
    followup_history = _build_followup_resolution_history(
        full_history,
        current_user_msg=orc.user_msg,
    )

    orc.is_search_result = any(_is_pending_search_payload(message) for message in recent_history)

    if orc.is_search_result:
        orc.ui.put(("agent_log", "   -> Context implies Search Result. Skipping Secretary/router LLM."))
        if not str(getattr(orc.turn_stats, "decision", "") or "").strip():
            orc.stats_collector.note_route(orc.turn_stats, decision="SEARCH")
        orc.stats_collector.end_phase(orc.turn_stats, "route")
        orc.next_stage = "REPORTER"
        return

    if proactive_trigger is not None:
        local_fire_at = display_fire_at_local(str(proactive_trigger.get("fire_at") or ""))
        orc.route_interceptor = "PROACTIVE_TRIGGER"
        orc.route_decision = {
            "decision": "CHAT",
            "interceptor": "PROACTIVE_TRIGGER",
            "system_notice": {
                "kind": "proactive_trigger",
                "id": str(proactive_trigger.get("id") or "").strip(),
                "message": str(proactive_trigger.get("message") or "").strip(),
                "fire_at": str(proactive_trigger.get("fire_at") or "").strip(),
                "fire_at_local": local_fire_at,
                "raw_message": str(proactive_trigger.get("raw_message") or "").strip(),
            },
        }
        orc.ui.put(("agent_log", "   -> Proactive reminder trigger detected. Skipping Secretary/router LLM."))
        orc.stats_collector.note_route(
            orc.turn_stats,
            decision="CHAT",
            bypass="proactive_trigger",
        )
        orc.stats_collector.end_phase(orc.turn_stats, "route")
        orc.next_stage = "PERSONA"
        return

    route_interceptor = detect_route_interceptor(orc.user_msg, router_history)
    if route_interceptor is not None:
        interceptor_kind = str(route_interceptor.get("kind") or "").strip().upper()
        orc.route_interceptor = interceptor_kind
        interceptor_decision = dict(route_interceptor.get("route_decision") or {})
        if interceptor_decision:
            orc.route_decision = interceptor_decision
        elif str(route_interceptor.get("stats_decision") or "").strip():
            orc.route_decision = {"decision": str(route_interceptor.get("stats_decision") or "").strip().upper()}
        log_message = str(route_interceptor.get("log_message") or "").strip()
        if log_message:
            orc.ui.put(("agent_log", log_message))
        orc.stats_collector.note_route(
            orc.turn_stats,
            decision=str(route_interceptor.get("stats_decision") or (orc.route_decision.get("decision") if orc.route_decision else "") or "CHAT").strip().upper(),
            bypass=str(route_interceptor.get("bypass") or interceptor_kind.lower()).strip().lower(),
        )
        orc.stats_collector.end_phase(orc.turn_stats, "route")
        orc.next_stage = str(route_interceptor.get("next_stage") or interceptor_kind or "PERSONA").strip().upper()
        return

    if _is_live_environment_chat_query(orc.user_msg):
        orc.route_decision = {"decision": "CHAT", "card": {"query": orc.user_msg}}
        orc.ui.put(("agent_log", "   -> Live environment query. Skipping Secretary/router LLM and answering in PERSONA."))
        orc.stats_collector.note_route(
            orc.turn_stats,
            decision="CHAT",
            bypass="environment_query",
        )
        orc.stats_collector.end_phase(orc.turn_stats, "route")
        orc.next_stage = "PERSONA"
        return

    try:
        _opstate_answer = orc.prompt_context.build_readonly_state_answer(orc.user_msg)
    except Exception:
        _opstate_answer = ""
    if _opstate_answer:
        orc.route_decision = {"decision": "CHAT", "card": {"query": orc.user_msg}}
        orc.ui.put(("agent_log", "   -> Operational state query. Skipping Secretary/router LLM and answering in PERSONA."))
        orc.stats_collector.note_route(
            orc.turn_stats,
            decision="CHAT",
            bypass="operational_state_query",
        )
        orc.stats_collector.end_phase(orc.turn_stats, "route")
        orc.next_stage = "PERSONA"
        return

    orc.ingested_document_chat = False
    try:
        ingested_documents = orc.prompt_context.document_memory.list_documents()
    except Exception:
        ingested_documents = []
    if (
        not looks_like_explicit_browser_request(orc.user_msg)
        and _should_route_ingested_document_chat(orc.user_msg, recent_history, ingested_documents)
    ):
        orc.route_decision = {"decision": "CHAT"}
        orc.ingested_document_chat = True
        orc.ui.put(("agent_log", "   -> Ingested document chat heuristic matched. Skipping Secretary/router LLM."))
        orc.stats_collector.note_route(
            orc.turn_stats,
            decision="CHAT",
            bypass="ingested_document_chat",
        )
        orc.stats_collector.end_phase(orc.turn_stats, "route")
        orc.next_stage = "DOC_FOCUS"
        return

    live_screen_path = _resolve_live_screen_turn_image(orc)
    if _should_route_live_screen_visual_chat(orc.user_msg, live_screen_path=live_screen_path):
        orc.route_decision = {"decision": "CHAT"}
        orc.ui.put(("agent_log", "   -> Live screen visual query matched. Skipping Secretary/router LLM."))
        orc.stats_collector.note_route(
            orc.turn_stats,
            decision="CHAT",
            bypass="live_screen_visual_chat",
        )
        orc.stats_collector.end_phase(orc.turn_stats, "route")
        orc.next_stage = "PERSONA"
        return

    orc._update_status(mode="ROUTING")
    orc.ui.put(("agent_log", "--- PHASE 1.1: SECRETARY (Router LLM) ---"))

    prompt_path = CFG.PROMPTS_DIR / "secretary.txt"
    sys_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else FALLBACK_SECRETARY
    sys_prompt = _merge_secretary_system_prompt(sys_prompt, latest_runtime_context)
    # Keep speaker identity extraction in the Router, not Persona. This must
    # run even after a voice guess because explicit speech/text can correct a
    # low-confidence active profile.
    user_runtime = getattr(getattr(orc, "_cfg", None), "user_runtime", None)
    if user_runtime is not None:
        active_profile = user_runtime.active_profile()
        active_label = "UNKNOWN" if getattr(active_profile, "is_unknown", False) else f"{active_profile.name} [{active_profile.user_id}]"
        sys_prompt += (
            "\n\n## SPEAKER IDENTITY\n"
            f"The active user is currently {active_label}. "
            "If and only if the latest user message is clearly introducing who is speaking, "
            "include an identity_intent object in your JSON response, for example: "
            '{"identity_intent":{"is_introduction":true,"name":"Max","relation_to_admin":"friend","confidence":"high"}}. '
            "Also emit identity_intent when the latest user message explicitly corrects the active speaker, "
            "such as \"I'm not Baris, I'm Max\" or \"I'm Jim\" while another user is active. "
            "For correction phrases like \"I'm not British, I'm Jim\", use the asserted speaker name after the correction, not the negated description. "
            "Do not emit identity_intent for ordinary replies or status phrases like 'not bad', 'fine', 'okay', 'sure', or 'I am tired'."
        )
    messages = [{"role": "system", "content": sys_prompt}]
    messages.append(
        {
            "role": "user",
            "content": f"{orc.user_msg}\nHistory:\n{json.dumps(router_history, indent=2)}",
        }
    )

    try:
        orc.ui.put(("status", "Routing..."))
        if CFG.DEBUG_LLM_PROMPTS:
            log_prompt_debug(CFG.ROUTER_DEBUG_PATH, messages, "SECRETARY")
        if live_screen_path is not None:
            raw = generate_with_image_attachment(
                orc.llm,
                messages=messages,
                image_path=live_screen_path,
                attachment_text=_LIVE_SCREEN_ROUTER_ATTACHMENT,
                temperature=0.1,
                max_tokens=int(getattr(CFG, "ROUTER_MAX_TOKENS", 400)),
                cancel_token=orc.cancel_token,
            )
        else:
            raw = orc.llm.generate(
                messages,
                temperature=0.1,
                max_tokens=int(getattr(CFG, "ROUTER_MAX_TOKENS", 400)),
                cancel_token=orc.cancel_token,
            )
        orc.ui.put(("agent_log", f"   -> Secretary Raw: {raw}"))
        try:
            parsed: RouteDecision = RouterBoundary.validate(raw)
        except BoundaryValidationError as exc:
            parsed = exc.fallback or RouterBoundary.fallback()
            orc.ui.put(("agent_log", f"   -> Router validation failed: {exc}. Applying CHAT fallback."))
        identity_intent = dict((parsed or {}).get("identity_intent") or {})
        if (
            bool(identity_intent.get("is_introduction"))
            and user_runtime is not None
        ):
            identity_name = str(identity_intent.get("name") or "").strip()
            relation_hint = str(identity_intent.get("relation_to_admin") or "").strip()
            if identity_name:
                try:
                    result = user_runtime.apply_router_identity_intent(identity_name, relation_hint=relation_hint)
                    if getattr(result, "switched", False):
                        switched_profile = getattr(result, "profile", None)
                        switched_name = str(getattr(switched_profile, "name", "") or identity_name).strip() or identity_name
                        switched_user_id = str(getattr(switched_profile, "user_id", "") or "").strip()
                        role_label = ""
                        try:
                            role_label = str(user_runtime.profile_role_label(switched_profile) or "").strip()
                        except Exception:
                            role_label = ""
                        notice_parts = [
                            "[SPEAKER IDENTITY UPDATED]",
                            f"The runtime just identified the current speaker as {switched_name}"
                            + (f" [{switched_user_id}]" if switched_user_id else "")
                            + (f" ({role_label})" if role_label else "")
                            + ".",
                            "Acknowledge this naturally and continue answering the user's current message.",
                            "Do not say a new session started; the existing transcript is being carried forward under the identified user.",
                        ]
                        orc.identity_switch_notice = "\n".join(notice_parts)
                        orc.ui.put(("agent_log", f"   -> Router identity intent: {identity_name}. Switched user."))
                        orc.ui.put(("active_user_changed", {"preserve_transcript": True}))
                    elif getattr(result, "requires_password", False) or getattr(result, "requires_identity_clarification", False):
                        orc.ui.put(("chat_append", {"role": "system", "content": str(getattr(result, "message", "") or "")}))
                except Exception as exc:
                    orc.ui.put(("agent_log", f"   -> Identity switch from router failed: {exc}"))
        normalized = normalize_route_decision(parsed, orc.user_msg, router_history)
        followup_resolved = _resolve_followup_route_with_llm(orc, normalized, followup_history)
        if followup_resolved is not None and followup_resolved != normalized:
            normalized = followup_resolved
            orc.ui.put(("agent_log", "   -> Follow-up resolver refined ambiguous continuation route."))
        clarified = _refine_ambiguous_task_route_with_llm(orc, normalized, router_history)
        if clarified is not None and clarified != normalized:
            normalized = clarified
            orc.ui.put(("agent_log", "   -> Ambiguous task route converted into clarification pause."))
        normalized = annotate_file_stage_kinds(normalized)
        skilled = apply_route_skill_layer(
            normalized,
            orc.user_msg,
            router_history,
            enabled=bool(getattr(CFG, "SKILL_LAYER_ENABLED", True)),
        )
        orc.route_decision = normalized
        if skilled != normalized:
            orc.route_decision = skilled
            selected_skill = dict(skilled.get("skill") or {})
            skill_name = str(selected_skill.get("name") or "").strip()
            if skill_name:
                orc.ui.put(("agent_log", f"   -> Skill: {skill_name}"))
        if (
            str(orc.route_decision.get("decision") or "").strip().upper() == "SEARCH"
            and bool(getattr(orc, "is_search_in_flight", lambda: False)())
        ):
            requested_query = str(
                (orc.route_decision.get("card") or {}).get("query")
                or orc.user_msg
                or ""
            ).strip()
            active_query = str(getattr(orc, "current_search_query", lambda: "")() or "").strip()
            orc.route_decision = {
                "decision": "CHAT",
                "card": {"query": requested_query},
                "system_notice": {
                    "kind": "search_in_flight",
                    "active_query": active_query,
                    "requested_query": requested_query,
                },
            }
            orc.ui.put(("agent_log", "   -> Search already in flight. Redirecting duplicate SEARCH request to PERSONA."))
            orc._log_dashboard("Search already running.")
        if normalized != parsed:
            orc.ui.put(("agent_log", "   -> Normalized route decision for current runtime behavior."))
        orc.ui.put(("agent_log", f"   -> Route: {orc.route_decision.get('decision')}"))
        if orc.route_decision.get("decision") == "TASK":
            orc._log_dashboard("Task Mode")
    except OperationCancelled:
        raise
    except VisionError as exc:
        orc.latest_route_error = str(exc)
        orc.emit_runtime_signal(
            {
                "kind": "route_error",
                "severity": "warning",
                "source": "router",
                "summary": f"Live screen router error: {exc}",
                "details": str(exc),
            }
        )
        orc.ui.put(("agent_log", f"   -> Live Screen Router Error: {exc}"))
        orc.route_decision = {"decision": "CHAT"}
    except Exception as exc:
        orc.latest_route_error = str(exc)
        orc.emit_runtime_signal(
            {
                "kind": "route_error",
                "severity": "error",
                "source": "router",
                "summary": f"Secretary error: {exc}",
                "details": str(exc),
            }
        )
        orc.ui.put(("agent_log", f"   -> Secretary Error: {exc}"))
        orc.route_decision = {"decision": "CHAT"}

    orc.stats_collector.note_route(
        orc.turn_stats,
        decision=str(orc.route_decision.get("decision") or "").strip().upper(),
        source_scope=str(orc.route_decision.get("source_scope") or "").strip().lower(),
        confidence=str(orc.route_decision.get("confidence") or "").strip().lower(),
        search_query=str(((orc.route_decision.get("card") or {}).get("query") or "")).strip(),
        latest_route_error=getattr(orc, "latest_route_error", ""),
    )
    orc.stats_collector.end_phase(orc.turn_stats, "route")
    decision = orc.route_decision.get("decision")
    # --- Privacy model: non-admin route guard ---
    _apply_non_admin_route_guard(orc)
    decision = orc.route_decision.get("decision")
    # If Secretary produced a bare CHAT with no stage instructions for a very
    # short/vague message, wrap it so the Planner/Persona asks for clarification
    # instead of treating it as casual chat (which leads to snarky responses).
    if decision == "CHAT":
        card = dict(orc.route_decision.get("card") or {})
        has_clarify_stage = any(
            str(s.get("stage_type") or "").upper() == "CHAT"
            and "clarif" in str(s.get("stage_goal") or "").lower()
            for s in (card.get("stages") or [])
        )
        if not has_clarify_stage:
            user_msg = str(getattr(orc, "user_msg", "") or "").strip()
            tokens = re.findall(r"[a-z0-9']+", user_msg.lower())
            if len(tokens) <= 5 and not _PATHISH_RE.search(user_msg):
                card["goal"] = "Clarify the meaning of the user's message"
                card["stages"] = [
                    {
                        "stage_type": "CHAT",
                        "stage_goal": f"Ask the user to clarify what they mean by: {user_msg}",
                        "success_condition": "User clarifies or rephrases",
                    }
                ]
                orc.route_decision["card"] = card
                orc.ui.put(("agent_log", "   -> Wrapped vague CHAT with clarification stage."))
    if decision == "SEARCH":
        orc.next_stage = "SEARCH"
    elif decision == "TASK":
        orc.next_stage = "MANAGER"
    else:
        orc.next_stage = "PERSONA"


def phase_route(orc) -> None:
    orc.raise_if_cancelled()
    orc._update_status(mode="ANALYZING")
    orc.ui.put(("agent_log", "--- PHASE 1: ROUTE CHECK ---"))
    orc.latest_route_error = ""
    orc.stats_collector.start_phase(orc.turn_stats, "route")
    _run_route_core(orc)

def phase_document_focus(orc) -> None:
    orc.raise_if_cancelled()
    orc.ui.put(("agent_log", "--- PHASE 1.5: DOCUMENT FOCUS ---"))
    orc.ui.put(("status", "Condensing document context..."))
    orc._log_dashboard("Document query detected.")

    try:
        hits = orc.prompt_context.document_memory.render_prompt_hits(
            orc.user_msg,
            limit=3,
            excerpt_chars=1400,
        )
    except Exception as exc:
        orc.ui.put(("agent_log", f"   -> Document focus skipped: {exc}"))
        orc.next_stage = "PERSONA"
        return

    if not hits:
        orc.ui.put(("agent_log", "   -> No ingested document hits available for focus."))
        orc.next_stage = "PERSONA"
        return

    messages = build_document_focus_messages(orc.user_msg, hits)
    if CFG.DEBUG_LLM_PROMPTS:
        log_prompt_debug(CFG.DOC_FOCUS_DEBUG_PATH, messages, "DOCUMENT_FOCUS")

    try:
        result = extract_document_focus(
            llm_client=orc.llm,
            query=orc.user_msg,
            document_hits=hits,
            cancel_token=orc.cancel_token,
        )
    except OperationCancelled:
        raise
    except Exception as exc:
        orc.ui.put(("agent_log", f"   -> Document focus failed: {exc}"))
        orc.next_stage = "PERSONA"
        return

    orc.document_focus_text = str(result.relevant_info or "").strip()
    orc.document_focus_refs = list(result.references or [])
    orc.document_focus_sources = list(result.source_names or [])

    if orc.document_focus_sources:
        orc._log_dashboard("Document source: " + ", ".join(orc.document_focus_sources[:2]))
    if orc.document_focus_refs:
        orc._log_dashboard("Document refs: " + " | ".join(orc.document_focus_refs))
    if result.visual_pages:
        orc._log_dashboard("Document visual pages: " + " | ".join(result.visual_pages))
    if result.used_visual_fallback:
        orc._log_dashboard("Document vision fallback used.")

    if orc.document_focus_text:
        orc.ui.put(("agent_log", "   -> Document focus ready."))
    else:
        orc.ui.put(("agent_log", "   -> Document focus found no grounded answer in the supplied document context."))

    orc.next_stage = "PERSONA"


def phase_search(orc) -> None:
    orc.raise_if_cancelled()
    orc.stats_collector.start_phase(orc.turn_stats, "persona")
    query = orc.route_decision.get("card", {}).get("query", orc.user_msg)
    orc.stats_collector.note_route(
        orc.turn_stats,
        decision="SEARCH",
        search_query=str(query or "").strip(),
    )
    if _is_live_environment_chat_query(query):
        orc.route_decision = {"decision": "CHAT", "card": {"query": query}}
        orc.ui.put(("agent_log", "   -> Search route downgraded to CHAT for live environment query."))
        orc.stats_collector.note_route(
            orc.turn_stats,
            decision="CHAT",
            bypass="environment_query",
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.next_stage = "PERSONA"
        return
    orc.ui.put(("agent_log", f"--- ROUTER: Triggering Background Search for '{query}' ---"))

    prompt_pack = orc.prompt_context.build_persona_pack(
        user_msg=orc.user_msg,
        style_overlay=orc.ss.overlay or "",
        knowledge_enabled=orc.knowledge_enabled,
        brain_limit=9,
        document_limit=0,
    )
    prompt_pack = orc.prompt_context.apply_context_arbitration(
        prompt_pack,
        route_decision=orc.route_decision,
        reporter_just_ran=False,
    )
    prompt_context = orc.prompt_context.to_prompt_context(prompt_pack)
    system_content = PromptBuilder.build_persona_prompt(prompt_context)
    history = _build_search_preview_history(orc.user_msg, query)
    speak_messages = build_persona_messages(
        system_content=system_content,
        history=history,
        tail_system_content=_build_search_first_pass_rule(query),
        model_path=getattr(CFG, "MODEL_PATH", None),
    )
    if CFG.DEBUG_LLM_PROMPTS:
        log_prompt_debug(CFG.PERSONA_DEBUG_PATH, speak_messages, "SEARCH_FIRST_PASS")

    fallback_text = _build_search_first_pass_fallback(query)
    full_answer = ""
    if _SEARCH_RECENCY_HINT_RE.search(str(query or "")):
        _emit_fallback_assistant_answer(orc, fallback_text)
    else:
        orc.ui.put(("assistant_stream_start", {"tts_voice": orc.ss.tts_voice, "tts_speed": orc.ss.tts_speed}))
        try:
            full_answer, _ = _stream_or_capture_persona_answer_text_only(
                orc,
                speak_messages,
                allow_recall=False,
            )
        except OperationCancelled:
            orc.ui.put(("assistant_stream_end", ""))
            raise
        except LLMClientError as exc:
            orc.emit_runtime_signal(
                {
                    "kind": "search_preview_error",
                    "severity": "warning",
                    "source": "search",
                    "summary": f"Search first-pass error: {exc}",
                    "details": str(exc),
                }
            )
            orc.ui.put(("agent_log", f"   -> Search first-pass error: {exc}"))
        except Exception as exc:
            orc.ui.put(("agent_log", f"   -> Search first-pass fallback: {exc}"))

        clean_answer = sanitize_persona_output(
            _strip_persona_control_tags(full_answer),
            route_decision=orc.route_decision,
            outcome_block="",
            user_msg=orc.user_msg,
        )
        if clean_answer:
            orc.ui.put(("assistant_stream_end", ""))
            if clean_answer != full_answer.strip():
                orc.chat.replace_last_assistant_content(clean_answer)
        else:
            orc.ui.put(("agent_log", "   -> Search first-pass reply unavailable. Using brief fallback acknowledgment."))
            _emit_fallback_assistant_answer(orc, fallback_text)

    orc.stats_collector.end_phase(orc.turn_stats, "persona")
    orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))

    from tools.search import perform_search

    if orc.cancel_token is not None:
        orc.retain_cancel_token(orc.cancel_token)
    orc.retain_search_in_flight(query)

    def _do_search() -> None:
        queued_result = False
        try:
            orc.raise_if_cancelled()
            data = perform_search(
                query,
                CFG.DATA_DIR,
                log_callback=orc._log_dashboard,
                cancel_token=orc.cancel_token,
            )
            if is_search_error_result(data):
                raise RuntimeError(normalize_search_error(data))
            orc.raise_if_cancelled()
            orc.ui.put(("search_result", {"query": query, "data": data, "cancel_token": orc.cancel_token}))
            queued_result = True
        except OperationCancelled:
            orc._log_dashboard("Search canceled.")
        except Exception as exc:
            orc.emit_runtime_signal(
                {
                    "kind": "search_error",
                    "severity": "error",
                    "source": "search",
                    "summary": f"Search error: {exc}",
                    "details": str(exc),
                }
            )
            error_data = f"Search Error: {exc}"
            orc.ui.put(
                (
                    "search_result",
                    {
                        "query": query,
                        "data": error_data,
                        "error": True,
                        "cancel_token": orc.cancel_token,
                    },
                )
            )
            queued_result = True
            orc._log_dashboard(error_data)
        finally:
            orc.release_search_in_flight()
            if orc.cancel_token is not None:
                orc.release_cancel_token(orc.cancel_token)
            if not queued_result:
                orc.ui.put(("status", "Canceled" if orc.cancel_token and orc.cancel_token.is_cancelled else "IDLE"))

    try:
        worker = threading.Thread(target=_do_search, daemon=True)
        worker.start()
    except Exception:
        orc.release_search_in_flight()
        if orc.cancel_token is not None:
            orc.release_cancel_token(orc.cancel_token)
        raise
    orc.stats_collector.defer_search_turn(
        orc.turn_stats,
        cancel_token=orc.cancel_token,
        fallback_owner=orc.chat,
    )
    orc.next_stage = "FINISHED"


def _resolve_followup_route_with_llm(
    orc,
    decision: RouteDecision,
    router_history: list[dict[str, Any]],
) -> RouteDecision | None:
    try:
        return _FOLLOWUP_RESOLUTION_ENGINE.refine_with_llm(
            llm=orc.llm,
            decision=decision,
            user_msg=str(getattr(orc, "user_msg", "") or ""),
            recent_history=router_history,
            operational_state_service=getattr(orc.prompt_context, "operational_state_service", None),
            knowledge_mgr=getattr(orc.prompt_context, "knowledge_mgr", None),
            cancel_token=getattr(orc, "cancel_token", None),
        )
    except BoundaryValidationError as exc:
        orc.ui.put(("agent_log", f"   -> Follow-up resolver validation failed: {exc}."))
        return exc.fallback
    except OperationCancelled:
        raise
    except Exception as exc:
        orc.ui.put(("agent_log", f"   -> Follow-up resolver error: {exc}"))
        return None


def _refine_ambiguous_task_route_with_llm(
    orc,
    decision: RouteDecision,
    router_history: list[dict[str, Any]],
) -> RouteDecision | None:
    try:
        return _ROUTE_CLARIFIER.refine_with_llm(
            llm=orc.llm,
            decision=decision,
            user_msg=str(getattr(orc, "user_msg", "") or ""),
            recent_history=router_history,
            cancel_token=getattr(orc, "cancel_token", None),
        )
    except BoundaryValidationError as exc:
        orc.ui.put(("agent_log", f"   -> Route clarifier validation failed: {exc}."))
        return exc.fallback
    except OperationCancelled:
        raise
    except Exception as exc:
        orc.ui.put(("agent_log", f"   -> Route clarifier error: {exc}"))
        return None


def phase_reporter(orc) -> None:
    orc.raise_if_cancelled()
    orc.ui.put(("agent_log", "   -> Search Result Detected. Activating Reporter Layer."))
    orc.stats_collector.start_phase(orc.turn_stats, "reporter")

    recent_history = orc.get_context()[-6:]
    raw_content = ""
    instruction_content = ""
    for message in reversed(recent_history):
        if _is_pending_search_payload(message):
            raw_content = message.get("content", "")
            break

    for message in reversed(recent_history):
        if _is_search_reporter_instruction(message):
            instruction_content = message.get("content", "")
            break

    payload = parse_background_search_content(raw_content)
    query = payload.query
    data = payload.data
    search_failed = bool(payload.failed)

    orc.stats_collector.note_reporter_query(orc.turn_stats, query)
    orc.latest_search_failed = search_failed
    orc.latest_search_error = normalize_search_error(data) if search_failed else ""

    orc.ui.put(("status", "Analyzing Search Results..."))
    if search_failed:
        summary = _build_search_failure_summary(query, data)
        orc.ui.put(("agent_log", f"   -> Search failed: {normalize_search_error(data)[:100]}..."))
        orc.stats_collector.finalize_outcome(
            orc.turn_stats,
            outcome="FAILED",
            detail=f"Search failed: {normalize_search_error(data)[:500]}",
        )
    else:
        reporter_path = CFG.PROMPTS_DIR / "reporter.txt"
        sys_template = reporter_path.read_text(encoding="utf-8") if reporter_path.exists() else "Summarize this."
        sys_prompt = sys_template.replace("{query}", query).replace("{data}", data)
        try:
            reporter_messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"Summarize the search findings for '{query}' using the instructions above."},
            ]
            summary = orc.llm.generate(
                reporter_messages,
                temperature=0.1,
                max_tokens=int(getattr(CFG, "REPORTER_MAX_TOKENS", 700)),
                cancel_token=orc.cancel_token,
            )
            orc.ui.put(("agent_log", f"   -> Reporter Summary: {summary[:100]}..."))
        except OperationCancelled:
            raise
        except Exception as exc:
            orc.emit_runtime_signal(
                {
                    "kind": "runtime_error",
                    "severity": "warning",
                    "source": "reporter",
                    "summary": f"Reporter error: {exc}",
                    "details": str(exc),
                }
            )
            orc.ui.put(("agent_log", f"   -> Reporter Error: {exc}"))
            summary = data

    new_msg = {"role": "system", "content": f"[SEARCH SUMMARY FOR '{query}']\n{summary}", "hidden": True}
    if raw_content:
        orc.chat.replace_last_system_message(raw_content, new_msg)
    if instruction_content:
        consumed_msg = {"role": "system", "content": f"[SEARCH REPORT CONSUMED FOR '{query}']", "hidden": True}
        orc.chat.replace_last_system_message(instruction_content, consumed_msg)
    orc.latest_search_summary = summary
    orc.reporter_just_ran = True
    orc.stats_collector.end_phase(orc.turn_stats, "reporter")
    orc.next_stage = "PERSONA"

def _run_manager_core(orc) -> None:
    """Core manager execution logic extracted from ``phase_manager``.

    Runs all stages from the TASK card through ``StageExecutor``, handles
    pauses, failures, auto-reroutes, and sets ``next_stage``.
    Side-effects on *orc* are expected (this is the legacy boundary).
    """
    orc.context_card = orc.route_decision.get("card", {})
    orc.pending_file_target_confirmation = None
    orc.pending_stage_pause = None
    orc._update_status(mode="PLANNING", goal=orc.context_card.get("goal", "Unknown"))
    orc.ui.put(("agent_log", "--- PHASE 2: EXECUTIVE LOOP (Task) ---"))
    orc.stats_collector.start_phase(orc.turn_stats, "manager")

    stages = orc.context_card.get("stages", [])
    # Privacy model: filter admin-only tools for non-admin users (defense-in-depth).
    _user_runtime = getattr(getattr(orc, "_cfg", None), "user_runtime", None)
    _is_non_admin = (
        _user_runtime is not None
        and not getattr(_user_runtime.active_profile(), "is_admin", False)
    )
    if _is_non_admin:
        _ADMIN_ONLY_TOOLS = {"FILE_OP", "RUN_CODE"}
        for _stage in stages:
            _orig = list(_stage.get("allowed_tools", []) or [])
            _filtered = [t for t in _orig if t not in _ADMIN_ONLY_TOOLS]
            if _filtered != _orig:
                _stage["allowed_tools"] = _filtered
                orc.ui.put(("agent_log", f"   -> Privacy guard removed admin-only tools from stage: {_orig} -> {_filtered}"))
    if not stages:
        orc.emit_runtime_signal(
            {
                "kind": "runtime_error",
                "severity": "error",
                "source": "manager",
                "summary": "TASK card had no stages.",
                "details": str(orc.context_card),
            }
        )
        orc.ui.put(("error", "TASK card received with no stages. Falling back to CHAT."))
        orc.stats_collector.note_route(orc.turn_stats, decision="TASK")
        orc.stats_collector.end_phase(orc.turn_stats, "manager")
        orc.next_stage = "PERSONA"
        return

    executor = StageExecutor(
        orc.llm,
        orc.brain,
        orc.img_gen,
        orc.boot,
        orc.ui,
        cancel_token=orc.cancel_token,
        signal_emitter=lambda signal: orc.emit_runtime_signal(signal, scratchpad=executor.scratchpad),
        stats_collector=orc.stats_collector,
        operational_state_service=getattr(orc.prompt_context, "operational_state_service", None),
    )
    executor._current_turn_id = str(getattr(getattr(orc, "turn_stats", None), "turn_id", "") or "")
    # Capture the parent objective once so every stage can reference it.
    objective = str(orc.context_card.get("goal", "") or "").strip()

    total_stages = len(stages)
    completed_change_operations: list[dict[str, Any]] = []
    completed_rollback_manifests: list[str] = []
    task_failed = False
    task_paused = False
    completed_all_stages = False
    for index, stage in enumerate(stages):
        orc.raise_if_cancelled()
        stage = dict(stage)
        if orc.context_card.get("context") and "context" not in stage:
            stage["context"] = list(orc.context_card.get("context") or [])
        # Inject the parent objective into the stage card so PlannerBoundary
        # can surface it without the executor needing a separate parameter.
        if "objective" not in stage or not stage.get("objective"):
            stage["objective"] = objective
        stage_num = index + 1
        orc.ui.put(("agent_log", f"=== STARTING STAGE {stage_num}/{total_stages}: {stage.get('stage_goal')} ==="))
        needs_user_input = stage_requires_user_input(stage)
        needs_user_approval = stage_requires_user_approval(stage)

        # Destructive stages that require approval pause before the executor runs.
        # Proposal stages run the executor first (for inspection/proposal) then pause.
        if needs_user_approval and not stage.get("approved") and not stage_is_explicit_proposal(stage):
            orc.pending_stage_pause = _build_pending_stage_pause(
                orc,
                pause_type="approval",
                stage=stage,
                stage_index=index,
                stage_num=stage_num,
                total_stages=total_stages,
                stage_log=[],
                pause_status="PAUSED / AWAITING USER APPROVAL",
            )
            orc.ui.put(("agent_log", f"   -> Stage {stage_num} is a destructive action. Awaiting user approval before execution."))
            orc._log_dashboard(f"Stage {stage_num} awaiting approval.")
            orc.scratchpad.append(f"=== STAGE {stage_num} PAUSED ===\nAwaiting user approval for: {stage.get('stage_goal', '')}")
            task_paused = True
            break

        success, stage_log = executor.run(stage, stage_num, total_stages)
        completed_change_operations.extend(
            [dict(item) for item in getattr(executor, "completed_change_operations", []) if isinstance(item, dict)]
        )
        completed_rollback_manifests.extend(
            [str(p) for p in getattr(executor, "completed_rollback_manifests", []) if str(p).strip()]
        )
        # Surface the typed VerificationResult so phase_persona uses the
        # authoritative verdict rather than re-inferring from scratchpad text.
        orc.last_verification = getattr(executor, "_last_verification", None)
        if (
            not success
            and FileStagePolicy.stage_requires_analysis_report(stage)
            and bool(orc.prompt_context.extract_latest_stage_proposal_answer(stage_log))
            and bool(orc.prompt_context.extract_exact_file_read_answer(stage_log))
        ):
            success = True
        if (
            not success
            and _maybe_pause_for_missing_file_target_confirmation(
                orc,
                executor,
                stage=stage,
                stage_index=index,
                stage_log=stage_log,
            )
        ):
            success = True
        if executor.pause_requested:
            if getattr(executor, "pause_mode", "") == "user_input":
                needs_user_input = True
                needs_user_approval = False
            elif getattr(executor, "pause_mode", "") == "approval":
                needs_user_input = False
                needs_user_approval = True
            elif not needs_user_input:
                needs_user_approval = True

        orc.scratchpad.extend(stage_log)
        steps_executed = _scratchpad_steps_executed(stage_log)
        last_entry = stage_log[-1] if stage_log else ""
        is_error_state = _scratchpad_entry_indicates_error(last_entry)
        base_success = success and not is_error_state and steps_executed

        if base_success and needs_user_input:
            pause_status = "PAUSED / AWAITING USER INPUT"
        elif base_success and needs_user_approval:
            pause_status = "PAUSED / AWAITING USER APPROVAL"
        else:
            pause_status = ""

        outcome_pack = ScratchpadFormatter.build_outcome_pack(
            success=base_success,
            stage_type=stage.get("stage_type", "UNKNOWN"),
            last_observation=last_entry,
            status_override=pause_status,
            stage_entries=stage_log,
            stage=stage,
        )
        orc.last_stage_outcome = outcome_pack
        true_success = outcome_pack.effective_success
        verification_verdict = str(getattr(getattr(orc, "last_verification", None), "verdict", "") or "").strip().upper()
        if verification_verdict not in {"VERIFIED", "PARTIAL", "FAILED"}:
            if true_success and pause_status:
                verification_verdict = "PARTIAL"
            elif true_success:
                verification_verdict = "VERIFIED"
            else:
                verification_verdict = "FAILED"
        stage_metrics = dict(getattr(executor, "_last_stage_metrics", {}) or {})
        orc.stats_collector.add_stage(
            orc.turn_stats,
            index=stage_num,
            stage=stage,
            planner_ms=float(stage_metrics.get("planner_ms") or 0.0),
            executor_ms=float(stage_metrics.get("executor_ms") or 0.0),
            total_ms=float(stage_metrics.get("stage_total_ms") or 0.0),
            verification=verification_verdict,
            status=str(getattr(outcome_pack, "status", "") or pause_status or ""),
            effective_success=bool(true_success),
            step_count=int(stage_metrics.get("step_count") or 0),
            action_count=int(stage_metrics.get("action_count") or 0),
            timeout_hit=bool(stage_metrics.get("timeout_hit")),
            action_budget_hit=bool(stage_metrics.get("action_budget_hit")),
        )
        outcome_text = ScratchpadFormatter.format_outcome(
            stage_num,
            true_success,
            stage.get("stage_type", "UNKNOWN"),
            last_entry,
            status_override=pause_status if true_success else "",
            stage_entries=stage_log,
        )
        orc.scratchpad.append(outcome_text)

        if true_success:
            if needs_user_input:
                if not getattr(orc, "pending_file_target_confirmation", None):
                    orc.pending_stage_pause = _build_pending_stage_pause(
                        orc,
                        pause_type="user_input",
                        stage=stage,
                        stage_index=index,
                        stage_num=stage_num,
                        total_stages=total_stages,
                        stage_log=stage_log,
                        pause_status=pause_status,
                    )
                orc.ui.put(("agent_log", f"   -> Stage {stage_num} Ready. Awaiting user input."))
                orc._log_dashboard(f"Stage {stage_num} awaiting user input.")
                task_paused = True
                break
            if needs_user_approval:
                orc.pending_stage_pause = _build_pending_stage_pause(
                    orc,
                    pause_type="approval",
                    stage=stage,
                    stage_index=index,
                    stage_num=stage_num,
                    total_stages=total_stages,
                    stage_log=stage_log,
                    pause_status=pause_status,
                )
                orc.ui.put(("agent_log", f"   -> Stage {stage_num} Ready. Awaiting user approval before execution."))
                orc._log_dashboard(f"Stage {stage_num} awaiting approval.")
                task_paused = True
                break
            orc.ui.put(("agent_log", f"   -> Stage {stage_num} Complete."))
            orc._log_dashboard(f"Stage {stage_num} Success.")
            if stage_num == total_stages:
                completed_all_stages = True
        else:
            task_failed = True
            if bool(getattr(outcome_pack, "auto_reroute", False)) and int(getattr(orc, "failed_task_router_retries", 0) or 0) < 1:
                orc.failed_task_router_retries = int(getattr(orc, "failed_task_router_retries", 0) or 0) + 1
                _hook_upsert_runtime_context(orc, reporter_just_ran=False)
                reason = str(getattr(outcome_pack, "reroute_reason", "") or "").strip()
                if reason:
                    orc.ui.put(("agent_log", f"   -> Auto-rerouting after failed stage: {reason}"))
                else:
                    orc.ui.put(("agent_log", "   -> Auto-rerouting after failed stage to re-evaluate intent."))
                orc._log_dashboard(f"Stage {stage_num} rerouting.")
                orc.stats_collector.end_phase(orc.turn_stats, "manager")
                orc.next_stage = "ROUTE"
                return
            orc.ui.put(("agent_log", f"   -> Stage {stage_num} Failed/Errors."))
            orc._log_dashboard(f"Stage {stage_num} Failed.")
            break

    orc.last_change_journal_entry = None
    orc.undo_notice_pending = False
    fire_hooks(
        "on_task_verified",
        orc,
        completed_change_operations=completed_change_operations,
        completed_rollback_manifests=completed_rollback_manifests,
        completed_all_stages=completed_all_stages,
        task_failed=task_failed,
        task_paused=task_paused,
    )

    orc.stats_collector.note_route(orc.turn_stats, decision="TASK")
    orc.stats_collector.end_phase(orc.turn_stats, "manager")
    orc.next_stage = "PERSONA"


def phase_manager(orc) -> None:
    orc.raise_if_cancelled()
    _run_manager_core(orc)


def phase_undo(orc) -> None:
    orc.raise_if_cancelled()
    orc.context_card = dict((orc.route_decision or {}).get("card") or {})
    stage = {}
    stages = orc.context_card.get("stages") or []
    if stages and isinstance(stages[0], dict):
        stage = dict(stages[0])
    if not stage:
        stage = {
            "stage_goal": "Undo the most recent mutating file task.",
            "stage_type": "FILE_WORK",
            "success_condition": "The latest recorded reversible file changes are restored.",
            "file_stage_kind": "CONTENT_EDIT",
        }
    orc._update_status(mode="PLANNING", goal=orc.context_card.get("goal", "Undo the last file task"))
    orc.ui.put(("agent_log", "--- PHASE 2: UNDO ---"))
    orc.stats_collector.start_phase(orc.turn_stats, "manager")
    started_at = time.perf_counter()
    orc.last_verification = None
    orc.undo_notice_pending = False
    orc.scratchpad = [ScratchpadFormatter.format_stage_header(1, stage)]

    workspace = Path(getattr(orc.brain, "workspace", "."))
    # If the latest journal entry has a rollback manifest (written for bulk
    # ops like consolidate_by_extension or move_many), use the manifest-based
    # inversion path.  It inverts every move in the recipe mechanically
    # instead of restoring binary file-content snapshots.
    latest_entry = orc.change_journal.peek_latest_entry()
    manifest_paths = [
        str(p) for p in (latest_entry or {}).get("rollback_manifests") or []
        if str(p).strip()
    ] if latest_entry and not str(latest_entry.get("undone_at") or "").strip() else []

    if manifest_paths:
        result = invert_rollback_manifest(Path(manifest_paths[-1]), workspace)
        if result.get("status") in ("VERIFIED", "PARTIAL"):
            # Mark the journal entry as undone so a second undo attempt is
            # correctly refused.
            orc.change_journal.mark_entry_undone(
                str(latest_entry.get("turn_id") or ""),
                status=str(result.get("status") or "VERIFIED"),
                detail=str(result.get("detail") or ""),
            )
    else:
        result = orc.change_journal.undo_latest(workspace)
    summary = str(result.get("summary") or "").strip()
    detail = str(result.get("detail") or summary).strip()
    status = str(result.get("status") or "FAILED").strip().upper()
    paths = [str(item).strip() for item in (result.get("paths") or []) if str(item).strip()]
    if status == "VERIFIED":
        summary, detail = _rewrite_undo_result_for_file_target_correction(
            orc,
            summary=summary,
            detail=detail,
        )
    observation = {
        "tool": "UNDO",
        "status": status,
        "summary": summary,
        "action": "undo_last_task",
        "evidence_files": paths[:6],
        "workspace_changed": bool(result.get("workspace_changed")),
    }
    orc.scratchpad.append(
        ScratchpadFormatter.format_step(
            1,
            "Revert the latest recorded mutating file task.",
            "[UNDO]",
            observation,
        )
    )

    effective_success = status == "VERIFIED"
    if effective_success:
        payload = {
            "kind": "mutation_verified",
            "tool": "UNDO",
            "action": "undo",
            "paths": paths[:6],
            "summary": summary,
            "reason": detail,
        }
        orc.scratchpad.append("FILE_WORK_VERIFIED_RESULT: " + json.dumps(payload, ensure_ascii=False))
        outcome_pack = StageOutcomePack(
            status="FILE OPERATION SUCCESS",
            detail=detail,
            effective_success=True,
            allow_persona_reroute=True,
        )
    else:
        outcome_pack = StageOutcomePack(
            status="FAILED / INCOMPLETE",
            detail=detail,
            effective_success=False,
            allow_persona_reroute=False,
        )
    orc.last_stage_outcome = outcome_pack

    outcome_text = f"=== STAGE 1 OUTCOME ===\nRESULT: {outcome_pack.status}"
    if detail:
        outcome_text += f"\nLAST_LOG: {detail}"
    orc.scratchpad.append(outcome_text)

    elapsed_ms = round(max(0.0, (time.perf_counter() - started_at) * 1000.0), 3)
    orc.stats_collector.add_stage(
        orc.turn_stats,
        index=1,
        stage=stage,
        planner_ms=0.0,
        executor_ms=elapsed_ms,
        total_ms=elapsed_ms,
        verification="VERIFIED" if effective_success else ("PARTIAL" if status == "PARTIAL" else "FAILED"),
        status=str(outcome_pack.status or ""),
        effective_success=bool(effective_success),
    )
    orc.stats_collector.note_route(orc.turn_stats, decision="TASK")
    orc.stats_collector.end_phase(orc.turn_stats, "manager")
    orc.next_stage = "PERSONA"


def phase_reminder_set(orc) -> None:
    orc.raise_if_cancelled()
    orc.context_card = {}
    orc._update_status(mode="PLANNING", goal="Set reminder")
    orc.ui.put(("agent_log", "--- PHASE 2: REMINDER SET ---"))
    orc.stats_collector.start_phase(orc.turn_stats, "manager")

    parsed = parse_reminder_request(orc.user_msg)
    if parsed.ok:
        entry = ReminderStore(CFG.REMINDERS_PATH).add(
            message=parsed.message,
            fire_at_utc=parsed.fire_at_utc,
        )
        orc.route_decision = {
            "decision": "CHAT",
            "interceptor": "REMINDER_SET",
            "system_notice": {
                "kind": "reminder_set_result",
                "status": "scheduled",
                "id": str(entry.get("id") or "").strip(),
                "message": str(entry.get("message") or "").strip(),
                "fire_at": str(entry.get("fire_at") or "").strip(),
                "fire_at_local": parsed.fire_at_local,
            },
        }
        orc.ui.put(("agent_log", f"   -> Reminder stored for {parsed.fire_at_local}."))
    else:
        orc.route_decision = {
            "decision": "CHAT",
            "interceptor": "REMINDER_SET",
            "system_notice": {
                "kind": "reminder_set_result",
                "status": "error",
                "error": str(parsed.error or "I couldn't resolve the reminder timing.").strip(),
            },
        }
        orc.ui.put(("agent_log", f"   -> Reminder not set: {parsed.error}"))

    orc.stats_collector.note_route(orc.turn_stats, decision="CHAT")
    orc.stats_collector.end_phase(orc.turn_stats, "manager")
    orc.next_stage = "PERSONA"


def _emit_fallback_assistant_answer(orc, text: str) -> None:
    fallback = (text or "").strip()
    if not fallback:
        return
    orc.ui.put(("assistant_stream_start", {"tts_voice": orc.ss.tts_voice, "tts_speed": orc.ss.tts_speed}))
    orc.ui.put(("assistant_stream_delta", {"text": fallback}))
    orc.ui.put(("assistant_stream_end", ""))


def _wants_user_confirmation(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    if "?" in cleaned:
        return True
    return any(phrase in cleaned for phrase in _CONFIRMATION_PHRASES)


def _extract_recall_query(text: str) -> str:
    match = _RECALL_TAG_RE.search(str(text or ""))
    return match.group(1).strip() if match else ""


def _strip_persona_control_tags(text: str) -> str:
    cleaned = _RECALL_TAG_RE.sub("", str(text or ""))
    cleaned = cleaned.replace("[ROUTER]", "")
    cleaned = re.sub(
        r"\[(?:ACTIVE_SKILL|LATEST_SYSTEM_EVENT|FINAL_STAGE_OUTCOME|NO_MUTATION_RULE|DOCUMENT_QA_RULE|FILE_WORK_REPORT_RULE|SEARCH_REPORT_RULE|SEARCH_FIRST_PASS_RULE|PROACTIVE_TRIGGER|REMINDER_SET_RESULT|EXPLAIN_LAST_TURN|CONTEXT_ARBITRATION_RULE)\]",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _render_recall_block(query: str, hits: list[dict]) -> str:
    lines = [f"[RECALL RESULT FOR '{query}']"]
    if not hits:
        lines.append("No matching recalled memories.")
        return "\n".join(lines)
    for hit in hits[:5]:
        text = str(hit.get("text") or "").strip()
        meta = hit.get("metadata", {}) or {}
        age_label = PromptBuilder._format_memory_age_label(meta)
        lines.append(f"- {text} [{age_label}]")
    return "\n".join(lines)


def _persona_recall_allowed(
    orc,
    *,
    reporter_just_ran: bool,
    explain_last_turn: bool,
) -> bool:
    if reporter_just_ran or explain_last_turn:
        return False
    if bool(getattr(orc, "ingested_document_chat", False)):
        return False
    if str(getattr(orc, "document_focus_text", "") or "").strip():
        return False
    decision = str((getattr(orc, "route_decision", {}) or {}).get("decision") or "").strip().upper()
    if decision != "CHAT":
        return False
    user_runtime = getattr(getattr(orc, "_cfg", None), "user_runtime", None)
    if user_runtime is not None:
        try:
            if getattr(user_runtime.active_profile(), "is_unknown", False):
                return False
        except Exception:
            pass
    return True


def _debug_log_stream(tokens, label: str):
    """Yield every token from *tokens* while emitting a debug log per token."""
    for token in tokens:
        _LOG.debug("[%s] %r", label, token)
        yield token


def _stream_or_capture_persona_answer(orc, messages, *, allow_recall: bool) -> tuple[str, bool]:
    full_answer = ""
    visible_stream_started = False
    leading_buffer = ""
    recall_query = ""
    stream = None
    live_screen_path = _resolve_live_screen_turn_image(orc)
    try:
        if live_screen_path is not None:
            stream = generate_stream_with_image_attachment(
                orc.llm,
                messages=messages,
                image_path=live_screen_path,
                attachment_text=_live_screen_persona_attachment_text(orc),
                temperature=orc.temperature,
                max_tokens=int(getattr(CFG, "PERSONA_MAX_TOKENS", 700)),
                cancel_token=orc.cancel_token,
            )
        else:
            stream = orc.llm.generate_stream(
                messages,
                temperature=orc.temperature,
                max_tokens=int(getattr(CFG, "PERSONA_MAX_TOKENS", 700)),
                cancel_token=orc.cancel_token,
            )
        _raw = _debug_log_stream(stream, "PIPE-IN") if _LOG.isEnabledFor(logging.DEBUG) else stream
        for display_delta in stream_thinking_filter(_raw):
            _LOG.debug("[FILTER-OUT] %r", display_delta)

            full_answer += display_delta
            if not allow_recall or visible_stream_started:
                if display_delta:
                    _LOG.debug("[QUEUE-PUT] len=%d", len(full_answer))
                    orc.ui.put(("assistant_stream_delta", {"text": display_delta}))
                continue

            # Recall detection: only buffer if response might start with [RECALL:
            leading_buffer += display_delta
            stripped = leading_buffer.lstrip()

            # Check if this looks like a RECALL marker
            if stripped.upper().startswith("[RECALL:"):
                closing_idx = stripped.find("]")
                if closing_idx == -1 and len(stripped) < 160:
                    # Incomplete RECALL marker but still under size limit; keep buffering
                    continue
                if closing_idx != -1:
                    # Complete RECALL marker found
                    recall_query = stripped[len("[RECALL:"):closing_idx].strip()
                    break
                # Buffer overflow: treat as regular response (RECALL incomplete)
                visible_stream_started = True
                orc.ui.put(("assistant_stream_delta", {"text": leading_buffer}))
                leading_buffer = ""
            else:
                # Doesn't start with [RECALL: — this is regular response text
                # Switch to streaming mode immediately (don't batch)
                visible_stream_started = True
                orc.ui.put(("assistant_stream_delta", {"text": leading_buffer}))
                leading_buffer = ""
        return full_answer, bool(recall_query)
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def _stream_or_capture_persona_answer_text_only(orc, messages, *, allow_recall: bool) -> tuple[str, bool]:
    full_answer = ""
    visible_stream_started = False
    leading_buffer = ""
    recall_query = ""
    stream = None
    try:
        stream = orc.llm.generate_stream(
            messages,
            temperature=orc.temperature,
            max_tokens=int(getattr(CFG, "PERSONA_MAX_TOKENS", 700)),
            cancel_token=orc.cancel_token,
        )
        _raw = _debug_log_stream(stream, "PIPE-IN") if _LOG.isEnabledFor(logging.DEBUG) else stream
        for display_delta in stream_thinking_filter(_raw):
            _LOG.debug("[FILTER-OUT] %r", display_delta)

            full_answer += display_delta
            if not allow_recall or visible_stream_started:
                if display_delta:
                    _LOG.debug("[QUEUE-PUT] len=%d", len(full_answer))
                    orc.ui.put(("assistant_stream_delta", {"text": display_delta}))
                continue

            # Recall detection: only buffer if response might start with [RECALL:
            leading_buffer += display_delta
            stripped = leading_buffer.lstrip()

            # Check if this looks like a RECALL marker
            if stripped.upper().startswith("[RECALL:"):
                closing_idx = stripped.find("]")
                if closing_idx == -1 and len(stripped) < 160:
                    # Incomplete RECALL marker but still under size limit; keep buffering
                    continue
                if closing_idx != -1:
                    # Complete RECALL marker found
                    recall_query = stripped[len("[RECALL:"):closing_idx].strip()
                    break
                # Buffer overflow: treat as regular response (RECALL incomplete)
                visible_stream_started = True
                orc.ui.put(("assistant_stream_delta", {"text": leading_buffer}))
                leading_buffer = ""
            else:
                # Doesn't start with [RECALL: — this is regular response text
                # Switch to streaming mode immediately (don't batch)
                visible_stream_started = True
                orc.ui.put(("assistant_stream_delta", {"text": leading_buffer}))
                leading_buffer = ""
        return full_answer, bool(recall_query)
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def _run_persona_core(orc) -> None:
    """Core PERSONA execution logic extracted from ``phase_persona``.

    Builds persona prompts, streams the assistant response, handles recall
    retries, and sets ``next_stage``.  Side-effects on *orc* are expected.
    """
    orc._update_status(mode="SPEAKING")
    orc.ui.put(("agent_log", "--- PHASE 3: PERSONA (Speaking) ---"))
    orc.stats_collector.start_phase(orc.turn_stats, "persona")

    reporter_just_ran = bool(getattr(orc, "reporter_just_ran", False))
    system_notice = dict((getattr(orc, "route_decision", {}) or {}).get("system_notice") or {})
    explain_last_turn = str(system_notice.get("kind") or "").strip().lower() == "explain_last_turn"
    if reporter_just_ran and bool(getattr(orc, "latest_search_failed", False)):
        _finish_persona_fast_path(
            orc,
            _build_search_failed_persona_reply(orc),
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if str(system_notice.get("kind") or "").strip().lower() == "search_in_flight":
        _finish_persona_fast_path(
            orc,
            _build_search_in_flight_reply(system_notice),
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if str(system_notice.get("kind") or "").strip().lower() == "non_admin_route_guard":
        _finish_persona_fast_path(
            orc,
            str(system_notice.get("reply") or "")
            or "I cannot access files or run code on this system. I can help you with chat or search though!",
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if str(system_notice.get("kind") or "").strip().lower() == "file_state_correction_ack":
        _finish_persona_fast_path(
            orc,
            _build_file_state_correction_ack_reply(system_notice),
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if str(system_notice.get("kind") or "").strip().lower() == "file_target_confirmation_cancelled":
        _finish_persona_fast_path(
            orc,
            _build_file_target_confirmation_cancelled_reply(system_notice),
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if str(system_notice.get("kind") or "").strip().lower() == "stage_approval_cancelled":
        _finish_persona_fast_path(
            orc,
            _build_stage_approval_cancelled_reply(system_notice),
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if str(system_notice.get("kind") or "").strip().lower() == "stage_approval_no_remaining_work":
        _finish_persona_fast_path(
            orc,
            _build_stage_approval_no_remaining_work_reply(system_notice),
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if str(system_notice.get("kind") or "").strip().lower() == "destructive_prompt_injection_refusal":
        _finish_persona_fast_path(
            orc,
            _build_destructive_prompt_injection_refusal_reply(system_notice),
            reporter_just_ran=reporter_just_ran,
            emit_start=True,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return

    live_screen_path = _current_live_screen_path(orc)
    live_screen_visual_chat = _should_route_live_screen_visual_chat(
        orc.user_msg,
        live_screen_path=live_screen_path,
    )
    route_card = dict((getattr(orc, "route_decision", {}) or {}).get("card") or {})
    effective_persona_user_msg = str(orc.user_msg or "").strip()
    if str((getattr(orc, "route_decision", {}) or {}).get("decision") or "").strip().upper() == "CHAT":
        effective_query = str(route_card.get("query") or "").strip()
        if effective_query:
            effective_persona_user_msg = effective_query

    prompt_pack = orc.prompt_context.build_persona_pack(
        user_msg=effective_persona_user_msg,
        style_overlay=orc.ss.overlay or "",
        knowledge_enabled=False if explain_last_turn else orc.knowledge_enabled,
        brain_limit=0 if explain_last_turn else (2 if live_screen_visual_chat else 9),
        document_limit=0 if (explain_last_turn or live_screen_visual_chat) else 5,
    )
    current_card = dict(getattr(orc, "context_card", {}) or route_card)
    current_stages = current_card.get("stages") or []
    latest_stage: dict[str, object] = {}
    if current_stages and isinstance(current_stages[-1], dict):
        latest_stage = dict(current_stages[-1])
    active_skill = dict(
        getattr(orc, "route_decision", {}).get("skill")
        or current_card.get("skill")
        or latest_stage.get("skill")
        or {}
    )
    if getattr(orc, "ingested_document_chat", False):
        prompt_pack = orc.prompt_context.apply_document_focus(
            prompt_pack,
            focus_text=(
                str(getattr(orc, "document_focus_text", "") or "").strip()
                or "No grounded answer could be extracted from the supplied document context."
            ),
            references=list(getattr(orc, "document_focus_refs", []) or []),
            sources=list(getattr(orc, "document_focus_sources", []) or []),
        )
    elif getattr(orc, "document_focus_text", ""):
        prompt_pack = orc.prompt_context.apply_document_focus(
            prompt_pack,
            focus_text=str(orc.document_focus_text or "").strip(),
            references=list(getattr(orc, "document_focus_refs", []) or []),
            sources=list(getattr(orc, "document_focus_sources", []) or []),
        )
    elif latest_stage and FileStagePolicy.stage_is_file_work(latest_stage):
        prompt_pack = orc.prompt_context.clear_memory_for_file_work(prompt_pack)

    prompt_pack = orc.prompt_context.apply_context_arbitration(
        prompt_pack,
        route_decision=orc.route_decision,
        ingested_document_chat=bool(getattr(orc, "ingested_document_chat", False)),
        reporter_just_ran=reporter_just_ran,
        document_focus_active=bool(getattr(orc, "document_focus_text", "") or ""),
    )

    prompt_context = orc.prompt_context.to_prompt_context(prompt_pack)

    _outcome_pack_for_persona = getattr(orc, "last_stage_outcome", None)
    # If the failed-task retry cap would silently drop [ROUTER], tell the persona
    # upfront so it does not say "initiating another pass" and then do nothing.
    if (
        _outcome_pack_for_persona is not None
        and bool(getattr(_outcome_pack_for_persona, "allow_persona_reroute", False))
        and int(getattr(orc, "failed_task_router_retries", 0) or 0) >= 1
    ):
        from dataclasses import replace as _dc_replace
        try:
            _outcome_pack_for_persona = _dc_replace(_outcome_pack_for_persona, allow_persona_reroute=False)
        except TypeError:
            # outcome_pack is not a dataclass — fall back to a simple wrapper
            class _Wrapped:
                def __init__(self, inner):
                    self.__dict__.update(inner.__dict__)
                    self.allow_persona_reroute = False
            _outcome_pack_for_persona = _Wrapped(_outcome_pack_for_persona)

    persona_runtime = orc.prompt_context.build_persona_runtime_pack(
        orc.scratchpad,
        latest_stage=latest_stage,
        reporter_just_ran=reporter_just_ran,
        verification_result=getattr(orc, "last_verification", None),
        outcome_pack=_outcome_pack_for_persona,
    )
    outcome_block = persona_runtime.outcome_block
    allow_persona_recall = _persona_recall_allowed(
        orc,
        reporter_just_ran=reporter_just_ran,
        explain_last_turn=explain_last_turn,
    )
    persona_directives = orc.prompt_context.build_persona_directive_pack(
        route_decision=orc.route_decision,
        ingested_document_chat=bool(getattr(orc, "ingested_document_chat", False)),
        document_focus_active=bool(getattr(orc, "document_focus_text", "") or ""),
        reporter_just_ran=reporter_just_ran,
        active_skill=active_skill,
        persona_runtime=persona_runtime,
    )

    system_content = PromptBuilder.build_persona_prompt(prompt_context)
    tail_system_parts = list(persona_directives.tail_system_blocks)
    identity_switch_notice = str(getattr(orc, "identity_switch_notice", "") or "").strip()
    if identity_switch_notice:
        tail_system_parts.append(identity_switch_notice)
    if str(getattr(getattr(orc, "_cfg", None), "input_modality", "") or "").strip().lower() == "voice":
        tail_system_parts.append(
            "[INPUT MODALITY]\n"
            "The current user turn came from microphone speech recognition.\n"
            "If [ACTIVE USER] is still unknown, do not ask who is speaking just because identity is unknown; "
            "voice recognition is handled by the runtime before Persona.\n"
            "Answer the user's actual message normally, using only public/session context and no persistent personal memory."
        )
    if not allow_persona_recall:
        tail_system_parts.append(
            "[MEMORY RECALL RULE]\n"
            "Do not emit [RECALL: ...] markers on this turn.\n"
            "Answer only from the current turn evidence, runtime context, and verified state."
        )

    history = orc.get_context()
    if reporter_just_ran:
        history = _build_search_report_history(history, user_msg=orc.user_msg)
    else:
        limit = getattr(CFG, "MODEL_MAX_TURNS", 10)
        # Fast trim only (llm=None) — no blocking LLM call before stream start.
        # Full LLM summarization runs in _hook_deferred_conversation_summary after reply.
        compression_result = orc.conversation_compressor.compress_history(
            history=history,
            existing_summary=str(getattr(orc, "conversation_summary", "") or "") if getattr(orc, "knowledge_enabled", True) else "",
            max_turns=limit,
            llm=None,
            cancel_token=None,
        )
        history = list(compression_result.history)
    if explain_last_turn:
        history = history[-6:]

    # Prepend style bootstrap tone examples — in-memory only, never persisted.
    # Injected only on session start (first turn) or when the active style changes.
    # After the first real exchange the conversation history itself primes the tone;
    # re-injecting every turn wastes tokens and pollutes the history view.
    # Skipped for EXPLAIN turns (explain needs clean recent history, not style priming).
    _bootstrap = list(getattr(orc.ss, "bootstrap", ()) or ())
    _active_style_name = str(getattr(orc.ss, "name", "") or "")
    _last_bootstrap_style = str(getattr(orc, "_bootstrap_injected_for_style", "") or "")
    if _bootstrap and not explain_last_turn and _active_style_name != _last_bootstrap_style:
        history = [dict(m) for m in _bootstrap] + list(history)
        orc._bootstrap_injected_for_style = _active_style_name

    orc.ui.put(("assistant_stream_start", {"tts_voice": orc.ss.tts_voice, "tts_speed": orc.ss.tts_speed}))
    full_answer = ""
    search_summary_fallback = str(getattr(orc, "latest_search_summary", "") or "").strip()
    outcome_failed = persona_runtime.outcome_failed
    outcome_paused = persona_runtime.outcome_paused
    compound_sequence_direct_answer = _build_compound_file_sequence_final_state_reply(orc)
    if compound_sequence_direct_answer:
        _finish_persona_fast_path(
            orc,
            _append_undo_notice_if_needed(orc, compound_sequence_direct_answer),
            reporter_just_ran=reporter_just_ran,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if persona_directives.direct_answer:
        _finish_persona_fast_path(
            orc,
            _append_undo_notice_if_needed(orc, persona_directives.direct_answer),
            reporter_just_ran=reporter_just_ran,
        )
        orc.stats_collector.end_phase(orc.turn_stats, "persona")
        orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
        _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
        return
    if (
        not explain_last_turn
        and str(getattr(orc, "route_decision", {}).get("decision") or "").strip().upper() == "CHAT"
    ):
        readonly_query = str(
            (
                (getattr(orc, "route_decision", {}).get("card") or {}).get("query")
                or orc.user_msg
            )
            or ""
        ).strip()
        readonly_state_answer = orc.prompt_context.build_readonly_state_answer(readonly_query)
        if readonly_state_answer:
            _finish_persona_fast_path(
                orc,
                _append_undo_notice_if_needed(orc, readonly_state_answer),
                reporter_just_ran=reporter_just_ran,
            )
            orc.stats_collector.end_phase(orc.turn_stats, "persona")
            orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
            _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
            return
    recall_blocks: list[str] = []
    persona_error = False
    full_answer = ""
    for recall_pass in range(3):
        tail_content = "\n\n".join(part for part in [*tail_system_parts, *recall_blocks] if part)
        messages = build_persona_messages(
            system_content=system_content,
            history=history,
            outcome_block=outcome_block,
            tail_system_content=tail_content,
            model_path=getattr(CFG, "MODEL_PATH", None),
        )
        if CFG.DEBUG_LLM_PROMPTS:
            log_prompt_debug(
                CFG.PERSONA_DEBUG_PATH,
                messages,
                "PERSONA" if recall_pass == 0 else f"PERSONA_RECALL_{recall_pass}",
            )
        try:
            full_answer, recall_requested = _stream_or_capture_persona_answer(
                orc,
                messages,
                allow_recall=allow_persona_recall,
            )
        except VisionError as exc:
            orc.ui.put(("agent_log", f"   -> Live Screen Persona Error: {exc}"))
            if live_screen_visual_chat:
                full_answer = "I couldn't read the current screen frame just now. Try again."
                orc.ui.put(("assistant_stream_delta", {"text": full_answer}))
                recall_requested = False
                break
            try:
                full_answer, recall_requested = _stream_or_capture_persona_answer_text_only(
                    orc,
                    messages,
                    allow_recall=allow_persona_recall,
                )
            except LLMClientError as retry_exc:
                orc.emit_runtime_signal(
                    {
                        "kind": "persona_error",
                        "severity": "error",
                        "source": "persona",
                        "summary": f"Persona error: {retry_exc}",
                        "details": str(retry_exc),
                    }
                )
                orc.ui.put(("error", f"Persona Error: {retry_exc}"))
                orc.stats_collector.note_persona_error(orc.turn_stats, str(retry_exc))
                persona_error = True
                break
        except LLMClientError as exc:
            orc.emit_runtime_signal(
                {
                    "kind": "persona_error",
                    "severity": "error",
                    "source": "persona",
                    "summary": f"Persona error: {exc}",
                    "details": str(exc),
                }
            )
            orc.ui.put(("error", f"Persona Error: {exc}"))
            orc.stats_collector.note_persona_error(orc.turn_stats, str(exc))
            persona_error = True
            break

        recall_query = _extract_recall_query(full_answer) if allow_persona_recall else ""
        if not recall_query:
            break
        if recall_pass >= 2:
            orc.ui.put(("agent_log", "   -> RECALL marker ignored after max recall passes."))
            break

        # Mid-sentence recall: the model placed [RECALL:…] after visible text,
        # so those first-pass tokens were already streamed to the pipeline.
        # Send a fresh start event to wipe the partial display before the
        # clean second-pass response streams in.  For start-of-response recall
        # (recall_requested=True) nothing was streamed, so no reset is needed.
        if not recall_requested:
            orc.ui.put(("assistant_stream_start", {"tts_voice": orc.ss.tts_voice, "tts_speed": orc.ss.tts_speed}))

        orc.ui.put(("agent_log", f"   -> RECALL marker accepted: {recall_query}"))
        orc._log_dashboard(f"Recalling memory: {recall_query}")
        try:
            recall_hits = orc.prompt_context.brain.recall(recall_query, n_results=5)
        except Exception as exc:
            orc.ui.put(("agent_log", f"   -> Recall failed: {exc}"))
            recall_hits = []
        recall_blocks.append(_render_recall_block(recall_query, recall_hits))
        full_answer = ""

    # Guard: model produced no response tokens (generate_stream filtered away all
    # thinking-only output or reasoning_content).  Retry once with /no_think appended
    # to the last user message — the Qwen3/3.5 model-level directive that tells it
    # to skip the thinking phase and respond directly.
    if not full_answer.strip() and not persona_error:
        orc.ui.put(("agent_log", "   -> Persona returned empty output; retrying with /no_think."))
        _retry_messages = list(messages)
        for _ri in range(len(_retry_messages) - 1, -1, -1):
            if _retry_messages[_ri].get("role") == "user":
                _rm = dict(_retry_messages[_ri])
                _rm["content"] = str(_rm.get("content") or "").rstrip() + " /no_think"
                _retry_messages[_ri] = _rm
                break
        try:
            full_answer, _ = _stream_or_capture_persona_answer(
                orc, _retry_messages, allow_recall=False,
            )
        except VisionError as exc:
            orc.ui.put(("agent_log", f"   -> /no_think retry failed (vision): {exc}"))
            full_answer = ""
        except LLMClientError as exc:
            orc.ui.put(("agent_log", f"   -> /no_think retry failed: {exc}"))
            orc.stats_collector.note_persona_error(orc.turn_stats, str(exc))
            persona_error = True

    router_requested = "[ROUTER]" in full_answer
    clean_answer = _strip_persona_control_tags(full_answer)
    clean_answer = sanitize_persona_output(
        clean_answer,
        route_decision=orc.route_decision,
        outcome_block=outcome_block,
        user_msg=orc.user_msg,
    )
    clean_answer = _append_undo_notice_if_needed(orc, clean_answer)
    latest_route_error = str(getattr(orc, "latest_route_error", "") or "").strip()
    if reporter_just_ran and not clean_answer and search_summary_fallback:
        clean_answer = search_summary_fallback

    if reporter_just_ran and not full_answer.strip() and search_summary_fallback:
        orc.ui.put(("assistant_stream_delta", {"text": search_summary_fallback}))

    if not persona_error:
        orc.ui.put(("assistant_stream_end", ""))

    if clean_answer and clean_answer != full_answer.strip():
        orc.chat.replace_last_assistant_content(clean_answer)

    if router_requested:
        if latest_route_error:
            orc.ui.put(("agent_log", "   -> ROUTER marker ignored because the latest secretary pass errored."))
            orc.next_stage = "FINISHED"
        elif reporter_just_ran:
            orc.ui.put(("agent_log", "   -> ROUTER marker ignored after completed search report."))
            orc.next_stage = "FINISHED"
        elif outcome_failed:
            if not bool(getattr(persona_runtime, "allow_persona_reroute", True)):
                orc.ui.put(("agent_log", "   -> ROUTER marker ignored because this failure is terminal for the current turn."))
                orc.next_stage = "FINISHED"
            elif _wants_user_confirmation(clean_answer):
                orc.ui.put(("agent_log", "   -> ROUTER marker ignored because the reply is asking for user confirmation."))
                orc.next_stage = "FINISHED"
            elif int(getattr(orc, "failed_task_router_retries", 0) or 0) >= 1:
                orc.ui.put(("agent_log", "   -> ROUTER marker ignored after failed-task retry cap."))
                orc.next_stage = "FINISHED"
            else:
                orc.failed_task_router_retries = int(getattr(orc, "failed_task_router_retries", 0) or 0) + 1
                orc.ui.put(("agent_log", "   -> ROUTER marker accepted after failed task outcome."))
                orc.stats_collector.note_router_reroute(orc.turn_stats)
                orc.next_stage = "ROUTE"
        elif outcome_block and not outcome_paused:
            orc.ui.put(("agent_log", "   -> ROUTER marker ignored after successful task outcome."))
            orc.next_stage = "FINISHED"
        else:
            loopback_count = int(getattr(orc, "persona_router_loopback_retries", 0) or 0)
            if loopback_count >= 1:
                orc.ui.put(("agent_log", f"   -> LOOPBACK ignored after cap ({loopback_count})."))
                orc.next_stage = "FINISHED"
            else:
                orc.persona_router_loopback_retries = loopback_count + 1
                orc.ui.put(("agent_log", "   -> LOOPBACK DETECTED."))
                orc.stats_collector.note_router_reroute(orc.turn_stats)
                orc.next_stage = "ROUTE"
    else:
        orc.next_stage = "FINISHED"

    orc.stats_collector.end_phase(orc.turn_stats, "persona")
    orc.stats_collector.note_tts_metrics(orc.turn_stats, _consume_pipeline_stream_metrics(orc))
    _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)


def phase_persona(orc) -> None:
    orc.raise_if_cancelled()
    _run_persona_core(orc)
