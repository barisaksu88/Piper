"""Guard tests for ChangeJournal.

These tests lock behavior for `ChangeJournal`.
They require no LLM, no web search, no threading, and no external services.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.services.change_journal import ChangeJournal


# ── 1. load/save behavior ────────────────────────────────────────────


class TestLoadSave:
    def test_load_entries_returns_empty_on_missing_file(self, tmp_path: Path) -> None:
        journal = ChangeJournal(tmp_path / "missing.json")
        assert journal.load_entries() == []

    def test_load_entries_returns_empty_on_corrupt_json(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.json"
        path.write_text("not json", encoding="utf-8")
        journal = ChangeJournal(path)
        assert journal.load_entries() == []

    def test_save_entries_prunes_to_max_entries(self, tmp_path: Path) -> None:
        # max_entries is clamped to a minimum of 5 in __init__
        journal = ChangeJournal(tmp_path / "journal.json", max_entries=5)
        entries = [{"n": i} for i in range(7)]
        journal.save_entries(entries)
        loaded = journal.load_entries()
        assert len(loaded) == 5
        assert loaded[-1]["n"] == 6


# ── 2. capture/finalize behavior ─────────────────────────────────────


class TestCaptureFinalize:
    def test_prepare_file_op_capture_skips_non_mutating_action(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        journal = ChangeJournal(tmp_path / "journal.json")
        payload = '{"action":"read_text","path":"a.txt"}'
        assert journal.prepare_file_op_capture(payload, workspace) is None

    def test_prepare_file_op_capture_snapshots_missing_parents_for_write_text(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        journal = ChangeJournal(tmp_path / "journal.json")
        payload = '{"action":"write_text","path":"deep/nested/file.txt","content":"hello"}'
        capture = journal.prepare_file_op_capture(payload, workspace)
        assert capture is not None
        paths = [s["path"] for s in capture.get("snapshots", [])]
        assert "deep" in paths
        assert "deep/nested" in paths
        assert "deep/nested/file.txt" in paths

    def test_finalize_file_op_capture_requires_executed_status(self, tmp_path: Path) -> None:
        journal = ChangeJournal(tmp_path / "journal.json")
        prepared = {
            "action": "write_text",
            "requested_paths": ["a.txt"],
            "snapshots": [{"path": "a.txt", "kind": "absent"}],
        }
        tool_result = {
            "action": "write_text",
            "status": "PENDING",
            "workspace_changed": True,
            "summary": "wrote file",
        }
        assert journal.finalize_file_op_capture(prepared, tool_result) is None

    def test_finalize_file_op_capture_requires_workspace_changed(self, tmp_path: Path) -> None:
        journal = ChangeJournal(tmp_path / "journal.json")
        prepared = {
            "action": "write_text",
            "requested_paths": ["a.txt"],
            "snapshots": [{"path": "a.txt", "kind": "absent"}],
        }
        tool_result = {
            "action": "write_text",
            "status": "EXECUTED",
            "workspace_changed": False,
            "summary": "wrote file",
        }
        assert journal.finalize_file_op_capture(prepared, tool_result) is None


# ── 3. record/undo state behavior ────────────────────────────────────


class TestRecordUndoState:
    def test_record_turn_returns_none_when_no_ops_and_no_manifests(self, tmp_path: Path) -> None:
        journal = ChangeJournal(tmp_path / "journal.json")
        result = journal.record_turn(
            turn_id="t1", user_msg="hi", task_goal="none", task_success=True, operations=[]
        )
        assert result is None

    def test_record_turn_includes_rollback_manifests(self, tmp_path: Path) -> None:
        journal = ChangeJournal(tmp_path / "journal.json")
        entry = journal.record_turn(
            turn_id="t2",
            user_msg="hi",
            task_goal="none",
            task_success=True,
            operations=[{"snapshots": [{"path": "a.txt", "kind": "absent"}]}],
            rollback_manifests=["/data/rollback/r1.json"],
        )
        assert entry is not None
        assert "/data/rollback/r1.json" in entry.get("rollback_manifests", [])

    def test_mark_entry_undone_finds_by_turn_id(self, tmp_path: Path) -> None:
        journal = ChangeJournal(tmp_path / "journal.json")
        journal.record_turn(
            turn_id="t3",
            user_msg="hi",
            task_goal="none",
            task_success=True,
            operations=[{"snapshots": [{"path": "a.txt", "kind": "absent"}]}],
        )
        journal.mark_entry_undone("t3", status="VERIFIED", detail="ok")
        latest = journal.peek_latest_entry()
        assert latest is not None
        assert latest.get("undo_last_status") == "VERIFIED"
        assert latest.get("undo_last_error") == "ok"
        assert str(latest.get("undone_at") or "").strip() != ""

    def test_has_pending_undo_true_and_false(self, tmp_path: Path) -> None:
        journal = ChangeJournal(tmp_path / "journal.json")
        assert journal.has_pending_undo() is False
        journal.record_turn(
            turn_id="t4",
            user_msg="hi",
            task_goal="none",
            task_success=True,
            operations=[{"snapshots": [{"path": "a.txt", "kind": "absent"}]}],
        )
        assert journal.has_pending_undo() is True
        journal.mark_entry_undone("t4")
        assert journal.has_pending_undo() is False


# ── 4. undo behavior ─────────────────────────────────────────────────


class TestUndoLatest:
    def test_undo_latest_fails_when_no_entries(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        journal = ChangeJournal(tmp_path / "journal.json")
        result = journal.undo_latest(workspace)
        assert result.get("status") == "FAILED"
        assert "no undoable" in result.get("summary", "").lower()

    def test_undo_latest_fails_when_already_undone(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        journal = ChangeJournal(tmp_path / "journal.json")
        journal.record_turn(
            turn_id="t5",
            user_msg="hi",
            task_goal="none",
            task_success=True,
            operations=[{"snapshots": [{"path": "a.txt", "kind": "absent"}]}],
        )
        journal.undo_latest(workspace)
        second = journal.undo_latest(workspace)
        assert second.get("status") == "FAILED"
        assert "already undone" in second.get("summary", "").lower()

    def test_undo_latest_restores_file_content(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "notes" / "a.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("before", encoding="utf-8")

        journal = ChangeJournal(tmp_path / "journal.json")
        payload = '{"action":"write_text","path":"notes/a.txt","content":"after"}'
        capture = journal.prepare_file_op_capture(payload, workspace)
        result = {
            "action": "write_text",
            "status": "EXECUTED",
            "workspace_changed": True,
            "summary": "wrote file",
            "path": "notes/a.txt",
        }
        op = journal.finalize_file_op_capture(capture, result)
        journal.record_turn(
            turn_id="t6", user_msg="hi", task_goal="none", task_success=True, operations=[op]
        )

        target.write_text("after", encoding="utf-8")
        undo = journal.undo_latest(workspace)
        assert undo.get("status") == "VERIFIED"
        assert target.read_text(encoding="utf-8") == "before"

    def test_undo_latest_removes_created_file(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        journal = ChangeJournal(tmp_path / "journal.json")
        payload = '{"action":"write_text","path":"new.txt","content":"hello"}'
        capture = journal.prepare_file_op_capture(payload, workspace)
        result = {
            "action": "write_text",
            "status": "EXECUTED",
            "workspace_changed": True,
            "summary": "wrote file",
            "path": "new.txt",
        }
        op = journal.finalize_file_op_capture(capture, result)
        journal.record_turn(
            turn_id="t7", user_msg="hi", task_goal="none", task_success=True, operations=[op]
        )

        target = workspace / "new.txt"
        target.write_text("hello", encoding="utf-8")
        undo = journal.undo_latest(workspace)
        assert undo.get("status") == "VERIFIED"
        assert not target.exists()

    def test_undo_latest_fails_gracefully_on_metadata_only(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        journal = ChangeJournal(tmp_path / "journal.json")
        op = {
            "action": "write_text",
            "summary": "wrote file",
            "requested_paths": ["big.bin"],
            "evidence_paths": ["big.bin"],
            "snapshots": [
                {
                    "path": "big.bin",
                    "kind": "file",
                    "size": 1234,
                    "snapshot_type": "metadata_only",
                }
            ],
        }
        journal.record_turn(
            turn_id="t8", user_msg="hi", task_goal="none", task_success=True, operations=[op]
        )
        result = journal.undo_latest(workspace)
        assert result.get("status") == "FAILED"
        detail = result.get("detail", "").lower()
        assert "not journaled" in detail or "metadata" in detail


# ── 5. snapshot policy ───────────────────────────────────────────────


class TestSnapshotPolicy:
    def test_snapshot_path_metadata_only_for_binary(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        path = workspace / "img.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nbinary")
        snapshot = ChangeJournal._snapshot_path(workspace, "img.png")
        assert snapshot["snapshot_type"] == "metadata_only"
        assert "content" not in snapshot
        assert "bytes_b64" not in snapshot

    def test_snapshot_path_metadata_only_for_large_text(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        path = workspace / "large.txt"
        path.write_text("a" * 1_000_001, encoding="utf-8")
        snapshot = ChangeJournal._snapshot_path(workspace, "large.txt")
        assert snapshot["snapshot_type"] == "metadata_only"
        assert snapshot.get("truncated") is True
        assert "content" not in snapshot

    def test_snapshot_path_content_for_normal_text(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        path = workspace / "small.txt"
        path.write_text("hello world", encoding="utf-8")
        snapshot = ChangeJournal._snapshot_path(workspace, "small.txt")
        assert snapshot.get("content") == "hello world"
        assert "snapshot_type" not in snapshot
