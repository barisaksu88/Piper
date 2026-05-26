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


class TestReadWorkspaceFileDispatch:
    """Smoke tests for _dispatch_web_action read_workspace_file handler."""

    def test_read_absolute_path_inside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "hello.py").write_text("print(1)", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "read_workspace_file",
            {"path": str(workspace / "hello.py")},
        )

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        file_events = [e for e in events if e[0] == "file_contents"]
        assert len(file_events) == 1
        payload = file_events[0][1]
        assert payload.get("content") == "print(1)"
        assert not payload.get("error")

    def test_read_relative_path(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "hello.py").write_text("print(2)", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "read_workspace_file",
            {"path": "hello.py"},
        )

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        file_events = [e for e in events if e[0] == "file_contents"]
        assert len(file_events) == 1
        payload = file_events[0][1]
        assert payload.get("content") == "print(2)"
        assert not payload.get("error")

    def test_read_rejects_absolute_outside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "evil.py"
        outside.write_text("bad", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "read_workspace_file",
            {"path": str(outside)},
        )

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        file_events = [e for e in events if e[0] == "file_contents"]
        assert len(file_events) == 1
        payload = file_events[0][1]
        assert payload.get("error") == "Access denied"

    def test_read_rejects_sibling_prefix_outside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        evil_dir = tmp_path / "ws_evil"
        evil_dir.mkdir()
        (evil_dir / "file.txt").write_text("leak", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "read_workspace_file",
            {"path": str(evil_dir / "file.txt")},
        )

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        file_events = [e for e in events if e[0] == "file_contents"]
        assert len(file_events) == 1
        payload = file_events[0][1]
        assert payload.get("error") == "Access denied"


class TestSaveWorkspaceFileDispatch:
    """Smoke tests for _dispatch_web_action save_workspace_file handler."""

    def test_save_absolute_path_inside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "save_workspace_file",
            {"path": str(workspace / "saved.py"), "content": "x = 1"},
        )

        assert (workspace / "saved.py").read_text(encoding="utf-8") == "x = 1"

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        log_events = [e for e in events if e[0] == "agent_log"]
        assert log_events
        assert "saved.py" in str(log_events[-1][1])

    def test_save_relative_path(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "save_workspace_file",
            {"path": "saved.py", "content": "x = 2"},
        )

        assert (workspace / "saved.py").read_text(encoding="utf-8") == "x = 2"

    def test_save_rejects_absolute_outside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "evil.py"
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "save_workspace_file",
            {"path": str(outside), "content": "bad"},
        )

        assert not outside.exists()

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        chat_events = [e for e in events if e[0] == "chat_append"]
        assert any("Save denied" in str(e[1].get("content", "")) for e in chat_events)

    def test_save_rejects_sibling_prefix_outside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        evil_dir = tmp_path / "ws_evil"
        evil_dir.mkdir()
        outside_file = evil_dir / "file.txt"
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action(
            "save_workspace_file",
            {"path": str(outside_file), "content": "leak"},
        )

        assert not outside_file.exists()

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        chat_events = [e for e in events if e[0] == "chat_append"]
        assert any("Save denied" in str(e[1].get("content", "")) for e in chat_events)


class TestListWorkspaceFilesDispatch:
    """Smoke tests for _dispatch_web_action list_workspace_files handler."""

    def test_includes_top_level_supported_files(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "a.py").write_text("a", encoding="utf-8")
        (workspace / "b.txt").write_text("b", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action("list_workspace_files", {})

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        file_events = [e for e in events if e[0] == "workspace_files"]
        assert len(file_events) == 1
        payload = file_events[0][1]
        names = [f["name"] for f in payload["files"]]
        assert "a.py" in names
        assert "b.txt" in names

    def test_includes_nested_supported_files(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        nested = workspace / "subdir"
        nested.mkdir()
        (nested / "deep.py").write_text("deep", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action("list_workspace_files", {})

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        payload = events[0][1]
        names = [f["name"] for f in payload["files"]]
        assert "deep.py" in names

    def test_excludes_unsupported_suffixes(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "good.py").write_text("good", encoding="utf-8")
        (workspace / "bad.exe").write_text("bad", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action("list_workspace_files", {})

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        payload = events[0][1]
        names = [f["name"] for f in payload["files"]]
        assert "good.py" in names
        assert "bad.exe" not in names

    def test_excludes_directories(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "file.py").write_text("file", encoding="utf-8")
        (workspace / "folder").mkdir()
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action("list_workspace_files", {})

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        payload = events[0][1]
        names = [f["name"] for f in payload["files"]]
        assert "file.py" in names
        assert "folder" not in names

    def test_returns_stable_sorted_output(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "z.py").write_text("z", encoding="utf-8")
        (workspace / "a.py").write_text("a", encoding="utf-8")
        nested = workspace / "sub"
        nested.mkdir()
        (nested / "m.py").write_text("m", encoding="utf-8")
        ctrl = _make_controller(workspace)

        ctrl._dispatch_web_action("list_workspace_files", {})

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        payload = events[0][1]
        names = [f["name"] for f in payload["files"]]
        assert names == sorted(names)

    def test_symlink_outside_workspace_not_listed(self, tmp_path: Path) -> None:
        import os

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / " legit.py").write_text("ok", encoding="utf-8")
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        link = workspace / "link.txt"
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")

        ctrl = _make_controller(workspace)
        ctrl._dispatch_web_action("list_workspace_files", {})

        events = []
        while not ctrl.ui_queue.empty():
            events.append(ctrl.ui_queue.get_nowait())
        payload = events[0][1]
        names = [f["name"] for f in payload["files"]]
        assert "link.txt" not in names
        assert " legit.py" in names
