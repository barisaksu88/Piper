# config.py
from dataclasses import dataclass, field, fields as dc_fields
import json
import sys
import os
import shutil
import fnmatch
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

# --- PATH SETUP ---
# This ensures scripts in sub-folders (like core/, tools/) can find 'config.py'
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def data_state_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "state"


def data_state_path(data_dir: Path, filename: str) -> Path:
    return data_state_dir(data_dir) / filename


def data_debug_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "debug"


def data_debug_path(data_dir: Path, filename: str) -> Path:
    return data_debug_dir(data_dir) / filename


def data_benchmarks_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "benchmarks"


def data_benchmark_results_dir(data_dir: Path) -> Path:
    return data_benchmarks_dir(data_dir) / "results"


def data_benchmark_logs_dir(data_dir: Path) -> Path:
    return data_benchmarks_dir(data_dir) / "logs"


def data_benchmark_scripts_dir(data_dir: Path) -> Path:
    return data_benchmarks_dir(data_dir) / "scripts"


def data_harness_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "harness"


def data_harness_results_dir(data_dir: Path) -> Path:
    return data_harness_dir(data_dir) / "results"


def data_harness_scripts_dir(data_dir: Path) -> Path:
    return data_harness_dir(data_dir) / "scripts"


def data_reference_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "reference"


def _first_existing_path(*candidates) -> Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _coerce_existing_path(raw_path: str) -> Path | None:
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        return None

    direct = Path(raw_path)
    if direct.exists():
        return direct

    windows_match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw_path)
    if windows_match and os.name != "nt":
        drive = windows_match.group(1).lower()
        suffix = windows_match.group(2).replace("\\", "/")
        wsl_path = Path(f"/mnt/{drive}/{suffix}")
        if wsl_path.exists():
            return wsl_path

    if os.name == "nt" and raw_path.startswith("/mnt/") and len(raw_path) > 6:
        drive = raw_path[5]
        suffix = raw_path[7:].replace("/", "\\")
        win_path = Path(f"{drive.upper()}:\\{suffix}")
        if win_path.exists():
            return win_path

    return None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _normalize_host_token(raw_value: str) -> str:
    token = str(raw_value or "").strip().lower()
    if not token:
        return ""
    probe = token if "://" in token else f"http://{token}"
    parsed = urlparse(probe)
    host = str(parsed.hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _env_host_list(name: str, default: list[str] | tuple[str, ...] | None = None) -> list[str]:
    raw = os.environ.get(name)
    source = raw if raw is not None else ",".join(default or [])
    ordered: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,\s;]+", str(source or "")):
        host = _normalize_host_token(token)
        if not host or host in seen:
            continue
        seen.add(host)
        ordered.append(host)
    return ordered


def _dedupe_paths(paths) -> list[Path]:
    ordered: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        key = str(path).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _first_matching_file(*pattern_groups) -> Path | None:
    for base_dir, patterns in pattern_groups:
        base_path = Path(base_dir)
        if not base_path.exists():
            continue
        files = sorted((path for path in base_path.iterdir() if path.is_file()), key=lambda path: path.name.lower())
        for pattern in patterns:
            pattern_l = str(pattern).lower()
            for path in files:
                if fnmatch.fnmatch(path.name.lower(), pattern_l):
                    return path
    return None


def _find_preferred_qwen_4b_model(*base_dirs) -> Path | None:
    patterns = (
        re.compile(r"qwen.*3[._-]?5.*(?:^|[^0-9])4b(?:[^0-9]|$).*\.gguf$", re.IGNORECASE),
        re.compile(r"qwen.*(?:^|[^0-9])4b(?:[^0-9]|$).*\.gguf$", re.IGNORECASE),
    )
    for base_dir in base_dirs:
        base_path = Path(base_dir)
        if not base_path.exists():
            continue
        files = sorted((path for path in base_path.iterdir() if path.is_file()), key=lambda path: path.name.lower())
        for pattern in patterns:
            for path in files:
                if pattern.search(path.name):
                    return path
    return None


def _load_selected_model_path(selection_path: Path) -> Path | None:
    if not selection_path.exists():
        return None
    try:
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    raw_path = str(payload.get("selected_model_path", "")).strip()
    if not raw_path:
        return None

    selected_path = _coerce_existing_path(raw_path)
    if selected_path and selected_path.suffix.lower() == ".gguf":
        return selected_path
    return None


def _load_selected_mmproj_path(selection_path: Path) -> Path | None:
    if not selection_path.exists():
        return None
    try:
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    raw_path = str(payload.get("selected_mmproj_path", "")).strip()
    if not raw_path:
        return None

    selected_path = _coerce_existing_path(raw_path)
    if selected_path and selected_path.suffix.lower() == ".gguf":
        return selected_path
    return None


