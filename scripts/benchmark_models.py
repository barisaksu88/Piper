from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _bootstrap import ROOT_DIR

DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models" / "llama"
RESULTS_PATH = DATA_DIR / "benchmarks" / "results" / "model_benchmarks.json"
SELECTION_PATH = DATA_DIR / "state" / "model_selection.json"
LOG_DIR = DATA_DIR / "benchmarks" / "logs"
HF_REPO = "unsloth/Qwen3.5-4B-GGUF"
HF_API_URL = f"https://huggingface.co/api/models/{HF_REPO}?blobs=1"
HF_RESOLVE_URL = f"https://huggingface.co/{HF_REPO}/resolve/main"
DEFAULT_CANDIDATE_NAMES = [
    "Qwen3.5-4B-Q4_K_M.gguf",
    "Qwen3.5-4B-Q5_K_M.gguf",
    "Qwen3.5-4B-Q8_0.gguf",
    "Qwen3.5-4B-IQ4_NL.gguf",
]
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_PORT_BASE = 8091
DEFAULT_CTX_SIZE = int(os.environ.get("PIPER_LLM_CTX_SIZE", "8192"))
DEFAULT_GPU_LAYERS = int(os.environ.get("PIPER_LLM_GPU_LAYERS", "99"))


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    path: Path
    source: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    messages: list[dict[str, str]]
    expected_kind: str
    expected_value: str
    max_tokens: int = 48


BENCHMARK_CASES = [
    BenchmarkCase(
        name="exact_ok",
        messages=[{"role": "user", "content": "Reply with exactly the word OK."}],
        expected_kind="text",
        expected_value="OK",
        max_tokens=16,
    ),
    BenchmarkCase(
        name="route_chat_json",
        messages=[
            {
                "role": "user",
                "content": (
                    "Return minified JSON only. "
                    "For the user message hi, return exactly {\"decision\":\"CHAT\"}."
                ),
            }
        ],
        expected_kind="json_field",
        expected_value="decision=CHAT",
        max_tokens=32,
    ),
    BenchmarkCase(
        name="task_event_split",
        messages=[
            {
                "role": "user",
                "content": (
                    "Return minified JSON only. "
                    "Classify the reminder Sarah birthday is tomorrow. "
                    "Return exactly {\"bucket\":\"event\"}."
                ),
            }
        ],
        expected_kind="json_field",
        expected_value="bucket=event",
        max_tokens=32,
    ),
]


def server_exe() -> Path:
    override = os.environ.get("PIPER_LLAMA_SERVER_EXE", "").strip()
    if override:
        candidate = Path(override)
        if candidate.exists():
            return candidate

    local_runtime = ROOT_DIR / "runtime" / "llama.cpp" / "llama-server.exe"
    if local_runtime.exists():
        return local_runtime

    hardcoded = Path(r"F:\llama.cpp\llama-server.exe")
    if hardcoded.exists():
        return hardcoded
    local = ROOT_DIR / "llama-server.exe"
    if local.exists():
        return local
    raise FileNotFoundError("llama-server.exe not found.")


