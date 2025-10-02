"""Minimal import sanity test for Piper (with optional TTS detection)."""
import importlib
import pkgutil
import sys

REQUIRED = [
    "scripts.entries.app_cli_entry",
    "scripts.entries.app_gui_entry",
    "scripts.core.core_machine",
    "scripts.core.core_commands",
    "scripts.services.asr.vosk_adapter",
]

# We don't know which TTS adapter your baseline uses, so treat TTS as OPTIONAL.
# We'll attempt a few known names and also auto-discover any services.* module
# whose name starts with "tts".
OPTIONAL_SEEDS = [
    "scripts.services.tts_pyttsx3",
    "scripts.services.tts_pyttsx3_adapter",
    "scripts.services.tts_engine",
    "scripts.services.tts.speak_once",
]

# Discover candidates under scripts.services
try:
    import scripts.services as _svc
    prefix = _svc.__name__ + "."
    for modinfo in pkgutil.iter_modules(_svc.__path__, prefix):
        name = modinfo.name
        base = name.rsplit(".", 1)[-1]
        if base.startswith("tts") and name not in OPTIONAL_SEEDS:
            OPTIONAL_SEEDS.append(name)
except Exception:
    pass

errors = []

def try_import(name: str, required: bool) -> None:
    try:
        importlib.import_module(name)
        print(f"âœ“ {name}")
    except Exception as e:
        mark = "âœ—" if required else "â€“"  # dash for optional failure
        print(f"{mark} {name}: {e}")
        if required:
            errors.append((name, e))

for mod in REQUIRED:
    try_import(mod, required=True)

# Try optional TTS modules; don't fail the run if none load
loaded_any_tts = False
for mod in OPTIONAL_SEEDS:
    try:
        importlib.import_module(mod)
        print(f"âœ“ {mod}")
        loaded_any_tts = True
    except Exception as e:
        print(f"â€“ {mod}: {e}")

if not loaded_any_tts:
    print("(info) No TTS module imported; that's okay for this sanity check.")

if errors:
    sys.exit(1)
else:
    print("All REQUIRED imports loaded successfully.")

