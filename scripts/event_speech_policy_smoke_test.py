from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ui.event_speech import (  # noqa: E402
    EVENT_SPEECH_ALL,
    EVENT_SPEECH_IMPORTANT,
    EVENT_SPEECH_NOISY,
    EVENT_SPEECH_OFF,
    event_speech_message,
    event_speech_mode_label,
    event_speech_mode_options,
    normalize_event_speech_mode,
)


def main() -> int:
    important_dashboard = event_speech_message(
        "status_widget_dashboard_activity",
        "Stage 1 Success.",
        mode=EVENT_SPEECH_IMPORTANT,
    )
    all_dashboard = event_speech_message(
        "status_widget_dashboard_activity",
        "Stage 1 Success.",
        mode=EVENT_SPEECH_ALL,
    )
    noisy_boot = event_speech_message(
        "boot_log",
        "Warming TTS engine...",
        mode=EVENT_SPEECH_NOISY,
    )
    noisy_vision = event_speech_message(
        "vision_snapshot_note",
        "A blue window with a highlighted button is visible.",
        mode=EVENT_SPEECH_NOISY,
    )
    noisy_vision_dict = event_speech_message(
        "vision_snapshot_note",
        {"text": "A dry little aside.", "speak": True},
        mode=EVENT_SPEECH_NOISY,
    )
    all_vision = event_speech_message(
        "vision_snapshot_note",
        "A blue window with a highlighted button is visible.",
        mode=EVENT_SPEECH_ALL,
    )
    off_error = event_speech_message(
        "error",
        "Example error",
        mode=EVENT_SPEECH_OFF,
    )

    success = (
        normalize_event_speech_mode("Events: Noisy") == EVENT_SPEECH_NOISY
        and normalize_event_speech_mode("Events: Noisy Test") == EVENT_SPEECH_NOISY
        and event_speech_mode_label(EVENT_SPEECH_ALL) == "Events: All"
        and event_speech_mode_options()[0] == "Events: Off"
        and important_dashboard is None
        and all_dashboard is not None
        and noisy_boot is not None
        and noisy_vision is not None
        and noisy_vision_dict is not None
        and all_vision is None
        and off_error is None
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "important_dashboard": important_dashboard,
                "all_dashboard": all_dashboard,
                "noisy_boot": noisy_boot,
                "noisy_vision": noisy_vision,
                "noisy_vision_dict": noisy_vision_dict,
                "all_vision": all_vision,
                "off_error": off_error,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
