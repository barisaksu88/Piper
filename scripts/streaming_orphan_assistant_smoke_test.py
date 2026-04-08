from __future__ import annotations

import json
import tempfile
from pathlib import Path

from _bootstrap import ROOT_DIR

from core.pipeline import ChatPipeline
from memory.chat_state import ChatState
from ui.controller_render import renderable_chat_messages


class _SilentTTS:
    def stream_start(self, *, voice=None, speed=None) -> None:
        return None

    def stream_push(self, text: str) -> None:
        return None

    def stream_flush(self) -> None:
        return None

    def stream_end(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def play_wav(self, path) -> None:
        return None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="piper-streaming-orphan-") as temp_dir:
        memory_path = Path(temp_dir) / "memory.jsonl"
        chat_state = ChatState(memory_path=memory_path)
        statuses: list[str] = []
        pipeline = ChatPipeline(
            tts=_SilentTTS(),
            chat_append_fn=chat_state.append,
            chat_upsert_fn=chat_state.upsert_streaming_assistant,
            persist_turn_fn=chat_state.persist_turn,
            set_status_fn=statuses.append,
            finalize_stream_fn=chat_state.finalize_streaming_assistant,
        )

        chat_state.append("user", "Delete old note")
        pipeline.handle_event("start", "")
        pipeline.handle_event("delta", "Removed")

        chat_state.append("user", "Delete test_notes")
        after_retry_messages = renderable_chat_messages(chat_state.get_messages_snapshot())
        if any(role == "assistant" and content == "Removed" for role, content in after_retry_messages):
            raise AssertionError(f"Orphaned assistant bubble survived into new turn: {after_retry_messages}")

        final_text = "Removed test_notes.txt and verified the file change."
        pipeline.handle_event("start", "")
        pipeline.handle_event("delta", final_text)
        pipeline.handle_event("end", "")

        visible_messages = renderable_chat_messages(chat_state.get_messages_snapshot())
        assistant_messages = [content for role, content in visible_messages if role == "assistant"]
        if assistant_messages != [final_text]:
            raise AssertionError(f"Expected one finalized assistant reply, got: {visible_messages}")

        persisted_lines = [line for line in memory_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(persisted_lines) != 1:
            raise AssertionError(f"Expected one persisted assistant turn, got: {persisted_lines}")

        print(
            json.dumps(
                {
                    "success": True,
                    "visible_messages": visible_messages,
                    "statuses": statuses,
                    "persisted_lines": len(persisted_lines),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
