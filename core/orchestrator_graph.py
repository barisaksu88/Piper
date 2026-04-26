from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, TypedDict

from config import CFG
from core.contracts import StageOutcomePack
from core.engines.verification import VerificationResult
from core.orchestrator import Orchestrator, OrchestratorConfig
from core.runtime_control import OperationCancelled
from memory.storage import append_jsonl, ensure_parent

_LOG = logging.getLogger(__name__)

_STAGE_NODE_BY_NAME = {
    "ROUTE": "route",
    "DOC_FOCUS": "document_focus",
    "SEARCH": "search",
    "REPORTER": "reporter",
    "MANAGER": "manager",
    "UNDO": "undo",
    "REMINDER_SET": "reminder_set",
    "EXPLAIN": "explain",
    "PERSONA": "persona",
}


class OrchestratorGraphState(TypedDict, total=False):
    next_stage: str
    stage_trace: list[str]
    stage_timings: list[dict[str, Any]]
    user_msg: str
    route_decision: dict[str, Any]
    context_card: dict[str, Any]
    scratchpad: list[str]
    ingested_document_chat: bool
    document_focus_text: str
    document_focus_refs: list[str]
    document_focus_sources: list[str]
    turn_screen_image_path: str
    turn_screen_image_kind: str
    latest_codex_escalation: dict[str, Any] | None
    failed_task_router_retries: int
    last_stage_outcome: dict[str, Any] | None
    last_verification: dict[str, Any] | None
    route_interceptor: str
    reporter_just_ran: bool
    latest_search_summary: str
    latest_search_failed: bool
    latest_search_error: str
    synthetic_user_turn: bool
    is_search_result: bool
    pending_file_target_confirmation: dict[str, Any] | None
    pending_stage_pause: dict[str, Any] | None
    interrupt_before_stage: str
    interrupt_payload: dict[str, Any]
    interrupt_resume_value: Any
    langgraph_interrupt_consumed: bool


@dataclass(frozen=True)
class OrchestratorGraphContext:
    orchestrator: Orchestrator


@dataclass
class _CheckpointHandle:
    checkpointer: Any | None
    mode: str
    path: Path | None = None
    history_limit: int = 0
    connection: sqlite3.Connection | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None


@dataclass
class OrchestratorGraphRuntime:
    graph: Any
    checkpoint_mode: str
    checkpoint_path: Path | None = None
    checkpoint_history_limit: int = 0
    _checkpoint_handle: _CheckpointHandle | None = None

    def prune_checkpoints(self) -> int:
        if self._checkpoint_handle is None or self._checkpoint_handle.connection is None:
            return 0
        return _prune_sqlite_checkpoint_store(
            self._checkpoint_handle.connection,
            max_checkpoints=self.checkpoint_history_limit,
        )

    def close(self) -> None:
        if self._checkpoint_handle is not None:
            self._checkpoint_handle.close()
            self._checkpoint_handle = None


def _normalize_checkpoint_mode(raw_mode: Any) -> str:
    mode = str(raw_mode or "sqlite").strip().lower()
    aliases = {
        "": "sqlite",
        "off": "none",
        "disable": "none",
        "disabled": "none",
        "false": "none",
        "0": "none",
        "in_memory": "memory",
        "in-memory": "memory",
        "inmemory": "memory",
        "sqlite3": "sqlite",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"none", "memory", "sqlite"}:
        raise RuntimeError(
            f"Unsupported LangGraph checkpoint mode '{raw_mode}'. "
            "Use 'sqlite', 'memory', or 'none'."
        )
    return mode


def _configured_checkpoint_path() -> Path:
    return Path(getattr(CFG, "LANGGRAPH_CHECKPOINT_PATH"))


def _configured_checkpoint_history_limit() -> int:
    return max(1, int(getattr(CFG, "LANGGRAPH_CHECKPOINT_HISTORY_LIMIT", 500) or 500))


def _recovery_path(path: Path | str | None = None) -> Path:
    return Path(path) if path is not None else Path(getattr(CFG, "LANGGRAPH_RECOVERY_PATH"))


def _interrupt_path(path: Path | str | None = None) -> Path:
    return Path(path) if path is not None else Path(getattr(CFG, "LANGGRAPH_INTERRUPT_PATH"))


