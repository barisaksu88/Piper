"""stream_filter.py
~~~~~~~~~~~~~~~~~~
Token-level filter that strips ``<think>…</think>`` preambles from LLM streams.

Qwen3.5 (thinking=0) may prepend a ``<think>…</think>`` block before every
response.  This module provides a single generator that consumes raw tokens from
the LLM and yields only the visible response tokens, discarding the thinking
preamble entirely.

Isolation goals
---------------
* **No I/O** — no logging, no queue access.  Callers are responsible for debug
  output so this function stays pure and unit-testable.
* **No imports beyond stdlib** — keeps the module self-contained.
* **Correct handling of the no-thinking case** — when the model omits the
  ``<think>`` block the filter must *not* buffer the entire response; it must
  detect immediately that no preamble is present and pass tokens through.
"""
from __future__ import annotations

from typing import Iterable, Iterator

_THINK_OPEN_PREFIX: str = "<think>"
_THINK_OPEN_PREFIX_LEN: int = len(_THINK_OPEN_PREFIX)  # 7
_THINK_CLOSE: str = "</think>"
_THINK_CLOSE_LEN: int = len(_THINK_CLOSE)              # 8
# After this many buffered characters without resolving, give up and treat
# the buffer as response text (safety valve for malformed think blocks).
_THINK_BUF_MAX: int = 4096


def stream_thinking_filter(tokens: Iterable[str]) -> Iterator[str]:
    """Yield response tokens from *tokens*, stripping any ``<think>…</think>`` preamble.

    Decision tree applied to the accumulated lookahead buffer on every token:

    1. Buffer is all-whitespace → keep buffering (whitespace-only preamble).
    2. First non-whitespace char is **not** ``<`` → no preamble; flush buffer
       and switch to pass-through immediately.
    3. Buffer starts with ``<`` but we have fewer than 7 non-whitespace chars →
       not enough data to identify the tag; keep buffering.
    4. Buffer has 7+ chars but does **not** start with ``<think>`` → it is a
       ``<…>`` response token (e.g. ``<tool_call>``); flush and pass through.
    5. Buffer starts with ``<think>`` → discard tokens until ``</think>`` is
       found, then yield any remainder after the closing tag.
    6. Buffer overflows ``_THINK_BUF_MAX`` without resolving → yield entire
       buffer as response text (safety flush).

    A trailing safety flush covers split-mode servers (where thinking tokens
    arrive on a separate SSE field and the ``content`` field never contains
    ``<think>``), as well as truncated streams.
    """
    buf: str = ""
    think_passed: bool = False

    for token in tokens:
        # Fast path: once preamble is gone, yield every token directly.
        if think_passed:
            yield token
            continue

        buf += token
        stripped = buf.lstrip()

        # 1. Nothing but whitespace yet.
        if not stripped:
            continue

        # 2. First visible char is not '<' — no thinking preamble present.
        if not stripped.startswith("<"):
            think_passed = True
            out = buf.lstrip("\n\r ")
            buf = ""
            if out:
                yield out
            continue

        # 3. Starts with '<' but too few chars to identify the tag.
        if len(stripped) < _THINK_OPEN_PREFIX_LEN:
            if len(buf) > _THINK_BUF_MAX:
                # Overflow safety — shouldn't happen for a 7-char decision.
                think_passed = True
                yield buf
                buf = ""
            continue

        # 4. Enough chars: not a <think> tag — regular response text.
        if not stripped.lower().startswith(_THINK_OPEN_PREFIX):
            think_passed = True
            out = buf.lstrip("\n\r ")
            buf = ""
            if out:
                yield out
            continue

        # 5. Confirmed <think> preamble — scan for closing tag.
        close_idx = buf.lower().find(_THINK_CLOSE)
        if close_idx != -1:
            think_passed = True
            remainder = buf[close_idx + _THINK_CLOSE_LEN:].lstrip("\n\r ")
            buf = ""
            if remainder:
                yield remainder
            continue

        # Still inside <think>…; keep discarding unless we overflow.
        if len(buf) > _THINK_BUF_MAX:
            # 6. Overflow — give up waiting for </think>.
            think_passed = True
            yield buf
            buf = ""

    # Trailing safety flush: handles split-mode servers (no preamble at all),
    # truncated <think> blocks, or any buffered remainder after the loop.
    if not think_passed and buf.strip():
        yield buf.strip()
