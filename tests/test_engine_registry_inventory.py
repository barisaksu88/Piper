"""tests/test_engine_registry_inventory.py

Guard tests for the engine registry inventory script.

These tests verify that the inventory correctly reflects the expected
registrations without requiring a full Piper boot, UI, LLM server, or models.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# We import the inventory module directly to test its build_report function.
# The inventory module calls register_builtin_engines() to trigger registrations.
from scripts import engine_registry_inventory as inventory


class TestInventoryBuildsWithoutBoot:
    def test_build_report_runs_without_ui_or_llm(self):
        report = inventory.build_report()
        assert report is not None
        assert isinstance(report.route_interceptors, list)
        assert isinstance(report.feature_hooks, dict)
        assert isinstance(report.tail_blocks, list)

    def test_json_output_from_script(self):
        script = Path(__file__).parent.parent / "scripts" / "engine_registry_inventory.py"
        result = subprocess.run(
            [sys.executable, str(script), "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        assert "route_interceptors" in data
        assert "feature_hooks" in data
        assert "tail_blocks" in data
        assert "summary" in data


class TestRouteInterceptors:
    def test_environment_query_interceptor_present_exactly_once(self):
        report = inventory.build_report()
        matches = [
            e for e in report.route_interceptors
            if e.function_name == "_registered_environment_query_interceptor"
        ]
        assert len(matches) == 1
        assert matches[0].module == "core.engines.environment_query"

    def test_operational_state_interceptor_present_exactly_once(self):
        report = inventory.build_report()
        matches = [
            e for e in report.route_interceptors
            if e.function_name == "_registered_operational_state_interceptor"
        ]
        assert len(matches) == 1
        assert matches[0].module == "core.engines.operational_state_answer"

    def test_proactive_reminder_interceptor_present(self):
        report = inventory.build_report()
        matches = [
            e for e in report.route_interceptors
            if e.function_name == "_registered_reminder_set_interceptor"
        ]
        assert len(matches) == 1
        assert matches[0].module == "core.engines.proactive_monitor"

    def test_no_duplicate_route_interceptor_qualnames(self):
        report = inventory.build_report()
        qualnames = [e.qualname for e in report.route_interceptors]
        assert len(qualnames) == len(set(qualnames))

    def test_route_interceptor_entries_have_signatures(self):
        report = inventory.build_report()
        for entry in report.route_interceptors:
            assert entry.signature != "", f"Missing signature for {entry.qualname}"


class TestFeatureHooks:
    def test_on_turn_end_hooks_present(self):
        report = inventory.build_report()
        assert "on_turn_end" in report.feature_hooks
        assert len(report.feature_hooks["on_turn_end"]) >= 1

    def test_on_task_verified_hooks_present(self):
        report = inventory.build_report()
        assert "on_task_verified" in report.feature_hooks
        assert len(report.feature_hooks["on_task_verified"]) >= 1

    def test_memory_hooks_under_engine_module(self):
        report = inventory.build_report()
        on_turn_end = report.feature_hooks.get("on_turn_end", [])
        consolidate = [
            e for e in on_turn_end
            if e.function_name == "_hook_consolidate_recent_memory"
        ]
        refresh = [
            e for e in on_turn_end
            if e.function_name == "_hook_refresh_profile_knowledge"
        ]
        assert len(consolidate) == 1
        assert len(refresh) == 1
        assert consolidate[0].module == "core.engines.memory_insertion"
        assert refresh[0].module == "core.engines.memory_insertion"

    def test_no_hooks_registered_under_memory_world_model(self):
        report = inventory.build_report()
        for hook_type, entries in report.feature_hooks.items():
            for entry in entries:
                assert entry.module != "memory.world_model", (
                    f"Unexpected hook in memory.world_model: {hook_type} {entry.qualname}"
                )

    def test_prompt_context_hook_present_via_orchestrator_path(self):
        report = inventory.build_report()
        pre_route = report.feature_hooks.get("on_pre_route", [])
        matches = [
            e for e in pre_route
            if e.function_name == "_hook_record_user_turn_once"
        ]
        assert len(matches) == 1
        assert matches[0].module == "core.prompt_context"

    def test_prompt_context_hook_registered_exactly_once(self):
        # Re-importing prompt_context should not duplicate thanks to dedup guards.
        from core.feature_hooks import _HOOKS
        before = len(_HOOKS.get("on_pre_route", []))
        import core.prompt_context  # noqa: F401
        after = len(_HOOKS.get("on_pre_route", []))
        assert after == before

    def test_hook_entries_have_signatures(self):
        report = inventory.build_report()
        for hook_type, entries in report.feature_hooks.items():
            for entry in entries:
                assert entry.signature != "", f"Missing signature for {hook_type} {entry.qualname}"


class TestTailBlocks:
    def test_core_tail_blocks_present(self):
        report = inventory.build_report()
        names = {e.function_name for e in report.tail_blocks}
        assert "_tail_block_no_mutation_rule" in names
        assert "_tail_block_context_arbitration" in names
        assert "_tail_block_document_qa_rule" in names

    def test_tail_block_entries_have_signatures(self):
        report = inventory.build_report()
        for entry in report.tail_blocks:
            assert entry.signature != "", f"Missing signature for {entry.qualname}"


class TestReportToDict:
    def test_report_roundtrips_to_dict(self):
        report = inventory.build_report()
        data = inventory.report_to_dict(report)
        summary = data["summary"]
        assert summary["route_interceptor_count"] == len(report.route_interceptors)
        assert summary["tail_block_count"] == len(report.tail_blocks)
        for hook_type, count in summary["feature_hook_counts"].items():
            assert count == len(report.feature_hooks[hook_type])