def load_langgraph_recovery_record(*, path: Path | str | None = None) -> dict[str, Any]:
    record_path = _recovery_path(path)
    if not record_path.exists():
        return {}
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_langgraph_recovery_record(record: dict[str, Any], *, path: Path | str | None = None) -> None:
    record_path = _recovery_path(path)
    ensure_parent(record_path)
    payload = _serialize_state_value(dict(record or {}))
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{record_path.name}.",
        suffix=".tmp",
        dir=str(record_path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, record_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise


def clear_langgraph_recovery_record(
    *,
    path: Path | str | None = None,
    thread_id: str = "",
) -> bool:
    record_path = _recovery_path(path)
    if not record_path.exists():
        return False
    if thread_id:
        current = load_langgraph_recovery_record(path=record_path)
        current_thread_id = str(current.get("thread_id") or "").strip()
        if current_thread_id and current_thread_id != str(thread_id or "").strip():
            return False
    try:
        record_path.unlink()
        return True
    except OSError:
        return False


def describe_langgraph_recovery_record(*, path: Path | str | None = None) -> str:
    record = load_langgraph_recovery_record(path=path)
    if not record:
        return "[LangGraph] No recoverable graph turn is recorded."
    thread_id = str(record.get("thread_id") or "").strip() or "?"
    next_nodes = record.get("checkpoint_next") or []
    next_text = ", ".join(str(item) for item in next_nodes) if next_nodes else "unknown"
    error = str(record.get("error") or "").strip()
    user_msg = str(record.get("user_msg") or "").strip()
    stage_trace = record.get("stage_trace") or []
    trace_text = " -> ".join(str(stage) for stage in stage_trace) if stage_trace else "none"
    lines = [
        "[LangGraph] Recoverable graph turn found.",
        f"Thread: {thread_id}",
        f"Next node: {next_text}",
        f"Trace so far: {trace_text}",
    ]
    if user_msg:
        lines.append(f"Original request: {user_msg}")
    if error:
        lines.append(f"Last error: {error}")
    lines.append("Use /graph resume to continue from the checkpoint, or /graph clear to discard it.")
    return "\n".join(lines)


def load_langgraph_interrupt_record(*, path: Path | str | None = None) -> dict[str, Any]:
    record_path = _interrupt_path(path)
    if not record_path.exists():
        return {}
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_langgraph_interrupt_record(record: dict[str, Any], *, path: Path | str | None = None) -> None:
    record_path = _interrupt_path(path)
    ensure_parent(record_path)
    payload = _serialize_state_value(dict(record or {}))
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{record_path.name}.",
        suffix=".tmp",
        dir=str(record_path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, record_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise


def clear_langgraph_interrupt_record(
    *,
    path: Path | str | None = None,
    thread_id: str = "",
) -> bool:
    record_path = _interrupt_path(path)
    if not record_path.exists():
        return False
    if thread_id:
        current = load_langgraph_interrupt_record(path=record_path)
        current_thread_id = str(current.get("thread_id") or "").strip()
        if current_thread_id and current_thread_id != str(thread_id or "").strip():
            return False
    try:
        record_path.unlink()
        return True
    except OSError:
        return False


def describe_langgraph_interrupt_record(*, path: Path | str | None = None) -> str:
    record = load_langgraph_interrupt_record(path=path)
    if not record:
        return "[LangGraph] No pending graph interrupt is recorded."
    thread_id = str(record.get("thread_id") or "").strip() or "?"
    payload = dict(record.get("interrupt_payload") or {})
    question = str(payload.get("question") or "").strip() or "Awaiting user input."
    kind = str(payload.get("kind") or "").strip() or "interrupt"
    return "\n".join(
        [
            "[LangGraph] Pending graph interrupt found.",
            f"Thread: {thread_id}",
            f"Kind: {kind}",
            f"Question: {question}",
        ]
    )


def _open_checkpoint_handle(
    *,
    with_checkpointer: bool,
    checkpoint_mode: str | None = None,
    checkpoint_path: Path | str | None = None,
    checkpoint_history_limit: int | None = None,
) -> _CheckpointHandle:
    if not with_checkpointer:
        return _CheckpointHandle(checkpointer=None, mode="none")

    mode = _normalize_checkpoint_mode(
        checkpoint_mode if checkpoint_mode is not None else getattr(CFG, "LANGGRAPH_CHECKPOINT_MODE", "sqlite")
    )
    history_limit = max(1, int(checkpoint_history_limit or _configured_checkpoint_history_limit()))
    if mode == "none":
        return _CheckpointHandle(checkpointer=None, mode="none", history_limit=history_limit)
    if mode == "memory":
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as exc:
            raise RuntimeError(
                "LangGraph memory checkpointer is unavailable. Install `langgraph` to enable it."
            ) from exc
        return _CheckpointHandle(checkpointer=InMemorySaver(), mode="memory", history_limit=history_limit)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph SQLite checkpointer is unavailable. Install `langgraph-checkpoint-sqlite` to enable it."
        ) from exc

    path = Path(checkpoint_path) if checkpoint_path is not None else _configured_checkpoint_path()
    connection: sqlite3.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), check_same_thread=False)
        checkpointer = SqliteSaver(connection)
        checkpointer.setup()
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise RuntimeError(f"Could not open LangGraph SQLite checkpoint store at {path}: {exc}") from exc
    return _CheckpointHandle(
        checkpointer=checkpointer,
        mode="sqlite",
        path=path,
        history_limit=history_limit,
        connection=connection,
    )


def _prune_sqlite_checkpoint_store(connection: sqlite3.Connection, *, max_checkpoints: int) -> int:
    max_checkpoints = max(1, int(max_checkpoints or 1))
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        ).fetchone()
    except sqlite3.Error:
        return 0
    if not table_exists:
        return 0

    stale_rows = list(
        connection.execute(
            """
            SELECT thread_id, checkpoint_ns, checkpoint_id
            FROM checkpoints
            ORDER BY rowid DESC
            LIMIT -1 OFFSET ?
            """,
            (max_checkpoints,),
        )
    )
    if not stale_rows:
        return 0

    writes_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='writes'"
    ).fetchone()
    if writes_exists:
        connection.executemany(
            """
            DELETE FROM writes
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            """,
            stale_rows,
        )
    connection.executemany(
        """
        DELETE FROM checkpoints
        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
        """,
        stale_rows,
    )
    connection.commit()
    return len(stale_rows)


def _serialize_state_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize_state_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_state_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_state_value(item) for item in value]
    return value


def _deserialize_verification(payload: Any) -> VerificationResult | None:
    if isinstance(payload, VerificationResult):
        return payload
    if not isinstance(payload, dict):
        return None
    try:
        return VerificationResult(**payload)
    except TypeError:
        return None


def _deserialize_stage_outcome(payload: Any) -> StageOutcomePack | None:
    if isinstance(payload, StageOutcomePack):
        return payload
    if not isinstance(payload, dict):
        return None
    try:
        return StageOutcomePack(**payload)
    except TypeError:
        return None


