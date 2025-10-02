# CONTRACT — Streaming API
# Provides stream_generate(user_text, persona=None) -> Iterator[str].
# Responsibilities:
#  - Wrap chosen provider's streaming output.
#  - Enforce timeout/fallback (LLM04 rules) on chunk or total.
#  - Yield text increments safely.
# Forbidden:
#  - UI code, logging UI elements.
#  - Persona shaping (delegated to services.llm_style).

# CONTRACT — LLM Client (Registry + Robustness)
# Single registry for providers; the UI/entries must call only this client.
# Responsibilities:
#  - Select provider via env PIPER_LLM_PROVIDER (echo, stub, llamacpp, ...).
#  - Enforce LLM04 robustness: timeout + safe fallback, never block the GUI.
#  - Apply style via services.llm_style (persona shaping later).
# Forbidden:
#  - UI imports
#  - GUI mutations, logging UIs

# CONTRACT — Cancelable Streaming (I01)
# - Provide start/stop lifecycle for streams.
# - Public: stream_generate(..., cancel_token) -> Iterator[str]
# - Cancel must be cooperative (checked between chunks) and forceful at provider boundary.
# - Never block the UI on stop(); always resolve within timeout.

#  - UI imports
#  - GUI mutations, logging UIs

import os
import time
import threading as _t
import queue as _q
import concurrent.futures as _f
from typing import Callable, Dict, Iterator, Optional, Any

ProviderFn = Callable[[str, Optional[str]], str]

# Built-in trivial providers
def _prov_echo(text: str, persona: Optional[str] = None) -> str:
    return text

def _prov_stub(text: str, persona: Optional[str] = None) -> str:
    return f"stub: {text}"

_REGISTRY: Dict[str, ProviderFn | None] = {
    "echo": _prov_echo,
    "stub": _prov_stub,
    "llamacpp": None,
}

def _resolve_provider(name: str) -> ProviderFn:
    try:
        if name == "llamacpp":
            from services.providers import llamacpp as _ll
            return _ll.generate  # type: ignore[return-value]
        prov = _REGISTRY.get(name)
        if prov is None:
            raise KeyError(name)
        return prov
    except Exception as e:
        print(f"[ERR] provider resolve failed for '{name}': {e} -> using echo")
        return _prov_echo

# Provider stream wiring (I01.2) — optional fast path for cancel

def _llamacpp_stream_start_if_available(text: str):
    """Try to start llama.cpp in streaming mode if provider present.
    Returns (handle, iter_fn, stop_fn) or (None, None, None) on failure.
    """
    try:
        from services.providers import llamacpp as _ll
        handle = _ll.start_stream(text)
        return handle, _ll.iter_chunks, _ll.stop
    except Exception:
        return None, None, None

# --------------- LLM08: style/env persona plumbing --------------------------

def _resolve_persona_env() -> Optional[dict]:
    """Build a small persona dict from env, or None if unset."""
    tone = os.getenv("PIPER_PERSONA_TONE", "").strip()
    sarcasm = os.getenv("PIPER_PERSONA_SARCASM", "").strip()
    persona: dict[str, object] = {}
    if tone:
        persona["tone"] = tone
    if sarcasm:
        s = sarcasm.lower()
        persona["sarcasm"] = (s in ("1", "true", "yes", "y"))
    return persona or None

# ---------------------------- Cancel token (I01) ------------------------------

class CancelToken:
    """Lightweight, thread-safe cancel token for streaming.

    - Cooperative checks between chunks via is_set().
    - Can carry an optional provider handle and stop callable for forceful stops.
    """
    __slots__ = ("_ev", "created_ms", "_provider_handle", "_provider_stop")

    def __init__(self) -> None:
        self._ev = _t.Event()
        self.created_ms = int(time.time() * 1000)
        self._provider_handle: Any = None
        self._provider_stop: Optional[Callable[[Any], None]] = None

    def is_set(self) -> bool:
        return self._ev.is_set()

    def set(self) -> None:
        self._ev.set()

    # Provider boundary wiring (used by I01.2 for llamacpp)
    def attach_provider(self, handle: Any, stop_fn: Optional[Callable[[Any], None]] = None) -> None:
        self._provider_handle = handle
        self._provider_stop = stop_fn


def stop(token: Optional[CancelToken], *, timeout_ms: int = 500) -> None:
    """Signal cancellation and try forceful provider stop (non-blocking for UI).

    - Flips the token flag immediately.
    - Best-effort call into provider stop() in a background thread.
    - Returns quickly; internal wait bounded by timeout_ms.
    """
    if token is None:
        return
    token.set()
    handle = getattr(token, "_provider_handle", None)
    stop_fn = getattr(token, "_provider_stop", None)
    if handle is None or stop_fn is None:
        return
    # Best-effort, short-lived thread
    def _do_stop():
        try:
            stop_fn(handle)
        except Exception as e:
            print(f"[ERR] provider stop failed: {e}")
    th = _t.Thread(target=_do_stop, name="llm_provider_stop", daemon=True)
    th.start()
    th.join(timeout_ms / 1000.0)