def _default_reasoning_budget(model_path: Path) -> int:
    model_name = Path(model_path).name.lower()
    if "qwen3.5" in model_name or "qwen35" in model_name:
        return 0
    return -1


def _candidate_vscode_extension_dirs() -> list[Path]:
    candidates: list[Path] = []
    for base in (
        Path.home(),
        _coerce_existing_path(os.environ.get("USERPROFILE", "")),
    ):
        if base:
            candidates.append(Path(base) / ".vscode" / "extensions")

    if os.name != "nt":
        users_root = Path("/mnt/c/Users")
        if users_root.exists():
            for user_dir in users_root.iterdir():
                if user_dir.is_dir():
                    candidates.append(user_dir / ".vscode" / "extensions")
    return [path for path in _dedupe_paths(candidates) if path.exists()]


def _to_wsl_path_text(raw_path: str | Path) -> str:
    raw = str(raw_path or "").strip()
    if not raw:
        return ""
    if raw.startswith("/mnt/"):
        return raw.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
    if match:
        drive = match.group(1).lower()
        suffix = match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{suffix}"
    return raw.replace("\\", "/")


def _is_wsl_runtime() -> bool:
    if os.name == "nt":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in os.uname().release.lower()
    except AttributeError:
        return False


def _default_gateway_ip() -> str | None:
    route_path = Path("/proc/net/route")
    if not route_path.exists():
        return None
    try:
        for raw_line in route_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            fields = raw_line.strip().split()
            if len(fields) < 3 or fields[1] != "00000000":
                continue
            gateway_hex = fields[2].strip()
            if len(gateway_hex) != 8:
                continue
            octets = [str(int(gateway_hex[idx : idx + 2], 16)) for idx in (6, 4, 2, 0)]
            return ".".join(octets)
    except Exception:
        return None
    return None


def _resolve_windows_host_ip() -> str | None:
    override = os.environ.get("PIPER_WINDOWS_HOST_IP", "").strip()
    if override:
        return override
    if not _is_wsl_runtime():
        return None
    gateway_ip = _default_gateway_ip()
    if gateway_ip:
        return gateway_ip
    try:
        resolv_conf = Path("/etc/resolv.conf")
        if resolv_conf.exists():
            for line in resolv_conf.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.strip().split()
                if len(parts) == 2 and parts[0].lower() == "nameserver":
                    return parts[1].strip() or None
    except Exception:
        return None
    return None


def _url_targets_loopback(raw_url: str) -> bool:
    parsed = urlparse(str(raw_url or "").strip())
    return (parsed.hostname or "").strip().lower() in {"127.0.0.1", "localhost"}


def _should_bridge_wsl_windows_llama(raw_url: str, server_exe: Path | str) -> bool:
    return _is_wsl_runtime() and str(server_exe or "").strip().lower().endswith(".exe") and _url_targets_loopback(raw_url)


def _should_expose_windows_llama_bind(raw_url: str, server_exe: Path | str) -> bool:
    return str(server_exe or "").strip().lower().endswith(".exe") and _url_targets_loopback(raw_url)


