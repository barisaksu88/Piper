from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from config import CFG
from core.contracts import RouteDecision
from core.document_focus import build_document_focus_messages, extract_document_focus
from core.debug_tools import log_prompt_debug
from core.engines.followup_resolution import FollowupResolutionEngine
from core.engines.route_clarity import RouteClarifier
from core.engines.state_mutation import StateMutationEngine
from core.executor import StageExecutor
from core.file_stage_policy import FileStagePolicy
from core.json_utils import parse_json_response
from core.persona_output import sanitize_persona_output
from core.prompting import ScratchpadFormatter, PromptBuilder, build_persona_messages
from core.route_normalizer import normalize_route_decision
from core.skills import apply_route_skill_layer
from core.stage_policy import stage_requires_user_approval, stage_requires_user_input
from core.stream_filter import stream_thinking_filter
from llm.llm_server_client import LLMClientError
from core.runtime_control import OperationCancelled
from tools.vision import VisionError, generate_stream_with_image_attachment, generate_with_image_attachment

FALLBACK_SECRETARY = "You are a Router. Output JSON: {decision: 'CHAT' or 'TASK', card: {goal, context}}."
SEARCH_RESULT_PREFIX = "Background search complete for '"
SEARCH_REPORTER_INSTRUCTION = "The web search is complete. Summarize the findings for the user now."
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


def _is_pending_search_payload(message: dict) -> bool:
    return (
        message.get("role") == "system"
        and str(message.get("content", "")).startswith(SEARCH_RESULT_PREFIX)
    )


def _is_search_reporter_instruction(message: dict) -> bool:
    return (
        message.get("role") == "system"
        and str(message.get("content", "")).strip() == SEARCH_REPORTER_INSTRUCTION
    )


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


def _extract_latest_stage_outcome_entry(scratchpad: list[str]) -> str:
    for entry in reversed(scratchpad or []):
        text = str(entry or "")
        if " OUTCOME ===" in text and "RESULT:" in text:
            return text
    return ""

def _build_latest_runtime_context_message(orc, *, reporter_just_ran: bool = False) -> str:
    return orc.prompt_context.build_runtime_context_message(
        orc,
        reporter_just_ran=reporter_just_ran,
    )


def _upsert_latest_runtime_context(orc, *, reporter_just_ran: bool = False) -> None:
    payload = _build_latest_runtime_context_message(orc, reporter_just_ran=reporter_just_ran)
    if not payload:
        return
    try:
        orc.chat.upsert_hidden_system_message(_LATEST_RUNTIME_CONTEXT_PREFIX, payload)
    except AttributeError:
        orc.chat.append_message({"role": "system", "content": payload, "hidden": True})


def _finalize_persona_turn(orc, *, reporter_just_ran: bool = False) -> None:
    orc.reporter_just_ran = False
    orc.latest_search_summary = ""
    recent_messages = orc.chat.recent_messages(3)
    profile_messages = orc.chat.recent_messages(8)
    if orc.knowledge_enabled and len(recent_messages) >= 3:
        orc.knowledge.consolidate_memory_async(recent_messages)
    if orc.knowledge_enabled and len(profile_messages) >= 4:
        orc.knowledge.update_knowledge_async(profile_messages)
    _upsert_latest_runtime_context(orc, reporter_just_ran=reporter_just_ran)


def _finish_persona_fast_path(orc, text: str, *, reporter_just_ran: bool = False) -> None:
    # Stream the pre-computed answer word-by-word so it appears progressively
    # in the UI rather than all at once.
    chunks = re.split(r'(\s+)', text)
    for chunk in chunks:
        if chunk:
            orc.ui.put(("assistant_stream_delta", {"text": chunk}))
    orc.ui.put(("assistant_stream_end", ""))
    orc.next_stage = "FINISHED"
    _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)


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


