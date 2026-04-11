"""core/debug_tools.py

Centralized logging for prompts and payloads.
Prevents accidental deletion during refactoring.

Each LLM layer writes to its own file so you can inspect any layer
independently without hunting through a combined log:
  - router_debug.txt      SECRETARY / routing decisions
  - persona_debug.txt     PERSONA and PERSONA_RECALL_* turns
  - planner_debug.txt     STAGE_*_STEP_* planner steps
  - doc_focus_debug.txt   DOCUMENT_FOCUS and doc-vision calls

All writes use os.fsync() so the file is on disk before the LLM call
begins — no stale reads from a partially-flushed OS buffer.
"""
import logging
import os
import time
from pathlib import Path
from typing import List, Dict

_LOG = logging.getLogger(__name__)

def log_prompt_debug(
    debug_path: Path,
    messages: List[Dict[str, str]],
    phase: str = "UNKNOWN",
) -> None:
    """Append the exact prompt sent to the LLM to *debug_path*.

    The write is followed by an explicit flush + fsync so the file
    reflects the prompt before the HTTP request leaves the process.
    """
    try:
        debug_path.parent.mkdir(parents=True, exist_ok=True)

        with open(debug_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"TIMESTAMP: {time.strftime('%H:%M:%S')}\n")
            f.write(f"PHASE: {phase}\n")
            f.write(f"{'='*60}\n")

            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                f.write(f"[{i}] ROLE: {role}\n")
                f.write(f"CONTENT:\n{content}\n")
                f.write(f"{'-'*40}\n")

            # Guarantee the entry is on disk before the LLM call fires.
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass  # fsync may be unavailable on some virtual FS

    except Exception as e:
        _LOG.warning("[DebugTools] Error writing prompt log: %s", e)

def log_agent_thought(
    debug_path: Path,
    thought: str,
    tool: str,
    result: str
):
    """Appends a single step summary to the agent log."""
    try:
        with open(debug_path, "a", encoding="utf-8") as f:
            f.write(f"THOUGHT: {thought}\n")
            f.write(f"TOOL: {tool}\n")
            f.write(f"RESULT: {result[:500]}...\n\n")
    except Exception as e:
        _LOG.warning("[DebugTools] Error writing agent log: %s", e)
