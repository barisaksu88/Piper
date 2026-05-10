from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

# Prevent heavy ML libraries from hanging the smoke test at import time.
# These are only needed for real audio/embedding inference; the fast smoke
# path works with pure-Python threshold logic and mocked engines.
class _StubModule(types.ModuleType):
    def __getattr__(self, name: str):
        raise ImportError(f"{self.__name__} is stubbed in smoke test")

for _mod_name in ("resemblyzer", "sentence_transformers"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _StubModule(_mod_name)

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG  # noqa: E402
from core.voice_recognition import VoiceFingerprintEngine  # noqa: E402
from memory.user_runtime import ActiveUserRuntime  # noqa: E402


@dataclass(frozen=True)
class VoiceIdentityInferenceReport:
    success: bool
    skipped: bool
    reason: str
    accepted_admin_score: float
    accepted_admin_second_score: float
    accepted_admin_margin: float
    low_admin_final_user: str
    low_admin_reason: str
    close_admin_final_user: str
    close_admin_reason: str
    public_final_user: str
    unknown_reason: str
    active_after_voice: str
    admin_unlocked: bool
    checks: dict[str, bool]


def _unit(angle: float):
    import numpy as np

    return np.array([math.cos(angle), math.sin(angle)])


def _embedding_for_admin_score(score: float):
    return _unit(math.acos(score))


def _embedding_with_score_against(reference, score: float):
    # A 2D unit vector whose cosine similarity against `reference` is `score`.
    import numpy as np

    base_angle = math.atan2(float(reference[1]), float(reference[0]))
    return np.array([math.cos(base_angle + math.acos(score)), math.sin(base_angle + math.acos(score))])


def _stt_hook_checks() -> dict[str, bool]:
    """Optional STT hook verification — may touch audio-stack mocks."""
    try:
        import numpy as np
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
                return np.array([1.0, 0.0])

            def evaluate_match(self, embedding):
                return SimpleNamespace(
                    best_user="baris",
                    best_score=0.865,
                    second_score=0.681,
                    margin=0.184,
                    best_is_admin=True,
                    threshold=0.82,
                    margin_threshold=0.14,
                    final_user="baris",
                    decision="accepted_admin",
                    reason="accepted_admin",
                )

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
        details = stt_match[2] if isinstance(stt_match, tuple) and len(stt_match) >= 3 else {}
        return {
            "stt_hook_records_margin_decision": (
                transcript == "hello"
                and isinstance(stt_match, tuple)
                and stt_match[0] == "baris"
                and float(stt_match[1]) >= 0.865
                and isinstance(details, dict)
                and abs(float(details.get("margin") or 0.0) - 0.184) < 0.001
                and bool(details.get("best_is_admin"))
            ),
        }
    except Exception:
        return {"stt_hook_records_margin_decision": False}


def run_smoke(include_stt_hook: bool = False) -> VoiceIdentityInferenceReport:
    try:
        import numpy as np
    except Exception as exc:
        return VoiceIdentityInferenceReport(
            success=True,
            skipped=True,
            reason=f"numpy unavailable in this Python: {exc}",
            accepted_admin_score=0.0,
            accepted_admin_second_score=0.0,
            accepted_admin_margin=0.0,
            low_admin_final_user="",
            low_admin_reason="",
            close_admin_final_user="",
            close_admin_reason="",
            public_final_user="",
            unknown_reason="",
            active_after_voice="",
            admin_unlocked=False,
            checks={"numpy_available": False},
        )

    with tempfile.TemporaryDirectory(prefix="piper-voice-identity-") as raw_tmp:
        data_dir = Path(raw_tmp) / "data"
        voice_dir = data_dir / "voice_embeddings"
        voice_dir.mkdir(parents=True, exist_ok=True)

        admin_embedding = np.array([1.0, 0.0])
        calibrated_baris_probe = _embedding_for_admin_score(0.865)
        calibrated_second = _embedding_with_score_against(calibrated_baris_probe, 0.681)

        engine = VoiceFingerprintEngine(data_dir=voice_dir)
        engine.import_profile("baris", [admin_embedding], admin=True)
        engine.import_profile("guest_public", [calibrated_second], admin=False)
        accepted_admin = engine.evaluate_match(calibrated_baris_probe)

        low_admin_probe = _embedding_for_admin_score(0.72)
        low_admin_second = _embedding_with_score_against(low_admin_probe, 0.50)
        low_engine = VoiceFingerprintEngine(data_dir=voice_dir / "low")
        low_engine.import_profile("baris", [admin_embedding], admin=True)
        low_engine.import_profile("guest_public", [low_admin_second], admin=False)
        low_admin = low_engine.evaluate_match(low_admin_probe)

        close_second = _embedding_with_score_against(calibrated_baris_probe, 0.78)
        close_engine = VoiceFingerprintEngine(data_dir=voice_dir / "close")
        close_engine.import_profile("baris", [admin_embedding], admin=True)
        close_engine.import_profile("guest_public", [close_second], admin=False)
        close_admin = close_engine.evaluate_match(calibrated_baris_probe)

        public_embedding = np.array([0.0, 1.0])
        public_probe = _embedding_with_score_against(public_embedding, 0.80)
        public_second = _embedding_with_score_against(public_probe, 0.68)
        public_engine = VoiceFingerprintEngine(data_dir=voice_dir / "public")
        public_engine.import_profile("baris", [public_second], admin=True)
        public_engine.import_profile("max", [public_embedding], admin=False)
        public_decision = public_engine.evaluate_match(public_probe)

        unknown_probe = _embedding_for_admin_score(0.50)
        unknown_second = _embedding_with_score_against(unknown_probe, 0.40)
        unknown_engine = VoiceFingerprintEngine(data_dir=voice_dir / "unknown")
        unknown_engine.import_profile("baris", [admin_embedding], admin=True)
        unknown_engine.import_profile("guest_public", [unknown_second], admin=False)
        unknown_decision = unknown_engine.evaluate_match(unknown_probe)

        runtime = ActiveUserRuntime(
            data_dir,
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
        )
        result = runtime.activate_voice_match(
            accepted_admin.final_user,
            accepted_admin.best_score,
            margin=accepted_admin.margin,
        )
        active_after_voice = runtime.active_profile().user_id
        admin_unlocked = runtime.is_admin_unlocked()
        rejected_runtime = ActiveUserRuntime(
            data_dir / "runtime_reject",
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
        )
        rejected_result = rejected_runtime.activate_voice_match("baris", 0.72, margin=0.20)

        checks = {
            "thresholds_match_calibration": (
                float(CFG.VOICE_ADMIN_SIMILARITY_THRESHOLD) == 0.82
                and float(CFG.VOICE_ADMIN_MARGIN_THRESHOLD) == 0.14
                and float(CFG.VOICE_SIMILARITY_THRESHOLD_HIGH) == 0.74
                and float(CFG.VOICE_SIMILARITY_THRESHOLD_LOW) == 0.58
                and float(CFG.VOICE_FIRST_TURN_INFER_THRESHOLD) == 0.74
                and float(CFG.VOICE_PUBLIC_MARGIN_THRESHOLD) == 0.08
            ),
            "calibrated_baris_admin_accepted": (
                accepted_admin.final_user == "baris"
                and accepted_admin.decision == "accepted_admin"
                and accepted_admin.best_score >= 0.865 - 0.001
                and accepted_admin.second_score >= 0.681 - 0.001
                and accepted_admin.margin >= 0.184 - 0.002
            ),
            "low_admin_score_rejected": (
                low_admin.final_user == ""
                and low_admin.best_user == "baris"
                and low_admin.reason == "admin_score_below_threshold"
            ),
            "admin_small_margin_rejected": (
                close_admin.final_user == ""
                and close_admin.best_user == "baris"
                and close_admin.reason == "admin_margin_below_threshold"
            ),
            "public_user_accepted_with_public_thresholds": (
                public_decision.final_user == "max"
                and public_decision.decision == "accepted_public"
            ),
            "below_low_threshold_stays_unknown": (
                unknown_decision.final_user == ""
                and unknown_decision.decision == "unknown"
                and unknown_decision.reason == "score_below_low_threshold"
            ),
            "runtime_unlocks_admin_only_with_margin": (
                bool(getattr(result, "switched", False))
                and active_after_voice == "admin_baris"
                and admin_unlocked
                and rejected_result is not None
                and not getattr(rejected_result, "switched", False)
                and rejected_runtime.active_profile().user_id == "unknown"
            ),
        }

        if include_stt_hook:
            checks.update(_stt_hook_checks())

    return VoiceIdentityInferenceReport(
        success=all(checks.values()),
        skipped=False,
        reason="",
        accepted_admin_score=float(accepted_admin.best_score),
        accepted_admin_second_score=float(accepted_admin.second_score),
        accepted_admin_margin=float(accepted_admin.margin),
        low_admin_final_user=str(low_admin.final_user),
        low_admin_reason=str(low_admin.reason),
        close_admin_final_user=str(close_admin.final_user),
        close_admin_reason=str(close_admin.reason),
        public_final_user=str(public_decision.final_user),
        unknown_reason=str(unknown_decision.reason),
        active_after_voice=active_after_voice,
        admin_unlocked=bool(admin_unlocked),
        checks=checks,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Voice identity inference smoke test.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    parser.add_argument(
        "--include-stt-hook",
        action="store_true",
        help="Also verify the STT hook margin-decision path (slower, touches audio-stack mocks).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke(include_stt_hook=args.include_stt_hook)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"SKIPPED: {report.skipped}")
        if report.reason:
            print(f"REASON: {report.reason}")
        print(f"CHECKS: {report.checks}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
