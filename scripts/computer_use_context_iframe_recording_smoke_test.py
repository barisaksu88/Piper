"""Smoke test for new ComputerUseEngine features: browser context isolation,
iframe traversal, and session recording.

These features are Playwright-only and require the Playwright package installed.
Local-fixture tests test the engine's structural correctness; Playwright tests
verify the full browser automation pipeline.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.computer_use_engine import ComputerUseEngine
from core.engines.computer_use_verifier import (
    new_stage_evidence,
    update_stage_evidence,
    evaluate_stage,
)


def _run_action(engine: ComputerUseEngine, payload: dict) -> dict:
    return engine.exec_browser_op(json.dumps(payload, ensure_ascii=False))


def _status_ok(result: dict, action: str) -> bool:
    return result.get("status") == "EXECUTED" and result.get("action") == action


def run_local_fixture_tests() -> dict[str, bool]:
    """Tests that work with local file:// fixture pages (no Playwright needed)."""
    results: dict[str, bool] = {}
    temp_root = Path(tempfile.mkdtemp(prefix="piper-ctx-iframe-rec-smoke-"))
    try:
        data_dir = temp_root / "data"
        workspace = temp_root / "workspace"
        data_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        engine = ComputerUseEngine(data_dir=data_dir, workspace=workspace)

        # --- new_context (without Playwright) ---
        ctx_result = _run_action(engine, {"action": "new_context", "context_id": "test-ctx-1"})
        results["new_context_ok"] = (
            _status_ok(ctx_result, "new_context")
            and str(ctx_result.get("context_id") or "") == "test-ctx-1"
        )

        # --- new_context with recording ---
        ctx_rec_result = _run_action(engine, {"action": "new_context", "context_id": "rec-ctx", "recording": True})
        results["new_context_recording_ok"] = (
            _status_ok(ctx_rec_result, "new_context")
            and bool(ctx_rec_result.get("recording_enabled"))
        )

        # --- new_context auto-generated ID ---
        ctx_auto_result = _run_action(engine, {"action": "new_context"})
        results["new_context_auto_id_ok"] = (
            _status_ok(ctx_auto_result, "new_context")
            and bool(str(ctx_auto_result.get("context_id") or "").strip())
        )

        # --- get_context_id ---
        results["get_context_id_ok"] = engine.get_context_id() == str(ctx_auto_result.get("context_id") or "")

        # --- export_recording (no actions recorded yet, but should succeed) ---
        export_result = _run_action(engine, {"action": "export_recording"})
        results["export_recording_ok"] = _status_ok(export_result, "export_recording")

        # --- export_recording with save_path ---
        export_save_result = _run_action(
            engine, {"action": "export_recording", "save_path": "logs/recording.json"}
        )
        results["export_recording_save_ok"] = (
            _status_ok(export_save_result, "export_recording")
            and bool(export_save_result.get("saved_path"))
        )
        saved_path = Path(str(export_save_result.get("saved_path") or ""))
        if saved_path.exists():
            recording_data = json.loads(saved_path.read_text(encoding="utf-8"))
            results["export_recording_file_valid"] = isinstance(recording_data, dict) and "actions" in recording_data
        else:
            results["export_recording_file_valid"] = False

        # --- list_iframes on local fixture (should fail gracefully) ---
        fixture_root = ROOT_DIR / "scripts" / "fixtures" / "computer_use"
        start_url = (fixture_root / "index.html").resolve().as_uri()
        _run_action(engine, {"action": "goto_url", "url": start_url})

        list_iframes_result = _run_action(engine, {"action": "list_iframes"})
        results["list_iframes_local_graceful"] = (
            list_iframes_result.get("status") in ("EXECUTED", "FAILED", "BLOCKED")
        )

        # --- extract_iframe_text missing identifier ---
        no_id_result = _run_action(engine, {"action": "extract_iframe_text"})
        results["extract_iframe_text_no_id_fails"] = no_id_result.get("status") == "FAILED"

        # --- Verifier integration: new_context evidence ---
        evidence = new_stage_evidence({"computer_use": {"require_context": True, "start_url": start_url}})
        results["verifier_new_context_evidence_empty"] = evidence.get("context_id") == ""

        ctx_action_result = _run_action(engine, {"action": "new_context", "context_id": "v-ctx-1"})
        evidence = update_stage_evidence(evidence, ctx_action_result)
        results["verifier_context_id_recorded"] = evidence.get("context_id") == "v-ctx-1"

        verdict = evaluate_stage({"computer_use": {"require_context": True, "start_url": start_url}}, evidence)
        results["verifier_context_satisfied"] = verdict.verdict == "VERIFIED"

        # --- Verifier integration: iframe_extract evidence ---
        evidence2 = new_stage_evidence({"computer_use": {"require_iframe_extract": True, "start_url": start_url}})
        fake_iframe_result = {
            "tool": "BROWSER_OP",
            "status": "EXECUTED",
            "action": "extract_iframe_text",
            "extracted_text": "Pricing info here",
            "iframe_url": "https://embed.example.com/pricing",
            "iframe_name": "pricing-frame",
        }
        evidence2 = update_stage_evidence(evidence2, fake_iframe_result)
        results["verifier_iframe_extract_recorded"] = len(evidence2.get("iframe_extracts") or []) == 1

        # --- Verifier integration: recording_exports evidence ---
        evidence3 = new_stage_evidence({"computer_use": {"start_url": start_url}})
        fake_export_result = {
            "tool": "BROWSER_OP",
            "status": "EXECUTED",
            "action": "export_recording",
            "context_id": "rec-1",
            "action_count": 5,
            "saved_path": "/tmp/rec.json",
        }
        evidence3 = update_stage_evidence(evidence3, fake_export_result)
        results["verifier_recording_export_recorded"] = len(evidence3.get("recording_exports") or []) == 1

        # --- Session recording: actions logged ---
        engine2 = ComputerUseEngine(data_dir=data_dir / "e2", workspace=workspace / "e2")
        _run_action(engine2, {"action": "new_context", "context_id": "rec-test", "recording": True})
        _run_action(engine2, {"action": "goto_url", "url": start_url})
        _run_action(engine2, {"action": "capture_state"})
        action_log = engine2.get_action_log()
        results["recording_logs_actions"] = len(action_log) >= 2  # goto_url + capture_state

        export_all = engine2.export_recording()
        results["recording_export_has_actions"] = (
            export_all.get("action_count", 0) >= 2
            and export_all.get("context_id") == "rec-test"
        )
        engine2.shutdown()

        engine.shutdown()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return results


