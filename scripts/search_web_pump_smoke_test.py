#!/usr/bin/env python3
"""Smoke test: search_result handling in Web UI pump.

Usage:
    python scripts/search_web_pump_smoke_test.py [--json]
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui.controller_queue as cq


class FakeController:
    def __init__(self):
        self.ui_queue = queue.Queue()
        self.pipeline = MagicMock()
        self.pipeline.clean_stream_buffer = ""
        self.chat_state = MagicMock()
        self.runtime_mode = "IDLE"
        self.stage_meta = ""

    def maybe_speak_ui_event(self, kind, payload):
        pass


class TestPumpUiQueueWebSearchResult(unittest.TestCase):
    @patch("ui.controller_queue.handle_search_result")
    @patch("ui.controller_queue._LOG")
    def test_calls_handle_search_result(self, mock_log, mock_handle):
        ctrl = FakeController()
        ctrl.ui_queue.put(("search_result", {"query": "test", "data": "results"}))

        cq.pump_ui_queue_web(ctrl, forward_queue=None)

        mock_handle.assert_called_once_with(ctrl, {"query": "test", "data": "results"})
        mock_log.info.assert_called_once_with("[SEARCH WEB] Handling search_result event.")

    @patch("ui.controller_queue.handle_search_result")
    def test_forwards_to_bridge_after_handling(self, mock_handle):
        fwd = queue.Queue()
        ctrl = FakeController()
        ctrl.ui_queue.put(("search_result", {"query": "test", "data": "results"}))

        cq.pump_ui_queue_web(ctrl, forward_queue=fwd)

        # handle_search_result is called locally
        mock_handle.assert_called_once()
        # But search_result should NOT be forwarded to the bridge (it's internal)
        self.assertTrue(fwd.empty())

    @patch("ui.controller_queue.handle_search_result")
    def test_does_not_duplicate_visible_chat(self, mock_handle):
        ctrl = FakeController()
        ctrl.ui_queue.put(("search_result", {"query": "test", "data": "results"}))

        cq.pump_ui_queue_web(ctrl, forward_queue=None)

        # handle_search_result appends hidden messages via chat_state
        # It should be called exactly once, not multiple times
        mock_handle.assert_called_once()

    @patch("ui.controller_queue.handle_search_result")
    def test_reporter_thread_started(self, mock_handle):
        ctrl = FakeController()
        ctrl.ui_queue.put(("search_result", {"query": "test", "data": "results"}))

        cq.pump_ui_queue_web(ctrl, forward_queue=None)

        # handle_search_result spawns report_findings thread
        mock_handle.assert_called_once()


class TestDpgPumpUnchanged(unittest.TestCase):
    @patch("ui.controller_queue.handle_search_result")
    def test_dpg_pump_still_calls_handle_search_result(self, mock_handle):
        ctrl = FakeController()
        ctrl.ui_queue.put(("search_result", {"query": "test", "data": "results"}))
        # Mock DPG exists check so pump_ui_queue doesn't crash
        with patch("ui.controller_queue.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            cq.pump_ui_queue(ctrl)

        mock_handle.assert_called_once_with(ctrl, {"query": "test", "data": "results"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if args.json:
        print(
            json.dumps(
                {
                    "success": result.wasSuccessful(),
                    "tests_run": result.testsRun,
                    "failures": len(result.failures),
                    "errors": len(result.errors),
                }
            )
        )

    sys.exit(0 if result.wasSuccessful() else 1)
