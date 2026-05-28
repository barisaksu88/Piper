"""Guard tests for _StreamChunker 3-phase chunking.

These tests lock behavior for _StreamChunker.
They require no LLM, no web search, no threading, and no external services.
"""

from __future__ import annotations

import pytest

from tools.tts import _StreamChunker


# ── 1. Phase 0 — first chunk fast start ──────────────────────────────


class TestPhase0FirstChunk:
    def test_short_first_sentence_emits_immediately(self) -> None:
        """A short first sentence >= first_complete_min_chars should emit once a
        sentence boundary is found."""
        chunker = _StreamChunker()
        chunks = chunker.push("Good day.")
        assert chunks == ["Good day."]
        assert chunker._chunks_sent == 1

    def test_very_short_sentence_waits(self) -> None:
        """A sentence below first_complete_min_chars should not emit."""
        chunker = _StreamChunker()
        chunks = chunker.push("Hello.")
        assert chunks == []
        assert chunker._chunks_sent == 0

    def test_first_sentence_at_boundary_emits(self) -> None:
        """A first sentence just above the complete-min threshold."""
        chunker = _StreamChunker()
        chunks = chunker.push("Good evening.")
        assert chunks == ["Good evening."]
        assert chunker._chunks_sent == 1

    def test_first_sentence_below_threshold_waits(self) -> None:
        """A very short fragment with no boundary should not emit."""
        chunker = _StreamChunker()
        chunks = chunker.push("Hi")
        assert chunks == []
        assert chunker._chunks_sent == 0

    def test_long_punctuationless_first_sentence_force_splits(self) -> None:
        """If the first chunk exceeds first_force_chars without a sentence
        boundary, it should force-split on a word boundary."""
        chunker = _StreamChunker()
        text = "The quick brown fox jumps over the lazy dog while the sun shines brightly on the meadow"
        # len(text) = 89, which is >= first_force_chars (80)
        chunks = chunker.push(text)
        assert len(chunks) == 1
        assert len(chunks[0]) <= 80
        assert chunker._chunks_sent == 1

    def test_long_punctuationless_force_split_on_comma(self) -> None:
        """If no space is available, force-split should fall back to comma."""
        chunker = _StreamChunker()
        # Build text with no spaces for the first 90 chars, but with a comma
        text = "a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,x,y,z,1,2,3,4,5,6,7,8,9,0,A,B,C,D,E,F,G,H,I,J,K,L,M,N,O,P,Q,R,S,T,U,V,W,X,Y,Z"
        chunks = chunker.push(text)
        assert len(chunks) == 1
        # Should split at comma, not exceed first_force_chars by much
        assert len(chunks[0]) <= 80
        assert chunker._chunks_sent == 1

    def test_first_chunk_force_split_no_safe_boundary(self) -> None:
        """If no space or comma exists, force-split at the hard limit."""
        chunker = _StreamChunker()
        text = "a" * 100
        chunks = chunker.push(text)
        assert len(chunks) == 1
        assert len(chunks[0]) == 80
        assert chunker._chunks_sent == 1

    def test_multiple_deltas_accumulate_first_chunk(self) -> None:
        """Small deltas without boundaries should accumulate."""
        chunker = _StreamChunker()
        assert chunker.push("Hello ") == []
        assert chunker.push("world") == []
        chunks = chunker.push(" today.")
        assert chunks == ["Hello world today."]
        assert chunker._chunks_sent == 1


# ── 2. Phase 1 — second chunk refill ─────────────────────────────────


class TestPhase1SecondChunk:
    def test_second_chunk_uses_lower_threshold(self) -> None:
        """After the first chunk is emitted, the second chunk should use
        second_min_chars (140) instead of later_min_chars (280)."""
        chunker = _StreamChunker()
        # Emit first chunk
        chunker.push("Hello there. ")
        assert chunker._chunks_sent == 1

        # Second chunk needs >= 100 chars to look for boundary.
        # Use enough text to also exceed second_force_chars (150) so it emits.
        text = "word " * 35  # 175 chars
        chunks = chunker.push(text)
        assert len(chunks) == 1
        assert chunker._chunks_sent == 2

    def test_second_chunk_waits_for_threshold(self) -> None:
        """Second chunk should not emit if under second_min_chars."""
        chunker = _StreamChunker()
        chunker.push("Hello there. ")
        assert chunker._chunks_sent == 1

        text = "word " * 15  # ~75 chars, under 100
        chunks = chunker.push(text)
        assert chunks == []
        assert chunker._chunks_sent == 1

    def test_second_chunk_force_splits(self) -> None:
        """If the second chunk exceeds second_force_chars without a boundary,
        it should force-split."""
        chunker = _StreamChunker()
        chunker.push("Hello there. ")
        assert chunker._chunks_sent == 1

        text = "word " * 40  # ~200 chars, exceeds second_force_chars (150)
        chunks = chunker.push(text)
        assert len(chunks) == 1
        assert len(chunks[0]) <= 150
        assert chunker._chunks_sent == 2


# ── 3. Phase 2+ — later chunks quality mode ──────────────────────────


