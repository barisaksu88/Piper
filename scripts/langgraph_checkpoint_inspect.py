from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG  # noqa: E402


_OMITTED_SCRATCHPAD = "<scratchpad omitted; pass --scratchpad to include bounded entries>"


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    )


def _compact_value(
    value: Any,
    *,
    max_chars: int,
    include_scratchpad: bool,
    key_name: str = "",
    depth: int = 0,
) -> Any:
    max_chars = max(80, int(max_chars or 500))
    if key_name == "scratchpad" and not include_scratchpad:
        try:
            return {
                "omitted": _OMITTED_SCRATCHPAD,
                "entries": len(value or []),
            }
        except Exception:
            return {"omitted": _OMITTED_SCRATCHPAD}
    if depth >= 5:
        text = repr(value)
        return text[:max_chars] + ("..." if len(text) > max_chars else "")
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, key in enumerate(sorted(value.keys(), key=lambda item: str(item))):
            if index >= 30:
                compact["..."] = f"{len(value) - index} more key(s)"
                break
            key_text = str(key)
            compact[key_text] = _compact_value(
                value[key],
                max_chars=max_chars,
                include_scratchpad=include_scratchpad,
                key_name=key_text,
                depth=depth + 1,
            )
        return compact
    if isinstance(value, (list, tuple)):
        compact_items = [
            _compact_value(
                item,
                max_chars=max_chars,
                include_scratchpad=include_scratchpad,
                key_name=key_name,
                depth=depth + 1,
            )
            for item in list(value)[:30]
        ]
        if len(value) > 30:
            compact_items.append(f"... {len(value) - 30} more item(s)")
        return compact_items
    if isinstance(value, str):
        return value[:max_chars] + ("..." if len(value) > max_chars else "")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = repr(value)
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _compact_channel_values(
    channel_values: dict[str, Any],
    *,
    max_chars: int,
    include_scratchpad: bool,
) -> dict[str, Any]:
    preferred_order = [
        "next_stage",
        "stage_trace",
        "stage_timings",
        "user_msg",
        "route_decision",
        "context_card",
        "last_stage_outcome",
        "last_verification",
        "pending_file_target_confirmation",
        "interrupt_before_stage",
        "interrupt_payload",
        "interrupt_resume_value",
        "scratchpad",
    ]
    ordered_keys = [key for key in preferred_order if key in channel_values]
    ordered_keys.extend(
        key
        for key in sorted(channel_values.keys(), key=str)
        if key not in ordered_keys and not str(key).startswith("__")
    )
    return {
        str(key): _compact_value(
            channel_values[key],
            max_chars=max_chars,
            include_scratchpad=include_scratchpad,
            key_name=str(key),
        )
        for key in ordered_keys
    }


def _checkpoint_item(
    row: Any,
    *,
    include_values: bool = False,
    include_scratchpad: bool = False,
    max_value_chars: int = 500,
) -> dict[str, Any]:
    config = dict(getattr(row, "config", {}) or {})
    configurable = dict(config.get("configurable") or {})
    checkpoint = dict(getattr(row, "checkpoint", {}) or {})
    metadata = dict(getattr(row, "metadata", {}) or {})
    channel_values = dict(checkpoint.get("channel_values") or {})
    parent_config = getattr(row, "parent_config", None)
    parent_checkpoint_id = ""
    if isinstance(parent_config, dict):
        parent_checkpoint_id = str(
            (parent_config.get("configurable") or {}).get("checkpoint_id", "") or ""
        )
    item = {
        "thread_id": str(configurable.get("thread_id", "") or ""),
        "checkpoint_id": str(configurable.get("checkpoint_id", "") or ""),
        "parent_checkpoint_id": parent_checkpoint_id,
        "timestamp": str(checkpoint.get("ts", "") or ""),
        "source": str(metadata.get("source", "") or ""),
        "step": metadata.get("step"),
        "next_stage": str(channel_values.get("next_stage", "") or ""),
        "stage_trace": channel_values.get("stage_trace") or [],
        "updated_channels": checkpoint.get("updated_channels") or [],
        "channel_keys": sorted(str(key) for key in channel_values.keys()),
        "pending_writes": len(getattr(row, "pending_writes", []) or []),
    }
    if include_values:
        item["values"] = _compact_channel_values(
            channel_values,
            max_chars=max_value_chars,
            include_scratchpad=include_scratchpad,
        )
    return item


def _thread_summaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in items:
        thread_id = str(item.get("thread_id") or "")
        if not thread_id:
            continue
        if thread_id not in summaries:
            summaries[thread_id] = {
                "thread_id": thread_id,
                "latest_checkpoint_id": str(item.get("checkpoint_id") or ""),
                "latest_timestamp": str(item.get("timestamp") or ""),
                "latest_step": item.get("step"),
                "latest_next_stage": str(item.get("next_stage") or ""),
                "latest_stage_trace": list(item.get("stage_trace") or []),
                "listed_checkpoints": 0,
            }
        summaries[thread_id]["listed_checkpoints"] = int(summaries[thread_id]["listed_checkpoints"] or 0) + 1
    return list(summaries.values())


