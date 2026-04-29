from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG  # noqa: E402
from memory.user_runtime import ActiveUserRuntime  # noqa: E402


@dataclass(frozen=True)
class SpeakerIdentityCorrectionReport:
    success: bool
    first_turn_threshold_is_not_low_confidence: bool
    router_identity_overrides_admin_voice_guess: bool
    negated_description_does_not_become_name: bool
    corrected_public_profile_created: bool


def run_smoke() -> SpeakerIdentityCorrectionReport:
    with tempfile.TemporaryDirectory() as raw_tmp:
        runtime = ActiveUserRuntime(
            Path(raw_tmp),
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
        )
        runtime.switch_active_user("admin_baris")

        router_result = runtime.apply_router_identity_intent("Jim")
        router_identity_overrides_admin_voice_guess = (
            bool(getattr(router_result, "switched", False))
            and runtime.active_profile().user_id == "jim"
            and not runtime.active_profile().is_admin
        )

        runtime.switch_active_user("admin_baris")
        corrective_result = runtime.observe_typed_identity_hint(
            "Okay, I'm not British, I'm Jim, and I didn't initiate this."
        )
        negated_description_does_not_become_name = runtime.active_profile().user_id != "not_british"
        corrected_public_profile_created = (
            bool(getattr(corrective_result, "switched", False))
            and runtime.active_profile().user_id == "jim"
            and not runtime.active_profile().is_admin
        )

    first_turn_threshold_is_not_low_confidence = float(CFG.VOICE_FIRST_TURN_INFER_THRESHOLD) >= 0.60
    success = all(
        [
            first_turn_threshold_is_not_low_confidence,
            router_identity_overrides_admin_voice_guess,
            negated_description_does_not_become_name,
            corrected_public_profile_created,
        ]
    )
    return SpeakerIdentityCorrectionReport(
        success=success,
        first_turn_threshold_is_not_low_confidence=first_turn_threshold_is_not_low_confidence,
        router_identity_overrides_admin_voice_guess=router_identity_overrides_admin_voice_guess,
        negated_description_does_not_become_name=negated_description_does_not_become_name,
        corrected_public_profile_created=corrected_public_profile_created,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
