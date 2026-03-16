# config.py
from dataclasses import dataclass, field
import json
import sys
import os
import shutil
import fnmatch
import re
from pathlib import Path

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


def _resolve_codex_executable() -> str:
    override = os.environ.get("PIPER_CODEX_EXECUTABLE", "").strip()
    if override:
        override_path = _coerce_existing_path(override)
        if override_path and override_path.exists():
            return str(override_path)
        override_which = shutil.which(override)
        if override_which:
            return override_which

    binary_names = ["codex.exe", "codex.cmd", "codex"] if os.name == "nt" else ["codex"]
    for name in binary_names:
        resolved = shutil.which(name)
        if resolved:
            return resolved

    platform_patterns = (
        ("openai.chatgpt-*/bin/windows-x86_64/codex.exe",) if os.name == "nt"
        else ("openai.chatgpt-*/bin/linux-x86_64/codex",)
    )
    for ext_dir in _candidate_vscode_extension_dirs():
        for pattern in platform_patterns:
            matches = sorted(ext_dir.glob(pattern), reverse=True)
            if matches:
                return str(matches[0])

    return override or "codex"


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


def _resolve_codex_wsl_executable() -> str:
    override = os.environ.get("PIPER_CODEX_WSL_EXECUTABLE", "").strip()
    if override:
        override_path = _coerce_existing_path(override)
        if override_path and override_path.exists():
            return _to_wsl_path_text(override_path)
        if override.startswith("/mnt/"):
            return override.replace("\\", "/")

    if os.name != "nt":
        resolved = shutil.which("codex")
        if resolved:
            return resolved

    for ext_dir in _candidate_vscode_extension_dirs():
        matches = sorted(ext_dir.glob("openai.chatgpt-*/bin/linux-x86_64/codex"), reverse=True)
        if matches:
            return _to_wsl_path_text(matches[0])

    return ""


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
    def LLM_PROMPT_DEBUG_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "llm_prompt_debug.txt")

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
    def CODEX_ESCALATION_LOG_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "codex_escalations.jsonl")

    @property
    def CODEX_REPAIR_REQUEST_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "codex_repair_request.json")

    @property
    def CODEX_REPAIR_STATUS_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "codex_repair_status.json")

    @property
    def CODEX_REPAIR_RECOVERY_PATH(self) -> Path:
        return data_state_path(self.DATA_DIR, "codex_recovery.json")

    @property
    def CODEX_REPAIR_WORKER_LOG_PATH(self) -> Path:
        return data_debug_path(self.DATA_DIR, "codex_repair_worker.log")

    @property
    def CODEX_REPAIR_OUTPUT_SCHEMA_PATH(self) -> Path:
        return self.REFERENCE_DIR / "codex_repair_output.schema.json"

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
    LLAMA_SERVER_URL: str = os.environ.get("PIPER_LLAMA_SERVER_URL", "http://127.0.0.1:8080").strip() or "http://127.0.0.1:8080"
    LLAMA_SERVER_MODEL: str = "qwen"
    LLAMA_SERVER_TIMEOUT_S: float = 300.0
    LLAMA_SERVER_STREAM_READ_TIMEOUT_S: float = float(os.environ.get("PIPER_LLM_STREAM_READ_TIMEOUT_S", "30"))
    LLAMA_SERVER_HEALTH_TIMEOUT_S: float = float(os.environ.get("PIPER_LLM_HEALTH_TIMEOUT_S", "120"))
    LLAMA_SERVER_GPU_LAYERS: int = int(os.environ.get("PIPER_LLM_GPU_LAYERS", "99"))
    LLAMA_SERVER_CTX_SIZE: int = int(os.environ.get("PIPER_LLM_CTX_SIZE", "8192"))
    EXECUTOR_MAX_STEPS: int = int(os.environ.get("PIPER_EXECUTOR_MAX_STEPS", "12"))
    SKILL_LAYER_ENABLED: bool = _env_flag("PIPER_SKILL_LAYER_ENABLED", True)
    DEBUG_LLM_HTTP_PAYLOADS: bool = _env_flag("PIPER_DEBUG_LLM_HTTP_PAYLOADS", False)
    # Prompt debug is light enough to keep on by default; full HTTP payload dumps stay opt-in.
    DEBUG_LLM_PROMPTS: bool = _env_flag("PIPER_DEBUG_LLM_PROMPTS", True)
    DEBUG_MANAGER_PROMPTS: bool = _env_flag("PIPER_DEBUG_MANAGER_PROMPTS", False)
    # Stream pipeline trace: prints [PIPE-IN], [FILTER-OUT], [QUEUE-PUT] per token.
    # Disable in production — enable only when debugging streaming regressions.
    DEBUG_STREAMING_PIPELINE: bool = _env_flag("PIPER_DEBUG_STREAMING_PIPELINE", False)
    CODEX_AUTO_REPAIR_ENABLED: bool = _env_flag("PIPER_CODEX_AUTO_REPAIR_ENABLED", True)
    CODEX_PREFER_WSL: bool = _env_flag("PIPER_CODEX_PREFER_WSL", os.name == "nt")
    CODEX_BOOT_PROBE_ENABLED: bool = _env_flag("PIPER_CODEX_BOOT_PROBE_ENABLED", True)
    CODEX_BOOT_PROBE_TIMEOUT_S: float = float(os.environ.get("PIPER_CODEX_BOOT_PROBE_TIMEOUT_S", "120"))
    CODEX_REPAIR_POLL_INTERVAL_S: float = float(os.environ.get("PIPER_CODEX_REPAIR_POLL_INTERVAL_S", "1.0"))
    CODEX_REPAIR_TIMEOUT_S: float = float(os.environ.get("PIPER_CODEX_REPAIR_TIMEOUT_S", "1800"))
    CODEX_EXECUTABLE: str = _resolve_codex_executable()
    CODEX_WSL_EXECUTABLE: str = _resolve_codex_wsl_executable()
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
    TTS_VOICE: str = "af_heart"
    TTS_SPEED: float = 0.85
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

    SEARCH_BLACKLIST: list[str] = field(default_factory=lambda: ["zhihu.com", "baidu.com", "weibo.com"])
    SEARCH_URL_FETCH_TIMEOUT_S: float = 20.0
    SEARCH_MIN_CONTENT_LENGTH: int = 100
    SEARCH_MAX_RESULTS: int = 8
    SEARCH_SNIPPETS_LIMIT: int = 3
    SEARCH_DEEP_DIVE_LINKS_LIMIT: int = 6
    SEARCH_CONTENT_SLICE_LENGTH: int = 1500


CFG = Config()