def snapshot_orchestrator_state(
    orc: Orchestrator,
    *,
    stage_trace: list[str] | None = None,
    stage_timings: list[dict[str, Any]] | None = None,
) -> OrchestratorGraphState:
    trace = list(stage_trace or [])
    return {
        "next_stage": str(getattr(orc, "next_stage", "") or "FINISHED").strip().upper() or "FINISHED",
        "stage_trace": trace,
        "stage_timings": [_serialize_state_value(item) for item in (stage_timings or [])],
        "user_msg": str(getattr(orc, "user_msg", "") or ""),
        "route_decision": _serialize_state_value(dict(getattr(orc, "route_decision", {}) or {})),
        "context_card": _serialize_state_value(dict(getattr(orc, "context_card", {}) or {})),
        "scratchpad": [str(item) for item in (getattr(orc, "scratchpad", []) or [])],
        "ingested_document_chat": bool(getattr(orc, "ingested_document_chat", False)),
        "document_focus_text": str(getattr(orc, "document_focus_text", "") or ""),
        "document_focus_refs": [str(item) for item in (getattr(orc, "document_focus_refs", []) or [])],
        "document_focus_sources": [str(item) for item in (getattr(orc, "document_focus_sources", []) or [])],
        "turn_screen_image_path": str(getattr(orc, "turn_screen_image_path", "") or ""),
        "turn_screen_image_kind": str(getattr(orc, "turn_screen_image_kind", "") or ""),
        "latest_codex_escalation": _serialize_state_value(getattr(orc, "latest_codex_escalation", None)),
        "failed_task_router_retries": int(getattr(orc, "failed_task_router_retries", 0) or 0),
        "last_stage_outcome": _serialize_state_value(getattr(orc, "last_stage_outcome", None)),
        "last_verification": _serialize_state_value(getattr(orc, "last_verification", None)),
        "route_interceptor": str(getattr(orc, "route_interceptor", "") or ""),
        "reporter_just_ran": bool(getattr(orc, "reporter_just_ran", False)),
        "latest_search_summary": str(getattr(orc, "latest_search_summary", "") or ""),
        "latest_search_failed": bool(getattr(orc, "latest_search_failed", False)),
        "latest_search_error": str(getattr(orc, "latest_search_error", "") or ""),
        "synthetic_user_turn": bool(getattr(orc, "synthetic_user_turn", False)),
        "is_search_result": bool(getattr(orc, "is_search_result", False)),
        "pending_file_target_confirmation": _serialize_state_value(
            getattr(orc, "pending_file_target_confirmation", None)
        ),
        "pending_stage_pause": _serialize_state_value(
            getattr(orc, "pending_stage_pause", None)
        ),
    }


def restore_orchestrator_state(orc: Orchestrator, state: OrchestratorGraphState) -> None:
    orc.next_stage = str(state.get("next_stage", getattr(orc, "next_stage", "ROUTE")) or "ROUTE").strip().upper()
    orc.user_msg = str(state.get("user_msg", getattr(orc, "user_msg", "")) or "")
    orc.route_decision = dict(state.get("route_decision") or {})
    orc.context_card = dict(state.get("context_card") or {})
    orc.scratchpad = [str(item) for item in (state.get("scratchpad") or [])]
    orc.ingested_document_chat = bool(state.get("ingested_document_chat", getattr(orc, "ingested_document_chat", False)))
    orc.document_focus_text = str(state.get("document_focus_text", getattr(orc, "document_focus_text", "")) or "")
    orc.document_focus_refs = [str(item) for item in (state.get("document_focus_refs") or [])]
    orc.document_focus_sources = [str(item) for item in (state.get("document_focus_sources") or [])]
    turn_screen_image_path = str(state.get("turn_screen_image_path", "") or "")
    orc.turn_screen_image_path = turn_screen_image_path or None
    orc.turn_screen_image_kind = str(state.get("turn_screen_image_kind", getattr(orc, "turn_screen_image_kind", "")) or "")
    orc.latest_codex_escalation = dict(state.get("latest_codex_escalation") or {}) or None
    orc.failed_task_router_retries = int(
        state.get("failed_task_router_retries", getattr(orc, "failed_task_router_retries", 0)) or 0
    )
    orc.last_stage_outcome = _deserialize_stage_outcome(state.get("last_stage_outcome"))
    orc.last_verification = _deserialize_verification(state.get("last_verification"))
    orc.route_interceptor = str(state.get("route_interceptor", getattr(orc, "route_interceptor", "")) or "")
    orc.reporter_just_ran = bool(state.get("reporter_just_ran", getattr(orc, "reporter_just_ran", False)))
    orc.latest_search_summary = str(state.get("latest_search_summary", getattr(orc, "latest_search_summary", "")) or "")
    orc.latest_search_failed = bool(state.get("latest_search_failed", getattr(orc, "latest_search_failed", False)))
    orc.latest_search_error = str(state.get("latest_search_error", getattr(orc, "latest_search_error", "")) or "")
    orc.synthetic_user_turn = bool(state.get("synthetic_user_turn", getattr(orc, "synthetic_user_turn", False)))
    orc.is_search_result = bool(state.get("is_search_result", getattr(orc, "is_search_result", False)))
    pending_confirmation = state.get("pending_file_target_confirmation")
    orc.pending_file_target_confirmation = dict(pending_confirmation or {}) or None
    pending_stage_pause = state.get("pending_stage_pause")
    orc.pending_stage_pause = dict(pending_stage_pause or {}) or None


def _state_requests_interrupt(state: OrchestratorGraphState) -> bool:
    if bool(state.get("langgraph_interrupt_consumed")):
        return False
    interrupt_before = str(state.get("interrupt_before_stage", "") or "").strip().upper()
    next_stage = str(state.get("next_stage", "FINISHED") or "FINISHED").strip().upper()
    if not interrupt_before or interrupt_before != next_stage:
        return False
    return bool(state.get("interrupt_payload"))


def _route_next_stage(state: OrchestratorGraphState) -> str:
    if _state_requests_interrupt(state):
        return "AWAIT_INTERRUPT"
    next_stage = str(state.get("next_stage", "FINISHED") or "FINISHED").strip().upper()
    return next_stage if next_stage in _STAGE_NODE_BY_NAME else "FINISHED"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _relative_debug_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(CFG.ROOT_DIR.resolve()))
    except Exception:
        return str(path)


def _checkpoint_summary(*, mode: str, path: Path | None = None) -> str:
    mode = _normalize_checkpoint_mode(mode)
    if mode == "sqlite":
        location = _relative_debug_path(path or _configured_checkpoint_path())
        return f"sqlite ({location})"
    if mode == "memory":
        return "memory (process-local)"
    return "disabled"


def _log_graph_runtime_banner(
    orc: Orchestrator,
    *,
    fallback_reason: str = "",
    checkpoint_mode: str = "sqlite",
    checkpoint_path: Path | None = None,
) -> None:
    if fallback_reason:
        summary = f"LangGraph requested but unavailable; using legacy runtime. {fallback_reason}".strip()
        orc.ui.put(("status_widget_dashboard_activity", summary))
        orc.ui.put(("agent_log", f"[LANGGRAPH] Fallback to legacy runtime: {fallback_reason}"))
        return
    trace_location = _relative_debug_path(CFG.LANGGRAPH_TRACE_PATH)
    checkpoint_text = _checkpoint_summary(mode=checkpoint_mode, path=checkpoint_path)
    orc.ui.put(
        (
            "status_widget_dashboard_activity",
            f"LangGraph runtime active. Checkpoints: {checkpoint_text}. Trace: {trace_location}",
        )
    )
    orc.ui.put(
        (
            "agent_log",
            f"[LANGGRAPH] runtime active. Checkpoints: {checkpoint_text}. Trace: {trace_location}",
        )
    )


