# C:\Piper\mission_control\mission_control.py
# Mission Control v2.1 — CLI wired directly (hidden process), logs inline below CLI

from __future__ import annotations
import os, sys, time, subprocess, threading
from pathlib import Path
import dearpygui.dearpygui as dpg
import pyperclip

ROOT = Path(r"C:\Piper")
MC_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
RUN_DIR = ROOT / "run"
SNAPSHOTS = ROOT / "snapshots"
LOG_FILE = RUN_DIR / "core.log"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def open_dev_shell():
    # Absolute PowerShell path (avoids WindowsApps alias issues)
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
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
    subprocess.Popen(
        [ps, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )

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

# -----------------------------------------------------------------------------
# Snapshot (non-blocking)
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
        proc = subprocess.Popen(
            [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
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
# Logs tailer
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
# CLI background runner (hidden, accepts stdin, tees to core.log)
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
    proc = subprocess.Popen(
        [str(py), "-u", "-m", "scripts.entries.app_cli_entry"],
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
# Watchdogs
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
    proc = subprocess.Popen(
        [str(py), "-u", "tools\\mirror_py_to_txt.py", "--dest", dest],
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
# GUI
# -----------------------------------------------------------------------------
def start_gui_direct():
    if processes["GUI"] and processes["GUI"].poll() is None:
        return
    pyw = ROOT / "venv" / "Scripts" / "pythonw.exe"
    proc = subprocess.Popen([str(pyw), "-m", "entries.app_gui_entry"], cwd=str(SCRIPTS))
    processes["GUI"] = proc

def stop_gui():
    proc = processes.get("GUI")
    if proc and proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    processes["GUI"] = None

def build_gui():
    with dpg.window(label="Mission Control", width=800, height=600, tag="mc_root"):
        with dpg.tab_bar():
            with dpg.tab(label="Processes"):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Start GUI", callback=start_gui_direct)
                    dpg.add_button(label="Stop GUI", callback=stop_gui)
                    dpg.add_button(label="Open Dev Shell", callback=open_dev_shell)
                dpg.add_spacer(height=10)
                with dpg.group(horizontal=True):

                    
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

                dpg.add_spacer(height=10)
                dpg.add_text("CLI Command Input:")
                dpg.add_input_text(tag="cli_input", width=300, callback=send_cli_command, on_enter=True)
                dpg.focus_item("cli_input")
                dpg.add_spacer(height=8)
                dpg.add_text("Logs (live):")
                with dpg.child_window(width=385, height=100, border=True, tag="logs_container"):
                    dpg.add_text("", tag="logs_text")
                    dpg.add_spacer(height=30)
            with dpg.tab(label="Snapshots"):
                dpg.add_text("Clipboard name will be used for snapshot.")
                dpg.add_button(label="Make Snapshot", callback=make_snapshot)
                dpg.add_text("", tag="snapshot_status")
                dpg.add_spacer(height=10)
                dpg.add_text("Recent Snapshots:")
                dpg.add_listbox(list_snapshots(), width=385, num_items=5, tag="snap_list")
            with dpg.tab(label="Config"):
                dpg.add_text(f"Python: {sys.executable}")
                dpg.add_text(f"Venv: {os.environ.get('VIRTUAL_ENV','(none)')}")
                dpg.add_spacer(height=10)
                dpg.add_button(label="Set Theme=blue", callback=lambda: os.environ.update(PIPER_UI_THEME="blue"))
                dpg.add_button(label="Set Theme=clean", callback=lambda: os.environ.update(PIPER_UI_THEME="clean"))
                dpg.add_button(label="Enable Dev Input", callback=lambda: os.environ.update(PIPER_UI_DEV_INPUT="1"))
                dpg.add_button(label="Disable Dev Input", callback=lambda: os.environ.pop("PIPER_UI_DEV_INPUT", None))

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


if __name__ == "__main__":
    dpg.create_context()
    build_gui()
    threading.Thread(target=tail_logs, daemon=True).start()
    dpg.create_viewport(title="Piper Mission Control", width=418, height=383)
    threading.Thread(target=_watchdog_heartbeat, daemon=True).start()
    dpg.set_primary_window("mc_root", True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()
