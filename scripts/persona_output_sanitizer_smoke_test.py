from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.persona_output import sanitize_persona_output  # noqa: E402


def main() -> int:
    event_wording = sanitize_persona_output(
        "Systems indicate that the event regarding the card has been removed from the schedule, Sir. It is no longer listed among your upcoming tasks.",
        route_decision={"decision": "TASK"},
        outcome_block="=== STAGE 1 OUTCOME ===\nRESULT: EVENT REMOVED\nLAST_LOG: Event removed: I need to watch my card",
        user_msg="Remove the one about the card.",
    )
    casual_chat = sanitize_persona_output(
        "It is a pleasure to hear that, Sir. Given your background as a mechanical engineer and your current 2012 BMW, I suspect your enthusiasm is well-founded.\n\nSystems indicate that your car insurance renewal is scheduled for 25th March. Would you like to discuss any specific modifications or maintenance plans for the BMW, or perhaps explore the latest automotive news?",
        route_decision={"decision": "CHAT"},
        outcome_block="",
        user_msg="I like cars.",
    )
    no_mutation_ack = sanitize_persona_output(
        "You are most welcome, Sir. The systems indicate no further mutations were required for that entry, and the record stands as confirmed.",
        route_decision={"decision": "CHAT"},
        outcome_block="",
        user_msg="Thank you",
    )

    success = (
        "upcoming tasks" not in event_wording.lower()
        and "upcoming events" in event_wording.lower()
        and "Would you like" not in casual_chat
        and "car insurance renewal" not in casual_chat
        and "mechanical engineer" in casual_chat
        and "no further mutations were required" not in no_mutation_ack.lower()
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "event_wording": event_wording,
                "casual_chat": casual_chat,
                "no_mutation_ack": no_mutation_ack,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
