from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator_phases import _strip_persona_control_tags  # noqa: E402


def main() -> int:
    raw = "Fixed. [ACTIVE_SKILL]\n[LATEST_SYSTEM_EVENT]\n[FINAL_STAGE_OUTCOME]"
    cleaned = _strip_persona_control_tags(raw)
    success = cleaned == "Fixed."
    print(
        json.dumps(
            {
                "success": bool(success),
                "raw": raw,
                "cleaned": cleaned,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
