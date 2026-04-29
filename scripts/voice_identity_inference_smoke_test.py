from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.voice_recognition import VoiceFingerprintEngine
from memory.user_runtime import ActiveUserRuntime


@dataclass(frozen=True)
class VoiceIdentityInferenceReport:
    success: bool
    skipped: bool
    reason: str
    best_user: str
    best_score: float
    active_after_voice: str
    admin_unlocked: bool
    checks: dict[str, bool]


def run_smoke() -> VoiceIdentityInferenceReport:
    try:
        import numpy as np
    except Exception as exc:
        return VoiceIdentityInferenceReport(
            success=True,
            skipped=True,
            reason=f"numpy unavailable in this Python: {exc}",
            best_user="",
            best_score=0.0,
            active_after_voice="",
            admin_unlocked=False,
            checks={"numpy_available": False},
        )

    with tempfile.TemporaryDirectory(prefix="piper-voice-identity-") as raw_tmp:
        data_dir = Path(raw_tmp) / "data"
        voice_dir = data_dir / "voice_embeddings"
        voice_dir.mkdir(parents=True, exist_ok=True)

        engine = VoiceFingerprintEngine(data_dir=voice_dir)
        engine.import_profile("baris", [np.array([1.0, 0.0, 0.0])], admin=True)
        best_user, best_score = engine.best_match(np.array([0.65, 0.76, 0.0]))

        runtime = ActiveUserRuntime(
            data_dir,
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
        )
        result = runtime.activate_voice_match(best_user or "", best_score)
        active_after_voice = runtime.active_profile().user_id
        admin_unlocked = runtime.is_admin_unlocked()
        checks = {
            "best_match_keeps_candidate_below_strict_admin_threshold": best_user == "baris"
            and 0.60 <= best_score < 0.90,
            "voice_match_maps_baris_to_admin_profile": bool(getattr(result, "switched", False))
            and active_after_voice == "admin_baris",
            "voice_match_unlocks_admin": admin_unlocked,
        }
        try:
            import core.voice_recognition as voice_recognition
            from tools.stt import STTEngine

            class _Segment:
                text = "hello"

            class _FakeWhisper:
                def transcribe(self, audio, **kwargs):
                    return [_Segment()], object()

            class _FakeVoiceEngine:
                def available(self):
                    return True

                def extract_embedding(self, samples):
                    return np.array([0.65, 0.76, 0.0])

                def match(self, embedding):
                    return None, 0.65

                def best_match(self, embedding):
                    return "baris", 0.65

                def is_enrolling(self, user_id):
                    return False

            previous_engine = getattr(voice_recognition, "_voice_engine", None)
            voice_recognition._voice_engine = _FakeVoiceEngine()
            try:
                stt = STTEngine()
                stt.model = _FakeWhisper()
                stt._min_rms = 0.0
                stt._audio_data = [np.ones((16000, 1), dtype=np.int16)]
                stt.set_active_voice_profile("unknown", is_unknown=True)
                transcript = stt.stop_recording()
                stt_match = stt.consume_last_voice_match()
            finally:
                voice_recognition._voice_engine = previous_engine
            checks["stt_hook_records_first_turn_best_match"] = (
                transcript == "hello"
                and isinstance(stt_match, tuple)
                and stt_match[0] == "baris"
                and float(stt_match[1]) >= 0.60
            )
        except Exception:
            checks["stt_hook_records_first_turn_best_match"] = False

    return VoiceIdentityInferenceReport(
        success=all(checks.values()),
        skipped=False,
        reason="",
        best_user=str(best_user or ""),
        best_score=float(best_score),
        active_after_voice=active_after_voice,
        admin_unlocked=bool(admin_unlocked),
        checks=checks,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
