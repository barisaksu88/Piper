# Extracted from C:\Piper\scripts\core\poll_helpers.py — kept for reference
# Do NOT import from here at runtime.


# --- FunctionDef poll_asr_once
def poll_asr_once(
    bridge: Any,
    app: Any,
    publish: Any,
    EventType: Any,
    *,
    ticks: int = 3,
    timeout: float = 0.05,
) -> Tuple[str, int]:
    """
    Polls bridge.asr.listen() up to `ticks` times, forwards any tokens to Core.

    Parameters
    ----------
    bridge : CoreBridge-like object with .asr (having listen(timeout)->str|None)
    app    : CoreApp instance with .queue and .tick()
    publish: function(queue, EventType, payload)
    EventType: Core EventType enum (expects .ASRResult)
    ticks  : number of listen attempts (non-negative)
    timeout: per-listen timeout seconds (float)

    Returns
    -------
    (last_state_name, tokens_forwarded)
    """
    count = 0
    last_state = getattr(app.state, "name", str(app.state))
    asr = getattr(bridge, "asr", None)

    if ticks <= 0 or asr is None:
        return last_state, 0

    for _ in range(ticks):
        token: Optional[str]
        try:
            token = asr.listen(timeout=timeout)  # may be None, "", or text
        except Exception:
            token = None

        if token is None:
            continue

        publish(app.queue, EventType.ASRResult, {"text": token})
        new_state = app.tick()
        last_state = getattr(new_state, "name", str(new_state))
        count += 1

        # Optional early exit: treat "" as EOU; stop after forwarding it
        if token == "":
            break

    return last_state, count
