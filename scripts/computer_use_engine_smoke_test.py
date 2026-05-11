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
class ComputerUseEngineSmokeReport:
    success: bool
    title_ok: bool
    inventory_ok: bool
    extract_ok: bool
    body_extract_ok: bool
    topic_extract_ok: bool
    generic_topic_extract_ok: bool
    type_ok: bool
    has_text_click_ok: bool
    click_ok: bool
    go_back_ok: bool
    wait_ok: bool
    download_ok: bool
    navigation_download_ok: bool
    direct_url_download_ok: bool
    scope_block_ok: bool
    configured_allowlist_block_ok: bool
    downloaded_rel: str
    final_url: str


def _run_action(engine: ComputerUseEngine, payload: dict) -> dict:
    return engine.exec_browser_op(json.dumps(payload, ensure_ascii=False))


def run_smoke() -> ComputerUseEngineSmokeReport:
    temp_root = Path(tempfile.mkdtemp(prefix="piper-computer-use-smoke-"))
    try:
        data_dir = temp_root / "data"
        workspace = temp_root / "workspace"
        data_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        engine = ComputerUseEngine(data_dir=data_dir, workspace=workspace)
        fixture_root = ROOT_DIR / "scripts" / "fixtures" / "computer_use"
        start_url = (fixture_root / "index.html").resolve().as_uri()
        topic_url = (fixture_root / "topic_sections.html").resolve().as_uri()
        hub_url = (fixture_root / "download_hub.html").resolve().as_uri()

        goto_result = _run_action(
            engine,
            {
                "action": "goto_url",
                "url": start_url,
            },
        )
        capture_result = _run_action(engine, {"action": "capture_state"})
        extract_result = _run_action(engine, {"action": "extract_text", "selector": "#status"})
        body_extract_result = _run_action(engine, {"action": "extract_text", "selector": "body"})
        goto_topic_page = _run_action(engine, {"action": "goto_url", "url": topic_url})
        topic_extract_result = _run_action(
            engine,
            {"action": "extract_text", "selector": "body", "topic": "warranty disclaimer"},
        )
        generic_topic_extract_result = _run_action(
            engine,
            {"action": "extract_text", "selector": "body", "topic": "general info"},
        )
        goto_start_before_type = _run_action(engine, {"action": "goto_url", "url": start_url})
        type_result = _run_action(engine, {"action": "type_text", "selector": "#email", "text": "alice@example.com"})
        has_text_click_result = _run_action(engine, {"action": "click", "selector": "button:has-text('next')"})
        goto_start_again = _run_action(engine, {"action": "goto_url", "url": start_url})
        click_result = _run_action(engine, {"action": "click", "selector": "#next-link"})
        wait_result = _run_action(engine, {"action": "wait_for", "selector": "#destination"})
        go_back_result = _run_action(engine, {"action": "go_back"})
        goto_download_page = _run_action(engine, {"action": "goto_url", "url": start_url})
        download_result = _run_action(
            engine,
            {
                "action": "download",
                "selector": "#download-link",
                "download_dir": "browser_downloads",
            },
        )
        goto_hub_page = _run_action(engine, {"action": "goto_url", "url": hub_url})
        hub_click_result = _run_action(engine, {"action": "click", "selector": "#quarterly-report-link"})
        navigation_download_result = _run_action(
            engine,
            {
                "action": "download",
                "text": "quarterly report",
                "download_dir": "browser_downloads_nav",
            },
        )
        direct_url_download_result = _run_action(
            engine,
            {
                "action": "download",
                "url": "downloads/quarterly-report.pdf",
                "download_dir": "browser_downloads_direct",
                "filename": "quarterly-report.pdf",
            },
        )
        scope_block_result = _run_action(
            engine,
            {
                "action": "goto_url",
                "url": "https://example.com/",
                "allowed_domains": ["not-example.com"],
            },
        )
        configured_allowlist_block_result = _run_action(
            engine,
            {
                "action": "goto_url",
                "url": "https://openai.com/",
                "allowed_domains": ["openai.com"],
            },
        )

        downloaded_rel = str(download_result.get("saved_path") or "")
        downloaded_path = workspace / downloaded_rel if downloaded_rel else workspace / "missing"
        navigation_downloaded_rel = str(navigation_download_result.get("saved_path") or "")
        navigation_downloaded_path = workspace / navigation_downloaded_rel if navigation_downloaded_rel else workspace / "missing"
        direct_downloaded_rel = str(direct_url_download_result.get("saved_path") or "")
        direct_downloaded_path = workspace / direct_downloaded_rel if direct_downloaded_rel else workspace / "missing"

        title_ok = (
            goto_result.get("status") == "EXECUTED"
            and capture_result.get("status") == "EXECUTED"
            and str(capture_result.get("title") or "") == "Browser Fixture Home"
        )
        inventory = capture_result.get("element_inventory") or []
        selectors = {str(item.get("selector") or "") for item in inventory if isinstance(item, dict)}
        inventory_ok = selectors >= {"#status", "#email", "#next-link", "#download-link"}
        extract_ok = (
            extract_result.get("status") == "EXECUTED"
            and str(extract_result.get("extracted_text") or "") == "Hello from Piper fixture"
        )
        body_extract_ok = (
            body_extract_result.get("status") == "EXECUTED"
            and "Hello from Piper fixture" in str(body_extract_result.get("extracted_text") or "")
            and "Download report" in str(body_extract_result.get("extracted_text") or "")
        )
        topic_extract_ok = (
            goto_topic_page.get("status") == "EXECUTED"
            and topic_extract_result.get("status") == "EXECUTED"
            and str(topic_extract_result.get("topic") or "") == "warranty disclaimer"
            and "as is" in str(topic_extract_result.get("extracted_text") or "").lower()
            and "merchantability" in str(topic_extract_result.get("extracted_text") or "").lower()
        )
        generic_topic_extract_ok = (
            generic_topic_extract_result.get("status") == "EXECUTED"
            and str(generic_topic_extract_result.get("topic") or "") == "general info"
            and "general background" in str(generic_topic_extract_result.get("extracted_text") or "").lower()
        )
        type_ok = (
            goto_start_before_type.get("status") == "EXECUTED"
            and
            type_result.get("status") == "EXECUTED"
            and str(type_result.get("field_value") or "") == "alice@example.com"
        )
        has_text_click_ok = (
            has_text_click_result.get("status") == "EXECUTED"
            and str(has_text_click_result.get("current_url") or "").endswith("/next.html")
            and goto_start_again.get("status") == "EXECUTED"
        )
        click_ok = (
            click_result.get("status") == "EXECUTED"
            and str(click_result.get("title") or "") == "Browser Fixture Next"
            and str(click_result.get("current_url") or "").endswith("/next.html")
        )
        go_back_ok = (
            go_back_result.get("status") == "EXECUTED"
            and str(go_back_result.get("title") or "") == "Browser Fixture Home"
            and str(go_back_result.get("current_url") or "").endswith("/index.html")
        )
        wait_ok = wait_result.get("status") == "EXECUTED"
        download_ok = (
            goto_download_page.get("status") == "EXECUTED"
            and download_result.get("status") == "EXECUTED"
            and downloaded_path.exists()
            and downloaded_path.read_text(encoding="utf-8").strip() == "fixture download ok"
        )
        navigation_download_ok = (
            goto_hub_page.get("status") == "EXECUTED"
            and hub_click_result.get("status") == "EXECUTED"
            and str(hub_click_result.get("current_url") or "").endswith("/quarterly_reports.html")
            and navigation_download_result.get("status") == "EXECUTED"
            and navigation_downloaded_rel == "browser_downloads_nav/quarterly-report.pdf"
            and navigation_downloaded_path.exists()
            and navigation_downloaded_path.read_text(encoding="utf-8").strip() == "fixture quarterly report pdf"
        )
        direct_url_download_ok = (
            direct_url_download_result.get("status") == "EXECUTED"
            and direct_downloaded_rel == "browser_downloads_direct/quarterly-report.pdf"
            and direct_downloaded_path.exists()
            and direct_downloaded_path.read_text(encoding="utf-8").strip() == "fixture quarterly report pdf"
        )
        scope_block_ok = (
            scope_block_result.get("status") == "BLOCKED"
            and "outside the allowed browser scope" in str(scope_block_result.get("summary") or "").lower()
        )
        configured_allowlist_block_ok = (
            configured_allowlist_block_result.get("status") == "BLOCKED"
            and "outside the live-site browser pilot allowlist" in str(configured_allowlist_block_result.get("summary") or "").lower()
        )
        success = all(
            (
                title_ok,
                inventory_ok,
                extract_ok,
                body_extract_ok,
                topic_extract_ok,
                generic_topic_extract_ok,
                type_ok,
                has_text_click_ok,
                click_ok,
                go_back_ok,
                wait_ok,
                download_ok,
                navigation_download_ok,
                direct_url_download_ok,
                scope_block_ok,
                configured_allowlist_block_ok,
            )
        )

        return ComputerUseEngineSmokeReport(
            success=bool(success),
            title_ok=bool(title_ok),
            inventory_ok=bool(inventory_ok),
            extract_ok=bool(extract_ok),
            body_extract_ok=bool(body_extract_ok),
            topic_extract_ok=bool(topic_extract_ok),
            generic_topic_extract_ok=bool(generic_topic_extract_ok),
            type_ok=bool(type_ok),
            has_text_click_ok=bool(has_text_click_ok),
            click_ok=bool(click_ok),
            go_back_ok=bool(go_back_ok),
            wait_ok=bool(wait_ok),
            download_ok=bool(download_ok),
            navigation_download_ok=bool(navigation_download_ok),
            direct_url_download_ok=bool(direct_url_download_ok),
            scope_block_ok=bool(scope_block_ok),
            configured_allowlist_block_ok=bool(configured_allowlist_block_ok),
            downloaded_rel=downloaded_rel,
            final_url=str(click_result.get("current_url") or ""),
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test the local-fixture browser computer-use engine.")
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
        print(f"INVENTORY_OK: {report.inventory_ok}")
        print(f"EXTRACT_OK: {report.extract_ok}")
        print(f"BODY_EXTRACT_OK: {report.body_extract_ok}")
        print(f"TOPIC_EXTRACT_OK: {report.topic_extract_ok}")
        print(f"GENERIC_TOPIC_EXTRACT_OK: {report.generic_topic_extract_ok}")
        print(f"TYPE_OK: {report.type_ok}")
        print(f"HAS_TEXT_CLICK_OK: {report.has_text_click_ok}")
        print(f"CLICK_OK: {report.click_ok}")
        print(f"GO_BACK_OK: {report.go_back_ok}")
        print(f"WAIT_OK: {report.wait_ok}")
        print(f"DOWNLOAD_OK: {report.download_ok}")
        print(f"NAVIGATION_DOWNLOAD_OK: {report.navigation_download_ok}")
        print(f"DIRECT_URL_DOWNLOAD_OK: {report.direct_url_download_ok}")
        print(f"SCOPE_BLOCK_OK: {report.scope_block_ok}")
        print(f"DOWNLOADED_REL: {report.downloaded_rel}")
        print(f"FINAL_URL: {report.final_url}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
