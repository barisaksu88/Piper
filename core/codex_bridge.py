from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CFG, data_debug_path
from core.contracts import EscalationDecision
from memory.codex_repair_store import CodexRepairStateStore


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _truncate_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    head = max(0, max_chars - 160)
    return text[:head].rstrip() + "\n[TRUNCATED]\n" + text[-120:].lstrip()


def _condense_signal(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(signal.get("kind") or "").strip(),
        "severity": str(signal.get("severity") or "").strip(),
        "source": str(signal.get("source") or "").strip(),
        "summary": _truncate_text(signal.get("summary"), max_chars=220),
        "stage_goal": _truncate_text(signal.get("stage_goal"), max_chars=220),
        "tool": str(signal.get("tool") or "").strip(),
        "step": int(signal.get("step") or 0),
    }


def condense_escalation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    condensed_route = {}
    route_decision = payload.get("route_decision") or {}
    if isinstance(route_decision, dict):
        card = route_decision.get("card") or {}
        condensed_route = {
            "decision": str(route_decision.get("decision") or "").strip(),
            "goal": _truncate_text(card.get("goal"), max_chars=220),
            "stages": [
                {
                    "stage_goal": _truncate_text(stage.get("stage_goal"), max_chars=220),
                    "stage_type": str(stage.get("stage_type") or "").strip(),
                    "success_condition": _truncate_text(stage.get("success_condition"), max_chars=220),
                }
                for stage in list(card.get("stages") or [])[:4]
                if isinstance(stage, dict)
            ],
        }

    return {
        "timestamp_utc": str(payload.get("timestamp_utc") or "").strip(),
        "manual": bool(payload.get("manual")),
        "source": str(payload.get("source") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "summary": _truncate_text(payload.get("summary"), max_chars=260),
        "status_snapshot": str(payload.get("status_snapshot") or "").strip(),
        "user_msg": _truncate_text(payload.get("user_msg"), max_chars=260),
        "route": condensed_route,
        "recent_signals": [
            _condense_signal(item)
            for item in list(payload.get("recent_signals") or [])[-4:]
            if isinstance(item, dict)
        ],
        "trigger_signal": _condense_signal(payload.get("trigger_signal") or {})
        if isinstance(payload.get("trigger_signal") or {}, dict)
        else {},
        "history_tail": [
            {
                "role": str(item.get("role") or "").strip(),
                "content": _truncate_text(item.get("content"), max_chars=280),
            }
            for item in list(payload.get("history_tail") or [])[-6:]
            if isinstance(item, dict)
        ],
        "scratchpad_tail": [
            _truncate_text(entry, max_chars=1200)
            for entry in list(payload.get("scratchpad_tail") or [])[-8:]
        ],
        "monitor_tail": [
            _truncate_text(entry, max_chars=240)
            for entry in list(payload.get("monitor_tail") or [])[-10:]
        ],
        "dashboard_tail": [
            _truncate_text(entry, max_chars=240)
            for entry in list(payload.get("dashboard_tail") or [])[-10:]
        ],
    }


def load_latest_escalation_payload(log_path: Path) -> dict[str, Any]:
    path = Path(log_path)
    if not path.exists():
        return {}
    last_line = ""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                if raw_line.strip():
                    last_line = raw_line
    except Exception:
        return {}
    if not last_line:
        return {}
    try:
        payload = json.loads(last_line)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_codex_executable() -> str:
    configured = str(getattr(CFG, "CODEX_EXECUTABLE", "") or "").strip()
    if configured:
        direct = Path(configured)
        if direct.exists():
            return str(direct)
        resolved = shutil.which(configured)
        if resolved:
            return resolved
    for name in ("codex.exe", "codex.cmd", "codex"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return ""


def _resolve_wsl_launcher() -> str:
    for candidate in ("wsl.exe", r"C:\Windows\System32\wsl.exe"):
        resolved = shutil.which(candidate) or candidate
        if resolved and Path(str(resolved)).exists():
            return str(resolved)
    return ""


def _to_wsl_path_text(raw_path: str | Path) -> str:
    raw = str(raw_path or "").strip()
    if not raw:
        return ""
    if raw.startswith("/mnt/"):
        return raw.replace("\\", "/")
    match = None
    try:
        import re

        match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
    except Exception:
        match = None
    if match:
        drive = match.group(1).lower()
        suffix = match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{suffix}"
    return raw.replace("\\", "/")


def resolve_codex_launch_prefix() -> tuple[list[str], str]:
    prefer_wsl = bool(getattr(CFG, "CODEX_PREFER_WSL", False))
    wsl_executable = str(getattr(CFG, "CODEX_WSL_EXECUTABLE", "") or "").strip()
    wsl_launcher = _resolve_wsl_launcher()
    if os.name == "nt" and prefer_wsl and wsl_executable and wsl_launcher:
        return [wsl_launcher, "-e", wsl_executable], f"WSL Codex ({wsl_executable})"

    codex_executable = resolve_codex_executable()
    if codex_executable:
        return [codex_executable], codex_executable

    if os.name == "nt" and wsl_executable and wsl_launcher:
        return [wsl_launcher, "-e", wsl_executable], f"WSL Codex ({wsl_executable})"
    return [], ""


def build_codex_exec_command(
    *,
    cd_path: str | Path,
    output_schema_path: str | Path,
    output_path: str | Path,
    sandbox: str | None = None,
    ephemeral: bool = False,
    dangerous: bool = False,
) -> tuple[list[str], str]:
    prefix, label = resolve_codex_launch_prefix()
    if not prefix:
        return [], ""

    use_wsl = len(prefix) >= 2 and prefix[0].lower().endswith("wsl.exe")
    cd_arg = _to_wsl_path_text(cd_path) if use_wsl else str(cd_path)
    schema_arg = _to_wsl_path_text(output_schema_path) if use_wsl else str(output_schema_path)
    output_arg = _to_wsl_path_text(output_path) if use_wsl else str(output_path)

    command = list(prefix)
    command.extend(
        [
            "exec",
            "--cd",
            cd_arg,
            "--skip-git-repo-check",
        ]
    )
    if dangerous:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    elif sandbox:
        command.extend(["--sandbox", str(sandbox)])
    if ephemeral:
        command.append("--ephemeral")
    command.extend(
        [
            "--color",
            "never",
            "--output-schema",
            schema_arg,
            "-o",
            output_arg,
            "-",
        ]
    )
    return command, label


def probe_codex_support(*, timeout_s: float | None = None) -> tuple[bool, str]:
    simulated = str(os.environ.get("PIPER_CODEX_BOOT_PROBE_SIMULATE") or "").strip().lower()
    if simulated == "ok":
        return True, "Engineering channel: ONLINE"
    if simulated:
        return False, f"Engineering channel: OFFLINE ({simulated})"

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["status"],
        "properties": {
            "status": {"type": "string", "enum": ["ok"]},
        },
    }
    prompt = (
        "This is Piper boot health check.\n"
        "Do not inspect files. Do not run commands.\n"
        "Return JSON with status set to ok.\n"
    )
    with tempfile.TemporaryDirectory(prefix="piper-codex-probe-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        schema_path = tmp_path / "schema.json"
        output_path = tmp_path / "result.json"
        schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            command, label = build_codex_exec_command(
                cd_path=CFG.ROOT_DIR,
                output_schema_path=schema_path,
                output_path=output_path,
                sandbox="read-only",
                ephemeral=True,
                dangerous=False,
            )
            if not command:
                return False, "Engineering channel: OFFLINE (Codex executable not found)"
            completed = subprocess.run(
                command,
                cwd=str(CFG.ROOT_DIR),
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=float(timeout_s if timeout_s is not None else getattr(CFG, "CODEX_BOOT_PROBE_TIMEOUT_S", 120)),
            )
        except subprocess.TimeoutExpired:
            return False, "Engineering channel: OFFLINE (probe timed out)"
        except Exception as exc:
            return False, f"Engineering channel: OFFLINE ({exc})"

        if completed.returncode != 0:
            detail = str(label or "Codex").strip()
            return False, f"Engineering channel: OFFLINE ({detail} exit {completed.returncode})"
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            return False, "Engineering channel: OFFLINE (invalid probe output)"
        if str(payload.get("status") or "").strip().lower() == "ok":
            return True, "Engineering channel: ONLINE"
        return False, "Engineering channel: OFFLINE (probe response invalid)"


def codex_repair_output_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "status",
            "summary",
            "changed_files",
            "verification_commands",
            "restart_recommended",
            "notes",
        ],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["fixed", "blocked", "no_fix"],
            },
            "summary": {"type": "string"},
            "changed_files": {
                "type": "array",
                "items": {"type": "string"},
            },
            "verification_commands": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "restart_recommended": {"type": "boolean"},
            "notes": {"type": "string"},
        },
    }


