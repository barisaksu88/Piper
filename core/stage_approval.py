from __future__ import annotations

import re
from typing import Literal


StageApprovalDecision = Literal["approve", "decline"]

_APPROVE_RE = re.compile(
    r"(?is)^\s*(?:"
    r"yes(?:\s*[, ]\s*please)?|yeah|yep|yup|sure|okay|ok|alright|all right|"
    r"go ahead|please do|do it|proceed|continue|approved?|confirmed?|"
    r"apply it|execute it|sounds good|looks good|that's right|that is right|correct"
    r")\s*[.!?]*\s*$"
)
_DECLINE_RE = re.compile(
    r"(?is)^\s*(?:"
    r"no|nope|nah|no thanks|not now|don't|dont|do not|cancel|stop|"
    r"never mind|nevermind|leave it|leave them|forget it"
    r")\s*[.!?]*\s*$"
)


def classify_stage_approval_reply(user_msg: str) -> StageApprovalDecision | None:
    """Classify a reply to an approval pause using exact, conservative phrases."""
    text = str(user_msg or "").strip()
    if not text:
        return None
    if _APPROVE_RE.match(text):
        return "approve"
    if _DECLINE_RE.match(text):
        return "decline"
    return None
