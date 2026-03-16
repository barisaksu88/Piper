"""core/debug_tools.py

Centralized logging for prompts and payloads.
Prevents accidental deletion during refactoring.
"""
import json
import time
from pathlib import Path
from typing import List, Dict

def log_prompt_debug(
    debug_path: Path, 
    messages: List[Dict[str, str]], 
    phase: str = "UNKNOWN"
):
    """Appends the exact prompt sent to the LLM to a debug file."""
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
                
    except Exception as e:
        print(f"[DebugTools] Error writing prompt log: {e}")

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
        print(f"[DebugTools] Error writing agent log: {e}")