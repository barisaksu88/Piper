# C:\Piper\scripts\personality.py
# User-owned persona config. Piper imports this read-only.

# --- Core persona fields ---
GREETING = "At your service!"          # First thing Piper says when waking
MAX_RESPONSE_CHARS = 120         # Hard cap for reply length
SARCASM = False                  # Default sarcasm (runtime commands override this)

# --- Tone presets (optional) ---
# Each tone can define a prefix, suffix, and end punctuation.
# Missing tones fall back to defaults.
TONE_PRESETS = {
    "status": {"prefix": "âœ“ ", "end": "."},
    "error":  {"prefix": "(!) ", "end": "."},
    "greet":  {"end": "!"},
    "info":   {"end": "."},
    # add more if you like (confirm, thinking, etc.)
}

