# C:\Piper\mission_control\mission_control.py
# Mission Control v2.0 — Watchdog hidden mode with live state tracking (send_cli_command + stubs restored)

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
# Process management
# -----------------------------------------------------------------------------
def run_py(name: str, py_file: Path, with_stdin=False):
    proc = subprocess.Popen(
        [sys.executable, str(py_file)],
        cwd=str(MC_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE if not with_stdin else 0,
        stdin=subprocess.PIPE if with_stdin else None,
        text=True
    )
    processes[name] = proc
    return proc

# -----------------------------------------------------------------------------
# Snapshot
# -----------------------------------------------------------------------------

def make_snapshot():
    name = pyperclip.paste().strip()
    if not name:
        dpg.set_value("snapshot_status", "Clipboard empty, no snapshot made")
        return
    try:
        ps1 = ROOT / "tools" / "make_kgb_snapshot.ps1"
        subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps1)], check=True)
        dpg.set_value("snapshot_status", f"Snapshot attempted with name: {name}")
    except Exception as e:
        dpg.set_value("snapshot_status", f"Snapshot failed: {e}")

def list_snapshots():
    if not SNAPSHOTS.exists():
        return []
    return sorted([p.name for p in SNAPSHOTS.glob("*.zip")], reverse=True)
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
                            dpg.set_value("logs_text", dpg.get_value("logs_text") + new)
                    last_size = size
        except Exception:
            pass
        time.sleep(1)
# -----------------------------------------------------------------------------
# CLI background runner (hidden, accepts stdin, tees to core.log)
# -----------------------------------------------------------------------------
def start_cli_bg():
    if processes["CLI"] and processes["CLI"].poll() is None:
        return processes["CLI"]
    cli_py = MC_DIR / "launch_cli.py"
    return run_py("CLI", cli_py, with_stdin=True)

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
    dpg.focus_item("cli_input")  # re-focus input box


# -----------------------------------------------------------------------------
# Hidden Watchdogs
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
                dpg.set_value(tag, f"currently working (last change: {watchdog_status[name]['last_change']})")
            elif "[WATCH] Using watchdog" in lo:
                watchdog_status[name]["state"] = "standing by"
                dpg.set_value(tag, f"standing by (last change: {watchdog_status[name]['last_change']})")
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
                dpg.add_button(label="Start GUI", callback=start_gui_direct)
                dpg.add_button(label="Stop GUI", callback=stop_gui)
                dpg.add_spacer(height=10)
                dpg.add_text("Watchdog A (hidden):")
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Start A (hidden)", callback=start_watchdog_a_hidden)
                    dpg.add_button(label="Stop A", callback=stop_watchdog_a)
                dpg.add_text("not running", tag="wdA_status")
                dpg.add_spacer(height=6)
                dpg.add_text("Watchdog B (hidden):")
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Start B (hidden)", callback=start_watchdog_b_hidden)
                    dpg.add_button(label="Stop B", callback=stop_watchdog_b)
                dpg.add_text("not running", tag="wdB_status")
                dpg.add_spacer(height=10)
                dpg.add_text("CLI Command Input:")
                dpg.add_input_text(tag="cli_input", width=400, callback=send_cli_command, on_enter=True)
                dpg.focus_item("cli_input")
            with dpg.tab(label="Logs"):
                dpg.add_input_text(multiline=True, readonly=True, tag="logs_text", width=750, height=400, default_value="")
            with dpg.tab(label="Snapshots"):
                dpg.add_text("Clipboard name will be used for snapshot.")
                dpg.add_button(label="Make Snapshot", callback=make_snapshot)
                dpg.add_text("", tag="snapshot_status")
                dpg.add_spacer(height=10)
                dpg.add_text("Recent Snapshots:")
                dpg.add_listbox(list_snapshots(), width=400, num_items=5, tag="snap_list")
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
                        dpg.set_value(tag, f"standing by (last change: {watchdog_status[name]['last_change']})")
        except Exception:
            pass
        time.sleep(0.5)

if __name__ == "__main__":
    dpg.create_context()
    build_gui()
    threading.Thread(target=tail_logs, daemon=True).start()
    dpg.create_viewport(title="Piper Mission Control", width=820, height=640)
    threading.Thread(target=_watchdog_heartbeat, daemon=True).start()
    dpg.set_primary_window("mc_root", True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()
