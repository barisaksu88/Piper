"""stream_filter_smoke_test.py

Exercises every branch of stream_thinking_filter's six-case decision tree.
No server, no I/O, no external state — runs in isolation.

Cases covered
-------------
1. No preamble (first non-whitespace char is not '<')
   - Single-chunk response
   - Multi-token response: first token is a normal word
   - Leading whitespace/newlines before content (stripped from output)
   - Response starting with a non-<think> XML-like tag (e.g. <tool_call>)

2. Complete <think>...</think> block
   - Entire block in a single token
   - Block split across many tokens
   - Content immediately after closing tag (no gap)
   - Newlines between </think> and content (stripped from output)
   - Case-insensitive: <THINK>...</THINK>
   - Nested content inside think block does not leak

3. Split-mode server (thinking never arrives in content stream)
   - Stream contains only plain response tokens, never sees '<think>'
   - Must pass through without buffering

4. Partial tag at buffer boundary
   - '<think>' arrives one character at a time — no premature flush
   - '<' arrives alone, then non-think characters — flushes correctly (case 4)
   - '</think>' closing tag split across multiple tokens

5. Empty / whitespace-only stream
   - Empty token list — no output, no crash
   - Whitespace-only tokens — no output (trailing safety flush is quiet)
   - Tokens that are empty strings — skipped silently

6. Overflow safety flush
   - <think> block that never closes, exceeds _THINK_BUF_MAX — whole buffer yielded
   - Truncated stream with partial <think> never resolved — trailing flush fires
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.stream_filter import (  # noqa: E402
    _THINK_BUF_MAX,
    stream_thinking_filter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(tokens: list[str]) -> list[str]:
    """Collect all yielded chunks into a list."""
    return list(stream_thinking_filter(iter(tokens)))


def _joined(tokens: list[str]) -> str:
    """Collect and join — useful when exact chunk boundaries don't matter."""
    return "".join(_run(tokens))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreamFilterReport:
    # 1. No preamble
    no_preamble_single_chunk: bool
    no_preamble_multi_token: bool
    no_preamble_leading_whitespace_stripped: bool
    no_preamble_non_think_tag_passes_through: bool

    # 2. Complete <think> block
    think_block_single_token: bool
    think_block_split_tokens: bool
    think_block_content_immediately_after: bool
    think_block_newlines_after_stripped: bool
    think_block_case_insensitive: bool
    think_block_inner_content_does_not_leak: bool

    # 3. Split-mode server
    split_mode_plain_tokens_pass_through: bool

    # 4. Partial tag at buffer boundary
    partial_tag_char_by_char: bool
    partial_lt_then_non_think_flushes: bool
    partial_closing_tag_split: bool

    # 5. Empty / whitespace-only stream
    empty_stream_no_output: bool
    whitespace_only_no_output: bool
    empty_string_tokens_silent: bool

    # 6. Overflow / safety flush
    overflow_unclosed_think_yields_buffer: bool
    truncated_think_trailing_flush: bool

    @property
    def all_passed(self) -> bool:
        return all(asdict(self).values())


# ---------------------------------------------------------------------------
# Case 1 — No preamble
# ---------------------------------------------------------------------------

def _case1_no_preamble_single_chunk() -> bool:
    result = _run(["Hello, Sir."])
    return result == ["Hello, Sir."]


def _case1_no_preamble_multi_token() -> bool:
    # First token is a plain word — filter must pass through immediately,
    # not buffer waiting to see if more tokens form a <think> tag.
    result = _run(["Good", " evening", "."])
    return result == ["Good", " evening", "."]


def _case1_leading_whitespace_stripped() -> bool:
    # Leading \n\r\n before content should be stripped from the first yielded chunk.
    result = _joined(["\n\r\n", "Hello"])
    return result == "Hello"


def _case1_non_think_tag_passes_through() -> bool:
    # A response starting with <tool_call> is not a think preamble — must flush
    # the tag and switch to pass-through (decision tree branch 4).
    result = _joined(["<tool_call>", "search", "</tool_call>"])
    return "<tool_call>" in result and "search" in result


# ---------------------------------------------------------------------------
# Case 2 — Complete <think> block
# ---------------------------------------------------------------------------

def _case2_single_token() -> bool:
    result = _run(["<think>hidden reasoning</think>The answer"])
    assert len(result) == 1
    return result[0] == "The answer"


def _case2_split_tokens() -> bool:
    tokens = ["<th", "ink>", "some", " reasoning", "</th", "ink>", "Answer"]
    return _joined(tokens) == "Answer"


def _case2_content_immediately_after() -> bool:
    # No gap — content directly appended to closing tag in same token.
    result = _joined(["<think>x</think>Direct"])
    return result == "Direct"


def _case2_newlines_after_stripped() -> bool:
    # Newlines between </think> and content are stripped.
    result = _joined(["<think>x</think>\n\nActual response"])
    return result == "Actual response"


def _case2_case_insensitive() -> bool:
    # Filter uses .lower() — uppercase tags must be handled.
    result = _joined(["<THINK>hidden</THINK>Visible"])
    return result == "Visible"


def _case2_inner_content_does_not_leak() -> bool:
    # Nothing inside the think block should appear in output.
    tokens = ["<think>SECRET REASONING DO NOT SHOW</think>Safe output"]
    result = _joined(tokens)
    return "SECRET" not in result and result == "Safe output"


# ---------------------------------------------------------------------------
# Case 3 — Split-mode server
# ---------------------------------------------------------------------------

def _case3_split_mode_plain_tokens() -> bool:
    # Thinking tokens arrive on a separate SSE field; content stream only
    # sees plain response tokens.  Must pass through without buffering.
    tokens = ["I", " am", " ready", " to", " help", "."]
    result = _run(tokens)
    return result == tokens