def _write_graph_trace(
    *,
    orc: Orchestrator,
    state: OrchestratorGraphState | None,
    runtime_mode: str,
    status: str,
    error: str = "",
    checkpoint_mode: str = "",
    checkpoint_path: Path | None = None,
    checkpoint_history_limit: int = 0,
) -> None:
    if not getattr(CFG, "DEBUG_LANGGRAPH_TRACE", True):
        return
    trace_path = CFG.LANGGRAPH_TRACE_PATH
    state = dict(state or {})
    route_decision = dict(state.get("route_decision") or getattr(orc, "route_decision", {}) or {})
    stage_trace = [str(item) for item in (state.get("stage_trace") or [])]
    stage_timings = [_serialize_state_value(item) for item in (state.get("stage_timings") or [])]
    payload = {
        "timestamp": _now_utc_iso(),
        "runtime_mode": runtime_mode,
        "status": status,
        "error": str(error or "").strip(),
        "turn_id": str(getattr(getattr(orc, "turn_stats", None), "turn_id", "") or ""),
        "user_msg": str(state.get("user_msg", getattr(orc, "user_msg", "")) or ""),
        "route_decision": str(route_decision.get("decision", "") or "").strip().upper(),
        "next_stage": str(state.get("next_stage", getattr(orc, "next_stage", "")) or "").strip().upper(),
        "stage_trace": stage_trace,
        "stage_timings": stage_timings,
        "total_graph_ms": round(sum(float(item.get("elapsed_ms", 0.0) or 0.0) for item in stage_timings), 3),
        "scratchpad_entries": len(getattr(orc, "scratchpad", []) or []),
        "checkpoint_mode": str(checkpoint_mode or "").strip().lower(),
        "checkpoint_path": _relative_debug_path(checkpoint_path) if checkpoint_path else "",
        "checkpoint_history_limit": int(checkpoint_history_limit or 0),
    }
    append_jsonl(
        trace_path,
        payload,
        max_lines=int(getattr(CFG, "LANGGRAPH_TRACE_HISTORY_LIMIT", 500) or 500),
    )


def _checkpoint_config(thread_id: str, checkpoint_id: str = "") -> dict[str, Any]:
    configurable = {"thread_id": str(thread_id or "").strip()}
    if checkpoint_id:
        configurable["checkpoint_id"] = str(checkpoint_id or "").strip()
    return {"configurable": configurable}


def _extract_interrupt_values(result: Any) -> list[Any]:
    if not isinstance(result, dict):
        return []
    return [getattr(item, "value", item) for item in (result.get("__interrupt__") or [])]


def _result_has_interrupt(result: Any) -> bool:
    return bool(_extract_interrupt_values(result))


def _interrupt_question(payload: dict[str, Any]) -> str:
    question = str(payload.get("question") or "").strip()
    if question:
        return question
    kind = str(payload.get("kind") or "").strip().lower()
    if kind:
        return f"LangGraph is waiting for input for `{kind}`."
    return "LangGraph is waiting for input."


def _emit_interrupt_prompt(orc: Orchestrator, payload: dict[str, Any]) -> None:
    prompt = _interrupt_question(payload)
    if not prompt:
        return
    stream_payload: dict[str, Any] = {}
    style_state = getattr(orc, "ss", None)
    if style_state is not None:
        stream_payload = {
            "tts_voice": getattr(style_state, "tts_voice", None),
            "tts_speed": getattr(style_state, "tts_speed", None),
        }
    orc.ui.put(("assistant_stream_start", stream_payload))
    orc.ui.put(("assistant_stream_delta", {"text": prompt}))
    orc.ui.put(("assistant_stream_end", ""))


def _upsert_runtime_context_for_interrupt(orc: Orchestrator) -> None:
    try:
        from core.runtime_context import LATEST_RUNTIME_CONTEXT_PREFIX

        payload = orc.prompt_context.build_runtime_context_message(orc, reporter_just_ran=False)
        if not payload:
            return
        try:
            orc.chat.upsert_hidden_system_message(LATEST_RUNTIME_CONTEXT_PREFIX, payload)
        except AttributeError:
            orc.chat.append_message({"role": "system", "content": payload, "hidden": True})
    except Exception as exc:
        _LOG.debug("Could not upsert runtime context for LangGraph interrupt: %s", exc)


def _snapshot_checkpoint_details(runtime: OrchestratorGraphRuntime, *, thread_id: str) -> dict[str, Any]:
    try:
        snapshot = runtime.graph.get_state(_checkpoint_config(thread_id))
    except Exception:
        return {}
    values = dict(getattr(snapshot, "values", {}) or {})
    config = dict(getattr(snapshot, "config", {}) or {})
    configurable = dict(config.get("configurable") or {})
    return {
        "checkpoint_id": str(configurable.get("checkpoint_id", "") or ""),
        "checkpoint_next": [str(item) for item in (getattr(snapshot, "next", ()) or ())],
        "stage_trace": [str(item) for item in (values.get("stage_trace") or [])],
        "next_stage": str(values.get("next_stage", "") or "").strip().upper(),
        "values_present": bool(values),
    }


