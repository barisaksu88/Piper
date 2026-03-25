from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.stats_collector import StatsCollector, TurnStatsState


@dataclass(frozen=True)
class StatsCollectorSmokeReport:
    success: bool
    record_count: int
    alert_count: int
    last_outcome: str
    report_preview: str


def _build_state(*, persona_ms: float, total_ms: float, outcome: str = "VERIFIED") -> TurnStatsState:
    state = TurnStatsState()
    state.decision = "CHAT"
    state.user_msg = "hello"
    state.phase_ms["route"] = 15.0
    state.phase_ms["persona"] = float(persona_ms)
    state.phase_ms["tts"] = 40.0
    state.phase_ms["total"] = float(total_ms)
    state.outcome = outcome
    return state


def run_smoke() -> StatsCollectorSmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-stats-") as tmp:
        data_dir = Path(tmp) / "data"
        collector = StatsCollector(
            data_dir / "stats.jsonl",
            data_dir / "debug" / "stats_alerts.log",
            rolling_window=12,
            min_samples_for_alerts=5,
        )
        for _ in range(6):
            collector.record_turn(_build_state(persona_ms=120.0, total_ms=180.0))
        collector.record_turn(_build_state(persona_ms=920.0, total_ms=980.0))

        stats_lines = collector.stats_path.read_text(encoding="utf-8").splitlines() if collector.stats_path.exists() else []
        alert_lines = collector.load_alert_lines(limit=20)
        last_payload = json.loads(stats_lines[-1]) if stats_lines else {}
        report = collector.build_readonly_report()
        success = (
            len(stats_lines) == 7
            and bool(alert_lines)
            and "field=persona" in "\n".join(alert_lines)
            and str(last_payload.get("outcome") or "") == "VERIFIED"
            and "Phase Latency" in report
            and "Recent Turns" in report
        )
        return StatsCollectorSmokeReport(
            success=bool(success),
            record_count=len(stats_lines),
            alert_count=len(alert_lines),
            last_outcome=str(last_payload.get("outcome") or ""),
            report_preview=report[:400],
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test the stats collector append-only store and outlier detection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"RECORD_COUNT: {report.record_count}")
        print(f"ALERT_COUNT: {report.alert_count}")
        print(f"LAST_OUTCOME: {report.last_outcome}")
        print(report.report_preview)
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
