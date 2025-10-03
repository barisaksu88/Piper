# CONTRACT — Single Config Loader
# - load_config(path=None) -> dict; defaults to C:\Piper\config\piper.toml
# - Only source of truth for runtime knobs (no envs).
# - Optional escape hatch: PIPER_CONFIG path (dev only).
# - Validates & clamps: context_size, threads, ngl, temperature, top_k, top_p.
# - No UI imports; no network; no background threads.

import os
import sys
import pathlib
from typing import Any, Dict, Tuple

try:
    import tomllib  # Py3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

DEFAULT_CONFIG_PATH = r"C:\Piper\config\piper.toml"

_NUM_CLAMPS = {
    ("model", "context_size"): (1024, 262144),
    ("model", "threads"): (1, 512),
    ("model", "ngl"): (0, 512),
    ("model", "temperature"): (0.0, 2.0),
    ("model", "top_k"): (0, 100000),
    ("model", "top_p"): (0.0, 1.0),
}

def _clamp(name: Tuple[str, str], value: Any):
    lo, hi = _NUM_CLAMPS.get(name, (None, None))
    if lo is None:
        return value
    try:
        if isinstance(value, float):
            return float(min(max(value, lo), hi))
        return int(min(max(int(value), lo), hi))
    except Exception:
        # Fallback to lower bound on parse failure
        return lo

def _ensure_dirs(cfg: Dict[str, Any]) -> None:
    log_dir = cfg.get("paths", {}).get("log_dir")
    if log_dir:
        pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)
    prompt_log = cfg.get("paths", {}).get("prompt_log")
    if prompt_log:
        pathlib.Path(prompt_log).parent.mkdir(parents=True, exist_ok=True)

def _merge_defaults(user: Dict[str, Any]) -> Dict[str, Any]:
    # Reasonable defaults if keys are missing
    cfg = {
        "model": {
            "path": r"C:\Piper\models\Hermes-3-Llama-3.1-8B-Q5_K_M.gguf",
            "context_size": 16384,
            "threads": 12,
            "ngl": 35,
            "temperature": 0.8,
            "top_k": 40,
            "top_p": 0.95,
        },
        "prompt": {
            "template": r"C:\Piper\config\hermes3_chatml.jinja",
            "reply_reserve_tokens": 1000,
        },
        "persona": {
            "background_file": r"C:\Piper\config\persona_background.md",
            "traits_file":     r"C:\Piper\config\persona_traits.ini",
        },
        "memory": {
            "mode": "running_only",
        },
        "paths": {
            "log_dir": r"C:\Piper\logs",
            "prompt_log": r"C:\Piper\logs\rendered_prompt.log",
        },
        "features": {
            "use_legacy_envs": False,
        },
        "provider": {
            # Optional: override llama.cpp runner path; if empty, assume PATH or C:\Piper\llama.cpp\llama-run
            "llamacpp_exe": r"C:\Piper\llama.cpp\llama-run",
        },
    }
    # Shallow+deep merge
    def deep_merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                deep_merge(dst[k], v)
            else:
                dst[k] = v
        return dst
    return deep_merge(cfg, user or {})

def _validate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    for section, key in _NUM_CLAMPS:
        if section in cfg and key in cfg[section]:
            cfg[section][key] = _clamp((section, key), cfg[section][key])

    # Enforce BC01 hard rule
    if cfg.get("memory", {}).get("mode") != "running_only":
        cfg.setdefault("memory", {})["mode"] = "running_only"

    return cfg

def _load_toml(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)

def _detect_path(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit
    # Escape hatch (dev only)
    dev = os.environ.get("PIPER_CONFIG")
    return dev if (dev and dev.strip()) else DEFAULT_CONFIG_PATH

def load_config(path: str | None = None) -> Dict[str, Any]:
    cfg_path = _detect_path(path)
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    raw = _load_toml(cfg_path)
    cfg = _validate(_merge_defaults(raw))
    _ensure_dirs(cfg)
    return cfg

def print_startup_summary(cfg: Dict[str, Any]) -> None:
    m = cfg["model"]; p = cfg["prompt"]; a = cfg["persona"]; f = cfg["features"]; pv = cfg.get("provider", {})
    default_exe = r"C:\Piper\llama.cpp\llama-run"
    lines = [
        "[Piper/BC01] Startup",
        f"  model.path         = {m['path']}",
        f"  context_size       = {m['context_size']}",
        f"  threads            = {m['threads']}",
        f"  ngl                = {m['ngl']}",
        f"  temperature/top_k/top_p = {m['temperature']}/{m['top_k']}/{m['top_p']}",
        f"  template           = {p['template']}",
        f"  persona.background = {a['background_file']}",
        f"  persona.traits     = {a['traits_file']}",
        f"  provider.llamacpp  = {pv.get('llamacpp_exe', default_exe)}",
        f"  memory.mode        = {cfg['memory']['mode']}",
        f"  features.legacy_envs = {f['use_legacy_envs']}",
    ]
    print("\n".join(lines))
    if f.get("use_legacy_envs", False):
        print("[WARN] Legacy env override enabled (dev only). BC01 forbids env influence when false.")
