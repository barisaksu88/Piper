# Extracted from C:\Piper\scripts\entries\app_cli_entry.py — kept for reference
# Do NOT import from here at runtime.


# ---
def _readline() -> str:
    sys.stdout.write(current_prompt())  # was "> "
    sys.stdout.flush()
    s = sys.stdin.readline()
    return "" if not s else s.rstrip("\r\n")
