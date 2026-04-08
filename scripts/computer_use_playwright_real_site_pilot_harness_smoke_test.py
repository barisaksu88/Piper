from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from config import data_state_path


_PROMPTS = (
    ("iana_title", "Open iana.org/domains/reserved in the browser and tell me the page title."),
    ("iana_heading", "Open iana.org/domains/reserved in the browser and tell me the main heading."),
    ("apache_title", "Open apache.org/licenses/LICENSE-2.0 in the browser and tell me the page title."),
    ("apache_heading", "Open apache.org/licenses/LICENSE-2.0 in the browser and tell me the main heading."),
    ("w3_title", "Open w3.org/TR/PNG/ in the browser and tell me the page title."),
    ("w3_heading", "Open w3.org/TR/PNG/ in the browser and tell me the main heading."),
    ("python_title", "Open docs.python.org/3/license.html in the browser and tell me the page title."),
    ("python_heading", "Open docs.python.org/3/license.html in the browser and tell me the main heading."),
    ("rfc_title", "Open rfc-editor.org/rfc/rfc2606.html in the browser and tell me the page title."),
)


@dataclass(frozen=True)
class RealSitePilotHarnessReport:
    ready: bool
    success: bool
    replies: dict[str, str]
    timed_out_keys: list[str]
    outcomes: dict[str, str]
    stage_goals: dict[str, str]


def _stats_lines(data_dir) -> list[dict]:
    stats_path = data_dir / "stats.jsonl"
    if not stats_path.exists():
        return []
    rows: list[dict] = []
    for line in stats_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def run_smoke(*, timeout: float, keep_data_copy: bool) -> RealSitePilotHarnessReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    boot = harness.start()
    harness.chat_state.clear()
    replies: dict[str, str] = {}
    timed_out_keys: list[str] = []
    outcomes: dict[str, str] = {}
    stage_goals: dict[str, str] = {}
    try:
        if boot.ready:
            for key, prompt in _PROMPTS:
                result = harness.send_text(prompt, timeout_s=timeout)
                replies[key] = result.assistant_text
                if result.timed_out:
                    timed_out_keys.append(key)

            stats = _stats_lines(harness.data_dir)
            relevant_stats = stats[-len(_PROMPTS) :] if len(stats) >= len(_PROMPTS) else stats
            for (key, _prompt), stat in zip(_PROMPTS, relevant_stats):
                outcomes[key] = str(stat.get("outcome") or "")
                stages = stat.get("stages") or []
                first_stage = stages[0] if stages and isinstance(stages[0], dict) else {}
                stage_goals[key] = str(first_stage.get("stage_goal") or "")
    finally:
        harness.close()

    checks = (
        boot.ready,
        not timed_out_keys,
        outcomes.get("iana_title") == "VERIFIED",
        outcomes.get("iana_heading") == "VERIFIED",
        outcomes.get("apache_title") == "VERIFIED",
        outcomes.get("apache_heading") == "VERIFIED",
        outcomes.get("w3_title") == "VERIFIED",
        outcomes.get("w3_heading") == "VERIFIED",
        outcomes.get("python_title") == "VERIFIED",
        outcomes.get("python_heading") == "VERIFIED",
        outcomes.get("rfc_title") == "VERIFIED",
        "iana-managed reserved domains" in replies.get("iana_title", "").lower(),
        "iana-managed reserved domains" in replies.get("iana_heading", "").lower(),
        "apache license, version 2.0" in replies.get("apache_title", "").lower(),
        "apache license, version 2.0" in replies.get("apache_heading", "").lower(),
        "portable network graphics" in replies.get("w3_title", "").lower(),
        "portable network graphics" in replies.get("w3_heading", "").lower(),
        "history and license" in replies.get("python_title", "").lower(),
        "history and license" in replies.get("python_heading", "").lower(),
        "rfc 2606" in replies.get("rfc_title", "").lower(),
        "systems indicate" not in " ".join(replies.values()).lower(),
    )
    success = bool(all(checks))
    return RealSitePilotHarnessReport(
        ready=bool(boot.ready),
        success=success,
        replies=replies,
        timed_out_keys=timed_out_keys,
        outcomes=outcomes,
        stage_goals=stage_goals,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the live-site COMPUTER_USE pilot against narrowly allowlisted real read-only pages."
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"TIMED_OUT_KEYS: {report.timed_out_keys}")
        print(f"OUTCOMES: {report.outcomes}")
        print(f"STAGE_GOALS: {report.stage_goals}")
        print(f"REPLIES: {report.replies}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
