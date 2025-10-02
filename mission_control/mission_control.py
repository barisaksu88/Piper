# C:\Piper\mission_control\mission_control.py
# Sections:
#   Imports & Constants | Helpers | Snapshots | Logs Tailer | CLI Runner
#   Watchdogs | GUI | Main
# Change log:
# Mission Control v2.1 - CLI stdin, inline logs, watchdog heartbeat, async snapshot, Dev shell

# -----------------------------------------------------------------------------
# Imports & Constants
# -----------------------------------------------------------------------------
from __future__ import annotations
import os, sys, time, subprocess, threading, shutil, json

# --- spawn helper: hide console windows on Windows ---
CREATE_NO_WINDOW = 0x08000000

def popen_no_window(args, **kwargs):
    """Wrapper around subprocess.Popen that hides the console on Windows.
    Keeps all original kwargs/streams unchanged.
    """
    if sys.platform.startswith("win"):
        si = kwargs.get("startupinfo") or subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
        # shell=True tends to spawn consoles; prefer shell=False unless explicitly set
        kwargs.setdefault("shell", False)
    return subprocess.Popen(args, **kwargs)
from pathlib import Path
import dearpygui.dearpygui as dpg
import pyperclip

# S01.3 — canonical chat for Mission Control
from services import state_store
from ui.helpers.refresh_chat import build_chat_lines

# Resource Guard deps (safe fallbacks if missing)
try:
    import psutil  # system stats
except Exception:
    psutil = None

try:
    import pynvml  # NVIDIA NVML bindings (optional)
except Exception:
    pynvml = None

ROOT = Path(r"C:\Piper")
MC_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
RUN_DIR = ROOT / "run"
SNAPSHOTS = ROOT / "snapshots"
LOG_FILE = RUN_DIR / "core.log"

