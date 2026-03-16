from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from _bootstrap import ROOT_DIR

from config import CFG, data_state_path
from AGENTS.harness.session import PiperHarness


DATA_DIR = ROOT_DIR / "data"
RESULTS_PATH = DATA_DIR / "benchmarks" / "results" / "model_compare_fair.json"


@dataclass(frozen=True)
class Candidate:
    name: str
    model_path: Path


def _first_existing_path(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _to_windows_path(path: Path | str) -> str:
    raw = str(path)
    if os.name == "nt":
        return raw
    if raw.startswith("/mnt/") and len(raw) > 6:
        drive = raw[5].upper()
        suffix = raw[7:].replace("/", "\\")
        return f"{drive}:\\{suffix}"
    return raw


def _server_exe() -> Path:
    server_path = Path(getattr(CFG, "LLAMA_SERVER_EXE"))
    if not server_path.exists():
        raise FileNotFoundError(f"llama-server.exe not found at {server_path}")
    return server_path


def _resolve_mmproj(model_path: Path) -> Path | None:
    model_name = model_path.name.lower()
    if "qwen3.5" not in model_name:
        return None

    size_tag = None
    if "9b" in model_name:
        size_tag = "9B"
    elif "4b" in model_name:
        size_tag = "4B"

    preferred: list[str] = []
    if size_tag is not None:
        preferred.extend(
            [
                f"Qwen3.5-{size_tag}.mmproj-F16.gguf",
                f"Qwen3.5-{size_tag}.mmproj-BF16.gguf",
                f"Qwen3.5-{size_tag}.mmproj-F32.gguf",
            ]
        )
    preferred.extend(["mmproj-F16.gguf", "mmproj-BF16.gguf", "mmproj-F32.gguf"])

    model_dir = model_path.parent
    for name in preferred:
        candidate = model_dir / name
        if candidate.exists():
            return candidate
    return None


def _reasoning_budget(model_path: Path) -> int:
    return 0 if "qwen3.5" in model_path.name.lower() else -1


def _wsl_host_gateway() -> str:
    if os.name == "nt":
        return "127.0.0.1"
    try:
        proc = subprocess.run(
            ["bash", "-lc", "ip route show default | awk '/default/ {print $3; exit}'"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        candidate = proc.stdout.strip()
        if candidate:
            return candidate
    except Exception:
        pass
    return "127.0.0.1"


def _wait_for_health(base_url: str, timeout_s: float, process: subprocess.Popen[str]) -> tuple[bool, float, str]:
    started = time.perf_counter()
    while (time.perf_counter() - started) < timeout_s:
        if process.poll() is not None:
            return False, time.perf_counter() - started, f"crashed:{process.returncode}"
        try:
            request = urllib.request.Request(f"{base_url}/health", method="GET")
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 200:
                    return True, time.perf_counter() - started, "ready"
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                return False, time.perf_counter() - started, f"http:{exc.code}"
        except Exception:
            pass
        time.sleep(1)
    return False, time.perf_counter() - started, "timeout"


def _kill_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=15)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


class _ConfigOverlay:
    def __init__(self, **overrides: Any) -> None:
        self.overrides = overrides
        self.snapshot: dict[str, Any] = {}

    def __enter__(self) -> None:
        for key, value in self.overrides.items():
            self.snapshot[key] = getattr(CFG, key)
            object.__setattr__(CFG, key, value)

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, value in self.snapshot.items():
            object.__setattr__(CFG, key, value)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _snapshot_workspace(workspace_dir: Path) -> dict[str, Any]:
    if not workspace_dir.exists():
        return {}
    snapshot: dict[str, Any] = {}
    for path in sorted(workspace_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(workspace_dir).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            if len(text) > 4000:
                text = text[:4000] + "\n[TRUNCATED]"
            snapshot[rel] = text
        except Exception:
            snapshot[rel] = {"size_bytes": path.stat().st_size}
    return snapshot


def _snapshot_state(data_dir: Path) -> dict[str, Any]:
    return {
        "tasks": _read_json(data_state_path(data_dir, "tasks.json")),
        "events": _read_json(data_state_path(data_dir, "events.json")),
        "knowledge": _read_json(data_state_path(data_dir, "knowledge.json")),
        "world_model": _read_json(data_state_path(data_dir, "world_model.json")),
        "situational_state": _read_json(data_state_path(data_dir, "situational_state.json")),
        "intent_state": _read_json(data_state_path(data_dir, "intent_state.json")),
        "workspace": _snapshot_workspace(data_dir / "workspace"),
    }


def _knowledge_value(knowledge: dict[str, Any], key: str) -> str:
    target = key.strip().lower()
    for existing_key, payload in (knowledge or {}).items():
        if str(existing_key).strip().lower() != target:
            continue
        if isinstance(payload, dict):
            return str(payload.get("value", ""))
        return str(payload)
    return ""


def _turn_record(result, data_dir: Path) -> dict[str, Any]:
    errors = [event["payload"] for event in result.ui_events if event.get("kind") == "error"]
    agent_logs = [event["payload"] for event in result.ui_events if event.get("kind") == "agent_log"]
    return {
        "user_text": result.user_text,
        "assistant_text": result.assistant_text,
        "timed_out": result.timed_out,
        "duration_s": result.duration_s,
        "status_history": result.status_history,
        "system_messages": result.system_messages,
        "errors": errors,
        "agent_logs": agent_logs,
        "tts_utterances": result.tts_utterances,
        "state": _snapshot_state(data_dir),
    }


def _collect_errors(turns: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for turn in turns:
        errors.extend(str(item) for item in turn.get("errors", []))
    return errors


def _task_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    tasks_mid = turns[1]["state"].get("tasks") or {}
    tasks_final = turns[-1]["state"].get("tasks") or {}
    errors = _collect_errors(turns)
    return {
        "pass": ("buy milk" in tasks_mid) and ("buy milk" not in tasks_final) and not errors,
        "signals": {
            "task_added": "buy milk" in tasks_mid,
            "task_removed": "buy milk" not in tasks_final,
            "persona_errors": [err for err in errors if "Persona Error" in err],
        },
    }


def _event_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    events_mid = turns[1]["state"].get("events") or {}
    events_final = turns[-1]["state"].get("events") or {}
    errors = _collect_errors(turns)
    event_added = any("dentist appointment" in name.lower() for name in events_mid.keys())
    event_removed = not any("dentist appointment" in name.lower() for name in events_final.keys())
    return {
        "pass": event_added and event_removed and not errors,
        "signals": {
            "event_added": event_added,
            "event_removed": event_removed,
            "persona_errors": [err for err in errors if "Persona Error" in err],
        },
    }


def _knowledge_context_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    final_reply = (turns[-1].get("assistant_text") or "").lower()
    knowledge = turns[-1]["state"].get("knowledge") or {}
    occupation = _knowledge_value(knowledge, "occupation").lower()
    vehicle = ", ".join(
        value
        for value in [
            _knowledge_value(knowledge, "Vehicle"),
            _knowledge_value(knowledge, "vehicle"),
        ]
        if value
    ).lower()
    family = _knowledge_value(knowledge, "Family/Relationships").lower()
    errors = _collect_errors(turns)
    return {
        "pass": (
            "mechanical engineer" in occupation
            and "pilot" in occupation
            and "motorcycle" in vehicle
            and "bmw" in vehicle
            and "dora" in family
            and "dora" in final_reply
            and ("mechanical engineer" in final_reply or "pilot" in final_reply)
            and ("bmw" in final_reply or "motorcycle" in final_reply)
            and "work tomorrow" not in final_reply
            and not errors
        ),
        "signals": {
            "occupation": occupation,
            "vehicle": vehicle,
            "family": family,
            "final_reply": turns[-1].get("assistant_text"),
            "persona_errors": [err for err in errors if "Persona Error" in err],
        },
    }


def _file_flow_eval(turns: list[dict[str, Any]]) -> dict[str, Any]:
    workspace = turns[-1]["state"].get("workspace") or {}
    notes = str(workspace.get("cmpdemo/notes.txt", ""))
    calc = str(workspace.get("cmpdemo/calc.py", ""))
    project_cfg = str(workspace.get("cmpdemo/project/config.json", ""))
    errors = _collect_errors(turns)
    return {
        "pass": (
            "alpha" in notes
            and "beta" in notes
            and "def add" in calc
            and '"version": 2' in project_cfg.lower()
            and '"enabled": true' in project_cfg.lower()
            and not errors
        ),
        "signals": {
            "workspace_files": sorted(workspace.keys()),
            "notes_preview": notes,
            "calc_preview": calc,
            "config_preview": project_cfg,
            "persona_errors": [err for err in errors if "Persona Error" in err],
        },
    }


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "task_flow",
        "turns": [
            "Add a task to buy milk.",
            "What tasks do I have right now?",
            "I bought the milk.",
            "What tasks do I have now?",
        ],
        "evaluator": _task_flow_eval,
    },
    {
        "name": "event_flow",
        "turns": [
            "Add an event dentist appointment on today.",
            "What events do I have scheduled?",
            "I went to the dentist appointment.",
            "What events do I have now?",
        ],
        "evaluator": _event_flow_eval,
    },
    {
        "name": "knowledge_long_context",
        "turns": [
            "I am a pilot for Turkish Airlines.",
            "I am also a mechanical engineer.",
            "My daughter is Dora.",
            "I have a BMW.",
            "I also have a motorcycle.",
            "I like pistachios.",
            "I am saving up to buy a house.",
            "Tomorrow is off.",
            "I do not need to wake early for work tomorrow.",
            "What do you know about me, and what is tomorrow like for me?",
        ],
        "evaluator": _knowledge_context_eval,
    },
    {
        "name": "file_management",
        "turns": [
            "In the workspace, create a folder named cmpdemo and inside it create a file named notes.txt with exactly two lines: alpha and beta.",
            "Inside cmpdemo, create a Python file named calc.py that defines add(a, b) and prints add(2, 3).",
            "Inside cmpdemo, create a folder named project and inside it create config.json with keys name='demo' and version=1.",
            "Update cmpdemo/project/config.json so version becomes 2 and enabled becomes true.",
            "Read cmpdemo/notes.txt, cmpdemo/calc.py, and cmpdemo/project/config.json and tell me the final contents.",
        ],
        "evaluator": _file_flow_eval,
    },
]


def _run_scenario(turns: list[str], *, timeout_s: float) -> tuple[list[dict[str, Any]], str]:
    harness = PiperHarness(
        persist_turns=False,
        enable_memory_learning=True,
        isolated_data=True,
        keep_data_copy=True,
    )
    boot = harness.start()
    scenario_turns: list[dict[str, Any]] = []
    if not boot.ready:
        harness.close()
        return [{"boot_error": True, "boot": boot.__dict__}], ""

    try:
        for text in turns:
            result = harness.send_text(text, timeout_s=timeout_s)
            scenario_turns.append(_turn_record(result, harness.data_dir))
    finally:
        harness.close()
    return scenario_turns, str(harness.kept_data_dir or "")


def _compare_candidate(
    candidate: Candidate,
    *,
    port: int,
    timeout_s: float,
) -> dict[str, Any]:
    host = _wsl_host_gateway()
    base_url = f"http://{host}:{port}"
    mmproj = _resolve_mmproj(candidate.model_path)
    reasoning_budget = _reasoning_budget(candidate.model_path)
    server_log = DATA_DIR / "benchmarks" / "logs" / f"{candidate.model_path.stem}.faircmp.log"
    server_log.parent.mkdir(parents=True, exist_ok=True)

    command = [
        str(_server_exe()),
        "-m",
        _to_windows_path(candidate.model_path),
        "--port",
        str(port),
        "--ctx-size",
        str(getattr(CFG, "LLAMA_SERVER_CTX_SIZE", 8192)),
        "-ngl",
        str(getattr(CFG, "LLAMA_SERVER_GPU_LAYERS", 99)),
        "--host",
        "0.0.0.0",
        "--reasoning-budget",
        str(reasoning_budget),
    ]
    if mmproj is not None:
        command.extend(["--mmproj", _to_windows_path(mmproj)])

    process: subprocess.Popen[str] | None = None
    try:
        with server_log.open("w", encoding="utf-8", errors="replace") as log_handle:
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            boot_ok, boot_s, boot_status = _wait_for_health(base_url, timeout_s, process)
        result: dict[str, Any] = {
            "model_name": candidate.name,
            "model_path": str(candidate.model_path),
            "mmproj_path": str(mmproj) if mmproj else None,
            "reasoning_budget": reasoning_budget,
            "base_url": base_url,
            "boot_ok": boot_ok,
            "boot_seconds": round(boot_s, 3),
            "boot_status": boot_status,
            "server_log": str(server_log),
            "scenarios": [],
        }
        if not boot_ok:
            return result

        with _ConfigOverlay(
            LLAMA_SERVER_URL=base_url,
            MODEL_PATH=candidate.model_path,
            MMPROJ_PATH=mmproj,
            LLAMA_SERVER_REASONING_BUDGET=reasoning_budget,
        ):
            for scenario in SCENARIOS:
                turns, kept_data_dir = _run_scenario(scenario["turns"], timeout_s=timeout_s)
                evaluation = scenario["evaluator"](turns)
                result["scenarios"].append(
                    {
                        "name": scenario["name"],
                        "turns": turns,
                        "kept_data_dir": kept_data_dir,
                        "evaluation": evaluation,
                    }
                )
        result["summary"] = {
            "passed": sum(1 for item in result["scenarios"] if item["evaluation"].get("pass")),
            "total": len(result["scenarios"]),
            "persona_errors": sum(
                len(item["evaluation"].get("signals", {}).get("persona_errors", []))
                for item in result["scenarios"]
            ),
        }
        return result
    finally:
        _kill_process(process)


def _candidate_from_name(name: str) -> Candidate:
    normalized = name.strip().lower()
    q25_filename = "qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf"
    q25_path = _first_existing_path(
        Path(r"C:\Piper\models\llama") / q25_filename,
        ROOT_DIR / "models" / "llama" / q25_filename,
    )
    mapping = {
        "q25": Candidate(
            name="qwen2.5-14b",
            model_path=q25_path,
        ),
        "q4": Candidate(
            name="qwen3.5-4b-q4_k_m",
            model_path=ROOT_DIR / "models" / "llama" / "Qwen3.5-4B-Q4_K_M.gguf",
        ),
        "q6": Candidate(
            name="qwen3.5-4b-q6_k",
            model_path=ROOT_DIR / "models" / "llama" / "Qwen3.5-4B-Q6_K.gguf",
        ),
        "q8": Candidate(
            name="qwen3.5-4b-q8_0",
            model_path=ROOT_DIR / "models" / "llama" / "Qwen3.5-4B-Q8_0.gguf",
        ),
        "q9": Candidate(
            name="qwen3.5-9b-q6_k",
            model_path=ROOT_DIR / "models" / "llama" / "Qwen_Qwen3.5-9B-Q6_K.gguf",
        ),
        "bf16": Candidate(
            name="qwen3.5-4b-bf16",
            model_path=ROOT_DIR / "models" / "llama" / "Qwen3.5-4B-BF16.gguf",
        ),
    }
    candidate = mapping.get(normalized)
    if candidate is None:
        raise SystemExit(f"Unknown candidate '{name}'. Use q25, q4, q6, q8, q9, or bf16.")
    if not candidate.model_path.exists():
        raise SystemExit(f"Model file not found: {candidate.model_path}")
    return candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a fair end-to-end Piper model comparison.")
    parser.add_argument(
        "--candidates",
        nargs="+",
        default=["q25", "q6"],
        help="Candidate set to compare. Choices: q25 q4 q6 q8 q9 bf16",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--port-base", type=int, default=8111)
    parser.add_argument("--output", type=Path, default=RESULTS_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates = [_candidate_from_name(name) for name in args.candidates]
    results = []
    for index, candidate in enumerate(candidates):
        print(f"[compare] testing {candidate.name}", flush=True)
        results.append(
            _compare_candidate(
                candidate,
                port=args.port_base + index,
                timeout_s=args.timeout,
            )
        )

    payload = {
        "updated_at_epoch_s": int(time.time()),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