def write_codex_repair_schema(path: Path) -> Path:
    schema_path = Path(path)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(
        json.dumps(codex_repair_output_schema(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return schema_path


def build_codex_repair_prompt(request: dict[str, Any]) -> str:
    request_json = json.dumps(request, indent=2, ensure_ascii=False)
    return (
        "You are Codex acting as Piper's external engineering repair agent.\n\n"
        "Authority:\n"
        "- AGENTS.md is the architecture doctrine.\n"
        "- Keep execution truthful.\n"
        "- Patch Piper's own code only.\n"
        "- Do not modify arbitrary user workspace files as part of self-healing.\n\n"
        "Task:\n"
        "1. Read the engineering escalation context below.\n"
        "2. Diagnose the actual Piper runtime/code issue.\n"
        "3. Make the smallest safe repository changes that fix it.\n"
        "4. Run targeted validation commands.\n"
        "5. Update notes/ if the repair reveals stable operational knowledge.\n"
        "6. Return JSON only, matching the provided schema.\n\n"
        "Output requirements:\n"
        "- status: fixed | blocked | no_fix\n"
        "- summary: short plain-English outcome\n"
        "- changed_files: relative repo paths you changed\n"
        "- verification_commands: argv arrays for idempotent commands that should pass after the fix\n"
        "- restart_recommended: true only if the fix is verified and Piper should restart\n"
        "- notes: concise risk or follow-up note\n\n"
        "Command rules:\n"
        "- Prefer Python compile/smoke commands already used in this repo.\n"
        "- verification_commands must be argv arrays, not shell strings.\n"
        "- Do not include destructive commands.\n\n"
        "Brief usage:\n"
        "- Start from the condensed brief below.\n"
        "- The original escalation log is also available at brief_path if you need to inspect it directly.\n"
        "- Do not spend time rereading unrelated history unless the brief is insufficient.\n\n"
        "Engineering escalation request:\n"
        f"{request_json}\n"
    )


class CodexRepairCoordinator:
    _ACTIVE_STATES = {"queued", "running", "validating", "restart_requested"}

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        data_dir: Path | None = None,
        auto_enabled: bool | None = None,
        poll_interval_s: float | None = None,
    ) -> None:
        self.repo_root = Path(repo_root or CFG.ROOT_DIR)
        self.data_dir = Path(data_dir or CFG.DATA_DIR)
        self.store = CodexRepairStateStore.for_data_dir(self.data_dir)
        self.auto_enabled = CFG.CODEX_AUTO_REPAIR_ENABLED if auto_enabled is None else bool(auto_enabled)
        self.poll_interval_s = float(CFG.CODEX_REPAIR_POLL_INTERVAL_S if poll_interval_s is None else poll_interval_s)
        self._last_poll_ts = 0.0
        self._last_status_signature = ""
        initial_status = self.store.load_status()
        if self._should_suppress_boot_replay(initial_status, recovery=self.store.load_recovery()):
            self._last_status_signature = self._status_signature(initial_status)

    def current_status(self) -> dict[str, Any]:
        return self.store.load_status()

    def peek_recovery(self) -> dict[str, Any]:
        return self.store.load_recovery()

    def consume_recovery(self) -> dict[str, Any]:
        recovery = self.store.load_recovery()
        if not recovery:
            return {}
        self.store.clear_recovery()
        self.store.clear_request()
        status = self.store.load_status()
        if status and str(status.get("request_id") or "") == str(recovery.get("request_id") or ""):
            updated = dict(status)
            updated["state"] = "resumed"
            updated["restart_requested"] = False
            updated["message"] = "Repair recovery handed back to Piper."
            updated["updated_at_utc"] = _utc_timestamp()
            self.store.save_status(updated)
        return recovery

    def poll_status(self, *, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        if not force and (now - self._last_poll_ts) < self.poll_interval_s:
            return None
        self._last_poll_ts = now
        status = self.store.load_status()
        if not status:
            return None
        signature = self._status_signature(status)
        if not force and signature == self._last_status_signature:
            return None
        self._last_status_signature = signature
        return status

    def request_repair(self, escalation: EscalationDecision | dict[str, Any]) -> dict[str, Any]:
        decision = dict(escalation or {})
        if not self.auto_enabled:
            return {
                "accepted": False,
                "message": "Codex auto-repair is disabled.",
            }

        existing = self.store.load_status()
        existing_state = str(existing.get("state") or "").strip().lower()
        if existing_state in self._ACTIVE_STATES and self._status_is_stale(existing):
            self.store.clear_status()
            existing = {}
            existing_state = ""
        if existing_state in self._ACTIVE_STATES:
            return {
                "accepted": False,
                "message": f"Codex repair already {existing_state}.",
                "status": existing,
            }

        brief_path = Path(str(decision.get("brief_path") or CFG.CODEX_ESCALATION_LOG_PATH)).resolve()
        support_payload = load_latest_escalation_payload(brief_path)
        support_excerpt = condense_escalation_payload(support_payload)
        request_id = datetime.now(timezone.utc).strftime("codex-repair-%Y%m%d-%H%M%S")
        request = {
            "request_id": request_id,
            "created_at_utc": _utc_timestamp(),
            "reason": str(decision.get("reason") or "").strip(),
            "summary": str(decision.get("summary") or "").strip(),
            "manual": bool(decision.get("manual")),
            "trigger_kind": str(decision.get("trigger_kind") or "").strip(),
            "brief_path": str(brief_path),
            "support_excerpt": support_excerpt,
            "user_msg": str(support_excerpt.get("user_msg") or "").strip(),
            "retry_user_message": str(support_excerpt.get("user_msg") or "").strip(),
            "status_snapshot": str(support_excerpt.get("status_snapshot") or "").strip(),
            "repo_root": str(self.repo_root),
        }
        self.store.save_request(request)
        queued_status = {
            "request_id": request_id,
            "state": "queued",
            "message": "Queued Codex repair request.",
            "updated_at_utc": _utc_timestamp(),
            "reason": request["reason"],
            "summary": request["summary"],
            "brief_path": request["brief_path"],
            "restart_requested": False,
        }
        self.store.save_status(queued_status)
        worker_info = self._launch_worker(request_id)
        if not worker_info.get("ok"):
            failed_status = dict(queued_status)
            failed_status["state"] = "failed"
            failed_status["message"] = str(worker_info.get("message") or "Failed to start Codex repair worker.")
            failed_status["updated_at_utc"] = _utc_timestamp()
            self.store.save_status(failed_status)
            return {
                "accepted": False,
                "message": failed_status["message"],
                "status": failed_status,
            }
        started_status = dict(queued_status)
        started_status["state"] = "running"
        started_status["message"] = "Codex repair worker started."
        started_status["worker_pid"] = int(worker_info.get("pid") or 0)
        started_status["started_at_utc"] = _utc_timestamp()
        started_status["updated_at_utc"] = _utc_timestamp()
        self.store.save_status(started_status)
        return {
            "accepted": True,
            "message": started_status["message"],
            "status": started_status,
        }

    def _launch_worker(self, request_id: str) -> dict[str, Any]:
        worker_path = self.repo_root / "scripts" / "codex_repair_worker.py"
        if not worker_path.exists():
            return {"ok": False, "message": f"Worker script missing: {worker_path}"}

        worker_log_path = data_debug_path(self.data_dir, "codex_repair_worker.log")
        worker_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = worker_log_path.open("a", encoding="utf-8", errors="replace")
        cmd = [
            sys.executable,
            str(worker_path),
            "--data-dir",
            str(self.data_dir),
            "--request-id",
            str(request_id),
        ]
        simulate_mode = str(os.environ.get("PIPER_CODEX_REPAIR_SIMULATE") or "").strip()
        if simulate_mode:
            cmd.extend(["--simulate", simulate_mode])

        try:
            popen_kwargs: dict[str, Any] = {
                "cwd": str(self.repo_root),
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
                "start_new_session": True,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            log_handle.close()
            return {"ok": False, "message": f"Failed to launch Codex repair worker: {exc}"}
        finally:
            try:
                log_handle.close()
            except Exception:
                pass
        return {"ok": True, "pid": int(process.pid)}

    @staticmethod
    def _status_signature(status: dict[str, Any]) -> str:
        return json.dumps(
            {
                "request_id": status.get("request_id", ""),
                "state": status.get("state", ""),
                "message": status.get("message", ""),
                "restart_requested": bool(status.get("restart_requested")),
            },
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def _should_suppress_boot_replay(cls, status: dict[str, Any], *, recovery: dict[str, Any] | None = None) -> bool:
        if not status:
            return False
        state = str(status.get("state") or "").strip().lower()
        if state == "restart_requested" and cls._recovery_matches_status(status, recovery or {}):
            return True
        if state in cls._ACTIVE_STATES:
            return False
        return True

    @staticmethod
    def _recovery_matches_status(status: dict[str, Any], recovery: dict[str, Any]) -> bool:
        if not status or not recovery:
            return False
        status_request_id = str(status.get("request_id") or "").strip()
        recovery_request_id = str(recovery.get("request_id") or "").strip()
        return bool(status_request_id and status_request_id == recovery_request_id)

    @staticmethod
    def _status_is_stale(status: dict[str, Any]) -> bool:
        state = str(status.get("state") or "").strip().lower()
        if state not in CodexRepairCoordinator._ACTIVE_STATES:
            return False
        updated_raw = str(status.get("updated_at_utc") or "").strip()
        if not updated_raw:
            return True
        try:
            updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        except Exception:
            return True
        age_s = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age_s > max(float(getattr(CFG, "CODEX_REPAIR_TIMEOUT_S", 1800)) + 300.0, 1800.0)
