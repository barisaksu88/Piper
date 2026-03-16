from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Sequence

from core.contracts import RouteDecision, StageCard
from core.engines.state_mutation import StateMutationEngine
from core.route_patterns import (
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
)
from core.route_subjects import (
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
_FILE_TARGET_RE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,8}")
_GENERIC_LOOKUP_SUBJECTS = {
    "document",
    "doc",
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
_INTERACTIVE_VERIFY_RE = re.compile(r"\b(verify|confirm|check|observe|test|try|report)\b", re.IGNORECASE)
_INTERACTIVE_CONTROL_RE = re.compile(
    r"\b(controls?|input|movement|left|right|up|down|keyboard|mouse|responsive|respond|press|click|catch|gameplay|works?)\b",
    re.IGNORECASE,
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


def normalize_route_decision(
    decision: RouteDecision,
    user_msg: str,
    recent_history: Sequence[dict[str, Any]] | None = None,
) -> RouteDecision:
    history = [dict(item) for item in (recent_history or []) if isinstance(item, dict)]
    speculative_chat = _normalize_speculative_task_idea_to_chat(decision, user_msg)
    if speculative_chat is not None:
        return speculative_chat

    direct_file_work = _normalize_direct_file_work(user_msg)
    if direct_file_work is not None:
        return direct_file_work

    document_lookup = _normalize_workspace_document_lookup(decision, user_msg, history)
    if document_lookup is not None:
        return document_lookup

    code_target_followup = _normalize_code_target_followup(decision, user_msg, history)
    if code_target_followup is not None:
        return code_target_followup

    interactive_runtime = _normalize_interactive_runtime_verification(decision, user_msg)
    if interactive_runtime is not None:
        return interactive_runtime

    state_normalized = _STATE_MUTATION_ENGINE.normalize_route_decision(
        decision=decision,
        user_msg=user_msg,
        recent_history=history,
    )
    if state_normalized is not None:
        return state_normalized

    if not decision or decision.get("decision") != "TASK":
        return decision

    card = dict(decision.get("card") or {})
    stages = [dict(stage) for stage in card.get("stages") or []]
    if not stages:
        return decision

    extension_file_work = _normalize_extension_file_work(decision, card, stages, user_msg)
    if extension_file_work is not None:
        return extension_file_work
    return decision


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

    subject = _extract_document_lookup_subject(text) or _extract_recent_document_lookup_subject(
        recent_history,
        current_text=text,
    )
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


def _extract_document_lookup_subject(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    patterns = (
        re.compile(r"(?i)\b(?:tell me what it says in|what does it say in|what(?:'s| is) in)\s+(?:the\s+)?(?P<subject>.+?)(?:[?!.]|$)"),
        re.compile(r"(?i)\b(?:search for|look for|find|locate|open|read|show me)\s+(?:the\s+)?(?P<subject>.+?)(?:[?!.]|$)"),
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
    return normalized not in _BLOCKED_LOOKUP_SUBJECTS


def _extract_explicit_file_target_from_decision(decision: RouteDecision) -> str:
    if not decision or decision.get("decision") != "TASK":
        return ""
    card = dict(decision.get("card") or {})
    blobs = [str(card.get("goal") or "")]
    blobs.extend(str(item) for item in (card.get("context") or []))
    for stage in card.get("stages") or []:
        if isinstance(stage, dict):
            blobs.append(str(stage.get("stage_goal") or ""))
            blobs.append(str(stage.get("success_condition") or ""))
    return _extract_file_target_from_texts(blobs)


def _extract_recent_explicit_file_target(
    recent_history: Sequence[dict[str, Any]],
    *,
    current_text: str,
) -> str:
    for message in reversed(recent_history):
        content = str(message.get("content") or "").strip()
        if not content or content == current_text:
            continue
        target = _extract_file_target_from_texts([content])
        if target:
            return target
    return ""


def _extract_file_target_from_texts(texts: Sequence[str]) -> str:
    for text in texts:
        for match in _FILE_TARGET_RE.findall(str(text or "")):
            cleaned = _clean_route_path(match)
            if cleaned:
                return cleaned
    return ""


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
        return {
            "decision": "TASK",
            "card": {
                "goal": f"Delete '{path}'.",
                "context": [
                    "The workspace root is '.'.",
                ],
                "stages": [
                    {
                        "stage_goal": f"Delete the file '{path}'.",
                        "stage_type": "FILE_WORK",
                        "success_condition": f"'{path}' does not exist in the workspace.",
                        "allowed_tools": ["FILE_OP"],
                    }
                ],
            },
        }

    return None


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


def _clean_route_path(raw_path: str) -> str:
    return str(raw_path or "").strip().rstrip(".,;:!?").replace("\\", "/")


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
) -> RouteDecision | None:
    if not any(str(stage.get("stage_type", "")).upper() == "FILE_WORK" for stage in stages):
        return None

    stage_blob = " ".join(
        f"{stage.get('stage_goal', '')} {stage.get('success_condition', '')}"
        for stage in stages
    )
    context_blob = " ".join(str(item) for item in card.get("context") or [])
    combined = " ".join(filter(None, [user_msg, str(card.get("goal", "")), stage_blob, context_blob]))
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

    normalized = dict(decision)
    new_card = dict(card)
    new_card["goal"] = (
        "Consolidate workspace files so each extension ends up in one relevant folder "
        "and remove folders that become empty."
        if cleanup_empty
        else "Consolidate workspace files so each extension ends up in one relevant folder."
    )
    new_card["stages"] = [
        {
            "stage_goal": "Inspect the workspace and build an extension inventory with a destination folder chosen for each extension.",
            "stage_type": "FILE_WORK",
            "success_condition": "An extension inventory exists and a destination folder is identified for each relevant extension.",
            "allowed_tools": ["FILE_OP"],
        },
        {
            "stage_goal": "Consolidate files so each extension lives in one chosen destination folder without creating duplicates.",
            "stage_type": "FILE_WORK",
            "success_condition": "For every relevant extension, files are consolidated into a single destination folder and duplicate identical files are not kept twice.",
            "allowed_tools": ["FILE_OP"],
        },
    ]
    if cleanup_empty:
        new_card["stages"].append(
            {
                "stage_goal": "Delete folders that are empty after consolidation.",
                "stage_type": "FILE_WORK",
                "success_condition": "No empty folders remain under the workspace root.",
                "allowed_tools": ["FILE_OP"],
            }
        )
    normalized["card"] = new_card
    return normalized
