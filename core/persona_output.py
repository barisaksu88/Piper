from __future__ import annotations

import re

from core.contracts import RouteDecision

_FORBIDDEN_PERSONA_TAIL_RE = re.compile(
    r"(?is)(?:\n\s*\n|\s+)"
    r"(?:Would you like|Do you want|Shall I|May I|Can I|Would you care to|"
    r"Would you like me to|Would you like to discuss|Let me know if|Is there anything else)"
    r"[^?]*\?\s*$"
)
_OPERATIONAL_GARNISH_RE = re.compile(
    r"(?i)\b(task|tasks|event|events|schedule|scheduled|calendar|reminder|records?)\b"
)
_CASUAL_CHAT_REFERENCE_RE = re.compile(
    r"(?i)\b(task|tasks|event|events|schedule|scheduled|calendar|reminder|memory|knowledge)\b"
)
_THINK_TAG_RE = re.compile(r"(?is)</?think>")
_RECALL_TAG_INLINE_RE = re.compile(r"(?is)\[RECALL:\s*.*?\]")


def sanitize_persona_output(
    text: str,
    *,
    route_decision: RouteDecision | None = None,
    outcome_block: str = "",
    user_msg: str = "",
) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""

    cleaned = _THINK_TAG_RE.sub("", cleaned).strip()
    cleaned = _RECALL_TAG_INLINE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"(?i)\bupcoming tasks\b", "upcoming events", cleaned)
    cleaned = re.sub(r"(?i)\bupcoming task\b", "upcoming event", cleaned)
    cleaned = re.sub(
        r"(?is)(?:^|(?<=[.?!])\s+)(?:The\s+)?systems indicate no further mutations were required[^.?!]*[.?!]\s*",
        "",
        cleaned,
    )
    cleaned = cleaned.strip()

    prior = None
    while prior != cleaned:
        prior = cleaned
        cleaned = _FORBIDDEN_PERSONA_TAIL_RE.sub("", cleaned).strip()

    route = str((route_decision or {}).get("decision") or "").strip().upper()
    user_text = str(user_msg or "").strip()
    casual_chat_turn = (
        route == "CHAT"
        and not str(outcome_block or "").strip()
        and "?" not in user_text
        and not _CASUAL_CHAT_REFERENCE_RE.search(user_text)
    )
    if casual_chat_turn and "\n\n" in cleaned:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
        kept: list[str] = []
        for idx, paragraph in enumerate(paragraphs):
            if idx == 0:
                kept.append(paragraph)
                continue
            if (
                re.search(r"(?i)\b(?:Systems indicate|It appears that)\b", paragraph)
                and _OPERATIONAL_GARNISH_RE.search(paragraph)
            ):
                continue
            kept.append(paragraph)
        cleaned = "\n\n".join(kept).strip()

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()
