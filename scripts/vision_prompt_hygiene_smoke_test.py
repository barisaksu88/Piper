from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.prompting import build_persona_messages  # noqa: E402
from ui.vision_commentary import (  # noqa: E402
    VISION_COMMENT_SKIP_TOKEN,
    build_vision_comment_prompt,
    looks_like_viewer_assumption,
    recent_user_vision_context,
)


def main() -> int:
    history = [
        {"role": "user", "content": "we are watching ironman"},
        {"role": "assistant", "content": "Unless you want to talk about strategy games while we wait."},
        {"role": "user", "content": "stop saying you, this is a movie none of it is me"},
        {"role": "assistant", "content": "Thinking..."},
    ]
    messages = build_persona_messages(
        system_content="SYSTEM",
        history=history,
        model_path="Qwen3.5-test",
    )
    recent_users = recent_user_vision_context(history, limit=3)
    prompt = build_vision_comment_prompt(
        recent_notes=["That scene is trying too hard."],
        recent_user_messages=recent_users,
    )
    convo = [m for m in messages if m.get("role") in {"user", "assistant"}]
    assistant_texts = [str(m.get("content") or "") for m in convo if m.get("role") == "assistant"]
    success = (
        recent_users
        == [
            "we are watching ironman",
            "stop saying you, this is a movie none of it is me",
        ]
        and assistant_texts == ["Unless you want to talk about strategy games while we wait."]
        and "movie" in prompt.lower()
        and "not narrating for a blind user" in prompt.lower()
        and "do not describe the viewer" in prompt.lower()
        and "we are watching ironman" in prompt
        and "stop saying you, this is a movie none of it is me" in prompt
        and VISION_COMMENT_SKIP_TOKEN in prompt
        and looks_like_viewer_assumption("You look like you just started a war with a toaster.")
        and not looks_like_viewer_assumption("You should skip this scene.")
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "recent_users": recent_users,
                "assistant_texts": assistant_texts,
                "prompt": prompt,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