def phase_route(orc) -> None:
    orc.raise_if_cancelled()
    orc._update_status(mode="ROUTING")
    orc.ui.put(("agent_log", "--- PHASE 1: SECRETARY (Routing) ---"))
    orc.latest_route_error = ""

    full_history = orc.get_context()
    recent_history = full_history[-6:]
    latest_runtime_context = _latest_runtime_context_message(full_history)
    router_history = list(recent_history)
    orc.user_msg = ""
    for message in reversed(recent_history):
        if message.get("role") == "user":
            orc.user_msg = message.get("content", "")
            break

    if str(orc.user_msg or "").strip():
        try:
            orc.prompt_context.record_user_turn(str(orc.user_msg))
        except Exception:
            pass

    orc.is_search_result = any(_is_pending_search_payload(message) for message in recent_history)

    if orc.is_search_result:
        orc.ui.put(("agent_log", "   -> Context implies Search Result. Bypassing Router."))
        orc.next_stage = "REPORTER"
        return

    orc.ingested_document_chat = False
    try:
        ingested_documents = orc.prompt_context.document_memory.list_documents()
    except Exception:
        ingested_documents = []
    if _should_route_ingested_document_chat(orc.user_msg, recent_history, ingested_documents):
        orc.route_decision = {"decision": "CHAT"}
        orc.ingested_document_chat = True
        orc.ui.put(("agent_log", "   -> Routed to CHAT via ingested document memory."))
        orc.next_stage = "DOC_FOCUS"
        return

    live_screen_path = _resolve_live_screen_turn_image(orc)
    if _should_route_live_screen_visual_chat(orc.user_msg, live_screen_path=live_screen_path):
        orc.route_decision = {"decision": "CHAT"}
        orc.ui.put(("agent_log", "   -> Routed to CHAT via live screen visual query rule."))
        orc.next_stage = "PERSONA"
        return

    prompt_path = CFG.PROMPTS_DIR / "secretary.txt"
    sys_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else FALLBACK_SECRETARY
    sys_prompt = _merge_secretary_system_prompt(sys_prompt, latest_runtime_context)
    messages = [{"role": "system", "content": sys_prompt}]
    messages.append(
        {
            "role": "user",
            "content": f"User Input: {orc.user_msg}\nHistory:\n{json.dumps(router_history, indent=2)}",
        }
    )

    try:
        orc.ui.put(("status", "Routing..."))
        if CFG.DEBUG_LLM_PROMPTS:
            log_prompt_debug(CFG.LLM_PROMPT_DEBUG_PATH, messages, "SECRETARY")
        if live_screen_path is not None:
            raw = generate_with_image_attachment(
                orc.llm,
                messages=messages,
                image_path=live_screen_path,
                attachment_text=_LIVE_SCREEN_ROUTER_ATTACHMENT,
                temperature=0.1,
                cancel_token=orc.cancel_token,
            )
        else:
            raw = orc.llm.generate(messages, temperature=0.1, cancel_token=orc.cancel_token)
        orc.ui.put(("agent_log", f"   -> Secretary Raw: {raw}"))
        parsed_raw: RouteDecision | None = parse_json_response(raw)
        parsed: RouteDecision = parsed_raw or {"decision": "CHAT"}
        if not parsed_raw:
            orc.ui.put(("agent_log", "   -> Secretary JSON parse failed. Applying route normalization to CHAT fallback."))
        normalized = normalize_route_decision(parsed, orc.user_msg, router_history)
        followup_resolved = _resolve_followup_route_with_llm(orc, normalized, router_history)
        if followup_resolved is not None and followup_resolved != normalized:
            normalized = followup_resolved
            orc.ui.put(("agent_log", "   -> Follow-up resolver refined ambiguous continuation route."))
        clarified = _refine_ambiguous_task_route_with_llm(orc, normalized, router_history)
        if clarified is not None and clarified != normalized:
            normalized = clarified
            orc.ui.put(("agent_log", "   -> Ambiguous task route converted into clarification pause."))
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

    decision = orc.route_decision.get("decision")
    if decision == "SEARCH":
        orc.next_stage = "SEARCH"
    elif decision == "TASK":
        orc.next_stage = "MANAGER"
    else:
        orc.next_stage = "PERSONA"


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
        log_prompt_debug(CFG.LLM_PROMPT_DEBUG_PATH, messages, "DOCUMENT_FOCUS")

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
    query = orc.route_decision.get("card", {}).get("query", orc.user_msg)
    orc.ui.put(("agent_log", f"--- ROUTER: Triggering Background Search for '{query}' ---"))

    speak_messages = [
        {"role": "system", "content": "You are Piper. You have just triggered a background web search. Inform the user you are checking the web for them now. Be brief."},
        {"role": "user", "content": orc.user_msg},
    ]
    orc.ui.put(("assistant_stream_start", {"tts_voice": orc.ss.tts_voice, "tts_speed": orc.ss.tts_speed}))
    try:
        for delta in orc.llm.generate_stream(
            speak_messages,
            temperature=orc.temperature,
            cancel_token=orc.cancel_token,
        ):
            orc.ui.put(("assistant_stream_delta", {"text": delta}))
    except OperationCancelled:
        orc.ui.put(("assistant_stream_end", ""))
        raise
    except Exception:
        pass
    orc.ui.put(("assistant_stream_end", ""))

    from tools.search import perform_search

    def _do_search() -> None:
        queued_result = False
        if orc.cancel_token is not None:
            orc.retain_cancel_token(orc.cancel_token)
        try:
            orc.raise_if_cancelled()
            data = perform_search(
                query,
                CFG.DATA_DIR,
                log_callback=orc._log_dashboard,
                cancel_token=orc.cancel_token,
            )
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
            orc.ui.put(("error", f"Search Error: {exc}"))
            orc._log_dashboard(f"Search Error: {exc}")
        finally:
            if orc.cancel_token is not None:
                orc.release_cancel_token(orc.cancel_token)
            if not queued_result:
                orc.ui.put(("status", "Canceled" if orc.cancel_token and orc.cancel_token.is_cancelled else "IDLE"))

    threading.Thread(target=_do_search, daemon=True).start()
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
    except OperationCancelled:
        raise
    except Exception as exc:
        orc.ui.put(("agent_log", f"   -> Route clarifier error: {exc}"))
        return None


