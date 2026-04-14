from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.computer_use_engine import ComputerUseEngine
from scripts.computer_use_fixture_server import running_fixture_server


@dataclass(frozen=True)
class EnhancedActionsDemoReport:
    success: bool
    backend_ok: bool
    new_context_ok: bool
    wait_for_load_ok: bool
    screenshot_ok: bool
    scroll_ok: bool
    key_press_ok: bool
    select_option_ok: bool
    check_ok: bool
    uncheck_ok: bool
    upload_file_ok: bool
    list_iframes_ok: bool
    extract_iframe_text_ok: bool
    export_recording_ok: bool
    report_path: str
    output_dir: str
    context_id: str
    screenshot_path: str
    recording_path: str
    iframe_count: int
    uploaded_filename: str
    backend_summary: str
    scroll_status: str
    key_status: str
    select_status: str
    check_status: str
    upload_status: str
    iframe_excerpt: str


def _run_action(engine: ComputerUseEngine, payload: dict) -> dict:
    return engine.exec_browser_op(json.dumps(payload, ensure_ascii=False))


def _extract_text(engine: ComputerUseEngine, selector: str) -> str:
    result = _run_action(engine, {"action": "extract_text", "selector": selector})
    return str(result.get("extracted_text") or "").strip()


def _parse_scroll_y(text: str) -> int:
    match = re.search(r"(-?\d+)", text)
    return int(match.group(1)) if match else 0


def _default_output_dir() -> Path:
    return ROOT_DIR / "data" / "harness" / "results" / "enhanced_browser_demo"


def _resolve_output_dir(raw_value: str) -> Path:
    value = str(raw_value or "").strip()
    if not value:
        return _default_output_dir()
    raw_path = Path(value)
    if raw_path.is_absolute():
        return raw_path
    return (ROOT_DIR / raw_path).resolve()


