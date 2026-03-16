from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.prompt_context import PromptContextService  # noqa: E402
from core.instructions_loader import InstructionLoader  # noqa: E402
from memory.vision_session import VisionSessionMemory  # noqa: E402
from ui.event_speech import EVENT_SPEECH_NOISY, EVENT_SPEECH_OFF, event_speech_message  # noqa: E402


class _DummyEnv:
    def render_block(self) -> str:
        return "[ENVIRONMENT]\n{}"


class _DummyOps:
    def render_block(self, query: str = "") -> str:
        return "[OPERATIONAL STATE]\n{}"


class _DummyKnowledge:
    def load(self):
        return {}

    def render_prompt_state(self, user_msg: str) -> str:
        return "[WORLD STATE]\n"

    def render_situational_state(self, user_msg: str) -> str:
        return ""


class _DummyBrain:
    def recall(self, user_msg: str, n_results: int = 5):
        return [{"text": "ordinary recalled memory", "metadata": {"date": "Mar 10, 2026"}}]


class _DummyDocs:
    def render_prompt_hits(self, user_msg: str, limit: int = 5):
        return []


def main() -> int:
    notes = VisionSessionMemory()
    notes.set_active(True)
    first_note = "That looks busier than it needs to be."
    third_note = "Might be time to close a tab or two."

    quiet_event = event_speech_message(
        "vision_snapshot_note",
        {"text": first_note},
        mode=EVENT_SPEECH_OFF,
    )
    if quiet_event and notes.should_speak(first_note):
        notes.add_note(first_note)

    speak_first_event = event_speech_message(
        "vision_snapshot_note",
        {"text": first_note},
        mode=EVENT_SPEECH_NOISY,
    )
    speak_first = bool(speak_first_event) and notes.should_speak(first_note)
    if speak_first:
        notes.add_note(first_note)

    speak_second_event = event_speech_message(
        "vision_snapshot_note",
        {"text": first_note},
        mode=EVENT_SPEECH_NOISY,
    )
    speak_second = bool(speak_second_event) and notes.should_speak(first_note)
    if speak_second:
        notes.add_note(first_note)

    speak_third_event = event_speech_message(
        "vision_snapshot_note",
        {"text": third_note},
        mode=EVENT_SPEECH_NOISY,
    )
    speak_third = bool(speak_third_event) and notes.should_speak(third_note)
    if speak_third:
        notes.add_note(third_note)
    speak_fourth = bool(speak_third_event) and notes.should_speak(first_note)
    if speak_fourth:
        notes.add_note(first_note)

    service = PromptContextService(
        instruction_loader=InstructionLoader(ROOT_DIR / "data" / "prompts" / "instructions.txt"),
        environment_service=_DummyEnv(),
        operational_state_service=_DummyOps(),
        knowledge_mgr=_DummyKnowledge(),
        brain=_DummyBrain(),
        document_memory=_DummyDocs(),
        vision_session_memory=notes,
    )
    context = service.build_persona_context(user_msg="What do you think?", style_overlay="", knowledge_enabled=True)

    notes.set_active(False)
    after_clear = notes.recent_notes(limit=5)

    success = (
        quiet_event is None
        and
        speak_first
        and not speak_second
        and speak_third
        and not speak_fourth
        and context.vision_notes == [
            "That looks busier than it needs to be.",
            "Might be time to close a tab or two.",
        ]
        and context.brain_hits == [{"text": "ordinary recalled memory", "metadata": {"date": "Mar 10, 2026"}}]
        and after_clear == []
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "quiet_event": quiet_event,
                "speak_first": speak_first,
                "speak_second": speak_second,
                "speak_third": speak_third,
                "speak_fourth": speak_fourth,
                "vision_notes": context.vision_notes,
                "brain_hits": context.brain_hits,
                "after_clear": after_clear,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
