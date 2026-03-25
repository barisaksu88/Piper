from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.conversation_compressor import ConversationCompressor  # noqa: E402


class _StubLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def generate(self, messages, temperature: float = 0.1, cancel_token=None):
        self.calls += 1
        return self.response


@dataclass(frozen=True)
class ConversationCompressorReport:
    success: bool
    existing_summary_injected: bool
    under_budget_no_llm_call: bool
    under_budget_summary_updated: bool
    control_noise_removed: bool
    over_budget_used_llm: bool
    over_budget_summary_trimmed: bool
    persistence_round_trip: bool


def _message(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def run_smoke() -> ConversationCompressorReport:
    compressor = ConversationCompressor(token_budget=20)

    short_history = [
        _message("user", "Hello there."),
        _message("assistant", "Hi."),
    ]
    short_result = compressor.compress_history(
        history=short_history,
        existing_summary=(
            "User prefers concise replies.\n"
            "System: === New session\n"
            "[SEARCH REPORT CONSUMED FOR 'old query']"
        ),
        max_turns=10,
        llm=None,
    )
    existing_summary_injected = (
        len(short_result.history) == 3
        and str(short_result.history[0].get("content") or "").startswith(
            "[EARLIER CONVERSATION SUMMARY - MAY OMIT DETAIL]\nUser prefers concise replies."
        )
        and "System:" not in str(short_result.history[0].get("content") or "")
        and "[SEARCH REPORT CONSUMED FOR 'old query']" not in str(short_result.history[0].get("content") or "")
    )

    no_llm = _StubLLM("should not be used")
    medium_history = [
        _message("system", "=== New session"),
        _message("user", "Turn 1"),
        _message("assistant", "Reply 1"),
        _message("system", "[SEARCH REPORT CONSUMED FOR 'old query']"),
        _message("user", "Turn 2"),
        _message("assistant", "Reply 2"),
        _message("user", "Turn 3"),
        _message("assistant", "Reply 3"),
    ]
    medium_result = compressor.compress_history(
        history=medium_history,
        existing_summary="",
        max_turns=4,
        llm=no_llm,
    )
    under_budget_no_llm_call = no_llm.calls == 0 and not medium_result.summarization_used
    under_budget_summary_updated = "User: Turn 1" in medium_result.summary and "Assistant: Reply 1" in medium_result.summary
    control_noise_removed = (
        "=== New session" not in medium_result.summary
        and "[SEARCH REPORT CONSUMED FOR 'old query']" not in medium_result.summary
        and "System:" not in medium_result.summary
    )

    llm = _StubLLM("User prefers concise replies and is debugging Piper.")
    long_history = [
        _message("user", "alpha beta gamma delta epsilon zeta eta theta iota kappa"),
        _message("assistant", "lambda mu nu xi omicron pi rho sigma tau upsilon"),
        _message("user", "phi chi psi omega alpha beta gamma delta epsilon"),
        _message("assistant", "long repeated context that should be compressed before persona"),
        _message("user", "latest question one"),
        _message("assistant", "latest answer one"),
    ]
    long_result = compressor.compress_history(
        history=long_history,
        existing_summary="",
        max_turns=2,
        llm=llm,
    )
    over_budget_used_llm = llm.calls == 1 and long_result.summarization_used
    over_budget_summary_trimmed = (
        long_result.summary == "User prefers concise replies and is debugging Piper."
        and str(long_result.history[0].get("content") or "").startswith(
            "[EARLIER CONVERSATION SUMMARY - MAY OMIT DETAIL]\nUser prefers concise replies"
        )
    )

    with tempfile.TemporaryDirectory(prefix="piper-conversation-summary-") as tmp:
        path = Path(tmp) / "conversation_summary.json"
        ConversationCompressor.save_summary(path, long_result.summary)
        loaded = ConversationCompressor.load_summary(path)
        persistence_round_trip = loaded == long_result.summary

    success = all(
        [
            existing_summary_injected,
            under_budget_no_llm_call,
            under_budget_summary_updated,
            control_noise_removed,
            over_budget_used_llm,
            over_budget_summary_trimmed,
            persistence_round_trip,
        ]
    )

    return ConversationCompressorReport(
        success=bool(success),
        existing_summary_injected=existing_summary_injected,
        under_budget_no_llm_call=under_budget_no_llm_call,
        under_budget_summary_updated=under_budget_summary_updated,
        control_noise_removed=control_noise_removed,
        over_budget_used_llm=over_budget_used_llm,
        over_budget_summary_trimmed=over_budget_summary_trimmed,
        persistence_round_trip=persistence_round_trip,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