def _resolve_llama_server_bind_host(raw_url: str, server_exe: Path | str) -> str:
    if _should_expose_windows_llama_bind(raw_url, server_exe):
        return "0.0.0.0"
    parsed = urlparse(str(raw_url or "").strip())
    return (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"


def _resolve_llama_server_url(raw_url: str, server_exe: Path | str) -> str:
    url_text = str(raw_url or "").strip() or "http://127.0.0.1:8080"
    if not _should_bridge_wsl_windows_llama(url_text, server_exe):
        return url_text
    host_ip = _resolve_windows_host_ip()
    if not host_ip:
        return url_text
    parsed = urlparse(url_text)
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    netloc = f"{auth}{host_ip}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _resolve_llama_model_path() -> Path:
    override = os.environ.get("PIPER_MODEL_PATH", "").strip()
    if override:
        override_path = _coerce_existing_path(override)
        if override_path and override_path.suffix.lower() == ".gguf":
            return override_path

    windows_model_dir = Path(r"C:\Piper\models\llama")
    local_model_dir = ROOT_DIR / "models" / "llama"
    selection_path = data_state_path(ROOT_DIR / "data", "model_selection.json")

    selected_model = _load_selected_model_path(selection_path)
    if selected_model is not None:
        return selected_model

    preferred_4b = _find_preferred_qwen_4b_model(local_model_dir, windows_model_dir)
    if preferred_4b is not None:
        return preferred_4b

    hardcoded_14b = Path(r"C:\Piper\models\llama\qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf")
    fallback = _first_existing_path(
        hardcoded_14b,
        _first_matching_file(
            (windows_model_dir, ("qwen2.5-14b-instruct*.gguf", "*qwen2.5*14b*.gguf", "*.gguf")),
            (local_model_dir, ("*qwen2.5*14b*.gguf", "*.gguf")),
        ),
        ROOT_DIR / "models" / "model.gguf",
    )
    return fallback or (ROOT_DIR / "models" / "model.gguf")


def _resolve_mmproj_path(model_path: Path) -> Path | None:
    override = os.environ.get("PIPER_MMPROJ_PATH", "").strip()
    if override:
        override_path = _coerce_existing_path(override)
        if override_path and override_path.suffix.lower() == ".gguf":
            return override_path

    model_path = Path(model_path)
    model_name = model_path.name.lower()
    if "qwen3.5" not in model_name:
        return None

    selection_path = data_state_path(ROOT_DIR / "data", "model_selection.json")
    selected_mmproj = _load_selected_mmproj_path(selection_path)
    if selected_mmproj is not None:
        return selected_mmproj

    size_tag = None
    if "9b" in model_name:
        size_tag = "9B"
    elif "4b" in model_name:
        size_tag = "4B"

    search_dirs = []
    if model_path.parent.exists():
        search_dirs.append(model_path.parent)

    for base_dir in search_dirs:
        files = sorted((path for path in base_dir.iterdir() if path.is_file()), key=lambda path: path.name.lower())
        preferred_patterns = []
        if size_tag is not None:
            if "bf16" in model_name:
                preferred_patterns.extend(
                    (
                        f"qwen3.5-{size_tag.lower()}.mmproj-bf16.gguf",
                        f"qwen3.5-{size_tag.lower()}.mmproj-f16.gguf",
                    )
                )
            else:
                preferred_patterns.extend(
                    (
                        f"qwen3.5-{size_tag.lower()}.mmproj-f16.gguf",
                        f"qwen3.5-{size_tag.lower()}.mmproj-bf16.gguf",
                    )
                )
            preferred_patterns.append(f"qwen3.5-{size_tag.lower()}.mmproj-f32.gguf")

        if "bf16" in model_name:
            preferred_patterns.append("mmproj-bf16.gguf")
        if "f16" in model_name:
            preferred_patterns.append("mmproj-f16.gguf")
        preferred_patterns.extend(("mmproj-f16.gguf", "mmproj-bf16.gguf", "mmproj-f32.gguf", "mmproj*.gguf"))

        seen = set()
        for pattern in preferred_patterns:
            pattern_l = pattern.lower()
            if pattern_l in seen:
                continue
            seen.add(pattern_l)
            for path in files:
                if fnmatch.fnmatch(path.name.lower(), pattern_l):
                    return path
    return None

@dataclass(frozen=True)
class Config:
    # Where Piper Core lives
    ROOT_DIR: Path = ROOT_DIR  # Use the global variable we defined above
    
    # Memory/logging
    DATA_DIR: Path = ROOT_DIR / "data"
    MEMORY_PATH: Path = DATA_DIR / "state" / "memory.jsonl"

    @property
    def STATE_DIR(self) -> Path:
        return data_state_dir(self.DATA_DIR)

    @property
    def DEBUG_DIR(self) -> Path:
        return data_debug_dir(self.DATA_DIR)

    @property
    def BENCHMARKS_DIR(self) -> Path:
        return data_benchmarks_dir(self.DATA_DIR)

    @property
    def BENCHMARK_RESULTS_DIR(self) -> Path:
        return data_benchmark_results_dir(self.DATA_DIR)

    @property
    def BENCHMARK_LOGS_DIR(self) -> Path:
        return data_benchmark_logs_dir(self.DATA_DIR)

    @property
    def BENCHMARK_SCRIPTS_DIR(self) -> Path:
        return data_benchmark_scripts_dir(self.DATA_DIR)

    @property
    def HARNESS_DIR(self) -> Path:
        return data_harness_dir(self.DATA_DIR)

    @property
    def HARNESS_RESULTS_DIR(self) -> Path:
        return data_harness_results_dir(self.DATA_DIR)

    @property
    def HARNESS_SCRIPTS_DIR(self) -> Path:
        return data_harness_scripts_dir(self.DATA_DIR)

    @property
    def REFERENCE_DIR(self) -> Path:
        return data_reference_dir(self.DATA_DIR)

    @property
    def PROMPTS_DIR(self) -> Path:
        return self.DATA_DIR / "prompts"

    @property
    def STYLES_DIR(self) -> Path:
        return self.DATA_DIR / "styles"

    @property
    def SFX_DIR(self) -> Path:
        return self.DATA_DIR / "sfx"

    @property
    def TEMPLATES_DIR(self) -> Path:
        return self.DATA_DIR / "templates"

    @property
    def VECTOR_STORE_DIR(self) -> Path:
        return self.DATA_DIR / "vector_store"

    @property
    def WORKSPACE_DIR(self) -> Path:
        return self.DATA_DIR / "workspace"

    @property
    def USERS_PATH(self) -> Path:
        return self.DATA_DIR / "users.json"

    @property
    def USER_SILOS_DIR(self) -> Path:
        return self.DATA_DIR / "users"

    @property
    def TASKS_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "tasks.json")

    @property
    def EVENTS_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "events.json")

    @property
    def KNOWLEDGE_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "knowledge.json")

    @property
    def WORLD_MODEL_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "world_model.json")

    @property
    def SITUATIONAL_STATE_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "situational_state.json")

    @property
    def INTENT_STATE_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "intent_state.json")

    @property
    def INGESTED_DOCUMENTS_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "ingested_documents.json")

    @property
    def MODEL_SELECTION_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "model_selection.json")

    @property
    def CONVERSATION_SUMMARY_PATH(self) -> Path:
        return self.DATA_DIR / "conversation_summary.json"

    @property
    def STATS_PATH(self) -> Path:
        return self.DATA_DIR / "stats.jsonl"

    @property
    def STATS_ALERTS_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "stats_alerts.log")

    @property
    def CHANGE_JOURNAL_PATH(self) -> Path:
        return self.DATA_DIR / "change_journal.json"

    @property
    def REMINDERS_PATH(self) -> Path:
        return self.DATA_DIR / "reminders.json"

    @property
    def LLM_PROMPT_DEBUG_PATH(self) -> Path:
        """Legacy combined log — kept for backward compat; prefer layer paths below."""
        return data_debug_path(self.DATA_DIR, "llm_prompt_debug.txt")

    # ── Per-layer debug paths ──────────────────────────────────────────────
    @property
    def ROUTER_DEBUG_PATH(self) -> Path:
        """SECRETARY / routing decisions."""
        return data_debug_path(self.DATA_DIR, "router_debug.txt")

    @property
    def PERSONA_DEBUG_PATH(self) -> Path:
        """PERSONA and PERSONA_RECALL_* turns."""
        return data_debug_path(self.DATA_DIR, "persona_debug.txt")

    @property
    def PLANNER_DEBUG_PATH(self) -> Path:
        """STAGE_*_STEP_* planner steps."""
        return data_debug_path(self.DATA_DIR, "planner_debug.txt")

    @property
    def DOC_FOCUS_DEBUG_PATH(self) -> Path:
        """DOCUMENT_FOCUS and doc-vision calls."""
        return data_debug_path(self.DATA_DIR, "doc_focus_debug.txt")

    @property
    def LLM_HTTP_PAYLOAD_DEBUG_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "llm_http_payload_debug.txt")

    @property
    def MANAGER_DEBUG_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "manager_debug.txt")

    @property
    def TTS_DEBUG_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "tts_debug.txt")

    @property
    def LANGGRAPH_TRACE_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "langgraph_trace.jsonl")

    @property
    def LANGGRAPH_CHECKPOINT_PATH(self) -> Path:
        raw_path = os.environ.get("PIPER_LANGGRAPH_CHECKPOINT_PATH", "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            return path if path.is_absolute() else self.ROOT_DIR / path
        return data_state_path(self.DATA_DIR, "langgraph_checkpoints.sqlite")

    @property
    def LANGGRAPH_RECOVERY_PATH(self) -> Path:
        raw_path = os.environ.get("PIPER_LANGGRAPH_RECOVERY_PATH", "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            return path if path.is_absolute() else self.ROOT_DIR / path
        return data_state_path(self.DATA_DIR, "langgraph_recovery.json")

    @property
    def LANGGRAPH_INTERRUPT_PATH(self) -> Path:
        raw_path = os.environ.get("PIPER_LANGGRAPH_INTERRUPT_PATH", "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            return path if path.is_absolute() else self.ROOT_DIR / path
        return data_state_path(self.DATA_DIR, "langgraph_interrupt.json")

    @property
    def COMFY_OUTPUT_DIR(self) -> Path:
        return self.COMFY_DIR / "ComfyUI" / "output"

    @property
    def MODEL_BENCHMARKS_PATH(self) -> Path:
        return self.BENCHMARK_RESULTS_DIR / "model_benchmarks.json"

    @property
    def MODEL_COMPARE_FAIR_PATH(self) -> Path:
        return self.BENCHMARK_RESULTS_DIR / "model_compare_fair.json"

    # ---------------------------------------------------------------------
    # LLM Backends
    # ---------------------------------------------------------------------

    # Preferred backend (current): llama.cpp server (OpenAI-compatible HTTP)
    # You start the server separately (llama-server.exe) and Piper connects to it.
    _raw_llama_server_url: str = os.environ.get("PIPER_LLAMA_SERVER_URL", "http://127.0.0.1:8080").strip() or "http://127.0.0.1:8080"
    LLAMA_SERVER_MODEL: str = "qwen"
    LLAMA_SERVER_TIMEOUT_S: float = 300.0
    LLAMA_SERVER_STREAM_READ_TIMEOUT_S: float = float(os.environ.get("PIPER_LLM_STREAM_READ_TIMEOUT_S", "30"))
    LLAMA_SERVER_HEALTH_TIMEOUT_S: float = float(os.environ.get("PIPER_LLM_HEALTH_TIMEOUT_S", "120"))
    LLAMA_SERVER_GPU_LAYERS: int = int(os.environ.get("PIPER_LLM_GPU_LAYERS", "99"))
    LLAMA_SERVER_CTX_SIZE: int = int(os.environ.get("PIPER_LLM_CTX_SIZE", "8192"))
    ROUTER_MAX_TOKENS: int = int(os.environ.get("PIPER_ROUTER_MAX_TOKENS", "400"))
    ROUTE_CLARIFIER_MAX_TOKENS: int = int(os.environ.get("PIPER_ROUTE_CLARIFIER_MAX_TOKENS", "120"))
    FOLLOWUP_RESOLUTION_MAX_TOKENS: int = int(os.environ.get("PIPER_FOLLOWUP_RESOLUTION_MAX_TOKENS", "220"))
    PLANNER_MAX_TOKENS: int = int(os.environ.get("PIPER_PLANNER_MAX_TOKENS", "700"))
    INSPECTOR_MAX_TOKENS: int = int(os.environ.get("PIPER_INSPECTOR_MAX_TOKENS", "120"))
    FILE_CHECKER_MAX_TOKENS: int = int(os.environ.get("PIPER_FILE_CHECKER_MAX_TOKENS", "220"))
    REPORTER_MAX_TOKENS: int = int(os.environ.get("PIPER_REPORTER_MAX_TOKENS", "700"))
    PERSONA_MAX_TOKENS: int = int(os.environ.get("PIPER_PERSONA_MAX_TOKENS", "700"))
    CONVERSATION_SUMMARY_MAX_TOKENS: int = int(os.environ.get("PIPER_CONVERSATION_SUMMARY_MAX_TOKENS", "500"))
    EXECUTOR_MAX_STEPS: int = int(os.environ.get("PIPER_EXECUTOR_MAX_STEPS", "12"))
    EXECUTOR_MAX_STAGE_RUNTIME_S: float = float(os.environ.get("PIPER_EXECUTOR_MAX_STAGE_RUNTIME_S", "120"))
    EXECUTOR_MAX_ACTIONS_PER_STAGE: int = int(os.environ.get("PIPER_EXECUTOR_MAX_ACTIONS_PER_STAGE", "15"))
    SKILL_LAYER_ENABLED: bool = _env_flag("PIPER_SKILL_LAYER_ENABLED", True)
    LANGGRAPH_RUNTIME_ENABLED: bool = _env_flag("PIPER_LANGGRAPH_RUNTIME_ENABLED", False)
    USE_LANGGRAPH_ORCHESTRATOR: bool = _env_flag("PIPER_USE_LANGGRAPH_ORCHESTRATOR", True)
    LANGGRAPH_CHECKPOINT_MODE: str = (
        os.environ.get("PIPER_LANGGRAPH_CHECKPOINT_MODE", "sqlite").strip().lower() or "sqlite"
    )
    VOICE_RECOGNITION_ENABLED: bool = _env_flag("PIPER_VOICE_RECOGNITION_ENABLED", True)
    VOICE_SIMILARITY_THRESHOLD_HIGH: float = float(os.environ.get("PIPER_VOICE_SIMILARITY_THRESHOLD_HIGH", "0.74"))
    VOICE_SIMILARITY_THRESHOLD_LOW: float = float(os.environ.get("PIPER_VOICE_SIMILARITY_THRESHOLD_LOW", "0.58"))
    VOICE_FIRST_TURN_INFER_THRESHOLD: float = float(os.environ.get("PIPER_VOICE_FIRST_TURN_INFER_THRESHOLD", "0.74"))
    VOICE_ENROLLMENT_TURNS: int = int(os.environ.get("PIPER_VOICE_ENROLLMENT_TURNS", "5"))
    VOICE_ADMIN_ENROLLMENT_TURNS: int = int(os.environ.get("PIPER_VOICE_ADMIN_ENROLLMENT_TURNS", "10"))
    VOICE_ADMIN_SIMILARITY_THRESHOLD: float = float(os.environ.get("PIPER_VOICE_ADMIN_SIMILARITY_THRESHOLD", "0.70"))
    VOICE_ADMIN_MARGIN_THRESHOLD: float = float(os.environ.get("PIPER_VOICE_ADMIN_MARGIN_THRESHOLD", "0.08"))
    VOICE_PUBLIC_MARGIN_THRESHOLD: float = float(os.environ.get("PIPER_VOICE_PUBLIC_MARGIN_THRESHOLD", "0.08"))
    VOICE_DRIFT_CONFIRMATION_TURNS: int = int(os.environ.get("PIPER_VOICE_DRIFT_CONFIRMATION_TURNS", "3"))
    VOICE_LOW_CONFIDENCE_ASK_AFTER: int = int(os.environ.get("PIPER_VOICE_LOW_CONFIDENCE_ASK_AFTER", "3"))
    COMPUTER_USE_ENABLED: bool = _env_flag("PIPER_COMPUTER_USE_ENABLED", True)
    COMPUTER_USE_HTTP_ENABLED: bool = _env_flag("PIPER_COMPUTER_USE_HTTP_ENABLED", True)
    COMPUTER_USE_ALLOWED_HTTP_DOMAINS: list[str] = field(
        default_factory=lambda: _env_host_list(
            "PIPER_COMPUTER_USE_ALLOWED_HTTP_DOMAINS",
            ["example.com", "iana.org", "apache.org", "w3.org", "python.org", "rfc-editor.org", "localhost", "127.0.0.1"],
        )
    )
    LOG_LEVEL: str = os.environ.get("PIPER_LOG_LEVEL", "INFO").upper()
    DEBUG_LLM_HTTP_PAYLOADS: bool = _env_flag("PIPER_DEBUG_LLM_HTTP_PAYLOADS", False)
    # Prompt debug is light enough to keep on by default; full HTTP payload dumps stay opt-in.
    DEBUG_LLM_PROMPTS: bool = _env_flag("PIPER_DEBUG_LLM_PROMPTS", True)
    DEBUG_MANAGER_PROMPTS: bool = _env_flag("PIPER_DEBUG_MANAGER_PROMPTS", False)
    DEBUG_LANGGRAPH_TRACE: bool = _env_flag("PIPER_DEBUG_LANGGRAPH_TRACE", True)
    DEBUG_LANGGRAPH_VISUALIZE: bool = _env_flag("PIPER_DEBUG_LANGGRAPH_VISUALIZE", False)
    # Stream pipeline trace: prints [PIPE-IN], [FILTER-OUT], [QUEUE-PUT] per token.
    # Disable in production — enable only when debugging streaming regressions.
    DEBUG_STREAMING_PIPELINE: bool = _env_flag("PIPER_DEBUG_STREAMING_PIPELINE", False)
    LANGGRAPH_TRACE_HISTORY_LIMIT: int = int(os.environ.get("PIPER_LANGGRAPH_TRACE_HISTORY_LIMIT", "500"))
    LANGGRAPH_CHECKPOINT_HISTORY_LIMIT: int = int(os.environ.get("PIPER_LANGGRAPH_CHECKPOINT_HISTORY_LIMIT", "500"))
    MODELS_DIR = ROOT_DIR / "models"

    # --- PATH RESOLUTION (Safe & Simple) ---

    # 1. Llama Server EXE
    # Allow env override so newer llama.cpp builds can be tested safely before
    # they become the default runtime.
    _env_llama = os.environ.get("PIPER_LLAMA_SERVER_EXE", "").strip()
    _hard_llama = r"F:\llama.cpp\llama-server.exe"
    _local_newer_llama = ROOT_DIR / "runtime" / "llama.cpp" / "llama-server.exe"
    _env_llama_path = _coerce_existing_path(_env_llama) if _env_llama else None
    if _env_llama_path:
        LLAMA_SERVER_EXE = _env_llama_path
    elif _local_newer_llama.exists():
        LLAMA_SERVER_EXE = _local_newer_llama
    elif os.path.exists(_hard_llama):
        LLAMA_SERVER_EXE = Path(_hard_llama)
    else:
        LLAMA_SERVER_EXE = ROOT_DIR / "llama-server.exe"

    # 2. Model Path
    # Prefer a valid Qwen 3.5 4B GGUF when present. Otherwise stay on the
    # working Qwen 2.5 14B model. `PIPER_MODEL_PATH` can override both.
    MODEL_PATH = _resolve_llama_model_path()
    MMPROJ_PATH = _resolve_mmproj_path(MODEL_PATH)
    LLAMA_SERVER_BIND_HOST: str = _resolve_llama_server_bind_host(_raw_llama_server_url, LLAMA_SERVER_EXE)
    LLAMA_SERVER_URL: str = _resolve_llama_server_url(_raw_llama_server_url, LLAMA_SERVER_EXE)
    LLAMA_SERVER_REASONING_BUDGET: int = int(
        os.environ.get(
            "PIPER_LLM_REASONING_BUDGET",
            str(_default_reasoning_budget(MODEL_PATH)),
        )
    )

    # 3. ComfyUI
    # Check if the hardcoded path exists, otherwise look locally
    _hard_comfy = r"F:\ComfyUI_windows_portable"
    if os.path.exists(_hard_comfy):
        COMFY_DIR = Path(_hard_comfy)
    else:
        COMFY_DIR = ROOT_DIR / "ComfyUI"
    
    # -----------------------------------------------

    # Generation defaults
    TEMPERATURE: float = 0.7
    MAX_TOKENS: int = 2048

    # Context window
    CONTEXT_SIZE: int = LLAMA_SERVER_CTX_SIZE

    # NEW: Reduced to prevent Context Drift
    MODEL_MAX_TURNS: int = 10

    # Optional: CPU threads for llama-run (0 lets it decide)
    THREADS: int = 0

    # Instruction file (static system prompt)
    INSTRUCTIONS_PATH: Path = DATA_DIR / "prompts" / "instructions.txt"

    # Active character style file (in DATA_DIR/styles). Change at runtime via /style.
    ACTIVE_STYLE_FILE: str = "default.style"

    # ---------------------------------------------------------------------
    # TTS (Kokoro ONNX)
    # ---------------------------------------------------------------------

    TTS_ENABLED: bool = True
    TTS_BACKEND: str = "auto"
    TTS_VOICE: str = "af_heart"
    TTS_SPEED: float = 0.85
    TTS_KOKORO_TIMEOUT_S: float = 8.0
    TTS_KOKORO_TORCH_READY_WAIT_S: float = 2.0
    TTS_KOKORO_HF_REPO_ID: str = "hexgrad/Kokoro-82M"
    KOKORO_TORCH_SUBDIR: str = "torch"
    KOKORO_TORCH_MODEL: str = "kokoro-v1_0.pth"
    KOKORO_TORCH_CONFIG: str = "config.json"
    BOOT_SCREEN_MIN_VISIBLE_S: float = 0.75
    TTS_LANG: str = "en-us"
    LIVE_SCREEN_INTERVAL_S: float = 10.0
    LIVE_SCREEN_FILENAME: str = "live_screen.jpg"
    LIVE_SCREEN_FOCUS_FILENAME: str = "live_focus.jpg"
    LIVE_SCREEN_SOURCE_MODE: str = "display"
    LIVE_SCREEN_MAX_STALE_S: float = 30.0
    SCREEN_CAPTURE_MAX_DIM: int = 1920
    SCREEN_POINTER_FOCUS_WIDTH: int = 1400
    SCREEN_POINTER_FOCUS_HEIGHT: int = 900

    _hard_kokoro = r"C:\Piper\models\kokoro"
    if os.path.exists(_hard_kokoro):
        KOKORO_DIR = Path(_hard_kokoro)
    else:
        KOKORO_DIR = ROOT_DIR / "models" / "kokoro"
    KOKORO_MODEL: str = "kokoro-v1.0.onnx"
    KOKORO_VOICES: str = "voices-v1.0.bin"

    # ---------------------------------------------------------------------
    # Web Search (DuckDuckGo + Jina.ai Reader)
    # ---------------------------------------------------------------------

    SEARCH_BACKEND: str = "searxng"
    SEARXNG_URL: str = "http://127.0.0.1:8888"
    SEARXNG_TIMEOUT_S: float = 10.0
    SEARXNG_AUTO_START: bool = True
    SEARXNG_STOP_ON_EXIT: bool = True
    SEARXNG_DOCKER_CONTAINER: str = "piper-searxng"
    SEARXNG_DOCKER_IMAGE: str = "searxng/searxng:latest"
    SEARXNG_DOCKER_HOST_PORT: int = 8888
    SEARXNG_DOCKER_CONTAINER_PORT: int = 8080
    SEARXNG_DOCKER_CONFIG_DIR: str = ".local/searxng"
    SEARXNG_REQUIRE: bool = False
    SEARCH_BLACKLIST: list[str] = field(default_factory=lambda: ["zhihu.com", "baidu.com", "weibo.com"])
    SEARCH_URL_FETCH_TIMEOUT_S: float = 20.0
    SEARCH_MIN_CONTENT_LENGTH: int = 100
    SEARCH_MAX_RESULTS: int = 8
    SEARCH_SNIPPETS_LIMIT: int = 3
    SEARCH_DEEP_DIVE_LINKS_LIMIT: int = 6
    SEARCH_CONTENT_SLICE_LENGTH: int = 1500

    # -----------------------------------------------------------------
    # Web UI bridge (opt-in; DearPyGui remains the default)
    # -----------------------------------------------------------------
    WEB_UI_ENABLED: bool = field(
        default_factory=lambda: _env_flag("PIPER_WEB_UI_ENABLED", False)
    )
    WEB_UI_HOST: str = field(
        default_factory=lambda: os.environ.get("PIPER_WEB_UI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    )
    WEB_UI_PORT: int = field(
        default_factory=lambda: int(os.environ.get("PIPER_WEB_UI_PORT", "8787"))
    )
    WEB_UI_WS_PATH: str = field(
        default_factory=lambda: os.environ.get("PIPER_WEB_UI_WS_PATH", "/ws").strip() or "/ws"
    )
    WEB_MIC_MAX_DECODED_BYTES: int = field(
        default_factory=lambda: int(os.environ.get("PIPER_WEB_MIC_MAX_DECODED_BYTES", "10485760"))  # 10 MiB
    )
    WEB_MIC_MAX_SECONDS: int = field(
        default_factory=lambda: int(os.environ.get("PIPER_WEB_MIC_MAX_SECONDS", "60"))
    )
    WEB_MIC_FFMPEG_TIMEOUT_S: int = field(
        default_factory=lambda: int(os.environ.get("PIPER_WEB_MIC_FFMPEG_TIMEOUT_S", "30"))
    )
    WEB_UI_MAX_WS_MESSAGE_BYTES: int = field(
        default_factory=lambda: int(os.environ.get("PIPER_WEB_UI_MAX_WS_MESSAGE_BYTES", str(20 * 1024 * 1024)))
    )
    WEB_UI_FRONTEND_DIST_DIR: Path = field(
        default_factory=lambda: Path(
            os.environ.get("PIPER_WEB_UI_FRONTEND_DIST_DIR", str(ROOT_DIR / "web_ui" / "frontend" / "dist"))
        )
    )
    WEB_UI_WINDOW: bool = field(
        default_factory=lambda: _env_flag("PIPER_WEB_UI_WINDOW", False)
    )


class LiveConfig:
    """Mutable config holder wrapping a frozen Config.

    Drop-in replacement: ``CFG.field_name`` still works via ``__getattr__``.
    """

    def __init__(self, initial: Config) -> None:
        self._data: dict[str, Any] = self._public_values(initial)
        self._config_class = type(initial)
        self._listeners: list[Callable[[list[str]], None]] = []
        self._override_path: Path = data_state_path(
            Path(self._data.get("DATA_DIR", ROOT_DIR / "data")),
            "config_override.json",
        )
        self._override_mtime: float = 0.0

    @staticmethod
    def _public_values(source: Config) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for f in dc_fields(source):
            if not f.name.startswith("_"):
                values[f.name] = getattr(source, f.name)
        for name, value in vars(type(source)).items():
            if name.startswith("_") or name in values:
                continue
            if isinstance(value, property) or callable(value):
                continue
            values[name] = getattr(source, name)
        return values

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._data:
            return self._data[name]
        class_value = getattr(self._config_class, name, None)
        if class_value is not None and not isinstance(class_value, property) and not callable(class_value):
            return class_value
        prop = getattr(self._config_class, name, None)
        if isinstance(prop, property):
            return prop.fget(self)
        raise AttributeError(f"Config has no field '{name}'")

    @staticmethod
    def _coerce_override_value(current: Any, new_value: Any) -> Any:
        if isinstance(current, Path) and isinstance(new_value, str):
            return Path(new_value)
        return new_value

    def update(self, overrides: dict[str, Any]) -> list[str]:
        """Apply overrides and return the list of changed keys."""
        changed: list[str] = []
        for key, value in overrides.items():
            if key.startswith("_"):
                continue
            if key not in self._data:
                continue
            coerced = self._coerce_override_value(self._data[key], value)
            if self._data[key] != coerced:
                self._data[key] = coerced
                changed.append(key)
        if changed:
            self._notify(changed)
        return changed

    def on_change(self, listener: Callable[[list[str]], None]) -> None:
        self._listeners.append(listener)

    def _notify(self, changed: list[str]) -> None:
        for fn in self._listeners:
            try:
                fn(changed)
            except Exception:
                pass

    def reload_if_stale(self) -> list[str]:
        """Reload `data/state/config_override.json` if its mtime changed."""
        if not self._override_path.exists():
            if self._override_mtime > 0:
                self._override_mtime = 0.0
                return self._revert_overrides()
            return []
        try:
            stat_result = self._override_path.stat()
        except OSError:
            return []
        current_mtime = stat_result.st_mtime
        if current_mtime == self._override_mtime:
            return []
        self._override_mtime = current_mtime
        if stat_result.st_size > 10240:
            return []
        try:
            payload = json.loads(self._override_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, dict):
            return []
        # config_override.json intentionally accepts only scalar-style overrides.
        restart_only = {"ROOT_DIR", "DATA_DIR", "MEMORY_PATH", "LLAMA_SERVER_REASONING_BUDGET"}
        safe_overrides = {
            key: value
            for key, value in payload.items()
            if key not in restart_only
            and not key.startswith("_")
            and not isinstance(value, (dict, list))
        }
        return self.update(safe_overrides)

    def _revert_overrides(self) -> list[str]:
        """Revert overridden fields back to the current process defaults."""
        defaults = Config()
        default_values = self._public_values(defaults)
        changed: list[str] = []
        for name, default_value in default_values.items():
            if name in self._data and self._data[name] != default_value:
                self._data[name] = default_value
                changed.append(name)
        if changed:
            self._notify(changed)
        return changed


CFG = LiveConfig(Config())
