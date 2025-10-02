# C:\Piper\scripts\core\core_machine.py
from __future__ import annotations
import sys
from typing import Callable, Optional

try:
    from core.core_commands import handle_core_command, core_banner  # type: ignore
except Exception:
    from core.core_commands import handle_core_command, core_banner  # type: ignore

# NEW: prompt seam
try:
    from services.cli_prompt import current_prompt  # type: ignore
except Exception:
    from services.cli_prompt import current_prompt  # type: ignore

# in C:\Piper\scripts\core\core_machine.py near the top
try:
    from services.cli_prompt import current_prompt  # type: ignore
except Exception:
    from services.cli_prompt import current_prompt  # type: ignore

# ... then in the REPL loop, write the prompt via current_prompt()
# sys.stdout.write(current_prompt()); sys.stdout.flush()

SayFn = Callable[[str, Optional[str]], None]

class CoreMachine:
    def __init__(self, say: SayFn, version_str: str) -> None:
        self._say = say
        self._version = version_str

    def run(self) -> None:
        core_banner(self._say, self._version)

        while True:
            try:
                # prompt via seam (behavior-preserving: still prints "> ")
                sys.stdout.write(current_prompt())
                sys.stdout.flush()

                line = sys.stdin.readline()
                if not line:
                    return
                user_input = line.rstrip("\r\n")

                res = handle_core_command(user_input, self._say, version_str=self._version)
                if res == "EXIT":
                    return
                if res is True:
                    continue

                self._say("I don't know that command. Try 'help'.", "error_hard")

            except KeyboardInterrupt:
                self._say("âœ” Bye.", "confirm")
                return
            except Exception as e:
                self._say(f"Unexpected error: {e}", "error")


