# CONTRACT — LLM Style Hook
# This module applies persona/tone shaping to raw model output.
# - Input: (text: str, persona: str|dict|None)
# - Output: str (possibly transformed)
# Env-driven knobs (safe, non-critical):
#   PIPER_PERSONA_TONE, PIPER_PERSONA_SARCASM, PIPER_LLM_STYLE (legacy: plain|pilot|snark)
# Forbidden:
# - Blocking calls or subprocesses
# - UI imports
# - State mutations

from __future__ import annotations
from typing import Any, Dict, Optional, Union
import os

PersonaT = Union[str, Dict[str, Any], None]

# -------------------------------
# Legacy style (LLM04) — kept for compatibility
# -------------------------------
_STYLE_ENV = "PIPER_LLM_STYLE"
_ALLOWED_STYLES = ("plain", "pilot", "snark")
_DEFAULT_STYLE = "plain"

def _style_plain(reply: str, persona: Any) -> str:
    return reply

def _style_pilot(reply: str, persona: Any) -> str:
    r = (reply or "").strip()
    if not r:
        return r
    if r.lower().startswith("roger"):
        return r
    return f"Roger — {r}"

def _style_snark(reply: str, persona: Any) -> str:
    r = reply or ""
    if r.endswith((".", "!", "?")):
        return r + " Sure."
    return r + " — sure."

_STYLES = {
    "plain": _style_plain,
    "pilot": _style_pilot,
    "snark": _style_snark,
}

def _current_legacy_style() -> str:
    v = (os.environ.get(_STYLE_ENV, _DEFAULT_STYLE) or "").strip().lower()
    return v if v in _ALLOWED_STYLES else _DEFAULT_STYLE

# -------------------------------
# LLM08 persona knobs (env + persona dict)
# -------------------------------

def _resolve_env_persona() -> Dict[str, Any]:
    tone = (os.getenv("PIPER_PERSONA_TONE", "") or "").strip().lower()
    sarcasm_raw = (os.getenv("PIPER_PERSONA_SARCASM", "") or "").strip().lower()
    sarcasm: Optional[bool] = None
    if sarcasm_raw:
        sarcasm = sarcasm_raw in {"1", "true", "yes", "y"}
    out: Dict[str, Any] = {}
    if tone:
        out["tone"] = tone
    if sarcasm is not None:
        out["sarcasm"] = sarcasm
    return out

def _coalesce_persona(persona: PersonaT) -> Dict[str, Any]:
    out = _resolve_env_persona()
    if isinstance(persona, dict):
        # persona from caller takes effect only where env not set
        for k, v in persona.items():
            out.setdefault(k, v)
    elif isinstance(persona, str):
        p = persona.strip().lower()
        if p in ("formal", "casual"):
            out.setdefault("tone", p)
        if "sarcasm=1" in p:
            out.setdefault("sarcasm", True)
    return out

# -------------------------------
# Public API
# -------------------------------

def apply_style(text: str, *, persona: PersonaT = None) -> str:
    """Return a styled version of *text*.

    Behavior in LLM08:
    - Legacy style (PIPER_LLM_STYLE) stays in effect first.
    - Persona dict/strings + env knobs (PIPER_PERSONA_TONE / _SARCASM) add light touches.
    - Must never raise; on any error, return the original text.
    """
    try:
        if text is None:
            return text
        # 1) Legacy style pass
        legacy_fn = _STYLES.get(_current_legacy_style(), _style_plain)
        out = legacy_fn(text, persona)
        # 2) Persona knobs (non-destructive, light-weight)
        p = _coalesce_persona(persona)
        # tone is a no-op placeholder for LLM08; reserved for LLM09 richer transforms
        sarcasm = bool(p.get("sarcasm", False))
        if sarcasm and any(c.isalnum() for c in out):
            out = f"{out} 😏"
        return out
    except Exception:
        return text