def run_demo(output_dir: Path) -> EnhancedActionsDemoReport:
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_dir = output_dir / "data"
    workspace = output_dir / "workspace"
    data_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    upload_dir = workspace / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_file = upload_dir / "demo_upload.txt"
    upload_file.write_text("enhanced browser demo upload payload\n", encoding="utf-8")

    report_path = output_dir / "report.json"

    engine = ComputerUseEngine(data_dir=data_dir, workspace=workspace)
    try:
        with running_fixture_server() as base_url:
            start_url = f"{base_url}/enhanced_actions.html"
            allowed_domains = ["127.0.0.1"]
            context_id = "enhanced-demo"

            new_context_result = _run_action(
                engine,
                {
                    "action": "new_context",
                    "context_id": context_id,
                    "recording": True,
                },
            )
            goto_result = _run_action(
                engine,
                {
                    "action": "goto_url",
                    "url": start_url,
                    "allowed_domains": allowed_domains,
                },
            )

            backend_ok = (
                goto_result.get("status") == "EXECUTED"
                and str(goto_result.get("backend") or "") == "playwright"
            )
            backend_summary = str(goto_result.get("summary") or "")

            if not backend_ok:
                report = EnhancedActionsDemoReport(
                    success=False,
                    backend_ok=False,
                    new_context_ok=(
                        new_context_result.get("status") == "EXECUTED"
                        and str(new_context_result.get("context_id") or "") == context_id
                    ),
                    wait_for_load_ok=False,
                    screenshot_ok=False,
                    scroll_ok=False,
                    key_press_ok=False,
                    select_option_ok=False,
                    check_ok=False,
                    uncheck_ok=False,
                    upload_file_ok=False,
                    list_iframes_ok=False,
                    extract_iframe_text_ok=False,
                    export_recording_ok=False,
                    report_path=str(report_path),
                    output_dir=str(output_dir),
                    context_id=context_id,
                    screenshot_path="",
                    recording_path="",
                    iframe_count=0,
                    uploaded_filename="",
                    backend_summary=backend_summary,
                    scroll_status="",
                    key_status="",
                    select_status="",
                    check_status="",
                    upload_status="",
                    iframe_excerpt="",
                )
                report_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
                return report

            wait_for_load_result = _run_action(
                engine,
                {
                    "action": "wait_for_load",
                    "state": "domcontentloaded",
                    "timeout": 10000,
                },
            )
            screenshot_result = _run_action(
                engine,
                {
                    "action": "screenshot",
                    "full_page": True,
                    "save_path": "artifacts/enhanced-actions.png",
                },
            )
            scroll_result = _run_action(
                engine,
                {
                    "action": "scroll",
                    "direction": "down",
                    "pixels": 900,
                },
            )
            scroll_status = _extract_text(engine, "#scroll-status")

            click_keyboard_target = _run_action(engine, {"action": "click", "selector": "#keyboard-target"})
            key_press_result = _run_action(engine, {"action": "key_press", "key": "Enter"})
            key_status = _extract_text(engine, "#key-status")

            select_option_result = _run_action(
                engine,
                {
                    "action": "select_option",
                    "selector": "#demo-select",
                    "label": "Beta",
                },
            )
            select_status = _extract_text(engine, "#select-status")

            check_result = _run_action(engine, {"action": "check", "selector": "#demo-check"})
            check_status_after_check = _extract_text(engine, "#check-status")

            uncheck_result = _run_action(engine, {"action": "uncheck", "selector": "#demo-check"})
            check_status = _extract_text(engine, "#check-status")

            upload_file_result = _run_action(
                engine,
                {
                    "action": "upload_file",
                    "selector": "#demo-upload",
                    "file_path": "uploads/demo_upload.txt",
                },
            )
            upload_status = _extract_text(engine, "#upload-status")

            list_iframes_result = _run_action(engine, {"action": "list_iframes"})
            iframe_entries = list_iframes_result.get("iframes") if isinstance(list_iframes_result.get("iframes"), list) else []
            iframe_name = str(iframe_entries[0].get("name") or "demo-frame") if iframe_entries else "demo-frame"
            extract_iframe_result = _run_action(
                engine,
                {
                    "action": "extract_iframe_text",
                    "name": iframe_name,
                    "topic": "pricing overview",
                },
            )

            export_recording_result = _run_action(
                engine,
                {
                    "action": "export_recording",
                    "save_path": "artifacts/session-recording.json",
                },
            )

            screenshot_path = str(screenshot_result.get("saved_path") or "")
            recording_path = str(export_recording_result.get("saved_path") or "")
            iframe_excerpt = str(extract_iframe_result.get("extracted_text") or "").strip()

            new_context_ok = (
                new_context_result.get("status") == "EXECUTED"
                and str(new_context_result.get("context_id") or "") == context_id
            )
            wait_for_load_ok = wait_for_load_result.get("status") == "EXECUTED"
            screenshot_ok = (
                screenshot_result.get("status") == "EXECUTED"
                and Path(screenshot_path).exists()
                and int(screenshot_result.get("image_size_bytes") or 0) > 0
                and len(str(screenshot_result.get("image_base64") or "")) > 100
            )
            scroll_ok = (
                scroll_result.get("status") == "EXECUTED"
                and _parse_scroll_y(scroll_status) > 0
            )
            key_press_ok = (
                click_keyboard_target.get("status") == "EXECUTED"
                and key_press_result.get("status") == "EXECUTED"
                and "enter" in key_status.lower()
            )
            select_option_ok = (
                select_option_result.get("status") == "EXECUTED"
                and "beta" in select_status.lower()
            )
            check_ok = (
                check_result.get("status") == "EXECUTED"
                and "checkbox is checked" in check_status_after_check.lower()
            )
            uncheck_ok = (
                uncheck_result.get("status") == "EXECUTED"
                and "checkbox is not checked" in check_status.lower()
            )
            upload_file_ok = (
                upload_file_result.get("status") == "EXECUTED"
                and str(upload_file_result.get("uploaded_filename") or "") == "demo_upload.txt"
                and "demo_upload.txt" in upload_status
            )
            list_iframes_ok = (
                list_iframes_result.get("status") == "EXECUTED"
                and len(iframe_entries) >= 1
            )
            extract_iframe_text_ok = (
                extract_iframe_result.get("status") == "EXECUTED"
                and "pricing overview" in iframe_excerpt.lower()
            )
            export_recording_ok = (
                export_recording_result.get("status") == "EXECUTED"
                and Path(recording_path).exists()
                and int(export_recording_result.get("action_count") or 0) >= 10
            )
            success = all(
                (
                    backend_ok,
                    new_context_ok,
                    wait_for_load_ok,
                    screenshot_ok,
                    scroll_ok,
                    key_press_ok,
                    select_option_ok,
                    check_ok,
                    uncheck_ok,
                    upload_file_ok,
                    list_iframes_ok,
                    extract_iframe_text_ok,
                    export_recording_ok,
                )
            )

            report = EnhancedActionsDemoReport(
                success=bool(success),
                backend_ok=bool(backend_ok),
                new_context_ok=bool(new_context_ok),
                wait_for_load_ok=bool(wait_for_load_ok),
                screenshot_ok=bool(screenshot_ok),
                scroll_ok=bool(scroll_ok),
                key_press_ok=bool(key_press_ok),
                select_option_ok=bool(select_option_ok),
                check_ok=bool(check_ok),
                uncheck_ok=bool(uncheck_ok),
                upload_file_ok=bool(upload_file_ok),
                list_iframes_ok=bool(list_iframes_ok),
                extract_iframe_text_ok=bool(extract_iframe_text_ok),
                export_recording_ok=bool(export_recording_ok),
                report_path=str(report_path),
                output_dir=str(output_dir),
                context_id=context_id,
                screenshot_path=screenshot_path,
                recording_path=recording_path,
                iframe_count=len(iframe_entries),
                uploaded_filename=str(upload_file_result.get("uploaded_filename") or ""),
                backend_summary=backend_summary,
                scroll_status=scroll_status,
                key_status=key_status,
                select_status=select_status,
                check_status=check_status,
                upload_status=upload_status,
                iframe_excerpt=iframe_excerpt[:240],
            )
            report_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
            return report
    finally:
        engine.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an end-to-end demo for the enhanced browser actions.")
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory to write demo artifacts into. Defaults to data/harness/results/enhanced_browser_demo.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = _resolve_output_dir(args.output_dir)
    report = run_demo(output_dir)

    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"BACKEND_OK: {report.backend_ok}")
        print(f"NEW_CONTEXT_OK: {report.new_context_ok}")
        print(f"WAIT_FOR_LOAD_OK: {report.wait_for_load_ok}")
        print(f"SCREENSHOT_OK: {report.screenshot_ok}")
        print(f"SCROLL_OK: {report.scroll_ok} ({report.scroll_status})")
        print(f"KEY_PRESS_OK: {report.key_press_ok} ({report.key_status})")
        print(f"SELECT_OPTION_OK: {report.select_option_ok} ({report.select_status})")
        print(f"CHECK_OK: {report.check_ok}")
        print(f"UNCHECK_OK: {report.uncheck_ok} ({report.check_status})")
        print(f"UPLOAD_FILE_OK: {report.upload_file_ok} ({report.upload_status})")
        print(f"LIST_IFRAMES_OK: {report.list_iframes_ok} (count={report.iframe_count})")
        print(f"EXTRACT_IFRAME_TEXT_OK: {report.extract_iframe_text_ok}")
        print(f"EXPORT_RECORDING_OK: {report.export_recording_ok}")
        print(f"OUTPUT_DIR: {report.output_dir}")
        print(f"SCREENSHOT_PATH: {report.screenshot_path}")
        print(f"RECORDING_PATH: {report.recording_path}")
        print(f"REPORT_PATH: {report.report_path}")

    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
