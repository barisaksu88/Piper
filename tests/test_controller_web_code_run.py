"""Tests for web UI code_run path normalization in PiperController.

These tests require no LLM, no web search, and no external services.
They verify that absolute workspace paths sent by the frontend are
normalized to relative paths before save/run.
"""

from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memory.vision_session import VisionSessionMemory
import ui.controller as _controller_module
import ui.controller_actions as _controller_actions_module
import ui.controller_queue as _controller_queue_module
import ui.controller_status as _controller_status_module
from ui.controller import PiperController


class FakeDpg:
    def does_item_exist(self, tag) -> bool:  # noqa: ANN001
        del tag
        return True

    def configure_item(self, tag, **kwargs) -> None:  # noqa: ANN001
        del tag, kwargs

    def set_item_label(self, tag, label: str) -> None:  # noqa: ANN001
        del tag, label

    def get_value(self, tag):  # noqa: ANN001
        del tag
        return ""

    def set_value(self, tag, value) -> None:  # noqa: ANN001
        del tag, value


_original_dpgs = {
    "controller": _controller_module.dpg,
    "actions": _controller_actions_module.dpg,
    "queue": _controller_queue_module.dpg,
    "status": _controller_status_module.dpg,
}


def setup_module() -> None:
    fake = FakeDpg()
    _controller_module.dpg = fake
    _controller_actions_module.dpg = fake
    _controller_queue_module.dpg = fake
    _controller_status_module.dpg = fake


def teardown_module() -> None:
    _controller_module.dpg = _original_dpgs["controller"]
    _controller_actions_module.dpg = _original_dpgs["actions"]
    _controller_queue_module.dpg = _original_dpgs["queue"]
    _controller_status_module.dpg = _original_dpgs["status"]


class FakeAgentBrain:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace


def _make_controller(workspace: Path) -> PiperController:
    """Build a minimal PiperController for path-normalization tests."""
    ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
    agent_brain = FakeAgentBrain(workspace)
    chat_state = SimpleNamespace(finalize_streaming_assistant=lambda: None)
    return PiperController(
        app_title="test",
        width=800,
        height=600,
        ui_queue=ui_queue,
        chat_state=chat_state,
        style_mgr=None,
        tts=None,
        llm=None,
        knowledge_mgr=None,
        document_mgr=None,
        agent_brain=agent_brain,
        prompt_context_service=None,
        user_runtime=None,
        boot_mgr=None,
        img_gen=None,
        live_screen=None,
        vision_session_memory=VisionSessionMemory(),
        searxng_service=None,
    )


class TestWorkspaceRelativeWebPath:
    """Guard tests for _workspace_relative_web_path."""

    def test_relative_path_passthrough(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        assert ctrl._workspace_relative_web_path("counter.py") == "counter.py"

    def test_relative_path_with_backslash(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        assert ctrl._workspace_relative_web_path("foo\\bar.py") == "foo/bar.py"

    def test_absolute_path_inside_workspace(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        absolute = str(tmp_path / "counter.py")
        assert ctrl._workspace_relative_web_path(absolute) == "counter.py"

    def test_absolute_path_in_subdir(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        absolute = str(tmp_path / "src" / "main.py")
        assert ctrl._workspace_relative_web_path(absolute) == "src/main.py"

    def test_empty_path_raises(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        with pytest.raises(ValueError, match="Path is required"):
            ctrl._workspace_relative_web_path("")

    def test_whitespace_only_path_raises(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        with pytest.raises(ValueError, match="Path is required"):
            ctrl._workspace_relative_web_path("   ")

    def test_path_outside_workspace_raises(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        outside = str(tmp_path.parent / "evil.py")
        with pytest.raises(ValueError, match="Path outside workspace"):
            ctrl._workspace_relative_web_path(outside)

    def test_dotdot_path_raises(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        with pytest.raises(ValueError, match="Path escapes workspace"):
            ctrl._workspace_relative_web_path("../evil.py")

    def test_dotdot_in_absolute_path_raises(self, tmp_path: Path) -> None:
        ctrl = _make_controller(tmp_path)
        outside = str(tmp_path.parent / "evil.py")
        with pytest.raises(ValueError, match="Path outside workspace"):
            ctrl._workspace_relative_web_path(outside)


class TestCodeRunDispatch:
    """Smoke tests for _dispatch_web_action code_run handler."""

    def test_code_run_with_absolute_path_normalizes_and_saves(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        ctrl = _make_controller(workspace)

        # Patch start_code_session to capture the normalized path
        started_paths: list[str] = []

        def _capture_start(rel_path: str) -> None:
            started_paths.append(rel_path)
            # Don't call the real implementation (it would try to spawn a process)

        ctrl.start_code_session = _capture_start  # type: ignore[method-assign]

        absolute_path = str(workspace / "counter.py")
        ctrl._dispatch_web_action(
            "code_run",
            {"path": absolute_path, "content": "print(42)"},
        )

        # Verify file was saved using normalized relative path
        saved_file = workspace / "counter.py"
        assert saved_file.exists()
        assert saved_file.read_text(encoding="utf-8") == "print(42)"

        # Verify start_code_session received the relative path
        assert started_paths == ["counter.py"]

        # Verify a queued log event mentions the relative path
        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        log_events = [e for e in events if e[0] == "agent_log"]
        assert log_events
        assert "counter.py" in str(log_events[-1][1])

    def test_code_run_rejects_path_outside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "code_run",
            {"path": str(tmp_path / "evil.py"), "content": "print(42)"},
        )

        # File should NOT be created outside workspace
        assert not (tmp_path / "evil.py").exists()

        # An error chat event should be queued
        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        chat_events = [e for e in events if e[0] == "chat_append"]
        assert any("Path outside workspace" in str(e[1].get("content", "")) for e in chat_events)
