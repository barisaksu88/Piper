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
from scripts.computer_use_fixture_server import running_fixture_server


@dataclass(frozen=True)
class PlaywrightLocalhostEngineSmokeReport:
    success: bool
    backend_ok: bool
    title_ok: bool
    extract_ok: bool
    type_ok: bool
    click_ok: bool
    wait_ok: bool
    download_ok: bool
    scope_block_ok: bool
    downloaded_rel: str
    final_url: str


def _run_action(engine: ComputerUseEngine, payload: dict) -> dict:
    return engine.exec_browser_op(json.dumps(payload, ensure_ascii=False))


def run_smoke() -> PlaywrightLocalhostEngineSmokeReport:
    temp_root = Path(tempfile.mkdtemp(prefix="piper-playwright-localhost-smoke-"))
    try:
        data_dir = temp_root / "data"
        workspace = temp_root / "workspace"
        data_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        engine = ComputerUseEngine(data_dir=data_dir, workspace=workspace)
        with running_fixture_server() as base_url:
            start_url = f"{base_url}/index.html"
            allowed_domains = ["127.0.0.1"]

            goto_result = _run_action(
                engine,
                {
                    "action": "goto_url",
                    "url": start_url,
                    "allowed_domains": allowed_domains,
                },
            )
            capture_result = _run_action(engine, {"action": "capture_state"})
            extract_result = _run_action(engine, {"action": "extract_text", "selector": "#status"})
            type_result = _run_action(engine, {"action": "type_text", "selector": "#email", "text": "alice@example.com"})
            click_result = _run_action(engine, {"action": "click", "selector": "#next-link"})
            wait_result = _run_action(engine, {"action": "wait_for", "selector": "#destination"})
            goto_download_page = _run_action(
                engine,
                {
                    "action": "goto_url",
                    "url": start_url,
                    "allowed_domains": allowed_domains,
                },
            )
            download_result = _run_action(
                engine,
                {
                    "action": "download",
                    "selector": "#download-link",
                    "download_dir": "browser_downloads",
                },
            )
            scope_block_result = _run_action(
                engine,
                {
                    "action": "goto_url",
                    "url": start_url,
                    "allowed_domains": ["not-localhost.invalid"],
                },
            )

        downloaded_rel = str(download_result.get("saved_path") or "")
        downloaded_path = workspace / downloaded_rel if downloaded_rel else workspace / "missing"

        backend_ok = (
            goto_result.get("status") == "EXECUTED"
            and str(goto_result.get("backend") or "") == "playwright"
            and str(capture_result.get("backend") or "") == "playwright"
        )
        title_ok = (
            capture_result.get("status") == "EXECUTED"
            and str(capture_result.get("title") or "") == "Browser Fixture Home"
        )
        extract_ok = (
            extract_result.get("status") == "EXECUTED"
            and str(extract_result.get("extracted_text") or "") == "Hello from Piper fixture"
        )
        type_ok = (
            type_result.get("status") == "EXECUTED"
            and str(type_result.get("field_value") or "") == "alice@example.com"
        )
        click_ok = (
            click_result.get("status") == "EXECUTED"
            and str(click_result.get("title") or "") == "Browser Fixture Next"
            and str(click_result.get("current_url") or "").endswith("/next.html")
        )
        wait_ok = wait_result.get("status") == "EXECUTED"
        download_ok = (
            goto_download_page.get("status") == "EXECUTED"
            and download_result.get("status") == "EXECUTED"
            and downloaded_path.exists()
            and downloaded_path.read_text(encoding="utf-8").strip() == "fixture download ok"
        )
        scope_block_ok = (
            scope_block_result.get("status") == "BLOCKED"
            and "outside the allowed browser scope" in str(scope_block_result.get("summary") or "").lower()
        )
        success = all((backend_ok, title_ok, extract_ok, type_ok, click_ok, wait_ok, download_ok, scope_block_ok))

        return PlaywrightLocalhostEngineSmokeReport(
            success=bool(success),
            backend_ok=bool(backend_ok),
            title_ok=bool(title_ok),
            extract_ok=bool(extract_ok),
            type_ok=bool(type_ok),
            click_ok=bool(click_ok),
            wait_ok=bool(wait_ok),
            download_ok=bool(download_ok),
            scope_block_ok=bool(scope_block_ok),
            downloaded_rel=downloaded_rel,
            final_url=str(click_result.get("current_url") or ""),
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test the Playwright localhost browser engine path.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"BACKEND_OK: {report.backend_ok}")
        print(f"TITLE_OK: {report.title_ok}")
        print(f"EXTRACT_OK: {report.extract_ok}")
        print(f"TYPE_OK: {report.type_ok}")
        print(f"CLICK_OK: {report.click_ok}")
        print(f"WAIT_OK: {report.wait_ok}")
        print(f"DOWNLOAD_OK: {report.download_ok}")
        print(f"SCOPE_BLOCK_OK: {report.scope_block_ok}")
        print(f"DOWNLOADED_REL: {report.downloaded_rel}")
        print(f"FINAL_URL: {report.final_url}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
