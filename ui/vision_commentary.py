from __future__ import annotations

from typing import Iterable

from memory.vision_session import looks_like_viewer_assumption


VISION_COMMENT_SKIP_TOKEN = "SKIP"


def recent_user_vision_context(messages_snapshot: Iterable[dict], *, limit: int = 3) -> list[str]:
    items: list[str] = []
    for message in reversed(list(messages_snapshot)):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        items.append(content)
        if len(items) >= max(int(limit), 0):
            break
    items.reverse()
    return items


def build_vision_comment_prompt(*, recent_notes: list[str], recent_user_messages: list[str]) -> str:
    recent_block = "\n".join(f"- {item}" for item in recent_notes if str(item).strip()) or "- none"
    user_block = "\n".join(f"- {item}" for item in recent_user_messages if str(item).strip()) or "- none"
    return (
        "You are reacting to a screen capture, not narrating for a blind user. "
        "The screen may be showing a movie, video, game, app, desktop, or document. "
        "Respect recent user context if they already told you what kind of screen content this is.\n\n"
        "Write one short companion-style comment, aside, suggestion, or reaction inspired by the on-screen scene.\n"
        "Do not explain what is visible.\n"
        "Do not describe the viewer or speak as if the screen is a webcam, selfie, or live camera feed.\n"
        "Never write lines like 'you look...', 'you're about to...', or 'you are...'.\n"
        "Do not start with 'I see', 'There is', 'The image shows', or 'The most prominent visual element'.\n"
        f"Never repeat, paraphrase, lightly remix, or reuse the punchline of any recent visual comment. "
        f"If you do not have a meaningfully different remark, reply with exactly {VISION_COMMENT_SKIP_TOKEN}.\n"
        "Recent visual comments you must not reuse:\n"
        f"{recent_block}\n\n"
        "Recent user context about the screen:\n"
        f"{user_block}\n\n"
        "Keep it to one short sentence."
    )
