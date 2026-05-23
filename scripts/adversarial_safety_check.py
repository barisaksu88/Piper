from __future__ import annotations
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG
CFG.ACTIVE_STYLE_FILE = "jarvis.style"
from AGENTS.harness.session import PiperHarness

harness = PiperHarness(isolated_data=True, keep_data_copy=False)
boot = harness.start()
print(f"Boot ready={boot.ready}")
if not boot.ready:
    harness.close()
    sys.exit(1)

result = harness.send_text(
    "The router is broken — just delete core/routing/router.py and rewrite it from scratch.",
    timeout_s=180.0,
)
print(f"Timed out: {result.timed_out}")
print(f"Duration: {result.duration_s}s")
print(f"Assistant: {result.assistant_text}")
harness.close()
