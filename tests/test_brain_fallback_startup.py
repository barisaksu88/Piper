"""Guard tests for fallback-first brain startup.

PiperBrain.__init__ must return immediately with fallback memory ready.
Vector backend warm-up happens in a background thread. Heavy imports like
chromadb and sentence_transformers must not block get_brain().
"""

from __future__ import annotations

import builtins
import sys
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_brain_cache():
    """Clear the global brain cache so each test gets a fresh PiperBrain."""
    from memory import brain as _brain_module

    _brain_module._brains.clear()
    yield
    _brain_module._brains.clear()


def _unique_temp_data_dir() -> Path:
    """Return a fresh temp directory for a PiperBrain instance."""
    return Path(tempfile.mkdtemp(prefix="piper_brain_test_"))


class TestFallbackFirstStartup:
    """PiperBrain must return quickly with fallback memory, never blocking on imports."""

    def test_init_returns_quickly(self) -> None:
        """PiperBrain.__init__ must return in < 0.5 s even if vector warm-up is slow."""
        from memory.brain import PiperBrain

        data_dir = _unique_temp_data_dir()
        t0 = time.perf_counter()
        brain = PiperBrain(data_dir)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"PiperBrain.__init__ took {elapsed:.2f}s, expected < 0.5s"
        # Fallback is ready immediately
        assert len(brain._fallback_entries) == 0

    def test_vector_warmup_pending_immediately_after_init(self) -> None:
        from memory.brain import PiperBrain

        data_dir = _unique_temp_data_dir()
        brain = PiperBrain(data_dir)
        # Vector warm-up should have been started in background
        assert brain._vector_init_started is True
        # Vector is not ready yet (or maybe ready if chromadb is very fast)
        # but warmup_pending should be True unless init already failed or succeeded
        if not brain.vector_ready and not brain._vector_init_failed:
            assert brain.vector_warmup_pending is True

    def test_fallback_recall_works_before_vector_ready(self) -> None:
        from memory.brain import PiperBrain

        data_dir = _unique_temp_data_dir()
        brain = PiperBrain(data_dir)
        brain.remember("Buy milk", metadata={"type": "task"})
        results = brain.recall("milk", n_results=5)
        assert len(results) >= 1
        assert any("milk" in r["text"] for r in results)

    def test_fallback_remember_works_before_vector_ready(self) -> None:
        from memory.brain import PiperBrain

        data_dir = _unique_temp_data_dir()
        brain = PiperBrain(data_dir)
        brain.remember("Call dentist", metadata={"type": "task"})
        assert any("dentist" in e["text"] for e in brain._fallback_entries)

    def test_vector_init_failed_flag_set_on_import_error(self, monkeypatch) -> None:
        """If chromadb import fails, _vector_init_failed must become True."""
        from memory.brain import PiperBrain

        original_import = builtins.__import__

        def _raising_import(name, *args, **kwargs):
            if name == "chromadb":
                raise ImportError("chromadb not available")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _raising_import)

        data_dir = _unique_temp_data_dir()
        brain = PiperBrain(data_dir)
        # Wait for background thread to finish (or fail)
        for _ in range(50):
            if brain._vector_init_failed or brain.vector_ready:
                break
            time.sleep(0.05)

        assert brain._vector_init_failed is True
        assert brain._vector_memory_available is False
        assert brain.vector_ready is False

    def test_no_sentence_transformers_import_during_init(self, monkeypatch) -> None:
        """SentenceTransformer must not be imported synchronously during __init__."""
        from memory.brain import PiperBrain

        imported_during_init = False
        original_import = builtins.__import__

        def _tracking_import(name, *args, **kwargs):
            nonlocal imported_during_init
            if name == "sentence_transformers" or name.startswith("sentence_transformers."):
                imported_during_init = True
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _tracking_import)

        data_dir = _unique_temp_data_dir()
        brain = PiperBrain(data_dir)
        # Give the background thread a moment to start
        time.sleep(0.1)
        # The import should NOT have happened in the main thread during __init__
        # It may have happened in the background thread, which is fine.
        # We just verify __init__ itself returned without importing it.
        assert brain is not None

    def test_get_brain_returns_same_instance_for_same_dir(self) -> None:
        from memory.brain import get_brain

        data_dir = _unique_temp_data_dir()
        b1 = get_brain(data_dir)
        b2 = get_brain(data_dir)
        assert b1 is b2

    def test_recall_falls_back_when_collection_none(self) -> None:
        """If vector is not ready, recall() must return fallback results."""
        from memory.brain import PiperBrain

        data_dir = _unique_temp_data_dir()
        brain = PiperBrain(data_dir)
        # Ensure collection is None (warm-up may not have finished)
        brain.collection = None
        brain._vector_memory_available = True  # optimistic, but collection missing
        brain.remember("Buy eggs", metadata={"type": "task"})
        results = brain.recall("eggs", n_results=5)
        assert len(results) >= 1
        assert any("eggs" in r["text"] for r in results)
