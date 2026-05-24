"""Unit tests for core.services.computer_use_verifier.

These tests require no browser, no filesystem, no LLM, no web calls,
and no threading. They validate deterministic pure functions.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.services.computer_use_verifier import (
    build_verified_payload,
    evaluate_stage,
    new_stage_evidence,
    update_stage_evidence,
)
from core.services.verification import VerificationResult


# ── 1. new_stage_evidence ────────────────────────────────────────────

class TestNewStageEvidence:
    def test_creates_default_structure(self) -> None:
        evidence = new_stage_evidence()
        assert evidence["start_url"] == ""
        assert evidence["current_url"] == ""
        assert evidence["title"] == ""
        assert evidence["actions"] == []
        assert evidence["extracts"] == []
        assert evidence["downloads"] == []
        assert evidence["download_details"] == []
        assert evidence["field_values"] == {}
        assert evidence["element_inventory"] == []
        assert evidence["history_navigation"] == {}

    def test_preserves_stage_start_url(self) -> None:
        stage = {"computer_use": {"start_url": "https://example.com"}}
        evidence = new_stage_evidence(stage)
        assert evidence["start_url"] == "https://example.com"

    def test_handles_none_stage(self) -> None:
        evidence = new_stage_evidence(None)
        assert evidence["current_url"] == ""


# ── 2. update_stage_evidence ─────────────────────────────────────────

class TestUpdateStageEvidence:
    def test_ignores_non_browser_op(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(evidence, {"tool": "FILE_OP", "status": "EXECUTED"})
        assert result["actions"] == []
        assert result["current_url"] == ""

    def test_ignores_non_executed(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(
            evidence, {"tool": "BROWSER_OP", "status": "FAILED"}
        )
        assert result["actions"] == []

    def test_accumulates_url_and_title(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(
            evidence,
            {
                "tool": "BROWSER_OP",
                "status": "EXECUTED",
                "action": "navigate",
                "current_url": "https://example.com",
                "title": "Example",
            },
        )
        assert result["current_url"] == "https://example.com"
        assert result["title"] == "Example"
        assert "navigate" in result["actions"]

    def test_deduplicates_actions(self) -> None:
        evidence = new_stage_evidence()
        for _ in range(3):
            evidence = update_stage_evidence(
                evidence,
                {"tool": "BROWSER_OP", "status": "EXECUTED", "action": "click"},
            )
        assert evidence["actions"] == ["click"]

    def test_accumulates_extracts(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(
            evidence,
            {
                "tool": "BROWSER_OP",
                "status": "EXECUTED",
                "action": "extract",
                "selector": "#status",
                "extracted_text": "All systems operational",
                "topic": "status",
                "matched_heading": "System Status",
                "selector_strategy": "topic_ranked_extract",
                "topic_match_score": 95,
            },
        )
        assert len(result["extracts"]) == 1
        extract = result["extracts"][0]
        assert extract["selector"] == "#status"
        assert extract["text"] == "All systems operational"
        assert extract["topic"] == "status"
        assert extract["matched_heading"] == "System Status"
        assert extract["selector_strategy"] == "topic_ranked_extract"
        assert extract["topic_match_score"] == 95

    def test_accumulates_downloads(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(
            evidence,
            {
                "tool": "BROWSER_OP",
                "status": "EXECUTED",
                "action": "download",
                "saved_path": "browser_downloads/report.pdf",
                "selector": "#download-link",
                "download_label": "Quarterly Report",
                "source_href": "https://example.com/report.pdf",
            },
        )
        assert result["downloads"] == ["browser_downloads/report.pdf"]
        assert len(result["download_details"]) == 1
        detail = result["download_details"][0]
        assert detail["saved_path"] == "browser_downloads/report.pdf"
        assert detail["label"] == "Quarterly Report"
        assert detail["href"] == "https://example.com/report.pdf"

    def test_deduplicates_downloads(self) -> None:
        evidence = new_stage_evidence()
        for _ in range(2):
            evidence = update_stage_evidence(
                evidence,
                {
                    "tool": "BROWSER_OP",
                    "status": "EXECUTED",
                    "action": "download",
                    "saved_path": "report.pdf",
                },
            )
        assert evidence["downloads"] == ["report.pdf"]

    def test_accumulates_field_values(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(
            evidence,
            {
                "tool": "BROWSER_OP",
                "status": "EXECUTED",
                "action": "type_text",
                "selector": "#username",
                "field_value": "alice",
                "element_inventory": [
                    {"selector": "#username", "id": "username", "name": "user"}
                ],
            },
        )
        assert result["field_values"]["#username"] == "alice"
        assert result["field_values"]["#username"] == "alice"
        assert result["field_values"]["[name='user']"] == "alice"

    def test_accumulates_inventory(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(
            evidence,
            {
                "tool": "BROWSER_OP",
                "status": "EXECUTED",
                "action": "scan",
                "element_inventory": [
                    {"selector": "#btn", "tag": "button", "text": "Submit"}
                ],
            },
        )
        assert len(result["element_inventory"]) == 1
        assert result["element_inventory"][0]["selector"] == "#btn"

    def test_accumulates_go_back_history(self) -> None:
        evidence = new_stage_evidence()
        result = update_stage_evidence(
            evidence,
            {
                "tool": "BROWSER_OP",
                "status": "EXECUTED",
                "action": "go_back",
                "current_url_before_action": "https://example.com/page2",
                "previous_url": "https://example.com/page1",
            },
        )
        assert result["history_navigation"] == {
            "current_url_before_action": "https://example.com/page2",
            "previous_url": "https://example.com/page1",
        }


# ── helpers ──────────────────────────────────────────────────────────

def _make_stage(**computer_use_overrides: Any) -> dict[str, Any]:
    return {"computer_use": dict(computer_use_overrides)}


# ── 3. evaluate_stage download verification ──────────────────────────

class TestEvaluateStageDownload:
    def test_success_when_download_exists_without_hint(self) -> None:
        stage = _make_stage(require_download=True)
        evidence = {
            "downloads": ["browser_downloads/file.zip"],
            "download_details": [
                {"saved_path": "browser_downloads/file.zip", "label": "", "href": ""}
            ],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"
        assert "downloaded artifact saved to" in result.evidence_summary

    def test_success_when_hint_matches(self) -> None:
        stage = _make_stage(require_download=True, download_hint="quarterly report")
        evidence = {
            "downloads": ["browser_downloads/quarterly_report.pdf"],
            "download_details": [
                {
                    "saved_path": "browser_downloads/quarterly_report.pdf",
                    "label": "Quarterly Report",
                    "href": "https://example.com/quarterly_report.pdf",
                }
            ],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"

    def test_failure_when_download_missing(self) -> None:
        stage = _make_stage(require_download=True)
        evidence = {"downloads": [], "download_details": []}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "FAILED"
        assert "download the requested artifact" in result.evidence_summary

    def test_partial_when_hint_mismatch_below_threshold(self) -> None:
        stage = _make_stage(require_download=True, download_hint="quarterly report")
        evidence = {
            "downloads": ["browser_downloads/readme.txt"],
            "download_details": [
                {"saved_path": "browser_downloads/readme.txt", "label": "", "href": ""}
            ],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict in ("PARTIAL", "FAILED")
        assert "download" in result.evidence_summary.lower()

    def test_respects_download_dir(self) -> None:
        stage = _make_stage(
            require_download=True, download_dir="browser_downloads/reports"
        )
        evidence = {
            "downloads": ["browser_downloads/reports/file.pdf"],
            "download_details": [
                {"saved_path": "browser_downloads/reports/file.pdf", "label": "", "href": ""}
            ],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"


# ── 4. evaluate_stage form-fill verification ─────────────────────────

class TestEvaluateStageFormFill:
    def test_success_when_selector_value_match(self) -> None:
        stage = _make_stage(
            require_form_fill=True, selector_hint="#username", input_text="alice"
        )
        evidence = {"field_values": {"#username": "alice"}}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"
        assert "verified field value alice" in result.evidence_summary

    def test_success_via_inventory_alias(self) -> None:
        stage = _make_stage(
            require_form_fill=True, selector_hint="[name='user']", input_text="alice"
        )
        evidence = {
            "field_values": {"#username": "alice"},
            "element_inventory": [
                {"selector": "#username", "name": "user"}
            ],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"

    def test_failure_when_field_missing(self) -> None:
        stage = _make_stage(
            require_form_fill=True, selector_hint="#username", input_text="alice"
        )
        evidence = {"field_values": {}}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "FAILED"
        assert "fill" in result.evidence_summary.lower()

    def test_partial_when_value_mismatch(self) -> None:
        stage = _make_stage(
            require_form_fill=True, selector_hint="#username", input_text="alice"
        )
        evidence = {"field_values": {"#username": "bob"}}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "PARTIAL"
        assert "fill" in result.evidence_summary.lower()

    def test_success_without_selector_hint(self) -> None:
        stage = _make_stage(require_form_fill=True, input_text="alice")
        evidence = {"field_values": {"#any": "alice"}}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"


# ── 5. evaluate_stage navigation verification ────────────────────────

class TestEvaluateStageNavigation:
    def test_success_on_forward_click_and_url_change(self) -> None:
        stage = _make_stage(require_navigation=True)
        evidence = {
            "start_url": "https://example.com",
            "current_url": "https://example.com/page2",
            "actions": ["click"],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"
        assert "navigated to" in result.evidence_summary

    def test_partial_when_no_click(self) -> None:
        stage = _make_stage(require_navigation=True)
        evidence = {
            "start_url": "https://example.com",
            "current_url": "https://example.com/page2",
            "actions": ["navigate"],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "PARTIAL"
        assert "navigation" in result.evidence_summary.lower()

    def test_partial_when_url_unchanged(self) -> None:
        stage = _make_stage(require_navigation=True)
        evidence = {
            "start_url": "https://example.com",
            "current_url": "https://example.com",
            "actions": ["click"],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "PARTIAL"
        assert "navigation" in result.evidence_summary.lower()

    def test_success_on_go_back(self) -> None:
        stage = _make_stage(require_navigation=True, history_navigation="back")
        evidence = {
            "actions": ["go_back"],
            "current_url": "https://example.com/page1",
            "history_navigation": {
                "current_url_before_action": "https://example.com/page2",
                "previous_url": "https://example.com/page1",
            },
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"
        assert "went back to" in result.evidence_summary

    def test_partial_on_go_back_without_history_evidence(self) -> None:
        stage = _make_stage(require_navigation=True, history_navigation="back")
        evidence = {
            "actions": ["go_back"],
            "current_url": "https://example.com/page1",
            "history_navigation": {},
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "PARTIAL"
        assert "go back" in result.evidence_summary.lower()


# ── 6. evaluate_stage extraction verification ────────────────────────

class TestEvaluateStageExtraction:
    def test_success_on_topic_match(self) -> None:
        stage = _make_stage(require_extract=True, requested_topic="status")
        evidence = {
            "extracts": [
                {
                    "selector": "#status",
                    "text": "All systems operational",
                    "topic": "status",
                }
            ]
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"
        assert "status" in result.evidence_summary.lower()

    def test_partial_when_topic_missing(self) -> None:
        stage = _make_stage(require_extract=True, requested_topic="revenue")
        evidence = {
            "extracts": [
                {"selector": "#status", "text": "All systems operational", "topic": "status"}
            ]
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "PARTIAL"
        assert "extract" in result.evidence_summary.lower()

    def test_success_on_title_report(self) -> None:
        stage = _make_stage(require_extract=True, report_title=True)
        evidence = {"title": "Example Domain"}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"
        assert "Example Domain" in result.evidence_summary

    def test_failure_when_title_missing(self) -> None:
        stage = _make_stage(require_extract=True, report_title=True)
        evidence = {"title": ""}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "FAILED"

    def test_success_on_selector_match(self) -> None:
        stage = _make_stage(require_extract=True, selector_hint="#status")
        evidence = {
            "extracts": [{"selector": "#status", "text": "Operational"}]
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"

    def test_success_on_status_text(self) -> None:
        stage = _make_stage(require_extract=True, report_status_text=True)
        evidence = {
            "extracts": [{"selector": "#status", "text": "200 OK"}],
            "element_inventory": [{"selector": "#status", "text": "200 OK"}],
        }
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"

    def test_failure_when_extraction_missing(self) -> None:
        stage = _make_stage(require_extract=True)
        evidence = {"extracts": []}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "FAILED"


# ── 7. evaluate_stage fallback behavior ──────────────────────────────

class TestEvaluateStageFallback:
    def test_verified_when_page_opened_no_requirements(self) -> None:
        stage = _make_stage()
        evidence = {"current_url": "https://example.com"}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "VERIFIED"
        assert "opened browser page" in result.evidence_summary

    def test_failed_when_nothing_done(self) -> None:
        stage = _make_stage()
        evidence = {}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "FAILED"
        assert "open the requested browser page" in result.evidence_summary

    def test_partial_when_some_progress_no_requirements(self) -> None:
        stage = _make_stage()
        evidence = {"actions": ["click"], "current_url": "", "extracts": [{"selector": "#x", "text": "y"}]}
        result = evaluate_stage(stage, evidence)
        assert result.verdict == "PARTIAL"
        assert "Partial browser progress" in result.evidence_summary


# ── 8. build_verified_payload ────────────────────────────────────────

class TestBuildVerifiedPayload:
    def test_includes_extracts(self) -> None:
        stage = _make_stage()
        evidence = {
            "extracts": [
                {
                    "selector": "#status",
                    "text": "200 OK",
                    "topic": "status",
                    "matched_heading": "Health",
                }
            ]
        }
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert len(payload["extracts"]) == 1
        assert payload["extracts"][0]["text"] == "200 OK"

    def test_includes_downloads(self) -> None:
        stage = _make_stage()
        evidence = {"downloads": ["report.pdf"], "download_details": []}
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["downloads"] == ["report.pdf"]

    def test_includes_field_values(self) -> None:
        stage = _make_stage()
        evidence = {"field_values": {"#user": "alice"}}
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["field_values"]["#user"] == "alice"

    def test_includes_status_text(self) -> None:
        stage = _make_stage(report_status_text=True)
        evidence = {
            "extracts": [{"selector": "#status", "text": "200 OK"}],
            "element_inventory": [{"selector": "#status", "text": "200 OK"}],
        }
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["status_text"] == "200 OK"

    def test_includes_heading_text(self) -> None:
        stage = _make_stage(selector_hint="h1")
        evidence = {
            "extracts": [{"selector": "h1", "text": "Welcome"}],
        }
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["heading_text"] == "Welcome"

    def test_includes_reported_title(self) -> None:
        stage = _make_stage(report_title=True)
        evidence = {"title": "Example Domain"}
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["reported_title"] == "Example Domain"

    def test_preserves_summary(self) -> None:
        stage = _make_stage()
        evidence = {"current_url": "https://example.com"}
        verification = VerificationResult.verified("all good")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["summary"] == "all good"

    def test_includes_download_label_and_href(self) -> None:
        stage = _make_stage(download_hint="report")
        evidence = {
            "downloads": ["report.pdf"],
            "download_details": [
                {
                    "saved_path": "report.pdf",
                    "label": "Annual Report",
                    "href": "https://example.com/report.pdf",
                }
            ],
        }
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["download_label"] == "Annual Report"
        assert payload["source_href"] == "https://example.com/report.pdf"
        assert payload["saved_path"] == "report.pdf"

    def test_includes_requested_topic(self) -> None:
        stage = _make_stage(requested_topic="status")
        evidence = {"current_url": "https://example.com"}
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["requested_topic"] == "status"

    def test_extracted_text_from_expected_text(self) -> None:
        stage = _make_stage(expected_text="operational")
        evidence = {
            "extracts": [{"selector": "#status", "text": "All systems operational"}],
            "element_inventory": [{"selector": "#status", "text": "All systems operational"}],
        }
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["extracted_text"] == "All systems operational"

    def test_topic_match_sets_extracted_text(self) -> None:
        stage = _make_stage(requested_topic="status")
        evidence = {
            "extracts": [
                {
                    "selector": "#status",
                    "text": "All systems operational",
                    "topic": "status",
                    "matched_heading": "System Health",
                }
            ]
        }
        verification = VerificationResult.verified("ok")
        payload = build_verified_payload(stage, evidence, verification)
        assert payload["extracted_text"] == "All systems operational"
        assert payload["matched_heading"] == "System Health"
