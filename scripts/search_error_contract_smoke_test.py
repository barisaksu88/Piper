from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from types import SimpleNamespace

ROOT_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG  # noqa: E402
from core.contracts import RuntimeContextPack  # noqa: E402
from core.services.context_pack_renderer import ContextPackRenderer  # noqa: E402
from core.orchestrator_phases import phase_persona, phase_reporter, phase_search  # noqa: E402
from core.runtime_control import CancellationToken  # noqa: E402
from core.search_contracts import (  # noqa: E402
    SEARCH_FAILURE_REPORTER_INSTRUCTION,
    build_background_search_content,
)
import core.orchestrator_phases as orchestrator_phases  # noqa: E402
import tools.search as search_module  # noqa: E402


class DummyQueue:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def put(self, item) -> None:
        self.events.append(item)


class DummyStatsCollector:
    def __init__(self) -> None:
        self.defer_called = False
        self.reporter_query = ""
        self.outcome = ""
        self.outcome_detail = ""

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

    def note_reporter_query(self, state, query: str) -> None:
        del state
        self.reporter_query = str(query or "")

    def finalize_outcome(self, state, *, outcome=None, detail: str = "") -> None:
        del state
        self.outcome = str(outcome or "")
        self.outcome_detail = str(detail or "")


class DummyPromptContext:
    def build_persona_pack(self, **kwargs):
        return {"kwargs": kwargs}

    def apply_context_arbitration(self, pack, **kwargs):
        del kwargs
        return pack

    def to_prompt_context(self, pack):
        return pack


class DummyChat:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages = list(messages or [])
        self.replacements: list[tuple[str, dict]] = []

    def replace_last_assistant_content(self, content: str) -> None:
        del content

    def replace_last_system_message(self, raw_content: str, new_msg: dict) -> None:
        self.replacements.append((raw_content, dict(new_msg)))
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].get("role") == "system" and self.messages[index].get("content") == raw_content:
                self.messages[index] = dict(new_msg)
                return


class FailingReporterLLM:
    def __init__(self) -> None:
        self.generate_called = False

    def generate(self, *args, **kwargs):
        del args, kwargs
        self.generate_called = True
        raise AssertionError("Reporter LLM should not be called for search backend errors.")


class DummyOrc:
    def __init__(self, *, messages: list[dict] | None = None) -> None:
        self.cancel_token = CancellationToken()
        self.turn_stats = object()
        self.stats_collector = DummyStatsCollector()
        self.route_decision = {"decision": "SEARCH", "card": {"query": "latest Python 3.13 news"}}
        self.user_msg = "search the web for latest Python 3.13 news"
        self.knowledge_enabled = True
        self.ss = SimpleNamespace(overlay="", tts_voice="af_heart", tts_speed=0.85)
        self.prompt_context = DummyPromptContext()
        self.ui = DummyQueue()
        self.chat = DummyChat(messages)
        self.next_stage = ""
        self.search_in_flight_count = 0
        self.cancel_retain_count = 0
        self.signals: list[dict] = []
        self.llm = FailingReporterLLM()
        self.latest_search_failed = False
        self.latest_search_error = ""
        self.latest_search_summary = ""
        self.reporter_just_ran = False
        self.status_updates: list[str] = []

    def get_context(self):
        return list(self.chat.messages)

    def _update_status(self, **kwargs) -> None:
        self.status_updates.append(str(kwargs.get("mode") or ""))

    def raise_if_cancelled(self) -> None:
        self.cancel_token.raise_if_cancelled()

    def emit_runtime_signal(self, signal) -> None:
        self.signals.append(dict(signal or {}))

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


@dataclass(frozen=True)
class SearchErrorContractReport:
    success: bool
    search_event_error: bool
    search_event_data: str
    defer_called: bool
    search_in_flight_count: int
    cancel_retain_count: int
    reporter_outcome: str
    reporter_detail: str
    reporter_llm_called: bool
    reporter_summary_present: bool
    consumed_marker_present: bool
    runtime_status_failed: bool
    persona_failure_reply: str
    persona_llm_called: bool


