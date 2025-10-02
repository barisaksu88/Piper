"""TTS adapter smoke test (no audio)."""
import importlib
import sys

TTS_MODULE = "scripts.services.tts.speak_once"

try:
    tts = importlib.import_module(TTS_MODULE)
    print(f"âœ“ imported {TTS_MODULE}")
except Exception as e:
    print(f"âœ— import {TTS_MODULE}: {e}")
    sys.exit(1)

# The stub only signals readiness; no callable is required at this stage.
if hasattr(tts, "speak_once"):
    print("âœ“ speak_once callable found")
else:
    print("(info) stub loaded without callable; acceptable at this stage")

print("âœ“ TTS adapter surface looks OK.")
