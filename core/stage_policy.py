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


def stage_requires_user_approval(stage: StageCard | dict) -> bool:
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
