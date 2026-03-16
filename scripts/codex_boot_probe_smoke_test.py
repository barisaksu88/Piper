from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.codex_bridge import probe_codex_support  # noqa: E402


def main() -> int:
    previous = os.environ.get("PIPER_CODEX_BOOT_PROBE_SIMULATE")
    try:
        os.environ["PIPER_CODEX_BOOT_PROBE_SIMULATE"] = "ok"
        ok, message = probe_codex_support(timeout_s=1.0)
        if not ok or message != "Engineering channel: ONLINE":
            raise AssertionError((ok, message))

        os.environ["PIPER_CODEX_BOOT_PROBE_SIMULATE"] = "offline"
        ok, message = probe_codex_support(timeout_s=1.0)
        if ok or "OFFLINE" not in message:
            raise AssertionError((ok, message))

        print(
            "{\n"
            '  "success": true,\n'
            '  "online_message": "Engineering channel: ONLINE",\n'
            f'  "offline_message": "{message}"\n'
            "}"
        )
        return 0
    finally:
        if previous is None:
            os.environ.pop("PIPER_CODEX_BOOT_PROBE_SIMULATE", None)
        else:
            os.environ["PIPER_CODEX_BOOT_PROBE_SIMULATE"] = previous


if __name__ == "__main__":
    raise SystemExit(main())
