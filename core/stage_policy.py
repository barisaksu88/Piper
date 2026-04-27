from __future__ import annotations

import re

from core.contracts import StageCard


_APPROVAL_RE = re.compile(
    r"\b("
    r"user confirmation|user approval|for approval|await approval|"
    r"ask for approval|ask for confirmation|present .* for approval|"
    r"present .* for confirmation|confirm before executing|approval before executing"
    r")\b"
)
_USER_INPUT_RE = re.compile(
    r"\b("
    r"clarif\w*|ask (?:the )?user|user confirms|user confirm|"
    r"await user|await the user|wait for the user|need more details|"
    r"gather requirements|determine .* with the user|collect requirements|"
    r"confirm the game|confirm the idea|confirm the concept"
    r")\b"
)
_CONTEXT_APPROVAL_STAGE_RE = re.compile(
    r"\b("
    r"propos\w*|plan|planning|recommend|suggest|approval|approve|"
    r"execute|apply|organize|organise|reorgani[sz]\w*|move|rename|"
    r"delete|remove|edit|modify|write|change"
    r")\b"
)


def stage_type_name(stage: StageCard | dict) -> str:
    return str((stage or {}).get("stage_type", "")).strip().upper()


def stage_is_chat(stage: StageCard | dict) -> bool:
    return stage_type_name(stage) == "CHAT"


def stage_goal_success_text(stage: StageCard | dict) -> str:
    return " ".join(
        [
            str((stage or {}).get("stage_goal", "") or ""),
            str((stage or {}).get("success_condition", "") or ""),
        ]
    ).strip().lower()


def stage_text_with_context(stage: StageCard | dict) -> str:
    return " ".join(
        [
            str((stage or {}).get("stage_goal", "") or ""),
            str((stage or {}).get("success_condition", "") or ""),
            " ".join(str(item) for item in ((stage or {}).get("context") or [])),
        ]
    ).strip().lower()


def stage_requires_user_approval(stage: StageCard | dict) -> bool:
    # Already approved on resume — do not pause again.
    if stage.get("approved"):
        return False
    goal_success = stage_goal_success_text(stage)
    if _APPROVAL_RE.search(goal_success):
        return True
    # Destructive actions (deletion, removal) require explicit approval.
    if re.search(r"\b(delete|remove)\b", goal_success):
        return True
    context_text = " ".join(str(item) for item in ((stage or {}).get("context") or [])).strip().lower()
    if not context_text or not _APPROVAL_RE.search(context_text):
        return False
    return bool(_CONTEXT_APPROVAL_STAGE_RE.search(goal_success))


def stage_is_explicit_proposal(stage: StageCard | dict) -> bool:
    """Return True if the stage text explicitly frames itself as a proposal/approval request."""
    return bool(_APPROVAL_RE.search(stage_goal_success_text(stage)))


def stage_requires_user_input(stage: StageCard | dict) -> bool:
    if stage_is_chat(stage):
        return True
    text = stage_goal_success_text(stage)
    if not text:
        return False
    if stage_requires_user_approval(stage):
        return False
    return bool(_USER_INPUT_RE.search(text))
