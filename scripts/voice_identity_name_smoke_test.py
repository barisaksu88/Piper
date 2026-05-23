from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
from dataclasses import asdict, dataclass
from pathlib import Path


class _StubModule(types.ModuleType):
    def __getattr__(self, name: str):
        raise ImportError(f"{self.__name__} is stubbed in smoke test")


for _mod_name in ("resemblyzer", "sentence_transformers"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _StubModule(_mod_name)

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator_phases import _run_route_core  # noqa: E402
from memory.user_runtime import ActiveUserRuntime  # noqa: E402


class _FakeLLM:
    def generate(self, messages, **kwargs) -> str:
        return json.dumps(
            {
                "decision": "CHAT",
                "identity_intent": {
                    "is_introduction": True,
                    "name": "Ekin",
                    "confidence": "high",
                },
            }
        )


class _FakeStatsCollector:
    def note_route(self, *args, **kwargs) -> None:
        pass

    def end_phase(self, *args, **kwargs) -> None:
        pass


class _FakeDocumentMemory:
    def list_documents(self) -> list[dict]:
        return []


class _FakePromptContext:
    document_memory = _FakeDocumentMemory()

    def build_readonly_state_answer(self, text: str) -> str:
        return ""


class _FakeUi:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def put(self, event: tuple[str, object]) -> None:
        self.events.append(event)


class _FakeOrchestrator:
    def __init__(self, runtime: ActiveUserRuntime, history: list[dict[str, str]]) -> None:
        self._cfg = types.SimpleNamespace(user_runtime=runtime)
        self.llm = _FakeLLM()
        self.ui = _FakeUi()
        self.prompt_context = _FakePromptContext()
        self.stats_collector = _FakeStatsCollector()
        self.turn_stats = types.SimpleNamespace(decision="")
        self.cancel_token = None
        self.route_decision: dict = {}
        self.route_interceptor = ""
        self.next_stage = ""
        self.user_msg = ""
        self.synthetic_user_turn = False
        self.is_search_result = False
        self.identity_switch_notice = ""
        self.latest_route_error = ""
        self._history = history
        self.live_screen = None

    def get_context(self) -> list[dict[str, str]]:
        return list(self._history)

    def _update_status(self, **kwargs) -> None:
        pass

    def _log_dashboard(self, text: str) -> None:
        pass

    def emit_runtime_signal(self, payload: dict) -> None:
        pass

    def is_search_in_flight(self) -> bool:
        return False

    def current_search_query(self) -> str:
        return ""


@dataclass(frozen=True)
class VoiceIdentityNameReport:
    success: bool
    active_after_spelling_user_id: str
    active_after_spelling_name: str
    spoken_aliases: list[str]
    router_notice: str
    ui_events: list[str]
    checks: dict[str, bool]


def run_smoke() -> VoiceIdentityNameReport:
    with tempfile.TemporaryDirectory(prefix="piper_voice_name_") as raw_tmp:
        data_dir = Path(raw_tmp) / "data"
        runtime = ActiveUserRuntime(data_dir, llm_client=None, admin_user_id="admin_baris", admin_name="Baris")

        akin_intro = runtime.observe_typed_identity_hint("I'm Akin")
        akin_profile = runtime.active_profile()
        spelling_result = runtime.observe_typed_identity_hint("I will spell my name, e-k-i-n.")
        corrected_profile = runtime.active_profile()
        alias_matches = runtime.registry.matching_profiles("Akin")
        plain_ekin_duplicate = runtime.registry.profile_for_id("ekin")
        relation_result = runtime.observe_typed_identity_hint("I am his girlfriend. I am Turkish.")
        admin_graph = runtime.knowledge_manager_for("admin_baris").load_graph()
        partner_relation = any(
            str(edge.get("source") or "") == "person:user"
            and str(edge.get("target") or "") == f"person:{corrected_profile.user_id}"
            and str(edge.get("relation") or "") == "partner"
            for edge in (admin_graph.get("edges") or [])
            if isinstance(edge, dict)
        )

        duplicate_runtime = ActiveUserRuntime(
            Path(raw_tmp) / "duplicates",
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
        )
        duplicate_runtime.observe_typed_identity_hint("I'm Ekin, Baris's partner")
        duplicate_runtime.switch_active_user("unknown")
        duplicate_runtime.observe_typed_identity_hint("I'm Ekin, Baris's friend")
        duplicate_runtime.switch_active_user("unknown")
        fake_orc = _FakeOrchestrator(
            duplicate_runtime,
            [{"role": "user", "content": "I'm Ekin"}],
        )
        _run_route_core(fake_orc)
        rendered_ui_events = [
            json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, default=str)
            for kind, payload in fake_orc.ui.events
        ]
        leaked_chat_events = [
            item
            for item in rendered_ui_events
            if '"kind": "chat_append"' in item and ("[UI]" in item or "more than one person named" in item)
        ]
        checks = {
            "spelling_intro_switched_to_akin": bool(getattr(akin_intro, "switched", False)) and akin_profile.user_id == "akin",
            "spelling_correction_keeps_same_profile": corrected_profile.user_id == akin_profile.user_id,
            "spelling_correction_displays_ekin": corrected_profile.name == "Ekin",
            "spelling_correction_stores_akin_alias": "Akin" in set(corrected_profile.spoken_aliases),
            "spoken_akin_matches_corrected_profile": any(
                profile.user_id == corrected_profile.user_id and profile.name == "Ekin"
                for profile in alias_matches
            ),
            "spelling_correction_does_not_create_ekin_duplicate": plain_ekin_duplicate is None,
            "generic_girlfriend_relation_records_partner": relation_result is None and bool(partner_relation),
            "ambiguous_duplicate_ekin_sets_persona_notice": "[VOICE IDENTITY CLARIFICATION]" in fake_orc.identity_switch_notice,
            "ambiguous_duplicate_ekin_notice_strips_ui_prefix": "[UI]" not in fake_orc.identity_switch_notice,
            "ambiguous_duplicate_ekin_does_not_chat_append_raw_ui": not leaked_chat_events,
        }
        return VoiceIdentityNameReport(
            success=all(checks.values()),
            active_after_spelling_user_id=corrected_profile.user_id,
            active_after_spelling_name=corrected_profile.name,
            spoken_aliases=list(corrected_profile.spoken_aliases),
            router_notice=str(fake_orc.identity_switch_notice or ""),
            ui_events=rendered_ui_events,
            checks=checks,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Voice identity name spelling and UI-leak smoke test.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        for name, value in report.checks.items():
            print(f"  {name}: {value}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