def phase_reporter(orc) -> None:
    orc.raise_if_cancelled()
    orc.ui.put(("agent_log", "   -> Search Result Detected. Activating Reporter Layer."))

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

    query = "Unknown Query"
    data = raw_content
    if SEARCH_RESULT_PREFIX in raw_content:
        try:
            parts = raw_content.split("'", 2)
            if len(parts) >= 2:
                query = parts[1]
            if "Data:\n" in raw_content:
                data = raw_content.split("Data:\n", 1)[1]
        except Exception:
            pass

    reporter_path = CFG.PROMPTS_DIR / "reporter.txt"
    sys_template = reporter_path.read_text(encoding="utf-8") if reporter_path.exists() else "Summarize this."
    sys_prompt = sys_template.replace("{query}", query).replace("{data}", data)

    orc.ui.put(("status", "Analyzing Search Results..."))
    try:
        reporter_messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Summarize the search findings for '{query}' using the instructions above."},
        ]
        summary = orc.llm.generate(reporter_messages, temperature=0.1, cancel_token=orc.cancel_token)
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
    orc.next_stage = "PERSONA"


def phase_manager(orc) -> None:
    orc.raise_if_cancelled()
    orc.context_card = orc.route_decision.get("card", {})
    orc._update_status(mode="PLANNING", goal=orc.context_card.get("goal", "Unknown"))
    orc.ui.put(("agent_log", "--- PHASE 2: EXECUTIVE LOOP (Task) ---"))

    stages = orc.context_card.get("stages", [])
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
    )
    total_stages = len(stages)
    for index, stage in enumerate(stages):
        orc.raise_if_cancelled()
        stage = dict(stage)
        if orc.context_card.get("context") and "context" not in stage:
            stage["context"] = list(orc.context_card.get("context") or [])
        stage_num = index + 1
        orc.ui.put(("agent_log", f"=== STARTING STAGE {stage_num}/{total_stages}: {stage.get('stage_goal')} ==="))
        needs_user_input = stage_requires_user_input(stage)
        needs_user_approval = stage_requires_user_approval(stage)

        success, stage_log = executor.run(stage, stage_num, total_stages)
        if (
            not success
            and FileStagePolicy.stage_requires_analysis_report(stage)
            and bool(orc.prompt_context.extract_latest_stage_proposal_answer(stage_log))
            and bool(orc.prompt_context.extract_exact_file_read_answer(stage_log))
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

        orc.scratchpad = stage_log
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
        )
        true_success = outcome_pack.effective_success
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
                orc.ui.put(("agent_log", f"   -> Stage {stage_num} Ready. Awaiting user input."))
                orc._log_dashboard(f"Stage {stage_num} awaiting user input.")
                break
            if needs_user_approval:
                orc.ui.put(("agent_log", f"   -> Stage {stage_num} Ready. Awaiting user approval before execution."))
                orc._log_dashboard(f"Stage {stage_num} awaiting approval.")
                break
            orc.ui.put(("agent_log", f"   -> Stage {stage_num} Complete."))
            orc._log_dashboard(f"Stage {stage_num} Success.")
        else:
            if bool(getattr(outcome_pack, "auto_reroute", False)) and int(getattr(orc, "failed_task_router_retries", 0) or 0) < 1:
                orc.failed_task_router_retries = int(getattr(orc, "failed_task_router_retries", 0) or 0) + 1
                _upsert_latest_runtime_context(orc, reporter_just_ran=False)
                reason = str(getattr(outcome_pack, "reroute_reason", "") or "").strip()
                if reason:
                    orc.ui.put(("agent_log", f"   -> Auto-rerouting after failed stage: {reason}"))
                else:
                    orc.ui.put(("agent_log", "   -> Auto-rerouting after failed stage to re-evaluate intent."))
                orc._log_dashboard(f"Stage {stage_num} rerouting.")
                orc.next_stage = "ROUTE"
                return
            orc.ui.put(("agent_log", f"   -> Stage {stage_num} Failed/Errors."))
            orc._log_dashboard(f"Stage {stage_num} Failed.")
            break

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
        r"\[(?:ACTIVE_SKILL|LATEST_SYSTEM_EVENT|FINAL_STAGE_OUTCOME|NO_MUTATION_RULE|DOCUMENT_QA_RULE|FILE_WORK_REPORT_RULE|SEARCH_REPORT_RULE|ENGINEERING_SUPPORT_RULE)\]",
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


