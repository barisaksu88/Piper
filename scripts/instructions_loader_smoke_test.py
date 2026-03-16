from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.instructions_loader import InstructionLoader  # noqa: E402


def main() -> int:
    path = ROOT_DIR / "data" / "prompts" / "instructions.txt"
    raw = path.read_text(encoding="utf-8")
    loaded = InstructionLoader(path).load()
    success = (
        len(raw.strip()) == len(loaded)
        and "[TRUNCATED instructions.txt]" not in loaded
        and raw.strip() == loaded
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "raw_len": len(raw),
                "loaded_len": len(loaded),
                "has_truncation_marker": "[TRUNCATED instructions.txt]" in loaded,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