def _record_pending_interrupt(
    *,
    runtime: OrchestratorGraphRuntime,
    orc: Orchestrator,
    thread_id: str,
    result: dict[str, Any],
) -> None:
    interrupt_values = _extract_interrupt_values(result)
    payload = dict(interrupt_values[0] or {}) if interrupt_values and isinstance(interrupt_values[0], dict) else {}
    details = _snapshot_checkpoint_details(runtime, thread_id=thread_id)
    record = {
        "schema": 1,
        "status": "pending",
        "created_at_utc": _now_utc_iso(),
        "thread_id": str(thread_id or "").strip(),
        "checkpoint_id": str(details.get("checkpoint_id") or ""),
        "checkpoint_next": list(details.get("checkpoint_next") or []),
        "checkpoint_path": str(runtime.checkpoint_path or ""),
        "checkpoint_mode": runtime.checkpoint_mode,
        "interrupt_payload": payload,
        "user_msg": str(getattr(orc, "user_msg", "") or ""),
        "stage_trace": list(details.get("stage_trace") or result.get("stage_trace") or []),
    }
    try:
        save_langgraph_interrupt_record(record)
    except Exception as exc:
        _LOG.warning("Could not save LangGraph interrupt record: %s", exc)
        orc.ui.put(("agent_log", f"[LANGGRAPH] interrupt record save failed: {exc}"))
    if payload:
        pending = dict(payload.get("pending_file_target_confirmation") or {})
        if pending:
            try:
                from core.file_target_confirmation import (
                    PENDING_FILE_TARGET_CONFIRMATION_PREFIX,
                    build_pending_file_target_confirmation_message,
                )

                message = build_pending_file_target_confirmation_message(pending)
                try:
                    orc.chat.upsert_hidden_system_message(PENDING_FILE_TARGET_CONFIRMATION_PREFIX, message)
                except AttributeError:
                    orc.chat.append_message({"role": "system", "content": message, "hidden": True})
            except Exception:
                pass
        if str(payload.get("kind") or "").strip().lower() in {"stage_user_input_pause", "stage_approval_pause"}:
            _upsert_runtime_context_for_interrupt(orc)
        _emit_interrupt_prompt(orc, payload)
    orc.ui.put(("agent_log", "[LANGGRAPH] graph interrupted; waiting for user input."))


def _record_recoverable_failure(
    *,
    runtime: OrchestratorGraphRuntime,
    orc: Orchestrator,
    thread_id: str,
    error: str,
) -> None:
    if runtime.checkpoint_mode != "sqlite":
        orc.ui.put(("agent_log", "[LANGGRAPH] recovery not recorded; checkpoint mode is not durable SQLite."))
        return
    details = _snapshot_checkpoint_details(runtime, thread_id=thread_id)
    if not details.get("values_present"):
        orc.ui.put(("agent_log", "[LANGGRAPH] recovery not recorded; no checkpoint state was found."))
        return
    record = {
        "schema": 1,
        "status": "failed",
        "created_at_utc": _now_utc_iso(),
        "thread_id": str(thread_id or "").strip(),
        "checkpoint_id": str(details.get("checkpoint_id") or ""),
        "checkpoint_next": list(details.get("checkpoint_next") or []),
        "checkpoint_path": str(runtime.checkpoint_path or ""),
        "checkpoint_mode": runtime.checkpoint_mode,
        "checkpoint_history_limit": runtime.checkpoint_history_limit,
        "error": str(error or "").strip(),
        "user_msg": str(getattr(orc, "user_msg", "") or ""),
        "next_stage": str(details.get("next_stage") or getattr(orc, "next_stage", "") or "").strip().upper(),
        "stage_trace": list(details.get("stage_trace") or []),
    }
    try:
        save_langgraph_recovery_record(record)
    except Exception as exc:
        _LOG.warning("Could not save LangGraph recovery record: %s", exc)
        orc.ui.put(("agent_log", f"[LANGGRAPH] recovery record save failed: {exc}"))
        return
    orc.ui.put(
        (
            "agent_log",
            "[LANGGRAPH] recoverable checkpoint recorded. Use /graph resume to continue it.",
        )
    )
    orc.ui.put(
        (
            "status_widget_dashboard_activity",
            "LangGraph turn failed; recovery checkpoint saved. Use /graph resume.",
        )
    )


def _make_stage_node(stage_name: str):
    def _node(state: OrchestratorGraphState, runtime) -> OrchestratorGraphState:
        context = getattr(runtime, "context", None)
        orc = getattr(context, "orchestrator", None)
        if orc is None:
            raise RuntimeError("LangGraph orchestrator context is missing.")

        restore_orchestrator_state(orc, state)
        expected_stage = str(state.get("next_stage", "") or "").strip().upper()
        if expected_stage and expected_stage != stage_name:
            raise RuntimeError(f"LangGraph stage mismatch: expected {stage_name}, got {expected_stage}.")

        trace = list(state.get("stage_trace") or [])
        timings = [_serialize_state_value(item) for item in (state.get("stage_timings") or [])]
        started_at = time.perf_counter()
        trace.append(stage_name)
        orc.dispatch_stage(stage_name)
        elapsed_ms = round(max(0.0, (time.perf_counter() - started_at) * 1000.0), 3)
        next_stage = str(getattr(orc, "next_stage", "FINISHED") or "FINISHED").strip().upper()
        timings.append(
            {
                "stage": stage_name,
                "elapsed_ms": elapsed_ms,
                "next_stage": next_stage,
            }
        )
        orc.ui.put(("agent_log", f"[LANGGRAPH] {stage_name} {elapsed_ms:.1f} ms -> {next_stage}"))
        next_state = snapshot_orchestrator_state(orc, stage_trace=trace, stage_timings=timings)
        pending_confirmation = dict(getattr(orc, "pending_file_target_confirmation", {}) or {})
        pending_stage_pause = dict(getattr(orc, "pending_stage_pause", {}) or {})
        if stage_name == "MANAGER" and pending_confirmation and next_stage == "PERSONA":
            next_state["interrupt_before_stage"] = "PERSONA"
            next_state["interrupt_payload"] = {
                "kind": "missing_file_target_confirmation",
                "question": str(pending_confirmation.get("question") or "").strip(),
                "next_stage": "PERSONA",
                "pending_file_target_confirmation": _serialize_state_value(pending_confirmation),
            }
            next_state["langgraph_interrupt_consumed"] = False
        elif (
            stage_name == "MANAGER"
            and pending_stage_pause
            and str(pending_stage_pause.get("pause_type") or "").strip().lower() == "user_input"
            and next_stage == "PERSONA"
        ):
            next_state["interrupt_before_stage"] = "PERSONA"
            next_state["interrupt_payload"] = {
                "kind": "stage_user_input_pause",
                "question": str(pending_stage_pause.get("question") or "").strip(),
                "next_stage": "PERSONA",
                "pending_stage_pause": _serialize_state_value(pending_stage_pause),
            }
            next_state["langgraph_interrupt_consumed"] = False
        elif (
            stage_name == "MANAGER"
            and pending_stage_pause
            and str(pending_stage_pause.get("pause_type") or "").strip().lower() == "approval"
            and next_stage == "PERSONA"
        ):
            next_state["interrupt_before_stage"] = "PERSONA"
            next_state["interrupt_payload"] = {
                "kind": "stage_approval_pause",
                "question": str(pending_stage_pause.get("question") or "").strip(),
                "next_stage": "PERSONA",
                "pending_stage_pause": _serialize_state_value(pending_stage_pause),
            }
            next_state["langgraph_interrupt_consumed"] = False
        return next_state

    return _node


