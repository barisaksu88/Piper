from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Dict, List, Sequence

from core.browser_route_utils import (
    build_browser_context_followup_route,
    build_explicit_browser_task_card as build_browser_task_card,
    extract_browser_url as extract_browser_url,
    has_placeholder_browser_url as has_placeholder_browser_url,
    looks_like_explicit_browser_request as shared_looks_like_explicit_browser_request,
)
from core.contracts import RouteDecision, StageCard
from core.file_reference_matcher import file_reference_matches
from core.file_target_confirmation import (
    build_confirmed_route_decision,
    classify_pending_file_target_confirmation_reply,
    extract_pending_file_target_confirmation,
)
from core.engines.file_work import FileWorkEngine
from core.engines.state_mutation import StateMutationEngine
from core.routing.environment_queries import looks_like_live_environment_query
from core.runtime_context import extract_latest_runtime_context_fields
from core.turn_explanation import (
    extract_last_turn_explanation_snapshot,
    looks_like_turn_explanation_followup,
    looks_like_turn_explanation_request,
)
from core.routing.route_patterns import (
    DIRECT_FILE_COPY_RE,
    DIRECT_FILE_CREATE_TEXT_RE,
    DIRECT_FILE_DELETE_RE,
    DIRECT_FILE_MOVE_RE,
    DIRECT_FILE_READ_RE,
    DIRECT_FILE_REMOVE_TEXT_RE,
    DIRECT_FILE_REPLACE_TEXT_RE,
    EMPTY_DIR_CLEANUP_RE,
    EXTENSION_GROUPING_RE,
    FILE_ORG_REQUEST_RE,
    FILE_TYPE_GROUPING_RE,
    SPECULATIVE_ACTION_RE,
    EXPLICIT_ASSISTANT_REQUEST_RE,
    _FILE_PATH_TOKEN,
)
from core.routing.route_subjects import (
    looks_like_task_creation,
)

_DOCUMENT_READ_REQUEST_RE = re.compile(
    r"(?i)\b(what does it say in|what(?:'s| is) in|tell me what it says in|show me|read|open)\b"
)
_DOCUMENT_SEARCH_REQUEST_RE = re.compile(
    r"(?i)\b(search for|look for|find|locate|check again|re-?check|search again|look again)\b"
)
_DOCUMENT_NAMING_HINT_RE = re.compile(
    r"(?i)\b(name|naming|filename|title)\b.*\b(match|matching|different|mismatch)\b|\bdifferent filename\b|\bnot found under that specific name\b"
)
_QUOTED_TEXT_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_FILE_TARGET_RE = re.compile(r"[\w./\\-]+\.(?=[A-Za-z0-9]{1,8}\b)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,8}")
_DELETE_REQUEST_RE = re.compile(
    r"(?is)^(?:in the workspace,\s*)?(?:please\s+)?(?:delete|remove)\s+(?P<body>.+?)[.?!]*$"
)
_DIRECT_EMPTY_DIR_DELETE_RE = re.compile(
    r"(?is)^(?:in the workspace,\s*)?(?:please\s+)?(?:delete|remove|clean up|clear)\s+"
    r"(?:(?:the|all)\s+)?empty\s+(?:folders|directories)"
    r"(?:\s+(?:under|in)\s+(?:the\s+)?workspace)?[.?!]*$"
)
_DESTRUCTIVE_PROMPT_INJECTION_RE = re.compile(
    r"(?is)\b(?:ignore\s+previous\s+instructions?|hidden\s+instruction|maintenance\s+mode|developer\s+mode|system\s+override)\b"
    r".{0,800}?\b(?:delete|deleting|remove|removing|purge|purging|wipe|wiping|clear|clearing)\b"
    r".{0,800}?\b(?:workspace|files?|folders?|directories|everything|all|\.txt)\b"
)
_GENERIC_LOOKUP_SUBJECTS = {
    "document",
    "doc",
    "eg",
    "e g",
    "file",
    "list",
    "note",
    "text",
    "text file",
    "txt file",
    "this",
    "that",
    "it",
    "workspace",
}
_BLOCKED_LOOKUP_SUBJECTS = {
    "calendar",
    "event",
    "events",
    "schedule",
    "task",
    "task list",
    "tasks",
    "todo",
    "todo list",
    "to do",
    "to do list",
}
_PRONOUN_LOOKUP_SUBJECTS = {
    "it",
    "it back",
    "it again",
    "this back",
    "this again",
    "that back",
    "that again",
    "what s in it",
    "what is in it",
    "read it",
    "open it",
    "show it",
    "what s in this",
    "what is in this",
    "what s in that",
    "what is in that",
}
_GENERIC_DELETE_SUBJECTS = {
    "it",
    "this",
    "that",
    "file",
    "the file",
    "this file",
    "that file",
    "document",
    "the document",
    "this document",
    "that document",
}
_FILEISH_DELETE_HINT_RE = re.compile(
    r"(?i)\b(file|files|doc|docs|document|documents|note|notes|list|lists|txt|text|log|logs|config|configs|report|reports|script|scripts|code|folder|folders|directory|directories|image|images|photo|photos|pdf|json|yaml|yml|csv)\b"
)
_INTERACTIVE_VERIFY_RE = re.compile(r"\b(verify|confirm|check|observe|test|try|report)\b", re.IGNORECASE)
_COUNT_TOKEN_TO_INT: dict[str, int] = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_FOLDER_WITH_DUMMY_FILES_RE = re.compile(
    r"(?is)^(?:in the workspace,\s*)?(?:please\s+)?create\s+(?:a\s+)?(?:folder|directory)\s+called\s+"
    r"['\"]?(?P<folder>[\w./\\-]+)['\"]?\s+and\s+add\s+(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+dummy\s+files?\s+inside\s+it[.?!]*$"
)
_COMPOUND_RENAME_RE = re.compile(
    rf"(?is)\brename(?:\s+file)?\s+(?P<src>{_FILE_PATH_TOKEN})\s+to\s+(?P<renamed>{_FILE_PATH_TOKEN})\b"
)
_COMPOUND_MOVE_INTO_FOLDER_RE = re.compile(
    rf"(?is)\b(?:then|and then)\s+(?:move|put)\s+(?:the\s+new\s+)?(?:file\s+)?(?P<reported>{_FILE_PATH_TOKEN})\s+"
    rf"(?:into|to)\s+(?:a\s+folder\s+called|a\s+folder\s+named|folder\s+called|folder\s+named|the\s+folder\s+called|the\s+folder\s+named)?\s*"
    rf"['\"]?(?P<folder>[\w./\\-]+)['\"]?"
)
_INTERACTIVE_CONTROL_RE = re.compile(
    r"\b(controls?|input|movement|left|right|up|down|keyboard|mouse|responsive|respond|press|click|catch|gameplay|works?)\b",
    re.IGNORECASE,
)
_FILE_EDIT_REQUEST_RE = re.compile(r"\b(edit|update|modify|change|rewrite)\b", re.IGNORECASE)
_SECOND_LINE_APPEND_RE = re.compile(
    r"\b(?:also\s+)?add\s+(?:a\s+)?(?:second|new|another)\s+line\b",
    re.IGNORECASE,
)
_APPEND_LINE_REQUEST_RE = re.compile(
    r"\b(?:append|add|insert)\s+(?:a\s+)?(?:new\s+)?line(?:\s+(?:saying|with|containing))?\b",
    re.IGNORECASE,
)
_LOOKUP_SOURCE_REQUEST_RE = re.compile(
    r"(?is)^\s*(?:maybe\s+|just\s+)?(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?"
    r"(?P<verb>search for|look for|look up|find|locate|check(?: again)?)\s+"
    r"(?P<subject>.+?)[.?!]*\s*$"
)
_EXPLICIT_WEB_SEARCH_SUBJECT_RE = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:do|make|run|perform)?\s*"
    r"(?:(?:an?|the)\s+)?(?:online|web|internet)\s+search\s+for\s+(?P<subject>.+?)[.?!]*\s*$"
    r"|^\s*(?:please\s+)?search\s+(?:the\s+web|online|the\s+internet)\s+for\s+(?P<subject2>.+?)[.?!]*\s*$"
)
_WEB_SOURCE_HINT_RE = re.compile(
    r"(?i)\b(web|internet|online|website|websites|site|sites|google|bing|search engine|latest|current|news|headlines?)\b"
)
_WORKSPACE_SOURCE_HINT_RE = re.compile(
    r"(?i)\b(workspace|file|files|folder|folders|directory|directories|path|paths|filename|filenames|document|documents|doc|docs|pdf|txt|json|yaml|yml|csv|md|note|notes|script|scripts|code)\b"
)
_STATEISH_LOOKUP_SUBJECT_RE = re.compile(
    r"(?i)\b(memory|knowledge|world state|world model|records?|operational logs?)\b"
)
_WEB_SOURCE_CHOICE_RE = re.compile(
    r"(?is)^\s*(?:the\s+)?(?:web|internet|online|web search|search the web|online search)\s*[.!?]*\s*$"
)
_AFFIRMATIVE_WEB_SEARCH_FOLLOWUP_RE = re.compile(
    r"(?is)^\s*(?:yes(?:\s*,?\s*please)?|yeah|yep|yup|sure(?:\s*,?\s*(?:go ahead|please))?|"
    r"go ahead|please do|do it|sounds good|absolutely|definitely)\s*[.!?]*\s*$"
)
_WEB_SEARCH_OFFER_RE = re.compile(
    r"(?is)\b(?:search|check|look(?:\s+it)?\s+up|initiate)\b.{0,140}\b(?:web|internet|online|current|latest|real[- ]time|ephemeris)\b"
    r"|\b(?:web|internet|online|current|latest|real[- ]time|ephemeris)\b.{0,140}\b(?:search|check|look(?:\s+it)?\s+up|query)\b"
    r"|\b(?:perform|initiate)\s+(?:a\s+)?search\b"
)
_EXCERPT_IDENTIFICATION_REQUEST_RE = re.compile(
    r"(?i)\b("
    r"do you know (?:these|the) (?:lyrics|lyric|quote|quotes|words|lines)|"
    r"what (?:song|quote|book|poem|movie|show|speech) is this|"
    r"what is this from|where is this from|"
    r"who (?:said|wrote|sang) this|"
    r"who is this by|"
    r"are these (?:lyrics|lines|words|quotes) real|"
    r"is this (?:a real song|a real quote|real|correct)|"
    r"do you recognize (?:this|these)"
    r")\b"
)
_LYRIC_HINT_RE = re.compile(r"(?i)\b(lyric|lyrics|song|sang|sung|artist|band|track)\b")
_QUOTE_HINT_RE = re.compile(r"(?i)\b(quote|quoted|said|wrote|author|attributed|attribution|from)\b")
_SEARCH_REQUEST_RE = re.compile(
    r"(?is)^\s*(?:now\s+|then\s+|also\s+|just\s+)?(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?"
    r"(?:(?:search(?:\s+the\s+web)?|look\s+up|look\s+for|find|check)(?:\s+for)?\s+)(?P<subject>.+?)[.?!]*\s*$"
)
_SEARCH_REQUEST_TAIL_RE = re.compile(
    r"(?is)\s+(?:and|while)\s+"
    r"(?:"
    r"tell\s+me\s+what\s+you\s+(?:already\s+)?know(?:\s+about\s+it)?(?:\s+while\s+it\s+loads)?"
    r"|tell\s+me\s+what\s+you\s+find"
    r"|let\s+me\s+know\s+what\s+you\s+find"
    r"|give\s+me\s+(?:an\s+)?update"
    r"|keep\s+me\s+posted"
    r"|while\s+it\s+loads"
    r"|while\s+you(?:'re|\s+are)?\s+searching"
    r"|while\s+the\s+search\s+runs"
    r")\s*$"
)
_SEARCH_CORRECTION_RE = re.compile(
    r"(?is)^\s*(?:it\s+got\s+cut\s+off\s*,?\s*)?(?:no\s*,?\s*)?(?:sorry\s*,?\s*)?(?:i\s+meant|i\s+mean)\s+(?P<subject>.+?)\s*[.?!]*\s*$"
)
_SEARCH_GENERIC_SUBJECT_RE = re.compile(
    r"(?i)\b(models?|news|updates?|developments?|improvements?|results?|info|information|details?)\b"
)
_SEARCH_CONTEXT_SKIP_WORDS = {
    "a", "an", "and", "are", "for", "from", "latest", "current", "recent", "news", "now",
    "online", "search", "searching", "the", "these", "those", "this", "that",
    "updates", "update", "developments", "development", "improvements", "improvement", "details",
    "information", "info", "about", "in", "on", "of", "to", "up", "what", "recently",
}
_SEARCH_CONTEXT_GREETING_TOKENS = {
    "hello",
    "hi",
    "hey",
    "yo",
    "sup",
    "greetings",
}
_WORKSPACE_SOURCE_CHOICE_RE = re.compile(
    r"(?is)^\s*(?:the\s+)?(?:workspace|workspace files?|workspace file lookup|workspace lookup|file|files|document|documents|docs?)\s*[.!?]*\s*$"
)
_LOOKUP_SOURCE_CLARIFICATION_GOAL_RE = re.compile(
    r"(?is)^clarify lookup source \(web vs workspace\) for:\s*(?P<subject>.+?)\s*$"
)
_LOOKUP_SOURCE_CLARIFICATION_QUESTION_RE = re.compile(
    r'(?is)did you want me to search the web for "(?P<subject>.+?)",\s+or look for it in your workspace files\?'
)
_WORKSPACE_LOOKUP_TASK_GOAL_RE = re.compile(
    r"(?is)^find the workspace path that best matches ['\"](?P<subject>.+?)['\"]\.?\s*$"
)
_FINAL_STATE_CORRECTION_RE = re.compile(
    r"(?is)^\s*(?:i\s+think\s+)?(?:its|it's|the)\s+final\s+state\s+should\s+be\s+(?P<state>.+?)\s*[.?!]*\s*$"
)
_FILE_TARGET_CORRECTION_RE = re.compile(
    r"(?is)^\s*(?:it|that|this|the\s+file)?\s*was\s+(?P<correct>[\w./\\-]+)\s+not\s+(?P<wrong>[\w./\\-]+)\s*[.?!]*\s*$"
)
_UNDO_REQUEST_RE = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:undo(?:\s+(?:that|last\s+task))?|revert(?:\s+(?:that|last\s+task))?)\s*[.!?]*\s*$"
)
_COMPOUND_FILE_UNDO_REDO_RE = re.compile(
    r"(?is)\b(?:create|make|write)\b.*\bfile\b.*\b(?:delete|remove)\b.*\bundo\b.*\bredo\b"
)
_COMPOUND_FILE_CREATE_WITH_CONTENT_RE = re.compile(
    rf"(?is)^(?:in the workspace,\s*)?(?:please\s+)?(?:create|write|make)(?:\s+(?:a|the))?\s+(?:text\s+)?file\s+"
    rf"(?P<path>{_FILE_PATH_TOKEN})\s+with\s+(?:the\s+)?exact\s+contents?\s*:\s*(?P<content>.+?)\s+(?:and then|then)\b"
)
_COMPOUND_FILE_NAMED_WRITE_RE = re.compile(
    rf"(?is)^(?:in the workspace,\s*)?(?:please\s+)?create\s+(?:a\s+)?file\s+(?:named|called)\s+"
    rf"(?P<path>{_FILE_PATH_TOKEN})\s+and\s+(?:write|put)\s+(?P<content>.+?)\s+(?:and then|then)\b"
)
_ABSENT_STATE_HINT_RE = re.compile(
    r"(?i)\b(non[- ]?existing|nonexistent|not\s+existing|deleted|removed|gone|absent|missing)\b"
)

