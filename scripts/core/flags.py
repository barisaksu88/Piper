# scripts/core/flags.py
"""
Flag reader/summary for Core sandbox demos.
No side effects; pure reads of environment variables.
"""

from __future__ import annotations
import os
from dataclasses import dataclass

# All flags we currently surface in logs
_FLAG_KEYS = [
    "PIPER_CORE_RUNTIME",
    "PIPER_CORE_TRANSITIONS",
    "PIPER_CORE_FORWARD_INPUT",
    "PIPER_CORE_SANDBOX",
    "PIPER_CORE_DEMO_ASR",
    "PIPER_CORE_DEMO_ASR2",
    "PIPER_CORE_DEMO_SPEAK",
    "PIPER_CORE_DEMO_STOP",
    "PIPER_CORE_BRIDGE_DEMO",
    "PIPER_CORE_BRIDGE_MOCK",
    "PIPER_CORE_BRIDGE_REAL",
    "PIPER_CORE_BRIDGE_BG",
]

@dataclass(frozen=True)
class CoreFlags:
    runtime: bool
    transitions: bool
    forward_input: bool
    sandbox: bool
    demo_asr: bool
    demo_asr2: bool
    demo_speak: bool
    demo_stop: bool
    bridge_demo: bool
    bridge_mock: bool
    bridge_real: bool
    bridge_bg: bool

    def active_keys(self) -> list[str]:
        keys = []
        values = [
            self.runtime, self.transitions, self.forward_input, self.sandbox,
            self.demo_asr, self.demo_asr2, self.demo_speak, self.demo_stop,
            self.bridge_demo, self.bridge_mock, self.bridge_real, self.bridge_bg,
        ]
        for k, v in zip(_FLAG_KEYS, values):
            if v:
                keys.append(k)
        return keys

def _is_on(name: str) -> bool:
    return os.getenv(name) == "1"

def read() -> CoreFlags:
    """Read current environment into a structured snapshot."""
    return CoreFlags(
        runtime=_is_on("PIPER_CORE_RUNTIME"),
        transitions=_is_on("PIPER_CORE_TRANSITIONS"),
        forward_input=_is_on("PIPER_CORE_FORWARD_INPUT"),
        sandbox=_is_on("PIPER_CORE_SANDBOX"),
        demo_asr=_is_on("PIPER_CORE_DEMO_ASR"),
        demo_asr2=_is_on("PIPER_CORE_DEMO_ASR2"),
        demo_speak=_is_on("PIPER_CORE_DEMO_SPEAK"),
        demo_stop=_is_on("PIPER_CORE_DEMO_STOP"),
        bridge_demo=_is_on("PIPER_CORE_BRIDGE_DEMO"),
        bridge_mock=_is_on("PIPER_CORE_BRIDGE_MOCK"),
        bridge_real=_is_on("PIPER_CORE_BRIDGE_REAL"),
        bridge_bg=_is_on("PIPER_CORE_BRIDGE_BG"),
    )

def summarize() -> str:
    """Return concise comma-separated list of active flag names (or empty)."""
    flags = read()
    active = flags.active_keys()
    return ",".join(active)

