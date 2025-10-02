# C:\Piper\scripts\core\core_commands.py
# T-Core01: core CLI commands extracted (no persona deps)

from __future__ import annotations
from datetime import datetime
from typing import Callable, Literal, Optional

SayFn = Callable[[str, Optional[str]], None]  # say(text, tone)

AVAILABLE_STATES = ("SLEEPING", "WAKING", "LISTENING", "THINKING", "SPEAKING")

def core_banner(say: SayFn, version_str: str) -> None:
    say(f"[STATE] available_states={'|'.join(AVAILABLE_STATES)}", "status")
    say("Piper is ready. Type 'wake' to greet or 'help' for commands.", "info")

def handle_core_command(cmd: str, say: SayFn, *, version_str: str) -> Optional[Literal["EXIT", True, False]]:
    """
    Returns:
      - "EXIT"  â†’ caller should exit program
      - True    â†’ handled (continue loop)
      - False   â†’ not a core command (let caller try others)
    """
    c = (cmd or "").strip().lower()

    if c == "":
        return True  # ignore empty lines

    if c == "wake":
        # Emit a state transition first so GUI can parse it
        say("[STATE] SLEEPING -> WAKING", "status")
        say("Hello sir!", "greet")
        return True

    if c == "sleep":
        # Emit a state transition before the message
        say("[STATE] SPEAKING -> SLEEPING", "status")
        say("[TTS] Going to sleep.", "info")
        return True

    if c == "about":
        say(f"Piper CLI - Core {version_str}. SAFE_MODE is ON.", "about")
        return True

    if c == "time":
        say(f"The time is {datetime.now().strftime('%H:%M')}.", "info")
        return True

    if c == "date":
        say(f"Today is {datetime.now().strftime('%Y-%m-%d')}.", "info")
        return True

    if c == "version":
        say(f"Core version {version_str}.", "confirm")
        return True

    if c == "help":
        say("Commands: wake, sleep, about, time, date, version, help, exit.", "info")
        say("Flow: wake -> (LISTENING) -> type a command.", "info")
        return True

    if c == "exit":
        say("Bye.", "confirm")
        return "EXIT"

    return False  # not handled here

