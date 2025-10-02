# CONTRACT — Persona Binding (Single Source)
# - Both generate() and stream_generate():
#     persona = persona_source.load_persona()
#     text = llm_style.apply_style(text, persona)
# - No UI imports. Catch style errors and continue with raw text.

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
import re

# --- Debug helpers (opt-in via PIPER_LLM_DEBUG=1) ----------------------------
_DEF_TRUE = {"1","true","yes","on","enabled"}

def _debug_on() -> bool:
    try:
        return os.getenv("PIPER_LLM_DEBUG", "0").strip().lower() in _DEF_TRUE
    except Exception:
        return False

def _dbg(msg: str) -> None:
    if _debug_on():
        try:
            print(f"[LLMDBG] {msg}")
        except Exception:
            pass

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
            from scripts.services.providers import llamacpp as _ll
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
        from scripts.services.providers import llamacpp as _ll
        handle = _ll.start_stream(text)
        return handle, _ll.iter_chunks, _ll.stop
    except Exception:
        return None, None, None

# PS1: single-source persona (services.persona_source.load_persona)

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

# ---------------------------- Memory recall (M02) -----------------------------

import json  # make sure this is imported near the top

# Heuristic gating for recall inclusion
_ANAPHORA_HINTS = (" it ", " that ", " this ", " those ", " the above ", " the latter ")
# No hardcoded keywords — configure via regex envs
try:
    _RECALL_INCLUDE_COMPILED = re.compile(os.getenv("PIPER_MEM_RECALL_INCLUDE_RE", "").strip(), re.IGNORECASE) if os.getenv("PIPER_MEM_RECALL_INCLUDE_RE") else None
except Exception:
    _RECALL_INCLUDE_COMPILED = None
try:
    _RECALL_EXCLUDE_COMPILED = re.compile(os.getenv("PIPER_MEM_RECALL_EXCLUDE_RE", "").strip(), re.IGNORECASE) if os.getenv("PIPER_MEM_RECALL_EXCLUDE_RE") else None
except Exception:
    _RECALL_EXCLUDE_COMPILED = None

def _should_include_recall(current_text: str) -> bool:
    """Decide whether to include recall without hardcoded phrases.
    Env knobs:
      - PIPER_MEM_RECALL_INCLUDE_RE: if matches current message → include
      - PIPER_MEM_RECALL_EXCLUDE_RE: if matches current message → exclude
      - PIPER_MEM_RECALL_SHORTLEN: anaphora threshold (default 120 chars)
    Fallback: for short messages, light anaphora heuristic (it/that/this...)
    """
    try:
        s = " " + (current_text or "") + " "
        slow = s.lower()
        stripped = slow.strip()
        if not stripped:
            return False
        if _RECALL_EXCLUDE_COMPILED and _RECALL_EXCLUDE_COMPILED.search(s):
            return False
        if _RECALL_INCLUDE_COMPILED and _RECALL_INCLUDE_COMPILED.search(s):
            return True
        try:
            max_len = int(os.getenv("PIPER_MEM_RECALL_SHORTLEN", "120"))
        except Exception:
            max_len = 120
        if len(stripped) <= max_len:
            for pr in _ANAPHORA_HINTS:
                if pr in slow:
                    return True
        return False
    except Exception:
        return False