_STATE_MUTATION_ENGINE = StateMutationEngine()
_SCRIPT_LAUNCH_RE = re.compile(r"\b(run|execute|launch|start|open|play)\b", re.IGNORECASE)
_SCRIPT_APP_RE = re.compile(r"\b(game|application|app|script|player)\b", re.IGNORECASE)
_CODE_FILE_EXTENSIONS = {
    ".bat",
    ".c",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".lua",
    ".md",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".xml",
    ".yaml",
    ".yml",
}
_CODE_FOLLOWUP_HINT_RE = re.compile(
    r"\b(code|script|source|program|game|app|application|pygame|input|event|keyboard|mouse|movement|left|right|up|down|button|buttons|controls?|handler|logic|collision|ship|player|basket|star|bug|issue|debug|diagnos\w*|inspect|analy[sz]e|review|fix|repair|correct|implement|crash)\b",
    re.IGNORECASE,
)
_CODE_ANALYSIS_RE = re.compile(
    r"\b(inspect|analy[sz]e|analysis|diagnos\w*|identify|debug|review|audit|check|find|locate|look into|root cause|why)\b",
    re.IGNORECASE,
)
_CODE_EDIT_RE = re.compile(
    r"\b(fix|correct|repair|update|modify|edit|rewrite|change|implement|patch)\b",
    re.IGNORECASE,
)

NormalizerFn = Callable[[RouteDecision, str, Sequence[dict[str, Any]]], RouteDecision | None]
RouteInterceptorFn = Callable[[str, Sequence[dict[str, Any]]], dict[str, Any] | None]

_NORMALIZER_REGISTRY: list[NormalizerFn] = []
_ROUTE_INTERCEPTOR_REGISTRY: list[RouteInterceptorFn] = []


def register_normalizer(fn: NormalizerFn) -> NormalizerFn:
    _NORMALIZER_REGISTRY.append(fn)
    return fn


def register_route_interceptor(fn: RouteInterceptorFn) -> RouteInterceptorFn:
    _ROUTE_INTERCEPTOR_REGISTRY.append(fn)
    return fn


def normalize_route_decision(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]] | None = None,
) -> RouteDecision:
    history = [dict(item) for item in (recent_history or []) if isinstance(item, dict)]
    for normalizer in _NORMALIZER_REGISTRY:
        normalized = normalizer(decision, user_msg, history)
        if normalized is not None:
            return normalized
    return decision


def detect_route_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    text = str(user_msg or "").strip()
    if not text:
        return None
    history = [dict(item) for item in (recent_history or []) if isinstance(item, dict)]
    for interceptor in _ROUTE_INTERCEPTOR_REGISTRY:
        result = interceptor(text, history)
        if result is not None:
            return result
    return None


def _destructive_prompt_injection_reply(text: str) -> str:
    article_match = re.search(
        r"(?is)\bsummarize\s+this\s+article\s*:\s*(?P<article>.*?)(?:\bhidden\s+instruction\s*:|$)",
        text or "",
    )
    article = " ".join(str((article_match.group("article") if article_match else "") or "").split())
    if article:
        return (
            f"Summary: {article[:260].rstrip()}"
            + ("..." if len(article) > 260 else "")
            + "\n\nI cannot follow hidden or override-style instructions to remove workspace files."
        )
    return "I cannot follow override-style instructions to remove workspace files."