def _resume_text(resume_value: Any) -> str:
    if isinstance(resume_value, dict):
        for key in ("user_msg", "text", "answer", "response", "resume"):
            value = resume_value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(resume_value or "").strip()


def _resume_missing_file_target_confirmation(
    state: OrchestratorGraphState,
    resume_value: Any,
) -> OrchestratorGraphState:
    from core.file_target_confirmation import (
        build_confirmed_route_decision,
        classify_pending_file_target_confirmation_reply,
    )

    updated = dict(state)
    payload = dict(updated.get("interrupt_payload") or {})
    pending = dict(updated.get("pending_file_target_confirmation") or payload.get("pending_file_target_confirmation") or {})
    resolution = classify_pending_file_target_confirmation_reply(_resume_text(resume_value), pending)
    if resolution is None:
        question = str(pending.get("question") or payload.get("question") or "Please confirm or name the intended target.").strip()
        updated["interrupt_payload"] = {
            **payload,
            "kind": "missing_file_target_confirmation",
            "question": f"{question} Please answer yes/no or name one of the listed files.",
            "pending_file_target_confirmation": _serialize_state_value(pending),
        }
        updated["interrupt_before_stage"] = str(updated.get("next_stage", "PERSONA") or "PERSONA").strip().upper()
        updated["langgraph_interrupt_consumed"] = False
        return updated

    exact_target = str(pending.get("exact_target") or "").strip()
    candidates = [str(item).strip() for item in (pending.get("candidates") or []) if str(item).strip()]
    chosen_target = str(resolution.get("chosen_target") or "").strip()
    decision = str(resolution.get("decision") or "").strip().lower()

    if decision in {"confirm", "choose"} and exact_target and chosen_target:
        confirmed_route = build_confirmed_route_decision(
            dict(pending.get("route_decision") or {}),
            exact_target=exact_target,
            chosen_target=chosen_target,
        )
        updated["route_decision"] = _serialize_state_value(confirmed_route)
        updated["context_card"] = _serialize_state_value(dict(confirmed_route.get("card") or {}))
        updated["next_stage"] = "MANAGER"
        updated["pending_file_target_confirmation"] = None
        updated["interrupt_payload"] = {}
        updated["interrupt_before_stage"] = ""
        updated["langgraph_interrupt_consumed"] = True
        scratchpad = [str(item) for item in (updated.get("scratchpad") or [])]
        scratchpad.append(f"LANGGRAPH_INTERRUPT_RESUME: file target confirmed as {chosen_target}")
        updated["scratchpad"] = scratchpad
        return updated

    if decision == "decline":
        reply = "Understood. I will leave the workspace unchanged."
        if exact_target and candidates:
            reply = f"Understood. I will not substitute `{candidates[0]}` for `{exact_target}`."
        updated["route_decision"] = {
            "decision": "CHAT",
            "interceptor": "FILE_TARGET_CONFIRMATION_CANCELLED",
            "system_notice": {
                "kind": "file_target_confirmation_cancelled",
                "reply": reply,
                "exact_target": exact_target,
                "candidates": candidates[:3],
            },
        }
        updated["context_card"] = {}
        updated["next_stage"] = "PERSONA"
        updated["pending_file_target_confirmation"] = None
        updated["interrupt_payload"] = {}
        updated["interrupt_before_stage"] = ""
        updated["langgraph_interrupt_consumed"] = True
        return updated

    return updated


def _resume_stage_user_input_pause(
    state: OrchestratorGraphState,
    resume_value: Any,
) -> OrchestratorGraphState:
    updated = dict(state)
    payload = dict(updated.get("interrupt_payload") or {})
    pending = dict(updated.get("pending_stage_pause") or payload.get("pending_stage_pause") or {})
    reply_text = _resume_text(resume_value)
    if not reply_text:
        question = str(pending.get("question") or payload.get("question") or "Please provide the requested details.").strip()
        updated["interrupt_payload"] = {
            **payload,
            "kind": "stage_user_input_pause",
            "question": question,
            "pending_stage_pause": _serialize_state_value(pending),
        }
        updated["interrupt_before_stage"] = str(updated.get("next_stage", "PERSONA") or "PERSONA").strip().upper()
        updated["langgraph_interrupt_consumed"] = False
        return updated

    updated["user_msg"] = reply_text
    updated["route_decision"] = {}
    updated["context_card"] = {}
    updated["next_stage"] = "ROUTE"
    updated["pending_stage_pause"] = None
    updated["interrupt_payload"] = {}
    updated["interrupt_before_stage"] = ""
    updated["langgraph_interrupt_consumed"] = True
    updated["reporter_just_ran"] = False
    updated["synthetic_user_turn"] = False
    updated["is_search_result"] = False
    scratchpad = [str(item) for item in (updated.get("scratchpad") or [])]
    pause_label = str(pending.get("pause_type") or "user_input").strip() or "user_input"
    scratchpad.append(f"LANGGRAPH_INTERRUPT_RESUME: {pause_label} reply received; routing resumed turn.")
    updated["scratchpad"] = scratchpad
    return updated


