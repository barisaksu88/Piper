# Extracted from C:\Piper\scripts\services\persona_adapter.py — kept for reference
# Do NOT import from here at runtime.


# ---

def reload_persona():
    """Hot-reload personality module; supports both import paths."""
    global personality, _last_loaded_at, _last_load_error
    try:
        importlib.invalidate_caches()
        mod = None
        # Reload whichever is present, else import fresh
        if "personality" in sys.modules:
            mod = importlib.reload(sys.modules["personality"])
        elif "scripts.personality" in sys.modules:
            mod = importlib.reload(sys.modules["scripts.personality"])
        else:
            try:
                mod = importlib.import_module("personality")
            except Exception:
                mod = importlib.import_module("scripts.personality")
        personality = mod
        _rehydrate_from_persona(mod)
        _last_loaded_at, _last_load_error = time.time(), None
        return True, f"Reloaded personality at {time.strftime('%H:%M:%S')}."
    except Exception as e:
        _last_load_error = str(e)
        return False, f"Reload failed: {e!s}"

# -------- Runtime setters/getters --------

# ---
def get_runtime_sarcasm() -> bool | None:    return _runtime["sarcasm"]

# ---

def get_runtime_max_len() -> int | None: return _runtime["max_len"]

# -------- Tones (defaults + overrides) --------

# ---

def list_tones() -> list[str]:
    return sorted(_get_tone_presets().keys())

# ---

def show_tone(tone: str) -> dict:
    return dict(_get_tone_presets().get(tone, {}))

# ---

def set_tone_field(tone: str, field: str, value: str) -> None:
    if tone not in _get_tone_presets():
        _runtime["tones"][tone] = dict(_get_base_tone_presets().get("neutral", {"prefix": "", "suffix": "", "end": "."}))
    if tone not in _runtime["tones"]:
        _runtime["tones"][tone] = {}
    if field in ("prefix", "suffix", "end"):
        _runtime["tones"][tone][field] = value

# ---

def clear_tone(tone: str) -> None:
    if tone in _runtime["tones"]:
        del _runtime["tones"][tone]

# -------- Export/Import --------

# ---
def export_runtime_dict() -> dict:
    return copy.deepcopy(_runtime)

# ---

def import_runtime_dict(state: dict) -> None:
    if not isinstance(state, dict): return
    set_runtime_sarcasm(state.get("sarcasm") if state.get("sarcasm") in (True, False, None) else None)
    set_runtime_max_len(state.get("max_len") if isinstance(state.get("max_len"), int) or state.get("max_len") is None else None)
    tones = state.get("tones", {})
    if isinstance(tones, dict):
        _runtime["tones"] = {}
        for tone, cfg in tones.items():
            if isinstance(cfg, dict):
                filtered = {k: v for k, v in cfg.items() if k in ("prefix", "suffix", "end") and isinstance(v, str)}
                if filtered: _runtime["tones"][tone] = filtered

# -------- Styling core --------

# ---
def get_greeting() -> str:
    return str(_get_attr("GREETING", _DEFAULT_GREETING))