def baseline_model() -> Path | None:
    candidates = [
        Path(r"C:\Piper\models\llama\qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf"),
        MODELS_DIR / "qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def fetch_repo_metadata() -> dict[str, Any]:
    request = urllib.request.Request(
        HF_API_URL,
        headers={"User-Agent": "PiperModelBench/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def repo_file_sizes(metadata: dict[str, Any]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for sibling in metadata.get("siblings", []):
        name = sibling.get("rfilename")
        if not name:
            continue
        size = sibling.get("size") or sibling.get("lfs", {}).get("size")
        if isinstance(size, int):
            sizes[name] = size
    return sizes


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def download_file(name: str, expected_size: int | None) -> Path:
    ensure_dir(MODELS_DIR)
    destination = MODELS_DIR / name
    if destination.exists() and destination.stat().st_size > 0:
        if expected_size is None or destination.stat().st_size == expected_size:
            print(f"[download] Reusing {destination.name}", flush=True)
            return destination

    url = f"{HF_RESOLVE_URL}/{urllib.parse.quote(name)}?download=true"
    print(f"[download] Fetching {name}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "PiperModelBench/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)
    if expected_size is not None and destination.stat().st_size != expected_size:
        raise RuntimeError(
            f"Downloaded size mismatch for {name}: "
            f"{destination.stat().st_size} != {expected_size}"
        )
    return destination


def resolve_mmproj(model_path: Path) -> Path | None:
    model_name = model_path.name.lower()
    if "qwen3.5" not in model_name:
        return None

    size_tag = None
    if "9b" in model_name:
        size_tag = "9B"
    elif "4b" in model_name:
        size_tag = "4B"

    candidates: list[str] = []
    if size_tag is not None:
        if "bf16" in model_name:
            candidates.extend(
                [
                    f"Qwen3.5-{size_tag}.mmproj-BF16.gguf",
                    f"Qwen3.5-{size_tag}.mmproj-F16.gguf",
                ]
            )
        else:
            candidates.extend(
                [
                    f"Qwen3.5-{size_tag}.mmproj-F16.gguf",
                    f"Qwen3.5-{size_tag}.mmproj-BF16.gguf",
                ]
            )
        candidates.append(f"Qwen3.5-{size_tag}.mmproj-F32.gguf")

    if "bf16" in model_name:
        candidates.extend(["mmproj-BF16.gguf", "mmproj-F16.gguf"])
    else:
        candidates.extend(["mmproj-F16.gguf", "mmproj-BF16.gguf"])
    candidates.append("mmproj-F32.gguf")

    for name in candidates:
        candidate = MODELS_DIR / name
        if candidate.exists():
            return candidate
    return None


def reasoning_budget_for_model(model_path: Path) -> int:
    override = os.environ.get("PIPER_LLM_REASONING_BUDGET", "").strip()
    if override:
        return int(override)
    if "qwen3.5" in model_path.name.lower():
        return 0
    return -1


def kill_process(process: subprocess.Popen[str] | None) -> None:
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


def http_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content", "")).strip()


def validate_case(case: BenchmarkCase, content: str) -> tuple[bool, str]:
    if case.expected_kind == "text":
        passed = content == case.expected_value
        return passed, content

    if case.expected_kind == "json_field":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return False, content
        field, expected = case.expected_value.split("=", 1)
        actual = str(payload.get(field, "")).strip()
        return actual.lower() == expected.lower(), content

    return False, content


def benchmark_case(base_url: str, case: BenchmarkCase) -> dict[str, Any]:
    payload = {
        "model": "qwen",
        "messages": case.messages,
        "temperature": 0.0,
        "max_tokens": case.max_tokens,
    }
    start = time.perf_counter()
    try:
        response = http_json(f"{base_url}/v1/chat/completions", payload)
        latency_s = time.perf_counter() - start
        content = extract_content(response)
        passed, observed = validate_case(case, content)
        timings = response.get("timings") or {}
        usage = response.get("usage") or {}
    except Exception as exc:
        latency_s = time.perf_counter() - start
        passed = False
        observed = f"ERROR: {exc}"
        timings = {}
        usage = {}
    return {
        "name": case.name,
        "passed": passed,
        "observed": observed,
        "latency_s": round(latency_s, 3),
        "usage": usage,
        "timings": timings,
    }


def wait_for_health(base_url: str, timeout_s: float, process: subprocess.Popen[str]) -> tuple[bool, float, str]:
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


def tail_text(path: Path, max_chars: int = 1200) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def composite_score(result: dict[str, Any]) -> float:
    cases = result.get("cases", [])
    passed = [case for case in cases if case.get("passed")]
    if len(passed) != len(cases):
        return -1.0
    if not result.get("boot_ok"):
        return -1.0

    prompt_tps = [
        float(case.get("timings", {}).get("prompt_per_second", 0.0))
        for case in cases
        if case.get("timings")
    ]
    gen_tps = [
        float(case.get("timings", {}).get("predicted_per_second", 0.0))
        for case in cases
        if case.get("timings")
    ]
    avg_prompt = sum(prompt_tps) / len(prompt_tps) if prompt_tps else 0.0
    avg_gen = sum(gen_tps) / len(gen_tps) if gen_tps else 0.0
    boot_seconds = float(result.get("boot_seconds", 999.0))
    return round((avg_gen * 4.0) + avg_prompt - (boot_seconds * 0.5), 3)


def benchmark_candidate(
    candidate: ModelCandidate,
    *,
    timeout_s: float,
    port: int,
    ctx_size: int,
    gpu_layers: int,
) -> dict[str, Any]:
    ensure_dir(LOG_DIR)
    mmproj_path = resolve_mmproj(candidate.path)
    log_path = LOG_DIR / f"{candidate.path.stem}.log"
    popen_flags = 0
    if os.name == "nt":
        popen_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    command = [
        str(server_exe()),
        "-m",
        str(candidate.path),
        "--port",
        str(port),
        "--ctx-size",
        str(ctx_size),
        "-ngl",
        str(gpu_layers),
        "--host",
        "127.0.0.1",
    ]
    if mmproj_path is not None:
        command.extend(["--mmproj", str(mmproj_path)])
    command.extend(["--reasoning-budget", str(reasoning_budget_for_model(candidate.path))])

    print(f"[bench] Launching {candidate.name} on port {port}", flush=True)
    process: subprocess.Popen[str] | None = None
    with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
        try:
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=popen_flags,
            )
            base_url = f"http://127.0.0.1:{port}"
            boot_ok, boot_seconds, boot_status = wait_for_health(base_url, timeout_s, process)
            result: dict[str, Any] = {
                "name": candidate.name,
                "model_path": str(candidate.path),
                "mmproj_path": str(mmproj_path) if mmproj_path else None,
                "source": candidate.source,
                "size_bytes": candidate.size_bytes,
                "log_path": str(log_path),
                "boot_ok": boot_ok,
                "boot_seconds": round(boot_seconds, 3),
                "boot_status": boot_status,
                "ctx_size": ctx_size,
                "gpu_layers": gpu_layers,
                "reasoning_budget": reasoning_budget_for_model(candidate.path),
                "cases": [],
            }
            if boot_ok:
                for case in BENCHMARK_CASES:
                    case_result = benchmark_case(base_url, case)
                    result["cases"].append(case_result)
            result["score"] = composite_score(result)
            result["log_tail"] = tail_text(log_path)
            return result
        finally:
            kill_process(process)


def benchmark_candidates(
    candidates: list[ModelCandidate],
    *,
    timeout_s: float,
    port_base: int,
    ctx_size: int,
    gpu_layers: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        results.append(
            benchmark_candidate(
                candidate,
                timeout_s=timeout_s,
                port=port_base + index,
                ctx_size=ctx_size,
                gpu_layers=gpu_layers,
            )
        )
    return results


def write_results(results: list[dict[str, Any]], winner: dict[str, Any] | None) -> None:
    ensure_dir(DATA_DIR)
    payload = {
        "updated_at_epoch_s": int(time.time()),
        "results": results,
        "winner": winner,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_selection(winner: dict[str, Any]) -> None:
    payload = {
        "selected_model_path": winner["model_path"],
        "selected_mmproj_path": winner.get("mmproj_path"),
        "selected_at_epoch_s": int(time.time()),
        "score": winner.get("score"),
        "source": "benchmark_models.py",
    }
    SELECTION_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def all_local_candidates(metadata_sizes: dict[str, int]) -> list[ModelCandidate]:
    seen: set[str] = set()
    candidates: list[ModelCandidate] = []

    baseline = baseline_model()
    if baseline is not None:
        seen.add(str(baseline).lower())
        candidates.append(
            ModelCandidate(
                name=baseline.name,
                path=baseline,
                source="baseline",
                size_bytes=baseline.stat().st_size,
            )
        )

    for path in sorted(MODELS_DIR.glob("*.gguf"), key=lambda item: item.name.lower()):
        if path.name.lower().startswith("mmproj"):
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            ModelCandidate(
                name=path.name,
                path=path,
                source="local",
                size_bytes=metadata_sizes.get(path.name, path.stat().st_size),
            )
        )
    return candidates


def choose_winner(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    successful = [
        result
        for result in results
        if result.get("boot_ok")
        and result.get("cases")
        and all(case.get("passed") for case in result["cases"])
    ]
    if not successful:
        return None
    return sorted(
        successful,
        key=lambda result: (
            float(result.get("score", -1.0)),
            float(result.get("cases", [{}])[0].get("timings", {}).get("predicted_per_second", 0.0)),
            -float(result.get("boot_seconds", 999.0)),
        ),
        reverse=True,
    )[0]


def ensure_default_downloads(metadata_sizes: dict[str, int]) -> None:
    planned = list(DEFAULT_CANDIDATE_NAMES)
    if (MODELS_DIR / "Qwen3.5-4B-BF16.gguf").exists():
        planned.append("Qwen3.5-4B-BF16.gguf")

    for name in planned:
        if name in metadata_sizes:
            download_file(name, metadata_sizes.get(name))

    mmproj_name = "mmproj-F16.gguf"
    if mmproj_name in metadata_sizes:
        download_file(mmproj_name, metadata_sizes.get(mmproj_name))

    bf16_mmproj = "mmproj-BF16.gguf"
    if (MODELS_DIR / "Qwen3.5-4B-BF16.gguf").exists() and bf16_mmproj in metadata_sizes:
        download_file(bf16_mmproj, metadata_sizes.get(bf16_mmproj))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and benchmark candidate Piper models.")
    subparsers = parser.add_subparsers(dest="command", required=False)

    auto = subparsers.add_parser("auto", help="Download defaults, benchmark them, and select the winner.")
    auto.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    auto.add_argument("--port-base", type=int, default=DEFAULT_PORT_BASE)
    auto.add_argument("--ctx-size", type=int, default=DEFAULT_CTX_SIZE)
    auto.add_argument("--gpu-layers", type=int, default=DEFAULT_GPU_LAYERS)
    auto.add_argument("--no-select", action="store_true")

    download = subparsers.add_parser("download", help="Download the default candidate set.")
    benchmark = subparsers.add_parser("benchmark", help="Benchmark already-downloaded models.")
    benchmark.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    benchmark.add_argument("--port-base", type=int, default=DEFAULT_PORT_BASE)
    benchmark.add_argument("--ctx-size", type=int, default=DEFAULT_CTX_SIZE)
    benchmark.add_argument("--gpu-layers", type=int, default=DEFAULT_GPU_LAYERS)
    benchmark.add_argument("--no-select", action="store_true")

    subparsers.add_parser("list", help="List the benchmark candidates and available sizes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = args.command or "auto"
    ensure_dir(DATA_DIR)
    ensure_dir(MODELS_DIR)
    metadata = fetch_repo_metadata()
    metadata_sizes = repo_file_sizes(metadata)

    if command == "list":
        print(json.dumps(metadata_sizes, indent=2))
        return 0

    if command in {"auto", "download"}:
        ensure_default_downloads(metadata_sizes)
        if command == "download":
            return 0

    candidates = all_local_candidates(metadata_sizes)
    print("[bench] Candidates:", flush=True)
    for candidate in candidates:
        print(f"  - {candidate.name} ({candidate.source})", flush=True)

    results = benchmark_candidates(
        candidates,
        timeout_s=args.timeout,
        port_base=args.port_base,
        ctx_size=args.ctx_size,
        gpu_layers=args.gpu_layers,
    )
    winner = choose_winner(results)
    write_results(results, winner)

    if winner and not args.no_select:
        write_selection(winner)
        print(f"[bench] Selected {winner['name']} score={winner['score']}", flush=True)
    elif winner:
        print(
            f"[bench] Winner without selection write: {winner['name']} score={winner['score']}",
            flush=True,
        )
    else:
        print("[bench] No passing winner. Existing selection left unchanged.", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
