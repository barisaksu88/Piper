# Extracted from C:\Piper\scripts\core\logbus.py — kept for reference
# Do NOT import from here at runtime.


# ---

def event(say: SayFn, name: str, **kv: Any) -> None:
    """
    Emit a compact event line.
    Example: [EVENT] wake button=dev_tools
    """
    if kv:
        args = " ".join(f"{k}={v}" for k, v in kv.items())
        say(f"[EVENT] {name} {args}", "status")
    else:
        say(f"[EVENT] {name}", "status")

# ---

def info(say: SayFn, msg: str) -> None:
    """Plain informational line (routes to Logs)."""
    say(msg, "info")
