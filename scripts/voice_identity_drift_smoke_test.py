from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
from dataclasses import asdict, dataclass
from pathlib import Path

# Prevent heavy ML libraries from hanging the smoke test at import time.
class _StubModule(types.ModuleType):
    def __getattr__(self, name: str):
        raise ImportError(f"{self.__name__} is stubbed in smoke test")

for _mod_name in ("resemblyzer", "sentence_transformers"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _StubModule(_mod_name)

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if "dearpygui.dearpygui" not in sys.modules:
    dearpygui_pkg = types.ModuleType("dearpygui")
    dearpygui_mod = types.ModuleType("dearpygui.dearpygui")
    dearpygui_mod.does_item_exist = lambda *args, **kwargs: False
    dearpygui_mod.set_item_label = lambda *args, **kwargs: None
    dearpygui_mod.bind_item_theme = lambda *args, **kwargs: None
    dearpygui_mod.theme = lambda *args, **kwargs: types.SimpleNamespace(
        __enter__=lambda self: self,
        __exit__=lambda self, exc_type, exc, tb: False,
    )
    dearpygui_mod.theme_component = dearpygui_mod.theme
    dearpygui_mod.add_theme_color = lambda *args, **kwargs: None
    dearpygui_mod.mvButton = object()
    dearpygui_mod.mvThemeCol_Button = object()
    dearpygui_pkg.dearpygui = dearpygui_mod
    sys.modules["dearpygui"] = dearpygui_pkg
    sys.modules["dearpygui.dearpygui"] = dearpygui_mod

from memory.user_runtime import ActiveUserRuntime  # noqa: E402
import ui.controller_actions as controller_actions  # noqa: E402

controller_actions._log_voice_identity_ui = lambda message: None
_apply_voice_identity_match = controller_actions._apply_voice_identity_match


class _DummyTTS:
    def stop(self) -> None:
        pass


class _DummyBrain:
    def suspend_runtime_sessions(self) -> None:
        pass


class _DummyChatState:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.bound_paths: list[str] = []
        self.fresh_sessions = 0
        self.persisted: list[dict[str, str]] = []

    def get_messages_snapshot(self) -> list[dict[str, str]]:
        return list(self.messages)

    def bind_memory_path(self, path: Path) -> None:
        self.bound_paths.append(str(path))

    def persist_turn(self, role: str, content: str) -> None:
        self.persisted.append({"role": role, "content": content})

    def begin_fresh_session(self, wipe_persistent: bool = False) -> None:
        self.fresh_sessions += 1
        self.messages.append({"role": "system", "content": "=== New session ==="})


class _DummyController:
    def __init__(self, runtime: ActiveUserRuntime) -> None:
        self.user_runtime = runtime
        self.tts = _DummyTTS()
        self.agent_brain = _DummyBrain()
        self.chat_state = _DummyChatState()
        self.style_mgr = type("StyleManager", (), {"active_filename": ""})()
        self.session_meta = ""
        self.stage_meta = ""
        self.runtime_mode = ""
        self.safe_logs: list[str] = []
        self._ui_queue: list[tuple[str, object]] = []

    def refresh_active_user_meta(self) -> None:
        pass

    def load_style_state(self):
        return type("StyleState", (), {"name": "default"})()

    def set_mode_indicator(self, text: str) -> None:
        pass

    def refresh_documents_view(self) -> None:
        pass

    def _refresh_chat_ui(self) -> None:
        pass

    def refresh_interaction_state(self) -> None:
        pass

    def safe_log(self, text: str) -> None:
        self.safe_logs.append(str(text))


class _OneMatchEngine:
    def __init__(self, payload: tuple) -> None:
        self.payload = payload

    def consume_last_voice_match(self):
        payload = self.payload
        self.payload = None
        return payload


@dataclass(frozen=True)
class VoiceIdentityDriftReport:
    success: bool
    baris_to_max_active_after_one: str
    baris_to_max_active_after_three: str
    max_to_baris_active_after_two: str
    max_to_baris_active_after_three: str
    baris_to_unknown_after_one: str
    baris_to_unknown_after_two: str
    baris_to_unknown_after_three: str
    unknown_count_after_one: int
    unknown_count_after_two: int
    baris_to_max_candidate_count_after_one: int
    baris_to_max_candidate_count_after_two: int
    max_switch_notice: str
    baris_return_after_uncertainty: str
    baris_candidate_reset_after_alice: str
    baris_stays_admin_after_alice_two: str
    fresh_sessions_after_single_admin_mismatch: int
    checks: dict[str, bool]


def _accepted(user_id: str, *, admin: bool = False) -> tuple:
    return (
        user_id,
        0.865 if admin else 0.80,
        {
            "best_user": user_id,
            "best_score": 0.865 if admin else 0.80,
            "second_score": 0.681 if admin else 0.68,
            "margin": 0.184 if admin else 0.12,
            "best_is_admin": admin,
            "threshold": 0.70 if admin else 0.74,
            "margin_threshold": 0.08 if admin else 0.08,
            "final_decision": "accepted_admin" if admin else "accepted_public",
            "reason": "accepted_admin" if admin else "accepted_public",
        },
    )


def _unknown() -> tuple:
    return (
        None,
        0.50,
        {
            "best_user": "none",
            "best_score": 0.50,
            "second_score": 0.40,
            "margin": 0.10,
            "best_is_admin": False,
            "threshold": 0.74,
            "margin_threshold": 0.08,
            "final_decision": "unknown",
            "reason": "score_below_low_threshold",
        },
    )


def _runtime(data_dir: Path) -> ActiveUserRuntime:
    runtime = ActiveUserRuntime(data_dir, llm_client=None, admin_user_id="admin_baris", admin_name="Baris")
    runtime.request_typed_user_switch("Max")
    return runtime


def _apply(controller: _DummyController, payload: tuple) -> None:
    _apply_voice_identity_match(controller, _OneMatchEngine(payload))


def run_smoke() -> VoiceIdentityDriftReport:
    with tempfile.TemporaryDirectory(prefix="piper_voice_drift_") as tmp:
        root = Path(tmp)

        # ── Scenario A: admin Baris → known candidate Max ──
        runtime_a = _runtime(root / "baris_to_max")
        runtime_a.switch_active_user("admin_baris")
        controller_a = _DummyController(runtime_a)
        _apply(controller_a, _accepted("max"))
        baris_to_max_after_one = runtime_a.active_profile().user_id
        fresh_after_one = controller_a.chat_state.fresh_sessions
        tracker_a1 = controller_actions._voice_drift_tracker(controller_a)
        candidate_count_after_one = int(tracker_a1.get("candidate_count") or 0)
        _apply(controller_a, _accepted("max"))
        tracker_a2 = controller_actions._voice_drift_tracker(controller_a)
        candidate_count_after_two = int(tracker_a2.get("candidate_count") or 0)
        _apply(controller_a, _accepted("max"))
        baris_to_max_after_three = runtime_a.active_profile().user_id
        max_switch_notice = str(getattr(controller_a, "_pending_voice_identity_notice", "") or "")

        # ── Scenario B: Max → admin Baris ──
        runtime_b = _runtime(root / "max_to_baris")
        runtime_b.switch_active_user("max")
        controller_b = _DummyController(runtime_b)
        _apply(controller_b, _accepted("baris", admin=True))
        _apply(controller_b, _accepted("baris", admin=True))
        max_to_baris_after_two = runtime_b.active_profile().user_id
        _apply(controller_b, _accepted("baris", admin=True))
        max_to_baris_after_three = runtime_b.active_profile().user_id

        # ── Scenario C: admin Baris → unknown ──
        runtime_c = _runtime(root / "baris_to_unknown")
        runtime_c.switch_active_user("admin_baris")
        controller_c = _DummyController(runtime_c)
        _apply(controller_c, _unknown())
        baris_to_unknown_after_one = runtime_c.active_profile().user_id
        tracker_c1 = controller_actions._voice_drift_tracker(controller_c)
        unknown_count_after_one = int(tracker_c1.get("unknown_count") or 0)
        _apply(controller_c, _unknown())
        baris_to_unknown_after_two = runtime_c.active_profile().user_id
        tracker_c2 = controller_actions._voice_drift_tracker(controller_c)
        unknown_count_after_two = int(tracker_c2.get("unknown_count") or 0)
        _apply(controller_c, _unknown())
        baris_to_unknown_after_three = runtime_c.active_profile().user_id

        # ── Scenario D: one uncertainty then admin match → tracker resets ──
        runtime_d = _runtime(root / "baris_return")
        runtime_d.switch_active_user("admin_baris")
        controller_d = _DummyController(runtime_d)
        _apply(controller_d, _unknown())
        _apply(controller_d, _accepted("baris", admin=True))
        baris_return_after_uncertainty = runtime_d.active_profile().user_id

        # ── Scenario E: candidate Max once, then candidate Alice ──
        runtime_e = _runtime(root / "baris_candidate_switch")
        runtime_e.switch_active_user("admin_baris")
        controller_e = _DummyController(runtime_e)
        _apply(controller_e, _accepted("max"))
        tracker_e1 = controller_actions._voice_drift_tracker(controller_e)
        baris_candidate_reset_after_alice = str(tracker_e1.get("candidate_user_id") or "")
        _apply(controller_e, _accepted("alice"))
        _apply(controller_e, _accepted("alice"))
        baris_stays_admin_after_alice_two = runtime_e.active_profile().user_id

    checks = {
        "baris_to_max_waits_after_first_mismatch": baris_to_max_after_one == "admin_baris",
        "baris_to_max_waits_for_three": baris_to_max_after_three == "max",
        "candidate_count_after_one": candidate_count_after_one == 1,
        "candidate_count_after_two": candidate_count_after_two == 2,
        "single_admin_mismatch_preserves_current_session": fresh_after_one == 0,
        "max_to_baris_waits_for_three": max_to_baris_after_two == "max"
        and max_to_baris_after_three == "admin_baris",
        "baris_to_unknown_after_one": baris_to_unknown_after_one == "admin_baris",
        "baris_to_unknown_after_two": baris_to_unknown_after_two == "admin_baris",
        "baris_to_unknown_after_three": baris_to_unknown_after_three == "unknown",
        "unknown_count_after_one": unknown_count_after_one == 1,
        "unknown_count_after_two": unknown_count_after_two == 2,
        "single_uncertainty_then_admin_match_stays_admin": baris_return_after_uncertainty == "admin_baris",
        "candidate_switch_resets_count": baris_candidate_reset_after_alice == "max",
        "candidate_change_keeps_admin": baris_stays_admin_after_alice_two == "admin_baris",
        "voice_events_use_event_block": "[VOICE IDENTITY EVENT]" in max_switch_notice,
    }
    return VoiceIdentityDriftReport(
        success=all(checks.values()),
        baris_to_max_active_after_one=baris_to_max_after_one,
        baris_to_max_active_after_three=baris_to_max_after_three,
        max_to_baris_active_after_two=max_to_baris_after_two,
        max_to_baris_active_after_three=max_to_baris_after_three,
        baris_to_unknown_after_one=baris_to_unknown_after_one,
        baris_to_unknown_after_two=baris_to_unknown_after_two,
        baris_to_unknown_after_three=baris_to_unknown_after_three,
        unknown_count_after_one=unknown_count_after_one,
        unknown_count_after_two=unknown_count_after_two,
        baris_to_max_candidate_count_after_one=candidate_count_after_one,
        baris_to_max_candidate_count_after_two=candidate_count_after_two,
        max_switch_notice=max_switch_notice,
        baris_return_after_uncertainty=baris_return_after_uncertainty,
        baris_candidate_reset_after_alice=baris_candidate_reset_after_alice,
        baris_stays_admin_after_alice_two=baris_stays_admin_after_alice_two,
        fresh_sessions_after_single_admin_mismatch=fresh_after_one,
        checks=checks,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Voice identity drift smoke test.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"SUCCESS: {report.success}")
        for name, value in report.checks.items():
            print(f"  {name}: {value}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
