"""Passive voice-based user identification using Resemblyzer."""

from __future__ import annotations

import os
import pickle
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from resemblyzer import VoiceEncoder, preprocess_wav
    _RESEMBLYZER_AVAILABLE = True
except ImportError:
    _RESEMBLYZER_AVAILABLE = False


@dataclass(frozen=True)
class VoiceMatchDecision:
    best_user: str = ""
    best_score: float = 0.0
    second_score: float = 0.0
    margin: float = 0.0
    best_is_admin: bool = False
    threshold: float = 0.0
    margin_threshold: float = 0.0
    final_user: str = ""
    decision: str = "unknown"
    reason: str = "no_match"

    @property
    def accepted(self) -> bool:
        return self.decision in {"accepted_admin", "accepted_public"} and bool(self.final_user)


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
        self._admin_users: set[str] = set()
        self._load_all_embeddings()
        self._load_admin_meta()

    def available(self) -> bool:
        return _RESEMBLYZER_AVAILABLE

    def _get_encoder(self) -> Any:
        if self._encoder is None and _RESEMBLYZER_AVAILABLE:
            self._encoder = VoiceEncoder()
        return self._encoder

    def _embedding_path(self, user_id: str) -> Path:
        return self._data_dir / f"{user_id}.pkl"

    def _admin_meta_path(self) -> Path:
        return self._data_dir / "_admin_meta.json"

    def _load_admin_meta(self) -> None:
        path = self._admin_meta_path()
        if path.exists():
            try:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._admin_users = set(data.get("admin_users", []))
            except Exception:
                self._admin_users = set()

    def _save_admin_meta(self) -> None:
        path = self._admin_meta_path()
        try:
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"admin_users": sorted(self._admin_users)}, f)
        except Exception:
            pass

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
            self._save_admin_meta()
        except Exception:
            pass

    def import_profile(self, user_id: str, embeddings: list[Any], *, admin: bool = False) -> None:
        """Directly import a pre-built voice profile from embeddings.

        This is intended for manual admin profile creation — e.g. record clean
        audio samples, extract embeddings, and drop them into the voice folder.
        """
        with self._lock:
            self._embeddings[user_id] = list(embeddings)
            self._save_embeddings(user_id)
            if admin:
                self._admin_users.add(user_id)
            else:
                self._admin_users.discard(user_id)
            self._save_admin_meta()

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

    def start_enrollment(self, user_id: str, *, admin: bool = False) -> None:
        """Begin collecting embeddings for a newly identified user.

        Admin enrollment uses more turns for higher-quality voice capture.
        """
        with self._lock:
            self._enrollment_buffer[user_id] = []
            from config import CFG
            turns = CFG.VOICE_ADMIN_ENROLLMENT_TURNS if admin else CFG.VOICE_ENROLLMENT_TURNS
            self._enrollment_turns_remaining[user_id] = turns
            # Track admin status so match() can apply a stricter threshold
            if admin:
                self._admin_users = getattr(self, '_admin_users', set())
                self._admin_users.add(user_id)
            else:
                self._admin_users = getattr(self, '_admin_users', set())
                self._admin_users.discard(user_id)

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

        Admin users use a stricter threshold. Returns (None, 0.0) if no match or
        the best match does not clear the user's threshold.
        """
        decision = self.evaluate_match(embedding)
        return (decision.final_user or None), decision.best_score

    def evaluate_match(self, embedding: Any, *, first_turn: bool = False) -> VoiceMatchDecision:
        """Return a score/margin-gated voice decision.

        This separates the best identity guess from permission unlock. Admin
        candidates must pass both the admin score threshold and admin margin
        threshold before the caller may unlock private/admin context.
        """
        matches = self.ranked_matches(embedding)
        if not matches:
            return VoiceMatchDecision(reason="no_enrolled_profiles")

        best_user, best_score = matches[0]
        second_score = matches[1][1] if len(matches) > 1 else 0.0
        margin = max(0.0, float(best_score) - float(second_score))
        admin_users = getattr(self, '_admin_users', set())
        best_is_admin = best_user in admin_users

        from config import CFG

        if best_is_admin:
            threshold = float(CFG.VOICE_ADMIN_SIMILARITY_THRESHOLD)
            margin_threshold = float(CFG.VOICE_ADMIN_MARGIN_THRESHOLD)
            accepted_decision = "accepted_admin"
        else:
            public_threshold = float(CFG.VOICE_SIMILARITY_THRESHOLD_HIGH)
            first_turn_threshold = float(CFG.VOICE_FIRST_TURN_INFER_THRESHOLD)
            threshold = max(public_threshold, first_turn_threshold) if first_turn else public_threshold
            margin_threshold = float(CFG.VOICE_PUBLIC_MARGIN_THRESHOLD)
            accepted_decision = "accepted_public"

        if best_score >= threshold and margin >= margin_threshold:
            return VoiceMatchDecision(
                best_user=str(best_user),
                best_score=float(best_score),
                second_score=float(second_score),
                margin=float(margin),
                best_is_admin=bool(best_is_admin),
                threshold=threshold,
                margin_threshold=margin_threshold,
                final_user=str(best_user),
                decision=accepted_decision,
                reason=accepted_decision,
            )

        low_threshold = float(CFG.VOICE_SIMILARITY_THRESHOLD_LOW)
        if best_score >= low_threshold:
            if best_score < threshold:
                reason = "admin_score_below_threshold" if best_is_admin else "public_score_below_threshold"
            else:
                reason = "admin_margin_below_threshold" if best_is_admin else "public_margin_below_threshold"
            decision = "low_confidence_admin" if best_is_admin else "low_confidence_public"
        else:
            reason = "score_below_low_threshold"
            decision = "unknown"

        return VoiceMatchDecision(
            best_user=str(best_user),
            best_score=float(best_score),
            second_score=float(second_score),
            margin=float(margin),
            best_is_admin=bool(best_is_admin),
            threshold=threshold,
            margin_threshold=margin_threshold,
            final_user="",
            decision=decision,
            reason=reason,
        )

    def best_match(self, embedding: Any) -> Tuple[Optional[str], float]:
        """Return the nearest enrolled voice without applying confidence thresholds."""
        matches = self.ranked_matches(embedding)
        if not matches:
            return None, 0.0
        return matches[0]

    def ranked_matches(self, embedding: Any) -> list[tuple[str, float]]:
        """Return enrolled voice similarities sorted best-first."""
        if not self._embeddings:
            return []

        scores: list[tuple[str, float]] = []
        import numpy as np
        for user_id, enrolled_embeddings in self._embeddings.items():
            if not enrolled_embeddings:
                continue
            avg_embedding = np.mean(enrolled_embeddings, axis=0)
            similarity = float(np.dot(embedding, avg_embedding) / (
                np.linalg.norm(embedding) * np.linalg.norm(avg_embedding) + 1e-8
            ))
            scores.append((str(user_id), similarity))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores

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
            admin_users = getattr(self, '_admin_users', set())
            admin_users.discard(user_id)
            self._admin_users = admin_users
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
