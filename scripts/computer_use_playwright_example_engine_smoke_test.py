from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.computer_use_engine import ComputerUseEngine


@dataclass(frozen=True)
class ComputerUsePlaywrightExampleEngineReport:
    success: bool
    title_ok: bool
    heading_ok: bool
    inventory_ok: bool
    allowlist_block_ok: bool
    start_url: str
    final_title: str
    heading_text: str


def _run_action(engine: ComputerUseEngine, payload: dict) -> dict:
    return engine.exec_browser_op(json.dumps(payload, ensure_ascii=False))


def run_smoke() -> ComputerUsePlaywrightExampleEngineReport:
    temp_root = Path(tempfile.mkdtemp(prefix="piper-computer-use-example-"))
    try:
        data_dir = temp_root / "data"
        workspace = temp_root / "workspace"
        data_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        engine = ComputerUseEngine(data_dir=data_dir, workspace=workspace)
        start_url = "https://example.com"
        goto_result = _run_action(
            engine,
            {
                "action": "goto_url",
                "url": start_url,
                "allowed_domains": ["example.com"],
            },
        )
        capture_result = _run_action(engine, {"action": "capture_state"})
        heading_result = _run_action(engine, {"action": "extract_text", "selector": "h1"})
        allowlist_block_result = _run_action(
            engine,
            {
                "action": "goto_url",
                "url": "https://openai.com",
                "allowed_domains": ["openai.com"],
            },
        )

        inventory = capture_result.get("element_inventory") or []
        title_ok = (
            goto_result.get("status") == "EXECUTED"
            and capture_result.get("status") == "EXECUTED"
            and str(capture_result.get("title") or "").strip() == "Example Domain"
        )
        heading_text = str(heading_result.get("extracted_text") or "").strip()
        heading_ok = heading_result.get("status") == "EXECUTED" and heading_text == "Example Domain"
        inventory_ok = any(
            isinstance(item, dict)
            and str(item.get("selector") or "").strip().lower() == "h1"
            and "Example Domain".lower() in str(item.get("text") or "").strip().lower()
            for item in inventory
        )
        allowlist_block_ok = (
            allowlist_block_result.get("status") == "BLOCKED"
            and "pilot allowlist" in str(allowlist_block_result.get("summary") or "").lower()
        )
        success = bool(title_ok and heading_ok and inventory_ok and allowlist_block_ok)
        return ComputerUsePlaywrightExampleEngineReport(
            success=success,
            title_ok=bool(title_ok),
            heading_ok=bool(heading_ok),
            inventory_ok=bool(inventory_ok),
            allowlist_block_ok=bool(allowlist_block_ok),
            start_url=start_url,
            final_title=str(capture_result.get("title") or ""),
            heading_text=heading_text,
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test live Playwright browser automation against example.com.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"TITLE_OK: {report.title_ok}")
        print(f"HEADING_OK: {report.heading_ok}")
        print(f"INVENTORY_OK: {report.inventory_ok}")
        print(f"ALLOWLIST_BLOCK_OK: {report.allowlist_block_ok}")
        print(f"START_URL: {report.start_url}")
        print(f"FINAL_TITLE: {report.final_title}")
        print(f"HEADING_TEXT: {report.heading_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