# -----------------------------------------------------------------------------
# Helpers  (tiny utilities, shell opener, log append/trim)
# -----------------------------------------------------------------------------
def open_dev_shell():
    # Absolute PowerShell path (avoids WindowsApps alias issues)
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    cmd = (
        "Set-Location C:\\Piper; "
        "$env:PYTHONUTF8='1'; "
        "$env:PYTHONIOENCODING='utf-8'; "
        "$env:PYTHONPATH='C:\\Piper'; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($true); "
        "chcp 65001 > $null; "
        "& C:\\Piper\\venv\\Scripts\\Activate.ps1"
    )
    # -NoExit keeps the window open; Bypass lets Activate.ps1 run
    subprocess.Popen([ps, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", cmd], creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NO_WINDOW)

def _ensure_psutil_available() -> bool:
    return psutil is not None

def _append_log(text: str):
    try:
        dpg.set_value("logs_text", dpg.get_value("logs_text") + text)
        _trim_logs()
        dpg.set_y_scroll("logs_container", dpg.get_y_scroll_max("logs_container"))
    except Exception:
        pass
    
MAX_LOG_BYTES = 500

def _trim_logs():
    try:
        txt = dpg.get_value("logs_text")
        # keep tail only
        if isinstance(txt, str) and len(txt) > MAX_LOG_BYTES:
            dpg.set_value("logs_text", txt[-MAX_LOG_BYTES:])
            dpg.set_y_scroll("logs_container", dpg.get_y_scroll_max("logs_container"))
    except Exception:
        pass

processes = {"CLI": None, "GUI": None, "WatchdogA": None, "WatchdogB": None}

# Watchdog runtime state
watchdog_status = {
    "WatchdogA": {"state": "not running", "last_change": "-"},
    "WatchdogB": {"state": "not running", "last_change": "-"},
}
# last time we saw a [MIRROR] line per watchdog (epoch seconds)
watchdog_last_mirror = {"WatchdogA": 0.0, "WatchdogB": 0.0}
WATCH_A_DEST = r"G:\\My Drive\\PiperTXT\\scripts"
WATCH_B_DEST = r"C:\\Users\\Hawk Gaming\\Dropbox\\scripts"

# --- Dev Tools helpers (UI-only; no Core mutation) ---
def _append_logline(line: str):
    """Append one normalized line to core.log and to the on-screen log."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write((line or "").rstrip("\r\n") + "\n")
    except Exception:
        pass
    # mirror to UI immediately
    try:
        _append_log((line or "").rstrip("\r\n") + "\n")
    except Exception:
        pass

def dev_emit_state_change(current: str, new_state: str):
    _append_logline(f"[STATE] {str(current or '?').upper()} -> {str(new_state or '?').upper()}")

def dev_emit_persona(tone: str, sarcasm_on: bool):
    _append_logline(f"[PERSONA] tone={tone} sarcasm={'on' if sarcasm_on else 'off'}")

def _fmt_bytes(n: float) -> str:
    # human-readable bytes
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0:
            return f"{n:0.1f} {unit}"
        n /= 1024.0
    return f"{n:0.1f} PB"

def install_resource_guard_deps():
    """Install psutil (CPU/RAM) and NVML bindings (GPU) into the venv."""
    try:
        py = ROOT / "venv" / "Scripts" / "python.exe"
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "psutil", "nvidia-ml-py3"], check=True)
        _append_log("[DEV] Installed psutil + NVML (nvidia-ml-py3) for Resource Guard\n")
    except Exception as e:
        _append_log(f"[DEV] Failed to install deps: {e}\n")

CREATE_NO_WINDOW = 0x08000000

def pid_alive(pid: int) -> bool:
    if not pid or not psutil:
        return False
    try:
        p = psutil.Process(int(pid))
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False

def kill_tree(pid: int):
    if not pid:
        return
    if psutil:
        try:
            p = psutil.Process(int(pid))
            for c in p.children(recursive=True):
                try: c.terminate()
                except Exception: pass
            try: p.terminate()
            except Exception: pass
            try:
                psutil.wait_procs([p], timeout=2.0)
            except Exception:
                pass
            if p.is_running():
                try: p.kill()
                except Exception: pass
            return
        except Exception:
            pass
    # fallback if psutil missing
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       creationflags=CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
def _gpu_snapshot():
    """
    Return a list of dicts like:
    [{"id": 0, "name": "RTX 3080", "util": 18.3, "mem_used": bytes, "mem_total": bytes}, ...]
    Uses NVML if available, otherwise calls `nvidia-smi` with CREATE_NO_WINDOW.
    """
    gpus = []
    try:
        if pynvml:
            try:
                pynvml.nvmlInit()
            finally:
                try: pynvml.nvmlShutdown()
                except Exception: pass
    except Exception:
        pass

    # Fallback: call nvidia-smi silently (no flashing window)
    try:
        cmd = ["nvidia-smi",
               "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
               "--format=csv,noheader,nounits"]
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=True,
            creationflags=CREATE_NO_WINDOW
        ).stdout.strip()
        for line in out.splitlines():
            # e.g. "0, NVIDIA GeForce RTX 3080, 12, 1024, 10024"
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                idx = int(parts[0])
                name = parts[1]
                util = float(parts[2])
                mem_used = float(parts[3]) * 1024 * 1024   # MB → bytes
                mem_total = float(parts[4]) * 1024 * 1024
                gpus.append({
                    "id": idx, "name": name, "util": util,
                    "mem_used": mem_used, "mem_total": mem_total
                })
    except Exception:
        # no NVIDIA GPU or nvidia-smi not present
        pass
    return gpus[:1]

# -----------------------------------------------------------------------------
# Snapshots  (PowerShell launcher + watcher + list_snapshots)
# -----------------------------------------------------------------------------
def _snapshot_watcher(proc: subprocess.Popen):
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            lo = line.strip()
            cur = dpg.get_value("snapshot_status")
            dpg.set_value("snapshot_status", (cur + "\n" if cur else "") + lo)
    except Exception:
        pass
    finally:
        # refresh list when done
        try:
            dpg.configure_item("snap_list", items=list_snapshots())
        except Exception:
            pass

def make_snapshot():
    """Kick off make_kgb_snapshot.ps1 without freezing the UI."""
    clip = pyperclip.paste().strip()
    dpg.set_value("snapshot_status", f"Starting snapshot… name from clipboard: '{clip or '(empty)'}'")

    ps1 = ROOT / "tools" / "make_kgb_snapshot.ps1"
    if not ps1.exists():
        dpg.set_value("snapshot_status", f"Snapshot failed: {ps1} not found")
        return

    # Prefer classic Windows PowerShell; fallback to PowerShell 7
    candidates = [
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Program Files (x86)\PowerShell\7\pwsh.exe",
        "powershell.exe",
        "pwsh.exe",
    ]
    exe = next((p for p in candidates if os.path.exists(p)), None)
    if not exe:
        dpg.set_value("snapshot_status", "Snapshot failed: PowerShell not found")
        return

    try:
        # Run hidden & non-blocking; stream output to the status box
        CREATE_NO_WINDOW = 0x08000000
        proc = popen_no_window([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=_snapshot_watcher, args=(proc,), daemon=True).start()
    except Exception as e:
        dpg.set_value("snapshot_status", f"Snapshot failed to start: {e}")

def list_snapshots():
    if not SNAPSHOTS.exists():
        return []
    snaps = sorted(
        [p.name for p in SNAPSHOTS.glob("*.zip")],
        key=lambda n: (SNAPSHOTS / n).stat().st_mtime,
        reverse=True
    )
    return snaps[:5]

# -----------------------------------------------------------------------------
# Logs Tailer  (tail_logs only)
# -----------------------------------------------------------------------------
def tail_logs():
    last_size = 0
    while True:
        try:
            if LOG_FILE.exists():
                size = LOG_FILE.stat().st_size
                if size > last_size:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_size)
                        new = f.read()
                        if new:
                            _append_log(new)
                    last_size = size
        except Exception:
            pass
        time.sleep(1)

# -----------------------------------------------------------------------------
# CLI Runner  (start_cli_bg, send_cli_command)
# -----------------------------------------------------------------------------
def start_cli_bg():
    # Launch CLI directly (hidden), accept stdin, tee stdout to core.log and GUI
    if processes["CLI"] and processes["CLI"].poll() is None:
        return processes["CLI"]

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        LOG_FILE.unlink()
    except Exception:
        pass

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT)

    py = ROOT / "venv" / "Scripts" / "python.exe"
    proc = popen_no_window([str(py), "-u", "-m", "scripts.entries.app_cli_entry"],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )
    processes["CLI"] = proc

    def _tee():
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                for line in iter(proc.stdout.readline, ""):
                    if not line:
                        break
                    f.write(line)
                    f.flush()
                  
        finally:
            processes["CLI"] = None

    threading.Thread(target=_tee, daemon=True).start()
    return proc

def send_cli_command(sender, app_data):
    line = app_data.strip()
    if not line:
        return
    proc = processes.get("CLI")
    if not proc or proc.poll() is not None:
        proc = start_cli_bg()
    try:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
    except Exception:
        pass
    dpg.set_value("cli_input", "")
    dpg.focus_item("cli_input")

# -----------------------------------------------------------------------------
# Watchdogs  (_start/_stop, reader, heartbeat, state)
# -----------------------------------------------------------------------------
def _watchdog_reader(name: str, proc: subprocess.Popen):
    tag = "wdA_status" if name == "WatchdogA" else "wdB_status"
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            lo = line.strip()
            if lo.startswith("[MIRROR]"):
                watchdog_status[name]["state"] = "currently working"
                watchdog_status[name]["last_change"] = time.strftime("%H:%M:%S")
                watchdog_last_mirror[name] = time.time()
                dpg.set_value(tag, f"WORKING (lc: {watchdog_status[name]['last_change']})")
            elif "[WATCH] Using watchdog" in lo:
                watchdog_status[name]["state"] = "standing by"
                dpg.set_value(tag, f"standby (lc: {watchdog_status[name]['last_change']})")
    except Exception:
        pass
    finally:
        watchdog_status[name]["state"] = "not running"
        watchdog_status[name]["last_change"] = "-"
        watchdog_last_mirror[name] = 0.0
        dpg.set_value(tag, "not running")
        processes[name] = None

def _start_watchdog(name: str, dest: str):
    if processes.get(name) and processes[name].poll() is None:
        return
    py = ROOT / "venv" / "Scripts" / "python.exe"
    CREATE_NO_WINDOW = 0x08000000
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = popen_no_window([str(py), "-u", "tools\mirror_py_to_txt.py", "--dest", dest],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    processes[name] = proc
    watchdog_status[name]["state"] = "starting"
    tag = "wdA_status" if name == "WatchdogA" else "wdB_status"
    dpg.set_value(tag, "starting…")
    threading.Thread(target=_watchdog_reader, args=(name, proc), daemon=True).start()

def start_watchdog_a_hidden():
    _start_watchdog("WatchdogA", WATCH_A_DEST)

def start_watchdog_b_hidden():
    _start_watchdog("WatchdogB", WATCH_B_DEST)

def _stop_watchdog(name: str):
    proc = processes.get(name)
    if proc and proc.poll() is None:
        try:
            proc.terminate(); time.sleep(0.6)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    processes[name] = None
    watchdog_status[name]["state"] = "not running"
    watchdog_status[name]["last_change"] = "-"
    tag = "wdA_status" if name == "WatchdogA" else "wdB_status"
    dpg.set_value(tag, "not running")

def stop_watchdog_a():
    _stop_watchdog("WatchdogA")

def stop_watchdog_b():
    _stop_watchdog("WatchdogB")

def _watchdog_heartbeat():
    # Flip back to "standing by" if no [MIRROR] for a short while
    while True:
        try:
            now = time.time()
            for name in ("WatchdogA", "WatchdogB"):
                proc = processes.get(name)
                if not proc or proc.poll() is not None:
                    continue
                last = watchdog_last_mirror.get(name, 0.0)
                if last and (now - last) > 2.0:
                    if watchdog_status[name]["state"] != "standing by":
                        watchdog_status[name]["state"] = "standing by"
                        tag = "wdA_status" if name == "WatchdogA" else "wdB_status"
                        dpg.set_value(tag, f"standby (lc: {watchdog_status[name]['last_change']})")
        except Exception:
            pass
        time.sleep(0.5)

# -----------------------------------------------------------------------------
# Env Overrides (UI + logic)
# -----------------------------------------------------------------------------
ENV_STORE = MC_DIR / "env_overrides.json"
env_overrides: dict[str, str] = {}

def _env_load():
    global env_overrides
    try:
        if ENV_STORE.exists():
            env_overrides = json.loads(ENV_STORE.read_text(encoding="utf-8")) or {}
        else:
            env_overrides = {}
    except Exception:
        env_overrides = {}
    # apply to current process env
    for k, v in env_overrides.items():
        os.environ[str(k)] = str(v)


def _env_save():
    try:
        ENV_STORE.write_text(json.dumps(env_overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _env_refresh_list():
    try:
        items = [f"{k} = {v}" for k, v in sorted(env_overrides.items())]
        if dpg.does_item_exist("env_list"):
            dpg.configure_item("env_list", items=items)
        # if selection disappeared, clear selection label
        if not items:
            dpg.set_value("env_selected_label", "Selected: (none)")
    except Exception:
        pass


def _env_parse_line(line: str) -> tuple[str, str] | tuple[None, None]:
    # accepts: KEY = value   |   KEY=value   |   key = "value"
    if not line:
        return (None, None)
    s = line.strip()
    if "=" not in s:
        return (None, None)
    key, val = s.split("=", 1)
    key = key.strip()
    val = val.strip()
    # strip optional quotes around value
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return (key, val)


def _env_set(key: str, val: str):
    # If user types '= default', we DELETE the override instead of keeping a 'default' row
    if val.lower() == "default" or val == "":
        env_overrides.pop(key, None)
        os.environ.pop(key, None)
    else:
        env_overrides[key] = val
        os.environ[key] = val
    _env_save()
    _env_refresh_list()


def _env_delete_selected():
    try:
        sel = dpg.get_value("env_list")  # selected item string e.g. "KEY = value"
        if not sel:
            return
        # If listbox returns the item string, parse key up to ' ='
        if isinstance(sel, str):
            s = sel
        else:
            # some DearPyGUI versions may return index; handle that
            items = dpg.get_item_configuration("env_list").get("items", [])
            if isinstance(sel, int) and 0 <= sel < len(items):
                s = items[sel]
            else:
                return
        if "=" in s:
            key = s.split("=", 1)[0].strip()
            env_overrides.pop(key, None)
            os.environ.pop(key, None)
            _env_save()
            _env_refresh_list()
            dpg.set_value("env_selected_label", "Selected: (none)")
    except Exception:
        pass


def _env_on_enter(sender, app_data):
    line = (app_data or "").strip()
    if not line:
        return
    key, val = _env_parse_line(line)
    if not key:
        _append_log("[ENV] Invalid input. Use KEY = value.")
        return
    _env_set(key, val)
    dpg.set_value("env_input", "")
    dpg.focus_item("env_input")


def _env_on_select(sender, app_data):
    try:
        if isinstance(app_data, str):
            s = app_data
        else:
            items = dpg.get_item_configuration("env_list").get("items", [])
            if isinstance(app_data, int) and 0 <= app_data < len(items):
                s = items[app_data]
            else:
                s = ""
        if s:
            dpg.set_value("env_selected_label", f"Selected: {s}")
    except Exception:
        pass

# -----------------------------------------------------------------------------
# Mission Control updaters (threads)
# -----------------------------------------------------------------------------

def _mc_text() -> str:
    try:
        state = state_store.read_all()
        lines = build_chat_lines(state)
        return "\n".join(lines)
    except Exception:
        return ""


def _mc_update_loop():
    while True:
        try:
            dpg.set_value("mc_chat_text", _mc_text())
            try:
                dpg.set_y_scroll("mc_chat_container", dpg.get_y_scroll_max("mc_chat_container"))
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(1.0)


def _rg_update_loop():
    # Prime psutil cpu_percent so next calls are instant
    try:
        if psutil:
            psutil.cpu_percent(interval=None)
    except Exception:
        pass
    while True:
        # CPU
        try:
            if psutil:
                cpu = psutil.cpu_percent(interval=None)
                dpg.set_value("rg_cpu", f"CPU: {cpu:0.1f} %")
                dpg.set_value("rg_cpu_bar", cpu / 100.0)
                dpg.configure_item("rg_cpu_bar", overlay=f"{cpu:0.1f}%")
            else:
                dpg.set_value("rg_cpu", "CPU: psutil missing")
                dpg.set_value("rg_cpu_bar", 0.0)
                dpg.configure_item("rg_cpu_bar", overlay="-")
        except Exception:
            pass
        # RAM
        try:
            if psutil:
                vm = psutil.virtual_memory()
                used = _fmt_bytes(vm.used)
                total = _fmt_bytes(vm.total)
                dpg.set_value("rg_mem", f"RAM: {used} / {total} ({vm.percent:0.1f}%)")
                dpg.set_value("rg_ram_bar", vm.percent / 100.0)
                dpg.configure_item("rg_ram_bar", overlay=f"{vm.percent:0.1f}%")
            else:
                dpg.set_value("rg_mem", "RAM: psutil missing")
                dpg.set_value("rg_ram_bar", 0.0)
                dpg.configure_item("rg_ram_bar", overlay="-")
        except Exception:
            pass
        # GPU(s)
        try:
            try:
                gpus = _gpu_snapshot()
                if not gpus:
                    dpg.set_value("rg_gpu_line", "No NVIDIA GPU detected (or NVML/nvidia-smi unavailable).")
                    dpg.set_value("rg_gpu_bar", 0.0)
                    dpg.configure_item("rg_gpu_bar", overlay="-")
                    dpg.set_value("rg_vram_line", "-")
                    dpg.set_value("rg_vram_bar", 0.0)
                    dpg.configure_item("rg_vram_bar", overlay="-")
                else:
                    g = gpus[0]  # single GPU view
                    # util
                    dpg.set_value("rg_gpu_line", f"{g['name']} - {g['util']:0.1f}%")
                    dpg.set_value("rg_gpu_bar", g['util'] / 100.0)
                    dpg.configure_item("rg_gpu_bar", overlay=f"{g['util']:0.1f}%")
                    # vram
                    if g['mem_total'] > 0:
                        vperc = (g['mem_used'] / g['mem_total']) * 100.0
                        dpg.set_value("rg_vram_line", f"{_fmt_bytes(g['mem_used'])} / {_fmt_bytes(g['mem_total'])} ({vperc:0.1f}%)")
                        dpg.set_value("rg_vram_bar", vperc / 100.0)
                        dpg.configure_item("rg_vram_bar", overlay=f"{vperc:0.1f}%")
                    else:
                        dpg.set_value("rg_vram_line", "-")
                        dpg.set_value("rg_vram_bar", 0.0)
                        dpg.configure_item("rg_vram_bar", overlay="-")
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(1.0)

# -----------------------------------------------------------------------------
# GUI  (build_gui, small layout helpers only)
# -----------------------------------------------------------------------------

def start_gui():
    """Launch GUI with a per-process theme env (no global env mutation)."""
    if processes["GUI"] and processes["GUI"].poll() is None:
        return
    pyw = ROOT / "venv" / "Scripts" / "pythonw.exe"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT)
    proc = popen_no_window([str(pyw), "-u", "-m", "scripts.entries.app_gui_entry"],
        cwd=str(ROOT),
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    processes["GUI"] = proc

def stop_gui():
    proc = processes.get("GUI")
    try:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                time.sleep(0.6)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
    finally:
        processes["GUI"] = None

def build_gui():
    with dpg.window(label="Mission Control", width=800, height=600, tag="mc_root"):
        with dpg.tab_bar():
            with dpg.tab(label="Processes"):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Start GUI", callback=start_gui)
                    dpg.add_button(label="Stop GUI", callback=stop_gui)
                    dpg.add_button(label="Open Dev Shell", callback=open_dev_shell)
                dpg.add_spacer(height=10)
                with dpg.group(horizontal=True):
# Watchdogs                    
                    # Column: Watchdog A
                    with dpg.group():
                        dpg.add_text("Watchdog A (hidden):")
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Start A (hidden)", callback=start_watchdog_a_hidden)
                            dpg.add_button(label="Stop A", callback=stop_watchdog_a)
                        dpg.add_text("not running", tag="wdA_status")

                    dpg.add_spacer(width=12)  # small gap

                    # Column: Watchdog B
                    with dpg.group():
                        dpg.add_text("Watchdog B (hidden):")
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Start B (hidden)", callback=start_watchdog_b_hidden)
                            dpg.add_button(label="Stop B", callback=stop_watchdog_b)
                        dpg.add_text("not running", tag="wdB_status")
# CLI Command Input
                dpg.add_spacer(height=10)
                dpg.add_text("CLI Command Input:")
                dpg.add_input_text(tag="cli_input", width=300, callback=send_cli_command, on_enter=True)
                dpg.focus_item("cli_input")
                dpg.add_spacer(height=8)
                dpg.add_text("Logs (live):")
                with dpg.child_window(width=385, height=100, border=True, tag="logs_container"):
                    dpg.add_text("", tag="logs_text")
                    dpg.add_spacer(height=30)
# Snapshots                   
            with dpg.tab(label="Snapshots"):
                dpg.add_text("Clipboard name will be used for snapshot.")
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Make Snapshot", callback=make_snapshot)
                    dpg.add_button(label="Open Folder", callback=lambda: subprocess.Popen(["explorer", str(SNAPSHOTS)]))
                dpg.add_text("", tag="snapshot_status")
                dpg.add_spacer(height=10)
                dpg.add_text("Recent Snapshots:")
                dpg.add_listbox(list_snapshots(), width=385, num_items=5, tag="snap_list")
# Dev Tools
            with dpg.tab(label="Dev Tools"):
                # --- Dev Input (compose & inject into logs only; no CLI send here) ---
                dpg.add_text("Dev Input (UI-only)")
                dpg.add_input_text(
                    tag="dev_input_text",
                    hint="Type a line to inject (not sent to CLI)",
                    width=360,
                )
                with dpg.group(horizontal=True):
                    def _inject_chat(sender=None, app_data=None, user_data=None):
                        txt = dpg.get_value("dev_input_text") or "demo chat line"
                        _append_logline(f"> {txt}")
                    def _inject_log(sender=None, app_data=None, user_data=None):
                        txt = dpg.get_value("dev_input_text") or "demo log line"
                        _append_logline(f"[DEV] {txt}")
                    def _inject_error(sender=None, app_data=None, user_data=None):
                        _append_logline("RuntimeError: demo error (IN05)")
                    dpg.add_button(label="Inj Chat (UI-only)", callback=_inject_chat)
                    dpg.add_button(label="Inj Log [DEV]",     callback=_inject_log)
                    dpg.add_button(label="Inj Error [DEV]",   callback=_inject_error)

                dpg.add_spacer(height=8)
                dpg.add_separator()
                dpg.add_spacer(height=8)

                # --- State (UI-only preview) ---
                dpg.add_text("State preview (UI-only)")
                dpg.add_combo(
                    items=["SLEEPING","WAKING","LISTENING","THINKING","SPEAKING"],
                    default_value="SLEEPING",
                    width=220,
                    callback=lambda s,a,u: dev_emit_state_change("?", str(a)),
                )
                # (Optional live labels if you want later)
                # dpg.add_text("State: ?", tag="dev_state_label")
                # dpg.add_text("Queued: -", tag="dev_state_queue")
                # dpg.add_text("Last update: -", tag="dev_state_age")

                dpg.add_spacer(height=8)
                dpg.add_separator()
                dpg.add_spacer(height=8)

                # --- Persona (UI-only) ---
                dpg.add_text("Persona (UI-only)")
                dpg.add_combo(
                    items=["neutral","friendly","professional","playful","serious"],
                    default_value="neutral",
                    width=220,
                    tag="dev_persona_tone",
                    callback=lambda s,a,u: dev_emit_persona(str(a), bool(dpg.get_value('dev_persona_sarcasm'))),
                )
                dpg.add_checkbox(
                    label="Sarcasm",
                    default_value=False,
                    tag="dev_persona_sarcasm",
                    callback=lambda s,a,u: dev_emit_persona(str(dpg.get_value('dev_persona_tone')), bool(a)),
                )
# Env Overrides
            with dpg.tab(label="Env"):
                dpg.add_text("Set or clear overrides for Piper environment variables.")
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag="env_input", hint="KEY = value   (ENTER to apply)", width=320, on_enter=True, callback=_env_on_enter)
                    dpg.add_button(label="Delete", callback=lambda: _env_delete_selected())
                dpg.add_spacer(height=6)
                dpg.add_text("Selected: (none)", tag="env_selected_label")
                dpg.add_listbox(items=[], width=385, num_items=8, tag="env_list", callback=_env_on_select)
# Conversation (canonical state)
            with dpg.tab(label="Convo"):
                dpg.add_text("Conversation (from canonical state)")
                with dpg.child_window(width=385, height=280, border=True, tag="mc_chat_container"):
                    dpg.add_text("", tag="mc_chat_text")

# Resource Guard
            with dpg.tab(label="Resource"):
                dpg.add_spacer(height=25)
                # CPU
                dpg.add_text("CPU: -- %", tag="rg_cpu")
                dpg.add_progress_bar(tag="rg_cpu_bar", default_value=0.0, width=385, overlay="")
                dpg.add_spacer(height=8)

                # RAM
                dpg.add_text("RAM: -- / -- (--%)", tag="rg_mem")
                dpg.add_progress_bar(tag="rg_ram_bar", default_value=0.0, width=385, overlay="")
                dpg.add_spacer(height=8)

                # GPU
                dpg.add_text("No NVIDIA GPU detected (or NVML/nvidia-smi unavailable).", tag="rg_gpu_line")
                dpg.add_progress_bar(tag="rg_gpu_bar", default_value=0.0, width=385, overlay="")
                dpg.add_spacer(height=8)

                # VRAM
                dpg.add_text("-", tag="rg_vram_line")
                dpg.add_progress_bar(tag="rg_vram_bar", default_value=0.0, width=385, overlay="")

                # start background updaters
                threading.Thread(target=_rg_update_loop, daemon=True).start()
                threading.Thread(target=_mc_update_loop, daemon=True).start()

# -----------------------------------------------------------------------------
# Main  (create_context, threads, viewport)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    dpg.create_context()
    _env_load()
    build_gui()
    _env_refresh_list()
    threading.Thread(target=tail_logs, daemon=True).start()
    dpg.create_viewport(title="Piper Mission Control", width=418, height=383)
    threading.Thread(target=_watchdog_heartbeat, daemon=True).start()
    dpg.set_primary_window("mc_root", True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()