def _resume_stage_approval_pause(
    state: OrchestratorGraphState,
    resume_value: Any,
) -> OrchestratorGraphState:
    from core.stage_approval import classify_stage_approval_reply

    updated = dict(state)
    payload = dict(updated.get("interrupt_payload") or {})
    pending = dict(updated.get("pending_stage_pause") or payload.get("pending_stage_pause") or {})
    decision = classify_stage_approval_reply(_resume_text(resume_value))
    if decision is None:
        question = str(
            pending.get("question")
            or payload.get("question")
            or "Please confirm whether I should continue."
        ).strip()
        updated["interrupt_payload"] = {
            **payload,
            "kind": "stage_approval_pause",
            "question": f"{question} Please answer yes/no before I continue.",
            "pending_stage_pause": _serialize_state_value(pending),
        }
        updated["interrupt_before_stage"] = str(updated.get("next_stage", "PERSONA") or "PERSONA").strip().upper()
        updated["langgraph_interrupt_consumed"] = False
        return updated

    stage_goal = str(pending.get("stage_goal") or "").strip()
    if decision == "decline":
        updated["route_decision"] = {
            "decision": "CHAT",
            "interceptor": "STAGE_APPROVAL_CANCELLED",
            "system_notice": {
                "kind": "stage_approval_cancelled",
                "stage_goal": stage_goal,
                "reply": "Understood. I will stop here and leave things unchanged.",
            },
        }
        updated["context_card"] = {}
        updated["next_stage"] = "PERSONA"
        updated["pending_stage_pause"] = None
        updated["interrupt_payload"] = {}
        updated["interrupt_before_stage"] = ""
        updated["langgraph_interrupt_consumed"] = True
        scratchpad = [str(item) for item in (updated.get("scratchpad") or [])]
        scratchpad.append("LANGGRAPH_INTERRUPT_RESUME: approval declined; task stopped before execution.")
        updated["scratchpad"] = scratchpad
        return updated

    approved_route = dict(pending.get("approved_route_decision") or pending.get("route_decision") or {})
    approved_card = dict(approved_route.get("card") or {})
    approved_stages = [dict(item) for item in (approved_card.get("stages") or []) if isinstance(item, dict)]
    if not approved_stages:
        updated["route_decision"] = {
            "decision": "CHAT",
            "interceptor": "STAGE_APPROVAL_NO_REMAINING_WORK",
            "system_notice": {
                "kind": "stage_approval_no_remaining_work",
                "stage_goal": stage_goal,
            },
        }
        updated["context_card"] = {}
        updated["next_stage"] = "PERSONA"
        updated["pending_stage_pause"] = None
        updated["interrupt_payload"] = {}
        updated["interrupt_before_stage"] = ""
        updated["langgraph_interrupt_consumed"] = True
        scratchpad = [str(item) for item in (updated.get("scratchpad") or [])]
        scratchpad.append("LANGGRAPH_INTERRUPT_RESUME: approval received but no remaining execution stage was recorded.")
        updated["scratchpad"] = scratchpad
        return updated

    approved_card["stages"] = approved_stages
    approved_route["card"] = approved_card
    updated["route_decision"] = _serialize_state_value(approved_route)
    updated["context_card"] = _serialize_state_value(approved_card)
    updated["next_stage"] = "MANAGER"
    updated["pending_stage_pause"] = None
    updated["interrupt_payload"] = {}
    updated["interrupt_before_stage"] = ""
    updated["langgraph_interrupt_consumed"] = True
    updated["reporter_just_ran"] = False
    updated["synthetic_user_turn"] = False
    updated["is_search_result"] = False
    scratchpad = [str(item) for item in (updated.get("scratchpad") or [])]
    scratchpad.append("LANGGRAPH_INTERRUPT_RESUME: approval received; continuing approved task.")
    updated["scratchpad"] = scratchpad
    return updated


def _make_interrupt_node():
    def _node(state: OrchestratorGraphState) -> OrchestratorGraphState:
        try:
            from langgraph.types import interrupt
        except ImportError as exc:
            raise RuntimeError("LangGraph interrupt support is unavailable.") from exc

        payload = _serialize_state_value(
            dict(state.get("interrupt_payload") or {})
            or {
                "kind": "manual_interrupt",
                "next_stage": str(state.get("next_stage", "") or "").strip().upper(),
            }
        )
        resume_value = interrupt(payload)
        updated = dict(state)
        updated["interrupt_resume_value"] = _serialize_state_value(resume_value)
        if str(payload.get("kind") or "").strip().lower() == "missing_file_target_confirmation":
            return _resume_missing_file_target_confirmation(updated, resume_value)
        if str(payload.get("kind") or "").strip().lower() == "stage_user_input_pause":
            return _resume_stage_user_input_pause(updated, resume_value)
        if str(payload.get("kind") or "").strip().lower() == "stage_approval_pause":
            return _resume_stage_approval_pause(updated, resume_value)
        updated["langgraph_interrupt_consumed"] = True
        return updated

    return _node


def _compile_orchestrator_graph(*, checkpointer: Any | None):
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph dependencies are not installed. Install `langgraph` to enable the graph runtime."
        ) from exc

    builder = StateGraph(OrchestratorGraphState, context_schema=OrchestratorGraphContext)
    for stage_name, node_name in _STAGE_NODE_BY_NAME.items():
        builder.add_node(node_name, _make_stage_node(stage_name))
    builder.add_node("await_interrupt", _make_interrupt_node())

    edge_map = {stage_name: node_name for stage_name, node_name in _STAGE_NODE_BY_NAME.items()}
    edge_map["AWAIT_INTERRUPT"] = "await_interrupt"
    edge_map["FINISHED"] = END

    builder.add_conditional_edges(START, _route_next_stage, edge_map)
    for node_name in _STAGE_NODE_BY_NAME.values():
        builder.add_conditional_edges(node_name, _route_next_stage, edge_map)
    builder.add_conditional_edges("await_interrupt", _route_next_stage, edge_map)

    return builder.compile(checkpointer=checkpointer)


def build_orchestrator_graph_runtime(
    *,
    with_checkpointer: bool = True,
    checkpoint_mode: str | None = None,
    checkpoint_path: Path | str | None = None,
    checkpoint_history_limit: int | None = None,
) -> OrchestratorGraphRuntime:
    handle = _open_checkpoint_handle(
        with_checkpointer=with_checkpointer,
        checkpoint_mode=checkpoint_mode,
        checkpoint_path=checkpoint_path,
        checkpoint_history_limit=checkpoint_history_limit,
    )
    try:
        graph = _compile_orchestrator_graph(checkpointer=handle.checkpointer)
    except Exception:
        handle.close()
        raise
    return OrchestratorGraphRuntime(
        graph=graph,
        checkpoint_mode=handle.mode,
        checkpoint_path=handle.path,
        checkpoint_history_limit=handle.history_limit,
        _checkpoint_handle=handle,
    )