class TestPhase2LaterChunks:
    def test_third_chunk_uses_later_threshold(self) -> None:
        """After two chunks, later chunks should use later_min_chars (280)."""
        chunker = _StreamChunker()
        chunker.push("Hello there. ")
        chunker.push("word " * 35 + ". ")  # 177 chars, enough to emit in phase 1
        assert chunker._chunks_sent == 2

        # Third chunk needs >= 280 chars to look for boundary,
        # and >= 320 (max_chars) to force-split without a boundary.
        text = "word " * 65  # 325 chars
        chunks = chunker.push(text)
        assert len(chunks) == 1
        assert chunker._chunks_sent == 3

    def test_third_chunk_does_not_use_lower_threshold(self) -> None:
        """Third chunk should NOT emit at the second_min_chars threshold."""
        chunker = _StreamChunker()
        chunker.push("Hello there. ")
        chunker.push("word " * 35 + ". ")
        assert chunker._chunks_sent == 2

        # 150 chars is above second_min_chars (140) but below later_min_chars (280)
        text = "word " * 30  # ~150 chars
        chunks = chunker.push(text)
        assert chunks == []
        assert chunker._chunks_sent == 2


# ── 4. Newline / list behavior ───────────────────────────────────────


class TestNewlineHandling:
    def test_newline_triggers_early_split(self) -> None:
        """A newline should act as a hard sentence boundary even with small
        amounts of text (above first_complete_min_chars)."""
        chunker = _StreamChunker()
        chunks = chunker.push("1. Apple\n2. Banana")
        assert chunks == ["1. Apple"]
        assert chunker._chunks_sent == 1

    def test_newline_does_not_trigger_below_min_chars(self) -> None:
        """A newline before first_complete_min_chars should not trigger."""
        chunker = _StreamChunker()
        chunks = chunker.push("A\nB")
        assert chunks == []
        assert chunker._chunks_sent == 0

    def test_newline_after_first_chunk_still_triggers(self) -> None:
        """Newlines should continue to trigger in later phases."""
        chunker = _StreamChunker()
        chunker.push("Hello world. ")
        assert chunker._chunks_sent == 1

        chunks = chunker.push("Item one\nItem two")
        assert chunks == ["Item one"]
        assert chunker._chunks_sent == 2


# ── 5. End / flush behavior ──────────────────────────────────────────


class TestEndFlush:
    def test_end_emits_remaining_text(self) -> None:
        """end() should emit any remaining text even without boundaries."""
        chunker = _StreamChunker()
        chunker.push("Hello world")
        chunks = chunker.end()
        assert chunks == ["Hello world"]
        assert chunker._chunks_sent == 0  # reset by end()

    def test_end_splits_at_boundaries_then_emits_rest(self) -> None:
        """end() should emit any remaining text after push() has already
        emitted available chunks."""
        chunker = _StreamChunker()
        # push() will emit the first sentence immediately
        chunker.push("Hello. World today is")
        chunks = chunker.end()
        # end() emits whatever push() left behind
        assert "World today is" in chunks
        assert chunker._chunks_sent == 0  # reset by end()

    def test_flush_emits_everything_remaining(self) -> None:
        """flush() should emit all remaining text immediately, ignoring any
        prior chunks already emitted by push()."""
        chunker = _StreamChunker()
        # push() may have already emitted some chunks
        chunker.push("Hello world. This is a test")
        chunks = chunker.flush()
        # flush emits whatever is left after push()
        assert len(chunks) >= 1
        assert chunker._chunks_sent == 0

    def test_reset_clears_state(self) -> None:
        """reset() should clear buffer, emitted, and chunks_sent."""
        chunker = _StreamChunker()
        chunker.push("Hello there. ")
        chunker.push("world today. ")
        assert chunker._chunks_sent >= 1
        chunker.reset()
        assert chunker.buf == ""
        assert chunker.emitted == 0
        assert chunker._chunks_sent == 0


# ── 6. Multi-chunk streaming scenarios ───────────────────────────────


class TestMultiChunkStreaming:
    def test_three_phase_streaming(self) -> None:
        """A realistic stream should show all three phases."""
        chunker = _StreamChunker()

        # Phase 0: short first sentence
        chunks = chunker.push("Hello there! ")
        assert chunks == ["Hello there!"]
        assert chunker._chunks_sent == 1

        # Phase 1: medium chunk
        text2 = "word " * 35 + ". "  # ~175 chars, triggers at second_min_chars (140)
        chunks = chunker.push(text2)
        assert len(chunks) == 1
        assert chunker._chunks_sent == 2

        # Phase 2: large chunk
        text3 = "word " * 60 + ". "  # ~300 chars, triggers at later_min_chars (280)
        chunks = chunker.push(text3)
        assert len(chunks) == 1
        assert chunker._chunks_sent == 3

    def test_force_split_on_long_second_sentence(self) -> None:
        """If the first sentence is short and the second is very long,
        the second chunk should force-split to prevent silence."""
        chunker = _StreamChunker()
        chunker.push("Hello there! ")
        assert chunker._chunks_sent == 1

        # A very long second sentence with no punctuation
        text = "the " * 80  # 320 chars, exceeds second_force_chars (210)
        chunks = chunker.push(text)
        assert len(chunks) == 1
        assert len(chunks[0]) <= 210
        assert chunker._chunks_sent == 2

    def test_phase2_force_split_at_max_chars(self) -> None:
        """In phase 2+, if text exceeds max_chars without a boundary,
        it should force-split at max_chars (320)."""
        chunker = _StreamChunker()
        # Reach phase 2+
        chunker.push("Hello there. ")
        chunker.push("word " * 50 + ". ")
        assert chunker._chunks_sent == 2

        # Feed a huge block with no spaces or boundaries
        text = "x" * 500
        chunks = chunker.push(text)
        assert len(chunks) >= 1
        # Phase 2+ force-split should be around max_chars (320),
        # not at the phase-1 threshold (150).
        assert 280 <= len(chunks[0]) <= 320
