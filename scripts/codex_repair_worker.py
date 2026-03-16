from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG, data_debug_path  # noqa: E402
from core.codex_bridge import build_codex_exec_command, build_codex_repair_prompt, write_codex_repair_schema  # noqa: E402
from memory.codex_repair_store import CodexRepairStateStore  # noqa: E402


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def _emit_stdout(text: str) -> None:
    chunk = str(text or "")
    if not chunk:
        return
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(chunk.encode("utf-8", errors="replace"))
        buffer.flush()
        return
    sys.stdout.write(chunk)
    sys.stdout.flush()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _update_status(store: CodexRepairStateStore, request: dict[str, Any], *, state: str, message: str, **extra: Any) -> None:
    current = store.load_status()
    payload = {
        "request_id": str(request.get("request_id") or ""),
        "reason": str(request.get("reason") or ""),
        "summary": str(request.get("summary") or ""),
        "brief_path": str(request.get("brief_path") or ""),
        "state": str(state or "").strip(),
        "message": str(message or "").strip(),
        "updated_at_utc": _utc_timestamp(),
        "restart_requested": bool(extra.pop("restart_requested", False)),
    }
    if str(current.get("request_id") or "") == payload["request_id"]:
        for key in ("worker_pid", "codex_pid", "started_at_utc", "result_path"):
            if key in current and key not in extra:
                payload[key] = current[key]
    payload.update(extra)
    store.save_status(payload)
    _emit_stdout(f"[Codex Repair] {payload['state']}: {payload['message']}\n")


def _normalize_command(command: list[str]) -> list[str]:
    if not command:
        return []
    normalized = [str(part) for part in command if str(part)]
    if not normalized:
        return []
    head = normalized[0].lower()
    if head in {"python", "python3", "py"} or head.endswith("python.exe") or head.endswith("/python") or head.endswith("/python3"):
        normalized[0] = sys.executable
    elif not Path(normalized[0]).exists():
        resolved = shutil.which(normalized[0])
        if resolved:
            normalized[0] = resolved
    return normalized