def run_playwright_tests() -> dict[str, bool]:
    """Tests that require Playwright to be installed. These test real browser actions."""
    results: dict[str, bool] = {}
    temp_root = Path(tempfile.mkdtemp(prefix="piper-ctx-iframe-pw-smoke-"))
    try:
        data_dir = temp_root / "data"
        workspace = temp_root / "workspace"
        data_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        engine = ComputerUseEngine(data_dir=data_dir, workspace=workspace)
        fixture_root = ROOT_DIR / "scripts" / "fixtures" / "computer_use"
        index_url = (fixture_root / "index.html").resolve().as_uri()

        # --- Navigate first to establish Playwright ---
        goto_result = _run_action(
            engine,
            {"action": "goto_url", "url": index_url, "allowed_domains": []},
        )
        if goto_result.get("status") != "EXECUTED":
            results["playwright_available"] = False
            engine.shutdown()
            return results

        results["playwright_available"] = True

        # --- new_context with Playwright ---
        ctx_result = _run_action(engine, {"action": "new_context", "context_id": "pw-ctx-1", "recording": True})
        results["pw_new_context_ok"] = (
            _status_ok(ctx_result, "new_context")
            and str(ctx_result.get("context_id") or "") == "pw-ctx-1"
        )

        # --- After new_context, re-navigate ---
        goto2_result = _run_action(
            engine,
            {"action": "goto_url", "url": index_url, "allowed_domains": []},
        )
        results["pw_goto_after_new_context_ok"] = _status_ok(goto2_result, "goto_url")

        # --- list_iframes ---
        list_result = _run_action(engine, {"action": "list_iframes"})
        results["pw_list_iframes_ok"] = _status_ok(list_result, "list_iframes")

        # --- export_recording with actions ---
        export_result = _run_action(engine, {"action": "export_recording"})
        results["pw_export_recording_ok"] = (
            _status_ok(export_result, "export_recording")
            and int(export_result.get("action_count") or 0) >= 1
        )

        # --- export_recording with save_path ---
        export_save_result = _run_action(
            engine, {"action": "export_recording", "save_path": "logs/pw_session.json"}
        )
        results["pw_export_recording_save_ok"] = (
            _status_ok(export_save_result, "export_recording")
            and bool(export_save_result.get("saved_path"))
        )

        engine.shutdown()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return results


def main() -> int:
    print("=" * 60)
    print("CONTEXT / IFRAME / RECORDING SMOKE TEST")
    print("=" * 60)

    print("\n--- Local Fixture Tests (no Playwright required) ---")
    local_results = run_local_fixture_tests()
    local_pass = 0
    local_total = len(local_results)
    for name, ok in local_results.items():
        status_str = "PASS" if ok else "FAIL"
        print(f"  {status_str}  {name}")
        if ok:
            local_pass += 1
    print(f"\n  Local: {local_pass}/{local_total} passed")

    print("\n--- Playwright Integration Tests ---")
    pw_results = run_playwright_tests()
    pw_pass = 0
    pw_total = len(pw_results)
    for name, ok in pw_results.items():
        status_str = "PASS" if ok else "FAIL"
        print(f"  {status_str}  {name}")
        if ok:
            pw_pass += 1
    print(f"\n  Playwright: {pw_pass}/{pw_total} passed")

    total_pass = local_pass + pw_pass
    total_tests = local_total + pw_total
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total_pass}/{total_tests} passed")
    print(f"{'=' * 60}")

    return 0 if total_pass == total_tests else 1


if __name__ == "__main__":
    raise SystemExit(main())