def _maybe_build_recall_preamble() -> str:
    """Build a minimal preamble from recent episodic messages when enabled.
    Controlled by PIPER_MEM_RECALL=1 and PIPER_MEM_EPISODES path.
    Reads only the tail of the JSONL to stay fast; returns '' on any issue.

    Tolerant parser: supports many JSON shapes (role/speaker/who/author/name and
    text/content/message/body/delta/snippet/data) and also our compact
    `summary` lines (e.g., "user: hi | assistant: hello"). Falls back to raw
    prefixes "User:"/"Assistant:" if needed.
    """
    try:
        if os.getenv("PIPER_MEM_RECALL", "0").strip() != "1":
            return ""
        path = os.getenv("PIPER_MEM_EPISODES", "").strip()
        if not path or not os.path.exists(path):
            return ""
        # Tail read (up to 64KB) to improve chances of catching recent lines
        max_bytes = 65536
        with open(path, "rb") as fh:
            try:
                fh.seek(-max_bytes, os.SEEK_END)
            except OSError:
                fh.seek(0)
            data = fh.read().decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        if not lines:
            return ""

        role_keys = ("role", "speaker", "who", "author", "name", "source")
        text_keys = ("text", "content", "message", "body", "delta", "snippet", "data")

        msgs = []
        for ln in reversed(lines):  # newest first
            # 1) Try JSON first
            obj = None
            try:
                obj = json.loads(ln)
            except Exception:
                obj = None

            if isinstance(obj, dict):
                # 1a) Direct role/text keys
                role_lbl = None
                text = None
                for rk in role_keys:
                    if rk in obj and obj[rk]:
                        role_lbl = str(obj[rk]).strip()
                        break
                if not role_lbl and isinstance(obj.get("role"), dict):
                    role_lbl = str(obj["role"].get("type", "")).strip()
                for tk in text_keys:
                    if tk in obj and obj[tk]:
                        text = obj[tk]
                        break
                if text is None and isinstance(obj.get("choices"), list):
                    try:
                        deltas = [c.get("delta", {}).get("content", "") for c in obj["choices"]]
                        text = "".join(deltas).strip()
                    except Exception:
                        pass
                if isinstance(text, (list, tuple)):
                    text = " ".join(str(x) for x in text if x)
                if isinstance(text, dict):
                    text = text.get("text") or text.get("content") or ""
                if text is not None:
                    text = str(text).strip()
                if role_lbl or text:
                    rlow = (role_lbl or "").lower()
                    role_out = "User" if "user" in rlow else ("Assistant" if "assist" in rlow else (role_lbl or "Assistant").title())
                    if text:
                        if len(text) > 200:
                            text = text[:200].rstrip() + "…"
                        msgs.append((role_out, text))
                        if len(msgs) >= 4:
                            break
                    # If we already extracted a direct message, continue to next line
                    continue
                # 1b) Summary string (our current logger format)
                summary = obj.get("summary")
                if isinstance(summary, str) and summary.strip():
                    parts = [p.strip() for p in summary.split("|") if p.strip()]
                    for part in reversed(parts):  # newest segment first
                        low = part.lower()
                        if low.startswith("user:"):
                            role_out = "User"
                            text = part.split(":", 1)[1].strip()
                        elif low.startswith("assistant:"):
                            role_out = "Assistant"
                            text = part.split(":", 1)[1].strip()
                        else:
                            continue
                        if not text:
                            continue
                        if len(text) > 200:
                            text = text[:200].rstrip() + "…"
                        msgs.append((role_out, text))
                        if len(msgs) >= 4:
                            break
                    if len(msgs) >= 4:
                        break
                    continue
            # 2) Fallback: raw line with prefix
            low = ln.lower()
            role_lbl = None
            text = None
            if low.startswith("user:"):
                role_lbl, text = "User", ln.split(":", 1)[1].strip()
            elif low.startswith("assistant:"):
                role_lbl, text = "Assistant", ln.split(":", 1)[1].strip()
            if text:
                if len(text) > 200:
                    text = text[:200].rstrip() + "…"
                msgs.append((role_lbl or "Assistant", text))
                if len(msgs) >= 4:
                    break

        if not msgs:
            return ""
        # Keep only User lines so we don't re-answer prior assistant text
        msgs = [m for m in msgs if m[0] == "User"]
        if not msgs:
            return ""
        out = ["[recall]", "Context only — do not answer these lines. Newest first:"]
        out.extend(f"{r}: {t}" for r, t in msgs)
        out.append("[/recall]")
        return "\n".join(out)  # fixed join
    except Exception as e:
        print(f"[ERR] recall preamble failed: {e}")
        return ""

