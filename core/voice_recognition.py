"""Passive voice-based user identification using Resemblyzer."""

from __future__ import annotations

import os
import pickle
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from resemblyzer import VoiceEncoder, preprocess_wav
    _RESEMBLYZER_AVAILABLE = True
except ImportError:
    _RESEMBLYZER_AVAILABLE = False


class VoiceFingerprintEngine:
    """Extracts voice embeddings from audio and matches against enrolled users.
    
    Philosophy: Every voice is enrolled automatically once identity is known.
    No confirmation, no explicit 'say something' step. Passive, always-on.
    """
    
    def __init__(self, *, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._encoder: Any = None
        self._lock = threading.Lock()
        self._embeddings: Dict[str, list] = {}  # user_id -> list of embedding vectors
        self._enrollment_buffer: Dict[str, list] = {}  # user_id -> buffered embeddings during enrollment
        self._enrollment_turns_remaining: Dict[str, int] = {}
        self._low_confidence_counter: Dict[str, int] = {}  # user_id -> consecutive low-confidence turns
        self._load_all_embeddings()
    
    def available(self) -> bool:
        return _RESEMBLYZER_AVAILABLE
    
    def _get_encoder(self) -> Any:
        if self._encoder is None and _RESEMBLYZER_AVAILABLE:
            self._encoder = VoiceEncoder()
        return self._encoder
    
    def _embedding_path(self, user_id: str) -> Path:
        return self._data_dir / f"{user_id}.pkl"
    
    def _load_all_embeddings(self) -> None:
        """Load all enrolled embeddings from disk."""
        if not self._data_dir.exists():
            return
        for path in self._data_dir.glob("*.pkl"):
            user_id = path.stem
            try:
                with open(path, "rb") as f:
                    self._embeddings[user_id] = pickle.load(f)
            except Exception:
                pass
    
    def _save_embeddings(self, user_id: str) -> None:
        """Save a user's embeddings to disk."""
        embeddings = self._embeddings.get(user_id, [])
        if not embeddings:
            return
        try:
            with open(self._embedding_path(user_id), "wb") as f:
                pickle.dump(embeddings, f)
        except Exception:
            pass
    
    def extract_embedding(self, audio_samples: Any, sample_rate: int = 16000) -> Optional[Any]:
        """Extract voice embedding from raw audio samples. Returns None on failure."""
        encoder = self._get_encoder()
        if encoder is None:
            return None
        try:
            import numpy as np
            wav = preprocess_wav(audio_samples, source_sr=sample_rate)
            embedding = encoder.embed_utterance(wav)
            return embedding
        except Exception:
            return None
    
    def start_enrollment(self, user_id: str) -> None:
        """Begin collecting embeddings for a newly identified user."""
        with self._lock:
            self._enrollment_buffer[user_id] = []
            from config import CFG
            self._enrollment_turns_remaining[user_id] = CFG.VOICE_ENROLLMENT_TURNS
    
    def add_enrollment_sample(self, user_id: str, embedding: Any) -> bool:
        """Add one embedding to the enrollment buffer. Returns True when enrollment complete."""
        with self._lock:
            if user_id not in self._enrollment_buffer:
                return False
            self._enrollment_buffer[user_id].append(embedding)
            self._enrollment_turns_remaining[user_id] -= 1
            if self._enrollment_turns_remaining[user_id] <= 0:
                # Enrollment complete — save averaged embedding
                self._embeddings[user_id] = self._enrollment_buffer[user_id]
                self._save_embeddings(user_id)
                del self._enrollment_buffer[user_id]
                del self._enrollment_turns_remaining[user_id]
                return True
            return False
    
    def match(self, embedding: Any) -> Tuple[Optional[str], float]:
        """Compare embedding against all enrolled users. Returns (user_id, similarity).
        
        Returns (None, 0.0) if no match or no enrolled users.
        """
        if not self._embeddings:
            return None, 0.0
        
        best_user: Optional[str] = None
        best_score: float = 0.0
        
        import numpy as np
        for user_id, enrolled_embeddings in self._embeddings.items():
            if not enrolled_embeddings:
                continue
            # Average all enrolled embeddings for this user, then compare
            avg_embedding = np.mean(enrolled_embeddings, axis=0)
            similarity = float(np.dot(embedding, avg_embedding) / (
                np.linalg.norm(embedding) * np.linalg.norm(avg_embedding) + 1e-8
            ))
            if similarity > best_score:
                best_score = similarity
                best_user = user_id
        
        return best_user, best_score
    
    def check_low_confidence_ask(self, user_id: str, similarity: float) -> Optional[str]:
        """Returns a clarification question if confidence stays low too long."""
        from config import CFG
        
        if similarity >= CFG.VOICE_SIMILARITY_THRESHOLD_LOW:
            self._low_confidence_counter.pop(user_id, None)
            return None
        
        self._low_confidence_counter[user_id] = self._low_confidence_counter.get(user_id, 0) + 1
        if self._low_confidence_counter[user_id] >= CFG.VOICE_LOW_CONFIDENCE_ASK_AFTER:
            self._low_confidence_counter[user_id] = 0  # reset so we don't spam
            return "I'm not quite sure — is that you?"
        return None
    
    def forget_user(self, user_id: str) -> None:
        """Remove all voice data for a user."""
        with self._lock:
            self._embeddings.pop(user_id, None)
            self._enrollment_buffer.pop(user_id, None)
            self._enrollment_turns_remaining.pop(user_id, None)
            try:
                self._embedding_path(user_id).unlink(missing_ok=True)
            except Exception:
                pass
    
    def is_enrolling(self, user_id: str) -> bool:
        return user_id in self._enrollment_buffer


# Singleton
_voice_engine: Optional[VoiceFingerprintEngine] = None
_voice_lock = threading.Lock()

def get_voice_engine() -> VoiceFingerprintEngine:
    global _voice_engine
    if _voice_engine is None:
        from config import CFG
        _voice_engine = VoiceFingerprintEngine(data_dir=Path(CFG.DATA_DIR) / "voice_embeddings")
    return _voice_engine
