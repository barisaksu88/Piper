from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.prompting import build_persona_messages  # noqa: E402


def main() -> int:
    messages = build_persona_messages(
        system_content="You are Piper.",
        history=[
            {"role": "user", "content": "Run the game."},
            {"role": "assistant", "content": "The game is open."},
            {"role": "user", "content": "Please test the controls."},
            {"role": "assistant", "content": "Thinking..."},
        ],
        tail_system_content="[NO_MUTATION_RULE]\nDo not claim a mutation.",
        outcome_block="=== STAGE 1 OUTCOME ===\nRESULT: SUCCESS",
        model_path="Qwen3.5-9B-Q6_K.gguf",
    )
    system_content = str(messages[0].get("content") or "") if messages else ""
    user_content = str(messages[1].get("content") or "") if len(messages) > 1 else ""
    message_roles = [str(message.get("role") or "") for message in messages]
    transcript_idx = system_content.find("[CONVERSATION_TRANSCRIPT]")
    runtime_idx = system_content.rfind("[LATEST_RUNTIME_CONTEXT]")
    current_user_placeholder_idx = system_content.rfind("[CURRENT_USER_TURN]")
    latest_system_idx = system_content.rfind("ROLE: system")
    success = (
        len(messages) == 2
        and message_roles == ["system", "user"]
        and transcript_idx >= 0
        and runtime_idx > transcript_idx
        and system_content.startswith("You are Piper.")
        and "[MESSAGE_PROTOCOL]\n" in system_content
        and "[LATEST_RUNTIME_CONTEXT]\n" in system_content
        and "[1] ROLE: user" in system_content
        and "Run the game." in system_content
        and "The game is open." in system_content
        and "[CURRENT_USER_TURN]" in system_content
        and "Thinking..." not in system_content
        and current_user_placeholder_idx > system_content.rfind("The game is open.")
        and latest_system_idx > current_user_placeholder_idx
        and "Please test the controls." not in system_content
        and user_content == "Please test the controls."
        and "[NO_MUTATION_RULE]\nDo not claim a mutation." in system_content
        and "[LATEST_SYSTEM_EVENT]\n[FINAL_STAGE_OUTCOME]\n=== STAGE 1 OUTCOME ===\nRESULT: SUCCESS" in system_content
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "message_count": len(messages),
                "message_roles": message_roles,
                "system_has_runtime_context": "[LATEST_RUNTIME_CONTEXT]" in system_content,
                "runtime_after_transcript": runtime_idx > transcript_idx >= 0,
                "has_trailing_system_message": any(
                    idx > 0 and str(message.get("role") or "") == "system"
                    for idx, message in enumerate(messages)
                ),
                "latest_user_text_only_in_final_message": "Please test the controls." not in system_content
                and user_content == "Please test the controls.",
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