def _extract_facts_from_recall(recall: str) -> str:
    """Extract tiny, high-signal facts from recall (newest-first).
    Currently: user's callsign from phrases like 'my callsign is X' or 'callsign: X'.
    Returns 'Facts: ...' or '' if none.
    """
    try:
        if not isinstance(recall, str) or not recall.strip():
            return ""
        user_lines: list[str] = []
        for ln in recall.splitlines():
            s = ln.strip()
            if s.lower().startswith("user:"):
                user_lines.append(s.split(":", 1)[1].strip())
                if len(user_lines) >= 3:
                    break
        for txt in user_lines:
            low = txt.lower()
            # my callsign is X
            key = "my callsign is"
            if key in low:
                tail = txt[low.find(key) + len(key):].strip()
                cand = tail.split()[0] if tail else ""
                cand = cand.strip('\"\'.,;:!()[]{}')
                if cand:
                    return f"Facts: The user's callsign is {cand}."
            # callsign: X
            key2 = "callsign:"
            if key2 in low:
                tail = txt[low.find(key2) + len(key2):].strip()
                cand = tail.split()[0] if tail else ""
                cand = cand.strip('\"\'.,;:!()[]{}')
                if cand:
                    return f"Facts: The user's callsign is {cand}."
            # call sign: X
            key3 = "call sign:"
            if key3 in low:
                tail = txt[low.find(key3) + len(key3):].strip()
                cand = tail.split()[0] if tail else ""
                cand = cand.strip('\"\'.,;:!()[]{}')
                if cand:
                    return f"Facts: The user's callsign is {cand}."
        return ""
    except Exception:
        return ""

# ---------------------------- persona primer (PS1) ----------------------------

def _build_persona_primer(persona: dict | None) -> str:
    """Compose a compact, model-friendly primer from PS1 persona.
    Pure transform of the provided dict; no I/O, no env.
    Uses the first non-empty paragraph of background (clipped to 500 chars).
    """
    try:
        if not isinstance(persona, dict):
            return ""
        traits = persona.get("traits", {}) or {}
        bg = (persona.get("background") or "")

        def _pct(name: str) -> float:
            try:
                return float(traits.get(name, 0.0) or 0.0)
            except Exception:
                return 0.0

        parts: list[str] = ["[persona]"]
        prof = _pct("professionalism"); warm = _pct("warmth"); brev = _pct("brevity"); direct = _pct("directness")
        humor = _pct("humor"); sarcasm = _pct("sarcasm")

        # Tone & guidance from sliders
        if prof >= 0.6:
            parts.append("Tone: professional, clear.")
        elif prof <= 0.3:
            parts.append("Tone: casual, friendly.")
        if direct >= 0.6:
            parts.append("Guideline: lead with the conclusion.")
        if warm >= 0.6:
            parts.append("Guideline: be warm without fluff.")
        if brev >= 0.8:
            parts.append("Length: concise.")
        elif brev <= 0.3:
            parts.append("Length: may elaborate when needed.")
        if humor >= 0.5:
            parts.append("Guideline: light humor when appropriate.")
        if sarcasm >= 0.5:
            parts.append("Guideline: dry sarcasm sparingly.")
        # Make recall authoritative
        parts.append("Non-negotiables: Only use [recall] when the user's current message clearly refers to prior turns (e.g., 'as I said', 'what's my callsign', 'continue'). Otherwise ignore [recall] and answer the current message.")

        # Background (first non-empty paragraph)
        if isinstance(bg, str):
            paras = [p.strip() for p in bg.split("\n\n") if p.strip()]
            if paras:
                para = paras[0]
                clip = (para[:500] + "…") if len(para) > 500 else para
                parts.append(f"Background: {clip}")

        parts.append("[/persona]")
        return "\n".join(parts)
    except Exception:
        return ""


def _compose_with_persona(text: str, persona: dict | None) -> str:
    primer = _build_persona_primer(persona)
    return f"{primer}\n\n{text}" if primer else text

# ---------------------------- single-shot API --------------------------------