def _debug_log_stream(tokens, label: str):
    """Yield every token from *tokens* while printing a debug line per token.

    Only called when ``CFG.DEBUG_STREAMING_PIPELINE`` is True so there is no
    runtime cost in normal operation.
    """
    for token in tokens:
        print(f"[{label}] {repr(token)}", flush=True)
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
                cancel_token=orc.cancel_token,
            )
        else:
            stream = orc.llm.generate_stream(
                messages,
                temperature=orc.temperature,
                cancel_token=orc.cancel_token,
            )
        # Wrap raw stream with PIPE-IN trace when pipeline debug is on.
        _raw = _debug_log_stream(stream, "PIPE-IN") if CFG.DEBUG_STREAMING_PIPELINE else stream
        for display_delta in stream_thinking_filter(_raw):
            if CFG.DEBUG_STREAMING_PIPELINE:
                print(f"[FILTER-OUT] {repr(display_delta)}", flush=True)

            full_answer += display_delta
            if not allow_recall or visible_stream_started:
                if display_delta:
                    if CFG.DEBUG_STREAMING_PIPELINE:
                        print(f"[QUEUE-PUT] len={len(full_answer)}", flush=True)
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
            cancel_token=orc.cancel_token,
        )
        _raw = _debug_log_stream(stream, "PIPE-IN") if CFG.DEBUG_STREAMING_PIPELINE else stream
        for display_delta in stream_thinking_filter(_raw):
            if CFG.DEBUG_STREAMING_PIPELINE:
                print(f"[FILTER-OUT] {repr(display_delta)}", flush=True)

            full_answer += display_delta
            if not allow_recall or visible_stream_started:
                if display_delta:
                    if CFG.DEBUG_STREAMING_PIPELINE:
                        print(f"[QUEUE-PUT] len={len(full_answer)}", flush=True)
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


