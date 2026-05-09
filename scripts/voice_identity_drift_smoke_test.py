from __future__ import annotations

import json
import sys
import tempfile
import types
from dataclasses import asdict, dataclass
from pathlib import Path

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
    baris_to_unknown_after_three: str
    fresh_sessions_after_single_admin_mismatch: int
    baris_recovered_after_admin_revocation: str
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
            "threshold": 0.82 if admin else 0.74,
            "margin_threshold": 0.14 if admin else 0.08,
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

        runtime_a = _runtime(root / "baris_to_max")
        runtime_a.switch_active_user("admin_baris")
        controller_a = _DummyController(runtime_a)
        _apply(controller_a, _accepted("max"))
        baris_to_max_after_one = runtime_a.active_profile().user_id
        fresh_after_one = controller_a.chat_state.fresh_sessions
        _apply(controller_a, _accepted("max"))
        _apply(controller_a, _accepted("max"))
        baris_to_max_after_three = runtime_a.active_profile().user_id
        baris_to_max_notice = str(getattr(controller_a, "_pending_voice_identity_notice", "") or "")

        runtime_b = _runtime(root / "max_to_baris")
        runtime_b.switch_active_user("max")
        controller_b = _DummyController(runtime_b)
        _apply(controller_b, _accepted("baris", admin=True))
        _apply(controller_b, _accepted("baris", admin=True))
        max_to_baris_after_two = runtime_b.active_profile().user_id
        _apply(controller_b, _accepted("baris", admin=True))
        max_to_baris_after_three = runtime_b.active_profile().user_id
        max_to_baris_notice = str(getattr(controller_b, "_pending_voice_identity_notice", "") or "")

        runtime_c = _runtime(root / "baris_to_unknown")
        runtime_c.switch_active_user("admin_baris")
        controller_c = _DummyController(runtime_c)
        _apply(controller_c, _unknown())
        baris_to_unknown_after_one = runtime_c.active_profile().user_id
        _apply(controller_c, _unknown())
        _apply(controller_c, _unknown())
        baris_to_unknown_after_three = runtime_c.active_profile().user_id

        runtime_d = _runtime(root / "baris_return")
        runtime_d.switch_active_user("admin_baris")
        controller_d = _DummyController(runtime_d)
        _apply(controller_d, _unknown())
        _apply(controller_d, _accepted("baris", admin=True))
        baris_recovered_after_admin_revocation = runtime_d.active_profile().user_id

    checks = {
        "baris_to_max_revokes_admin_on_first_mismatch": baris_to_max_after_one == "unknown",
        "baris_to_max_waits_for_three": baris_to_max_after_three == "max",
        "single_admin_mismatch_hides_prior_admin_session": fresh_after_one == 1,
        "max_to_baris_waits_for_three": max_to_baris_after_two == "max"
        and max_to_baris_after_three == "admin_baris",
        "baris_to_unknown_revokes_admin_then_stays_unknown": baris_to_unknown_after_one == "unknown"
        and baris_to_unknown_after_three == "unknown",
        "baris_return_after_admin_revocation_recovers_immediately": baris_recovered_after_admin_revocation == "admin_baris",
        "voice_events_use_event_block": "[VOICE IDENTITY EVENT]" in baris_to_max_notice
        and "[VOICE IDENTITY EVENT]" in max_to_baris_notice,
    }
    return VoiceIdentityDriftReport(
        success=all(checks.values()),
        baris_to_max_active_after_one=baris_to_max_after_one,
        baris_to_max_active_after_three=baris_to_max_after_three,
        max_to_baris_active_after_two=max_to_baris_after_two,
        max_to_baris_active_after_three=max_to_baris_after_three,
        baris_to_unknown_after_one=baris_to_unknown_after_one,
        baris_to_unknown_after_three=baris_to_unknown_after_three,
        fresh_sessions_after_single_admin_mismatch=fresh_after_one,
        baris_recovered_after_admin_revocation=baris_recovered_after_admin_revocation,
        checks=checks,
    )


if __name__ == "__main__":
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2))
    raise SystemExit(0 if report.success else 1)