def _run_phase_search_error_case() -> tuple[DummyOrc, dict]:
    original_prompt_builder = orchestrator_phases.PromptBuilder
    original_build_persona_messages = orchestrator_phases.build_persona_messages
    original_stream = orchestrator_phases._stream_or_capture_persona_answer_text_only
    original_consume = orchestrator_phases._consume_pipeline_stream_metrics
    original_sanitize = orchestrator_phases.sanitize_persona_output
    original_debug_prompts = CFG.DEBUG_LLM_PROMPTS
    original_search = search_module.perform_search

    orc = DummyOrc()
    try:
        orchestrator_phases.PromptBuilder = SimpleNamespace(build_persona_prompt=lambda prompt_context: "system")
        orchestrator_phases.build_persona_messages = lambda **kwargs: [{"role": "system", "content": "search preview"}]
        orchestrator_phases._stream_or_capture_persona_answer_text_only = (
            lambda orc, messages, allow_recall=False: ("Preview answer", False)
        )
        orchestrator_phases._consume_pipeline_stream_metrics = lambda orc: {}
        orchestrator_phases.sanitize_persona_output = lambda text, **kwargs: str(text or "").strip()
        CFG.DEBUG_LLM_PROMPTS = False

        def _fake_search(query: str, data_dir, log_callback=None, cancel_token=None):
            del query, data_dir, cancel_token
            if log_callback:
                log_callback("[fake-search] forcing provider rate limit")
            return "Search Error: 403 Ratelimit"

        search_module.perform_search = _fake_search
        phase_search(orc)

        deadline = time.time() + 3.0
        event_payload: dict = {}
        while time.time() < deadline:
            for kind, payload in orc.ui.events:
                if kind == "search_result":
                    event_payload = dict(payload or {})
                    break
            if event_payload:
                break
            time.sleep(0.02)
        return orc, event_payload
    finally:
        orchestrator_phases.PromptBuilder = original_prompt_builder
        orchestrator_phases.build_persona_messages = original_build_persona_messages
        orchestrator_phases._stream_or_capture_persona_answer_text_only = original_stream
        orchestrator_phases._consume_pipeline_stream_metrics = original_consume
        orchestrator_phases.sanitize_persona_output = original_sanitize
        CFG.DEBUG_LLM_PROMPTS = original_debug_prompts
        search_module.perform_search = original_search


def _run_phase_reporter_error_case() -> DummyOrc:
    raw = build_background_search_content(
        "latest Python 3.13 news",
        "Search Error: 403 Ratelimit",
        failed=True,
    )
    messages = [
        {"role": "system", "content": raw, "hidden": True},
        {"role": "system", "content": SEARCH_FAILURE_REPORTER_INSTRUCTION, "hidden": True},
    ]
    orc = DummyOrc(messages=messages)
    phase_reporter(orc)
    return orc


def _run_phase_persona_error_case() -> DummyOrc:
    original_fire_hooks = orchestrator_phases.fire_hooks
    orc = _run_phase_reporter_error_case()
    try:
        orchestrator_phases.fire_hooks = lambda *args, **kwargs: None
        phase_persona(orc)
    finally:
        orchestrator_phases.fire_hooks = original_fire_hooks
    return orc


def main() -> int:
    search_orc, event_payload = _run_phase_search_error_case()
    reporter_orc = _run_phase_reporter_error_case()
    persona_orc = _run_phase_persona_error_case()
    runtime_message = ContextPackRenderer().render_runtime_context_message(
        RuntimeContextPack(
            previous_route="SEARCH",
            search_query="latest Python 3.13 news",
            reporter_just_ran=True,
            search_failed=True,
            search_error="403 Ratelimit",
        )
    )
    replacement_text = "\n".join(
        str(new_msg.get("content") or "")
        for _, new_msg in reporter_orc.chat.replacements
    )
    search_event_data = str(event_payload.get("data") or "")
    persona_reply = "".join(
        str(payload.get("text") or "")
        for kind, payload in persona_orc.ui.events
        if kind == "assistant_stream_delta" and isinstance(payload, dict)
    )
    success = (
        bool(event_payload.get("error"))
        and "403 Ratelimit" in search_event_data
        and search_orc.stats_collector.defer_called
        and search_orc.search_in_flight_count == 0
        and search_orc.cancel_retain_count == 0
        and reporter_orc.stats_collector.outcome == "FAILED"
        and "403 Ratelimit" in reporter_orc.stats_collector.outcome_detail
        and not reporter_orc.llm.generate_called
        and "[SEARCH SUMMARY FOR 'latest Python 3.13 news']" in replacement_text
        and "Verified web findings: none." in replacement_text
        and "[SEARCH REPORT CONSUMED FOR 'latest Python 3.13 news']" in replacement_text
        and "Execution status: SEARCH FAILED" in runtime_message
        and "Verified web findings from this attempt: none." in persona_reply
        and "HTTP 403 Ratelimit" in persona_reply
        and "assume" not in persona_reply.casefold()
        and not persona_orc.llm.generate_called
    )
    report = SearchErrorContractReport(
        success=bool(success),
        search_event_error=bool(event_payload.get("error")),
        search_event_data=search_event_data,
        defer_called=bool(search_orc.stats_collector.defer_called),
        search_in_flight_count=int(search_orc.search_in_flight_count),
        cancel_retain_count=int(search_orc.cancel_retain_count),
        reporter_outcome=str(reporter_orc.stats_collector.outcome or ""),
        reporter_detail=str(reporter_orc.stats_collector.outcome_detail or ""),
        reporter_llm_called=bool(reporter_orc.llm.generate_called),
        reporter_summary_present="[SEARCH SUMMARY FOR 'latest Python 3.13 news']" in replacement_text,
        consumed_marker_present="[SEARCH REPORT CONSUMED FOR 'latest Python 3.13 news']" in replacement_text,
        runtime_status_failed="Execution status: SEARCH FAILED" in runtime_message,
        persona_failure_reply=persona_reply,
        persona_llm_called=bool(persona_orc.llm.generate_called),
    )
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
