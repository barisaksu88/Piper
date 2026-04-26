from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG  # noqa: E402
from core.orchestrator_phases import phase_search  # noqa: E402
from core.runtime_control import CancellationToken  # noqa: E402
import core.orchestrator_phases as orchestrator_phases  # noqa: E402


class DummyQueue:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def put(self, item) -> None:
        self.events.append(item)


class DummyStatsCollector:
    def __init__(self) -> None:
        self.defer_called = False

    def start_phase(self, state, phase_name: str) -> None:
        del state, phase_name

    def note_route(self, state, **kwargs) -> None:
        del state, kwargs

    def end_phase(self, state, phase_name: str) -> float:
        del state, phase_name
        return 0.0

    def note_tts_metrics(self, state, metrics) -> None:
        del state, metrics

    def defer_search_turn(self, state, *, cancel_token=None, fallback_owner=None) -> None:
        del state, cancel_token, fallback_owner
        self.defer_called = True


class DummyPromptContext:
    def build_persona_pack(self, **kwargs):
        return {"kwargs": kwargs}

    def apply_context_arbitration(self, pack, **kwargs):
        del kwargs
        return pack

    def to_prompt_context(self, pack):
        return pack


class DummyChat:
    def replace_last_assistant_content(self, content: str) -> None:
        del content


class DummyOrc:
    def __init__(self) -> None:
        self.cancel_token = CancellationToken()
        self.turn_stats = object()
        self.stats_collector = DummyStatsCollector()
        self.route_decision = {"decision": "SEARCH", "card": {"query": "latest Piper news"}}
        self.user_msg = "latest Piper news"
        self.knowledge_enabled = True
        self.ss = SimpleNamespace(overlay="", tts_voice="af_heart", tts_speed=0.85)
        self.prompt_context = DummyPromptContext()
        self.ui = DummyQueue()
        self.chat = DummyChat()
        self.next_stage = ""
        self.search_in_flight_count = 0
        self.cancel_retain_count = 0

    def raise_if_cancelled(self) -> None:
        self.cancel_token.raise_if_cancelled()

    def emit_runtime_signal(self, signal) -> None:
        del signal

    def _log_dashboard(self, text: str) -> None:
        self.ui.put(("status_widget_dashboard_activity", text))

    def retain_cancel_token(self, token) -> None:
        if token is not None:
            self.cancel_retain_count += 1

    def release_cancel_token(self, token) -> None:
        if token is not None and self.cancel_retain_count > 0:
            self.cancel_retain_count -= 1

    def retain_search_in_flight(self, query: str = "") -> None:
        del query
        self.search_in_flight_count += 1

    def release_search_in_flight(self) -> None:
        if self.search_in_flight_count > 0:
            self.search_in_flight_count -= 1


class FailingThread:
    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon

    def start(self) -> None:
        raise RuntimeError("forced thread start failure")


@dataclass(frozen=True)
class SearchThreadCleanupReport:
    success: bool
    error_text: str
    cancel_retain_count: int
    search_in_flight_count: int
    defer_called: bool
    next_stage: str


def main() -> int:
    original_prompt_builder = orchestrator_phases.PromptBuilder
    original_build_persona_messages = orchestrator_phases.build_persona_messages
    original_stream = orchestrator_phases._stream_or_capture_persona_answer_text_only
    original_consume = orchestrator_phases._consume_pipeline_stream_metrics
    original_sanitize = orchestrator_phases.sanitize_persona_output
    original_debug_prompts = CFG.DEBUG_LLM_PROMPTS
    original_thread_cls = orchestrator_phases.threading.Thread

    report: SearchThreadCleanupReport
    orc = DummyOrc()
    try:
        orchestrator_phases.PromptBuilder = SimpleNamespace(build_persona_prompt=lambda prompt_context: "system")
        orchestrator_phases.build_persona_messages = lambda **kwargs: [{"role": "system", "content": "search preview"}]
        orchestrator_phases._stream_or_capture_persona_answer_text_only = (
            lambda orc, messages, allow_recall=False: ("Preview answer", False)
        )
        orchestrator_phases._consume_pipeline_stream_metrics = lambda orc: {}
        orchestrator_phases.sanitize_persona_output = lambda text, **kwargs: str(text or "").strip()
        orchestrator_phases.threading.Thread = FailingThread
        CFG.DEBUG_LLM_PROMPTS = False

        error_text = ""
        try:
            phase_search(orc)
        except Exception as exc:
            error_text = str(exc)

        success = (
            "forced thread start failure" in error_text
            and orc.cancel_retain_count == 0
            and orc.search_in_flight_count == 0
            and not orc.stats_collector.defer_called
            and orc.next_stage == ""
        )
        report = SearchThreadCleanupReport(
            success=bool(success),
            error_text=error_text,
            cancel_retain_count=orc.cancel_retain_count,
            search_in_flight_count=orc.search_in_flight_count,
            defer_called=bool(orc.stats_collector.defer_called),
            next_stage=str(orc.next_stage or ""),
        )
    finally:
        orchestrator_phases.PromptBuilder = original_prompt_builder
        orchestrator_phases.build_persona_messages = original_build_persona_messages
        orchestrator_phases._stream_or_capture_persona_answer_text_only = original_stream
        orchestrator_phases._consume_pipeline_stream_metrics = original_consume
        orchestrator_phases.sanitize_persona_output = original_sanitize
        orchestrator_phases.threading.Thread = original_thread_cls
        CFG.DEBUG_LLM_PROMPTS = original_debug_prompts

    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