def _run_verification_commands(commands: list[list[str]], *, repo_root: Path, timeout_s: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    per_command_timeout = max(30.0, min(float(timeout_s), 600.0))
    for command in commands:
        argv = _normalize_command([str(part) for part in command])
        if not argv:
            results.append({"ok": False, "command": command, "error": "Empty verification command."})
            break
        try:
            completed = subprocess.run(
                argv,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=per_command_timeout,
            )
            item = {
                "ok": completed.returncode == 0,
                "command": argv,
                "returncode": int(completed.returncode),
                "stdout": str(completed.stdout or "")[-4000:],
                "stderr": str(completed.stderr or "")[-4000:],
            }
            results.append(item)
            if completed.returncode != 0:
                break
        except Exception as exc:
            results.append({"ok": False, "command": argv, "error": str(exc)})
            break
    return results


def _load_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _simulated_result(mode: str) -> dict[str, Any]:
    normalized = str(mode or "").strip().lower()
    if normalized == "fixed":
        return {
            "status": "fixed",
            "summary": "Simulated repair completed successfully.",
            "changed_files": ["core/codex_bridge.py"],
            "verification_commands": [[sys.executable, "-c", "print('codex repair smoke ok')"]],
            "restart_recommended": True,
            "notes": "Simulation mode only.",
        }
    if normalized == "blocked":
        return {
            "status": "blocked",
            "summary": "Simulated repair could not proceed.",
            "changed_files": [],
            "verification_commands": [],
            "restart_recommended": False,
            "notes": "Simulation mode only.",
        }
    return {
        "status": "no_fix",
        "summary": "Simulated repair found no safe fix.",
        "changed_files": [],
        "verification_commands": [],
        "restart_recommended": False,
        "notes": "Simulation mode only.",
    }


def main() -> int:
    _configure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Run the external Codex repair worker.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--simulate", default="")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    repo_root = ROOT_DIR
    store = CodexRepairStateStore.for_data_dir(data_dir)
    request = store.load_request()
    if not request:
        _emit_stdout("[Codex Repair] No pending request found.\n")
        return 1
    if str(request.get("request_id") or "") != str(args.request_id):
        _emit_stdout("[Codex Repair] Request id mismatch.\n")
        return 1

    _update_status(store, request, state="running", message="Preparing Codex repair job.")
    schema_path = write_codex_repair_schema(data_dir / "reference" / "codex_repair_output.schema.json")
    result_path = data_debug_path(data_dir, f"codex_repair_result_{args.request_id}.json")

    try:
        simulate_mode = str(args.simulate or "").strip()
        if simulate_mode:
            result_payload = _simulated_result(simulate_mode)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            command, launch_label = build_codex_exec_command(
                cd_path=repo_root,
                output_schema_path=schema_path,
                output_path=result_path,
                dangerous=True,
            )
            if not command:
                configured = str(getattr(CFG, "CODEX_EXECUTABLE", "") or "").strip()
                configured_wsl = str(getattr(CFG, "CODEX_WSL_EXECUTABLE", "") or "").strip()
                details = (
                    "Codex executable not found. "
                    f"Configured value: {configured or '(empty)'}. "
                    f"WSL value: {configured_wsl or '(empty)'}."
                )
                _update_status(store, request, state="failed", message=details)
                return 1

            prompt = build_codex_repair_prompt(request)
            process = subprocess.Popen(
                command,
                cwd=str(repo_root),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            _update_status(
                store,
                request,
                state="running",
                message=f"Codex is analyzing the escalation brief via {launch_label or 'Codex'}. PID {process.pid}.",
                codex_pid=int(process.pid),
            )
            stdout, _ = process.communicate(
                prompt,
                timeout=float(getattr(CFG, "CODEX_REPAIR_TIMEOUT_S", 1800)),
            )
            if stdout:
                _emit_stdout(stdout[-12000:])
                if not str(stdout).endswith("\n"):
                    _emit_stdout("\n")
            if process.returncode != 0:
                _update_status(
                    store,
                    request,
                    state="failed",
                    message=f"Codex repair execution failed with exit code {process.returncode}.",
                    codex_returncode=int(process.returncode),
                )
                return process.returncode or 1

        result = _load_result(result_path)
        if not result:
            _update_status(store, request, state="failed", message="Codex repair did not produce a valid JSON result.")
            return 1

        status = str(result.get("status") or "").strip().lower()
        summary = str(result.get("summary") or "").strip() or "Codex repair finished."
        verification_commands = result.get("verification_commands") or []
        if not isinstance(verification_commands, list):
            verification_commands = []

        if status != "fixed" or not bool(result.get("restart_recommended")):
            _update_status(
                store,
                request,
                state="failed",
                message=summary,
                result_path=str(result_path),
                codex_status=status or "unknown",
                notes=str(result.get("notes") or "").strip(),
            )
            return 1

        if not verification_commands:
            _update_status(
                store,
                request,
                state="failed",
                message="Codex marked the repair fixed without any verification commands.",
                result_path=str(result_path),
            )
            return 1

        _update_status(store, request, state="validating", message="Running repair verification commands.", result_path=str(result_path))
        verification_results = _run_verification_commands(
            [list(command) for command in verification_commands if isinstance(command, list)],
            repo_root=repo_root,
            timeout_s=float(getattr(CFG, "CODEX_REPAIR_TIMEOUT_S", 1800)),
        )
        failed_result = next((item for item in verification_results if not bool(item.get("ok"))), None)
        if failed_result is not None:
            _update_status(
                store,
                request,
                state="failed",
                message="Repair verification failed after Codex patch.",
                result_path=str(result_path),
                verification_results=verification_results,
            )
            return 1

        retry_user_message = str(request.get("retry_user_message") or request.get("user_msg") or "").strip()
        recovery_payload = {
            "request_id": str(request.get("request_id") or ""),
            "created_at_utc": _utc_timestamp(),
            "summary": summary,
            "reason": str(request.get("reason") or ""),
            "brief_path": str(request.get("brief_path") or ""),
            "result_path": str(result_path),
            "changed_files": [str(item) for item in (result.get("changed_files") or []) if str(item).strip()],
            "verification_commands": verification_commands,
            "verification_results": verification_results,
            "retry_user_message": retry_user_message,
            "notes": str(result.get("notes") or "").strip(),
        }
        store.save_recovery(recovery_payload)
        _update_status(
            store,
            request,
            state="restart_requested",
            message="Repair verified. Restarting Piper to resume the interrupted request.",
            restart_requested=True,
            result_path=str(result_path),
            verification_results=verification_results,
        )
        return 0
    except subprocess.TimeoutExpired:
        process_obj = locals().get("process")
        if process_obj is not None:
            try:
                process_obj.kill()
            except Exception:
                pass
        _update_status(store, request, state="failed", message="Codex repair timed out.")
        return 1
    except Exception as exc:
        _update_status(store, request, state="failed", message=f"Unhandled Codex repair failure: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