def build_orchestrator_graph(
    *,
    with_checkpointer: bool = True,
    checkpoint_mode: str | None = None,
    checkpoint_path: Path | str | None = None,
    checkpoint_history_limit: int | None = None,
):
    runtime = build_orchestrator_graph_runtime(
        with_checkpointer=with_checkpointer,
        checkpoint_mode=checkpoint_mode,
        checkpoint_path=checkpoint_path,
        checkpoint_history_limit=checkpoint_history_limit,
    )
    return runtime.graph


def _prune_runtime_checkpoints(runtime: OrchestratorGraphRuntime, orc: Orchestrator) -> None:
    try:
        pruned = runtime.prune_checkpoints()
    except Exception as exc:
        _LOG.warning("LangGraph checkpoint pruning failed: %s", exc)
        orc.ui.put(("agent_log", f"[LANGGRAPH] checkpoint prune warning: {exc}"))
        return
    if pruned:
        orc.ui.put(("agent_log", f"[LANGGRAPH] pruned {pruned} old checkpoint(s)."))


def run_agent_loop_with_langgraph(orc_cfg: OrchestratorConfig) -> None:
    runtime: OrchestratorGraphRuntime | None = None
    resume_thread_id = str(getattr(orc_cfg, "langgraph_resume_thread_id", "") or "").strip()
    resume_checkpoint_id = str(getattr(orc_cfg, "langgraph_resume_checkpoint_id", "") or "").strip()
    resume_value = getattr(orc_cfg, "langgraph_resume_value", None)
    try:
        runtime = build_orchestrator_graph_runtime(with_checkpointer=True)
    except RuntimeError as exc:
        if resume_thread_id:
            raise
        _LOG.warning("LangGraph runtime unavailable; falling back to legacy loop: %s", exc)
        orc = Orchestrator(orc_cfg)
        _log_graph_runtime_banner(orc, fallback_reason=str(exc))
        _write_graph_trace(
            orc=orc,
            state=None,
            runtime_mode="legacy_fallback",
            status="fallback",
            error=str(exc),
        )
        orc.run()
        return

    try:
        orc = Orchestrator(orc_cfg)
        orc.prepare_turn()
        if resume_thread_id and getattr(orc, "turn_stats", None) is not None:
            orc.turn_stats.turn_id = resume_thread_id
    except Exception:
        runtime.close()
        raise
    _log_graph_runtime_banner(
        orc,
        checkpoint_mode=runtime.checkpoint_mode,
        checkpoint_path=runtime.checkpoint_path,
    )
    initial_state: Any = None if resume_thread_id else snapshot_orchestrator_state(orc, stage_trace=[])
    if resume_thread_id and resume_value is not None:
        try:
            from langgraph.types import Command
        except ImportError as exc:
            runtime.close()
            raise RuntimeError("LangGraph resume command support is unavailable.") from exc
        initial_state = Command(resume=resume_value)
    thread_id = resume_thread_id or str(getattr(getattr(orc, "turn_stats", None), "turn_id", "") or "default")
    if resume_thread_id:
        checkpoint_details = _snapshot_checkpoint_details(runtime, thread_id=thread_id)
        if not checkpoint_details.get("values_present"):
            runtime.close()
            raise RuntimeError(f"No LangGraph checkpoint found for thread '{thread_id}'.")
        if not checkpoint_details.get("checkpoint_next"):
            runtime.close()
            raise RuntimeError(f"LangGraph thread '{thread_id}' has no pending node to resume.")
        orc.ui.put(("agent_log", f"[LANGGRAPH] resuming checkpoint thread {thread_id}."))
        orc.ui.put(("status_widget_dashboard_activity", f"Resuming LangGraph checkpoint {thread_id}..."))
    context = OrchestratorGraphContext(orchestrator=orc)

    try:
        result = runtime.graph.invoke(
            initial_state,
            config=_checkpoint_config(thread_id, resume_checkpoint_id),
            context=context,
        )
        if _result_has_interrupt(result):
            _record_pending_interrupt(
                runtime=runtime,
                orc=orc,
                thread_id=thread_id,
                result=dict(result),
            )
            _write_graph_trace(
                orc=orc,
                state=result,
                runtime_mode="langgraph_resume" if resume_thread_id else "langgraph",
                status="interrupted",
                checkpoint_mode=runtime.checkpoint_mode,
                checkpoint_path=runtime.checkpoint_path,
                checkpoint_history_limit=runtime.checkpoint_history_limit,
            )
            return
        _write_graph_trace(
            orc=orc,
            state=result,
            runtime_mode="langgraph_resume" if resume_thread_id else "langgraph",
            status="completed",
            checkpoint_mode=runtime.checkpoint_mode,
            checkpoint_path=runtime.checkpoint_path,
            checkpoint_history_limit=runtime.checkpoint_history_limit,
        )
        if resume_thread_id:
            clear_langgraph_recovery_record(thread_id=thread_id)
            clear_langgraph_interrupt_record(thread_id=thread_id)
            orc.ui.put(("agent_log", f"[LANGGRAPH] recovery record cleared for {thread_id}."))
        orc._record_turn_stats_if_ready()
    except OperationCancelled:
        _write_graph_trace(
            orc=orc,
            state=snapshot_orchestrator_state(orc),
            runtime_mode="langgraph_resume" if resume_thread_id else "langgraph",
            status="cancelled",
            checkpoint_mode=runtime.checkpoint_mode,
            checkpoint_path=runtime.checkpoint_path,
            checkpoint_history_limit=runtime.checkpoint_history_limit,
        )
        orc.ui.put(("agent_log", "   -> Action canceled by user."))
        orc._log_dashboard("Canceled.")
        raise
    except Exception as exc:
        _write_graph_trace(
            orc=orc,
            state=snapshot_orchestrator_state(orc),
            runtime_mode="langgraph_resume" if resume_thread_id else "langgraph",
            status="failed",
            error=str(exc),
            checkpoint_mode=runtime.checkpoint_mode,
            checkpoint_path=runtime.checkpoint_path,
            checkpoint_history_limit=runtime.checkpoint_history_limit,
        )
        _record_recoverable_failure(
            runtime=runtime,
            orc=orc,
            thread_id=thread_id,
            error=str(exc),
        )
        orc._record_turn_stats_if_ready(aborted=True, detail=str(exc), phase=orc.next_stage)
        raise
    finally:
        if runtime is not None:
            _prune_runtime_checkpoints(runtime, orc)
            runtime.close()