def phase_persona(orc) -> None:
    orc.raise_if_cancelled()
    orc._update_status(mode="SPEAKING")
    orc.ui.put(("agent_log", "--- PHASE 3: PERSONA (Speaking) ---"))

    reporter_just_ran = bool(getattr(orc, "reporter_just_ran", False))

    live_screen_path = _current_live_screen_path(orc)
    live_screen_visual_chat = _should_route_live_screen_visual_chat(
        orc.user_msg,
        live_screen_path=live_screen_path,
    )

    prompt_pack = orc.prompt_context.build_persona_pack(
        user_msg=orc.user_msg,
        style_overlay=orc.ss.overlay or "",
        knowledge_enabled=orc.knowledge_enabled,
        brain_limit=2 if live_screen_visual_chat else 5,
        document_limit=0 if live_screen_visual_chat else 5,
    )
    current_card = dict(getattr(orc, "context_card", {}) or getattr(orc, "route_decision", {}).get("card") or {})
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

    prompt_context = orc.prompt_context.to_prompt_context(prompt_pack)

    persona_runtime = orc.prompt_context.build_persona_runtime_pack(
        orc.scratchpad,
        latest_stage=latest_stage,
        reporter_just_ran=reporter_just_ran,
        escalation_active=bool(getattr(orc, "latest_codex_escalation", None)),
    )
    outcome_block = persona_runtime.outcome_block
    persona_directives = orc.prompt_context.build_persona_directive_pack(
        route_decision=orc.route_decision,
        ingested_document_chat=bool(getattr(orc, "ingested_document_chat", False)),
        reporter_just_ran=reporter_just_ran,
        active_skill=active_skill,
        latest_codex_escalation=getattr(orc, "latest_codex_escalation", None) or {},
        persona_runtime=persona_runtime,
    )

    system_content = PromptBuilder.build_persona_prompt(prompt_context)
    tail_system_parts = list(persona_directives.tail_system_blocks)

    history = orc.get_context()
    limit = getattr(CFG, "MODEL_MAX_TURNS", 10)
    if len(history) > limit:
        history = history[-limit:]

    orc.ui.put(("assistant_stream_start", {"tts_voice": orc.ss.tts_voice, "tts_speed": orc.ss.tts_speed}))
    full_answer = ""
    search_summary_fallback = str(getattr(orc, "latest_search_summary", "") or "").strip()
    outcome_failed = persona_runtime.outcome_failed
    outcome_paused = persona_runtime.outcome_paused
    if persona_directives.direct_answer:
        _finish_persona_fast_path(
            orc,
            persona_directives.direct_answer,
            reporter_just_ran=reporter_just_ran,
        )
        return
    if str(getattr(orc, "route_decision", {}).get("decision") or "").strip().upper() == "CHAT":
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
                readonly_state_answer,
                reporter_just_ran=reporter_just_ran,
            )
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
                CFG.LLM_PROMPT_DEBUG_PATH,
                messages,
                "PERSONA" if recall_pass == 0 else f"PERSONA_RECALL_{recall_pass}",
            )
        try:
            full_answer, recall_requested = _stream_or_capture_persona_answer(
                orc,
                messages,
                allow_recall=True,
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
                    allow_recall=True,
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
            persona_error = True
            break

        recall_query = _extract_recall_query(full_answer)
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
            persona_error = True

    router_requested = "[ROUTER]" in full_answer
    clean_answer = _strip_persona_control_tags(full_answer)
    clean_answer = sanitize_persona_output(
        clean_answer,
        route_decision=orc.route_decision,
        outcome_block=outcome_block,
        user_msg=orc.user_msg,
    )
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
            if bool(getattr(orc, "latest_codex_escalation", None)):
                orc.ui.put(("agent_log", "   -> ROUTER marker ignored because engineering support escalation is active — user must decide next step."))
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
                orc.next_stage = "ROUTE"
        elif outcome_block and not outcome_paused:
            orc.ui.put(("agent_log", "   -> ROUTER marker ignored after successful task outcome."))
            orc.next_stage = "FINISHED"
        else:
            orc.ui.put(("agent_log", "   -> LOOPBACK DETECTED."))
            orc.next_stage = "ROUTE"
    else:
        orc.next_stage = "FINISHED"

    _finalize_persona_turn(orc, reporter_just_ran=reporter_just_ran)