# ---------------------------------------------------------------------------
# Case 4 — Partial tag at buffer boundary
# ---------------------------------------------------------------------------

def _case4_char_by_char_think_tag() -> bool:
    # '<think>' arrives one character at a time.
    # Filter must not flush prematurely, and must strip the full block.
    tokens = list("<think>") + ["reasoning", "</think>", "Response"]
    return _joined(tokens) == "Response"


def _case4_partial_lt_then_non_think() -> bool:
    # '<' arrives alone, followed by something that is NOT 'think>'.
    # Once 7+ non-whitespace chars are buffered and it's not <think>,
    # branch 4 fires: flush the buffer and pass through.
    tokens = ["<", "b", "r", "a", "v", "o", ">", " text"]
    result = _joined(tokens)
    # The <bravo> tag and subsequent text must all be in the output.
    return "<bravo>" in result and "text" in result


def _case4_closing_tag_split() -> bool:
    # </think> closing tag arrives in two halves.
    tokens = ["<think>inner</thi", "nk>After"]
    return _joined(tokens) == "After"


# ---------------------------------------------------------------------------
# Case 5 — Empty / whitespace-only stream
# ---------------------------------------------------------------------------

def _case5_empty_stream() -> bool:
    return _run([]) == []


def _case5_whitespace_only() -> bool:
    # All tokens are whitespace.  Trailing safety flush: buf.strip() == ""
    # so nothing should be yielded.
    return _run(["   ", "\n", "\t", "\r\n"]) == []


def _case5_empty_string_tokens() -> bool:
    # Empty string tokens mixed with real content — must not corrupt output.
    result = _joined(["", "Hello", "", " world", ""])
    return result == "Hello world"


# ---------------------------------------------------------------------------
# Case 6 — Overflow safety flush
# ---------------------------------------------------------------------------

def _case6_overflow_unclosed_think() -> bool:
    # <think> block that never closes and exceeds _THINK_BUF_MAX.
    # The entire buffer must be yielded rather than silently dropped.
    filler = "x" * (_THINK_BUF_MAX + 1)
    tokens = ["<think>", filler]  # no </think>
    result = _joined(tokens)
    # Output must contain the filler content — nothing silently dropped.
    return filler in result


def _case6_truncated_think_trailing_flush() -> bool:
    # Stream ends while still inside <think> block (server cut the connection).
    # Trailing safety flush at end of generator should yield what's buffered.
    tokens = ["<think>", "partial reasoning, stream died here"]
    result = _joined(tokens)
    return "partial reasoning" in result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all(*, verbose: bool = False) -> StreamFilterReport:
    def check(name: str, fn) -> bool:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            if verbose:
                print(f"  EXCEPTION in {name}: {exc}")
        if verbose:
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name}")
        return ok

    return StreamFilterReport(
        # 1
        no_preamble_single_chunk=check(
            "no_preamble_single_chunk", _case1_no_preamble_single_chunk),
        no_preamble_multi_token=check(
            "no_preamble_multi_token", _case1_no_preamble_multi_token),
        no_preamble_leading_whitespace_stripped=check(
            "no_preamble_leading_whitespace_stripped", _case1_leading_whitespace_stripped),
        no_preamble_non_think_tag_passes_through=check(
            "no_preamble_non_think_tag_passes_through", _case1_non_think_tag_passes_through),
        # 2
        think_block_single_token=check(
            "think_block_single_token", _case2_single_token),
        think_block_split_tokens=check(
            "think_block_split_tokens", _case2_split_tokens),
        think_block_content_immediately_after=check(
            "think_block_content_immediately_after", _case2_content_immediately_after),
        think_block_newlines_after_stripped=check(
            "think_block_newlines_after_stripped", _case2_newlines_after_stripped),
        think_block_case_insensitive=check(
            "think_block_case_insensitive", _case2_case_insensitive),
        think_block_inner_content_does_not_leak=check(
            "think_block_inner_content_does_not_leak", _case2_inner_content_does_not_leak),
        # 3
        split_mode_plain_tokens_pass_through=check(
            "split_mode_plain_tokens_pass_through", _case3_split_mode_plain_tokens),
        # 4
        partial_tag_char_by_char=check(
            "partial_tag_char_by_char", _case4_char_by_char_think_tag),
        partial_lt_then_non_think_flushes=check(
            "partial_lt_then_non_think_flushes", _case4_partial_lt_then_non_think),
        partial_closing_tag_split=check(
            "partial_closing_tag_split", _case4_closing_tag_split),
        # 5
        empty_stream_no_output=check(
            "empty_stream_no_output", _case5_empty_stream),
        whitespace_only_no_output=check(
            "whitespace_only_no_output", _case5_whitespace_only),
        empty_string_tokens_silent=check(
            "empty_string_tokens_silent", _case5_empty_string_tokens),
        # 6
        overflow_unclosed_think_yields_buffer=check(
            "overflow_unclosed_think_yields_buffer", _case6_overflow_unclosed_think),
        truncated_think_trailing_flush=check(
            "truncated_think_trailing_flush", _case6_truncated_think_trailing_flush),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="stream_thinking_filter smoke test")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    report = run_all(verbose=args.verbose)
    results = asdict(report)
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if not args.verbose:
        # Print a compact summary even without --verbose
        for name, ok in results.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    print(f"\n{'ALL PASSED' if report.all_passed else 'FAILURES DETECTED'} ({passed}/{total})")
    sys.exit(0 if report.all_passed else 1)


if __name__ == "__main__":
    main()