def generate(text: str, persona: Optional[str] = None) -> str:
    provider_name = os.getenv("PIPER_LLM_PROVIDER", "echo").strip().lower()
    timeout_ms_str = os.getenv("PIPER_LLM_TIMEOUT_MS", "2000").strip()
    try:
        timeout_ms = int(timeout_ms_str)
    except ValueError:
        timeout_ms = 2000

    provider = _resolve_provider(provider_name)

    # PS1: Single-source persona
    try:
        from scripts.services import persona_source as _ps
        persona_payload = _ps.load_persona()
    except Exception as e:
        print(f"[ERR] persona load failed (PS1): {e}")
        persona_payload = {"background": "", "traits": {}}

    # M02: recall inject (single-shot)
    mode = os.getenv("PIPER_MEM_RECALL_MODE", "auto").strip().lower()
    include_recall = (mode == "always") or (mode != "off" and _should_include_recall(text))
    recall = _maybe_build_recall_preamble() if include_recall else ""
    ep_path = os.getenv("PIPER_MEM_EPISODES", "").strip()
    _dbg(f"include_recall={include_recall} recall_len={len(recall)} episodes={(ep_path or 'unset')} exists={os.path.exists(ep_path) if ep_path else False}")
    facts = _extract_facts_from_recall(recall) if recall else ""
    injected_text = text + (f"\n\n{facts}" if facts else "") + ("\n\n" + recall if recall else "")
    # PS1: compose with persona primer for the model itself
    prompt = _compose_with_persona(injected_text, persona_payload)
    _dbg(f"persona_bg_len={len(persona_payload.get('background',''))} tone_hints={'yes' if persona_payload.get('traits') else 'no'}")

    def _call() -> str:
        return provider(prompt, None)

    with _f.ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm_call") as ex:
        fut = ex.submit(_call)
        try:
            raw = fut.result(timeout=timeout_ms / 1000.0)
        except _f.TimeoutError:
            print(f"[ERR] LLM provider '{provider_name}' timeout after {timeout_ms} ms")
            raw = _prov_echo(prompt, None)
        except Exception as e:
            print(f"[ERR] LLM provider '{provider_name}' failed: {e}")
            raw = _prov_echo(prompt, None)

    # PS1 style hook; never break
    try:
        from scripts.services import llm_style as _style
        p = dict(persona_payload) if isinstance(persona_payload, dict) else {"background": "", "traits": {}}
        p["_first_chunk"] = True
        return _style.apply_style(raw, persona=p)
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

    # PS1: Single-source persona
    try:
        from scripts.services import persona_source as _ps
        persona_payload = _ps.load_persona()
    except Exception as e:
        print(f"[ERR] persona load failed (PS1): {e}")
        persona_payload = {"background": "", "traits": {}}

    # M02: recall inject (streaming)
    mode = os.getenv("PIPER_MEM_RECALL_MODE", "auto").strip().lower()
    include_recall = (mode == "always") or (mode != "off" and _should_include_recall(user_text))
    recall = _maybe_build_recall_preamble() if include_recall else ""
    ep_path = os.getenv("PIPER_MEM_EPISODES", "").strip()
    _dbg(f"include_recall={include_recall} recall_len={len(recall)} episodes={(ep_path or 'unset')} exists={os.path.exists(ep_path) if ep_path else False} (stream)")
    facts = _extract_facts_from_recall(recall) if recall else ""
    injected_text = user_text + (f"\n\n{facts}" if facts else "") + ("\n\n" + recall if recall else "")
    # PS1: compose with persona primer for the model itself
    prompt = _compose_with_persona(injected_text, persona_payload)

    q: _q.Queue[Optional[str]] = _q.Queue()

    def _producer() -> None:
        try:
            # If llamacpp provider supports native streaming, use it so cancel can be forceful
            if provider_name == "llamacpp" and cancel_token is not None:
                handle, iter_fn, stop_fn = _llamacpp_stream_start_if_available(prompt)
                if handle and iter_fn:
                    cancel_token.attach_provider(handle, stop_fn)
                    for chunk in iter_fn(handle):
                        if cancel_token.is_set():
                            break
                        q.put(chunk)
                    q.put(None)
                    return
            # Fallback path: single-shot then word-split (cooperative cancel)
            out = provider(prompt, None)
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

    # Sliding deadline: reset after each received chunk
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    emitted_any = False

    while True:
        # Fast exit on cancel
        if cancel_token is not None and cancel_token.is_set():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            # Timeout fallback (styled once)
            fallback = _prov_echo(prompt, None)
            try:
                from scripts.services import llm_style as _style
                p = dict(persona_payload) if isinstance(persona_payload, dict) else {"background": "", "traits": {}}
                p["_first_chunk"] = True
                fallback = _style.apply_style(fallback, persona=p)
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
        # Reset the timeout on any progress (sliding timeout)
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        # Style each chunk; never break stream
        styled = item
        try:
            from scripts.services import llm_style as _style
            p = dict(persona_payload) if isinstance(persona_payload, dict) else {"background": "", "traits": {}}
            p["_first_chunk"] = not emitted_any
            styled = _style.apply_style(item, persona=p)
        except Exception as e:
            if not emitted_any:
                print(f"[ERR] style hook failed (first chunk): {e}")
        emitted_any = True
        yield styled