# ---------------------------- single-shot API --------------------------------

def generate(text: str, persona: Optional[str] = None) -> str:
    provider_name = os.getenv("PIPER_LLM_PROVIDER", "echo").strip().lower()
    timeout_ms_str = os.getenv("PIPER_LLM_TIMEOUT_MS", "2000").strip()
    try:
        timeout_ms = int(timeout_ms_str)
    except ValueError:
        timeout_ms = 2000

    provider = _resolve_provider(provider_name)

    # Merge persona with env knobs; env wins if present
    env_persona = _resolve_persona_env()
    persona_payload = env_persona if env_persona is not None else persona

    def _call() -> str:
        return provider(text, persona_payload)

    with _f.ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm_call") as ex:
        fut = ex.submit(_call)
        try:
            raw = fut.result(timeout=timeout_ms / 1000.0)
        except _f.TimeoutError:
            print(f"[ERR] LLM provider '{provider_name}' timeout after {timeout_ms} ms")
            raw = _prov_echo(text, persona_payload)
        except Exception as e:
            print(f"[ERR] LLM provider '{provider_name}' failed: {e}")
            raw = _prov_echo(text, persona_payload)

    # LLM08 style hook; never break
    try:
        from services import llm_style as _style
        return _style.apply_style(raw, persona=persona_payload)
    except Exception as e:
        print(f"[ERR] style hook failed (generate): {e}")
        return raw

# ----------------------------- streaming API ---------------------------------

def stream_generate(user_text: str, persona: Optional[str] = None, cancel_token: Optional[CancelToken] = None) -> Iterator[str]:
    provider_name = os.getenv("PIPER_LLM_PROVIDER", "echo").strip().lower()
    timeout_ms_str = os.getenv("PIPER_LLM_TIMEOUT_MS", "2000").strip()
    try:
        timeout_ms = int(timeout_ms_str)
    except ValueError:
        timeout_ms = 2000

    provider = _resolve_provider(provider_name)

    # Merge persona with env knobs; env wins if present
    env_persona = _resolve_persona_env()
    persona_payload = env_persona if env_persona is not None else persona

    q: _q.Queue[Optional[str]] = _q.Queue()

    def _producer() -> None:
        try:
            # If llamacpp provider supports native streaming, use it so cancel can be forceful
            if provider_name == "llamacpp" and cancel_token is not None:
                handle, iter_fn, stop_fn = _llamacpp_stream_start_if_available(user_text)
                if handle and iter_fn:
                    cancel_token.attach_provider(handle, stop_fn)
                    for chunk in iter_fn(handle):
                        if cancel_token.is_set():
                            break
                        q.put(chunk)
                    q.put(None)
                    return
            # Fallback path: single-shot then word-split (cooperative cancel)
            out = provider(user_text, persona_payload)
            for i, tok in enumerate(out.split()):
                if cancel_token is not None and cancel_token.is_set():
                    break
                q.put(tok if i == 0 else " " + tok)
        except Exception as e:
            print(f"[ERR] stream producer failed for '{provider_name}': {e}")
        finally:
            q.put(None)

    t = _t.Thread(target=_producer, name="llm_stream_producer", daemon=True)
    t.start()

    deadline = time.monotonic() + (timeout_ms / 1000.0)
    emitted_any = False

    while True:
        # Fast exit on cancel
        if cancel_token is not None and cancel_token.is_set():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            # Timeout fallback (styled once)
            fallback = _prov_echo(user_text, persona_payload)
            try:
                from services import llm_style as _style
                fallback = _style.apply_style(fallback, persona=persona_payload)
            except Exception as e:
                print(f"[ERR] style hook failed (timeout fallback): {e}")
            if not emitted_any:
                yield fallback
            else:
                yield "\n[LLM timeout - fallback applied]"
            return
        try:
            item = q.get(timeout=min(0.1, max(0.0, remaining)))
        except _q.Empty:
            continue
        if item is None:
            return
        if cancel_token is not None and cancel_token.is_set():
            return
        # Style each chunk; never break stream
        styled = item
        try:
            from services import llm_style as _style
            styled = _style.apply_style(item, persona=persona_payload)
        except Exception as e:
            if not emitted_any:
                print(f"[ERR] style hook failed (first chunk): {e}")
        emitted_any = True
        yield styled