def _looks_like_excerpt_identification_request(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if not _EXCERPT_IDENTIFICATION_REQUEST_RE.search(raw):
        return False
    excerpt = _extract_identification_excerpt(raw)
    return bool(excerpt and len(excerpt.split()) >= 5)


def _extract_identification_excerpt(text: str) -> str:
    raw = " ".join(str(text or "").split()).strip()
    if not raw:
        return ""
    double_quoted = re.findall(r'"([^"\n]{5,200})"', raw)
    single_quoted = re.findall(r"(?<![A-Za-z0-9])'([^'\n]{5,200})'(?![A-Za-z0-9])", raw)
    for part in [*double_quoted, *single_quoted]:
        cleaned = str(part).strip(" ,.;:!?")
        if len(cleaned.split()) >= 5:
            return cleaned

    lowered = raw.lower()
    marker_patterns = [
        r"(?i)\bdo you know (?:these|the) (?:lyrics|lyric|quote|quotes|words|lines)\b",
        r"(?i)\bwhat (?:song|quote|book|poem|movie|show|speech) is this\b",
        r"(?i)\bwhat is this from\b",
        r"(?i)\bwhere is this from\b",
        r"(?i)\bwho (?:said|wrote|sang) this\b",
        r"(?i)\bwho is this by\b",
        r"(?i)\bare these (?:lyrics|lines|words|quotes) real\b",
        r"(?i)\bis this (?:a real song|a real quote|real|correct)\b",
        r"(?i)\bdo you recognize (?:this|these)\b",
    ]
    cut = len(raw)
    for pattern in marker_patterns:
        match = re.search(pattern, raw)
        if match:
            cut = min(cut, match.start())
    candidate = raw[:cut].strip(" ,.;:!?-")
    candidate = re.sub(r"(?i)^(?:and\s+|but\s+)?(?:these|this|the following)\s*:\s*", "", candidate).strip()
    if len(candidate.split()) >= 5:
        return candidate
    return ""


def _build_excerpt_identification_query(text: str) -> str:
    excerpt = _extract_identification_excerpt(text)
    if not excerpt:
        return ""
    suffix = " quote"
    if _LYRIC_HINT_RE.search(text):
        suffix = " lyrics song"
    elif _QUOTE_HINT_RE.search(text):
        suffix = " quote source"
    return f"\"{excerpt}\"{suffix}".strip()


def _extract_search_request_subject(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = _SEARCH_REQUEST_RE.match(raw)
    if not match:
        return ""
    subject = " ".join(str(match.group("subject") or "").split()).strip(" ,.;:!?")
    while subject:
        trimmed = _SEARCH_REQUEST_TAIL_RE.sub("", subject).strip(" ,.;:!?")
        if trimmed == subject:
            break
        subject = trimmed
    return subject


def _normalize_search_context_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9.+#-]+", str(text or "").lower()))


def _search_context_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9.+#-]+", str(text or "").lower()):
        if token in _SEARCH_CONTEXT_SKIP_WORDS:
            continue
        if len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _search_subject_is_generic(subject: str) -> bool:
    clean = " ".join(str(subject or "").split()).strip().lower()
    if not clean:
        return False
    tokens = _search_context_tokens(clean)
    if not tokens:
        return True
    if all(_SEARCH_GENERIC_SUBJECT_RE.fullmatch(token) for token in tokens):
        return True
    return len(tokens) <= 2 and bool(_SEARCH_GENERIC_SUBJECT_RE.search(clean))


def _extract_contextual_search_anchor(recent_history: Sequence[dict[str, Any]]) -> str:
    runtime = extract_latest_runtime_context_fields(recent_history)
    candidates = [
        str(runtime.get("search_query") or "").strip(),
        str(runtime.get("previous_user_request") or "").strip(),
    ]
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            candidates.append(content)
    for candidate in candidates:
        tokens = _search_context_tokens(candidate)
        if not tokens or all(token in _SEARCH_CONTEXT_GREETING_TOKENS for token in tokens):
            continue
        return " ".join(tokens[:4]).strip()
    return ""


def _repair_generic_search_followup_query(subject: str, recent_history: Sequence[dict[str, Any]]) -> str:
    clean_subject = " ".join(str(subject or "").split()).strip(" ,.;:!?")
    if not clean_subject:
        return ""
    if not _search_subject_is_generic(clean_subject):
        return clean_subject
    anchor = _extract_contextual_search_anchor(recent_history)
    if not anchor:
        return clean_subject
    subject_tokens = _search_context_tokens(clean_subject)
    anchor_tokens = _search_context_tokens(anchor)
    merged: list[str] = []
    for token in anchor_tokens + subject_tokens:
        if token not in merged:
            merged.append(token)
    if not merged:
        return clean_subject
    return " ".join(merged)


def _derive_search_query_from_turn(
    current_text: str,
    recent_history: Sequence[dict[str, Any]],
    *,
    prefer_context_repair: bool = True,
) -> str:
    text = str(current_text or "").strip()
    if not text:
        return ""

    candidates: list[str] = []

    search_subject = _extract_search_request_subject(text)
    if search_subject:
        candidates.append(search_subject)

    lookup_subject = _extract_lookup_source_subject(text)
    if lookup_subject:
        candidates.append(lookup_subject)

    cleaned_text = _clean_web_offer_query(text)
    if cleaned_text:
        candidates.append(cleaned_text)

    runtime = extract_latest_runtime_context_fields(recent_history)
    previous_request = str(runtime.get("previous_user_request") or "").strip()
    previous_subject = _extract_lookup_source_subject(previous_request)
    if previous_subject:
        candidates.append(previous_subject)
    elif previous_request and _normalize_lookup_text(previous_request) != _normalize_lookup_text(text):
        candidates.append(previous_request)

    for candidate in candidates:
        cleaned = _clean_web_offer_query(candidate)
        if not cleaned:
            continue
        if prefer_context_repair:
            repaired = _repair_generic_search_followup_query(cleaned, recent_history)
            if repaired:
                cleaned = repaired
        if not _query_is_generic_web_placeholder(cleaned):
            return cleaned

    fallback = _extract_recent_web_search_topic(recent_history, text)
    if fallback:
        repaired = _repair_generic_search_followup_query(fallback, recent_history)
        return repaired or fallback
    return ""


def annotate_file_stage_kinds(decision: RouteDecision) -> RouteDecision:
    if not decision or str(decision.get("decision") or "").strip().upper() != "TASK":
        return decision

    card = dict(decision.get("card") or {})
    stages = card.get("stages") or []
    if not isinstance(stages, list) or not stages:
        return decision

    updated_stages: list[StageCard] = []
    changed = False
    for raw_stage in stages:
        if not isinstance(raw_stage, dict):
            updated_stages.append(raw_stage)
            continue
        stage = dict(raw_stage)
        if str(stage.get("stage_type") or "").strip().upper() == "FILE_WORK":
            file_stage_kind = str(stage.get("file_stage_kind") or "").strip().upper()
            if file_stage_kind not in {
                "INSPECTION",
                "CONTENT_EDIT",
                "STRUCTURE_PREP",
                "BROAD_REORG",
                "SCRIPT_LAUNCH",
                "DEPENDENCY_RECOVERY",
                "UNKNOWN",
            }:
                stage["file_stage_kind"] = FileWorkEngine.classify(stage)
                changed = True
        updated_stages.append(stage)

    if not changed:
        return decision

    card["stages"] = updated_stages
    updated = dict(decision)
    updated["card"] = card
    return updated


@register_normalizer
def _registered_speculative_task_chat(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del recent_history
    return _normalize_speculative_task_idea_to_chat(decision, user_msg)


@register_normalizer
def _registered_live_environment_chat(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del recent_history
    return _normalize_live_environment_chat(decision, user_msg)


@register_normalizer
def _registered_excerpt_identification_search(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del recent_history
    return _normalize_excerpt_identification_search(decision, user_msg)


@register_normalizer
def _registered_lookup_source_choice_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_lookup_source_choice_followup(decision, user_msg, recent_history)


@register_normalizer
def _registered_web_search_offer_affirmative_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del decision
    return _normalize_web_search_offer_affirmative_followup(user_msg, recent_history)


@register_normalizer
def _registered_persona_web_search_loopback(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_persona_web_search_loopback(decision, user_msg, recent_history)


@register_normalizer
def _registered_contextual_search_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_contextual_search_followup(decision, user_msg, recent_history)


@register_normalizer
def _registered_search_correction_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_search_correction_followup(decision, user_msg, recent_history)


@register_normalizer
def _registered_malformed_browser_url_clarification(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del decision, recent_history
    text = str(user_msg or "").strip()
    if not text:
        return None
    if not has_placeholder_browser_url(text):
        return None
    if not re.search(r"(?i)\b(browser|page|site|website|click|open|visit|navigate|download|read|title|heading)\b", text):
        return None
    return _build_browser_url_clarification_card(
        "The browser URL still contains a placeholder like '<fixture-port>'. Please give me the real URL or port first."
    )


@register_normalizer
def _registered_explicit_browser_task(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del decision, recent_history
    return _normalize_explicit_browser_task(user_msg)


@register_normalizer
def _registered_explicit_web_search(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_explicit_web_search(decision, user_msg, recent_history)


@register_normalizer
def _registered_contextual_browser_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del decision
    return _normalize_contextual_browser_followup(user_msg, recent_history)


@register_normalizer
def _registered_direct_file_work(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del decision, recent_history
    return _normalize_direct_file_work(user_msg)


@register_normalizer
def _registered_ambiguous_lookup_source_clarification(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_ambiguous_lookup_source_clarification(
        decision,
        user_msg,
        recent_history,
    )


@register_normalizer
def _registered_workspace_document_lookup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_workspace_document_lookup(decision, user_msg, recent_history)


@register_normalizer
def _registered_workspace_file_delete_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_workspace_file_delete_followup(decision, user_msg, recent_history)


@register_normalizer
def _registered_code_target_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _normalize_code_target_followup(decision, user_msg, recent_history)


@register_normalizer
def _registered_interactive_runtime_verification(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    del recent_history
    return _normalize_interactive_runtime_verification(decision, user_msg)


@register_normalizer
def _registered_state_mutation_normalization(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return _STATE_MUTATION_ENGINE.normalize_route_decision(
        decision=decision,
        user_msg=user_msg,
        recent_history=recent_history,
    )


@register_normalizer
def _registered_extension_file_work(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    if not decision or decision.get("decision") != "TASK":
        return None

    card = dict(decision.get("card") or {})
    stages = [dict(stage) for stage in card.get("stages") or []]
    if not stages:
        return None
    return _normalize_extension_file_work(decision, card, stages, user_msg, recent_history)


@register_route_interceptor
def _registered_destructive_prompt_injection_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    del recent_history
    if not _DESTRUCTIVE_PROMPT_INJECTION_RE.search(user_msg or ""):
        return None
    reply = _destructive_prompt_injection_reply(user_msg)
    return {
        "kind": "DESTRUCTIVE_PROMPT_INJECTION_REFUSAL",
        "next_stage": "PERSONA",
        "stats_decision": "CHAT",
        "bypass": "destructive_prompt_injection_refusal",
        "log_message": "   -> Destructive prompt-injection guard matched. Skipping FILE_WORK.",
        "route_decision": {
            "decision": "CHAT",
            "interceptor": "DESTRUCTIVE_PROMPT_INJECTION_REFUSAL",
            "system_notice": {
                "kind": "destructive_prompt_injection_refusal",
                "reply": reply,
            },
        },
    }


@register_route_interceptor
def _registered_pending_file_target_confirmation_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    pending = extract_pending_file_target_confirmation(recent_history)
    if pending is None:
        return None
    resolution = classify_pending_file_target_confirmation_reply(user_msg, pending)
    if resolution is None:
        return None

    exact_target = str(pending.get("exact_target") or "").strip()
    candidates = [str(item).strip() for item in (pending.get("candidates") or []) if str(item).strip()]
    chosen_target = str(resolution.get("chosen_target") or "").strip()
    decision = str(resolution.get("decision") or "").strip().lower()

    if decision in {"confirm", "choose"} and exact_target and chosen_target:
        base_route = dict(pending.get("route_decision") or {})
        confirmed_route = build_confirmed_route_decision(
            base_route,
            exact_target=exact_target,
            chosen_target=chosen_target,
        )
        return {
            "kind": "FILE_TARGET_CONFIRMATION",
            "next_stage": "MANAGER",
            "stats_decision": str((confirmed_route.get("decision") or "TASK")).strip().upper(),
            "bypass": "file_target_confirmation",
            "log_message": f"   -> File-target confirmation resolved to '{chosen_target}'. Skipping Secretary/router LLM.",
            "route_decision": confirmed_route,
        }

    if decision == "decline":
        reply = "Understood. I will leave the workspace unchanged."
        if exact_target and candidates:
            reply = f"Understood. I will not substitute `{candidates[0]}` for `{exact_target}`."
        return {
            "kind": "FILE_TARGET_CONFIRMATION_CANCELLED",
            "next_stage": "PERSONA",
            "stats_decision": "CHAT",
            "bypass": "file_target_confirmation_cancelled",
            "log_message": "   -> File-target confirmation declined. Skipping Secretary/router LLM.",
            "route_decision": {
                "decision": "CHAT",
                "interceptor": "FILE_TARGET_CONFIRMATION_CANCELLED",
                "system_notice": {
                    "kind": "file_target_confirmation_cancelled",
                    "reply": reply,
                    "exact_target": exact_target,
                    "candidates": candidates[:3],
                },
            },
        }

    return None


@register_route_interceptor
def _registered_file_state_correction_ack_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    notice = _build_file_state_correction_notice(user_msg, recent_history)
    if notice is None:
        return None
    return {
        "kind": "FILE_STATE_CORRECTION_ACK",
        "next_stage": "PERSONA",
        "stats_decision": "CHAT",
        "bypass": "file_state_correction_ack",
        "log_message": "   -> File-state correction matched. Skipping Secretary/router LLM.",
        "route_decision": {
            "decision": "CHAT",
            "interceptor": "FILE_STATE_CORRECTION_ACK",
            "system_notice": notice,
        },
    }


@register_route_interceptor
def _registered_file_target_correction_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    notice = _build_file_target_correction_notice(user_msg, recent_history)
    if notice is None:
        return None
    wrong_target = str(notice.get("wrong_target") or "").strip()
    correct_target = str(notice.get("correct_target") or "").strip()
    return {
        "kind": "FILE_TARGET_CORRECTION",
        "next_stage": "UNDO",
        "stats_decision": "TASK",
        "bypass": "file_target_correction",
        "log_message": "   -> File-target correction matched. Undoing the mistaken file mutation before continuing.",
        "route_decision": {
            "decision": "TASK",
            "interceptor": "FILE_TARGET_CORRECTION",
            "system_notice": notice,
            "card": {
                "goal": f"Undo the mistaken change to '{wrong_target}' after the user corrected the target to '{correct_target}'.",
                "context": [
                    f"The previous successful file action targeted '{wrong_target}', but the user corrected the intended target to '{correct_target}'.",
                    "First undo the mistaken file mutation so the workspace returns to the pre-mistake state.",
                    "After the undo, report whether the corrected target already satisfies the intended final state.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Undo the mistaken file mutation affecting '{wrong_target}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"The mistaken mutation affecting '{wrong_target}' is reverted.",
                        "file_stage_kind": "CONTENT_EDIT",
                    }
                ],
            },
        },
    }


@register_route_interceptor
def _registered_undo_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    del recent_history
    if _UNDO_REQUEST_RE.match(str(user_msg or "").strip()):
        return {
            "kind": "UNDO",
            "next_stage": "UNDO",
            "stats_decision": "TASK",
            "bypass": "undo",
            "log_message": "   -> Undo interceptor matched. Skipping Secretary/router LLM.",
            "route_decision": {
                "decision": "TASK",
                "interceptor": "UNDO",
                "card": {
                    "goal": "Undo the last mutating file task.",
                    "stages": [
                        {
                            "stage_goal": "Undo the most recent mutating file task.",
                            "stage_type": "FILE_WORK",
                            "success_condition": "The latest recorded reversible file changes are restored.",
                            "file_stage_kind": "CONTENT_EDIT",
                        }
                    ],
                },
            },
        }
    return None


@register_route_interceptor
def _registered_explain_last_turn_interceptor(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    text = str(user_msg or "").strip()
    if not text:
        return None

    explicit_request = looks_like_turn_explanation_request(text)
    followup_request = looks_like_turn_explanation_followup(text)
    if not explicit_request and not followup_request:
        return None

    snapshot = extract_last_turn_explanation_snapshot(recent_history)
    if followup_request and not bool((snapshot or {}).get("explain_active")):
        return None

    detail_level = "detailed" if followup_request else "default"
    return {
        "kind": "EXPLAIN",
        "next_stage": "EXPLAIN",
        "stats_decision": "CHAT",
        "bypass": "explain_last_turn",
        "log_message": "   -> Explain interceptor matched. Skipping Secretary/router LLM.",
        "route_decision": {
            "decision": "CHAT",
            "interceptor": "EXPLAIN",
            "system_notice": {
                "kind": "explain_last_turn",
                "detail_level": detail_level,
                "available": bool(snapshot),
                "snapshot": dict(snapshot or {}),
            },
        },
    }


def _normalize_live_environment_chat(
    decision: RouteDecision,
    user_msg: str,
) -> RouteDecision | None:
    if not decision or str(decision.get("decision") or "").strip().upper() != "SEARCH":
        return None

    text = str(user_msg or "").strip()
    if not text:
        return None

    # Keep route-time behavior aligned with the readonly-state guard: current
    # date/time/day questions should stay in CHAT so persona can answer from
    # [ENVIRONMENT] instead of invoking background search.
    if not looks_like_live_environment_query(text):
        return None

    return {"decision": "CHAT"}


def _build_file_state_correction_notice(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    text = str(user_msg or "").strip()
    if not text:
        return None
    match = _FINAL_STATE_CORRECTION_RE.match(text)
    if not match:
        return None
    requested_state = str(match.group("state") or "").strip()
    if not _ABSENT_STATE_HINT_RE.search(requested_state):
        return None

    runtime = extract_latest_runtime_context_fields(recent_history)
    if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
        return None
    if "FILE OPERATION SUCCESS" not in str(runtime.get("execution_status") or "").upper():
        return None

    task_goal = str(runtime.get("task_goal") or "").strip()
    runtime_note = str(runtime.get("runtime_note") or "").strip()
    target = _extract_named_runtime_file_target(task_goal) or _extract_runtime_note_target(runtime_note)
    if not target or not _runtime_note_indicates_absent(runtime_note, target):
        return None

    return {
        "kind": "file_state_correction_ack",
        "target": target,
        "desired_state": "absent",
        "reply": f"Indeed. The verified final state was already that `{target}` did not exist.",
    }


def _build_file_target_correction_notice(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    text = str(user_msg or "").strip()
    if not text:
        return None
    match = _FILE_TARGET_CORRECTION_RE.match(text)
    if not match:
        return None

    correct_target = _clean_route_path(match.group("correct"))
    wrong_target = _clean_route_path(match.group("wrong"))
    if not correct_target or not wrong_target or correct_target.lower() == wrong_target.lower():
        return None

    runtime = extract_latest_runtime_context_fields(recent_history)
    if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
        return None
    if "FILE OPERATION SUCCESS" not in str(runtime.get("execution_status") or "").upper():
        return None

    task_goal = str(runtime.get("task_goal") or "").strip()
    runtime_note = str(runtime.get("runtime_note") or "").strip()
    goal_target = _extract_named_runtime_file_target(task_goal)
    runtime_target = _extract_runtime_note_target(runtime_note)
    if goal_target and not _route_targets_match(correct_target, goal_target):
        return None
    if runtime_target and not _route_targets_match(wrong_target, runtime_target):
        return None
    if not goal_target and not runtime_target:
        return None

    desired_state = "absent" if _goal_requests_absent_file_state(task_goal) else ""
    return {
        "kind": "file_target_correction",
        "correct_target": goal_target or correct_target,
        "wrong_target": runtime_target or wrong_target,
        "desired_state": desired_state,
    }


def _goal_requests_absent_file_state(task_goal: str) -> bool:
    goal = str(task_goal or "").strip().lower()
    if not goal:
        return False
    return any(
        phrase in goal
        for phrase in (
            "does not exist",
            "remain deleted",
            "is deleted",
            "be deleted",
            "non-existing final state",
        )
    )


def _extract_named_runtime_file_target(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    for pattern in (
        r"(?is)\bfile\s+['\"](?P<target>[^'\"]+)['\"]",
        r"(?is)\bcreate\s+['\"](?P<target>[^'\"]+)['\"]",
        r"(?is)\bdelete(?:\s+the\s+file)?\s+['\"](?P<target>[^'\"]+)['\"]",
        r"(?is)\brestore(?:\s+the\s+file)?\s+['\"](?P<target>[^'\"]+)['\"]",
    ):
        match = re.search(pattern, raw)
        if not match:
            continue
        candidate = _clean_route_path(match.group("target"))
        if candidate:
            return candidate
    return _extract_file_target_from_texts([raw])


def _extract_runtime_note_target(runtime_note: str) -> str:
    raw = str(runtime_note or "").strip()
    if not raw:
        return ""
    for pattern in (
        r"(?is)\b(?:deleted|removed|restored|updated|moved|copied)\s+(?P<target>[\w./\\-]+)",
        r"(?is)\bin\s+(?P<target>[\w./\\-]+)\b",
    ):
        match = re.search(pattern, raw)
        if not match:
            continue
        candidate = _clean_route_path(match.group("target"))
        if candidate:
            return candidate
    return _extract_named_runtime_file_target(raw)


def _runtime_note_indicates_absent(runtime_note: str, target: str) -> bool:
    note = str(runtime_note or "").strip()
    if not note or not target:
        return False
    lowered = note.lower()
    if not any(token in lowered for token in ("deleted", "removed", "already absent", "already satisfied")):
        return False
    noted_target = _extract_runtime_note_target(note)
    return not noted_target or _route_targets_match(target, noted_target)


def _route_targets_match(left: str, right: str) -> bool:
    left_clean = _clean_route_path(left).lower()
    right_clean = _clean_route_path(right).lower()
    if not left_clean or not right_clean:
        return False
    if left_clean == right_clean:
        return True

    left_name = PurePosixPath(left_clean).name
    right_name = PurePosixPath(right_clean).name
    if left_name == right_name:
        return True

    left_stem = PurePosixPath(left_name).stem
    right_stem = PurePosixPath(right_name).stem
    return bool(left_stem and right_stem and left_stem == right_stem)


def _normalize_explicit_web_search(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text or not _request_explicitly_scopes_lookup_to_web(text):
        return None

    subject = _derive_search_query_from_turn(text, recent_history)

    if _request_has_strong_workspace_scope(text):
        return _build_lookup_source_clarification_card(subject)

    existing_query = str(((decision or {}).get("card") or {}).get("query") or "").strip()
    query = str(subject or existing_query or "").strip()
    if not query:
        return _build_lookup_source_clarification_card(
            "",
            question_override="What exact topic should I search the web for?",
        )
    query = _clean_web_offer_query(query)
    if not query:
        return _build_lookup_source_clarification_card(
            "",
            question_override="What exact topic should I search the web for?",
        )
    return {
        "decision": "SEARCH",
        "card": {
            "query": query,
        },
    }


def _normalize_explicit_browser_task(user_msg: str) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text:
        return None
    url = extract_browser_url(text)
    if not url:
        return None
    if not shared_looks_like_explicit_browser_request(text):
        return None
    return build_browser_task_card(text, url)


def _build_browser_url_clarification_card(question: str) -> RouteDecision:
    question_text = str(question or "").strip() or "What exact browser URL should I open?"
    return {
        "decision": "TASK",
        "card": {
            "goal": "Clarify the browser URL before acting.",
            "context": [
                "The latest browser request includes a malformed or placeholder URL.",
                f"Preferred clarification question: {question_text}",
            ],
            "stages": [
                {
                    "stage_goal": f"Ask the user: {question_text}",
                    "stage_type": "CHAT",
                    "success_condition": "A concise browser-URL clarification question is ready for the user.",
                    "allowed_tools": [],
                }
            ],
        },
    }


def looks_like_explicit_browser_request(text: str) -> bool:
    return shared_looks_like_explicit_browser_request(text)


def _normalize_contextual_browser_followup(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    return build_browser_context_followup_route(user_msg, recent_history)


def _normalize_lookup_source_choice_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text:
        return None

    runtime = extract_latest_runtime_context_fields(recent_history)

    # A genuine source choice is short ("web", "web pls", "workspace files").
    # A longer message is a new search intent that should not be hijacked by
    # this normalizer — let it fall through to _normalize_explicit_web_search.
    normalized_text = _normalize_lookup_text(text)
    if len(normalized_text.split()) > 6:
        return None

    source_choice = _classify_lookup_source_choice(text)
    if not source_choice:
        return None

    subject = _extract_lookup_source_followup_subject(runtime, recent_history)
    if not subject:
        return None

    if source_choice == "web":
        return {
            "decision": "SEARCH",
            "card": {
                "query": subject,
            },
        }

    explicit_target = _extract_file_target_from_texts([subject])
    if explicit_target:
        return _build_explicit_workspace_file_search_card(explicit_target)
    if _subject_looks_like_workspace_document(subject):
        return _build_workspace_document_search_card(subject)
    return None


def _normalize_web_search_offer_affirmative_followup(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text or not _AFFIRMATIVE_WEB_SEARCH_FOLLOWUP_RE.match(text):
        return None

    assistant_offer = _latest_assistant_web_search_offer(recent_history)
    if not assistant_offer:
        return None

    query = _derive_query_from_web_search_offer(assistant_offer, recent_history)
    if not query:
        return None
    return {
        "decision": "SEARCH",
        "card": {
            "query": query,
        },
    }


def _normalize_excerpt_identification_search(
    decision: RouteDecision,
    user_msg: str,
) -> RouteDecision | None:
    if str((decision or {}).get("decision") or "").strip().upper() != "CHAT":
        return None
    text = str(user_msg or "").strip()
    if not _looks_like_excerpt_identification_request(text):
        return None
    query = _build_excerpt_identification_query(text)
    if not query:
        return None
    return {
        "decision": "SEARCH",
        "card": {
            "query": query,
        },
        "source_scope": "web",
        "confidence": "high",
    }


def _normalize_persona_web_search_loopback(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    if str((decision or {}).get("decision") or "").strip().upper() != "CHAT":
        return None
    latest_assistant = _latest_history_assistant_message(recent_history)
    if not latest_assistant:
        return None
    if not _WEB_SEARCH_OFFER_RE.search(latest_assistant):
        return None
    user_query = _clean_web_offer_query(user_msg)
    query = _derive_query_from_web_search_offer(latest_assistant, recent_history)
    if not query or _query_is_generic_web_placeholder(query):
        query = user_query
    if not query:
        return None
    return {
        "decision": "SEARCH",
        "card": {
            "query": query,
        },
        "source_scope": "web",
        "confidence": "high",
    }


def _normalize_contextual_search_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text:
        return None
    if str((decision or {}).get("decision") or "").strip().upper() != "SEARCH":
        return None
    if _SEARCH_CORRECTION_RE.match(text):
        return None
    query = _derive_search_query_from_turn(text, recent_history)
    if not query:
        return None
    return {
        "decision": "SEARCH",
        "card": {
            "query": query,
        },
        "source_scope": "web",
        "confidence": "high",
    }


def _normalize_search_correction_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text:
        return None
    match = _SEARCH_CORRECTION_RE.match(text)
    if not match:
        return None
    runtime = extract_latest_runtime_context_fields(recent_history)
    previous_route = str(runtime.get("previous_route") or "").strip().upper()
    previous_query = str(runtime.get("search_query") or "").strip()
    latest_decision = str((decision or {}).get("decision") or "").strip().upper()
    if previous_route != "SEARCH" and latest_decision != "SEARCH" and not previous_query:
        return None
    subject = _clean_web_offer_query(match.group("subject"))
    if not subject:
        return None
    return {
        "decision": "SEARCH",
        "card": {
            "query": subject,
        },
        "source_scope": "web",
        "confidence": "high",
    }


def _latest_history_assistant_message(recent_history: Sequence[dict[str, Any]]) -> str:
    if not recent_history:
        return ""
    latest = recent_history[-1]
    if not isinstance(latest, dict):
        return ""
    if str(latest.get("role") or "").strip().lower() != "assistant":
        return ""
    content = str(latest.get("content") or "").strip()
    if not content or content.lower() == "thinking...":
        return ""
    return content


def _query_is_generic_web_placeholder(text: str) -> bool:
    normalized = _normalize_lookup_text(text)
    return normalized in {
        "",
        "online",
        "web",
        "internet",
        "the web",
        "the internet",
        "check online",
        "look online",
        "search online",
        "check the web",
        "search the web",
    }


def _latest_assistant_web_search_offer(recent_history: Sequence[dict[str, Any]]) -> str:
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "assistant":
            continue
        content = str(item.get("content") or "").strip()
        if not content or content.lower() == "thinking...":
            continue
        if _WEB_SEARCH_OFFER_RE.search(content):
            return content
        return ""
    return ""


def _derive_query_from_web_search_offer(
    assistant_offer: str,
    recent_history: Sequence[dict[str, Any]],
) -> str:
    offer = re.sub(r"(?is)</?think>", " ", str(assistant_offer or ""))
    quoted = re.findall(r"['\"]([^'\"]{3,160})['\"]", offer)
    for item in reversed(quoted):
        cleaned = _clean_web_offer_query(item)
        if cleaned:
            return cleaned

    phrase_patterns = [
        r"(?is)\b(?:current|latest|precise|real[- ]time)\s+(?P<subject>[^.?!]{3,120})",
        r"(?is)\bsearch\s+(?:for|about)?\s*(?P<subject>[^.?!]{3,120})",
        r"(?is)\bcheck\s+(?:for|about)?\s*(?P<subject>[^.?!]{3,120})",
    ]
    for pattern in phrase_patterns:
        match = re.search(pattern, offer)
        if not match:
            continue
        cleaned = _clean_web_offer_query(match.group("subject"))
        if cleaned:
            return cleaned

    runtime = extract_latest_runtime_context_fields(recent_history)
    runtime_query = _clean_web_offer_query(str(runtime.get("search_query") or ""))
    if runtime_query:
        return runtime_query

    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content or _AFFIRMATIVE_WEB_SEARCH_FOLLOWUP_RE.match(content):
            continue
        if re.search(r"(?i)\bdid you check\b|\bhow do you know\b", content):
            continue
        cleaned = _clean_web_offer_query(content)
        if cleaned:
            return cleaned

    previous_request = str(runtime.get("previous_user_request") or "").strip()
    if not re.search(r"(?i)\bdid you check\b|\bhow do you know\b", previous_request):
        return _clean_web_offer_query(previous_request)
    return ""


def _clean_web_offer_query(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip(" \"'`.,;:!?")
    if not cleaned:
        return ""
    cleaned = re.sub(
        r"(?is)\b(?:for this exact moment|for the exact moment|if you (?:wish|require).*)$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?is)\bor any other real[- ]time data\b.*$", "", cleaned)
    cleaned = re.sub(r"(?is)\bi can\b.*$", "", cleaned)
    while cleaned:
        trimmed = _SEARCH_REQUEST_TAIL_RE.sub("", cleaned).strip(" \"'`.,;:!?")
        if trimmed == cleaned:
            break
        cleaned = trimmed
    cleaned = cleaned.strip(" \"'`.,;:!?")
    if len(cleaned) < 3:
        return ""
    return cleaned[:180]


def _extract_recent_web_search_topic(recent_history: Sequence[dict[str, Any]], current_text: str) -> str:
    current_norm = _normalize_lookup_text(current_text)
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        normalized = _normalize_lookup_text(content)
        if not normalized or normalized == current_norm:
            continue
        extracted = _extract_lookup_source_subject(content)
        if extracted:
            return _clean_web_offer_query(extracted)
        if re.search(r"(?i)\b(?:yes|yeah|yep|sure|go ahead|please do|do it)\b", content):
            continue
        cleaned = _clean_web_offer_query(content)
        if cleaned:
            return cleaned
    return ""


def _extract_lookup_source_followup_subject(
    runtime: dict[str, str],
    recent_history: Sequence[dict[str, Any]],
) -> str:
    task_goal = str(runtime.get("task_goal") or "").strip()
    match = _LOOKUP_SOURCE_CLARIFICATION_GOAL_RE.match(task_goal)
    if match:
        subject = _clean_document_lookup_subject(match.group("subject"))
        if subject:
            repaired = _repair_lookup_subject_from_context(subject, recent_history)
            if repaired:
                return repaired
            return subject

    match = _WORKSPACE_LOOKUP_TASK_GOAL_RE.match(task_goal)
    if match:
        subject = _clean_document_lookup_subject(match.group("subject"))
        if subject:
            repaired = _repair_lookup_subject_from_context(subject, recent_history)
            if repaired:
                return repaired
            return subject

    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        match = _LOOKUP_SOURCE_CLARIFICATION_QUESTION_RE.search(content)
        if match:
            subject = _clean_document_lookup_subject(match.group("subject"))
            if subject:
                repaired = _repair_lookup_subject_from_context(subject, recent_history)
                if repaired:
                    return repaired
                return subject

    previous_request = str(runtime.get("previous_user_request") or "").strip()
    subject = _extract_lookup_source_subject(previous_request)
    repaired = _repair_lookup_subject_from_context(subject, recent_history)
    if repaired:
        return repaired
    return subject


def _repair_lookup_subject_from_context(
    subject: str,
    recent_history: Sequence[dict[str, Any]],
) -> str:
    clean_subject = _clean_document_lookup_subject(subject)
    if not clean_subject:
        return ""
    if not _lookup_subject_looks_like_first_person_stt_assertion(clean_subject):
        return ""
    fallback = _extract_prior_contextual_lookup_subject(
        recent_history,
        blocked_subject=clean_subject,
    )
    return fallback or ""


def _lookup_subject_looks_like_first_person_stt_assertion(subject: str) -> bool:
    raw = str(subject or "").strip()
    if not raw:
        return False
    if not re.match(r"(?i)^(?:i[' ]?m|i am|im)\b", raw):
        return False
    return bool(
        re.search(
            r"\b(latest|current|recent|news|release|version|model|update|quote|lyrics|source|price|benchmark|result|results)\b",
            raw,
            re.IGNORECASE,
        )
    )


def _extract_prior_contextual_lookup_subject(
    recent_history: Sequence[dict[str, Any]],
    *,
    blocked_subject: str,
) -> str:
    blocked_norm = _normalize_lookup_text(blocked_subject)
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        normalized = _normalize_lookup_text(content)
        if not normalized or normalized == blocked_norm:
            continue
        if _classify_lookup_source_choice(content):
            continue
        extracted = _extract_lookup_source_subject(content)
        if extracted and _normalize_lookup_text(extracted) != blocked_norm:
            return _clean_web_offer_query(extracted)
        if re.search(r"(?i)\bdid you check\b|\bhow do you know\b", content):
            continue
        cleaned = _clean_web_offer_query(content)
        if cleaned and _normalize_lookup_text(cleaned) != blocked_norm:
            return cleaned
    return ""


def _classify_lookup_source_choice(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _WEB_SOURCE_CHOICE_RE.match(raw):
        return "web"
    if _WORKSPACE_SOURCE_CHOICE_RE.match(raw):
        return "workspace"

    normalized = _normalize_lookup_text(raw)
    if not normalized:
        return ""
    tokens = set(normalized.split())
    if not tokens:
        return ""

    web_markers = {"web", "internet", "online"}
    workspace_markers = {
        "workspace",
        "file",
        "files",
        "document",
        "documents",
        "doc",
        "docs",
        "folder",
        "folders",
        "directory",
        "directories",
    }

    has_web = bool(tokens & web_markers) or "search the web" in normalized or "web search" in normalized
    has_workspace = bool(tokens & workspace_markers) or "my files" in normalized

    if has_web and not has_workspace:
        return "web"
    if has_workspace and not has_web:
        return "workspace"
    return ""


def _normalize_ambiguous_lookup_source_clarification(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text:
        return None

    subject = _extract_lookup_source_subject(text)
    if not subject:
        return None
    explicit_web = _request_explicitly_scopes_lookup_to_web(text)
    explicit_workspace = _request_explicitly_scopes_lookup_to_workspace(text)
    if explicit_web and _request_has_strong_workspace_scope(text):
        return _build_lookup_source_clarification_card(subject)
    if explicit_web or explicit_workspace:
        return None
    if _lookup_source_is_resolved_by_context(
        decision=decision,
        subject=subject,
        recent_history=recent_history,
        current_text=text,
    ):
        return None

    return _resolve_ambiguous_lookup_source_from_router(decision, subject)


def _resolve_ambiguous_lookup_source_from_router(
    decision: RouteDecision,
    subject: str,
) -> RouteDecision:
    # This function is only called when the request has no explicit web or
    # workspace keywords, making the source genuinely ambiguous.  Always ask
    # for clarification regardless of the router's confidence field — the LLM
    # over-confidently assigns high confidence to phrasing like "search for X"
    # even when the source is not determinable from the wording alone.
    return _build_lookup_source_clarification_card(
        subject,
        question_override=str((decision or {}).get("question_if_uncertain") or "").strip(),
    )


def _extract_lookup_source_subject(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    explicit_web_match = _EXPLICIT_WEB_SEARCH_SUBJECT_RE.match(raw)
    if explicit_web_match:
        explicit_subject = _clean_document_lookup_subject(
            explicit_web_match.group("subject") or explicit_web_match.group("subject2") or ""
        )
        if explicit_subject:
            return _clean_web_offer_query(explicit_subject) or explicit_subject
    match = _LOOKUP_SOURCE_REQUEST_RE.match(raw)
    if not match:
        return ""
    subject = _clean_document_lookup_subject(match.group("subject"))
    if subject:
        return _clean_web_offer_query(subject) or subject
    fallback = str(match.group("subject") or "").strip()
    fallback = re.sub(r"(?i)\b(?:again|please|online|on the web|on web|in the workspace|in my workspace|in workspace)\b", "", fallback).strip(" ,.;:!?")
    return _clean_web_offer_query(fallback) or fallback


def _request_explicitly_scopes_lookup_to_web(text: str) -> bool:
    return bool(_WEB_SOURCE_HINT_RE.search(str(text or "")))


def _request_has_strong_workspace_scope(text: str) -> bool:
    return bool(_WORKSPACE_SOURCE_HINT_RE.search(str(text or "")))


def _request_explicitly_scopes_lookup_to_workspace(text: str) -> bool:
    raw = str(text or "")
    return bool(_request_has_strong_workspace_scope(raw))


def _lookup_source_is_resolved_by_context(
    *,
    decision: RouteDecision,
    subject: str,
    recent_history: Sequence[dict[str, Any]],
    current_text: str,
) -> bool:
    runtime = extract_latest_runtime_context_fields(recent_history)
    previous_route = str(runtime.get("previous_route") or "").strip().upper()
    normalized_subject = _normalize_lookup_text(subject)
    if not normalized_subject:
        return False

    recent_file_targets = _collect_recent_file_targets(decision, recent_history, current_text=current_text)
    recent_subject = _extract_recent_document_lookup_subject(recent_history, current_text=current_text)
    generic_reference = _lookup_subject_is_generic_reference(normalized_subject)

    if previous_route == "SEARCH":
        prior_search_text = _normalize_lookup_text(
            str(runtime.get("search_query") or runtime.get("previous_user_request") or "")
        )
        if generic_reference:
            return True
        if prior_search_text and (
            normalized_subject in prior_search_text or prior_search_text in normalized_subject
        ):
            return True

    decision_source_scope = str((decision or {}).get("source_scope") or "").strip().lower()
    explicit_target = ""
    if decision_source_scope == "workspace" or _request_has_strong_workspace_scope(current_text):
        explicit_target = _extract_explicit_file_target_from_decision(decision)
    if explicit_target and _lookup_subject_matches_file_reference(normalized_subject, explicit_target):
        return True

    if generic_reference and (recent_file_targets or recent_subject or _extract_runtime_relevant_file_targets(recent_history)):
        return True

    if recent_subject:
        recent_normalized = _normalize_lookup_text(recent_subject)
        if recent_normalized and (
            normalized_subject in recent_normalized or recent_normalized in normalized_subject
        ):
            return True

    for target in recent_file_targets:
        if _lookup_subject_matches_file_reference(normalized_subject, target):
            return True

    return False


def _lookup_subject_matches_file_reference(normalized_subject: str, file_reference: str) -> bool:
    return file_reference_matches(normalized_subject, _clean_route_path(file_reference))


def _lookup_subject_is_generic_reference(normalized_subject: str) -> bool:
    return normalized_subject in _GENERIC_LOOKUP_SUBJECTS or normalized_subject in _PRONOUN_LOOKUP_SUBJECTS


def _route_looks_like_workspace_lookup(decision: RouteDecision) -> bool:
    if not decision or str(decision.get("decision") or "").strip().upper() != "TASK":
        return False
    stages = [dict(stage) for stage in ((decision.get("card") or {}).get("stages") or []) if isinstance(stage, dict)]
    if not stages:
        return False
    return any(str(stage.get("stage_type") or "").strip().upper() == "FILE_WORK" for stage in stages)


def _build_high_confidence_workspace_lookup_route(
    decision: RouteDecision,
    subject: str,
) -> RouteDecision:
    if _route_looks_like_workspace_lookup(decision):
        preserved = dict(decision)
        preserved.pop("question_if_uncertain", None)
        return preserved

    explicit_target = _extract_file_target_from_texts([subject])
    if explicit_target:
        return _build_explicit_workspace_file_search_card(explicit_target)
    return _build_workspace_document_search_card(subject)


def _build_lookup_source_clarification_card(
    subject: str,
    *,
    question_override: str = "",
) -> RouteDecision:
    clean_subject = " ".join(str(subject or "").split()).strip()
    override = " ".join(str(question_override or "").split()).strip()
    override_lower = override.lower()
    if override and "web" in override_lower and "workspace" in override_lower:
        question = override
        if clean_subject:
            goal = f"Clarify lookup source (web vs workspace) for: {clean_subject}"
        else:
            goal = "Clarify lookup source (web vs workspace) for: the requested item"
    elif clean_subject:
        question = (
            f'Did you want me to search the web for "{clean_subject}", '
            "or look for it in your workspace files?"
        )
        goal = f"Clarify lookup source (web vs workspace) for: {clean_subject}"
    else:
        question = "Did you want me to search the web, or look in your workspace files?"
        goal = "Clarify lookup source (web vs workspace) for: the requested item"
    return {
        "decision": "TASK",
        "card": {
            "goal": goal,
            "context": [
                "The latest request asks Piper to find or search for something, but the source is ambiguous.",
                "Do not guess between web search and workspace file lookup when the user's intent is unclear.",
                f"Preferred clarification question: {question}",
            ],
            "stages": [
                {
                    "stage_goal": f"Ask the user: {question}",
                    "stage_type": "CHAT",
                    "success_condition": "A concise source-clarification question is ready for the user.",
                    "allowed_tools": [],
                }
            ],
        },
    }


def _normalize_speculative_task_idea_to_chat(
    decision: RouteDecision,
    user_msg: str,
) -> RouteDecision | None:
    if not decision or str(decision.get("decision") or "").strip().upper() != "TASK":
        return None

    text = str(user_msg or "").strip()
    if not text or not SPECULATIVE_ACTION_RE.search(text):
        return None
    if EXPLICIT_ASSISTANT_REQUEST_RE.search(text):
        return None

    card = dict(decision.get("card") or {})
    stages = [dict(stage) for stage in card.get("stages") or [] if isinstance(stage, dict)]
    if not stages:
        return {"decision": "CHAT"}

    stage_types = {str(stage.get("stage_type") or "").strip().upper() for stage in stages}
    if stage_types and not stage_types.intersection({"FILE_WORK", "IMAGE_WORK", "MEMORY_WORK", "TASK_EVENT_WORK"}):
        return None

    goal_blob = " ".join(
        str(part or "")
        for part in (
            text,
            card.get("goal"),
            *(card.get("context") or []),
            *(stage.get("stage_goal") or "" for stage in stages),
            *(stage.get("success_condition") or "" for stage in stages),
        )
    ).strip()
    if not goal_blob:
        return None

    if not re.search(
        r"\b(create|make|write|build|design|implement|fix|repair|edit|update|store|remember|forget|schedule|add|remove|delete|run|launch|execute|generate)\b",
        goal_blob,
        re.IGNORECASE,
    ):
        return None

    return {"decision": "CHAT"}


def _normalize_workspace_document_lookup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = (user_msg or "").strip()
    if not text:
        return None
    workspace_scoped = _request_has_strong_workspace_scope(text)
    if _request_explicitly_scopes_lookup_to_web(text) and not workspace_scoped:
        return None

    recent_subject = _extract_recent_document_lookup_subject(
        recent_history,
        current_text=text,
    )
    recent_workspace_context = bool(
        (recent_subject and _subject_looks_like_workspace_document(recent_subject))
        or _extract_recent_explicit_file_target(recent_history, current_text=text)
        or _extract_runtime_relevant_file_targets(recent_history)
    )
    if (
        str((decision or {}).get("decision") or "").strip().upper() == "SEARCH"
        and not workspace_scoped
        and not recent_workspace_context
    ):
        return None

    current_subject = _extract_document_lookup_subject(text)
    if _subject_looks_like_workspace_document(current_subject):
        if _looks_like_document_read_request(text):
            return _build_workspace_document_read_card(current_subject)
        if _looks_like_document_search_request(text, decision):
            return _build_workspace_document_search_card(current_subject)

    if _looks_like_document_read_request(text) and _subject_looks_like_workspace_document(recent_subject):
        return _build_workspace_document_read_card(recent_subject)

    explicit_target = _extract_explicit_file_target_from_decision(decision) or _extract_recent_explicit_file_target(
        recent_history,
        current_text=text,
    )
    if explicit_target and _looks_like_document_read_request(text):
        if _decision_already_targets_file(decision, explicit_target):
            return decision
        return _build_explicit_workspace_file_read_card(explicit_target)
    if explicit_target and _looks_like_document_search_request(text, decision):
        return _build_explicit_workspace_file_search_card(explicit_target)

    subject = current_subject or recent_subject
    if not _subject_looks_like_workspace_document(subject):
        return None

    if _looks_like_document_read_request(text):
        return _build_workspace_document_read_card(subject)

    if _looks_like_document_search_request(text, decision):
        return _build_workspace_document_search_card(subject)

    return None


def _normalize_interactive_runtime_verification(
    decision: RouteDecision,
    user_msg: str,
) -> RouteDecision | None:
    if not decision or decision.get("decision") != "TASK":
        return None
    card = dict(decision.get("card") or {})
    stages = [dict(stage) for stage in card.get("stages") or [] if isinstance(stage, dict)]
    if len(stages) < 2:
        return None

    launch_index = -1
    for idx, stage in enumerate(stages):
        if _stage_looks_like_script_launch(stage):
            launch_index = idx
            break
    if launch_index < 0:
        return None

    changed = False
    updated_stages: list[StageCard] = []
    for idx, stage in enumerate(stages):
        if idx <= launch_index:
            updated_stages.append(stage)
            continue
        if _stage_looks_like_interactive_verification(stage):
            goal_text = str(stage.get("stage_goal") or "").strip() or "test the running app and report what happens"
            updated_stages.append(
                {
                    "stage_goal": f"Ask the user to test the already-running app and report the observed behavior for: {goal_text}",
                    "stage_type": "CHAT",
                    "success_condition": "The user reports what they observed while interacting with the running app.",
                    "allowed_tools": [],
                }
            )
            changed = True
        else:
            updated_stages.append(stage)

    if not changed:
        return None

    normalized = dict(decision)
    new_card = dict(card)
    new_context = list(new_card.get("context") or [])
    new_context.append(
        "If the remaining evidence depends on user interaction with a running app, do not relaunch it repeatedly; ask the user to test it and report what happened."
    )
    new_card["context"] = new_context
    new_card["stages"] = updated_stages
    normalized["card"] = new_card
    return normalized


def _normalize_workspace_file_delete_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text:
        return None

    match = _DELETE_REQUEST_RE.match(text)
    if not match:
        return None

    explicit_target = _extract_file_target_from_texts([text, str(match.group("body") or "")])
    if explicit_target:
        return _build_explicit_workspace_file_delete_card(explicit_target)

    body = _clean_delete_followup_subject(match.group("body"))
    if not body:
        return None

    recent_targets = _collect_recent_file_targets(decision, recent_history, current_text=text)
    resolved_target = _resolve_delete_followup_target(body, recent_targets)
    if not resolved_target:
        if not _subject_looks_like_file_delete_reference(body):
            return None
        return _build_workspace_file_delete_search_card(body)
    return _build_explicit_workspace_file_delete_card(resolved_target)


def _normalize_code_target_followup(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    if not decision or decision.get("decision") != "TASK":
        return None

    target = _extract_explicit_file_target_from_decision(decision) or _extract_recent_explicit_file_target(
        recent_history,
        current_text=(user_msg or "").strip(),
    )
    if not _looks_like_code_file_target(target):
        return None

    card = dict(decision.get("card") or {})
    stages = [dict(stage) for stage in card.get("stages") or [] if isinstance(stage, dict)]
    if not stages:
        return None
    if not any(str(stage.get("stage_type") or "").upper() == "FILE_WORK" for stage in stages):
        return None
    if not _task_looks_like_code_followup(decision, user_msg):
        return None

    changed = False
    updated_stages: list[StageCard] = []
    for stage in stages:
        if str(stage.get("stage_type") or "").upper() != "FILE_WORK":
            updated_stages.append(stage)
            continue
        rewritten = _rewrite_code_followup_stage(stage, target, user_msg=user_msg)
        changed = changed or rewritten != stage
        updated_stages.append(rewritten)

    if not changed:
        return None

    goal = str(card.get("goal") or "").strip()
    new_card = dict(card)
    if goal and not _extract_file_target_from_texts([goal]):
        new_card["goal"] = f"Use '{target}' for this code task: {goal}"
    elif not goal:
        new_card["goal"] = f"Work on '{target}' for the current code task."
    new_context = list(new_card.get("context") or [])
    target_note = f"The relevant workspace code file is '{target}'."
    no_search_note = f"Use '{target}' directly for this task. Do not search the workspace for a different file unless reading '{target}' fails."
    if target_note not in new_context:
        new_context.append(target_note)
    if no_search_note not in new_context:
        new_context.append(no_search_note)
    new_card["context"] = new_context
    new_card["stages"] = updated_stages
    normalized = dict(decision)
    normalized["card"] = new_card
    return normalized


def _task_looks_like_code_followup(decision: RouteDecision, user_msg: str) -> bool:
    card = dict(decision.get("card") or {})
    blobs = [str(user_msg or ""), str(card.get("goal") or "")]
    blobs.extend(str(item) for item in (card.get("context") or []))
    for stage in card.get("stages") or []:
        if isinstance(stage, dict):
            blobs.append(str(stage.get("stage_goal") or ""))
            blobs.append(str(stage.get("success_condition") or ""))
    combined = " ".join(part for part in blobs if part).strip()
    if not combined:
        return False
    return bool(_CODE_FOLLOWUP_HINT_RE.search(combined))


def _rewrite_code_followup_stage(stage: StageCard, target: str, *, user_msg: str = "") -> StageCard:
    rewritten = dict(stage)
    original_goal = str(stage.get("stage_goal") or "").strip()
    original_success = str(stage.get("success_condition") or "").strip()
    stage_blob = " ".join(part for part in [original_goal, original_success] if part)
    latest_request = str(user_msg or "").strip()
    request_text = latest_request or original_goal

    if _stage_looks_like_code_edit(stage_blob):
        rewritten["stage_goal"] = (
            f"Read '{target}', apply the requested code changes for this step, and save the updated file. "
            f"Latest request: {request_text or 'Update the target file.'}"
        )
        rewritten["success_condition"] = (
            f"The modified artifact is '{target}' and satisfies the latest user request: {request_text}."
            if request_text
            else f"'{target}' is updated with the requested code changes."
        )
        return rewritten

    if _stage_looks_like_script_launch(stage_blob):
        rewritten["stage_goal"] = f"Run '{target}' for this request: {latest_request or original_goal or 'Launch the target script.'}"
        latest_launch_is_interactive = bool(
            latest_request
            and _INTERACTIVE_VERIFY_RE.search(latest_request)
            and _INTERACTIVE_CONTROL_RE.search(latest_request)
        )
        if latest_request and not latest_launch_is_interactive:
            if re.search(r"\boutput\b", latest_request, re.IGNORECASE):
                rewritten["success_condition"] = f"'{target}' runs successfully and any immediate output is available."
            else:
                rewritten["success_condition"] = f"'{target}' launches successfully for the latest user request."
        else:
            rewritten["success_condition"] = (
                original_success if original_success else f"'{target}' runs successfully for the requested task."
            )
        return rewritten

    if _stage_looks_like_code_analysis(stage_blob):
        rewritten["stage_goal"] = (
            f"Read and analyze '{target}' for this request: "
            f"{request_text or 'Inspect the target code and identify the issue.'}"
        )
        rewritten["success_condition"] = (
            f"{original_success or 'The diagnosis must be grounded in the target file.'} "
            f"The diagnosis must be grounded in the contents of '{target}'."
            if original_success or request_text
            else f"The requested diagnosis is grounded in the contents of '{target}'."
        )
        return rewritten

    rewritten["stage_goal"] = (
        f"Use '{target}' as the direct file target for this step. "
        f"Request: {request_text or 'Work on the target code file.'}"
    )
    rewritten["success_condition"] = (
        f"{original_success} The target file for this step is '{target}'."
        if original_success
        else f"The step is completed directly against '{target}'."
    )
    return rewritten


def _stage_looks_like_code_analysis(text: str) -> bool:
    return bool(_CODE_ANALYSIS_RE.search(text or ""))


def _stage_looks_like_code_edit(text: str) -> bool:
    return bool(_CODE_EDIT_RE.search(text or ""))


def _looks_like_code_file_target(path: str) -> bool:
    clean = _clean_route_path(path)
    if not clean:
        return False
    suffix = clean.rsplit(".", 1)
    if len(suffix) != 2:
        return False
    return f".{suffix[-1].lower()}" in _CODE_FILE_EXTENSIONS


def _stage_looks_like_script_launch(stage: StageCard | dict | str) -> bool:
    if isinstance(stage, str):
        text = stage
    else:
        text = " ".join(
            [
                str((stage or {}).get("stage_goal", "") or ""),
                str((stage or {}).get("success_condition", "") or ""),
            ]
        )
    if not text:
        return False
    return bool(_SCRIPT_LAUNCH_RE.search(text) and _SCRIPT_APP_RE.search(text))


def _stage_looks_like_interactive_verification(stage: StageCard | dict) -> bool:
    text = " ".join(
        [
            str((stage or {}).get("stage_goal", "") or ""),
            str((stage or {}).get("success_condition", "") or ""),
        ]
    )
    if not text:
        return False
    return bool(_INTERACTIVE_VERIFY_RE.search(text) and _INTERACTIVE_CONTROL_RE.search(text))


def _looks_like_document_read_request(text: str) -> bool:
    return bool(_DOCUMENT_READ_REQUEST_RE.search(text or ""))


def _looks_like_document_search_request(text: str, decision: RouteDecision) -> bool:
    raw = (text or "").strip()
    if _DOCUMENT_SEARCH_REQUEST_RE.search(raw):
        return True
    if _DOCUMENT_NAMING_HINT_RE.search(raw):
        return True
    if not decision or decision.get("decision") != "TASK":
        return False
    card = dict(decision.get("card") or {})
    stage_blob = " ".join(
        f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
        for stage in card.get("stages") or []
    ).lower()
    context_blob = " ".join(str(item) for item in card.get("context") or []).lower()
    combined = " ".join(part for part in [stage_blob, context_blob] if part)
    if not combined:
        return False
    return bool(
        _DOCUMENT_NAMING_HINT_RE.search(combined)
        or "content or keywords" in combined
        or "containing the word" in combined
        or "potential matching files" in combined
        or "different filename" in combined
    )


def _build_workspace_document_read_card(subject: str) -> RouteDecision:
    quoted_subject = json.dumps(subject, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Locate the workspace file that best matches {quoted_subject} and report its contents.",
            "context": [
                "The workspace root is '.'.",
                f"The requested document reference is {quoted_subject}.",
                "Prefer filename matching before assuming the file is absent.",
            ],
            "stages": [
                {
                    "stage_goal": f"Locate the workspace file that best matches {quoted_subject} and read its exact contents if found.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "A matching file is identified and its exact contents are read, or the absence of any plausible file match is confirmed.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_workspace_document_search_card(subject: str) -> RouteDecision:
    quoted_subject = json.dumps(subject, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Find workspace filenames that plausibly match {quoted_subject}.",
            "context": [
                "The workspace root is '.'.",
                f"The requested document reference is {quoted_subject}.",
                "Prefer filename matching before escalating to content scans.",
            ],
            "stages": [
                {
                    "stage_goal": f"Search workspace filenames for files that plausibly match {quoted_subject}.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "Matching file paths are identified, or the absence of any plausible filename match is confirmed.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_explicit_workspace_file_search_card(path: str) -> RouteDecision:
    clean_path = _clean_route_path(path)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Find workspace filenames that match '{clean_path}'.",
            "context": [
                "The workspace root is '.'.",
                f"The likely target filename/path is '{clean_path}'.",
                "This is a filename/path recheck. Confirm the matching path instead of rereading contents unless the user explicitly asks to read the file.",
            ],
            "stages": [
                {
                    "stage_goal": f"Search workspace filenames for files matching '{clean_path}'.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "Matching file paths are identified, or the absence of any plausible filename match is confirmed.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_explicit_workspace_file_read_card(path: str) -> RouteDecision:
    clean_path = _clean_route_path(path)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Read the exact contents of '{clean_path}'.",
            "context": [
                "The workspace root is '.'.",
                f"The target file path is '{clean_path}'.",
                "Return the file contents exactly once when read succeeds.",
            ],
            "stages": [
                {
                    "stage_goal": f"Read the exact contents of the file '{clean_path}'.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"The exact contents of '{clean_path}' are read once.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_explicit_workspace_file_delete_card(path: str) -> RouteDecision:
    clean_path = _clean_route_path(path)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Delete '{clean_path}'.",
            "context": [
                "The workspace root is '.'.",
                f"The target file path is '{clean_path}'.",
                f"Use the exact target path '{clean_path}' directly. Do not search for a different file unless that exact path is first confirmed missing.",
            ],
            "stages": [
                {
                    "stage_goal": f"Delete the file '{clean_path}'.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"'{clean_path}' does not exist in the workspace.",
                    "allowed_tools": ["FILE_OP"],
                    "active_targets": [clean_path],
                }
            ],
        },
    }


def _build_workspace_file_delete_search_card(subject: str) -> RouteDecision:
    quoted_subject = json.dumps(subject, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Locate the workspace file that best matches {quoted_subject} and delete it.",
            "context": [
                "The workspace root is '.'.",
                f"The requested file reference is {quoted_subject}.",
                "Prefer filename matching before assuming the file is absent.",
                "Delete only the best workspace file match for this reference. If no plausible match exists, report that no such file was found.",
            ],
            "stages": [
                {
                    "stage_goal": f"Find the workspace file that best matches {quoted_subject} and delete it if found.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "A matching file is deleted, or the absence of any plausible file match is confirmed.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_empty_directory_cleanup_card() -> RouteDecision:
    return {
        "decision": "TASK",
        "card": {
            "goal": "Delete empty folders under the workspace root.",
            "context": [
                "The workspace root is '.'.",
                "This is a directory-cleanup request, not a filename lookup.",
                "Delete only directories that are empty at execution time.",
            ],
            "stages": [
                {
                    "stage_goal": "Delete folders that are currently empty under the workspace root.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "No empty folders remain under the workspace root.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _extract_document_lookup_subject(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    patterns = (
        re.compile(r"(?is)\b(?:tell me what it says in|what does it say in|what(?:'s| is) in)\s+(?:the\s+)?(?P<subject>.+?)\s*$"),
        re.compile(r"(?is)\b(?:search for|look for|find|locate|open|read|show me)\s+(?:the\s+)?(?P<subject>.+?)\s*$"),
    )
    for pattern in patterns:
        match = pattern.search(raw)
        if match:
            cleaned = _clean_document_lookup_subject(match.group("subject"))
            if cleaned:
                return cleaned
    for parts in _QUOTED_TEXT_RE.findall(raw):
        quoted = next((part for part in parts if part), "")
        cleaned = _clean_document_lookup_subject(quoted)
        if cleaned:
            return cleaned
    return ""


def _extract_recent_document_lookup_subject(
    recent_history: Sequence[dict[str, Any]],
    *,
    current_text: str,
) -> str:
    for message in reversed(recent_history):
        if str(message.get("role") or "").strip().lower() == "system" or bool(message.get("hidden")):
            continue
        content = str(message.get("content") or "").strip()
        if not content or content == current_text:
            continue
        subject = _extract_document_lookup_subject(content)
        if subject:
            return subject
        for parts in _QUOTED_TEXT_RE.findall(content):
            quoted = next((part for part in parts if part), "")
            cleaned = _clean_document_lookup_subject(quoted)
            if cleaned:
                return cleaned
    return ""


def _clean_document_lookup_subject(raw_subject: str) -> str:
    subject = str(raw_subject or "").strip().strip(".,;:!?")
    if not subject:
        return ""
    if len(subject) >= 2 and subject[0] == subject[-1] and subject[0] in {"'", '"'}:
        subject = subject[1:-1].strip()
    subject = re.sub(r"(?i)^(?:the|a|an)\s+", "", subject).strip()
    if re.match(r"(?i)^(?:it|this|that)\b", subject):
        previous = None
        while subject and subject != previous:
            previous = subject
            subject = re.sub(r"(?i)\b(?:back|again|please|now)\b$", "", subject).strip()
            subject = re.sub(r"(?i)\b(?:for me|to me|out loud|aloud)\b$", "", subject).strip()
    subject = re.sub(r"(?i)\b(?:please|again|now|in the workspace|within the workspace)\b$", "", subject).strip()
    subject = re.sub(r"(?i)\s+(?:file|document|doc|text file|txt file)$", "", subject).strip()
    normalized = _normalize_lookup_text(subject)
    if not normalized or normalized in _GENERIC_LOOKUP_SUBJECTS or normalized in _PRONOUN_LOOKUP_SUBJECTS:
        return ""
    return subject


def _subject_looks_like_workspace_document(subject: str) -> bool:
    normalized = _normalize_lookup_text(subject)
    if not normalized or normalized in _GENERIC_LOOKUP_SUBJECTS or normalized in _PRONOUN_LOOKUP_SUBJECTS:
        return False
    if _STATEISH_LOOKUP_SUBJECT_RE.search(normalized):
        return False
    return normalized not in _BLOCKED_LOOKUP_SUBJECTS


def _extract_explicit_file_target_from_decision(decision: RouteDecision) -> str:
    if not decision or decision.get("decision") != "TASK":
        return ""
    card = dict(decision.get("card") or {})
    for stage in card.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for target in stage.get("active_targets") or []:
            cleaned = _clean_route_path(target)
            if cleaned:
                return cleaned
    blobs = [str(card.get("goal") or "")]
    blobs.extend(str(item) for item in (card.get("context") or []))
    for stage in card.get("stages") or []:
        if isinstance(stage, dict):
            blobs.append(str(stage.get("stage_goal") or ""))
            blobs.append(str(stage.get("success_condition") or ""))
    return _extract_named_runtime_file_target(" ".join(blobs)) or _extract_file_target_from_texts(blobs)


def _extract_recent_explicit_file_target(
    recent_history: Sequence[dict[str, Any]],
    *,
    current_text: str,
) -> str:
    matches = _extract_recent_explicit_file_targets(recent_history, current_text=current_text)
    return matches[0] if matches else ""


def _extract_recent_explicit_file_targets(
    recent_history: Sequence[dict[str, Any]],
    *,
    current_text: str,
) -> list[str]:
    matches: list[str] = []
    for message in reversed(recent_history):
        content = str(message.get("content") or "").strip()
        if not content or content == current_text:
            continue
        target = _extract_file_target_from_texts([content])
        if target:
            matches.append(target)
    return matches


def _extract_file_target_from_texts(texts: Sequence[str]) -> str:
    for text in texts:
        for match in _FILE_TARGET_RE.findall(str(text or "")):
            cleaned = _clean_route_path(match)
            if cleaned:
                return cleaned
    return ""


def _extract_runtime_relevant_file_targets(
    recent_history: Sequence[dict[str, Any]],
) -> list[str]:
    runtime = extract_latest_runtime_context_fields(recent_history)
    raw = str(runtime.get("relevant_paths") or "").strip()
    if not raw:
        return []
    targets: list[str] = []
    for part in raw.split("|"):
        cleaned = _clean_route_path(part)
        if cleaned:
            targets.append(cleaned)
    return targets


def _collect_recent_file_targets(
    decision: RouteDecision,
    recent_history: Sequence[dict[str, Any]],
    *,
    current_text: str,
) -> list[str]:
    ordered: list[str] = []
    decision_source_scope = str((decision or {}).get("source_scope") or "").strip().lower()
    current_decision_target = ""
    if decision_source_scope == "workspace" or _request_has_strong_workspace_scope(current_text):
        current_decision_target = _extract_explicit_file_target_from_decision(decision)
    for candidate in (
        current_decision_target,
        *_extract_recent_explicit_file_targets(recent_history, current_text=current_text),
        *_extract_runtime_relevant_file_targets(recent_history),
    ):
        cleaned = _clean_route_path(candidate)
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


def _clean_delete_followup_subject(raw_subject: str) -> str:
    subject = str(raw_subject or "").strip().strip(".,;:!?")
    subject = re.sub(r"(?i)^(?:the|this|that)\s+", "", subject).strip()
    subject = re.sub(r"(?i)\s+(?:from the workspace|in the workspace)$", "", subject).strip()
    return subject


def _resolve_delete_followup_target(subject: str, recent_targets: Sequence[str]) -> str:
    normalized_subject = _normalize_lookup_text(subject)
    if not normalized_subject or not recent_targets:
        return ""
    if normalized_subject in _GENERIC_DELETE_SUBJECTS:
        return str(recent_targets[0]).strip()

    for target in recent_targets:
        clean_target = _clean_route_path(target)
        basename = PurePosixPath(clean_target).name
        stem = PurePosixPath(clean_target).stem
        variants = {
            _normalize_lookup_text(clean_target),
            _normalize_lookup_text(basename),
            _normalize_lookup_text(stem),
        }
        if normalized_subject in variants:
            return clean_target
    return ""


def _subject_looks_like_file_delete_reference(subject: str) -> bool:
    normalized_subject = _normalize_lookup_text(subject)
    if not normalized_subject:
        return False
    if normalized_subject in _GENERIC_DELETE_SUBJECTS or normalized_subject in _BLOCKED_LOOKUP_SUBJECTS:
        return False
    raw_subject = str(subject or "").strip()
    if not raw_subject:
        return False
    if re.search(r"(?i)\bempty\s+(?:folders|directories)\b", raw_subject):
        return False
    if re.search(r"[/\\.]", raw_subject) or "_" in raw_subject:
        return True
    return bool(_FILEISH_DELETE_HINT_RE.search(raw_subject))


def _decision_already_targets_file(decision: RouteDecision, path: str) -> bool:
    target = _extract_explicit_file_target_from_decision(decision)
    return bool(target and _clean_route_path(target).lower() == _clean_route_path(path).lower())


def _normalize_lookup_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return " ".join(cleaned.split())


def _normalize_direct_file_work(user_msg: str) -> RouteDecision | None:
    text = (user_msg or "").strip()
    if not text:
        return None

    if _DIRECT_EMPTY_DIR_DELETE_RE.match(text):
        return _build_empty_directory_cleanup_card()

    compound_sequence = _normalize_compound_file_undo_redo_sequence(text)
    if compound_sequence is not None:
        return compound_sequence

    folder_dummy_files = _extract_folder_dummy_files_request(text)
    if folder_dummy_files is not None:
        folder, count = folder_dummy_files
        return _build_folder_dummy_files_card(folder, count)

    compound_rename_move = _extract_compound_rename_then_move_request(text)
    if compound_rename_move is not None:
        src, renamed, folder = compound_rename_move
        return _build_compound_rename_then_move_card(src, renamed, folder)

    compound_replace_append = _extract_direct_file_replace_append_request(text)
    if compound_replace_append is not None:
        path, old_text, new_text, appended_line = compound_replace_append
        return _build_explicit_file_text_replace_append_card(path, old_text, new_text, appended_line)

    append_line_request = _extract_direct_file_append_line_request(text)
    if append_line_request is not None:
        path, appended_line = append_line_request
        return _build_explicit_file_append_line_card(path, appended_line)

    create_match = DIRECT_FILE_CREATE_TEXT_RE.match(text)
    if create_match:
        path = _clean_route_path(create_match.group("path"))
        content = _clean_route_content(create_match.group("content"))
        quoted_content = json.dumps(content, ensure_ascii=False)
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Create the text file '{path}' with the requested exact contents.",
                "context": [
                    "The workspace root is '.'.",
                    f"The target path is '{path}'.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Create or overwrite the text file '{path}' with the exact contents {quoted_content}.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"The file '{path}' exists and its exact contents are {quoted_content}.",
                        "allowed_tools": ["FILE_OP"],
                    }
                ],
            },
        }

    remove_text_match = DIRECT_FILE_REMOVE_TEXT_RE.match(text)
    if remove_text_match:
        needle = _clean_route_content(remove_text_match.group("needle"))
        subject, wants_readback = _split_file_followup_tail(remove_text_match.group("subject"))
        explicit_path = _extract_file_target_from_texts([subject])
        if explicit_path:
            return _build_explicit_file_text_remove_card(explicit_path, needle, read_back=wants_readback)
        cleaned_subject = _clean_document_lookup_subject(subject)
        if cleaned_subject and _subject_looks_like_workspace_document(cleaned_subject):
            return _build_subject_file_text_remove_card(cleaned_subject, needle, read_back=wants_readback)

    replace_text_match = DIRECT_FILE_REPLACE_TEXT_RE.match(text)
    if replace_text_match:
        old_text = _clean_route_content(replace_text_match.group("old"))
        new_text = _clean_route_content(replace_text_match.group("new"))
        subject = replace_text_match.group("subject")
        explicit_path = _extract_file_target_from_texts([subject])
        if explicit_path:
            return _build_explicit_file_text_replace_card(explicit_path, old_text, new_text)
        cleaned_subject = _clean_document_lookup_subject(subject)
        if cleaned_subject and _subject_looks_like_workspace_document(cleaned_subject):
            return _build_subject_file_text_replace_card(cleaned_subject, old_text, new_text)

    copy_match = DIRECT_FILE_COPY_RE.match(text)
    if copy_match:
        src = _clean_route_path(copy_match.group("src"))
        dst = _clean_route_path(copy_match.group("dst"))
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Copy '{src}' to '{dst}'.",
                "context": [
                    "The workspace root is '.'.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Copy the file '{src}' to '{dst}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"Both '{src}' and '{dst}' exist and the destination contents match the source.",
                        "allowed_tools": ["FILE_OP"],
                    }
                ],
            },
        }

    move_match = DIRECT_FILE_MOVE_RE.match(text)
    if move_match:
        src = _clean_route_path(move_match.group("src"))
        dst = _clean_route_path(move_match.group("dst"))
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Move '{src}' to '{dst}'.",
                "context": [
                    "The workspace root is '.'.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Move the file '{src}' to '{dst}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"'{src}' no longer exists and '{dst}' exists.",
                        "allowed_tools": ["FILE_OP"],
                    }
                ],
            },
        }

    read_match = DIRECT_FILE_READ_RE.match(text)
    if read_match:
        path = _clean_route_path(read_match.group("path"))
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Read the exact contents of '{path}'.",
                "context": [
                    "The workspace root is '.'.",
                    "Return the file contents exactly once when read succeeds.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Read the exact contents of the file '{path}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"The exact contents of '{path}' are read once.",
                        "allowed_tools": ["FILE_OP"],
                    }
                ],
            },
        }

    delete_match = DIRECT_FILE_DELETE_RE.match(text)
    if delete_match:
        path = _clean_route_path(delete_match.group("path"))
        return _build_explicit_workspace_file_delete_card(path)

    return None


def _extract_folder_dummy_files_request(text: str) -> tuple[str, int] | None:
    match = _FOLDER_WITH_DUMMY_FILES_RE.match(text or "")
    if not match:
        return None
    folder = _clean_route_path(match.group("folder")).rstrip("/\\")
    count = _COUNT_TOKEN_TO_INT.get(str(match.group("count") or "").strip().lower(), 0)
    if not folder or count <= 0:
        return None
    return folder, count


def _build_folder_dummy_files_card(folder: str, count: int) -> RouteDecision:
    clean_folder = _clean_route_path(folder).rstrip("/\\")
    stages: list[StageCard] = [
        {
            "stage_goal": f"Create the directory '{clean_folder}' in the workspace root.",
            "stage_type": "FILE_WORK",
            "success_condition": f"The directory '{clean_folder}' exists.",
            "allowed_tools": ["FILE_OP"],
        }
    ]
    for idx in range(1, count + 1):
        filename = f"dummy{idx}.txt"
        rel_path = PurePosixPath(clean_folder, filename).as_posix()
        quoted_content = json.dumps(f"Dummy content {idx}", ensure_ascii=False)
        stages.append(
            {
                "stage_goal": f"Create the text file '{rel_path}' with the exact contents {quoted_content}.",
                "stage_type": "FILE_WORK",
                "success_condition": f"The file '{rel_path}' exists and its exact contents are {quoted_content}.",
                "allowed_tools": ["FILE_OP"],
            }
        )
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Create the folder '{clean_folder}' and populate it with {count} dummy files.",
            "context": [
                "The workspace root is '.'.",
                f"Use deterministic dummy file names inside '{clean_folder}' so the result is easy to verify.",
            ],
            "stages": stages,
        },
    }


def _extract_compound_rename_then_move_request(text: str) -> tuple[str, str, str] | None:
    rename_match = _COMPOUND_RENAME_RE.search(text or "")
    move_match = _COMPOUND_MOVE_INTO_FOLDER_RE.search(text or "")
    if not rename_match or not move_match:
        return None
    src = _clean_route_path(rename_match.group("src"))
    renamed = _clean_route_path(rename_match.group("renamed"))
    reported = _clean_route_path(move_match.group("reported"))
    folder = _clean_route_path(move_match.group("folder")).rstrip("/\\")
    if not src or not renamed or not folder:
        return None
    if reported and PurePosixPath(reported).name.lower() != PurePosixPath(renamed).name.lower():
        return None
    return src, renamed, folder


def _build_compound_rename_then_move_card(src: str, renamed: str, folder: str) -> RouteDecision:
    clean_src = _clean_route_path(src)
    clean_renamed = _clean_route_path(renamed)
    clean_folder = _clean_route_path(folder).rstrip("/\\")
    final_dst = PurePosixPath(clean_folder, PurePosixPath(clean_renamed).name).as_posix()
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Rename '{clean_src}' to '{PurePosixPath(clean_renamed).name}' and place it in '{clean_folder}'.",
            "context": [
                "The workspace root is '.'.",
                f"The source path is '{clean_src}'.",
                f"The final destination path is '{final_dst}'.",
                f"Perform this as one direct rename-and-move to '{final_dst}'. Do not stop after creating an intermediate '{PurePosixPath(clean_renamed).name}' in the original directory.",
            ],
            "stages": [
                {
                    "stage_goal": f"Move '{clean_src}' directly to '{final_dst}' so the file is renamed and placed in '{clean_folder}' in one step.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"'{clean_src}' no longer exists and '{final_dst}' exists with the original file content.",
                    "allowed_tools": ["FILE_OP"],
                    "constraints": [
                        {
                            "type": "MOVED",
                            "scope": "FILE",
                            "from_path": clean_src,
                            "to_path": final_dst,
                        }
                    ],
                }
            ],
        },
    }


def _normalize_compound_file_undo_redo_sequence(text: str) -> RouteDecision | None:
    if not _COMPOUND_FILE_UNDO_REDO_RE.search(text or ""):
        return None

    path = _extract_compound_file_sequence_path(text)
    content = _extract_compound_file_sequence_content(text)
    if not path or not content:
        return _build_compound_file_sequence_clarification_card(
            path=path,
            needs_path=not path,
            needs_content=not content,
        )
    return _build_compound_file_sequence_card(path, content)


def _extract_compound_file_sequence_path(text: str) -> str:
    for pattern in (_COMPOUND_FILE_CREATE_WITH_CONTENT_RE, _COMPOUND_FILE_NAMED_WRITE_RE):
        match = pattern.match(text or "")
        if match:
            candidate = _clean_route_path(match.group("path"))
            if candidate:
                return candidate

    candidate = _extract_file_target_from_texts([text])
    if candidate:
        return _clean_route_path(candidate)

    named_match = re.search(
        rf"(?is)\bfile\s+(?:named|called)\s+(?P<path>{_FILE_PATH_TOKEN})\b",
        text or "",
    )
    if named_match:
        return _clean_route_path(named_match.group("path"))
    return ""


def _extract_compound_file_sequence_content(text: str) -> str:
    for pattern in (_COMPOUND_FILE_CREATE_WITH_CONTENT_RE, _COMPOUND_FILE_NAMED_WRITE_RE):
        match = pattern.match(text or "")
        if match:
            candidate = _clean_route_content(match.group("content"))
            if candidate:
                return candidate

    write_match = re.search(
        r"(?is)\b(?:write|put)\s+(?P<content>.+?)\s+(?:and then|then)\s+(?:delete|remove)\b",
        text or "",
    )
    if write_match:
        candidate = _clean_route_content(write_match.group("content"))
        if candidate:
            return candidate

    content_match = re.search(
        r"(?is)\bwith\s+(?:the\s+)?exact\s+contents?\s*:\s*(?P<content>.+?)\s+(?:and then|then)\s+(?:delete|remove)\b",
        text or "",
    )
    if content_match:
        candidate = _clean_route_content(content_match.group("content"))
        if candidate:
            return candidate
    return ""


def _build_compound_file_sequence_clarification_card(
    *,
    path: str,
    needs_path: bool,
    needs_content: bool,
) -> RouteDecision:
    if needs_path and needs_content:
        question = "Which filename and exact content should I use for the create, delete, undo, and redo file sequence?"
    elif needs_path:
        question = "Which filename should I use for the create, delete, undo, and redo file sequence?"
    else:
        question = f"What exact content should I write into '{path}' before I run the create, delete, undo, and redo file sequence?"
    return {
        "decision": "TASK",
        "card": {
            "goal": "Clarify the target details for the requested create/delete/undo/redo file sequence.",
            "context": [
                "The user requested a multi-step file sequence: create, delete, undo, and redo.",
                "Do not invent a placeholder filename or file content when the user did not specify them.",
                "Pause and ask only for the missing file details needed to execute the sequence safely.",
            ],
            "stages": [
                {
                    "stage_goal": f"Ask the user: {question}",
                    "stage_type": "CHAT",
                    "success_condition": "A concise clarification question for the missing file details is ready for the user.",
                    "allowed_tools": [],
                }
            ],
        },
    }


def _build_compound_file_sequence_card(path: str, content: str) -> RouteDecision:
    clean_path = _clean_route_path(path)
    clean_content = _clean_route_content(content)
    quoted_content = json.dumps(clean_content, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Create '{clean_path}', delete it, restore it, and then delete it again.",
            "context": [
                "The workspace root is '.'.",
                f"The target file path is '{clean_path}'.",
                "This is a single-turn file-sequence request.",
                "Keep the workflow in FILE_WORK; do not reinterpret undo/redo as task or event mutations.",
            ],
            "stages": [
                {
                    "stage_goal": f"Create or overwrite the text file '{clean_path}' with the exact contents {quoted_content}.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"The file '{clean_path}' exists and its exact contents are {quoted_content}.",
                    "allowed_tools": ["FILE_OP"],
                    "active_targets": [clean_path],
                },
                {
                    "stage_goal": f"Delete the file '{clean_path}'.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"'{clean_path}' does not exist in the workspace.",
                    "allowed_tools": ["FILE_OP"],
                    "active_targets": [clean_path],
                },
                {
                    "stage_goal": f"Restore the file '{clean_path}' with the exact contents {quoted_content} after the deletion.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"The file '{clean_path}' exists again and its exact contents are {quoted_content}.",
                    "allowed_tools": ["FILE_OP"],
                    "active_targets": [clean_path],
                },
                {
                    "stage_goal": f"Delete the restored file '{clean_path}' again.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"'{clean_path}' does not exist in the workspace after the second deletion.",
                    "allowed_tools": ["FILE_OP"],
                    "active_targets": [clean_path],
                },
            ],
        },
    }


def _build_explicit_file_text_remove_card(path: str, needle: str, *, read_back: bool = False) -> RouteDecision:
    clean_path = _clean_route_path(path)
    quoted_needle = json.dumps(needle, ensure_ascii=False)
    stages: list[StageCard] = [
        {
            "stage_goal": f"Read '{clean_path}', remove the exact text {quoted_needle} from its contents, and save the updated file.",
            "stage_type": "FILE_WORK",
            "success_condition": f"The file '{clean_path}' no longer contains {quoted_needle}.",
            "allowed_tools": ["FILE_OP"],
        }
    ]
    if read_back:
        stages.append(
            {
                "stage_goal": f"Read the updated exact contents of '{clean_path}'.",
                "stage_type": "FILE_WORK",
                "success_condition": f"The exact contents of '{clean_path}' are read once after the requested removal.",
                "allowed_tools": ["FILE_OP"],
            }
        )
    return {
        "decision": "TASK",
        "card": {
            "goal": (
                f"Remove {quoted_needle} from '{clean_path}' and then read the updated file back."
                if read_back
                else f"Remove {quoted_needle} from '{clean_path}'."
            ),
            "context": [
                "The workspace root is '.'.",
                f"The target file path is '{clean_path}'.",
                "Keep all other file content unchanged unless the requested text removal requires a local formatting cleanup.",
                *(
                    ["After the removal step, read the updated file contents back exactly once."]
                    if read_back
                    else []
                ),
            ],
            "stages": stages,
        },
    }


def _build_subject_file_text_remove_card(subject: str, needle: str, *, read_back: bool = False) -> RouteDecision:
    quoted_subject = json.dumps(subject, ensure_ascii=False)
    quoted_needle = json.dumps(needle, ensure_ascii=False)
    stages: list[StageCard] = [
        {
            "stage_goal": f"Locate the workspace file that best matches {quoted_subject}, remove the exact text {quoted_needle} from its contents, and save the updated file.",
            "stage_type": "FILE_WORK",
            "success_condition": f"A matching file is identified and no longer contains {quoted_needle}.",
            "allowed_tools": ["FILE_OP"],
        }
    ]
    if read_back:
        stages.append(
            {
                "stage_goal": f"Read the updated exact contents of the workspace file matching {quoted_subject}.",
                "stage_type": "FILE_WORK",
                "success_condition": f"A matching file is identified and its exact updated contents are read once after the requested removal.",
                "allowed_tools": ["FILE_OP"],
            }
        )
    return {
        "decision": "TASK",
        "card": {
            "goal": (
                f"Remove {quoted_needle} from the workspace file matching {quoted_subject} and then read the updated file back."
                if read_back
                else f"Remove {quoted_needle} from the workspace file matching {quoted_subject}."
            ),
            "context": [
                "The workspace root is '.'.",
                f"The requested document reference is {quoted_subject}.",
                "Keep all other file content unchanged unless the requested text removal requires a local formatting cleanup.",
                *(
                    ["After the removal step, read the updated file contents back exactly once."]
                    if read_back
                    else []
                ),
            ],
            "stages": stages,
        },
    }


def _build_explicit_file_text_replace_card(path: str, old_text: str, new_text: str) -> RouteDecision:
    clean_path = _clean_route_path(path)
    quoted_old = json.dumps(old_text, ensure_ascii=False)
    quoted_new = json.dumps(new_text, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Replace {quoted_old} with {quoted_new} in '{clean_path}'.",
            "context": [
                "The workspace root is '.'.",
                f"The target file path is '{clean_path}'.",
                "Keep all other file content unchanged beyond the requested replacement.",
            ],
            "stages": [
                {
                    "stage_goal": f"Read '{clean_path}', replace the exact text {quoted_old} with {quoted_new}, and save the updated file.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"The file '{clean_path}' contains {quoted_new} in place of {quoted_old}.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_subject_file_text_replace_card(subject: str, old_text: str, new_text: str) -> RouteDecision:
    quoted_subject = json.dumps(subject, ensure_ascii=False)
    quoted_old = json.dumps(old_text, ensure_ascii=False)
    quoted_new = json.dumps(new_text, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Replace {quoted_old} with {quoted_new} in the workspace file matching {quoted_subject}.",
            "context": [
                "The workspace root is '.'.",
                f"The requested document reference is {quoted_subject}.",
                "Keep all other file content unchanged beyond the requested replacement.",
            ],
            "stages": [
                {
                    "stage_goal": f"Locate the workspace file that best matches {quoted_subject}, replace the exact text {quoted_old} with {quoted_new}, and save the updated file.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"A matching file is identified and contains {quoted_new} instead of {quoted_old}.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_explicit_file_text_replace_append_card(
    path: str,
    old_text: str,
    new_text: str,
    appended_line: str,
) -> RouteDecision:
    clean_path = _clean_route_path(path)
    quoted_old = json.dumps(old_text, ensure_ascii=False)
    quoted_new = json.dumps(new_text, ensure_ascii=False)
    quoted_appended = json.dumps(appended_line, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": (
                f"Edit '{clean_path}' by replacing {quoted_old} with {quoted_new} and adding a second line {quoted_appended}."
            ),
            "context": [
                "The workspace root is '.'.",
                f"The target file path is '{clean_path}'.",
                "Treat the quoted replacement text and appended line as literal file contents, not task or event status keywords.",
                "Keep all other file content unchanged beyond the requested replacement and the requested appended second line.",
            ],
            "stages": [
                {
                    "stage_goal": (
                        f"Read '{clean_path}', replace the exact text {quoted_old} with {quoted_new}, "
                        f"ensure the updated file includes a second line exactly {quoted_appended}, and save the file."
                    ),
                    "stage_type": "FILE_WORK",
                    "success_condition": (
                        f"The file '{clean_path}' contains {quoted_new} in place of {quoted_old} and has a second line exactly {quoted_appended}."
                    ),
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _build_explicit_file_append_line_card(path: str, appended_line: str) -> RouteDecision:
    clean_path = _clean_route_path(path)
    quoted_appended = json.dumps(appended_line, ensure_ascii=False)
    return {
        "decision": "TASK",
        "card": {
            "goal": f"Edit the existing file '{clean_path}' by appending a new line {quoted_appended}.",
            "context": [
                "The workspace root is '.'.",
                f"The target file path is '{clean_path}'.",
                "This request is to edit an existing file, not to create a new file.",
                "If the target file does not already exist, stop and report that it was not found instead of creating it.",
                "Keep all existing file content unchanged beyond appending the requested new line at the end.",
            ],
            "stages": [
                {
                    "stage_goal": f"Read the existing file '{clean_path}', append a new line exactly {quoted_appended} to the end, and save the updated file.",
                    "stage_type": "FILE_WORK",
                    "success_condition": f"The existing file '{clean_path}' was updated by appending a new line exactly {quoted_appended}; do not create a new file if it is missing.",
                    "allowed_tools": ["FILE_OP"],
                }
            ],
        },
    }


def _extract_direct_file_replace_append_request(text: str) -> tuple[str, str, str, str] | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    if not _FILE_EDIT_REQUEST_RE.search(candidate):
        return None
    if "replace" not in candidate.lower():
        return None
    if not _SECOND_LINE_APPEND_RE.search(candidate):
        return None

    explicit_path = _extract_file_target_from_texts([candidate])
    if not explicit_path:
        return None

    quoted_values = [
        str(next((part for part in match if part), "")).strip()
        for match in _QUOTED_TEXT_RE.findall(candidate)
    ]
    quoted_values = [value for value in quoted_values if value]
    if len(quoted_values) < 3:
        return None

    old_text, new_text, appended_line = quoted_values[:3]
    if not old_text or not new_text or not appended_line:
        return None
    return (explicit_path, old_text, new_text, appended_line)


def _extract_direct_file_append_line_request(text: str) -> tuple[str, str] | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    if not _APPEND_LINE_REQUEST_RE.search(candidate):
        return None
    explicit_path = _extract_file_target_from_texts([candidate])
    if not explicit_path:
        return None
    quoted_values = [
        str(next((part for part in match if part), "")).strip()
        for match in _QUOTED_TEXT_RE.findall(candidate)
    ]
    quoted_values = [value for value in quoted_values if value]
    if not quoted_values:
        return None
    appended_line = quoted_values[-1]
    if not appended_line:
        return None
    return (explicit_path, appended_line)


def _clean_route_path(raw_path: str) -> str:
    return str(raw_path or "").strip().rstrip(".,;:!?").replace("\\", "/")


def _normalize_workspace_scope_path(raw_path: str) -> str:
    clean = _clean_route_path(str(raw_path or "").strip().strip("'\""))
    if not clean:
        return ""
    normalized = clean.replace("\\", "/")
    lower = normalized.lower()
    workspace_roots = (
        "c:/projects/piper/data/workspace",
        "/mnt/c/projects/piper/data/workspace",
        "/projects/piper/data/workspace",
        "data/workspace",
        "./data/workspace",
        "workspace",
        "./workspace",
    )
    if lower in workspace_roots:
        return "."
    for prefix in tuple(f"{root}/" for root in workspace_roots):
        if lower.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    normalized = normalized.lstrip("/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in {"", "."}:
        return "."
    normalized = PurePosixPath(normalized).as_posix().rstrip("/")
    if normalized.startswith("../"):
        return ""
    return normalized or "."


def _display_workspace_scope_path(scope_path: str) -> str:
    normalized = _normalize_workspace_scope_path(scope_path)
    if not normalized or normalized == ".":
        return "."
    return f"./{normalized}"


def _extract_extension_file_work_scope(
    user_msg: str,
    card: Dict,
    recent_history: Sequence[dict[str, Any]],
) -> str:
    generic_names = {"root", "workspace", "folder", "directory", "subfolder", "path"}
    standalone_path_pattern = re.compile(
        r"(?i)(?P<path>(?:\./|\.\\|/|[A-Za-z]:[\\/])[\w./\\:-]+)"
    )
    path_patterns = (
        re.compile(r"(?is)\b(?:folder|directory|subfolder|path)\s+(?P<path>(?:[A-Za-z]:)?[\w./\\:-]+)"),
        re.compile(r"(?is)(?P<path>(?:[A-Za-z]:)?[\w./\\:-]+)\s+(?:folder|directory|subfolder)\b"),
    )
    named_folder_pattern = re.compile(
        r"(?i)\b(?:the\s+)?(?P<name>[a-z0-9][a-z0-9_.-]{1,80})\s+(?:folder|directory|subfolder)\b"
    )

    def _extract_from_text(text: str) -> str:
        raw = str(text or "")
        if not raw:
            return ""
        for match in standalone_path_pattern.finditer(raw):
            candidate = _normalize_workspace_scope_path(match.group("path"))
            if candidate and candidate.lower() not in generic_names:
                return candidate
        for pattern in path_patterns:
            for match in pattern.finditer(raw):
                candidate = _normalize_workspace_scope_path(match.group("path"))
                if candidate and candidate.lower() not in generic_names:
                    return candidate
        for parts in _QUOTED_TEXT_RE.findall(raw):
            quoted = next((part for part in parts if part), "")
            candidate = _normalize_workspace_scope_path(quoted)
            if candidate and candidate.lower() not in generic_names and candidate != ".":
                return candidate
        for match in named_folder_pattern.finditer(raw):
            candidate = _normalize_workspace_scope_path(match.group("name"))
            if candidate and candidate.lower() not in generic_names:
                return candidate
        return ""

    explicit_user_scope = _extract_from_text(user_msg)
    if explicit_user_scope:
        return explicit_user_scope

    user_text = str(user_msg or "").lower()
    if (
        FILE_ORG_REQUEST_RE.search(str(user_msg or ""))
        and (EXTENSION_GROUPING_RE.search(str(user_msg or "")) or FILE_TYPE_GROUPING_RE.search(str(user_msg or "")))
        and not re.search(
            r"\b(there|that folder|that directory|that subfolder|that path|same folder|same directory|same subfolder|inside it|under it)\b",
            user_text,
        )
    ):
        return "."

    blobs: list[str] = []
    blobs.append(str(card.get("goal") or ""))
    blobs.extend(str(item) for item in (card.get("context") or []))
    for stage in card.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        blobs.append(str(stage.get("stage_goal") or ""))
        blobs.append(str(stage.get("success_condition") or ""))
        blobs.extend(str(item) for item in (stage.get("active_targets") or []))
    for message in reversed(recent_history):
        blobs.append(str(message.get("content") or ""))

    for blob in blobs:
        candidate = _extract_from_text(blob)
        if candidate:
            return candidate
    return "."


def _clean_route_content(raw_content: str) -> str:
    text = str(raw_content or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _split_file_followup_tail(raw_subject: str) -> tuple[str, bool]:
    subject = str(raw_subject or "").strip()
    if not subject:
        return ("", False)
    followup_re = re.compile(
        r"(?is)^(?P<subject>.+?)\s*(?:,?\s*(?:and\s+then|then|and)\s+read(?:\s+it)?(?:\s+back)?(?:\s+(?:to me|for me|out loud|aloud))?)\s*$"
    )
    match = followup_re.match(subject)
    if not match:
        return (subject, False)
    return (str(match.group("subject") or "").strip(), True)


def _normalize_extension_file_work(
    decision: RouteDecision,
    card: Dict,
    stages: List[StageCard],
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    if not any(str(stage.get("stage_type", "")).upper() == "FILE_WORK" for stage in stages):
        return None

    stage_blob = " ".join(
        f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
        for stage in stages
    )
    context_blob = " ".join(str(item) for item in card.get("context") or [])
    history_blob = " ".join(str(message.get("content") or "") for message in recent_history)
    combined = " ".join(filter(None, [user_msg, str(card.get("goal", "")), stage_blob, context_blob, history_blob]))
    if not combined:
        return None

    if not FILE_ORG_REQUEST_RE.search(combined):
        return None

    extension_like = bool(
        EXTENSION_GROUPING_RE.search(combined)
        or FILE_TYPE_GROUPING_RE.search(combined)
    )
    if not extension_like:
        return None

    cleanup_empty = bool(EMPTY_DIR_CLEANUP_RE.search(combined))
    scope_root = _extract_extension_file_work_scope(user_msg, card, recent_history)
    scope_display = _display_workspace_scope_path(scope_root)
    scope_context = (
        "The workspace root is '.'."
        if scope_root == "."
        else f"The requested reorganization root is '{scope_display}'."
    )
    scope_guard = (
        "Reorganize files under the workspace root only."
        if scope_root == "."
        else f"Only reorganize files under '{scope_display}'. Do not sweep the whole workspace root."
    )

    normalized = dict(decision)
    new_card = dict(card)
    new_card["goal"] = (
        f"Consolidate files under '{scope_display}' so each extension ends up in one relevant folder "
        "and remove folders that become empty."
        if cleanup_empty
        else f"Consolidate files under '{scope_display}' so each extension ends up in one relevant folder."
    )
    new_card["context"] = [
        scope_context,
        scope_guard,
        "Treat this as extension-based file organization, not a filename lookup.",
    ]
    new_card["stages"] = [
        {
            "stage_goal": f"Inspect '{scope_display}' and build an extension inventory with a destination folder chosen for each extension found there.",
            "stage_type": "FILE_WORK",
            "success_condition": f"An extension inventory exists for '{scope_display}' and a destination folder is identified for each relevant extension under that scope.",
            "allowed_tools": ["FILE_OP"],
            "active_targets": [scope_root],
        },
        {
            "stage_goal": f"Consolidate files under '{scope_display}' so each extension lives in one chosen destination folder without creating duplicates.",
            "stage_type": "FILE_WORK",
            "success_condition": f"For every relevant extension under '{scope_display}', files are consolidated into a single destination folder and duplicate identical files are not kept twice.",
            "allowed_tools": ["FILE_OP"],
            "active_targets": [scope_root],
        },
    ]
    if cleanup_empty:
        new_card["stages"].append(
            {
                "stage_goal": f"Delete folders under '{scope_display}' that are empty after consolidation.",
                "stage_type": "FILE_WORK",
                "success_condition": f"No empty folders remain under '{scope_display}'.",
                "allowed_tools": ["FILE_OP"],
                "active_targets": [scope_root],
            }
        )
    normalized["card"] = new_card
    return normalized
