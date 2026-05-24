"""scripts/bulk_rollback_manifest_smoke_test.py

Smoke tests for the bulk mutation rollback manifest engine (§13.18).

Covers:
  1. record_manifest writes a valid manifest from a consolidate result
  2. invert_manifest moves files back to their original locations
  3. invert_manifest marks rolled_back=True on the manifest after success
  4. A second invert call is refused (rolled_back guard)
  5. move_many round-trip — same as consolidate path
  6. delete_many — records deletions, reports non-recoverable gracefully
  7. is_bulk_action recognises the four bulk ops and rejects others
  8. record_manifest returns None when there are no moves or deletions
  9. _prune_old_manifests keeps at most 5 manifests
 10. ChangeJournal.record_turn stores rollback_manifest path on entry
 11. ChangeJournal.mark_entry_undone sets undone_at on the right entry
 12. ISO turn ids produce Windows-safe manifest filenames
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.services.rollback_engine import (
    _prune_old_manifests,
    _safe_turn_id,
    invert_manifest,
    is_bulk_action,
    record_manifest,
)
from core.services.change_journal import ChangeJournal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ws(tmp: Path) -> Path:
    ws = tmp / "workspace"
    ws.mkdir()
    return ws


def _make_data(tmp: Path) -> Path:
    d = tmp / "data"
    d.mkdir()
    return d


def _place(ws: Path, rel: str, content: str = "x") -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _consolidate_result(moves: list[tuple[str, str]], created_dirs: list[str] | None = None) -> dict:
    return {
        "tool": "FILE_OP",
        "action": "consolidate_by_extension",
        "status": "EXECUTED",
        "workspace_changed": True,
        "requested_moves": [{"src": s, "dst": d} for s, d in moves],
        "created_dirs": created_dirs or [],
        "deleted_files": [],
        "summary": "Consolidated files.",
    }


def run_all() -> None:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, detail))
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))

    # ------------------------------------------------------------------
    # Case 1 — record_manifest writes valid JSON
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        data_dir = _make_data(tmp)
        result = _consolidate_result([("alpha.txt", "txt/alpha.txt"), ("beta.txt", "txt/beta.txt")])
        path = record_manifest("turn-001", "consolidate_by_extension", result, data_dir)
        if path is None:
            case("record_manifest writes manifest", False, "returned None")
        else:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            ok = (
                manifest.get("committed") is True
                and manifest.get("rolled_back") is False
                and len(manifest.get("moves") or []) == 2
                and manifest["moves"][0]["from"] == "alpha.txt"
                and manifest["moves"][0]["to"] == "txt/alpha.txt"
            )
            case("record_manifest writes manifest", ok, str(manifest) if not ok else "")

    # ------------------------------------------------------------------
    # Case 2 — invert_manifest moves files back
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        ws = _make_ws(tmp)
        data_dir = _make_data(tmp)
        # Set up workspace: files already in their consolidated positions
        _place(ws, "txt/alpha.txt", "hello")
        _place(ws, "txt/beta.txt", "world")
        moves = [("alpha.txt", "txt/alpha.txt"), ("beta.txt", "txt/beta.txt")]
        result = _consolidate_result(moves, created_dirs=["txt"])
        manifest_path = record_manifest("turn-002", "consolidate_by_extension", result, data_dir)
        inv = invert_manifest(manifest_path, ws)
        ok = (
            inv["status"] == "VERIFIED"
            and (ws / "alpha.txt").exists()
            and (ws / "beta.txt").exists()
            and not (ws / "txt" / "alpha.txt").exists()
        )
        case("invert_manifest moves files back", ok, inv.get("detail", "") if not ok else "")

    # ------------------------------------------------------------------
    # Case 3 — manifest marked rolled_back after invert
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        ws = _make_ws(tmp)
        data_dir = _make_data(tmp)
        _place(ws, "txt/a.txt")
        result = _consolidate_result([("a.txt", "txt/a.txt")])
        manifest_path = record_manifest("turn-003", "consolidate_by_extension", result, data_dir)
        invert_manifest(manifest_path, ws)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        case("manifest marked rolled_back after invert", manifest.get("rolled_back") is True)

    # ------------------------------------------------------------------
    # Case 4 — second invert is refused
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        ws = _make_ws(tmp)
        data_dir = _make_data(tmp)
        _place(ws, "txt/a.txt")
        result = _consolidate_result([("a.txt", "txt/a.txt")])
        manifest_path = record_manifest("turn-004", "consolidate_by_extension", result, data_dir)
        invert_manifest(manifest_path, ws)
        second = invert_manifest(manifest_path, ws)
        case("second invert is refused", second["status"] == "FAILED", second.get("summary", ""))

    # ------------------------------------------------------------------
    # Case 5 — move_many round-trip
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        ws = _make_ws(tmp)
        data_dir = _make_data(tmp)
        _place(ws, "archive/report.txt", "data")
        move_result = {
            "tool": "FILE_OP",
            "action": "move_many",
            "status": "EXECUTED",
            "workspace_changed": True,
            "requested_moves": [{"src": "report.txt", "dst": "archive/report.txt"}],
            "created_dirs": ["archive"],
            "deleted_files": [],
            "summary": "Moved 1 file.",
        }
        manifest_path = record_manifest("turn-005", "move_many", move_result, data_dir)
        inv = invert_manifest(manifest_path, ws)
        ok = inv["status"] == "VERIFIED" and (ws / "report.txt").exists()
        case("move_many round-trip", ok, inv.get("detail", "") if not ok else "")

    # ------------------------------------------------------------------
    # Case 6 — delete_many records deletions, reports non-recoverable
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        ws = _make_ws(tmp)
        data_dir = _make_data(tmp)
        del_result = {
            "tool": "FILE_OP",
            "action": "delete_many",
            "status": "EXECUTED",
            "workspace_changed": True,
            "requested_moves": [],
            "deleted_files": ["old/cache.tmp"],
            "created_dirs": [],
            "summary": "Deleted 1 file.",
        }
        manifest_path = record_manifest("turn-006", "delete_many", del_result, data_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        inv = invert_manifest(manifest_path, ws)
        ok = (
            len(manifest.get("deletions") or []) == 1
            and inv["status"] in ("FAILED", "PARTIAL")
            and "cannot be restored" in inv.get("detail", "")
        )
        case("delete_many records deletions, reports non-recoverable", ok, inv.get("detail", "") if not ok else "")

    # ------------------------------------------------------------------
    # Case 7 — is_bulk_action
    # ------------------------------------------------------------------
    bulk = ["consolidate_by_extension", "move_many", "copy_many", "delete_many"]
    non_bulk = ["move_path", "write_text", "delete_path", "ensure_dir", "RUN_CODE"]
    ok7 = all(is_bulk_action(a) for a in bulk) and not any(is_bulk_action(a) for a in non_bulk)
    case("is_bulk_action recognises bulk ops", ok7, str([a for a in non_bulk if is_bulk_action(a)]) if not ok7 else "")

    # ------------------------------------------------------------------
    # Case 8 — record_manifest returns None with no moves/deletions
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        data_dir = _make_data(tmp)
        empty_result = {
            "tool": "FILE_OP", "action": "consolidate_by_extension",
            "status": "EXECUTED", "workspace_changed": False,
            "requested_moves": [], "deleted_files": [], "created_dirs": [],
        }
        ret = record_manifest("turn-008", "consolidate_by_extension", empty_result, data_dir)
        case("record_manifest returns None for empty result", ret is None)

    # ------------------------------------------------------------------
    # Case 9 — _prune_old_manifests keeps at most 5
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        data_dir = _make_data(tmp)
        result = _consolidate_result([("a.txt", "d/a.txt")])
        for i in range(8):
            record_manifest(f"prune-{i:03d}", "consolidate_by_extension", result, data_dir)
        remaining = list((data_dir / "rollback").glob("rollback_*.json"))
        case("_prune_old_manifests keeps at most 5", len(remaining) <= 5, f"found {len(remaining)}")

    # ------------------------------------------------------------------
    # Case 10 — ChangeJournal.record_turn stores rollback_manifest path
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        data_dir = _make_data(tmp)
        journal = ChangeJournal(data_dir / "change_journal.json")
        fake_manifest_path = str(data_dir / "rollback" / "rollback_turn-010.json")
        entry = journal.record_turn(
            turn_id="turn-010",
            user_msg="consolidate files",
            task_goal="Organise by extension",
            task_success=True,
            operations=[],
            rollback_manifests=[fake_manifest_path],
        )
        ok = (
            entry is not None
            and fake_manifest_path in (entry.get("rollback_manifests") or [])
        )
        case("record_turn stores rollback_manifest path", ok, str(entry) if not ok else "")

    # ------------------------------------------------------------------
    # Case 11 — ChangeJournal.mark_entry_undone
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        data_dir = _make_data(tmp)
        journal = ChangeJournal(data_dir / "change_journal.json")
        journal.record_turn(
            turn_id="turn-011",
            user_msg="consolidate files",
            task_goal="Organise",
            task_success=True,
            operations=[],
            rollback_manifests=["fake/path.json"],
        )
        journal.mark_entry_undone("turn-011", status="VERIFIED", detail="ok")
        latest = journal.peek_latest_entry()
        ok = bool(latest and latest.get("undone_at") and latest.get("undo_last_status") == "VERIFIED")
        case("mark_entry_undone sets undone_at", ok, str(latest) if not ok else "")

    # ------------------------------------------------------------------
    # Case 12 — ISO turn ids produce Windows-safe manifest filenames
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        data_dir = _make_data(tmp)
        turn_id = "2026-04-09T01:17:17.261+00:00"
        result = _consolidate_result([("alpha.txt", "text/alpha.txt")])
        manifest_path = record_manifest(turn_id, "consolidate_by_extension", result, data_dir)
        filename = manifest_path.name if manifest_path is not None else ""
        safe_turn = _safe_turn_id(turn_id)
        ok = bool(
            manifest_path is not None
            and ":" not in filename
            and "+" not in filename
            and safe_turn
            and filename == f"rollback_{safe_turn}.json"
        )
        case("ISO turn ids produce Windows-safe manifest filenames", ok, filename if not ok else "")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    print(f"\n{'='*50}")
    print(f"Bulk rollback manifest smoke test: {passed}/{total} passed")
    if passed < total:
        print("FAILED cases:")
        for name, p, detail in results:
            if not p:
                print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
        sys.exit(1)
    else:
        print("All cases passed.")


if __name__ == "__main__":
    run_all()
