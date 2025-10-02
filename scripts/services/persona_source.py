# CONTRACT — Single Persona Source
# - load_persona() -> dict { background:str, traits:dict[str,float|str] }
# - Reads exactly two files:
#     config/persona_background.md (UTF-8)
#     config/persona_traits.ini    ([traits] 0–100%, honorific text)
# - Normalizes numeric traits to 0.0–1.0.
# - Ignores env/YAML except as emergency read-only fallback (PS1 disables them).
# - No UI imports, no network, no subprocess.

from __future__ import annotations
import os
from typing import Dict, Any, Tuple
import configparser

# Whitelisted numeric traits for PS1
_NUMERIC_TRAITS = (
    "sarcasm",
    "professionalism",
    "warmth",
    "brevity",
    "directness",
    "humor",
)

# One-time log guards
_LOGGED_LEGACY_YAML_IGNORED = False
_LOGGED_LEGACY_ENV_USED = False


def _read_background(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _read_traits_ini(path: str) -> Tuple[Dict[str, float | str], bool]:
    """Return (traits, ok). If file missing/bad, traits are defaults and ok=False."""
    traits: Dict[str, float | str] = {t: 0.0 for t in _NUMERIC_TRAITS}
    traits["honorific"] = ""
    parser = configparser.ConfigParser()
    try:
        if not os.path.exists(path):
            return traits, False
        with open(path, "r", encoding="utf-8") as f:
            parser.read_file(f)
        if not parser.has_section("traits"):
            return traits, False
        sec = parser["traits"]
        # numeric sliders 0–100 → 0.0–1.0
        for key in _NUMERIC_TRAITS:
            if key in sec:
                try:
                    pct = float(str(sec.get(key, "0")).strip())
                except Exception:
                    pct = 0.0
                if pct < 0.0:
                    pct = 0.0
                if pct > 100.0:
                    pct = 100.0
                traits[key] = round(pct / 100.0, 4)
        # honorific (free text)
        if "honorific" in sec:
            traits["honorific"] = str(sec.get("honorific", "")).strip()
        return traits, True
    except Exception:
        return traits, False


def _legacy_env_overrides(traits: Dict[str, float | str]) -> Dict[str, float | str]:
    """Emergency-only env mapping when PIPER_PERSONA_LEGACY=1.
    Maps old envs onto new trait space; conservative defaults.
    """
    global _LOGGED_LEGACY_ENV_USED
    try:
        if os.getenv("PIPER_PERSONA_LEGACY", "0").strip().lower() not in {"1", "true", "yes", "on"}:
            return traits
        if not _LOGGED_LEGACY_ENV_USED:
            print("[WARN] legacy persona env in effect (PS1 emergency mode)")
            _LOGGED_LEGACY_ENV_USED = True
        out = dict(traits)
        # Sarcasm 0/1 → 0.0/1.0
        s_raw = os.getenv("PIPER_PERSONA_SARCASM")
        if s_raw is not None:
            s = str(s_raw).strip().lower()
            out["sarcasm"] = 1.0 if s in {"1", "true", "yes", "y", "on"} else 0.0
        # Brevity short|normal|long → numeric (higher = more brief)
        b_raw = os.getenv("PIPER_PERSONA_BREVITY")
        if b_raw:
            b = str(b_raw).strip().lower()
            mapping = {"short": 0.85, "normal": 0.5, "long": 0.2}
            if b in mapping:
                out["brevity"] = mapping[b]
        # Honorific
        h_raw = os.getenv("PIPER_PERSONA_HONORIFIC")
        if h_raw is not None:
            out["honorific"] = str(h_raw).strip()
        return out
    except Exception:
        return traits


def load_persona() -> Dict[str, Any]:
    """Load persona from the single source (background.md + traits.ini).

    Returns: { 'background': str, 'traits': {trait: float|str} }
    On any failure, returns neutral persona: empty background, 0.0 sliders, empty honorific.
    """
    bg_path = os.path.join("config", "persona_background.md")
    ini_path = os.path.join("config", "persona_traits.ini")

    background = _read_background(bg_path)
    traits, ok = _read_traits_ini(ini_path)

    # PS1.3 legacy lockdown — ignore legacy persona.yml and envs by default
    # Log once if persona.yml exists but ignored
    try:
        yml_path = os.path.join("config", "persona.yml")
        global _LOGGED_LEGACY_YAML_IGNORED
        if os.path.exists(yml_path) and not _LOGGED_LEGACY_YAML_IGNORED and os.getenv("PIPER_PERSONA_LEGACY", "0").strip() not in {"1", "true", "yes", "on"}:
            print("[INFO] legacy persona file ignored (PS1): config/persona.yml")
            _LOGGED_LEGACY_YAML_IGNORED = True
    except Exception:
        pass

    # Emergency env overrides only when explicitly opted-in
    traits = _legacy_env_overrides(traits)

    return {"background": background, "traits": traits}