def _find_checkpoint_row(saver: Any, *, thread_id: str, checkpoint_id: str, scan_limit: int) -> Any | None:
    checkpoint_id = str(checkpoint_id or "").strip()
    if not checkpoint_id:
        return None
    if thread_id:
        try:
            row = saver.get_tuple(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": checkpoint_id,
                    }
                }
            )
            if row is not None:
                return row
        except Exception:
            pass
    try:
        rows = list(saver.list(None, limit=max(1, int(scan_limit or 1))))
    except Exception:
        rows = []
    for row in rows:
        config = dict(getattr(row, "config", {}) or {})
        configurable = dict(config.get("configurable") or {})
        if str(configurable.get("checkpoint_id") or "") == checkpoint_id:
            if not thread_id or str(configurable.get("thread_id") or "") == thread_id:
                return row
    return None


def inspect_checkpoints(
    *,
    path: Path,
    limit: int = 8,
    thread_id: str = "",
    checkpoint_id: str = "",
    include_values: bool = False,
    include_scratchpad: bool = False,
    max_value_chars: int = 500,
) -> dict[str, Any]:
    path = Path(path)
    report: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "tables": {},
        "thread_id_filter": str(thread_id or ""),
        "checkpoint_id_filter": str(checkpoint_id or ""),
        "threads": [],
        "checkpoints": [],
        "selected_checkpoint": {},
        "error": "",
    }
    if not path.exists():
        return report

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except Exception as exc:
        report["error"] = f"Could not import LangGraph SQLite saver: {exc}"
        return report

    connection = sqlite3.connect(str(path))
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        for table_name in tables:
            try:
                count = int(connection.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0])
            except sqlite3.Error:
                count = -1
            report["tables"][table_name] = count

        if not _table_exists(connection, "checkpoints"):
            return report

        config = {"configurable": {"thread_id": thread_id}} if thread_id else None
        saver = SqliteSaver(connection)
        rows = list(saver.list(config, limit=max(1, int(limit or 1))))
        items = [
            _checkpoint_item(
                row,
                include_values=False,
                include_scratchpad=include_scratchpad,
                max_value_chars=max_value_chars,
            )
            for row in rows
        ]
        report["checkpoints"] = items
        report["threads"] = _thread_summaries(items)
        if checkpoint_id:
            selected = _find_checkpoint_row(
                saver,
                thread_id=str(thread_id or ""),
                checkpoint_id=str(checkpoint_id or ""),
                scan_limit=max(1000, int(limit or 1)),
            )
            if selected is not None:
                report["selected_checkpoint"] = _checkpoint_item(
                    selected,
                    include_values=include_values,
                    include_scratchpad=include_scratchpad,
                    max_value_chars=max_value_chars,
                )
            else:
                report["error"] = f"Checkpoint id not found: {checkpoint_id}"
        return report
    except Exception as exc:
        report["error"] = str(exc)
        return report
    finally:
        connection.close()


def _print_text_report(report: dict[str, Any]) -> None:
    print(f"Path: {report['path']}")
    print(f"Exists: {report['exists']}")
    print(f"Size: {report['size_bytes']} bytes")
    if report.get("error"):
        print(f"Error: {report['error']}")
    tables = dict(report.get("tables") or {})
    if tables:
        table_summary = ", ".join(f"{name}={count}" for name, count in tables.items())
        print(f"Tables: {table_summary}")
    checkpoints = list(report.get("checkpoints") or [])
    if not checkpoints:
        print("Checkpoints: none")
        return
    threads = list(report.get("threads") or [])
    if threads:
        print("Threads:")
        for item in threads:
            trace = " -> ".join(str(stage) for stage in item.get("latest_stage_trace", []))
            print(
                "- "
                f"thread={item.get('thread_id', '')} "
                f"listed={item.get('listed_checkpoints', 0)} "
                f"latest_step={item.get('latest_step')} "
                f"next={item.get('latest_next_stage', '') or '-'} "
                f"latest_id={item.get('latest_checkpoint_id', '')}"
            )
            if trace:
                print(f"  latest_trace={trace}")
    print("Latest checkpoints:")
    for item in checkpoints:
        trace = " -> ".join(str(stage) for stage in item.get("stage_trace", []))
        print(
            "- "
            f"thread={item.get('thread_id', '')} "
            f"step={item.get('step')} "
            f"source={item.get('source', '')} "
            f"next={item.get('next_stage', '') or '-'} "
            f"id={item.get('checkpoint_id', '')}"
        )
        if trace:
            print(f"  trace={trace}")
    selected = dict(report.get("selected_checkpoint") or {})
    if selected:
        print("Selected checkpoint:")
        print(
            f"- thread={selected.get('thread_id', '')} "
            f"step={selected.get('step')} "
            f"id={selected.get('checkpoint_id', '')}"
        )
        values = selected.get("values")
        if values:
            print(json.dumps(values, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Piper's LangGraph SQLite checkpoint store.")
    parser.add_argument("--path", type=Path, default=CFG.LANGGRAPH_CHECKPOINT_PATH)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--checkpoint-id", default="")
    parser.add_argument("--values", action="store_true", help="Include bounded state values for --checkpoint-id.")
    parser.add_argument("--scratchpad", action="store_true", help="Include bounded scratchpad values when --values is set.")
    parser.add_argument("--max-value-chars", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = inspect_checkpoints(
        path=args.path,
        limit=args.limit,
        thread_id=args.thread_id,
        checkpoint_id=args.checkpoint_id,
        include_values=args.values,
        include_scratchpad=args.scratchpad,
        max_value_chars=args.max_value_chars,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_text_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
